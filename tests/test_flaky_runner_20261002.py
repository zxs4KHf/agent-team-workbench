"""浏览器测试统一治理（2026-10-02）：识别、复跑诊断、只有已登记「待查」的偶发才容忍。"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import run_tests as R  # noqa: E402

FLAKY_MD = """| 测试 | 现象 | 根因 | 状态 |
|---|---|---|---|
| `tests/ui_a.py` test_1 | x | y | 2026-09-28 已修：断言不改 |
| `tests/ui_b.py`、`tests/test_notify_c.py` | x | y | 待查：负载高 |
"""


class FlakyRunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.md = os.path.join(self.tmp, "FLAKY.md")
        open(self.md, "w", encoding="utf-8").write(FLAKY_MD)
        self._run, self._log, self._root = R.run, R.log_flaky, R.ROOT
        R.log_flaky = lambda *a: None

    def tearDown(self):
        R.run, R.log_flaky = self._run, self._log

    def fake(self, *rcs):
        seq = list(rcs)
        R.run = lambda f, timeout: (f, seq.pop(0), 1.0, f"out-{f}")

    def test_known_flaky_only_pending(self):
        self.assertEqual(R.known_flaky(self.md), {"ui_b.py", "test_notify_c.py"})

    def test_detects_browser_tests_beyond_ui_prefix(self):
        self.assertTrue(R.is_browser("ui_anything.py"))
        self.assertTrue(R.is_browser("test_notify_page_60e8.py"))
        self.assertFalse(R.is_browser("test_flaky_runner_20261002.py".replace("flaky_runner", "allocator")))

    def test_pass_first_time_no_retry(self):
        self.fake(0)
        self.assertEqual(R.run_browser("ui_x.py", 10, set())[1], 0)

    def test_registered_flaky_passes_on_retry_with_tag(self):
        self.fake(1, 0)
        res = R.run_browser("ui_b.py", 10, {"ui_b.py"})
        self.assertEqual((res[1], res[4]), (0, "偶发·已登记"))
        self.assertIn("首次失败", res[3])

    def test_unregistered_flaky_still_fails(self):
        self.fake(1, 0)
        res = R.run_browser("ui_new.py", 10, {"ui_b.py"})
        self.assertNotEqual(res[1], 0)
        self.assertEqual(res[4], "偶发·未登记")

    def test_stable_failure_fails(self):
        self.fake(1, 1)
        res = R.run_browser("ui_b.py", 10, {"ui_b.py"})
        self.assertNotEqual(res[1], 0)
        self.assertEqual(res[4], "稳定失败")

    def test_digest_keeps_fail_lines_cut_off_by_tail(self):
        out = "PASS a\nFAIL  反向对照：说明滚动后丢掉\n" + "PASS x\n" * 500
        d = R.digest(out)
        self.assertIn("FAIL  反向对照", d, "只留结尾时这行会被截掉")
        self.assertTrue(d.endswith("PASS x\n"))
        self.assertEqual(R.digest("PASS\nFAIL z"), "PASS\nFAIL z", "失败行已在结尾里就不重复")

    def test_no_retry_flag(self):
        self.fake(1)
        self.assertNotEqual(R.run_browser("ui_b.py", 10, {"ui_b.py"}, retry=False)[1], 0)


if __name__ == "__main__":
    unittest.main()
