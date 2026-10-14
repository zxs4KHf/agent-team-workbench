"""No real tunnels, processes, tokens or production configuration are used."""
from pathlib import Path
import concurrent.futures
import json
import sys
import tempfile
import time
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bus_guard as G

class ReplicaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='replica-singleton-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.exe=self.root/'cloudflared.exe';self.exe.write_text('fixture, not executable')
        self.token=self.root/'local-config.txt';self.token.write_text('fixture')
        self.running=[]
        for key,value in [('CF_EXE',str(self.exe)),('CF_TOKEN',str(self.token)),('CF_PID',str(self.root/'pid'))]:
            p=mock.patch.object(G,key,value);p.start();self.addCleanup(p.stop)
        p=mock.patch.object(G,'replica_instances',side_effect=lambda:list(self.running),create=True);self.query=p.start();self.addCleanup(p.stop)
        def launch(*args,**kwargs):time.sleep(.02);self.running.append(43210);return 43210
        p=mock.patch.object(G,'spawn',side_effect=launch);self.spawn=p.start();self.addCleanup(p.stop)
    def test_two_restart_triggers_spawn_only_once(self):
        G.start_replica();G.start_replica();self.assertEqual(self.spawn.call_count,1)
    def test_existing_unrecorded_matching_process_is_reused(self):
        self.running=[12345];G.start_replica();self.spawn.assert_not_called()
    def test_multiple_legacy_processes_do_not_create_more(self):
        self.running=[12345,12346];G.start_replica();G.start_replica();self.spawn.assert_not_called()
    def test_unknown_process_inventory_fails_closed(self):
        self.query.side_effect=RuntimeError('fixture unavailable')
        with self.assertRaises(RuntimeError):G.start_replica()
        self.spawn.assert_not_called()
    def test_concurrent_requests_are_serialized(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(lambda _:G.start_replica(),range(6)))
        self.assertEqual(self.spawn.call_count,1)
    def test_pidfile_damage_cannot_cause_duplicate(self):
        Path(G.CF_PID).write_text('invalid');self.running=[12345];G.start_replica();self.spawn.assert_not_called()
    def test_missing_config_does_not_spawn(self):
        self.token.unlink();G.start_replica();self.spawn.assert_not_called()
    def test_liveness_uses_identity_not_bare_pid(self):
        self.running=[12345]
        with mock.patch.object(G.subprocess,'run',side_effect=AssertionError('must use verified inventory')):
            alive,_,_=G.replica_alive()
        self.assertTrue(alive)

class IdentityTests(unittest.TestCase):
    def setUp(self):
        import bus_guard_process as M
        self.M=M
        self.tmp=tempfile.TemporaryDirectory(prefix='replica-identity-');self.addCleanup(self.tmp.cleanup)
        self.exe=str(Path(self.tmp.name)/'cloudflared.exe');self.token=str(Path(self.tmp.name)/'config.txt')
        self.argv=[self.exe,'tunnel','--no-autoupdate','run','--token-file',self.token]
        self.row={'ProcessId':12345,'ExecutablePath':self.exe,'CommandLine':'fixture'}
        patch=mock.patch.object(M,'command_args',side_effect=lambda command:list(self.argv));patch.start();self.addCleanup(patch.stop)
    def test_exact_identity_matches(self):self.assertTrue(self.M.matches(self.row,self.exe,self.token))
    def test_different_target_is_not_reused(self):
        self.argv[-1]=str(Path(self.tmp.name)/'another.txt');self.assertFalse(self.M.matches(self.row,self.exe,self.token))
    def test_name_alone_is_insufficient(self):
        self.row['ExecutablePath']=str(Path(self.tmp.name)/'other'/'cloudflared.exe');self.assertFalse(self.M.matches(self.row,self.exe,self.token))
    def test_unknown_identity_blocks(self):
        self.row['CommandLine']=None
        with self.assertRaises(RuntimeError):self.M.matches(self.row,self.exe,self.token)
    def test_additional_unknown_flags_not_adopted(self):
        self.argv.append('--extra');self.assertFalse(self.M.matches(self.row,self.exe,self.token))
    def test_relative_target_not_adopted(self):
        self.argv[-1]='config.txt';self.assertFalse(self.M.matches(self.row,self.exe,self.token))

class NativeParsingTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform=='win32','Windows argument parser')
    def test_quoted_native_arguments_roundtrip(self):
        import subprocess
        import bus_guard_process as M
        with tempfile.TemporaryDirectory(prefix='native-argv-') as tmp:
            argv=[str(Path(tmp)/'program files'/'cloudflared.exe'),'tunnel','--no-autoupdate','run','--token-file',str(Path(tmp)/'local config.txt')]
            self.assertEqual(M.command_args(subprocess.list2cmdline(argv)),argv)

if __name__=='__main__':unittest.main(verbosity=2)
