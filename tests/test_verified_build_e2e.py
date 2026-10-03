"""Disposable Git repositories: exact-commit merge and rollback boundary checks."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bus_build as BB
import bus_build_safety as S


class VerifiedBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wb-build-safety-')
        self.base = Path(self.tmp.name)
        self.repo = self.base/'main'; self.repo.mkdir()
        self.worker = self.base/'builder'
        self.store = self.base/'records'; self.store.mkdir()
        self.patcher = mock.patch.object(BB, 'BUILD_DIR', str(self.store)); self.patcher.start()
        self.gates = mock.patch.object(BB, 'run_gates', return_value={'ok': True, 'checks': []}); self.gates.start()
        self.git(self.repo, 'init', '-b', 'main')
        self.git(self.repo, 'config', 'user.email', 'workbench-tests@localhost')
        self.git(self.repo, 'config', 'user.name', 'Workbench fixture')
        self.git(self.repo, 'config', 'commit.gpgsign', 'false')
        self.git(self.repo, 'config', 'core.hooksPath', str(self.base/'no-hooks'))
        (self.repo/'.gitignore').write_text('.wt/\n__pycache__/\n',encoding='utf-8')
        (self.repo/'app.py').write_text('VALUE = "baseline"\n',encoding='utf-8')
        (self.repo/'notes.txt').write_text('baseline notes',encoding='utf-8')
        self.git(self.repo, 'add', '.gitignore', 'app.py', 'notes.txt'); self.git(self.repo, 'commit', '-m', 'baseline')
        self.baseline = self.git(self.repo, 'rev-parse', 'HEAD')
        self.git(self.repo, 'worktree', 'add', '-b', 'fixture-builder', str(self.worker), 'HEAD')
        (self.worker/'app.py').write_text('VALUE = "candidate"\n',encoding='utf-8')
        self.git(self.worker, 'add', 'app.py'); self.git(self.worker, 'commit', '-m', 'candidate')
        self.commit = self.git(self.worker, 'rev-parse', 'HEAD')
        BB.save_record("fixture-task", {'task_id':'fixture-task','repo':str(self.repo),'base':self.baseline,
                        'teammates':{'builder1':{'path':str(self.worker),'branch':'fixture-builder',
                          'commit':self.commit,'review':'','merged':False,'gates':{'ok':True}}}})
    def tearDown(self):
        self.gates.stop(); self.patcher.stop(); self.tmp.cleanup()
    def git(self, directory, *args):
        p = subprocess.run(['git', *args], cwd=directory, capture_output=True, text=True, encoding='utf-8', timeout=30)
        if p.returncode: raise AssertionError(p.stderr or p.stdout)
        return p.stdout.strip()
    def approve(self):
        return BB.set_review('fixture-task','builder1','PASS','reviewer1','fixture review',expected_commit=self.commit)
    def test_review_diff_has_exact_commit_and_current_merge_base(self):
        target = BB.review_target('fixture-task','builder1')
        self.assertEqual(target['commit'],self.commit)
        self.assertEqual(target['base'],self.baseline)
        text = BB.review_pack('fixture-task','builder1',target=target)
        self.assertIn(self.commit,text); self.assertIn('-VALUE = "baseline"',text)
    def test_legacy_pass_cannot_merge(self):
        rec=BB.record('fixture-task'); rec['teammates']['builder1']['review']='PASS'; BB.save_record("fixture-task",rec)
        with self.assertRaises(ValueError): BB.merge('fixture-task','builder1')
        self.assertEqual(self.git(self.repo,'rev-parse','HEAD'),self.baseline)
    def test_legacy_review_cannot_approve(self):
        with self.assertRaises(ValueError): BB.set_review('fixture-task','builder1','PASS','reviewer1','old answer')
    def test_self_review_rejected(self):
        with self.assertRaises(ValueError): BB.set_review('fixture-task','builder1','PASS','builder1',expected_commit=self.commit)
    def test_stale_review_cannot_approve_new_commit(self):
        (self.worker/'app.py').write_text('VALUE = "newer"\n',encoding='utf-8')
        self.git(self.worker,'commit','-am','newer')
        rec=BB.record('fixture-task'); rec['teammates']['builder1']['commit']=self.git(self.worker,'rev-parse','HEAD'); BB.save_record("fixture-task",rec)
        with self.assertRaises(ValueError): self.approve()
    def test_mutable_branch_tip_cannot_smuggle_unreviewed_commit(self):
        self.approve()
        (self.worker/'app.py').write_text('VALUE = "unreviewed"\n',encoding='utf-8')
        self.git(self.worker,'commit','-am','unreviewed branch tip')
        result=BB.merge('fixture-task','builder1')
        self.assertTrue(result['ok'],result)
        self.assertEqual((self.repo/'app.py').read_text(),'VALUE = "candidate"\n')
        self.assertEqual(result['reviewed_commit'],self.commit)
    def test_dirty_overlap_refuses_and_preserves_user_edit(self):
        self.approve(); (self.repo/'app.py').write_text('USER EDIT\n',encoding='utf-8')
        result=BB.merge('fixture-task','builder1')
        self.assertFalse(result['ok']); self.assertIn('app.py',result['conflicts'])
        self.assertEqual((self.repo/'app.py').read_text(),'USER EDIT\n')
        self.assertEqual(self.git(self.repo,'rev-parse','HEAD'),self.baseline)
    def test_unrelated_dirty_state_survives_successful_merge(self):
        self.approve(); (self.repo/'notes.txt').write_text('live user notes',encoding='utf-8')
        result=BB.merge('fixture-task','builder1')
        self.assertTrue(result['ok'],result)
        self.assertEqual((self.repo/'notes.txt').read_text(),'live user notes')
    def test_integration_gate_failure_leaves_main_unchanged(self):
        self.approve(); BB.run_gates.return_value={'ok':False,'checks':[{'reason':'fixture fail'}]}
        result=BB.merge('fixture-task','builder1')
        self.assertFalse(result['ok'])
        self.assertEqual(self.git(self.repo,'rev-parse','HEAD'),self.baseline)
    def test_conflict_is_not_misreported_as_success(self):
        self.approve(); (self.repo/'app.py').write_text('VALUE = "main-change"\n',encoding='utf-8')
        self.git(self.repo,'commit','-am','main changed'); before=self.git(self.repo,'rev-parse','HEAD')
        result=BB.merge('fixture-task','builder1')
        self.assertFalse(result['ok']); self.assertEqual(self.git(self.repo,'rev-parse','HEAD'),before)
    def test_close_without_removal_preserves_unmerged_worktree(self):
        BB.close('fixture-task',remove_worktrees=False)
        self.assertTrue((self.worker/'app.py').is_file())
        self.assertTrue(BB.record('fixture-task')['worktrees_retained'])
    def test_close_refuses_unmerged_worktree(self):
        with self.assertRaises(ValueError): BB.close('fixture-task',remove_worktrees=True)
        self.assertTrue((self.worker/'app.py').is_file())
    def test_close_refuses_dirty_merged_worktree(self):
        self.approve(); self.assertTrue(BB.merge('fixture-task','builder1')['ok'])
        (self.worker/'untracked.txt').write_text('keep me',encoding='utf-8')
        with self.assertRaises(ValueError): BB.close('fixture-task',remove_worktrees=True)
        self.assertEqual((self.worker/'untracked.txt').read_text(),'keep me')
    def test_close_clean_merged_worktree(self):
        self.approve(); self.assertTrue(BB.merge('fixture-task','builder1')['ok'])
        self.assertTrue(BB.close('fixture-task',remove_worktrees=True))
        self.assertFalse(self.worker.exists())

if __name__=='__main__':
    unittest.main(verbosity=2)
