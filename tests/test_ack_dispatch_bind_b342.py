# -*- coding: utf-8 -*-
"""b342：dispatch 回执绑定。ok=True 但绑不上时绝不能把占位决策写成 cancelled。

全部用临时目录，不碰生产 console / projects / outbox。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bus_core as B  # noqa: E402
import bus_projects as P  # noqa: E402


class AckBindTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wb-ackbind-")
        root = self.tmp.name
        self.saved = (P.PROJECTS, P.COMMANDS, P.DONE, P.BUILDS, B.OUTBOX, B.ROOT)
        P.PROJECTS = os.path.join(root, "projects")
        P.COMMANDS = os.path.join(root, "console", "commands")
        P.DONE = os.path.join(root, "console", "done")
        P.BUILDS = os.path.join(root, "console", "builds")
        B.OUTBOX = os.path.join(root, "outbox")
        B.ROOT = root
        for d in (P.PROJECTS, P.COMMANDS, P.DONE, P.BUILDS, B.OUTBOX, os.path.join(root, "log", "2026-09-29")):
            os.makedirs(d, exist_ok=True)
        self.mk_project("协作工作台", name="协作工作台")

    def tearDown(self):
        P.PROJECTS, P.COMMANDS, P.DONE, P.BUILDS, B.OUTBOX, B.ROOT = self.saved
        self.tmp.cleanup()

    # ---- fixtures -------------------------------------------------------
    def mk_project(self, slug, name=""):
        d = os.path.join(P.PROJECTS, slug)
        os.makedirs(d, exist_ok=True)
        B.write_json(os.path.join(d, "project.json"), {"slug": slug, "name": name or slug, "decisions": []})
        B.write_json(os.path.join(d, "decisions.json"), [])

    def mk_command(self, cid, project="协作工作台", kind="dispatch"):
        B.write_json(os.path.join(P.COMMANDS, cid + ".json"),
                     {"id": cid, "kind": kind, "payload": {"project": project, "question": "问题原文"},
                      "created": B.now_iso(), "status": "queued"})

    def mk_placeholder(self, cid, slug="协作工作台", title="用户原始提问"):
        P.upsert_decision(slug, "pending-" + cid, status="draft", title=title, peers=["opus4"], mode="independent")

    def mk_real_task(self, tid):
        B.write_json(os.path.join(B.OUTBOX, tid + ".meta.json"), {"task_id": tid, "peers": ["opus4"]})

    def rec(self, task_id, slug="协作工作台"):
        return next((x for x in P.decisions(slug) if x["task_id"] == task_id), None)

    # ---- 绑定成功的三条通道 ------------------------------------------------
    def test_standard_result_binds(self):
        cid, tid = "20260929-120000-aaaa", "20260929-120100-bbbb"
        self.mk_command(cid), self.mk_placeholder(cid), self.mk_real_task(tid)
        out = P.ack_command(cid, f"task_id: {tid}")
        self.assertIn("ACK", out)
        self.assertNotIn("WARNING", out)
        r = self.rec(tid)
        self.assertEqual(r["status"], "dispatched")
        self.assertIsNone(self.rec("pending-" + cid))
        self.assertNotIn("warning", r)

    def test_non_standard_result_with_bare_id_binds(self):
        cid, tid = "20260929-120000-cccc", "20260929-120200-dddd"
        self.mk_command(cid), self.mk_placeholder(cid), self.mk_real_task(tid)
        P.ack_command(cid, f"已派发给 opus4，任务 {tid}，等回答")
        self.assertEqual(self.rec(tid)["status"], "dispatched")

    def test_structured_task_id_binds_without_result(self):
        cid, tid = "20260929-120000-eeee", "20260929-120300-ffff"
        self.mk_command(cid), self.mk_placeholder(cid), self.mk_real_task(tid)
        P.ack_command(cid, "已处理", task_id=tid)
        self.assertEqual(self.rec(tid)["status"], "dispatched")
        self.assertEqual(self.rec(tid)["task_id_source"], "structured")

    def test_task_id_in_log_dir_is_verifiable(self):
        cid, tid = "20260929-120000-0a0a", "20260929-120400-0b0b"
        self.mk_command(cid), self.mk_placeholder(cid)
        Path(B.ROOT, "log", "2026-09-29", tid + ".merge.md").write_text("x", encoding="utf-8")
        P.ack_command(cid, f"task_id: {tid}")
        self.assertEqual(self.rec(tid)["status"], "dispatched")

    # ---- 绑不上：只挂 warning，绝不 cancel --------------------------------
    def test_unverifiable_candidate_is_refused_and_not_cancelled(self):
        cid, ghost = "20260929-120000-1111", "20260929-999999-9999"
        self.mk_command(cid), self.mk_placeholder(cid)
        out = P.ack_command(cid, f"task_id: {ghost}")
        self.assertIn("WARNING[ack_task_unbound]", out)
        r = self.rec("pending-" + cid)
        self.assertEqual(r["status"], "draft")            # 原状态保持
        self.assertEqual(r["title"], "用户原始提问")        # 标题保持
        self.assertEqual(r["warning"]["code"], "ack_task_unbound")
        self.assertEqual(r["warning"]["command_id"], cid)
        self.assertNotEqual(r["status"], "cancelled")
        done = B.read_json(os.path.join(P.DONE, cid + ".json"))
        self.assertEqual(done["ack_warning"]["code"], "ack_task_unbound")   # 警告回到工作台，不只写日志

    def test_ok_true_without_any_id_does_not_cancel(self):
        cid = "20260929-120000-2222"
        self.mk_command(cid), self.mk_placeholder(cid)
        P.ack_command(cid, "好的，已经安排下去了")
        self.assertEqual(self.rec("pending-" + cid)["status"], "draft")

    def test_ok_false_still_cancels(self):
        cid = "20260929-120000-3333"
        self.mk_command(cid), self.mk_placeholder(cid)
        P.ack_command(cid, "没有在线 Agent，派发失败", ok=False)
        r = self.rec("pending-" + cid)
        self.assertEqual(r["status"], "cancelled")
        self.assertEqual(r["summary"], "没有在线 Agent，派发失败")

    # ---- project 解析 -----------------------------------------------------
    def test_display_name_is_normalised_to_slug(self):
        self.mk_project("demo", name="Demo 项目")
        cid, tid = "20260929-120000-4444", "20260929-120500-4a4a"
        self.mk_command(cid, project="Demo 项目")
        self.mk_placeholder(cid, slug="demo")
        self.mk_real_task(tid)
        out = P.ack_command(cid, f"task_id: {tid}")
        self.assertIn("ack_project_display_name", out)
        self.assertEqual(self.rec(tid, "demo")["status"], "dispatched")
        self.assertFalse(os.path.exists(os.path.join(P.PROJECTS, "demo-项目")))  # 没有 slugify 出新目录

    def test_empty_project_is_recovered_by_placeholder_lookup(self):
        cid, tid = "20260929-120000-5555", "20260929-120600-5a5a"
        self.mk_command(cid, project="")
        self.mk_placeholder(cid)
        self.mk_real_task(tid)
        P.ack_command(cid, f"task_id: {tid}")
        self.assertEqual(self.rec(tid)["status"], "dispatched")

    def test_ambiguous_placeholder_across_projects_refuses(self):
        self.mk_project("重力回廊", name="重力回廊")
        cid, tid = "20260929-120000-6666", "20260929-120700-6a6a"
        self.mk_command(cid, project="")
        self.mk_placeholder(cid)
        self.mk_placeholder(cid, slug="重力回廊")
        self.mk_real_task(tid)
        out = P.ack_command(cid, f"task_id: {tid}")
        self.assertIn("ack_project_ambiguous", out)
        for slug in ("协作工作台", "重力回廊"):
            self.assertEqual(self.rec("pending-" + cid, slug)["status"], "draft")

    def test_unknown_project_name_refuses_without_cancel(self):
        cid = "20260929-120000-7777"
        self.mk_command(cid, project="不存在的项目")
        self.mk_placeholder(cid)
        out = P.ack_command(cid, "task_id: 20260929-120800-7a7a")
        self.assertIn("ack_project_unknown", out)
        self.assertEqual(self.rec("pending-" + cid)["status"], "draft")

    # ---- 幂等 / 可重试 / 并发 ---------------------------------------------
    def test_repeat_ack_is_idempotent(self):
        cid, ghost = "20260929-120000-8888", "20260929-999999-8a8a"
        self.mk_command(cid), self.mk_placeholder(cid)
        P.ack_command(cid, f"task_id: {ghost}")
        n1 = len(self.rec("pending-" + cid)["history"])
        P.ack_command(cid, f"task_id: {ghost}")
        self.assertEqual(len(self.rec("pending-" + cid)["history"]), n1)   # warning 不重复追加

    def test_second_ack_after_success_is_already_done(self):
        cid, tid = "20260929-120000-9999", "20260929-120900-9a9a"
        self.mk_command(cid), self.mk_placeholder(cid), self.mk_real_task(tid)
        P.ack_command(cid, f"task_id: {tid}")
        out = P.ack_command(cid, f"task_id: {tid}")
        self.assertIn("already done", out)                                  # 竞争的第二个进程不会重复写
        self.assertEqual(len([x for x in P.decisions("协作工作台") if x["task_id"] == tid]), 1)

    def test_decision_write_failure_keeps_command_retriable(self):
        cid, tid = "20260929-120000-abab", "20260929-121000-acac"
        self.mk_command(cid), self.mk_placeholder(cid), self.mk_real_task(tid)
        real = P.save_decisions

        def boom(*a, **k):
            raise OSError("磁盘写失败")

        P.save_decisions = boom
        try:
            with self.assertRaises(OSError):
                P.ack_command(cid, f"task_id: {tid}")
        finally:
            P.save_decisions = real
        self.assertTrue(os.path.exists(os.path.join(P.COMMANDS, cid + ".json")))   # 命令没被吞
        self.assertFalse(os.path.exists(os.path.join(P.DONE, cid + ".json")))
        P.ack_command(cid, f"task_id: {tid}")                                      # 重试成功
        self.assertEqual(self.rec(tid)["status"], "dispatched")

    # ---- 历史报告：只读且分类正确 -------------------------------------------
    def test_history_report_is_read_only_and_classifies(self):
        import importlib.util
        # 真任务：done 里有可验证 id，但决策被旧逻辑写成了 cancelled
        cid1, tid1 = "20260929-130000-1a1a", "20260929-130100-1b1b"
        self.mk_placeholder(cid1)
        self.mk_real_task(tid1)
        P.upsert_decision("协作工作台", "pending-" + cid1, status="cancelled")
        B.write_json(os.path.join(P.DONE, cid1 + ".json"), {"id": cid1, "kind": "dispatch", "status": "done", "result": f"task_id: {tid1}"})
        # 真取消
        cid2 = "20260929-130000-2a2a"
        self.mk_placeholder(cid2)
        P.upsert_decision("协作工作台", "pending-" + cid2, status="cancelled")
        B.write_json(os.path.join(P.DONE, cid2 + ".json"), {"id": cid2, "kind": "dispatch", "status": "failed", "result": "用户取消"})
        # 无 done 记录
        cid3 = "20260929-130000-3a3a"
        self.mk_placeholder(cid3)
        P.upsert_decision("协作工作台", "pending-" + cid3, status="cancelled")

        path = Path(__file__).resolve().parents[1] / "scripts" / "report_pending_ack_b342.py"
        spec = importlib.util.spec_from_file_location("report_b342", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        before = json.dumps(P.decisions("协作工作台"), ensure_ascii=False, sort_keys=True)
        kinds = {c: mod.classify("协作工作台", self.rec("pending-" + c))["kind"] for c in (cid1, cid2, cid3)}
        self.assertEqual(kinds[cid1], "real_task")
        self.assertEqual(kinds[cid2], "真取消")
        self.assertEqual(kinds[cid3], "unknown")
        self.assertEqual(before, json.dumps(P.decisions("协作工作台"), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
