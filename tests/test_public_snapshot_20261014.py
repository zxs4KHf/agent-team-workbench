"""Public export must use committed blobs, not index/worktree. Disposable repos only."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import export_public as X
try:
    import public_snapshot as S
except ImportError:
    S = None

class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(S, 'fixed-commit snapshot implementation is missing')
        self.tmp=tempfile.TemporaryDirectory(prefix='public-snapshot-');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.repo=self.base/'repo';self.repo.mkdir()
        self.git('init','-b','main');self.git('config','user.name','Fixture');self.git('config','user.email','fixture@localhost')
        self.git('config','commit.gpgsign','false');self.git('config','core.hooksPath',str(self.base/'no-hooks'))
        self.put('scripts/export_public.json',json.dumps({'exclude':['docs/private/'],'scan_allow':[],'generic_skip':[]}))
        self.put('app.py','VALUE = 1\n');self.commit();self.sha=self.git('rev-parse','HEAD').strip()
    def git(self,*args,input=None):
        p=subprocess.run(['git','-C',str(self.repo),*args],input=input,capture_output=True,timeout=20)
        if p.returncode:raise AssertionError(p.stderr.decode('utf-8','replace'))
        return p.stdout.decode('utf-8')
    def put(self,name,data):
        p=self.repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data,encoding='utf-8')
    def commit(self):
        self.git('add','--all');self.git('commit','-m','fixture','--allow-empty')
    def snapshot(self,ref=None):
        return S.materialize(self.repo,ref or self.sha,self.base/'snapshot')
    def test_dirty_and_staged_changes_never_exported(self):
        self.put('app.py','VALUE = 99\n');self.git('add','app.py');self.put('new.py','UNCOMMITTED=1')
        result=self.snapshot();self.assertEqual(result['commit'],self.sha)
        self.assertEqual((self.base/'snapshot/app.py').read_text(),'VALUE = 1\n')
        self.assertFalse((self.base/'snapshot/new.py').exists());self.assertIn('app.py',self.git('diff','--cached','--name-only'))
    def test_historical_ref_not_current_head(self):
        self.put('app.py','VALUE = 2\n');self.commit();self.snapshot()
        self.assertEqual((self.base/'snapshot/app.py').read_text(),'VALUE = 1\n')
    def test_policy_is_loaded_from_commit_not_dirty_worktree(self):
        self.put('docs/private/internal.md','internal');self.commit();self.sha=self.git('rev-parse','HEAD').strip()
        self.put('scripts/export_public.json','{"exclude":[]}');self.snapshot()
        self.assertFalse((self.base/'snapshot/docs/private/internal.md').exists())
    def test_runtime_and_credentials_excluded_even_when_tracked(self):
        names=['log/report.md','projects/demo/project.json','.runtime/task.json','console/settings.json','plugins.local.json','privacy.local.txt','nested/.env','nested/key.pem','nested/db.sqlite','nested/.private/notes.md']
        for name in names:self.put(name,'fixture')
        self.commit();r=self.snapshot('HEAD');self.assertFalse(any(n in r['files'] for n in names))
    def test_included_symlink_rejected_without_reading_target(self):
        oid=self.git('hash-object','-w','--stdin',input=b'../outside.txt').strip()
        self.git('update-index','--add','--cacheinfo','120000',oid,'escape.txt');self.git('commit','-m','symlink')
        with self.assertRaises(ValueError):self.snapshot('HEAD')
        self.assertFalse((self.base/'snapshot').exists())
    def test_submodule_rejected(self):
        self.git('update-index','--add','--cacheinfo','160000',self.sha,'dependency');self.git('commit','-m','gitlink')
        with self.assertRaises(ValueError):self.snapshot('HEAD')
    def test_case_collisions_rejected_on_portable_export(self):
        oid=self.git('hash-object','-w','--stdin',input=b'fixture').strip()
        self.git('update-index','--add','--cacheinfo','100644',oid,'App.py');self.git('commit','-m','case')
        with self.assertRaises(ValueError):self.snapshot('HEAD')
    def test_nonempty_target_preserved(self):
        out=self.base/'snapshot';out.mkdir();(out/'keep.txt').write_text('keep')
        with self.assertRaises((ValueError,FileExistsError)):self.snapshot()
        self.assertEqual((out/'keep.txt').read_text(),'keep')
    def test_invalid_ref_does_not_create_output(self):
        with self.assertRaises(ValueError):self.snapshot('--invalid-ref')
        self.assertFalse((self.base/'snapshot').exists())
    def test_missing_policy_fails_closed(self):
        self.git('rm','scripts/export_public.json');self.git('commit','-m','missing')
        with self.assertRaises(ValueError):self.snapshot('HEAD')
    def test_blob_hashes_and_modes_recorded(self):
        self.put('说明 空格.md','普通文本\n');self.commit();r=self.snapshot('HEAD')
        self.assertIn('说明 空格.md',r['files']);self.assertEqual(len(r['files']['app.py']['sha256']),64)
        self.assertEqual(r['files']['app.py']['mode'],'100644')
    def test_git_environment_cannot_redirect_snapshot(self):
        with mock.patch.dict(os.environ,{'GIT_DIR':str(self.base/'absent'),'GIT_WORK_TREE':str(self.base/'other')}):self.snapshot()
        self.assertTrue((self.base/'snapshot/app.py').exists())
    def test_scan_missing_file_is_not_a_pass(self):
        with self.assertRaises(OSError):X.scan(['missing.py'],str(self.repo),X.BUILTIN)
    def test_cli_scan_uses_fixed_ref_and_never_echoes_match(self):
        self.put('note.md','fixture-private-marker');self.commit();self.sha=self.git('rev-parse','HEAD').strip()
        rules=self.base/'private-patterns.txt';rules.write_text('fixture-private-marker\n',encoding='utf-8')
        self.put('note.md','now harmless')
        stream=io.StringIO()
        with mock.patch.object(X,'ROOT',str(self.repo)),contextlib.redirect_stdout(stream):
            code=X.main(['--scan-only','--ref',self.sha,'--private-patterns',str(rules)])
        self.assertEqual(code,1);self.assertNotIn('fixture-private-marker',stream.getvalue())

    def test_svg_text_is_scanned(self):
        self.put('art.svg','<text>person@corp.invalid</text>')
        hits=X.scan(['art.svg'],str(self.repo),X.BUILTIN)
        self.assertTrue(hits)
    def test_size_limit_rejects_before_writing(self):
        with mock.patch.object(S,'MAX_FILE',1):
            with self.assertRaises(ValueError):self.snapshot()
        self.assertFalse((self.base/'snapshot').exists())
    def test_explicit_missing_private_rules_fails(self):
        with mock.patch.object(X,'ROOT',str(self.repo)),contextlib.redirect_stdout(io.StringIO()):
            result=X.main(['--scan-only','--private-patterns',str(self.base/'missing.txt')])
        self.assertEqual(result,1)
    def test_unsafe_portable_paths_rejected(self):
        for name in ('../x','a/../x','a\\b','a:b','x.','a//b','/absolute','NUL.txt'):
            with self.subTest(name=name),self.assertRaises(ValueError):S.checked_path(name)

    def test_exported_repository_applies_windows_batch_checkout_attributes(self):
        src=self.base/'plain-source';src.mkdir()
        (src/'.gitattributes').write_bytes(b'*.bat text eol=crlf\n')
        (src/'start.bat').write_bytes(b'@echo off\nexit /b 0\n')
        out=self.base/'exported'
        X.export(str(out),['.gitattributes','start.bat'],str(src),'Fixture <fixture@example.com>','v-test')
        content=(out/'start.bat').read_bytes()
        self.assertEqual(content.count(b'\n'),content.count(b'\r\n'))

    def test_all_declared_runtime_roots_are_mandatorily_excluded(self):
        names=['feedback/private.json','profiles/account.json','.workbench-locks/owner.txt','workbench/state.json']
        for name in names:self.put(name,'fixture')
        self.commit();result=self.snapshot('HEAD')
        self.assertFalse(any(n in result['files'] for n in names))
    def test_missing_scan_exempt_and_binary_files_do_not_pass(self):
        for name,allow in [('missing.png',[]),('missing.py',['missing.py'])]:
            with self.subTest(name=name),self.assertRaises(OSError):
                X.scan([name],str(self.repo),X.BUILTIN,allow=allow)
    def test_export_refuses_existing_empty_output(self):
        out=self.base/'existing';out.mkdir()
        with self.assertRaises((SystemExit,FileExistsError,ValueError)):
            X.export(str(out),['app.py'],str(self.repo),'Fixture <fixture@example.com>','v-test')
        self.assertEqual(list(out.iterdir()),[])
    def test_cli_invalid_regex_fails_without_echoing_pattern(self):
        rules=self.base/'bad-patterns.txt';rules.write_text('private-regex([',encoding='utf-8')
        stream=io.StringIO()
        with mock.patch.object(X,'ROOT',str(self.repo)),contextlib.redirect_stdout(stream):
            result=X.main(['--scan-only','--private-patterns',str(rules)])
        self.assertEqual(result,1);self.assertNotIn('private-regex',stream.getvalue())

    def test_output_git_environment_cannot_modify_source_repository(self):
        src=self.base/'plain-source';src.mkdir();(src/'public.txt').write_text('public',encoding='utf-8')
        out=self.base/'safe-output';original=self.git('rev-parse','HEAD').strip()
        with mock.patch.dict(os.environ,{'GIT_DIR':str(self.repo/'.git'),'GIT_WORK_TREE':str(self.repo)}):
            X.export(str(out),['public.txt'],str(src),'Fixture <fixture@example.com>','v-test')
        self.assertEqual(self.git('rev-parse','HEAD').strip(),original)
        self.assertTrue((out/'.git').is_dir());self.assertEqual((out/'public.txt').read_text(),'public')

if __name__=='__main__':unittest.main(verbosity=2)
