"""派单 20261001-142456-ed28 · peers=["auto"] 由总线自动挑「在线 + 空闲」的 Agent。

全部在临时目录里跑：不读真实 peers/ inbox/ outbox/ console/，也不建 git worktree。
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bus_core as B  # noqa: E402


def iso(sec_ago=0):
    """带本机偏移的心跳串（ADR 0010 之后的写法）；sec_ago 为负 = 落在未来。"""
    return (dt.datetime.now().astimezone() - dt.timedelta(seconds=sec_ago)).isoformat(timespec="seconds")


class BusTempCase(unittest.TestCase):
    def setUp(self):
        self._saved = [(B, n, getattr(B, n)) for n in ("ROOT", "PEERS", "INBOX", "OUTBOX", "LOG", "WORKSPACE")]
        self.tmp = tempfile.mkdtemp(prefix="dispatch-auto-")
        B.ROOT = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.WORKSPACE = self.tmp
        B.ensure_dirs()

    def tearDown(self):
        for mod, name, value in self._saved:
            setattr(mod, name, value)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def register(self, peer, age=5, activity="idle", role=None, group=None, last_seen=None):
        rec = {"peer": peer, "last_seen": last_seen if last_seen is not None else iso(age),
               "activity": activity, "status": activity, "model": "test-model"}
        if role:
            rec["role"] = role
        if group:
            rec["group"] = group          # 显式组：peer_group() 短路，测试不会去碰组表/Arena
        B.write_json(os.path.join(B.PEERS, f"{peer}.json"), rec)
        return rec

    def put_inbox(self, peer, suffix):
        d = os.path.join(B.INBOX, peer)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "old" + suffix), "w", encoding="utf-8"):
            pass

    def task_files(self, peer):
        d = os.path.join(B.INBOX, peer)
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def reasons(self, res):
        return {s["peer"]: s["reason"] for s in res["reasons"]}

    def dispatch(self, peers, mode="independent", from_peer="leader1", **kw):
        return B.dispatch(from_peer, peers, mode, "题目", [], 30, None, 0, None, **kw)


class TestAutoPick(BusTempCase):
    def test_picks_freshest_two_by_default(self):
        self.register("slowpoke", age=60)
        self.register("fresh1", age=10)
        self.register("middle", age=30)
        res = B.auto_pick(exclude=["leader1"])
        self.assertEqual(res["picked"], ["fresh1", "middle"])
        self.assertEqual(res["idle_candidates"], ["fresh1", "middle", "slowpoke"])
        self.assertEqual(res["max_peers"], B.AUTO_MAX_PEERS)
        self.assertTrue(self.reasons(res)["slowpoke"].startswith("已够"))

    def test_excludes_controller_and_caller(self):
        self.register("controller", age=1)
        self.register("leader1", age=1)
        self.register("worker1", age=1)
        res = B.auto_pick(exclude=["leader1"])
        self.assertEqual(res["picked"], ["worker1"])
        for peer in ("controller", "leader1"):
            self.assertIn("调用方排除", self.reasons(res)[peer])

    def test_excludes_busy_agents(self):
        for name, activity in (("thinker", "think"), ("builder", "build"), ("talker", "discuss")):
            self.register(name, activity=activity)
        res = B.auto_pick()
        self.assertEqual(res["picked"], [])
        self.assertEqual(self.reasons(res)["thinker"], "忙（activity=think）")
        self.assertEqual(self.reasons(res)["builder"], "忙（activity=build）")
        self.assertEqual(self.reasons(res)["talker"], "忙（activity=discuss）")

    def test_excludes_offline(self):
        self.register("gone", age=B.HEARTBEAT_STALE_SEC + 7200)
        self.assertIn("离线", self.reasons(B.auto_pick())["gone"])

    def test_excludes_clock_skew(self):
        # 心跳落在未来且超出容忍（CLOCK_SKEW_SEC）= 时间戳不可信，按失联处理
        self.register("skewed", last_seen=iso(-(B.CLOCK_SKEW_SEC + 120)))
        self.assertIn("时钟错乱", self.reasons(B.auto_pick())["skewed"])

    def test_excludes_unparsable_heartbeat(self):
        self.register("broken", last_seen="昨天下午")
        self.assertIn("解析失败", self.reasons(B.auto_pick())["broken"])

    def test_excludes_relay_role(self):
        self.register("proxy", role="relay")
        self.assertIn("正在代理 Leader", self.reasons(B.auto_pick())["proxy"])

    def test_excludes_pending_inbox(self):
        self.register("queued1")
        self.register("claimed1")
        self.put_inbox("queued1", ".json")
        self.put_inbox("claimed1", ".taken")
        res = B.auto_pick()
        self.assertEqual(res["picked"], [])
        self.assertIn("待领 1", self.reasons(res)["queued1"])
        self.assertIn("已领 1", self.reasons(res)["claimed1"])

    def test_expired_copy_does_not_block(self):
        # .json.expired 是已释放的孤儿，不该让这个 Agent 永远选不上
        self.register("recovered")
        self.put_inbox("recovered", ".json.expired")
        self.assertEqual(B.auto_pick()["picked"], ["recovered"])

    def test_max_peers_floor_default_and_cap(self):
        for i in range(3):
            self.register(f"idle{i}", age=i)
        self.assertEqual(B.auto_pick(max_peers=0)["max_peers"], B.AUTO_MAX_PEERS)
        self.assertEqual(len(B.auto_pick(max_peers=1)["picked"]), 1)
        self.assertEqual(len(B.auto_pick(max_peers=-5)["picked"]), 1)
        self.assertEqual(len(B.auto_pick(max_peers=9)["picked"]), 3)

    def test_same_registry_same_answer(self):
        for i in range(4):
            self.register(f"dup{i}", age=i * 7)
        first, second = B.auto_pick(), B.auto_pick()
        self.assertEqual(first["picked"], second["picked"])
        self.assertEqual(first["reasons"], second["reasons"])


class TestDispatchWithAuto(BusTempCase):
    def test_only_picked_peers_get_the_task(self):
        self.register("fresh1", age=10)
        self.register("middle", age=30)
        self.register("busyguy", activity="build")
        r = self.dispatch(["auto"])
        self.assertEqual(r["peers"], ["fresh1", "middle"])
        self.assertEqual(len(self.task_files("fresh1")), 1)
        self.assertEqual(len(self.task_files("middle")), 1)
        self.assertEqual(self.task_files("busyguy"), [])
        self.assertEqual(r["auto"]["picked"], ["fresh1", "middle"])
        self.assertEqual(r["min_answers"], 2)
        meta = B.read_json(os.path.join(B.OUTBOX, f"{r['task_id']}.meta.json"), None)
        self.assertEqual(meta["auto"]["picked"], ["fresh1", "middle"])
        self.assertIn("busyguy", self.reasons(meta["auto"]))

    def test_named_peers_survive_auto(self):
        self.register("named1", age=1)
        self.register("other1", age=2)
        r = self.dispatch(["named1", "auto"], max_peers=1)
        self.assertEqual(r["peers"], ["named1", "other1"])
        self.assertEqual(len(self.task_files("named1")), 1)
        self.assertEqual(r["auto"]["picked"], ["other1"])
        self.assertIn("调用方排除", self.reasons(r["auto"])["named1"])

    def test_auto_token_is_case_insensitive(self):
        self.register("only1", age=5)
        self.assertEqual(self.dispatch(["AuTo"])["peers"], ["only1"])
        # only1 刚接到任务，已经不算空闲了；空白也要容忍
        self.register("only2", age=1)
        self.assertEqual(self.dispatch(["  AUTO  "])["peers"], ["only2"])

    def test_fail_fast_creates_no_task(self):
        self.register("busyguy", activity="think")
        self.register("gone", age=B.HEARTBEAT_STALE_SEC + 600)
        with self.assertRaises(ValueError) as ctx:
            self.dispatch(["auto"])
        text = str(ctx.exception)
        self.assertIn("没有在线空闲的 Agent", text)
        self.assertIn("任务未创建", text)
        self.assertIn("busyguy", text)          # 排除理由原样带出去，Leader 才能复核
        self.assertEqual(os.listdir(B.OUTBOX), [])
        self.assertEqual(os.listdir(B.INBOX), [])

    def test_fail_fast_before_any_worktree(self):
        # build 模式最容易留下半成品：auto 挑不到人必须在建 worktree 之前就停
        with self.assertRaises(ValueError):
            self.dispatch(["auto"], mode="build")
        self.assertFalse(os.path.isdir(os.path.join(B.WORKSPACE, ".wt")))
        self.assertEqual(os.listdir(B.INBOX), [])


class TestExplicitPeersUnchanged(BusTempCase):
    def test_return_shape_has_no_auto_field(self):
        self.register("one1", age=5)
        self.register("two2", age=5)
        r = self.dispatch(["one1", "two2"])
        self.assertEqual(set(r), {"task_id", "peers", "offline", "deadline", "min_answers", "substituted"})
        self.assertEqual(r["peers"], ["one1", "two2"])
        meta = B.read_json(os.path.join(B.OUTBOX, f"{r['task_id']}.meta.json"), None)
        self.assertNotIn("auto", meta)
        self.assertEqual(set(meta), {"id", "from", "peers", "offline_at_dispatch", "mode", "question", "angles",
                                     "min_answers", "context_files", "created", "deadline", "deadline_ts", "substituted"})

    def test_explicit_offline_peer_still_gets_task_and_is_reported(self):
        # 旧行为：显式点名的人离线也照发，只在返回里标 OFFLINE。auto 不得改变这一点。
        self.register("off1", age=B.HEARTBEAT_STALE_SEC + 600, group="solo-group")
        self.register("on1", age=1, group="other-group")
        r = self.dispatch(["off1"])
        self.assertEqual(r["offline"], ["off1"])
        self.assertEqual(len(self.task_files("off1")), 1)
        self.assertNotIn("auto", r)


class TestMCPSurface(BusTempCase):
    def test_bus_dispatch_exposes_max_peers_and_auto(self):
        import inspect
        import bus_mcp
        sig = inspect.signature(bus_mcp.bus_dispatch)
        self.assertEqual(sig.parameters["max_peers"].default, 0)
        src = inspect.getsource(bus_mcp.bus_dispatch)
        self.assertIn("auto", src)
        # 参数必须真的转发给 bus_tool，否则 max_peers 只是文档
        self.assertIn('"max_peers"', src)


if __name__ == "__main__":
    unittest.main()
