"""Known plugin/adapter runtime paths cannot become automatic source commits."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
import bus_build as BB
import bus_submission_scope as scope
import public_snapshot as snapshots

RUNTIME=('plugins/arena/server/data/accounts.json','adapters/fixture.state.json')

class RuntimeSubmissionBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='runtime-submit-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.repo=self.root/'repo';self.repo.mkdir()
        self.git('init','-b','main');self.git('config','user.name','Fixture');self.git('config','user.email','fixture@localhost')
        self.git('config','commit.gpgsign','false');self.git('config','core.hooksPath',str(self.root/'no-hooks'))
        self.put('.gitignore','__pycache__/\n');self.put('app.py','VALUE=1\n')
        self.git('add','.gitignore','app.py');self.git('commit','-m','fixture baseline')
        self.base=self.git('rev-parse','HEAD').strip()
        for p in [mock.patch.object(BB,'BUILD_DIR',str(self.root/'records')),
                  mock.patch.object(BB,'run_gates',return_value={'ok':True,'items':[]})]:
            p.start();self.addCleanup(p.stop)
        self.record={'task_id':'fixture','base':self.base,'repo':str(self.repo),
                     'teammates':{'builder':{'path':str(self.repo),'commit':'','branch':'main'}}}
        BB.save_record('fixture',self.record)
    def put(self,name,text):
        p=self.repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8')
    def git(self,*args):
        p=subprocess.run(['git','-C',str(self.repo),*args],capture_output=True,env=snapshots.git_env(),timeout=20)
        self.assertEqual(p.returncode,0,p.stderr.decode('utf-8','replace'));return p.stdout.decode('utf-8')
    def submit(self):return BB.commit_work('fixture','builder','fixture change')
    def assert_rejected_without_changes(self):
        before=(self.git('rev-parse','HEAD'),self.git('diff','--cached','--binary'),self.git('status','--porcelain'))
        data={name:(self.repo/name).read_bytes() for name in RUNTIME if (self.repo/name).exists()}
        with self.assertRaises(ValueError):self.submit()
        self.assertEqual((self.git('rev-parse','HEAD'),self.git('diff','--cached','--binary'),self.git('status','--porcelain')),before)
        for name,body in data.items():self.assertEqual((self.repo/name).read_bytes(),body)
        self.assertEqual(BB.record('fixture')['teammates']['builder']['commit'],'')
    def seed_runtime(self):
        for name in RUNTIME:self.put(name,'{"synthetic":true}')
    def test_unignored_new_runtime_is_rejected(self):
        self.seed_runtime();self.assert_rejected_without_changes()
    def test_force_staged_ignored_runtime_is_rejected(self):
        self.put('.gitignore','__pycache__/\nplugins/arena/server/data/\nadapters/*.state.json\n')
        self.seed_runtime();self.git('add','-f','--',*RUNTIME);self.assert_rejected_without_changes()
    def test_legacy_tracked_runtime_modifications_are_rejected(self):
        self.seed_runtime();self.git('add','--',*RUNTIME);self.git('commit','-m','simulated old baseline')
        self.record['base']=self.git('rev-parse','HEAD').strip();BB.save_record('fixture',self.record)
        for name in RUNTIME:self.put(name,'{"synthetic":"changed"}')
        self.assert_rejected_without_changes()
    def test_manual_runtime_commit_is_rejected(self):
        self.seed_runtime();self.git('add','--',*RUNTIME);self.git('commit','-m','simulated unintended addition')
        self.assert_rejected_without_changes()
    def test_runtime_deleted_after_manual_commit_is_still_rejected(self):
        self.seed_runtime();self.git('add','--',*RUNTIME);self.git('commit','-m','simulated unintended addition')
        self.git('rm','--',*RUNTIME);self.git('commit','-m','remove from latest tree')
        self.assert_rejected_without_changes()
    def test_ignored_runtime_is_retained_without_being_submitted(self):
        self.put('.gitignore','__pycache__/\nplugins/arena/server/data/\nadapters/*.state.json\n')
        self.git('add','.gitignore');self.git('commit','-m','ignore fixture runtime')
        self.seed_runtime();self.put('app.py','VALUE=2\n');self.submit()
        for name in RUNTIME:
            self.assertTrue((self.repo/name).is_file());self.assertNotIn(name,self.git('ls-files'))
    def test_real_plugin_and_adapter_source_paths_are_allowed(self):
        self.put('plugins/arena/server/schema.json','{}');self.put('adapters/fixture.py','VALUE=1\n')
        result=self.submit();self.assertIn('plugins/arena/server/schema.json',result['changed_files'])
        self.assertIn('adapters/fixture.py',result['changed_files'])
    def test_declared_nested_runtime_and_local_client_paths_are_protected(self):
        for path in (*RUNTIME,'arena/data/fixture.json','arena/leader/script.py',
                     '.workbench-locks/fixture.mutex','.workbuddy/state.json',
                     'profiles/fixture.json','tmp/probe.py','docs/notes.md.bak-20261014'):
            with self.subTest(path=path):
                self.assertTrue(scope._protected(path));self.assertTrue(snapshots.mandatory_excluded(path))

if __name__=='__main__':unittest.main(verbosity=2)
