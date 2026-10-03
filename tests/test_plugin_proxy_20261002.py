"""ADR 0014：插件声明 proxy 后 /api/<id>/* 才转发到插件自己的服务；关掉插件即 404。
页面只开放 page 所在目录（带服务端的插件，源码与数据不能被当静态文件读走）。守护不再拉起 Arena。"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bus_plugins as PL  # noqa: E402


class Upstream(BaseHTTPRequestHandler):
    def _reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._reply({"seen": self.path})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self._reply({"seen": self.path, "body": json.loads(self.rfile.read(n) or b"{}")})

    def log_message(self, *a):
        pass


class PluginProxy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wbproxy-")
        self.saved = {k: getattr(PL, k) for k in ("PLUGINS", "LOCAL", "LOGS", "DATA")}
        PL.PLUGINS = os.path.join(self.tmp, "plugins")
        PL.LOCAL = os.path.join(self.tmp, "plugins.local.json")
        PL.LOGS = os.path.join(self.tmp, "log", "plugins")
        PL.DATA = os.path.join(self.tmp, ".runtime", "plugins")
        self.up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        self.port = self.up.server_address[1]
        d = os.path.join(PL.PLUGINS, "demo")
        os.makedirs(os.path.join(d, "web"))
        os.makedirs(os.path.join(d, "server"))
        open(os.path.join(d, "web", "index.html"), "w").write("<p>demo</p>")
        open(os.path.join(d, "web", "app.js"), "w").write("// js")
        open(os.path.join(d, "server", "secret.json"), "w").write("{}")
        self.manifest(proxy={"port": "port"}, config={"port": self.port})

    def manifest(self, **extra):
        m = {"id": "demo", "api": PL.API, "kind": "panel", "page": "web/index.html", **extra}
        with open(os.path.join(PL.PLUGINS, "demo", "plugin.json"), "w", encoding="utf-8") as f:
            json.dump(m, f)

    def enable(self, on=True):
        with open(PL.LOCAL, "w", encoding="utf-8") as f:
            json.dump({"enabled": {"demo": on}}, f)

    def tearDown(self):
        if self.up:
            self.up.shutdown()
            self.up.server_close()
        for k, v in self.saved.items():
            setattr(PL, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_target_only_when_enabled(self):
        self.assertEqual(PL.proxy_target("/api/demo/x"), "")
        self.enable()
        self.assertEqual(PL.proxy_target("/api/demo/x"), f"http://127.0.0.1:{self.port}")
        self.assertEqual(PL.proxy_target("/api/other/x"), "")
        self.assertEqual(PL.proxy_target("/plugins/demo/x"), "")
        self.enable(False)
        self.assertEqual(PL.proxy_target("/api/demo/x"), "")

    def test_bad_port_is_manifest_error(self):
        self.manifest(proxy={"port": "port"}, config={"port": "9091"})
        self.enable()
        self.assertTrue(PL.get("demo")["errors"])
        self.assertEqual(PL.proxy_target("/api/demo/x"), "")

    def test_page_dir_only(self):
        self.assertTrue(PL.page_path("demo", "web/app.js"))
        self.assertTrue(PL.page_path("demo", ""))
        self.assertEqual(PL.page_path("demo", "server/secret.json"), "")
        self.assertEqual(PL.page_path("demo", "plugin.json"), "")
        self.assertEqual(PL.page_path("demo", "web/../server/secret.json"), "")

    def test_http_forwarding(self):
        import bus_web as WEB
        srv = ThreadingHTTPServer(("127.0.0.1", 0), WEB.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def call(path, body=None):
            req = urllib.request.Request(base + path, json.dumps(body).encode() if body is not None else None,
                                         {"Content-Type": "application/json", "Origin": base})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()
        try:
            self.assertEqual(call("/api/demo/groups")[0], 404)              # 插件没启用：核心不认识这个接口
            self.enable()
            code, raw = call("/api/demo/groups?x=1")
            self.assertEqual((code, json.loads(raw)["seen"]), (200, "/api/demo/groups?x=1"))
            code, raw = call("/api/demo/groups/save", {"id": "g1"})
            self.assertEqual(json.loads(raw), {"seen": "/api/demo/groups/save", "body": {"id": "g1"}})
            self.assertEqual(call("/api/ping")[0], 200)                     # 核心接口不受影响
            self.up.shutdown()
            self.up.server_close()
            code, raw = call("/api/demo/groups")
            self.assertEqual(code, 503)
            self.assertTrue(json.loads(raw)["plugin_down"])
            self.assertEqual(call("/plugins/demo/web/index.html")[0], 200)
            self.assertEqual(call("/plugins/demo/server/secret.json")[0], 404)
        finally:
            srv.shutdown()
            self.up = None                     # 已经关掉了，tearDown 不再 shutdown（对没跑起来的服务器 shutdown 会永远等）


class GuardNoArena(unittest.TestCase):
    def test_guard_does_not_own_arena(self):
        import bus_guard as G
        self.assertFalse({"arena", "arenax"} & set(G.SERVICES))
        src = open(os.path.join(ROOT, "bus_guard.py"), encoding="utf-8").read()
        self.assertNotIn("ARENAX_ROOT", src)       # 本机私人路径不再写在核心里


if __name__ == "__main__":
    unittest.main()
