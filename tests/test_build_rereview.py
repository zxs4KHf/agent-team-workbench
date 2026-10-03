"""WORKFLOW 规则 6 的已知差距：施工分支审查后 main 前进，合并结果要明确标出「需重审」。一次性 Git 仓库里真跑。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bus_build as BB
import bus_build_safety as S

LINES = ''.join(f'LINE_{i} = {i}\n' for i in range(1, 21))


class RereviewHint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wb-rereview-')
        base = Path(self.tmp.name)
        self.repo, self.worker, store = base/'main', base/'builder', base/'records'
        self.repo.mkdir(); store.mkdir()
        self.p1 = mock.patch.object(BB, 'BUILD_DIR', str(store)); self.p1.start()
        self.p2 = mock.patch.object(BB, 'run_gates', return_value={'ok': True, 'checks': []}); self.p2.start()
        g = self.git
        g(self.repo, 'init', '-b', 'main'); g(self.repo, 'config', 'user.email', 't@localhost'); g(self.repo, 'config', 'user.name', 'T')
        g(self.repo, 'config', 'commit.gpgsign', 'false'); g(self.repo, 'config', 'core.hooksPath', str(base/'no-hooks'))
        (self.repo/'.gitignore').write_text('.wt/\n', encoding='utf-8')
        (self.repo/'app.py').write_text(LINES, encoding='utf-8'); (self.repo/'notes.txt').write_text('notes\n', encoding='utf-8')
        g(self.repo, 'add', '.'); g(self.repo, 'commit', '-m', 'baseline'); self.baseline = g(self.repo, 'rev-parse', 'HEAD')
        g(self.repo, 'worktree', 'add', '-b', 'b1', str(self.worker), 'HEAD')
        (self.worker/'app.py').write_text(LINES.replace('LINE_2 = 2', 'LINE_2 = "builder"'), encoding='utf-8')
        g(self.worker, 'commit', '-am', 'candidate'); self.commit = g(self.worker, 'rev-parse', 'HEAD')
        BB.save_record('t', {'task_id': 't', 'repo': str(self.repo), 'base': self.baseline,
                             'teammates': {'b1': {'path': str(self.worker), 'branch': 'b1', 'commit': self.commit, 'review': '', 'merged': False, 'gates': {'ok': True}}}})

    def tearDown(self):
        self.p2.stop(); self.p1.stop(); self.tmp.cleanup()

    def git(self, d, *a):
        p = subprocess.run(['git', *a], cwd=d, capture_output=True, text=True, encoding='utf-8', timeout=30)
        if p.returncode: raise AssertionError(p.stderr or p.stdout)
        return p.stdout.strip()

    def approve(self):
        return BB.set_review('t', 'b1', 'PASS', 'reviewer1', 'ok', expected_commit=self.commit)

    def main_commit(self, path, text, msg):
        (self.repo/path).write_text(text, encoding='utf-8'); self.git(self.repo, 'commit', '-am', msg)

    def test_review_records_main_position(self):
        self.approve()
        self.assertEqual(BB.record('t')['teammates']['b1']['review_main'], self.baseline)

    def test_main_unchanged_no_rereview(self):
        self.approve(); r = BB.merge('t', 'b1')
        self.assertTrue(r['ok'], r); self.assertIs(r['rereview']['needed'], False)

    def test_main_advanced_unrelated_file_low_risk(self):
        self.approve(); self.main_commit('notes.txt', 'notes v2\n', 'main moves on')
        r = BB.merge('t', 'b1')
        self.assertTrue(r['ok'], r); h = r['rereview']
        self.assertIs(h['needed'], True); self.assertEqual(h['main_advanced_by'], 1)
        self.assertEqual(h['overlap_files'], []); self.assertEqual(h['risk'], 'low')
        self.assertEqual(BB.record('t')['teammates']['b1']['rereview'], h, '提示写进施工记录，事后可查')

    def test_main_touched_same_file_high_risk(self):
        self.approve()
        self.main_commit('app.py', LINES.replace('LINE_19 = 19', 'LINE_19 = "main"'), 'main edits same file, other line')
        self.main_commit('notes.txt', 'notes v2\n', 'second')
        r = BB.merge('t', 'b1')
        self.assertTrue(r['ok'], r); h = r['rereview']
        self.assertIs(h['needed'], True); self.assertEqual(h['main_advanced_by'], 2)
        self.assertEqual(h['overlap_files'], ['app.py']); self.assertEqual(h['risk'], 'high')
        self.assertIn('建议重审', h['message'])
        merged = (self.repo/'app.py').read_text(encoding='utf-8')
        self.assertIn('LINE_2 = "builder"', merged); self.assertIn('LINE_19 = "main"', merged)

    def test_legacy_record_without_review_main(self):
        self.approve()
        rec = BB.record('t'); rec['teammates']['b1'].pop('review_main'); BB.save_record('t', rec)
        self.main_commit('notes.txt', 'notes v2\n', 'main moves on')
        r = BB.merge('t', 'b1')
        self.assertTrue(r['ok'], r); self.assertIsNone(r['rereview']['needed']); self.assertIn('无法判断', r['rereview']['message'])


    def test_unreachable_review_main_does_not_block_merge(self):
        # fable2 a180：记录里有位置但仓库里已找不到——合并必须照常成功，提示如实写无法判断
        self.approve()
        rec = BB.record('t'); rec['teammates']['b1']['review_main'] = '0' * 40; BB.save_record('t', rec)
        self.main_commit('notes.txt', 'notes v2\n', 'main moves on')
        r = BB.merge('t', 'b1')
        self.assertTrue(r['ok'], r); self.assertIsNone(r['rereview']['needed']); self.assertIn('找不到', r['rereview']['message'])
        self.assertNotIn('None', r['rereview']['message'])
        self.assertIn('notes v2', (self.repo/'notes.txt').read_text(encoding='utf-8'))

    def test_unreachable_base_is_unknown_not_low_risk(self):
        # 分支基点取不到：不能因为「没取到文件」就报风险低
        self.approve(); self.main_commit('notes.txt', 'notes v2\n', 'main moves on')
        head = self.git(self.repo, 'rev-parse', 'HEAD')
        h = S.rereview_hint(str(self.repo), 'f' * 40, self.commit, self.baseline, head)
        self.assertIsNone(h['needed'], h); self.assertNotIn('risk', h); self.assertIn('无法判断', h['message'])

    def test_hint_exception_does_not_block_merge(self):
        # 兜底：提示计算本身抛任何异常，合并也不回滚
        self.approve()
        with mock.patch.object(S, 'rereview_hint', side_effect=RuntimeError('boom')):
            r = BB.merge('t', 'b1')
        self.assertTrue(r['ok'], r); self.assertIsNone(r['rereview']['needed']); self.assertIn('boom', r['rereview']['message'])
        self.assertTrue(self.git(self.repo, 'rev-parse', 'HEAD').startswith(r['merge_commit'][:7]), 'main 确实前进到合并提交')


class ImplementationLabel(unittest.TestCase):
    """界面施工状态行：已合并且审后 main 前进时，标签与结构化字段都要带上「需重审」"""
    def snap(self, rereview):
        t = {'merged': True, 'merge_commit': 'abcdef1234', 'commit': '1234567abc'}
        if rereview is not None: t['rereview'] = rereview
        return {'builds': {'t': {'teammates': {'b1': t}}}, 'taken': set(), 'builders': {'online_n': 1, 'groups': [], 'members': []}, 'commands': {}, 'project_commands': {}}

    def test_labels(self):
        import bus_projects as P
        o = P.implementation_of('t', self.snap(None)); self.assertNotIn('rereview', o); self.assertNotIn('重审', o['label'])
        o = P.implementation_of('t', self.snap({'needed': False})); self.assertNotIn('rereview', o)
        o = P.implementation_of('t', self.snap({'needed': None, 'message': '无法判断'})); self.assertNotIn('rereview', o, '旧记录无法判断时不乱报')
        o = P.implementation_of('t', self.snap({'needed': True, 'risk': 'low', 'message': 'm', 'overlap_files': []}))
        self.assertEqual(o['rereview']['risk'], 'low'); self.assertIn('main 已前进（未碰同一文件）', o['label'])
        o = P.implementation_of('t', self.snap({'needed': True, 'risk': 'high', 'message': 'm', 'overlap_files': ['app.py']}))
        self.assertEqual(o['rereview']['overlap_files'], ['app.py']); self.assertIn('需重审', o['label']); self.assertEqual(o['state'], 'merged')


if __name__ == '__main__':
    unittest.main()
