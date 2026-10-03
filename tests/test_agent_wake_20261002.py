"""唤醒掉线的 Agent（2026-10-02 用户：「掉线之后能不能按一下按钮就把它拉起来」）。
临时数据目录；Arena 侧用假的发送函数，不碰中继、不发真消息。"""
import json
import os
import sys
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "plugins", "arena", "server"))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_core as B          # noqa: E402
import bus_projects as P      # noqa: E402
import bus_plugins as PL      # noqa: E402
import bus_wake as WK         # noqa: E402
import arena_store as ST      # noqa: E402
import arena_wake as AW       # noqa: E402


def wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class WakeText(unittest.TestCase):
    def test_uses_real_peer_in_tool_args_and_label_only_in_greeting(self):
        t = WK.wake_text("sol-6-2", "gpt-6-sol", "", "小索")
        self.assertIn('bus_wait_task(peer="sol-6-2", model="gpt-6-sol")', t)
        self.assertIn("「小索」（协议名 sol-6-2）", t)
        self.assertIn("IDLE", t)
        self.assertIn("bus_reply", t, "交回工具名要对")
        self.assertNotIn("MCP 服务器", t, "没有接入地址就不提重新接入")

    def test_link_appended_for_hosts_that_lost_mcp(self):
        t = WK.wake_text("opus1", "", "https://h.example/mcp/TOKEN")
        self.assertIn('bus_wait_task(peer="opus1")', t)
        self.assertTrue(t.rstrip().endswith("https://h.example/mcp/TOKEN"))


class WakeApi(Base):
    def setUp(self):
        super().setUp()
        import bus_web as W
        B.heartbeat("opus1", "idle", "claude-opus-5", "", B.now_iso(), 60)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), W.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close()
        super().tearDown()

    def get(self, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.server_address[1]}{path}", method="POST" if body is not None else "GET",
                                     data=json.dumps(body).encode("utf-8") if body is not None else None, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def test_send_without_wake_plugins_returns_text_to_copy(self):
        with mock.patch.object(PL, "wake_targets", lambda: []):
            r = self.get("/api/wake/send", {"peer": "opus1"})
            self.assertEqual(self.get("/api/wake/auto"), {"peers": {}})
        self.assertFalse(r["sent"])
        self.assertIn('bus_wait_task(peer="opus1"', r["text"])

    def test_send_via_plugin_hides_text(self):
        with mock.patch.object(PL, "wake_targets", lambda: [("relay", "http://x/api/relay/wake")]),              mock.patch.object(WK, "_http", lambda url, body=None, timeout=5: {"bound": True, "status": "sending"}):
            r = self.get("/api/wake/send", {"peer": "opus1"})
        self.assertEqual((r["sent"], r["via"]), (True, "relay"))
        self.assertNotIn("text", r, "插件发了就不再把带令牌的文字回给页面")

    def test_wake_endpoint_returns_text_with_model_and_link(self):
        with mock.patch.object(P, "bridge_url", lambda: "https://h.example"):
            r = self.get("/api/wake?peer=opus1")
            r2 = self.get("/api/connect_link?kind=peer&peer=opus1&model=x")
        self.assertEqual((r["peer"], r["model"]), ("opus1", "claude-opus-5"))
        self.assertIn('model="claude-opus-5"', r["text"])
        self.assertIn("https://h.example/mcp/", r["text"], "附上该 Agent 自己的接入地址")
        self.assertIn(r2["url"], r["text"], "和接入链接用同一把令牌（不会每次唤醒都换令牌）")

    def test_wake_without_bridge_uses_local_mcp(self):
        with mock.patch.object(P, "bridge_url", lambda: ""):
            r = self.get("/api/wake?peer=opus1")
            link = self.get("/api/connect_link?kind=peer&peer=opus1&model=x")["url"]
        self.assertTrue(link.startswith("http://127.0.0.1:8791/mcp/"), "没登记 Bridge：退回本机 MCP 地址，不再生成缺主机的 /mcp/<令牌>")
        self.assertIn(link, r["text"])


