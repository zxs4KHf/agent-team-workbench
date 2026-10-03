"""改派施工单必须给新接手人开 worktree 席位；零交付的废单允许清理（3089）。

实证背景：施工单 20261001-085345-5724 先后改派给两个 Agent，两条分支都停在基线、0 提交。
原因是只有 bus_reassign._reassign_build 这一条路会调 bus_build.add_teammate，
守护的 auto_reassign、页面手动改派、free_stalled 的通用兜底都直接调 bus_core.reassign —— 不开席位。

跑法：py -3 tests/test_reassign_build_seat_3089.py
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
import bus_build_safety as BS


def git(repo, *args):
    subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)


class BuildSeatBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wb-seat-3089-')
        base = Path(self.tmp.name)
        self.repo = base / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-b', 'main')
        git(self.repo, 'config', 'user.email', 'wb@localhost')
        git(self.repo, 'config', 'user.name', 'wb')
        (self.repo / 'a.txt').write_text('a\n', encoding='utf-8')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-m', 'init')
        self.builds, self.outbox, self.peers, self.inbox = base / 'builds', base / 'outbox', base / 'peers', base / 'inbox'
        for d in (self.builds, self.outbox, self.peers, self.inbox):
            d.mkdir()
        self.patches = [mock.patch.object(BB, 'BUILD_DIR', str(self.builds)),
                        mock.patch.object(B, 'OUTBOX', str(self.outbox)),
                        mock.patch.object(B, 'PEERS', str(self.peers)),
                        mock.patch.object(B, 'INBOX', str(self.inbox))]
        for p in self.patches:
            p.start()
        self.tid = '20261002-070549-3089'

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def open_build(self, peer='p1'):
        return BB.open_worktrees(self.tid, [peer], str(self.repo))

    def open_task(self, peer='p1', mode='build'):
        B.write_json(str(self.outbox / f'{self.tid}.meta.json'),
                     {'id': self.tid, 'from': 'controller', 'peers': [peer], 'mode': mode, 'question': 'q'})
        (self.inbox / peer).mkdir(exist_ok=True)
        B.write_json(str(self.inbox / peer / f'{self.tid}.taken'), {'id': self.tid, 'mode': mode, 'question': 'q'})


class ReassignOpensSeat(BuildSeatBase):
    def test_reassigned_builder_gets_worktree_and_branch(self):
        """修复前失败：p2 收到任务却没有 worktree（teammates 里根本没有 p2）。"""
        self.open_build('p1')
        self.open_task('p1')
        r = B.reassign(self.tid, 'p1', to_peer='p2', by='guard')
        self.assertEqual(r['to'], 'p2')
        tm = (BB.record(self.tid).get('teammates') or {}).get('p2')
        self.assertIsNotNone(tm, 'p2 没有拿到施工席位')
        self.assertTrue(os.path.isdir(tm['path']), 'p2 的 worktree 没有真正建出来')
        task = B.read_json(str(self.inbox / 'p2' / f'{self.tid}.json'), None)
        self.assertEqual(task['worktree'], tm['path'])
        self.assertEqual(task['branch'], tm['branch'])

    def test_original_builder_worktree_is_kept(self):
        rec = self.open_build('p1')
        self.open_task('p1')
        B.reassign(self.tid, 'p1', to_peer='p2', by='guard')
        self.assertTrue(os.path.isdir(rec['teammates']['p1']['path']), '原施工者的 worktree 不应被删')
        self.assertIn('p1', BB.record(self.tid)['teammates'])

    def test_seat_is_idempotent_on_second_reassign(self):
        self.open_build('p1')
        self.open_task('p1')
        B.reassign(self.tid, 'p1', to_peer='p2', by='guard')
        first = BB.record(self.tid)['teammates']['p2']
        B.reassign(self.tid, 'p2', to_peer='p3', by='guard')
        B.reassign(self.tid, 'p3', to_peer='p2', by='guard')
        self.assertEqual(BB.record(self.tid)['teammates']['p2'], first)

    def test_non_build_task_is_untouched(self):
        self.open_task('p1', mode='independent')
        B.reassign(self.tid, 'p1', to_peer='p2', by='guard')
        task = B.read_json(str(self.inbox / 'p2' / f'{self.tid}.json'), None)
        self.assertNotIn('worktree', task)
        self.assertEqual(BB.record(self.tid), {})


class CloseAbandoned(BuildSeatBase):
    def test_zero_commit_clean_branch_can_be_closed(self):
        """零交付的废单：0 提交 + 干净 + 无 stash，允许直接清理。"""
        rec = self.open_build('p1')
        path = rec['teammates']['p1']['path']
        self.assertTrue(BS.close(self.tid))
        self.assertFalse(os.path.isdir(path))
        self.assertEqual(BB.record(self.tid)['status'], 'closed')

    def test_dirty_worktree_still_refuses(self):
        rec = self.open_build('p1')
        Path(rec['teammates']['p1']['path'], 'scratch.txt').write_text('wip\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            BS.close(self.tid)
        self.assertTrue(os.path.isdir(rec['teammates']['p1']['path']))

    def test_branch_with_commits_still_refuses(self):
        rec = self.open_build('p1')
        path = rec['teammates']['p1']['path']
        Path(path, 'b.txt').write_text('b\n', encoding='utf-8')
        git(path, 'add', '-A')
        git(path, 'commit', '-m', 'work')
        with self.assertRaises(ValueError):
            BS.close(self.tid)
        self.assertTrue(os.path.isdir(path))

    def test_stashed_work_still_refuses(self):
        rec = self.open_build('p1')
        path = rec['teammates']['p1']['path']
        Path(path, 'a.txt').write_text('changed\n', encoding='utf-8')
        git(path, 'stash', 'push', '-m', 'wip')
        with self.assertRaises(ValueError):
            BS.close(self.tid)
        self.assertTrue(os.path.isdir(path))


if __name__ == '__main__':
    unittest.main(verbosity=2)
