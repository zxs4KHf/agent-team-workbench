"""Regression evidence for the 2026-09-25 backlog integration. Never uses production state."""
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bus_core as B
import bus_projects as P
import bus_lineage as L
import bus_locks as K
import named_workspace_io as W

def cas_writer(root, version, ready, start, output):
    original = W._check
    def slow_check(*args):
        data = original(*args)
        time.sleep(0.08)
        return data
    W._check = slow_check
    ready.put(True)
    start.wait(15)
    try:
        W.write(root, 'item.txt', str(os.getpid()), version)
        output.put('won')
    except ValueError:
        output.put('conflict')
    except Exception as exc:
        output.put(type(exc).__name__)

def hold_lock(path, ready):
    with K.file_mutex(path):
        ready.set()
        time.sleep(60)

class Reliability(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wb-e2e-regression-')
        self.root = Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
    def test_process_cas_exactly_one_winner(self):
        W.write(self.root, 'item.txt', 'baseline', 'missing')
        ctx = mp.get_context('spawn')
        start, ready, output = ctx.Event(), ctx.Queue(), ctx.Queue()
        jobs = [ctx.Process(target=cas_writer, args=(str(self.root), W.version(b'baseline'), ready, start, output)) for _ in range(4)]
        try:
            for job in jobs: job.start()
            for _ in jobs: ready.get(timeout=30)
            start.set()
            results = [output.get(timeout=30) for _ in jobs]
            self.assertEqual(sorted(results), ['conflict']*3 + ['won'])
        finally:
            for job in jobs:
                job.join(5)
                if job.is_alive(): job.terminate(); job.join(5)
            ready.close(); output.close()
    def test_crash_releases_os_lock(self):
        ctx = mp.get_context('spawn'); ready = ctx.Event()
        path = str(self.root/'crash.mutex')
        job = ctx.Process(target=hold_lock, args=(path, ready)); job.start()
        try:
            self.assertTrue(ready.wait(30))
            with self.assertRaises(TimeoutError):
                with K.file_mutex(path, timeout=0.1): pass
        finally:
            job.terminate(); job.join(5)
        with K.file_mutex(path, timeout=1): pass
    def test_reentrant_path_alias(self):
        path = self.root/'nested.mutex'
        with K.file_mutex(path):
            with K.file_mutex(self.root/'x'/'..'/'nested.mutex', timeout=0.1): pass
    def test_atomic_failure_is_error_and_preserves_old_bytes(self):
        path = self.root/'record.json'; path.write_text('{"old":true}', encoding='utf-8')
        # 2026-10-03 起 Windows 先走 POSIX 改名（K._replace_posix），故障要两条替换路径都注入，约定不变：失败报错、原文件不动
        with mock.patch.object(K.os, 'replace', side_effect=PermissionError('fixture')), mock.patch.object(K, '_replace_posix', return_value=False), mock.patch.object(K.time, 'sleep'):
            with self.assertRaises(PermissionError): B.write_json(path, {'new': True})
        self.assertEqual(path.read_text(), '{"old":true}')
        self.assertFalse(list(self.root.glob('*.tmp')))
    def test_concurrent_atomic_writers_use_unique_temporary_files(self):
        path = self.root/'record.json'; errors=[]
        def run(i):
            try: B.write_json(path, {'writer': i, 'body': 'x'*10000})
            except Exception as exc: errors.append(str(exc))
        jobs=[threading.Thread(target=run,args=(i,)) for i in range(12)]
        for job in jobs: job.start()
        for job in jobs: job.join(10)
        self.assertEqual(errors, [])
        self.assertIn(json.loads(path.read_text())['writer'], range(12))
    def test_lock_directory_is_not_exposed(self):
        with self.assertRaises(PermissionError): W.resolve(self.root, '.workbench-locks/a.mutex')
    def test_four_projections_removed_without_losing_business_fields(self):
        row={'task_id':'r1','progress':{},'implementation':{},'lineage':{},'bucket':'done', 'implementation_task_id':'build1','execution':'pending','root_task':'r1'}
        clean=L.persistable_all([row])[0]
        for key in ('progress','implementation','lineage','bucket'): self.assertNotIn(key,clean); self.assertIn(key,row)
        self.assertEqual(clean['implementation_task_id'],'build1')
        self.assertEqual(clean['execution'],'pending')
    def test_legacy_parent_does_not_guess_root(self):
        self.assertEqual(L.lineage_of({'task_id':'child1','parent_task':'parent1'})['root_task'],'child1')
        self.assertEqual(L.lineage_of({'task_id':'child1','parent_task':'parent1','root_task':'root1'})['root_task'],'root1')
    def test_no_implementation_needed_is_not_in_agent_queue(self):
        self.assertEqual(L.landing_bucket({'task_id':'r1','status':'adopted','execution':'n-a'}),'done')

class ProfileRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='wb-profile-regression-'); self.root=Path(self.tmp.name)
        self.patches=[mock.patch.object(B,'ROOT',str(self.root)), mock.patch.object(B,'PROFILES',str(self.root/'profiles'))]
        for p in self.patches:p.start()
        (self.root/'profiles').mkdir()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def test_corrupt_utf8_profile_and_directory_do_not_break_role_line(self):
        path=self.root/'profiles'/'testpeer.json'; path.write_bytes(bytes([255,254]))
        self.assertEqual(B.get_profile('testpeer'),B.default_profile()); self.assertEqual(B.role_line_for('testpeer'),'')
        path.unlink(); path.mkdir()
        self.assertEqual(B.get_profile('testpeer'),B.default_profile())
    def test_falsey_non_string_answer_styles_are_rejected(self):
        for value in (None, [], {}, 0, False):
            with self.subTest(value=value), self.assertRaises(ValueError): B.set_profile('testpeer',{'answer_style':value})
        self.assertEqual(B.set_profile('testpeer',{'answer_style':''})['answer_style'],'')
    def test_malformed_build_records_do_not_break_scoring(self):
        builds=self.root/'console'/'builds'; builds.mkdir(parents=True)
        for i,value in enumerate(([], ['bad'], 17, 'bad', None)):
            (builds/(str(i)+'.json')).write_text(json.dumps(value),encoding='utf-8')
        (builds/'badutf.json').write_bytes(bytes([255]))
        self.assertEqual(P._recent_avg_score('testpeer'),0)
    def test_scope_uses_path_components(self):
        prof=B.default_profile(); prof['scope_paths']=['projA/src']
        with mock.patch.object(B,'list_peers',return_value=[{'peer':'testpeer','online':True}]), mock.patch.object(B,'migrate_team_tags',return_value=prof), mock.patch.object(P,'ensure'):
            self.assertEqual(P.suggest_peers('', ['projA/src-other/file.py'])[0]['matched'],0)
            self.assertEqual(P.suggest_peers('', ['projA/src/file.py'])[0]['matched'],1)

if __name__=='__main__':
    unittest.main(verbosity=2)
