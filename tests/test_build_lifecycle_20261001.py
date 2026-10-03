"""施工记录生命周期：合并/关闭后改状态；ws_* 根目录只认仍派给该成员的单（9fb1 漂移的回归）。

跑法：py -3 tests/test_build_lifecycle_20261001.py
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bus_core as B
import bus_build as BB


class TeammateRoot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wb-build-life-')
        base = Path(self.tmp.name)
        self.builds, self.outbox, self.peers, self.inbox = base / 'builds', base / 'outbox', base / 'peers', base / 'inbox'
        for d in (self.builds, self.outbox, self.peers, self.inbox):
            d.mkdir()
        self.wt = base / 'wt'
        self.patches = [mock.patch.object(BB, 'BUILD_DIR', str(self.builds)),
                        mock.patch.object(B, 'OUTBOX', str(self.outbox)),
                        mock.patch.object(B, 'PEERS', str(self.peers)),
                        mock.patch.object(B, 'INBOX', str(self.inbox))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def build(self, task_id, peer='p1', open_task=True, **tm):
        path = self.wt / task_id / peer
        path.mkdir(parents=True)
        BB.save_record(task_id, {'task_id': task_id, 'repo': str(self.wt), 'base': 'x', 'status': 'building',
                                 'teammates': {peer: {'path': str(path), 'branch': 'b', 'commit': '', 'merged': False, **tm}}})
        if open_task:
            B.write_json(str(self.outbox / f'{task_id}.meta.json'), {'id': task_id, 'peers': [peer]})
            (self.inbox / peer).mkdir(exist_ok=True)
            B.write_json(str(self.inbox / peer / f'{task_id}.taken'), {'id': task_id})
        return str(path)

    def test_stale_newer_build_does_not_capture_root(self):
        live = self.build('20261001-080000-aaaa')
        self.build('20261001-090000-bbbb', open_task=False)  # 掉线遗留：任务已归档，记录仍是 building
        self.assertEqual(BB.teammate_root('p1'), live)

    def test_replied_or_reassigned_build_is_not_root(self):
        self.build('20261001-080000-aaaa')
        (self.outbox / '20261001-080000-aaaa.p1.md').write_text('done', encoding='utf-8')
        self.assertEqual(BB.teammate_root('p1'), '')
        self.build('20261001-090000-cccc')
        B.write_json(str(self.outbox / '20261001-090000-cccc.meta.json'), {'id': 'x', 'peers': ['someone-else']})
        self.assertEqual(BB.teammate_root('p1'), '')

    def test_heartbeat_task_wins_when_two_builds_are_live(self):
        older = self.build('20261001-080000-aaaa')
        self.build('20261001-090000-bbbb')
        B.write_json(str(self.peers / 'p1.json'), {'peer': 'p1', 'task_id': '20261001-080000-aaaa'})
        self.assertEqual(BB.teammate_root('p1'), older)

    def test_closed_or_merged_record_is_not_root(self):
        self.build('20261001-080000-aaaa')
        rec = BB.record('20261001-080000-aaaa'); rec['status'] = 'merged'; BB.save_record('20261001-080000-aaaa', rec)
        self.assertEqual(BB.teammate_root('p1'), '')


    def test_released_claim_is_not_root(self):
        self.build('20261001-080000-aaaa')
        os.replace(self.inbox / 'p1' / '20261001-080000-aaaa.taken', self.inbox / 'p1' / '20261001-080000-aaaa.json.expired')
        self.assertEqual(BB.teammate_root('p1'), '')


class ReclaimClaims(unittest.TestCase):
    """空闲 Agent 回到 wait_task 时，它名下已领未回的孤儿领取：未过期重发一次，过期/重发过就释放。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wb-reclaim-')
        base = Path(self.tmp.name)
        self.inbox, self.outbox = base / 'inbox', base / 'outbox'
        for d in (self.inbox / 'p1', self.outbox, base / 'peers', base / 'log'):
            d.mkdir(parents=True)
        self.patches = [mock.patch.object(B, 'INBOX', str(self.inbox)), mock.patch.object(B, 'OUTBOX', str(self.outbox)),
                        mock.patch.object(B, 'PEERS', str(base / 'peers')), mock.patch.object(B, 'LOG', str(base / 'log')),
                        mock.patch.object(B, 'ensure_dirs', lambda: None), mock.patch.object(B, 'POLL_SEC', 0.01),
                        mock.patch.object(B, 'relay_candidate', lambda peer: False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def claim(self, task_id, minutes_left, **extra):
        deadline, ts = B.deadline_after(minutes_left)
        task = {'id': task_id, 'from': 'controller', 'to': 'p1', 'mode': 'independent', 'question': 'q', 'deadline': deadline,
                'deadline_ts': ts, 'taken_at': B.now_iso(), **extra}
        B.write_json(str(self.inbox / 'p1' / f'{task_id}.taken'), task)
        B.write_json(str(self.outbox / f'{task_id}.meta.json'), {'id': task_id, 'peers': ['p1'], 'mode': 'independent'})

    def test_live_claim_is_redelivered_once_then_released(self):
        self.claim('T1', 30)
        text = B.wait_task('p1', 0)
        self.assertTrue(text.startswith(B.REDELIVER_NOTE), text[:80])
        self.assertIn('T1', text)
        again = B.wait_task('p1', 0)  # 重发过一次仍没回：释放，不再无限重发
        self.assertTrue(again.startswith(B.IDLE_TEXT), again[:80])
        self.assertTrue((self.inbox / 'p1' / 'T1.json.expired').exists())
        self.assertEqual(B.inbox_counts('p1')['claimed'], 0)

    def test_expired_claim_is_released_and_late_reply_accepted(self):
        self.claim('T2', -5)
        self.assertTrue(B.wait_task('p1', 0).startswith(B.IDLE_TEXT))
        self.assertFalse((self.inbox / 'p1' / 'T2.taken').exists())
        B.reply('T2', 'p1', 'late answer')
        self.assertTrue((self.outbox / 'T2.p1.md').exists())
        self.assertFalse((self.inbox / 'p1' / 'T2.json.expired').exists())

    def test_plain_expired_copy_still_rejects_reply(self):
        B.write_json(str(self.inbox / 'p1' / 'T3.json.expired'), {'id': 'T3', 'mode': 'independent'})
        with self.assertRaises(ValueError):
            B.reply('T3', 'p1', 'x')

    def test_reassigned_or_answered_claim_left_alone(self):
        self.claim('T4', -5)
        B.write_json(str(self.outbox / 'T4.meta.json'), {'id': 'T4', 'peers': ['someone-else']})
        B.wait_task('p1', 0)
        self.assertTrue((self.inbox / 'p1' / 'T4.taken').exists())

    def test_controller_claims_untouched(self):
        (self.inbox / 'controller').mkdir()
        B.write_json(str(self.inbox / 'controller' / 'C1.taken'), {'id': 'C1', 'deadline_ts': 0})
        B.write_json(str(self.outbox / 'C1.meta.json'), {'id': 'C1', 'peers': ['controller']})
        with mock.patch.object(B, 'acquire_lease', lambda peer: {}):
            B.wait_task('controller', 0)
        self.assertTrue((self.inbox / 'controller' / 'C1.taken').exists())


class MergeAndCloseSetStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wb-build-life-merge-')
        base = Path(self.tmp.name)
        self.repo, self.worker, store = base / 'main', base / 'builder', base / 'records'
        self.repo.mkdir(); store.mkdir()
        self.patches = [mock.patch.object(BB, 'BUILD_DIR', str(store)),
                        mock.patch.object(BB, 'run_gates', return_value={'ok': True, 'items': []})]
        for p in self.patches:
            p.start()
        g = self.git
        g(self.repo, 'init', '-b', 'main')
        g(self.repo, 'config', 'user.email', 't@localhost'); g(self.repo, 'config', 'user.name', 't')
        g(self.repo, 'config', 'commit.gpgsign', 'false'); g(self.repo, 'config', 'core.hooksPath', str(base / 'no-hooks'))
        (self.repo / '.gitignore').write_text('.wt/\n', encoding='utf-8')
        (self.repo / 'app.py').write_text('V = 1\n', encoding='utf-8')
        g(self.repo, 'add', '.'); g(self.repo, 'commit', '-m', 'base')
        baseline = g(self.repo, 'rev-parse', 'HEAD')
        g(self.repo, 'worktree', 'add', '-b', 'task/T/b1', str(self.worker), 'HEAD')
        (self.worker / 'app.py').write_text('V = 2\n', encoding='utf-8')
        g(self.worker, 'add', 'app.py'); g(self.worker, 'commit', '-m', 'candidate')
        self.commit = g(self.worker, 'rev-parse', 'HEAD')
        BB.save_record('T', {'task_id': 'T', 'repo': str(self.repo), 'base': baseline, 'status': 'building',
                             'teammates': {'b1': {'path': str(self.worker), 'branch': 'task/T/b1', 'commit': self.commit,
                                                  'review': '', 'merged': False, 'gates': {'ok': True}}}})

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def git(self, cwd, *args):
        p = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, encoding='utf-8', timeout=30)
        if p.returncode:
            raise AssertionError(p.stderr or p.stdout)
        return p.stdout.strip()

    def test_merge_then_close(self):
        BB.set_review('T', 'b1', 'PASS', 'r1', 'ok', expected_commit=self.commit)
        self.assertTrue(BB.merge('T', 'b1')['ok'])
        self.assertEqual(BB.record('T')['status'], 'merged')
        BB.close('T')
        rec = BB.record('T')
        self.assertEqual(rec['status'], 'closed')
        self.assertTrue(rec.get('closed'))
        self.assertFalse(os.path.isdir(self.worker))


if __name__ == '__main__':
    unittest.main()
