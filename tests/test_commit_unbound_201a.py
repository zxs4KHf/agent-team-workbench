"""返工点 7（2c0a）：build 提交流程的 commit_unbound。builder 零改动时 HEAD 仍是任务基线，
这种「提交号 = 基线」不能绑 PASS。一次性 Git 仓库里真跑 commit_work / set_review，不碰生产记录。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bus_build as BB


class CommitUnbound(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wb-unbound-')
        base = Path(self.tmp.name)
        self.repo, self.worker, store = base / 'main', base / 'builder', base / 'records'
        self.repo.mkdir(); store.mkdir()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(BB, 'BUILD_DIR', str(store)).start()
        mock.patch.object(BB, 'run_gates', return_value={'ok': True, 'items': []}).start()
        g = self.git
        g(self.repo, 'init', '-b', 'main'); g(self.repo, 'config', 'user.email', 't@localhost')
        g(self.repo, 'config', 'user.name', 'T'); g(self.repo, 'config', 'commit.gpgsign', 'false')
        g(self.repo, 'config', 'core.hooksPath', str(base / 'no-hooks'))
        (self.repo / 'app.py').write_text('X = 1\n', encoding='utf-8')
        g(self.repo, 'add', '.'); g(self.repo, 'commit', '-m', 'baseline')
        self.baseline = g(self.repo, 'rev-parse', 'HEAD')
        g(self.repo, 'worktree', 'add', '-b', 'b1', str(self.worker), 'HEAD')
        BB.save_record('t', {'task_id': 't', 'repo': str(self.repo), 'base': self.baseline,
                             'teammates': {'b1': {'path': str(self.worker), 'branch': 'b1',
                                                  'commit': '', 'review': '', 'merged': False}}})

    def git(self, d, *a):
        p = subprocess.run(['git', *a], cwd=d, capture_output=True, timeout=30)
        out = (p.stdout + p.stderr).decode('utf-8', 'replace')
        if p.returncode:
            raise AssertionError(out)
        return out.strip()

    def test_零改动标为_unbound(self):
        t = BB.commit_work('t', 'b1', '讨论型回复，什么都没改')
        self.assertEqual(t['commit'], self.baseline)          # HEAD 还停在基线
        self.assertTrue(t['commit_unbound'])
        self.assertEqual(t['changed_files'], [])

    def test_零改动时_PASS_被拒_RETURN_仍放行(self):
        t = BB.commit_work('t', 'b1', '无改动')
        self.assertTrue(t['commit_unbound'])
        with self.assertRaises(ValueError) as e:
            BB.set_review('t', 'b1', 'PASS', 'reviewer1', '看着没问题', expected_commit=t['commit'])
        self.assertIn('Unbound commit', str(e.exception))
        r = BB.set_review('t', 'b1', 'RETURN', 'reviewer1', '没有交付物', expected_commit=t['commit'])
        self.assertEqual(r['review'], 'RETURN')

    def test_有改动时照常绑定可审提交(self):
        (self.worker / 'app.py').write_text('X = 2\n', encoding='utf-8')
        t = BB.commit_work('t', 'b1', '改了一行')
        self.assertNotEqual(t['commit'], self.baseline)
        self.assertFalse(t['commit_unbound'])
        self.assertEqual(t['changed_files'], ['app.py'])
        rec = BB.set_review('t', 'b1', 'PASS', 'reviewer1', 'ok', expected_commit=t['commit'])
        self.assertEqual(rec['review'], 'PASS')
        self.assertEqual(rec['reviewed_commit'], t['commit'])

    def test_旧记录没有_unbound_字段也按_base_拦(self):
        # 这次改动之前提交过的记录里没有 commit_unbound：只比提交号与基线也要拦住
        BB.commit_work('t', 'b1', '无改动')
        rec = BB.record('t')
        rec['teammates']['b1'].pop('commit_unbound')
        BB.save_record('t', rec)
        with self.assertRaises(ValueError) as e:
            BB.set_review('t', 'b1', 'PASS', 'reviewer1', 'ok', expected_commit=self.baseline)
        self.assertIn('Unbound commit', str(e.exception))


if __name__ == '__main__':
    unittest.main()
