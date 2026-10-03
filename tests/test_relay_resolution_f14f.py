# 任务 20260923-220727-f14f 自测：priority schema v2、组化解析（recent）、skip_stale_queue、兜底两分支、旧签名 relay_candidate、/api/priority 的 rev 冲突与出厂预置。
# 跑法：py -3 tests/test_relay_resolution_f14f.py（仓库根目录）。全部状态建在临时目录，不读写生产 console/priority.json、peers/、inbox/。
import datetime as dt
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

GROUPS = [{"id": "claude-fable-5", "match": ["claude-fable-5"], "order": 1}, {"id": "glm-5.3", "match": ["glm-5.3"], "order": 2}]


def iso(sec_ago=0):
    return (dt.datetime.now() - dt.timedelta(seconds=sec_ago)).strftime("%Y-%m-%dT%H:%M:%S")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="busf14f-")
        B.ROOT = self.tmp
        B.PEERS = os.path.join(self.tmp, "peers")
        B.INBOX = os.path.join(self.tmp, "inbox")
        B.OUTBOX = os.path.join(self.tmp, "outbox")
        B.LOG = os.path.join(self.tmp, "log")
        B.LOCK = os.path.join(self.tmp, "console", "controller.lock")
        B.PRIORITY = os.path.join(self.tmp, "console", "priority.json")
        P.PROJECTS = os.path.join(self.tmp, "projects")
        P.COMMANDS = os.path.join(self.tmp, "console", "commands")
        P.DONE = os.path.join(self.tmp, "console", "done")
        for d in (B.PEERS, B.INBOX, B.OUTBOX, B.LOG, os.path.dirname(B.LOCK), P.PROJECTS, P.COMMANDS, P.DONE):
            os.makedirs(d, exist_ok=True)
        self._mg = B.model_groups
        B.model_groups = lambda: GROUPS  # 不碰 Arena 控制面
        P._STATE_CACHE.update({"at": 0.0, "data": None})

    def tearDown(self):
        B.model_groups = self._mg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def peer(self, name, model, ago=0, status="idle", queued=0):
        B.write_json(os.path.join(B.PEERS, f"{name}.json"), {"peer": name, "model": model, "status": status, "last_seen": iso(ago)})
        for i in range(queued):
            os.makedirs(os.path.join(B.INBOX, name), exist_ok=True)
            B.write_json(os.path.join(B.INBOX, name, f"t{i}.json"), {"task_id": f"t{i}"})

    def prio(self, **kw):
        B.write_json(B.PRIORITY, kw)


class TestSchema(Base):
    def test_priority_fills_missing_keys_without_overwrite(self):
        self.prio(relay_order=["a"], agent_weight={"x": 1}, rev=3, relay_policy={"strategy": "rotate"})
        p = B.priority()
        self.assertEqual((p["version"], p["relay_order"], p["relay_order_meta"], p["rev"]), (2, ["a"], None, 3))
        self.assertEqual(p["relay_policy"]["strategy"], "rotate")
        self.assertEqual((p["relay_policy"]["skip_stale_queue"], p["relay_policy"]["rotate"], p["relay_policy"]["allow_busy"], p["relay_policy"]["relay_chain"]), (True, False, False, []))
        self.assertEqual(p["agent_weight"], {"x": 1})
        self.assertEqual(B.read_json(B.PRIORITY), {"relay_order": ["a"], "agent_weight": {"x": 1}, "rev": 3, "relay_policy": {"strategy": "rotate"}}, "priority() 只读，不回写文件")

    def test_priority_missing_file(self):
        p = B.priority()
        self.assertEqual((p["version"], p["relay_order"], p["relay_order_meta"], p["rev"], p["relay_policy"]["strategy"]), (2, [], None, 0, "recent"))

    def test_relay_meta_for_keeps_old_entries(self):
        old = [{"id": "glm-5.3", "kind": "group", "source": "ui", "added_at": "2026-01-01T00:00:00"}]
        m = B.relay_meta_for(["glm-5.3", "claude-fable-5", "p9"], old, "ui")
        self.assertEqual([x["kind"] for x in m], ["group", "group", "peer"])
        self.assertEqual(m[0]["added_at"], "2026-01-01T00:00:00")
        self.assertEqual([x["kind"] for x in B.relay_meta_for(["zzz"], None, "factory", "group")], ["group"])


