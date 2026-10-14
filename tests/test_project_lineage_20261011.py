# -*- coding: utf-8 -*-
"""跨项目串台修复（20261011-143345-89e7）：任务归属全链路以 meta.project 为准。

验收对照（任务包逐条）：
1. A 项目派发的任务，即使在 B 的上下文里执行 post，决策也只写入 A；
2. 旧任务（meta 无 project）回落到当前项目时，返回体带 _notice；
3. 反查缓存生效：第二次反查不再读盘（打桩计数验证）；
4. 不带 project 的派发 meta 形状不变（老调用方回归）；
5. 负对照：短路 project_for_task 后用例 1 的路由失效（决策错落到 B），证明测试真的在测归属解析。

全部跑在临时目录，不碰生产 peers/ projects/。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bus_core as B        # noqa: E402
import bus_projects as P    # noqa: E402


def _make_meta(outbox: str, task_id: str, project: str = "") -> dict:
    """手工摆一份 outbox meta（形状照抄 bus_core.dispatch 的落盘物）。"""
    meta = {"id": task_id, "from": "controller", "peers": ["p1"], "offline_at_dispatch": [], "mode": "independent",
            "question": "q", "angles": {}, "min_answers": 1, "context_files": [], "created": "2026-10-11T00:00:00-04:00",
            "deadline": "2026-10-11T01:00:00-04:00", "deadline_ts": 1791750000.0, "substituted": {}}
    if project:
        meta["project"] = project
    B.write_json(os.path.join(outbox, task_id + ".meta.json"), meta)
    return meta


class LineageBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lineage_")
        self.projects = os.path.join(self.tmp, "projects")
        os.makedirs(self.projects)
        self.outbox = os.path.join(self.tmp, "outbox")
        os.makedirs(self.outbox)
        self.p = [mock.patch.object(P, "PROJECTS", self.projects),
                  mock.patch.object(P, "COMMANDS", os.path.join(self.tmp, "commands")),
                  mock.patch.object(P, "DONE", os.path.join(self.tmp, "done")),
                  mock.patch.object(B, "OUTBOX", self.outbox),
                  mock.patch.object(B, "INBOX", os.path.join(self.tmp, "inbox"))]
        for x in self.p:
            x.start()
        P.ensure()
        for slug in ("a", "b"):
            P.create_project(slug, [], "independent", [])
        P._PFT_CACHE.clear()

    def tearDown(self):
        for x in reversed(self.p):
            x.stop()


class ProjectOwnership(LineageBase):
    def _dispatch_in_a(self):
        # 真实走 bus_core.dispatch：同时验证「带 project 派发写 meta」与归属链起点
        r = B.dispatch("t", ["p1"], "independent", "q", [], ttl_min=5, min_answers=1, substitute=False, project="a")
        tid = r["task_id"]
        meta = B.read_json(os.path.join(self.outbox, tid + ".meta.json"), {})
        self.assertEqual(meta.get("project"), "a", "带 project 派发必须写进 outbox meta")
        P.upsert_decision("a", tid, status="dispatched", title="归属A", peers=["p1"], mode="independent")
        return tid

    def test_A项目派发B上下文post只落A(self):
        tid = self._dispatch_in_a()
        result = P._post_impl("b", task_id=tid, status="ready_for_merge", title="在B上下文回写")
        self.assertIn("_notice", result, "以 meta.project 为准记账时返回体必须带 _notice")
        self.assertIn("实际归属项目「a」", result)
        recs_a = [x for x in P.decisions("a") if x["task_id"] == tid]
        self.assertEqual(recs_a[0]["status"], "ready_for_merge", "决策写入真实归属 A")
        self.assertNotIn(tid, [x["task_id"] for x in P.decisions("b")], "B 项目不得出现别人的任务")

    def test_旧任务回落当前项目带notice(self):
        _make_meta(self.outbox, "T2")   # meta 无 project，各项目决策里也没有 → 真回落
        result = P._post_impl("b", task_id="T2", status="ready_for_merge", title="旧任务")
        self.assertIn("_notice", result, "回落记账必须显式提示，不许静默")
        self.assertIn("没有归属记录", result)
        self.assertIn("T2", [x["task_id"] for x in P.decisions("b")])

    def test_反查缓存第二次不读盘(self):
        _make_meta(self.outbox, "T3")   # 无 meta.project，逼出逐项目反查路径
        calls = {"n": 0}
        orig = P.decisions

        def counting(slug):
            calls["n"] += 1
            return orig(slug)

        with mock.patch.object(P, "decisions", counting):
            self.assertEqual(P.project_for_task("T3"), "")   # 第一次：逐项目反查（a、b 各读一次）
            self.assertEqual(P.project_for_task("T3"), "")   # 第二次：60 秒缓存命中，零读盘
        self.assertEqual(calls["n"], 2, "两个项目各扫一次后必须走缓存")

    def test_不带project派发meta形状不变(self):
        r = B.dispatch("t", ["p1"], "independent", "q", [], ttl_min=5, min_answers=1, substitute=False)
        meta = B.read_json(os.path.join(self.outbox, r["task_id"] + ".meta.json"), {})
        self.assertNotIn("project", meta, "老调用方不传 project，meta 不得多出该字段")
        legacy = {"id", "from", "peers", "offline_at_dispatch", "mode", "question", "angles",
                  "min_answers", "context_files", "created", "deadline", "deadline_ts", "substituted"}
        self.assertEqual(set(meta), legacy, "不带 project 时 meta 字段集合与旧行为逐字段一致")

    def test_负对照_短路project_for_task后路由失效(self):
        tid = self._dispatch_in_a()
        with mock.patch.object(P, "project_for_task", lambda tid: ""):
            P._post_impl("b", task_id=tid, status="ready_for_merge", title="短路后回写")
        # 归属解析失效时回写错落到 B（正是修复前的串台行为）——证明用例 1 的通过确实依赖 project_for_task
        rec_a = next(x for x in P.decisions("a") if x["task_id"] == tid)
        self.assertEqual(rec_a["status"], "dispatched", "短路后 A 的决策不再被更新")
        rec_b = next(x for x in P.decisions("b") if x["task_id"] == tid)
        self.assertEqual(rec_b["status"], "ready_for_merge", "回写串到了 B")


class DispatchEntryPoints(LineageBase):
    def test_dispatch_race透传slug(self):
        captured = {}

        def fake_dispatch(*args, **kwargs):
            captured["project"] = kwargs.get("project", "")
            return {"task_id": "R1", "peers": ["p1"], "offline": [], "min_answers": 1, "substituted": {}}

        with mock.patch.object(B, "dispatch", fake_dispatch):
            P.dispatch_race({"project": "a", "peers": ["p1"], "question": "q"})
        self.assertEqual(captured["project"], "a", "抢单入口必须把当前 slug 传进 dispatch")

    def test_dispatch_implement透传slug(self):
        captured = {}

        def fake_dispatch(*args, **kwargs):
            captured["project"] = kwargs.get("project", "")
            return {"task_id": "B1", "peers": ["builder1"], "offline": [], "min_answers": 1, "substituted": {}}

        rec = P.upsert_decision("a", "T9", status="adopted")
        with mock.patch.object(P, "_implement_precheck", return_value=(rec, None)), \
             mock.patch.object(P, "implementation_snapshot", return_value={"project_commands": {}, "builds": {}}), \
             mock.patch("bus_reassign.pick_free_target", return_value="builder1"), \
             mock.patch.object(P, "export_task_pack", return_value={"markdown": "pack"}), \
             mock.patch.object(B, "dispatch", fake_dispatch):
            P.dispatch_implement("a", "T9")
        self.assertEqual(captured["project"], "a", "直接派落地入口必须把当前 slug 传进 dispatch")


if __name__ == "__main__":
    unittest.main()
