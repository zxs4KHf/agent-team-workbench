"""Named authentication paths must stay private in both submission and export.
Only artificial file contents and disposable repositories are used.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
import bus_submission_scope as submission
import public_snapshot as snapshots

AUTH_NAMES=('cookies.json','cookies.txt','storage_state.json','storage-state.json',
            'id_rsa','id_ed25519','credentials.json','auth.json','mcp_token.txt')


class AuthPathPolicies(unittest.TestCase):
    def test_known_auth_names_denied_in_both_policies(self):
        for leaf in AUTH_NAMES:
            for name in (leaf,'assets/fixture/'+leaf,'docs/'+leaf.upper()):
                with self.subTest(path=name):
                    self.assertTrue(submission._protected(name), 'submission: '+name)
                    self.assertTrue(snapshots.mandatory_excluded(name), 'snapshot: '+name)

    def test_named_exclusion_lists_cannot_drift_silently(self):
        all_names=submission.PRIVATE_NAMES | snapshots.PRIVATE_NAMES
        for name in all_names:
            with self.subTest(name=name):
                self.assertTrue(submission._protected('nested/'+name))
                self.assertTrue(snapshots.mandatory_excluded('nested/'+name))

    def test_ordinary_examples_and_public_key_names_remain_distinct(self):
        for name in ('docs/cookie-examples.md','src/authentication.py',
                     'docs/id_rsa.pub','docs/storage-state-example.json'):
            with self.subTest(path=name):
                self.assertFalse(submission._protected(name))
                self.assertFalse(snapshots.mandatory_excluded(name))

    def test_committed_auth_files_do_not_reach_snapshot_with_empty_exclusions(self):
        with tempfile.TemporaryDirectory(prefix='auth-policy-fixture-') as temp:
            root=Path(temp);repo=root/'repo';repo.mkdir()
            def git(*args):
                p=subprocess.run(['git','-C',str(repo),*args],capture_output=True,
                                 env=snapshots.git_env(),timeout=20)
                self.assertEqual(p.returncode,0,p.stderr.decode('utf-8','replace'))
                return p.stdout.decode('utf-8').strip()
            git('init','-b','main');git('config','user.name','Fixture')
            git('config','user.email','fixture@localhost');git('config','commit.gpgsign','false')
            git('config','core.hooksPath',str(root/'no-hooks'))
            (repo/'scripts').mkdir()
            (repo/'scripts/export_public.json').write_text(json.dumps({'exclude':[]}),encoding='utf-8')
            (repo/'app.py').write_text('VALUE = 1\n',encoding='utf-8')
            folder=repo/'assets/fixture';folder.mkdir(parents=True)
            for name in AUTH_NAMES:(folder/name).write_text('synthetic fixture, not credentials',encoding='utf-8')
            git('add','.');git('commit','-m','synthetic legacy fixture')
            before=git('rev-parse','HEAD');result=snapshots.materialize(repo,before,root/'snapshot')
            self.assertIn('app.py',result['files'])
            for name in AUTH_NAMES:
                with self.subTest(name=name):
                    rel='assets/fixture/'+name
                    self.assertNotIn(rel,result['files'])
                    self.assertFalse((root/'snapshot'/rel).exists())
                    self.assertTrue((repo/rel).is_file())
            self.assertEqual(git('rev-parse','HEAD'),before)


if __name__=='__main__':unittest.main(verbosity=2)
