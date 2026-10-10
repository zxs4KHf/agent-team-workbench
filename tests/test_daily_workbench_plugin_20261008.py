"""Daily workbench discovery uses only the declared proxy and temporary instance data."""
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bus_plugins as PL
import bus_web as WEB


class PluginTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="daily-workbench-plugin-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for key, rel in {"PLUGINS": "plugins", "LOCAL": "plugins.local.json",
                         "LOGS": "log/plugins", "DATA": ".runtime/plugins"}.items():
            replacement = patch.object(PL, key, str(self.root / rel))
            replacement.start()
            self.addCleanup(replacement.stop)
        self.plugin = self.root / "plugins" / "daily-workbench"
        shutil.copytree(ROOT / "plugins" / "daily-workbench", self.plugin)
        self.requests = []

    def config(self, enabled=True, **config):
        Path(PL.LOCAL).write_text(json.dumps({"enabled": {"daily-workbench": enabled},
                                            "config": {"daily-workbench": config}}), encoding="utf-8")

    def server(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        return server

    def test_manifest_and_local_configuration(self):
        plugin = PL.get("daily-workbench")
        self.assertEqual(plugin["errors"], [])
        self.assertEqual(plugin["kind"], "panel")
        self.assertIsNone(plugin["entry"])
        self.assertEqual(plugin["config"], {"port": 8790, "project": ""})
        self.assertEqual(plugin["proxy"], {"port": "port"})
        self.assertFalse(plugin["enabled"])
        self.assertEqual(PL.proxy_target("/api/daily-workbench/plugin-info"), "")
        self.config(port=18890, project="temporary-project")
        self.assertEqual(PL.proxy_target("/api/daily-workbench/plugin-info"), "http://127.0.0.1:18890")
        self.assertEqual(PL.get("daily-workbench")["config"]["project"], "temporary-project")
        self.assertEqual(PL.proxy_target("/api/daily-workbench-extra/plugin-info"), "")
        self.config(enabled=False)
        self.assertEqual(PL.proxy_target("/api/daily-workbench/plugin-info"), "")

    def test_invalid_config_does_not_create_proxy(self):
        for value in ("8790", 0, 80, 65536, None):
            with self.subTest(port=value):
                self.config(port=value)
                self.assertEqual(PL.proxy_target("/api/daily-workbench/plugin-info"), "")

    def test_only_web_files_are_exposed(self):
        for name in ("index.html", "panel.js", "panel.css"):
            self.assertEqual(PL.page_path("daily-workbench", "web/" + name), str(self.plugin / "web" / name))
        for name in ("plugin.json", "README.md", "web/../plugin.json"):
            self.assertEqual(PL.page_path("daily-workbench", name), "")

    def test_discovery_round_trip_and_disabled_proxy(self):
        seen = self.requests

        class Upstream(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                body = json.dumps({"id": "daily-workbench", "ready": True,
                                   "url": f"http://127.0.0.1:{self.server.server_port}/?embedded=1",
                                   "team_url": "http://127.0.0.1:8765"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        class Handler(WEB.Handler):
            def log_message(self, *args):
                pass

        upstream = self.server(Upstream)
        self.config(port=upstream.server_port)
        server = self.server(Handler)
        base = f"http://127.0.0.1:{server.server_port}"
        path = "/api/daily-workbench/plugin-info"
        with urllib.request.urlopen(base + path, timeout=5) as response:
            payload = json.load(response)
        self.assertEqual(payload, {"id": "daily-workbench", "ready": True,
                                  "url": f"http://127.0.0.1:{upstream.server_port}/?embedded=1",
                                  "team_url": "http://127.0.0.1:8765"})
        self.assertEqual(seen, [path])
        with urllib.request.urlopen(base + "/plugins/daily-workbench/web/index.html", timeout=5) as response:
            self.assertIn('id="daily-workbench"', response.read().decode())
        self.config(enabled=False)
        with self.assertRaises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(base + path, timeout=5)
        self.assertEqual(failure.exception.code, 404)
        self.assertEqual(seen, [path])


if __name__ == "__main__":
    unittest.main()
