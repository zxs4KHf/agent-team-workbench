"""ADR 0014：Arena 做成可选插件。清单合法、数据迁移只复制不删、ArenaX 按配置决定拉不拉、
插件进程真能起控制面；核心里不再有 Arena 的页面、动作和写死转发。测试只用临时目录与临时端口。"""
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUG = os.path.join(ROOT, "plugins", "arena")
sys.path.insert(0, ROOT)
import bus_plugins as PL  # noqa: E402


def load_main():
    spec = importlib.util.spec_from_file_location("arena_plugin_main", os.path.join(PLUG, "main.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


class Manifest(unittest.TestCase):
    def test_manifest_valid_and_proxy_port_declared(self):
        with open(os.path.join(PLUG, "plugin.json"), encoding="utf-8") as f:
            m = json.load(f)
        self.assertEqual(PL.validate(m, "arena"), [])
        self.assertEqual(m["proxy"], {"port": "port"})
        self.assertEqual(m["page"], "web/index.html")
        self.assertEqual(m["config"]["arenax_root"], "")          # 仓库里不写本机私人路径


class Launcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wbarena-")
        self.M = load_main()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_migrate_copies_once_and_keeps_legacy(self):
        legacy, data = os.path.join(self.tmp, "legacy"), os.path.join(self.tmp, "data")
        os.makedirs(legacy)
        for f in ("accounts.json", "keys.json"):
            open(os.path.join(legacy, f), "w").write('{"x": 1}')
        open(os.path.join(legacy, "notes.txt"), "w").write("skip")
        self.assertEqual(self.M.migrate(data, legacy), ["accounts.json", "keys.json"])
        self.assertTrue(os.path.exists(os.path.join(legacy, "accounts.json")))      # 旧数据不动，回滚可用
        open(os.path.join(legacy, "routes.json"), "w").write("{}")
        self.assertEqual(self.M.migrate(data, legacy), [])                           # 新目录已有数据：不再覆盖
        self.assertEqual(self.M.migrate(os.path.join(self.tmp, "d2"), os.path.join(self.tmp, "none")), [])

    def test_arenax_only_when_root_has_run_server(self):
        self.assertIsNone(self.M.arenax_plan({"arenax_root": ""}))
        self.assertIsNone(self.M.arenax_plan({"arenax_root": self.tmp}))
        open(os.path.join(self.tmp, "run_server.py"), "w").close()
        argv, cwd, env, port = self.M.arenax_plan({"arenax_root": self.tmp, "arenax_url": "http://127.0.0.1:9191/", "arenax_proxy": "http://p:1"})
        self.assertEqual((cwd, port, env["HTTPS_PROXY"]), (self.tmp, 9191, "http://p:1"))
        self.assertTrue(argv[-1].endswith("run_server.py"))
        _, _, env, port = self.M.arenax_plan({"arenax_root": self.tmp, "arenax_url": "https://relay.example", "arenax_proxy": ""})
        self.assertEqual(port, 443)
        self.assertNotIn("HTTPS_PROXY", {k: v for k, v in env.items() if v == ""})

    def test_plugin_process_serves_control_plane(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        data, legacy = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "legacy-empty")
        os.makedirs(data)
        with open(os.path.join(data, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"upstream": "http://127.0.0.1:1"}, f)            # 上游指向必拒端口：不碰真实 ArenaX
        env = dict(os.environ, WORKBENCH_PLUGIN_CONFIG=json.dumps({"port": port}), WORKBENCH_PLUGIN_DATA=data,
                   ARENA_LEGACY_DATA=legacy, WORKBENCH_PARENT_PID=str(os.getpid()), PYTHONIOENCODING="utf-8")
        p = subprocess.Popen([sys.executable, "-X", "utf8", "main.py"], cwd=PLUG, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            body = None
            for _ in range(60):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                        body = json.loads(r.read())
                        break
                except OSError:
                    time.sleep(0.5)
            self.assertIsNotNone(body, "插件进程 30 秒内没起来")
            self.assertTrue(body["ok"])
        finally:
            p.kill()
            p.wait(10)
            p.stdout.close()


class CoreHasNoArena(unittest.TestCase):
    def test_core_frontend_and_web_have_no_arena(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "web", "v2", "js", "arena.js")))
        for f in ("app.js", "views.js", "views2.js"):
            src = read("web", "v2", "js", f)
            self.assertNotIn("arena.js", src, f)
            self.assertNotIn("ARENA_", src, f)
        web = read("bus_web.py")
        self.assertNotIn("ARENA_URL", web)
        self.assertNotIn('"/api/arena/', web)
        self.assertFalse(os.path.isdir(os.path.join(ROOT, "arena")) and os.path.exists(os.path.join(ROOT, "arena", "arena_server.py")))

    def test_core_frontend_never_touches_arena_object(self):
        # 2026-10-02：搬走 arena.js 后 app.js 还有三处读 A.groups，「接入新 Agent」抽屉一打开就报 A is not defined
        import re
        for f in sorted(x for x in os.listdir(os.path.join(ROOT, "web", "v2", "js")) if x.endswith(".js")):   # 拆分后的 page-* / app-* 也算
            src = read("web", "v2", "js", f)
            self.assertIsNone(re.search(r"(?<![\w.$])A\.", src), f"{f} 还在用 Arena 的 A 对象")

    def test_plugin_page_uses_shared_tokens(self):
        html = read("plugins", "arena", "web", "index.html")
        self.assertIn("/v2/styles/tokens.css", html)
        self.assertIn('id="drawer"', html)
        js = read("plugins", "arena", "web", "arena.js")
        self.assertIn("from '/v2/js/core.js'", js)
        self.assertNotIn("start_arena.bat", js)


if __name__ == "__main__":
    unittest.main()
