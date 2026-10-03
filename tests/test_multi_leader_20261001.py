# 多 Leader 并行接入（多活）与热更新回归（2026-10-01）：
# 会话化心跳互不覆盖、双 Leader 领取互斥分单、租约并发完整性与损坏隔离、守护强释放复查、
# notified/nudges/settings 并发不丢更新、MCP 令牌身份绑定（peer token 不得冒充 controller）、热重载端点。
# 跑法：py -3 -X utf8 tests/test_multi_leader_20261001.py（仓库根目录）
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_core as B          # noqa: E402
import bus_events as E        # noqa: E402
import bus_projects as P      # noqa: E402
import bus_settings as SET    # noqa: E402

CTL = P.CONTROLLER


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="busml-")
        self._saved = (B.ROOT, B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.LOCK, B.PRIORITY, B.WORKSPACE,
                       P.PROJECTS, P.COMMANDS, P.DONE, P.NOTIFIED, P.NUDGES, P.STALE_MARK, P.BRIDGE,
                       SET.SETTINGS, SET.WORKSPACE_CFG, E.EVENTS)
        B.ROOT = B.WORKSPACE = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.LOCK = os.path.join(self.tmp, "console", "controller.lock")
        B.PRIORITY = os.path.join(self.tmp, "console", "priority.json")
        P.PROJECTS, P.COMMANDS, P.DONE = (os.path.join(self.tmp, *d) for d in (("projects",), ("console", "commands"), ("console", "done")))
        P.NOTIFIED = os.path.join(self.tmp, "console", "notified.json")
        P.NUDGES = os.path.join(self.tmp, "console", "nudges.json")
        P.STALE_MARK = os.path.join(self.tmp, "console", "stale_notified.json")
        P.BRIDGE = os.path.join(self.tmp, "console", "bridge.json")
        SET.SETTINGS = os.path.join(self.tmp, "settings.json")
        SET.WORKSPACE_CFG = os.path.join(self.tmp, "workspace.json")
        E.EVENTS = os.path.join(self.tmp, "events")
        for d in (B.PEERS, B.INBOX, B.OUTBOX, B.LOG, P.PROJECTS, P.COMMANDS, P.DONE,
                  os.path.join(self.tmp, "console"), E.EVENTS):
            os.makedirs(d, exist_ok=True)

    def tearDown(self):
        (B.ROOT, B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.LOCK, B.PRIORITY, B.WORKSPACE,
         P.PROJECTS, P.COMMANDS, P.DONE, P.NOTIFIED, P.NUDGES, P.STALE_MARK, P.BRIDGE,
         SET.SETTINGS, SET.WORKSPACE_CFG, E.EVENTS) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mkcmd(self, cid, kind="custom", payload=None):
        B.write_json(os.path.join(P.COMMANDS, cid + ".json"),
                     {"id": cid, "kind": kind, "payload": payload or {}, "created": B.now_iso(), "status": "queued"})


class TestDualLeaderClaim(Base):
    def test_two_leaders_claim_distinct_commands(self):
        # 两个并发会话领 30 条指令：不得重复领取、不得丢失，且各自留下会话标记
        for i in range(30):
            self.mkcmd(f"20261001-0000{i:02d}-c{i:02d}")
        bag, errors = {"s1": [], "s2": []}, []

        def worker(sid):
            try:
                while True:
                    got = P.pending_commands(claim=True, limit=1, session=sid)
                    if not got:
                        return
                    bag[sid].extend(c["id"] for c in got)
            except Exception as exc:  # noqa: BLE001  线程里的断言要回到主线程抛
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(sid,)) for sid in ("s1", "s2")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertFalse(errors, errors)
        s1, s2 = bag["s1"], bag["s2"]
        self.assertTrue(s1 and s2, "两个会话都应领到指令，不能一个饿死")
        self.assertFalse(set(s1) & set(s2), "同一条指令不得被两个会话重复领取")
        self.assertEqual(len(s1) + len(s2), 30)
        taken = [f for f in os.listdir(P.COMMANDS) if f.endswith(".taken")]
        self.assertEqual(len(taken), 30)
        rec = B.read_json(os.path.join(P.COMMANDS, taken[0]), {})
        self.assertIn(rec.get("claimed_session"), ("s1", "s2"))

    def test_console_wait_assigns_stable_session(self):
        # 不传 session：服务端分配并经返回体告知；带着它再来，心跳按会话并存
        out1 = P.console_wait(1, holder=CTL)
        self.assertIn("SESSION=", out1)
        sid = out1.split("SESSION=", 1)[1].split("（", 1)[0].strip().splitlines()[0]
        self.assertTrue(sid)
        out2 = P.console_wait(1, holder=CTL, session=sid)
        self.assertIn("SESSION=" + sid, out2)
        P.console_wait(1, holder=CTL, session="other999")
        data = B.read_json(os.path.join(B.PEERS, "controller.json"), {})
        self.assertIn(sid, data.get("sessions", {}))
        self.assertIn("other999", data.get("sessions", {}))

    def test_stale_session_is_reaped(self):
        B.heartbeat(CTL, "idle", "controller", session="old1")
        path = os.path.join(B.PEERS, "controller.json")
        data = B.read_json(path, {})
        data["sessions"]["old1"]["last_seen"] = "2000-01-01T00:00:00"
        B.write_json(path, data)
        B.heartbeat(CTL, "idle", "controller", session="new1")
        data = B.read_json(path, {})
        self.assertNotIn("old1", data.get("sessions", {}))
        self.assertIn("new1", data.get("sessions", {}))
        # 在线判定不受会话增删影响：顶层 last_seen 始终是最新一拍
        self.assertTrue(B.peer_state(CTL)["online"])


