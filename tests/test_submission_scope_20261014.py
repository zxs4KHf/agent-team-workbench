"""Disposable repositories only; exercise actual automatic build submission."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bus_build as BB

class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='submission-scope-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.repo=self.root/'repo';self.repo.mkdir()
        self.git('init','-b','main');self.git('config','user.name','fixture');self.git('config','user.email','fixture@localhost')
        self.git('config','commit.gpgsign','false');self.git('config','core.hooksPath',str(self.root/'no-hooks'))
        self.put('.gitignore','__pycache__/\n');self.put('app.py','VALUE=1\n');self.git('add','.gitignore','app.py');self.git('commit','-m','base')
        self.base=self.git('rev-parse','HEAD').strip()
        self.rec={'task_id':'fixture','base':self.base,'repo':str(self.repo),'teammates':{'builder':{'path':str(self.repo),'branch':'main','commit':''}}}
        for patch in [mock.patch.object(BB,'BUILD_DIR',str(self.root/'records')),mock.patch.object(BB,'run_gates',return_value={'ok':True,'items':[]})]:patch.start();self.addCleanup(patch.stop)
        BB.save_record('fixture',self.rec)
    def git(self,*args):
        p=subprocess.run(['git','-C',str(self.repo),*args],capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(p.returncode,0,p.stderr);return p.stdout
    def put(self,name,text):
        p=self.repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8');return p
    def submit(self):return BB.commit_work('fixture','builder','fixture change')
    def reject_unchanged(self):
        head=self.git('rev-parse','HEAD');index=self.git('diff','--cached','--binary');status=self.git('status','--porcelain')
        with self.assertRaises(ValueError):self.submit()
        self.assertEqual(self.git('rev-parse','HEAD'),head);self.assertEqual(self.git('diff','--cached','--binary'),index);self.assertEqual(self.git('status','--porcelain'),status)
    def test_untracked_local_settings_block_before_staging(self):
        self.put('app.py','VALUE=2\n');self.put('plugins.local.json','{}');self.reject_unchanged()
    def test_prestaged_runtime_not_silently_committed(self):
        self.put('console/state.json','{}');self.git('add','console/state.json');self.reject_unchanged()
    def test_modified_tracked_runtime_blocks(self):
        self.put('log/old.md','old');self.git('add','log/old.md');self.git('commit','-m','legacy baseline')
        self.rec['base']=self.git('rev-parse','HEAD').strip();BB.save_record('fixture',self.rec)
        self.put('log/old.md','changed');self.reject_unchanged()
    def test_already_committed_runtime_is_not_a_valid_submission(self):
        self.put('console/local.json','{}');self.git('add','console/local.json');self.git('commit','-m','unsafe');self.reject_unchanged()
    def test_added_then_deleted_runtime_in_history_is_rejected(self):
        self.put('console/local.json','{}');self.git('add','console/local.json');self.git('commit','-m','unsafe')
        self.git('rm','console/local.json');self.git('commit','-m','removed');self.reject_unchanged()
    def test_code_document_and_unicode_paths_commit(self):
        self.put('app.py','VALUE=2\n');self.put('docs/使用 说明.md','Example');self.put('b.txt','fixture')
        result=self.submit();self.assertTrue(result['gates']['ok']);self.assertIn('docs/使用 说明.md',result['changed_files'])
        self.assertEqual(self.git('status','--porcelain'),'')
    def test_unknown_untracked_artifact_requires_explicit_classification(self):
        self.put('download.backup','fixture');self.reject_unchanged()
    def test_deletion_of_old_runtime_is_allowed(self):
        self.put('log/old.md','old');self.git('add','log/old.md');self.git('commit','-m','legacy')
        self.rec['base']=self.git('rev-parse','HEAD').strip();BB.save_record('fixture',self.rec)
        (self.repo/'log/old.md').unlink();self.assertIn('log/old.md',self.submit()['changed_files'])
    def test_ignored_runtime_stays_untracked(self):
        self.put('.gitignore','__pycache__/\nconsole/\n');self.git('add','.gitignore');self.git('commit','-m','ignore')
        self.put('console/private.json','{}');self.put('app.py','VALUE=2\n');self.submit()
        self.assertTrue((self.repo/'console/private.json').exists());self.assertNotIn('console/private.json',self.git('ls-files'))
    def test_no_change_stays_unbound(self):
        result=self.submit();self.assertTrue(result['commit_unbound']);self.assertEqual(result['commit'],self.base)
    def test_existing_manual_code_commit_is_checked(self):
        self.put('app.py','VALUE=2\n');self.git('add','app.py');self.git('commit','-m','manual')
        self.assertFalse(self.submit()['commit_unbound'])
    def test_nonrepo_directory_is_not_initialized(self):
        folder=self.root/'not-repo';folder.mkdir();(folder/'note.txt').write_text('keep')
        with self.assertRaises(ValueError):BB.ensure_repo(str(folder))
        self.assertFalse((folder/'.git').exists());self.assertEqual((folder/'note.txt').read_text(),'keep')

    def make_merge_only_runtime_commit(self):
        self.git('checkout','-b','fixture-side');self.put('side.py','SIDE=1\n');self.git('add','side.py');self.git('commit','-m','side')
        self.git('checkout','main');self.put('main.py','MAIN=1\n');self.git('add','main.py');self.git('commit','-m','main')
        self.git('merge','--no-ff','--no-commit','fixture-side')
        self.put('console/merge-only.json','{}');self.git('add','console/merge-only.json');self.git('commit','-m','merge with unintended fixture data')
    def test_private_file_introduced_only_in_merge_is_rejected(self):
        self.make_merge_only_runtime_commit();self.reject_unchanged()
    def test_private_merge_file_deleted_later_still_rejected(self):
        self.make_merge_only_runtime_commit();self.git('rm','console/merge-only.json');self.git('commit','-m','remove merge fixture data');self.reject_unchanged()

    def test_prestaged_unknown_artifact_requires_classification(self):
        self.put('download.backup','fixture');self.git('add','download.backup');self.reject_unchanged()
    def test_prestaged_unknown_deleted_from_worktree_still_rejected(self):
        self.put('download.backup','fixture');self.git('add','download.backup')
        (self.repo/'download.backup').unlink();self.reject_unchanged()
    def test_staged_rename_to_unclassified_path_is_rejected(self):
        self.git('mv','app.py','download.backup');self.reject_unchanged()
    def test_prestaged_new_source_is_preserved_and_committed(self):
        self.put('src/new_module.py','VALUE=3\n');self.git('add','src/new_module.py')
        result=self.submit();self.assertIn('src/new_module.py',result['changed_files'])
        self.assertEqual(self.git('status','--porcelain'),'')
    def test_existing_unclassified_tracked_file_remains_editable(self):
        self.put('legacy.artifact','baseline');self.git('add','legacy.artifact');self.git('commit','-m','legacy baseline')
        self.rec['base']=self.git('rev-parse','HEAD').strip();BB.save_record('fixture',self.rec)
        self.put('legacy.artifact','updated');self.git('add','legacy.artifact')
        self.assertIn('legacy.artifact',self.submit()['changed_files'])

if __name__=='__main__':unittest.main(verbosity=2)
