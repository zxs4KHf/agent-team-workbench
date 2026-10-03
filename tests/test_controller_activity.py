import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # 从 tests/ 运行时找到根目录模块
import datetime as dt
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bus_core as B
import controller_activity as A

class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.paths = patch.multiple(B, ROOT=root, PEERS=os.path.join(root,'peers'),
            INBOX=os.path.join(root,'inbox'), OUTBOX=os.path.join(root,'outbox'),
            LOG=os.path.join(root,'log'), LOCK=os.path.join(root,'console','controller.lock'))
        self.paths.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.paths.stop)
        self.lease = B.acquire_lease('controller')
    def test_busy_refresh_and_renew(self):
        self.assertTrue(A.pulse(self.lease['epoch'], 'test')['ok'])
        state = B.peer_state('controller')
        self.assertTrue(state['online'])
        self.assertEqual(state['status'], 'busy')
        self.assertEqual(state['task_id'], 'test')
    def test_wrong_epoch_no_write(self):
        before = B.read_json(B.LOCK)
        with self.assertRaises(RuntimeError): A.pulse(self.lease['epoch']+1)
        self.assertEqual(before, B.read_json(B.LOCK))
        self.assertFalse(B.peer_state('controller')['online'])
    def test_foreign_holder_no_takeover(self):
        B.release_lease('controller')
        other = B.acquire_lease('other')
        with self.assertRaises(RuntimeError): A.pulse(self.lease['epoch'])
        self.assertEqual(B.lease(), other)
    def test_expired_lease_not_revived(self):
        old = dict(self.lease, expires='2000-01-01T00:00:00')
        B.write_json(B.LOCK, old)
        with self.assertRaises(RuntimeError): A.pulse(self.lease['epoch'])
        self.assertEqual(B.read_json(B.LOCK), old)
    def test_no_pulse_still_expires_at_180(self):
        A.pulse(self.lease['epoch'])
        path = os.path.join(B.PEERS, 'controller.json')
        state = B.read_json(path)
        state['last_seen'] = (dt.datetime.now()-dt.timedelta(seconds=181)).strftime('%Y-%m-%dT%H:%M:%S')
        B.write_json(path, state)
        # 2026-10-02 起在线只认总线心跳记录（console/beats/）：模拟「181 秒没心跳」要把它一起拨回去
        B.write_json(B._beat_path('controller'), {'at': state['last_seen'], 'activity': 'idle', 'task_id': ''})
        self.assertFalse(B.peer_state('controller')['online'])
    def test_io_failure_not_swallowed(self):
        with patch.object(B, '_heartbeat', side_effect=OSError('test')):
            with self.assertRaises(OSError): A.pulse(self.lease['epoch'])
    def test_read_only_status_does_not_refresh(self):
        A.pulse(self.lease['epoch'])
        path = os.path.join(B.PEERS, 'controller.json')
        before = B.read_json(path)
        B.peer_state('controller')
        self.assertEqual(before, B.read_json(path))

if __name__ == '__main__': unittest.main()
