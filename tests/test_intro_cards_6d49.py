# -*- coding: utf-8 -*-
"""卡片简介 intro：校验口径、manual/auto 优先级、软失败、派发透传、清空语义。

本单（20261002-074332-8954）在 current main 上重施 9e9ef99 的增量，并按验收要求修正三处：
① 英文禁用词只按词边界匹配，正常中文术语与日期不再误伤；
② 结构不做不可判别的硬门禁——自然正例必须过，堆字凑数的乱文必须拒；
③ auto 草稿校验失败时只丢 intro 并留警告，status/title 照常落盘；manual 当场拒绝。

全部跑在 tempfile 目录里，不碰 production 的 projects/。
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bus_core as B        # noqa: E402
import bus_projects as P    # noqa: E402

GOOD = "招标方发来澄清文件，设备清单表要按它逐条改三十多处，做了个工具自动改并自己检查有没有改错。"
GOOD2 = "新增了把零散记录按事情归成任务链的模块，附带测试；页面上还看不到，下一单接前端就能用。"


class IntroRules(unittest.TestCase):
    def test_自然正例通过(self):
        for s in (GOOD, GOOD2):
            self.assertEqual(P.validate_intro(s), s, "规范自己的示范简介必须通过：" + s[:20])

    def test_堆字凑数的乱文不得获准(self):
        filler = "要" * 10 + "改" * 10 + "能" * 10 + "啊" * 10
        with self.assertRaises(ValueError) as cm:
            P.validate_intro(filler)
        self.assertIn("堆字", str(cm.exception))

    def test_英文禁用词只按词边界(self):
        with self.assertRaises(ValueError):
            P.validate_intro("这次检查发现接口会 return 错误码，已经改好，你能直接看到真正的原因了哦")
        # R20 含 r2 子串、compass 含 pass 子串，都不该被当成黑话
        ok = "第 R20 包的模板机要重新核价，已经把供应商报价整理成对照表，你可以直接拿去填报价单不用再翻原件"
        self.assertEqual(P.validate_intro(ok), ok)

    def test_正常中文术语与日期不过禁(self):
        ok = "你担心记录太乱找不到头绪，所以做了一个只读的汇总程序，你能一眼看出每件事现在进行到第几步"
        self.assertEqual(P.validate_intro(ok), ok)
        ok2 = "20260929 发来的那份澄清文件要逐条核对，已经整理成对照表，你可以直接照着改不会漏掉任何一处"
        self.assertEqual(P.validate_intro(ok2), ok2)

    def test_真黑话仍然被拒(self):
        with self.assertRaises(ValueError):
            P.validate_intro("这条合并稿已经核对完成，接下来要把方案写进文档里，你可以直接打开看结论了")

    def test_长度边界(self):
        with self.assertRaises(ValueError):
            P.validate_intro("太短了不行")
        with self.assertRaises(ValueError):
            P.validate_intro("长" + GOOD * 3)

    def test_空串直接放行表示清空(self):
        self.assertEqual(P.validate_intro(""), "")
        self.assertEqual(P.validate_intro("   "), "")


class DecisionIntro(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="intro_")
        self.projects = os.path.join(self.tmp, "projects")
        os.makedirs(self.projects)
        self.p = [mock.patch.object(P, "PROJECTS", self.projects),
                  mock.patch.object(P, "COMMANDS", os.path.join(self.tmp, "commands")),
                  mock.patch.object(P, "DONE", os.path.join(self.tmp, "done"))]
        for x in self.p:
            x.start()
        P.ensure()
        P.create_project("t1", [], "independent", [])

    def tearDown(self):
        for x in reversed(self.p):
            x.stop()

    def _rec(self, **kw):
        return P.upsert_decision("t1", "T1", **kw)

    def test_source_必须与_intro_一起写(self):
        with self.assertRaises(ValueError):
            self._rec(intro_source="manual")
        with self.assertRaises(ValueError):
            self._rec(intro=GOOD, intro_source="bogus")

    def test_manual_first_write_wins(self):
        self._rec(intro=GOOD, intro_source="manual")
        rec = self._rec(intro=GOOD2, intro_source="auto")
        self.assertEqual(rec["intro"], GOOD, "人工写过之后自动草稿不得覆盖")
        self.assertEqual(rec["intro_source"], "manual")
        self.assertIn("未采用", rec.get("intro_warning", ""))
        # 人工自己改还是可以的
        rec = self._rec(intro=GOOD2, intro_source="manual")
        self.assertEqual(rec["intro"], GOOD2)

    def test_auto_校验失败只丢intro并留警告(self):
        rec = self._rec(intro=GOOD, intro_source="auto")
        self.assertEqual(rec["intro"], GOOD)
        rec = self._rec(intro="太短", intro_source="auto", title="标题照落", status="dispatched")
        self.assertEqual(rec["intro"], GOOD, "auto 失败不得破坏已有 intro")
        self.assertEqual(rec["title"], "标题照落", "status/title 必须照常落盘")
        self.assertEqual(rec["status"], "dispatched")
        self.assertIn("自动简介未采用", rec["intro_warning"])

    def test_manual_校验失败当场拒绝(self):
        with self.assertRaises(ValueError):
            self._rec(intro="太短", intro_source="manual")

    def test_清空语义(self):
        self._rec(intro=GOOD, intro_source="manual")
        rec = self._rec(intro="", intro_source="manual")
        self.assertEqual(rec["intro"], "")
        self.assertEqual(rec["intro_source"], "", "清空后 source 也要清掉，否则挡住后续自动草稿")
        rec = self._rec(intro=GOOD2, intro_source="auto")
        self.assertEqual(rec["intro"], GOOD2, "清空之后自动草稿应当可以再写进来")

    def test_post_软失败不打回整次回写(self):
        P._post_impl("t1", task_id="T2", status="merged", title="合并完成",
                     intro="太短", intro_source="auto")
        rec = next(x for x in P.decisions("t1") if x["task_id"] == "T2")
        self.assertEqual(rec["status"], "merged")
        self.assertEqual(rec["title"], "合并完成")
        self.assertEqual(rec["intro"], "")
        self.assertIn("自动简介未采用", rec["intro_warning"])

    def test_post_intro_source_不能单独传(self):
        with self.assertRaises(ValueError):
            P._post_impl("t1", task_id="T3", intro_source="manual")


class DispatchCarriesIntro(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="disp_")
        self.inbox = os.path.join(self.tmp, "inbox"); os.makedirs(self.inbox)
        self.outbox = os.path.join(self.tmp, "outbox"); os.makedirs(self.outbox)
        self.ctx = [mock.patch.object(B, "INBOX", self.inbox),
                    mock.patch.object(B, "OUTBOX", self.outbox),
                    mock.patch.object(B, "ensure_dirs", lambda: None),
                    mock.patch.object(B, "list_peers", lambda: [{"peer": "p1", "online": True}]),
                    mock.patch.object(B, "peer_group", lambda p: "g"),
                    mock.patch.object(B, "peer_state", lambda p: {"online": True}),
                    mock.patch.object(B, "role_line_for", lambda p: "")]
        for x in self.ctx:
            x.start()

    def tearDown(self):
        for x in reversed(self.ctx):
            x.stop()

    def _dispatch(self, **kw):
        return B.dispatch("controller", ["p1"], "independent", "q", [], 5, None, 1, "", None, **kw)

    def test_parent_与_intro_同时进任务包和meta(self):
        r = self._dispatch(parent_task="20260929-181437-30f6", intro=GOOD)
        task = B.read_json(os.path.join(self.inbox, "p1", r["task_id"] + ".json"), {})
        meta = B.read_json(os.path.join(self.outbox, r["task_id"] + ".meta.json"), {})
        self.assertEqual(task["parent_task"], "20260929-181437-30f6")
        self.assertEqual(task["intro"], GOOD)
        self.assertEqual(meta["parent_task"], "20260929-181437-30f6")
        self.assertEqual(meta["intro"], GOOD)

    def test_parent_task_兼容式校验_不合形状当没传且不报错(self):
        r = self._dispatch(parent_task="随便一个不是任务号的串", intro="")
        task = B.read_json(os.path.join(self.inbox, "p1", r["task_id"] + ".json"), {})
        self.assertNotIn("parent_task", task, "形状不对就当没传，不写进任务包")
        self.assertNotIn("intro", task)

    def test_不传时完全不出现这两个键(self):
        r = self._dispatch()
        task = B.read_json(os.path.join(self.inbox, "p1", r["task_id"] + ".json"), {})
        self.assertNotIn("parent_task", task)
        self.assertNotIn("intro", task)


if __name__ == "__main__":
    unittest.main(verbosity=2)
