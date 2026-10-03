# -*- coding: utf-8 -*-
"""merge_path 三态与兼容层回归（合并稿 20260929-131847-e1cf / 任务 9a96）。

一律用临时目录，不碰生产 console/ peers/ projects/。
    py -3 tests/test_merge_path_9a96.py
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bus_core as B
import bus_projects as P

MERGE_DOC = """# 合并稿

## 共识要点

1. 随便。

## 最终方案

- 第一步：改 A。
- 第二步：改 B。

## 验证依据

跑测试。
"""


class MergePathStates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wb9a96-")
        self._proj, self._ws, self._root = P.PROJECTS, B.WORKSPACE, B.ROOT
        P.PROJECTS = os.path.join(self.tmp, "projects")
        B.WORKSPACE = B.ROOT = self.tmp
        os.makedirs(P.PROJECTS, exist_ok=True)
        self.p = P.create_project("项目9a96", ["a1"], "independent", [])
        self.slug = self.p["slug"]

    def tearDown(self):
        P.PROJECTS, B.WORKSPACE, B.ROOT = self._proj, self._ws, self._root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, rel, text=MERGE_DOC):
        full = os.path.join(self.tmp, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)
        return full

    def dec(self, tid, mp):
        return P.upsert_decision(self.slug, tid, status="adopted", title="t", merge_path=mp)

    # ---- 正常路径 ----
    def test_规范路径可导出且path_source为canonical(self):
        tid = "20260929-000001-aaaa"
        self.put("log/2026-09-29/%s.merge.md" % tid)
        self.dec(tid, "log/2026-09-29/%s.merge.md" % tid)
        r = P.export_task_pack(self.slug, tid)
        self.assertIn("## 最终方案", r["markdown"])
        self.assertEqual(r["path_source"], "canonical", "规范路径不能被兼容层吃掉")

    # ---- 兼容层 ----
    def test_旧前缀旧日期也能导出且标记降级(self):
        tid = "20260929-000002-bbbb"
        self.put("log/2026-09-29/%s.merge.md" % tid)          # 文件在规范位置
        self.dec(tid, "log/2026-09-29/%s.merge.md" % tid)
        # 绕过写入侧规范化，直接把历史形态塞回记录里，模拟存量数据
        items = P.decisions(self.slug)
        for rec in items:
            if rec["task_id"] == tid:
                rec["merge_path"] = "workbench/bus/log/20260929/%s.merge.md" % tid
        P.save_decisions(self.slug, items)
        r = P.export_task_pack(self.slug, tid)
        self.assertIn("## 最终方案", r["markdown"])
        self.assertEqual(r["path_source"], "legacy_prefix")
        self.assertTrue(r["merge_path_used"].startswith("log/2026-09-29/"))

    # ---- 三态 ----
    def test_三态各自报错且不返回占位文案(self):
        t1 = "20260929-000003-cccc"
        self.dec(t1, "")
        with self.assertRaises(P.MergePathError) as e1:
            P.export_task_pack(self.slug, t1)
        self.assertEqual(e1.exception.code, P.MERGE_PATH_MISSING)

        t2 = "20260929-000004-dddd"
        self.dec(t2, "log/2026-09-29/%s.merge.md" % t2)       # 文件不存在
        with self.assertRaises(P.MergePathError) as e2:
            P.export_task_pack(self.slug, t2)
        self.assertEqual(e2.exception.code, P.MERGE_FILE_NOT_FOUND)

        t3 = "20260929-000005-eeee"
        self.put("log/2026-09-29/%s.merge.md" % t3, "# 只有标题\n\n## 共识要点\n\n无。\n")
        self.dec(t3, "log/2026-09-29/%s.merge.md" % t3)
        with self.assertRaises(P.MergePathError) as e3:
            P.export_task_pack(self.slug, t3)
        self.assertEqual(e3.exception.code, P.MERGE_NO_FINAL_PLAN_SECTION)

        for exc in (e1, e2, e3):
            self.assertNotIn("（合并稿不存在或没有", str(exc.exception), "不得回退成占位文案")

    # ---- 防误读 ----
    def test_peer应答文件不得被当成合并稿(self):
        tid = "20260929-000006-ffff"
        self.put("log/2026-09-29/%s.fable3.md" % tid, "# 我是 peer 应答\n\n## 最终方案\n\n别读我。\n")
        self.dec(tid, "log/2026-09-29/%s.merge.md" % tid)     # 登记的是合并稿，但只有应答文件在
        with self.assertRaises(P.MergePathError) as e:
            P.export_task_pack(self.slug, tid)
        self.assertEqual(e.exception.code, P.MERGE_FILE_NOT_FOUND)

    # ---- 写入侧规范化 ----
    def test_写入侧规范化(self):
        tid = "20260930-000007-9999"
        rec = P.upsert_decision(self.slug, tid, status="adopted",
                                merge_path="workbench/bus/log/20260930/x.merge.md")
        self.assertTrue(rec["merge_path"].startswith("log/2026-09-30/"), rec["merge_path"])
        again = next(x for x in P.decisions(self.slug) if x["task_id"] == tid)
        self.assertEqual(again["merge_path"], "log/2026-09-30/x.merge.md")

    def test_normalize_merge_path_幂等(self):
        self.assertEqual(P.normalize_merge_path("log/2026-09-30/x.md"), "log/2026-09-30/x.md")
        self.assertEqual(P.normalize_merge_path("AgentTeamWorkbench/log/20260930/x.md"), "log/2026-09-30/x.md")
        self.assertEqual(P.normalize_merge_path(""), "")

    # ---- adopt 侧不静默 ----
    def test_read_merge_plan_给出可区分原因(self):
        with self.assertRaises(P.MergePathError) as e:
            P.read_merge_plan("t", "")
        self.assertEqual(e.exception.code, P.MERGE_PATH_MISSING)
        self.assertIn("merge_path", str(e.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
