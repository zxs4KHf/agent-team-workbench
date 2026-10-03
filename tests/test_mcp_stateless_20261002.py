"""MCP 无状态模式（2026-10-02 查 Agent 掉线）：有状态时 MCP 重启 / 会话闲置 30 分钟后，宿主手里的会话号失效，
下一次调用 404「Session not found」，很多宿主就此断开。无状态下重启后拿旧会话号照样能用。
真起 bus_mcp 子进程（临时端口、临时令牌文件），只做握手和 tools/list，不调任何读写数据的工具。"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = "t" * 40


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class McpStateless(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mcp-stateless-")
        self.tok = os.path.join(self.tmp, "token.txt")
        with open(self.tok, "w", encoding="utf-8") as f:
            f.write(TOKEN)
        self.port = free_port()
        self.proc = None

    def tearDown(self):
        self.stop()

    def start(self, stateful=False):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        env.pop("BUS_MCP_STATEFUL", None)
        if stateful:
            env["BUS_MCP_STATEFUL"] = "1"
        self.proc = subprocess.Popen([sys.executable, "-X", "utf8", os.path.join(REPO, "bus_mcp.py"), "--port", str(self.port), "--token-file", self.tok],
                                     cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=1) as r:
                    return json.loads(r.read())
            except OSError:
                time.sleep(0.1)
        self.fail("MCP 没起来")

    def stop(self):
        if self.proc:
            self.proc.kill(); self.proc.wait(10); self.proc = None

    def rpc(self, method, params=None, sid="", rid=1):
        body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": f"Bearer {TOKEN}"}
        if sid:
            h["mcp-session-id"] = sid
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/mcp", data=json.dumps(body).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                text = r.read().decode("utf-8")
                data = [json.loads(l[5:]) for l in text.splitlines() if l.startswith("data:")] or [json.loads(text)]
                return r.status, r.headers.get("mcp-session-id") or "", data[-1]
        except urllib.error.HTTPError as e:
            return e.code, "", {}

    def handshake(self):
        code, sid, data = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        self.assertEqual(code, 200, data)
        return sid

    def test_restart_keeps_existing_clients_working(self):
        self.assertTrue(self.start()["stateless"])
        sid = self.handshake()
        code, _, data = self.rpc("tools/list", sid=sid, rid=2)
        self.assertEqual(code, 200)
        self.assertIn("bus_wait_task", [t["name"] for t in data["result"]["tools"]])
        self.stop(); self.start()                                   # MCP 重启（守护拉起、手动重启都一样）
        code, _, data = self.rpc("tools/list", sid=sid or "stale-session-from-before", rid=3)
        self.assertEqual(code, 200, "重启后宿主拿旧会话号照样能调用，不掉线")

    def test_stateful_rollback_switch_shows_the_old_failure(self):
        # 反向对照：BUS_MCP_STATEFUL=1 切回旧行为，重启后旧会话号 404——这正是 Agent 掉线的样子
        self.assertFalse(self.start(stateful=True)["stateless"])
        sid = self.handshake()
        self.assertTrue(sid, "有状态模式会发会话号")
        self.stop(); self.start(stateful=True)
        code, _, _ = self.rpc("tools/list", sid=sid, rid=2)
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
