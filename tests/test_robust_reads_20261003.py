"""接手积压（2026-10-03）三件小事：读文件撞上原子替换、「已实施」兜底误判、审查包截断看不全。临时目录 / 打桩，不碰运行数据。"""
import builtins
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bus_build as BB              # noqa: E402
import bus_build_safety as BS       # noqa: E402
import bus_core as B                # noqa: E402
import bus_web as W                 # noqa: E402


class ReadJsonRetry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="robust-")
        self.path = os.path.join(self.tmp, "d.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump([{"task_id": "x"}], f)
        self.real_open = builtins.open

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def flaky_open(self, fails):
        state = {"n": 0}
        real = B._open_shared

        def fake(path):
            if path == self.path and state["n"] < fails:
                state["n"] += 1
                raise PermissionError(13, "另一个程序正在使用此文件")
            return real(path)
        return fake

    def test_retries_through_brief_sharing_violation(self):
        with mock.patch.object(B, "_open_shared", self.flaky_open(3)):
            self.assertEqual(B.read_json(self.path, []), [{"task_id": "x"}])

    def test_persistent_permission_error_is_raised_not_defaulted(self):
        # 返回默认值会让先读后写的路径（upsert_decision）拿空表覆盖整个文件
        with mock.patch.object(B, "_open_shared", self.flaky_open(99)), mock.patch.object(B.time, "sleep", lambda s: None):
            with self.assertRaises(PermissionError):
                B.read_json(self.path, [])

    @unittest.skipUnless(os.name == "nt", "只有 Windows 有这个问题")
    def test_reader_no_longer_blocks_atomic_replace(self):
        # 2026-10-03 实测：工作台每秒读决策表，另一个进程原子替换同一个文件 6 次重试全失败
        with B._open_shared(self.path) as held:
            B.write_json(self.path, [{"task_id": "new"}])          # 读的句柄还开着，替换照样成功
            self.assertEqual(json.load(held), [{"task_id": "x"}], "读的一方拿到打开那一刻的完整旧内容")
        self.assertEqual(B.read_json(self.path), [{"task_id": "new"}])
        with self.real_open(self.path, encoding="utf-8"):             # 对照：普通 open 持有时替换被拒
            with self.assertRaises(PermissionError):
                B.write_json(self.path, [{"task_id": "blocked"}])

    def test_missing_and_corrupt_still_default(self):
        self.assertEqual(B.read_json(os.path.join(self.tmp, "none.json"), "d"), "d")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{bad")
        self.assertEqual(B.read_json(self.path, "d"), "d")


class DeclaresImplemented(unittest.TestCase):
    def test_only_complete_unqualified_statements_count(self):
        yes = ["本任务不需要改动。", "- **本任务不需要改动。**", "结论：本任务已上线，见 abc123。", "本轮无需实施"]
        no = ["本任务不需要改动吗？仍需验证才能决定。", "本任务不需要改动前端，但仍需要实施后端修复。", "> 本任务不需要改动。",
              "```\n本任务不需要改动。\n```", "本任务不需要改动前端。", "已上线，但还要补测试。"]
        for s in yes:
            self.assertTrue(W.declares_implemented(s), s)
        for s in no:
            self.assertFalse(W.declares_implemented(s), s)


class ReviewPackTruncation(unittest.TestCase):
    def test_truncated_pack_states_counts_and_lists_all_files(self):
        diff = "".join(f"diff --git a/f{i}.py b/f{i}.py\n" + "+x\n" * 50 for i in range(10))
        stat = "\n".join(f" f{i}.py | 50 +" for i in range(10))
        rec = {"repo": "r", "teammates": {"w1": {"commit": "c" * 40, "gates": {}}}}
        target = {"task_id": "T", "teammate": "w1", "commit": "c" * 40, "base": "b" * 40, "main_head": "m" * 40}
        with mock.patch.object(BB, "record", lambda t: rec), mock.patch.object(BS, "_commit", lambda repo, c: c), \
             mock.patch.object(BS, "_git", lambda repo, *a: stat if "--stat=200" in a else diff):
            pack = BS.review_pack("T", "w1", max_chars=300, target=target)
            full = BS.review_pack("T", "w1", max_chars=10 ** 6, target=target)
        self.assertIn("本 diff 已截断：共 10 个文件", pack)
        self.assertIn("f9.py | 50", pack, "被截掉的文件也在清单里")
        self.assertNotIn("已截断", full, "没截断就不加")


if __name__ == "__main__":
    unittest.main()
