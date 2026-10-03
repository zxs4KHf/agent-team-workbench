"""决策 20260929-181437-30f6 · brief 落地第 6 步：卡片「看不懂」反馈闸（后端一半）。

指令（kind=brief_rewrite，带 task_id 与原文）+ 决策标记（待重写）+ 幂等 + Leader 重写后标记自动清除；
浏览器按钮走的 /api/decision/brief-rewrite 真起 HTTP 服务端到端测。前端一半见 tests/smoke_brief_feedback_30f6.mjs。
全部在临时目录里跑，不碰真实 console/ peers/ inbox/ outbox/ projects/ settings.json。
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_core as B  # noqa: E402
import bus_projects as P  # noqa: E402
import bus_settings as SET  # noqa: E402

TITLE = "示例改表 · 澄清文件落单"
GOOD = "招标方发来澄清文件，设备清单要逐条改三十多处，已做工具自动改并自检。"
NEW = "招标方改了三十多处尺寸，已重新核对并改完，请你过目一遍。"
FALLBACK = "Agent 已把方案整理好了。打开看看：满意就采纳，还要改就继续讨论。"  # 兜底句只在前端有一份：这里只当「用户眼前那句话」传进来
PEERS3 = ["opus1", "glm1", "qwen1"]
FIXTURE = os.path.join(REPO, "tests", "brief_kinds_fixture.json")


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def isolate(obj, tmp):
    """把 bus_core / bus_projects / bus_settings 的落盘位置全指到临时目录，返回还原用的清单。"""
    saved = [(m, n, getattr(m, n)) for m, names in (
        (B, ("ROOT", "PEERS", "INBOX", "OUTBOX", "LOG", "WORKSPACE", "LOCK", "PRIORITY")),
        (P, ("PROJECTS", "COMMANDS", "DONE", "BUILDS", "ROLES", "NUDGES", "NOTIFIED", "STALE_MARK")),
        (SET, ("SETTINGS", "WORKSPACE_CFG"))) for n in names]
    B.ROOT = B.WORKSPACE = tmp
    B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(tmp, d) for d in ("peers", "inbox", "outbox", "log"))
    B.LOCK = os.path.join(tmp, "console", "controller.lock")
    B.PRIORITY = os.path.join(tmp, "console", "priority.json")
    P.PROJECTS = os.path.join(tmp, "projects")
    for name in ("COMMANDS", "DONE", "BUILDS"):
        setattr(P, name, os.path.join(tmp, "console", name.lower()))
    P.ROLES = os.path.join(tmp, "console", "roles.json")
    P.NUDGES = os.path.join(tmp, "console", "nudges.json")
    P.NOTIFIED = os.path.join(tmp, "console", "notified.json")
    P.STALE_MARK = os.path.join(tmp, "console", "stale_notified.json")
    SET.SETTINGS = os.path.join(tmp, "settings.json")
    SET.WORKSPACE_CFG = os.path.join(tmp, "workspace.json")
    os.makedirs(os.path.join(tmp, "console"), exist_ok=True)
    B.ensure_dirs()
    P.ensure()
    P._STATE_CACHE.update({"at": 0.0, "data": None})
    return saved


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brief30f6r-")
        self._saved = isolate(self, self.tmp)
        self.slug = P.create_project("示例", [], "independent", [], "")["slug"]

    def tearDown(self):
        for m, n, v in self._saved:
            setattr(m, n, v)
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        shutil.rmtree(self.tmp, ignore_errors=True)

    def card(self, tid, status="merged", title=TITLE, **extra):
        """造一条「新卡」：upsert_decision 新建的记录自带 brief 槽位；元数据（mode / peers）默认齐全。"""
        fields = dict(status=status, title=title, mode="discuss", peers=list(PEERS3))
        fields.update(extra)
        return P.upsert_decision(self.slug, tid, **fields)

    def rec(self, tid):
        return next(x for x in P.decisions(self.slug) if x["task_id"] == tid)

    def queued(self, kind="brief_rewrite"):
        return [c for c in P.pending_commands() if c.get("kind") == kind]


# ======================= 第 6 步：「看不懂」反馈闸 =======================
class TestBriefRewrite(Base):
    def test_click_queues_a_command_with_task_id_and_original_and_marks_the_card(self):
        self.card("t-r1", brief=GOOD)
        before = copy.deepcopy(self.rec("t-r1"))
        r = P.request_brief_rewrite(self.slug, "t-r1", shown="前端传的不该覆盖记录里的原文")
        self.assertTrue(r["ok"])
        self.assertFalse(r["dedup"])
        cmds = self.queued()
        self.assertEqual(len(cmds), 1)
        c = cmds[0]
        self.assertEqual((c["id"], c["kind"]), (r["command_id"], "brief_rewrite"))
        pl = c["payload"]
        self.assertEqual((pl["project"], pl["task_id"], pl["title"]), (self.slug, "t-r1", TITLE))
        self.assertEqual(pl["original"], GOOD, "原文取记录里的 brief，不信前端")
        self.assertEqual(pl["original_from"], "brief")
        self.assertIn("brief 模板", pl["template"], "Leader 要有模板才知道怎么重写")
        self.assertIn("看不懂", pl["hint"])
        self.assertIn("task_id", pl["hint"], "提示里写明怎么回写")
        rec = self.rec("t-r1")
        self.assertEqual(rec["brief_rewrite"]["command_id"], r["command_id"], "决策标记指向这条指令")
        self.assertEqual(rec["brief_rewrite"]["original"], GOOD)
        for k in ("brief", "summary", "status", "title", "history"):
            self.assertEqual(rec[k], before[k], f"只排队不改内容：{k}")

    def test_fallback_card_carries_what_the_user_actually_saw(self):
        self.card("t-r2")                                   # 没写 brief：用户眼前是兜底句
        with self.assertRaises(ValueError):
            P.request_brief_rewrite(self.slug, "t-r2")      # 不带 shown：不知道用户看到了什么，拒绝而不是排一条没有原文的指令
        self.assertEqual(self.queued(), [], "失败不入队")
        self.assertNotIn("brief_rewrite", self.rec("t-r2"), "失败不打标")
        P.request_brief_rewrite(self.slug, "t-r2", shown=FALLBACK)
        pl = self.queued()[0]["payload"]
        self.assertEqual((pl["original"], pl["original_from"]), (FALLBACK, "fallback"))
        self.assertEqual(self.rec("t-r2")["brief"], "", "兜底句不会被当成 brief 写进记录")

    def test_second_click_is_a_noop_until_the_leader_acks(self):
        self.card("t-r3", brief=GOOD)
        a = P.request_brief_rewrite(self.slug, "t-r3")
        b = P.request_brief_rewrite(self.slug, "t-r3")
        self.assertTrue(b["dedup"])
        self.assertEqual(b["command_id"], a["command_id"])
        self.assertEqual(len(self.queued()), 1, "排队中再点不重复入队")
        claimed = P.pending_commands(claim=True)           # Leader 领走了、还没回执：仍算处理中
        self.assertEqual([c["id"] for c in claimed], [a["command_id"]])
        c = P.request_brief_rewrite(self.slug, "t-r3")
        self.assertTrue(c["dedup"])
        self.assertEqual(self.queued(), [])
        self.assertEqual(len(P.claimed_commands()), 1)
        P.ack_command(a["command_id"], "看过了，没改", True)  # 回执了但没重写，用户还是看不懂 → 允许再来一条
        d = P.request_brief_rewrite(self.slug, "t-r3")
        self.assertFalse(d["dedup"])
        self.assertNotEqual(d["command_id"], a["command_id"])
        self.assertEqual(self.rec("t-r3")["brief_rewrite"]["command_id"], d["command_id"])

    def test_mark_clears_only_when_the_leader_writes_a_different_valid_brief(self):
        self.card("t-r4", brief=GOOD)
        P.request_brief_rewrite(self.slug, "t-r4")
        P.post(self.slug, "t-r4", "merged", TITLE, "只改流水账")
        self.assertIn("brief_rewrite", self.rec("t-r4"), "没碰 brief：标记还在")
        P.post(self.slug, "t-r4", brief=GOOD)
        self.assertIn("brief_rewrite", self.rec("t-r4"), "原样再写一遍不算重写")
        P.post(self.slug, "t-r4", brief="")
        self.assertIn("brief_rewrite", self.rec("t-r4"), "清空只是退回兜底句，不算重写")
        with self.assertRaises(ValueError):
            P.post(self.slug, "t-r4", brief="顺手优化了几处写法")
        self.assertIn("brief_rewrite", self.rec("t-r4"), "新 brief 被校验拒收：标记还在，记录不变")
        P.post(self.slug, "t-r4", brief=NEW)
        rec = self.rec("t-r4")
        self.assertNotIn("brief_rewrite", rec, "Leader 重写后「待重写」自动消失")
        self.assertEqual(rec["brief"], NEW)

    def test_first_brief_after_a_fallback_complaint_also_clears_the_mark(self):
        self.card("t-r5")
        P.request_brief_rewrite(self.slug, "t-r5", shown=FALLBACK)
        P.post(self.slug, "t-r5", brief=GOOD)
        self.assertNotIn("brief_rewrite", self.rec("t-r5"))

    def test_bad_requests_are_rejected_without_side_effects(self):
        with self.assertRaises(ValueError):
            P.request_brief_rewrite(self.slug, "no-such-task", shown="x")
        with self.assertRaises(ValueError):
            P.request_brief_rewrite(self.slug, "", shown="x")
        self.assertEqual(self.queued(), [])

    def test_legacy_card_can_be_flagged_too_without_getting_a_brief_slot(self):
        self.card("t-r6")
        items = P.decisions(self.slug)
        for it in items:
            it.pop("brief", None)
        P.save_decisions(self.slug, items)
        P.request_brief_rewrite(self.slug, "t-r6", shown=FALLBACK)
        rec = self.rec("t-r6")
        self.assertIn("brief_rewrite", rec)
        self.assertNotIn("brief", rec, "反馈不顺带倒灌 brief")


class TestHttpRoute(unittest.TestCase):
    """真起一个 HTTP 服务：浏览器按钮走的就是这条路（/api/decision/brief-rewrite）。"""

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        import bus_web as W
        cls.tmp = tempfile.mkdtemp(prefix="brief30f6h-")
        cls._saved = isolate(cls, cls.tmp)
        B.heartbeat("controller", "idle", "controller")
        cls.slug = P.create_project("示例", [], "independent", [], "")["slug"]
        P.upsert_decision(cls.slug, "t-http", status="merged", title=TITLE, mode="discuss", peers=list(PEERS3))
        P.upsert_decision(cls.slug, "t-http-w", status="merged", title=TITLE, mode="discuss", peers=list(PEERS3), brief=GOOD)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), W.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        for m, n, v in cls._saved:
            setattr(m, n, v)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def state_decision(self, tid):
        _, st = self.call("GET", "/api/state")
        proj = next(p for p in st["projects"] if p["slug"] == self.slug)
        return st, next(d for d in proj["decisions"] if d["task_id"] == tid)

    def test_button_route_queues_marks_dedups_and_rejects_bad_input(self):
        code, r = self.call("POST", "/api/decision/brief-rewrite", {"project": self.slug, "task_id": "t-http", "shown": FALLBACK})
        self.assertEqual(code, 200, r)
        self.assertFalse(r["dedup"])
        st, d = self.state_decision("t-http")
        self.assertEqual(d["brief_rewrite"]["command_id"], r["command_id"], "state 里决策带「待重写」标记")
        self.assertEqual(d["brief"], "")
        cmds = [c for c in st["commands"] if c["kind"] == "brief_rewrite"]
        self.assertEqual([c["id"] for c in cmds], [r["command_id"]], "指令出现在 Leader 的队列里")
        self.assertEqual(cmds[0]["payload"]["task_id"], "t-http")
        self.assertEqual(cmds[0]["payload"]["original"], FALLBACK)
        self.assertEqual(st["brief_alert_ratio"], 0.5, "提醒线随 state 下发")
        code, r2 = self.call("POST", "/api/decision/brief-rewrite", {"project": self.slug, "task_id": "t-http", "shown": FALLBACK})
        self.assertEqual(code, 200)
        self.assertTrue(r2["dedup"])
        self.assertEqual(r2["command_id"], r["command_id"])
        code, err = self.call("POST", "/api/decision/brief-rewrite", {"project": self.slug, "task_id": "nope", "shown": "x"})
        self.assertEqual(code, 400)
        self.assertIn("error", err)
        code, err = self.call("POST", "/api/decision/brief-rewrite", {"project": self.slug, "task_id": "t-http-w"})
        self.assertEqual(code, 200, "写过 brief 的卡不需要前端带原文")
        code, err = self.call("POST", "/api/decision/brief-rewrite", {"task_id": "t-http"})
        self.assertEqual(code, 400, "缺 project")


class TestAdoptClearsMark(Base):
    def test_adopting_clears_a_pending_rewrite_mark(self):
        self.card("t-mark")
        P.request_brief_rewrite(self.slug, "t-mark", shown=FALLBACK)
        P.post(self.slug, "t-mark", "merged", TITLE, brief_draft=True)
        self.assertNotIn("brief_rewrite", self.rec("t-mark"))


class TestToolDescription(unittest.TestCase):
    def test_mcp_description_tells_the_leader_about_the_rewrite_command(self):
        self.assertIn("kind=brief_rewrite", read_text(os.path.join(REPO, "bus_mcp.py")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