class TestLeaseIntegrity(Base):
    def test_concurrent_acquire_never_dual_holder(self):
        errors, results = [], []

        def worker(holder):
            try:
                for _ in range(40):
                    results.append(B.acquire_lease(holder).get("holder"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(h,)) for h in ("p1", "p2", "p1", "p2")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertFalse(errors, errors)
        self.assertTrue(results)
        self.assertTrue(set(results) <= {"p1", "p2"}, f"出现混乱 holder：{set(results)}")
        self.assertNotIn("?", results)
        # 文件任何时刻都是完整 JSON：最终读必须可解析且 holder 合法
        final = B.read_json(B.LOCK, None)
        self.assertIn(final.get("holder"), ("p1", "p2"))
        self.assertGreaterEqual(int(final.get("epoch", 0)), 1)

    def test_force_release_rechecks_holder_and_epoch(self):
        lease = B.acquire_lease("p1")
        self.assertFalse(B.force_release_lease("p1", lease["epoch"] + 999), "epoch 不符不得删")
        self.assertFalse(B.force_release_lease("p2", lease["epoch"]), "holder 不符不得删")
        self.assertEqual((B.lease() or {}).get("holder"), "p1")
        self.assertTrue(B.force_release_lease("p1", lease["epoch"]))
        self.assertFalse(B.lease())

    def test_corrupt_lease_is_quarantined_not_overwritten(self):
        os.makedirs(os.path.dirname(B.LOCK), exist_ok=True)
        with open(B.LOCK, "w", encoding="utf-8") as fh:
            fh.write("not-json{{")
        lease = B.acquire_lease("p1")
        self.assertEqual(lease.get("holder"), "p1")
        quarantined = [f for f in os.listdir(os.path.dirname(B.LOCK)) if ".corrupt-" in f]
        self.assertTrue(quarantined, "损坏的租约文件必须移走留证，不能当空白覆盖")
        self.assertEqual((B.lease() or {}).get("holder"), "p1")


class TestSharedFileLocks(Base):
    def test_nudges_concurrent_no_lost_count(self):
        errors = []

        def worker():
            try:
                for _ in range(25):
                    P.add_nudge("t1", by="user")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertFalse(errors, errors)
        self.assertEqual(P.load_nudges()["t1"]["count"], 200, "并发催办不得丢 count")

    def test_mark_notified_concurrent_no_lost_ids(self):
        errors = []

        def worker(i):
            try:
                for n in range(20):
                    P._mark_notified([f"t{i}-{n}"])
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertFalse(errors, errors)
        self.assertEqual(len(P._load_notified()), 120, "并发提醒台账不得丢 id")

    def test_settings_save_concurrent_no_lost_tokens(self):
        errors = []

        def worker(i):
            try:
                SET.save({"peer_tokens": {f"peer{i:02d}": "__rotate__"}})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertFalse(errors, errors)
        tokens = SET.load().get("peer_tokens") or {}
        self.assertEqual(len(tokens), 8, f"并发保存丢了 token：{sorted(tokens)}")
        self.assertTrue(all(len(v) >= 32 for v in tokens.values()))


class TestMcpPeerBinding(Base):
    def test_peer_token_cannot_impersonate(self):
        import bus_mcp as M
        cases = [
            ("glm1", "controller", "ERROR", "peer token 传 controller 必须拒绝"),
            ("glm1", "other1", "ERROR", "peer token 传别人的名字必须拒绝"),
            ("glm1", "", "glm1", "peer token 缺省 peer 按绑定身份"),
            ("glm1", "glm1", "glm1", "peer token 本人放行（代理 Leader 流程）"),
            ("", "controller", "controller", "主 token 不受限"),
            ("", "", "", "主 token 缺省保持空（bus_tool 落 controller）"),
        ]
        for bound, given, want, why in cases:
            token = M.CURRENT_PEER.set(bound)
            try:
                peer, err = M._bind_peer(given)
            finally:
                M.CURRENT_PEER.reset(token)
            if want == "ERROR":
                self.assertTrue(err.startswith("ERROR"), f"{why}：bound={bound!r} given={given!r} -> {err!r}")
            else:
                self.assertEqual((peer, err), (want, ""), why)


class TestHotReloadEndpoint(Base):
    def test_hot_reload_reload_endpoint(self):
        import bus_web as W
        srv = W.ThreadingHTTPServer(("127.0.0.1", 0), W.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/admin/hot_reload",
                                         data=b"{}", method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.load(resp)
            self.assertTrue(result["ok"], result.get("errors"))
            self.assertIn("bus_core", result["reloaded"])
            self.assertIn("bus_projects", result["reloaded"])
            self.assertNotIn("bus_locks", result["reloaded"], "锁注册表模块不得热重载")
            self.assertNotIn("bus_implementation_lock", result["reloaded"])
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=5) as resp:
                self.assertTrue(json.load(resp)["ok"])
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
