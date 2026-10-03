"""接入链接 404 + 主令牌外泄（2026-10-02）。跑法：py -3 tests/test_bridge_link_20261002.py

实况：Bridge 地址被登记成 https://host/mcp/<主令牌>，接入链接再拼 /mcp/<Agent 令牌>，
变成 /mcp/<主令牌>/mcp/<Agent 令牌>：Agent 连接 404；而且主令牌随每份接入提示词发给了所有 Agent。
"""
import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_projects as P  # noqa: E402

MASTER, PEER, OLD = "M" * 40, "P" * 40, "O" * 40


class BridgeBase(Base):
    def test_path_and_token_are_stripped(self):
        self.assertEqual(P.bridge_base(f"https://mcp.example.com/mcp/{MASTER}"), "https://mcp.example.com")
        self.assertEqual(P.bridge_base("http://127.0.0.1:8791/mcp/"), "http://127.0.0.1:8791")
        self.assertEqual(P.bridge_base("mcp.example.com"), "")

    def test_set_and_read_keep_only_host(self):
        P.set_bridge_url(f"https://mcp.example.com/mcp/{MASTER}")
        self.assertEqual(P.bridge_url(), "https://mcp.example.com")
        with self.assertRaises(ValueError):
            P.set_bridge_url("not a url")

    def test_old_stored_value_is_cleaned_on_read(self):
        P.B.write_json(P.BRIDGE, {"url": f"https://mcp.example.com/mcp/{MASTER}"})
        self.assertNotIn(MASTER, P.bridge_url())

    def test_prompt_link_has_one_mcp_segment_and_no_master(self):
        P.B.write_json(P.BRIDGE, {"url": f"https://mcp.example.com/mcp/{MASTER}"})
        link = f"{P.bridge_base(P.bridge_url())}/mcp/{PEER}"
        text = P.prompt_text("peer", "a1", "m", link=link)
        self.assertIn(f"https://mcp.example.com/mcp/{PEER}", text.splitlines())
        self.assertNotIn(MASTER, text)


class DoubledLinkAuth(unittest.TestCase):
    def call(self, path, tokens):
        import bus_mcp as M
        seen = {}

        async def app(scope, receive, send):
            seen["path"], seen["who"] = scope["path"], M.CURRENT_PEER.get()

        async def send(msg):
            seen.setdefault("status", msg.get("status"))
        with mock.patch.object(M, "_live", lambda k, d=None: {"peer_tokens": tokens, "auth_mode": "link"}.get(k, d)):
            asyncio.run(M.BearerAuth(app, MASTER)({"type": "http", "path": path, "headers": []}, None, send))
        return seen

    def test_doubled_link_identifies_as_the_agent_not_leader(self):
        r = self.call(f"/mcp/{MASTER}/mcp/{PEER}", {"a1": PEER})
        self.assertEqual((r["who"], r["path"]), ("a1", "/mcp"))

    def test_doubled_link_with_revoked_agent_token_is_rejected_not_leader(self):
        r = self.call(f"/mcp/{MASTER}/mcp/{OLD}", {"a1": PEER})
        self.assertEqual(r.get("status"), 401)
        self.assertNotIn("who", r)

    def test_doubled_link_with_trailing_junk(self):
        r = self.call(f"/mcp/{MASTER}/mcp/{PEER}，然后", {"a1": PEER})
        self.assertEqual(r["who"], "a1")

    def test_plain_master_link_still_works_for_leader(self):
        self.assertEqual(self.call(f"/mcp/{MASTER}", {})["who"], "")


if __name__ == "__main__":
    unittest.main()
