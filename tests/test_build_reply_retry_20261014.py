"""Submission failures must not be turned into successful archived answers."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bus_core as B
import bus_build as BB

class RetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='build-reply-retry-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for name in ['inbox','outbox','log','peers']:(self.root/name).mkdir()
        for name in ['INBOX','OUTBOX','LOG','PEERS']:
            p=mock.patch.object(B,name,str(self.root/name.lower()));p.start();self.addCleanup(p.stop)
        for name,value in [('ensure_dirs',lambda:None),('peer_state',lambda p:{'model':'fixture'}),('render_task',lambda t:'fixture')]:
            p=mock.patch.object(B,name,value);p.start();self.addCleanup(p.stop)
        p=mock.patch.object(B,'heartbeat');self.heartbeat=p.start();self.addCleanup(p.stop)
        p=mock.patch.object(B,'clear_strikes');self.clear=p.start();self.addCleanup(p.stop)
        (self.root/'inbox/b1').mkdir();self.claim=self.root/'inbox/b1/T1.taken'
        self.claim.write_text(json.dumps({'id':'T1','to':'b1','mode':'build','question':'fixture','taken_at':'fixture'}))
    def test_failure_keeps_claim_and_no_success_answer(self):
        before=self.claim.read_bytes()
        with mock.patch.object(BB,'commit_work',side_effect=ValueError('fixture rejection')):
            with self.assertRaises(ValueError):B.reply('T1','b1','important draft')
        self.assertEqual(self.claim.read_bytes(),before);self.assertFalse((self.root/'outbox/T1.b1.md').exists())
        self.heartbeat.assert_not_called();self.clear.assert_not_called()
        drafts=list((self.root/'log/submission-failures').glob('*.json'));self.assertEqual(len(drafts),1)
        self.assertEqual(json.loads(drafts[0].read_text(encoding='utf-8'))['answer_draft'],'important draft')
    def test_successful_retry_archives_only_after_real_commit(self):
        with mock.patch.object(BB,'commit_work',side_effect=OSError('fixture')):
            with self.assertRaises(ValueError):B.reply('T1','b1','draft')
        with mock.patch.object(BB,'commit_work',return_value={'commit':'a'*40,'changed_files':['app.py'],'gates':{'ok':True}}):
            result=B.reply('T1','b1','fixed')
        self.assertIn('DONE',result);self.assertFalse(self.claim.exists());self.assertTrue((self.root/'outbox/T1.b1.md').exists())
    def test_missing_build_record_is_not_success(self):
        with mock.patch.object(BB,'commit_work',return_value={}):
            with self.assertRaises(ValueError):B.reply('T1','b1','draft')
        self.assertTrue(self.claim.exists());self.assertFalse((self.root/'outbox/T1.b1.md').exists())
    def test_failure_does_not_echo_raw_error(self):
        with mock.patch.object(BB,'commit_work',side_effect=ValueError('PRIVATE-FIXTURE-DETAIL')):
            with self.assertRaises(ValueError) as exc:B.reply('T1','b1','draft')
        self.assertNotIn('PRIVATE-FIXTURE-DETAIL',str(exc.exception))
    def test_draft_storage_failure_still_does_not_archive(self):
        with mock.patch.object(BB,'commit_work',side_effect=ValueError('fixture')),mock.patch.object(B,'write_json',side_effect=OSError('disk-full')):
            with self.assertRaises(Exception):B.reply('T1','b1','draft')
        self.assertTrue(self.claim.exists());self.assertFalse((self.root/'outbox/T1.b1.md').exists())

    def test_real_submission_rejection_then_real_git_retry(self):
        import subprocess
        repo=self.root/'repository';repo.mkdir()
        def git(*args):
            result=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,encoding='utf-8',timeout=20)
            self.assertEqual(result.returncode,0,result.stderr);return result.stdout.strip()
        git('init','-b','main');git('config','user.name','fixture');git('config','user.email','fixture@localhost')
        git('config','commit.gpgsign','false');git('config','core.hooksPath',str(self.root/'no-hooks'))
        (repo/'.gitignore').write_text('__pycache__/\n');(repo/'app.py').write_text('VALUE=1\n')
        git('add','.gitignore','app.py');git('commit','-m','base');base=git('rev-parse','HEAD')
        with mock.patch.object(BB,'BUILD_DIR',str(self.root/'build-records')),mock.patch.object(BB,'run_gates',return_value={'ok':True,'items':[]}):
            BB.save_record('T1',{'task_id':'T1','repo':str(repo),'base':base,'teammates':{'b1':{'path':str(repo),'branch':'main','commit':''}}})
            (repo/'app.py').write_text('VALUE=2\n');blocked=repo/'plugins.local.json';blocked.write_text('{}')
            with self.assertRaises(ValueError):B.reply('T1','b1','draft')
            self.assertEqual(git('rev-parse','HEAD'),base);self.assertTrue(self.claim.exists())
            self.assertFalse((self.root/'outbox/T1.b1.md').exists());self.assertEqual(git('diff','--cached','--name-only'),'')
            blocked.unlink()  # Fixture-only remediation; production data is never deleted.
            self.assertIn('DONE',B.reply('T1','b1','fixed'))
            self.assertFalse(self.claim.exists());self.assertNotEqual(git('rev-parse','HEAD'),base)
            self.assertNotIn('plugins.local.json',git('ls-files'));self.assertTrue((self.root/'outbox/T1.b1.md').exists())

if __name__=='__main__':unittest.main(verbosity=2)