class TestResolution(Base):
    def test_group_recent_pick_and_items(self):
        self.peer("f1", "claude-fable-5", ago=10)
        self.peer("f2", "claude-fable-5", ago=5)
        self.peer("g1", "glm-5.3", ago=1)
        self.prio(relay_order=["claude-fable-5", "glm-5.3"])
        r = B.relay_resolution()
        self.assertEqual((r["pick"], r["via_group"], r["fallback"], r["refused"]), ("f2", "claude-fable-5", False, False))
        self.assertEqual(r["items"][0], {"item": "claude-fable-5", "kind": "group", "pick": "f2", "online_n": 2, "total_n": 2, "skipped_n": 0})
        self.assertEqual(r["items"][1]["pick"], "g1")
        self.assertEqual(B.relay_order_peers(), ["f2", "g1"])
        self.assertEqual(B.relay_pick(), ("f2", "claude-fable-5"))

    def test_tie_breaks_by_peer_name_total_order(self):
        self.peer("fb", "claude-fable-5", ago=3)
        self.peer("fa", "claude-fable-5", ago=3)
        self.prio(relay_order=["claude-fable-5"])
        self.assertEqual(B.relay_pick(), ("fa", "claude-fable-5"))

    def test_peer_item_and_exclude(self):
        self.peer("f1", "claude-fable-5", ago=10)
        self.peer("f2", "claude-fable-5", ago=5)
        self.prio(relay_order=["f1", "claude-fable-5"])
        r = B.relay_resolution()
        self.assertEqual((r["pick"], r["via_group"], r["items"][0]["kind"]), ("f1", "", "peer"))
        self.assertEqual(B.relay_pick(exclude=["f1"]), ("f2", "claude-fable-5"), "刚掉线者排除后顺延到下一项")
        self.assertEqual(B.relay_resolution(exclude=["f1", "f2"])["fallback"], False)
        self.assertEqual(B.relay_resolution(exclude=["f1", "f2"])["pick"], "")

    def test_skip_stale_queue_default(self):
        # 组内：f1 在线；f3 离线但 inbox 有积压 → 不入候选，逐项结果标 skipped_n
        self.peer("f1", "claude-fable-5", ago=20)
        self.peer("f3", "claude-fable-5", ago=400, queued=2)
        self.prio(relay_order=["claude-fable-5"])
        r = B.relay_resolution()
        self.assertEqual((r["pick"], r["skip_stale_queue"]), ("f1", True))
        self.assertEqual((r["items"][0]["online_n"], r["items"][0]["total_n"], r["items"][0]["skipped_n"]), (1, 2, 1))
        # 只剩离线有积压者：不解析、不兜底成它
        self.prio(relay_order=["f3"])
        r = B.relay_resolution()
        self.assertEqual(r["items"][0]["pick"], "")
        self.assertNotIn("f3", r["fallback_peers"])

    def test_fallback_when_order_nonempty_but_unresolvable(self):
        # 分支 A：relay_order 非空但逐项都拿不到人 → 保留无条件兜底，fallback=true
        self.peer("z1", "glm-5.3", ago=2)
        self.peer("a1", "glm-5.3", ago=1)
        self.prio(relay_order=["opus1", "no-such-group"])
        r = B.relay_resolution()
        self.assertEqual((r["pick"], r["fallback"], r["fallback_peers"], r["refused"]), ("a1", True, ["a1", "z1"], False))
        self.assertEqual([it["kind"] for it in r["items"]], ["unknown", "unknown"])
        self.assertEqual(B.relay_order_peers(), ["a1", "z1"])

    def test_no_fallback_when_order_empty(self):
        # 分支 B：relay_order 为空数组 → 视为明确拒绝替补，不兜底
        self.peer("z1", "glm-5.3", ago=2)
        self.prio(relay_order=[])
        r = B.relay_resolution()
        self.assertEqual((r["pick"], r["fallback"], r["refused"], r["items"]), ("", False, True, []))
        self.assertEqual(B.relay_order_peers(), [])
        B.write_json(os.path.join(B.PEERS, "controller.json"), {"peer": "controller", "status": "idle", "last_seen": iso(600)})
        self.assertFalse(B.relay_candidate("z1"))

    def test_policy_non_recent_treated_as_recent_and_flagged(self):
        self.peer("f1", "claude-fable-5", ago=10)
        self.peer("f2", "claude-fable-5", ago=5)
        self.prio(relay_order=["claude-fable-5"], relay_policy={"strategy": "rotate", "rotate": True, "allow_busy": True, "relay_chain": ["x"]})
        r = B.relay_resolution()
        self.assertEqual((r["pick"], r["policy"], r["policy_requested"]), ("f2", "recent", "rotate"))
        self.assertIn("未实现", r["policy_note"])
        self.prio(relay_order=["claude-fable-5"])
        self.assertEqual(B.relay_resolution()["policy_note"], "")

    def test_relay_candidate_legacy_signature(self):
        self.peer("f1", "claude-fable-5", ago=10)
        self.peer("f2", "claude-fable-5", ago=5)
        self.prio(relay_order=["claude-fable-5"])
        B.write_json(os.path.join(B.PEERS, "controller.json"), {"peer": "controller", "status": "idle", "last_seen": iso(10)})
        self.assertFalse(B.relay_candidate("f2"), "Leader心跳新鲜时不顶替")
        B.write_json(os.path.join(B.PEERS, "controller.json"), {"peer": "controller", "status": "idle", "last_seen": iso(600)})
        self.assertTrue(B.relay_candidate("f2"))
        self.assertFalse(B.relay_candidate("f1"))
        self.assertIsInstance(B.relay_candidate("f2"), bool)
        # 兜底分支下旧判定同样成立
        self.prio(relay_order=["nobody"])
        self.assertTrue(B.relay_candidate("f1"), "兜底按名字：f1 < f2")

    def test_full_state_carries_resolution(self):
        self.peer("f1", "claude-fable-5", ago=10)
        self.prio(relay_order=["claude-fable-5"])
        st = P._full_state()
        self.assertEqual(st["relay_order_effective"], ["f1"])
        self.assertEqual(st["relay_resolution"]["items"][0]["pick"], "f1")
        self.assertIsNone(st["priority"]["relay_order_meta"])