class PluginRouting(unittest.TestCase):
    def test_first_plugin_that_owns_the_session_sends(self):
        calls = []
        def http(url, body=None, timeout=5):
            calls.append(url)
            if "down" in url:
                raise OSError("refused")
            return {"bound": "b" in url}
        r = WK.send_via_plugins("p", "t", [("down", "http://down"), ("a", "http://a"), ("b", "http://b"), ("c", "http://c")], http=http)
        self.assertEqual((r["sent"], r["via"]), (True, "b"))
        self.assertNotIn("http://c", calls, "有人发了就不再问后面的")
        r = WK.send_via_plugins("p", "t", [("a", "http://a")], http=http)
        self.assertFalse(r["sent"])

    def test_auto_peers_skips_dead_plugins(self):
        def http(url, body=None, timeout=5):
            if "down" in url:
                raise OSError("refused")
            return {"bound": ["opus3"]}
        self.assertEqual(WK.auto_peers([("down", "http://down"), ("arena", "http://a")], http=http), {"opus3": "arena"})

    def test_manifest_wake_validation(self):
        base = {"id": "x1", "api": PL.API, "kind": "panel", "config": {"port": 9100}, "proxy": {"port": "port"}}
        self.assertEqual(PL.validate({**base, "wake": "wake"}, "x1"), [])
        self.assertTrue(PL.validate({**base, "wake": "../evil"}, "x1"))
        self.assertTrue(PL.validate({k: v for k, v in {**base, "wake": "wake"}.items() if k != "proxy"}, "x1"), "没有 proxy 不能声明 wake")


class ArenaWake(unittest.TestCase):
    def setUp(self):
        AW._STATE.clear()
        self.sessions = {"sessions": [{"id": "s-old", "peer": "opus3"}, {"id": "s-new", "peer": "opus3"}, {"id": "s-x"}]}
        self.p = mock.patch.object(ST, "load", lambda kind: self.sessions if kind == "sessions" else {})
        self.p.start()
        self.sent = []

    def tearDown(self):
        self.p.stop()

    def send(self, sid, text):
        self.sent.append((sid, text))

    def test_unbound_peer_falls_back_to_copy(self):
        self.assertEqual(AW.wake("opus1", "hi", send=self.send), {"peer": "opus1", "bound": False})
        self.assertEqual(self.sent, [])
        self.assertEqual(AW.bound_peers(), ["opus3"])

    def test_bound_peer_sends_to_latest_session_once(self):
        r = AW.wake("opus3", "醒醒", send=self.send)
        self.assertEqual((r["bound"], r["session_id"]), (True, "s-new"))
        self.assertTrue(wait(lambda: AW.status("opus3").get("status") == "sent"))
        self.assertEqual(self.sent, [("s-new", "醒醒")])
        again = AW.wake("opus3", "醒醒", send=self.send)
        self.assertTrue(again["duplicate"], "刚发过的不重复发（连点 / 多标签）")
        self.assertEqual(len(self.sent), 1)

    def test_quick_error_is_failed_slow_error_counts_as_sent(self):
        def boom(sid, text):
            raise RuntimeError("relay refused")
        AW.wake("opus3", "x", send=boom)
        self.assertTrue(wait(lambda: AW.status("opus3").get("status") == "failed"))
        AW._STATE.clear()
        with mock.patch.object(AW, "FAIL_FAST_SEC", -1):
            AW.wake("opus3", "x", send=boom)
            self.assertTrue(wait(lambda: AW.status("opus3").get("status") == "sent"), "慢超时按已发出处理，不重发")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            AW.wake("", "x")


class Frontend(unittest.TestCase):
    def test_buttons_wired(self):
        def js(n):
            with open(os.path.join(ROOT, "web", "v2", "js", n), encoding="utf-8") as f:
                return f.read()
        self.assertIn("data-action=\"wake-peer\"", js("page-agents.js"))
        self.assertIn("data-action=\"wake-all\"", js("page-agents.js"))
        main = js("main.js")
        for a in ("'wake-peer'", "'wake-all'", "'wake-auto-all'"):
            self.assertIn(a, main)
        self.assertIn("/api/wake/send", js("app-wake.js"))
        self.assertNotIn("/api/arena/", js("app-wake.js"), "核心前端不认识具体插件（ADR 0014）")
        with open(os.path.join(ROOT, "plugins", "arena", "plugin.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f).get("wake"), "wake", "Arena 声明自己能发唤醒语")


if __name__ == "__main__":
    unittest.main()