class TestWeb(Base):
    def setUp(self):
        super().setUp()
        import bus_web as W
        from http.server import ThreadingHTTPServer
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), W.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def post(self, body):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/priority", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_rev_conflict_and_meta(self):
        self.prio(relay_order=["opus1", "glm1"], rev=4)
        code, r = self.post({"relay_order": ["glm-5.3"], "rev": 3})
        self.assertEqual((code, r["conflict"]), (409, True))
        self.assertEqual(B.priority()["relay_order"], ["opus1", "glm1"], "冲突时不写")
        self.assertEqual(B.priority()["rev"], 4)
        code, r = self.post({"relay_order": ["glm-5.3", "opus1"], "rev": 4})
        self.assertEqual((code, r["rev"], r["relay_order"]), (200, 5, ["glm-5.3", "opus1"]))
        self.assertEqual([m["kind"] for m in r["relay_order_meta"]], ["group", "peer"])
        self.assertEqual(B.priority()["relay_order_meta"][0]["source"], "ui")
        code, r = self.post({"relay_order": "glm-5.3", "rev": 5})
        self.assertEqual(code, 400)
        code, r = self.post({"task_order": ["t1"]})  # 不带 rev 的旧客户端仍可写，rev 照样递增
        self.assertEqual((code, r["rev"], r["task_order"]), (200, 6, ["t1"]))

    def test_factory_reset_writes_meta(self):
        self.prio(relay_order=["opus1", "glm1", "astra1"])
        self.assertIsNone(B.priority()["relay_order_meta"], "手填 peer 名且无 meta → 判定为从未在界面编辑过")
        code, r = self.post({"factory_reset": True, "rev": 0})
        self.assertEqual(code, 200)
        self.assertEqual(r["relay_order"], list(B.RELAY_FACTORY_ORDER))
        self.assertTrue(all(m["kind"] == "group" and m["source"] == "factory" for m in r["relay_order_meta"]))
        self.assertEqual(r["rev"], 1)
        code, r2 = self.post({"factory_reset": True, "rev": 0})
        self.assertEqual(code, 409, "第二次用旧 rev 再点会被拒，不会静默重写")


if __name__ == "__main__":
    unittest.main(verbosity=2)
