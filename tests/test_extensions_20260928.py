"""扩展层回归测试：事件记录、收件箱、体验反馈、插件管理、请求来源校验、个人助理数据源插件。
全部在临时目录里跑，不碰真实 events/ intake/ feedback/ plugins.local.json。"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bus_events as E  # noqa: E402
import bus_feedback as FB  # noqa: E402
import bus_intake as IN  # noqa: E402
import bus_plugins as PL  # noqa: E402
import bus_projects as P  # noqa: E402


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wbext-")
        self.saved = {(E, "EVENTS"): E.EVENTS, (IN, "INTAKE"): IN.INTAKE, (FB, "FEEDBACK"): FB.FEEDBACK,
                      (PL, "PLUGINS"): PL.PLUGINS, (PL, "LOCAL"): PL.LOCAL, (PL, "LOGS"): PL.LOGS, (PL, "DATA"): PL.DATA,
                      (P, "_push_command_impl"): P._push_command_impl}
        E.EVENTS = os.path.join(self.tmp, "events")
        IN.INTAKE = os.path.join(self.tmp, "intake")
        FB.FEEDBACK = os.path.join(self.tmp, "feedback")
        PL.PLUGINS = os.path.join(self.tmp, "plugins")
        PL.LOCAL = os.path.join(self.tmp, "plugins.local.json")
        PL.LOGS = os.path.join(self.tmp, "log", "plugins")
        PL.DATA = os.path.join(self.tmp, ".runtime", "plugins")
        self.commands = []

        def fake_push(kind, payload):
            self.commands.append((kind, payload))
            return {"id": f"cmd-{len(self.commands)}", "kind": kind}
        P._push_command_impl = fake_push

    def tearDown(self):
        PL.stop_all()
        PL._procs.clear()
        for (mod, name), val in self.saved.items():
            setattr(mod, name, val)
        shutil.rmtree(self.tmp, ignore_errors=True)


class EventsTest(Sandbox):
    def test_emit_read_cursor(self):
        for i in range(3):
            self.assertTrue(E.emit("t.x", {"i": i}))
        r = E.read()
        self.assertEqual([e["data"]["i"] for e in r["events"]], [0, 1, 2])
        self.assertEqual(E.read(r["cursor"])["events"], [])
        E.emit("t.y", {"i": 3})
        r2 = E.read(r["cursor"])
        self.assertEqual([e["type"] for e in r2["events"]], ["t.y"])
        self.assertEqual(r2["events"][0]["specversion"], "1.0")
        self.assertEqual(E.recent(2)[0]["type"], "t.y")

    def test_partial_line_not_consumed(self):
        E.emit("t.a")
        day = sorted(os.listdir(E.EVENTS))[0]
        with open(os.path.join(E.EVENTS, day), "a", encoding="utf-8") as f:
            f.write('{"half":')
        r = E.read()
        self.assertEqual(len(r["events"]), 1)
        self.assertEqual(E.read(r["cursor"])["events"], [])

    def test_emit_never_raises(self):
        E.EVENTS = os.path.join(self.tmp, "file-not-dir")
        open(E.EVENTS, "w").close()
        self.assertEqual(E.emit("t.fail"), {})

    def test_post_and_command_emit(self):
        P.push_command("custom", {"text": "x"})
        self.assertEqual(self.commands[0][0], "custom")
        self.assertIn("command.queued", [e["type"] for e in E.read()["events"]])


class IntakeTest(Sandbox):
    def test_add_idempotent_and_decide(self):
        items = [{"fingerprint": "a", "title": "写周报"}, {"fingerprint": "b", "title": "关卡对读"},
                 {"fingerprint": "a", "title": "写周报"}, {"fingerprint": "", "title": "无指纹"}]
        r = IN.add("daily-assistant", items)
        self.assertEqual((r["added"], r["skipped"]), (2, 2))
        self.assertEqual(IN.add("daily-assistant", items[:1])["added"], 0)
        self.assertEqual(IN.counts()["pending"], 2)
        a, b = r["ids"]
        out = IN.decide(a, "accept", project="协作工作台", note="本周做")
        self.assertEqual(out["item"]["status"], "accepted")
        self.assertEqual(self.commands[-1][0], "external")
        self.assertEqual(self.commands[-1][1]["intake_id"], a)
        with self.assertRaises(ValueError):
            IN.decide(a, "dismiss")
        IN.decide(b, "dismiss")
        self.assertEqual(IN.counts(), {"pending": 0, "accepted": 1, "dismissed": 1})
        self.assertEqual(len(self.commands), 1)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            IN.add("Bad Source!", [])
        with self.assertRaises(ValueError):
            IN.add("x1", [{}] * (IN.MAX_BATCH + 1))
        with self.assertRaises(ValueError):
            IN.decide("../etc", "accept")


class FeedbackTest(Sandbox):
    def test_lifecycle(self):
        with self.assertRaises(ValueError):
            FB.create(" ")
        f = FB.create("派发后找不到任务", kind="ux", view="概览", target='button#dispatchBtn "派发任务"')
        self.assertEqual(f["status"], "open")
        c = FB.update(f["id"], "converted", note="优先")
        self.assertEqual(c["status"], "converted")
        kind, payload = self.commands[-1]
        self.assertEqual(kind, "custom")
        self.assertIn(f["id"], payload["text"])
        self.assertIn("dispatchBtn", payload["text"])
        with self.assertRaises(ValueError):
            FB.update(f["id"], "converted")
        self.assertEqual(FB.update(f["id"], "done", "abc123")["status"], "done")
        self.assertEqual(FB.counts()["done"], 1)
        types = [e["type"] for e in E.read()["events"]]
        self.assertIn("feedback.created", types)
        self.assertIn("feedback.done", types)


PLUGIN_MAIN = r'''
import json, os, time
d = os.environ["WORKBENCH_PLUGIN_DATA"]
cfg = json.loads(os.environ["WORKBENCH_PLUGIN_CONFIG"])
with open(os.path.join(d, "ran.json"), "w") as f:
    json.dump({"cfg": cfg, "id": os.environ["WORKBENCH_PLUGIN_ID"], "url": os.environ["WORKBENCH_URL"]}, f)
time.sleep(60)
'''


class PluginsTest(Sandbox):
    def make(self, folder, manifest, main=None):
        d = os.path.join(PL.PLUGINS, folder)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "plugin.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        if main:
            with open(os.path.join(d, "main.py"), "w", encoding="utf-8") as f:
                f.write(main)
        return d

    def wait_for(self, path, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if os.path.exists(path):
                return True
            time.sleep(0.2)
        return False

    def test_discover_toggle_config(self):
        good = {"id": "demo", "name": "演示", "api": PL.API, "kind": "source", "entry": ["python", "main.py"], "config": {"n": 1}}
        self.make("demo", good, PLUGIN_MAIN)
        self.make("wrong", dict(good, id="other"))
        with open(os.path.join(self.make("page", {"id": "page", "api": PL.API, "kind": "panel", "page": "index.html"}), "index.html"), "w") as f:
            f.write("<p>hi</p>")
        ps = {p["id"]: p for p in PL.discover()}
        self.assertEqual(ps["demo"]["errors"], [])
        self.assertTrue(ps["other"]["errors"])
        with self.assertRaises(ValueError):
            PL.toggle("other", True)
        PL._base_url = "http://127.0.0.1:1"
        p = PL.toggle("demo", True)
        self.assertTrue(p["enabled"])
        ran = os.path.join(PL.DATA, "demo", "ran.json")
        self.assertTrue(self.wait_for(ran), PL.log_tail("demo"))
        with open(ran) as f:
            info = json.load(f)
        self.assertEqual(info["cfg"], {"n": 1})
        self.assertEqual(info["id"], "demo")
        self.assertTrue(PL.status("demo")["running"])
        with self.assertRaises(ValueError):
            PL.set_config("demo", {"unknown": 1})
        os.remove(ran)
        PL.set_config("demo", {"n": 2})
        self.assertTrue(self.wait_for(ran))
        with open(ran) as f:
            self.assertEqual(json.load(f)["cfg"], {"n": 2})
        with open(PL.LOCAL, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["config"]["demo"], {"n": 2})
        PL.toggle("demo", False)
        self.assertFalse(PL.status("demo")["running"])
        self.assertTrue(PL.page_path("page", "index.html"))
        self.assertEqual(PL.page_path("page", "../demo/plugin.json"), "")
        self.assertEqual(PL.page_path("page", "../../plugins.local.json"), "")

    def test_crash_restart_limit(self):
        self.make("crash", {"id": "crash", "api": PL.API, "kind": "source", "entry": ["python", "main.py"]}, "raise SystemExit(3)\n")
        PL.toggle("crash", True)
        for _ in range(PL.RESTART_LIMIT + 3):
            time.sleep(0.4)
            PL.supervise_once()
        st = PL.status("crash")
        self.assertFalse(st["running"])
        self.assertIn("停止自动重启", st["error"])


class RequestGuardTest(Sandbox):
    def test_rules(self):
        import bus_web as WEB
        ok = WEB.request_allowed
        self.assertTrue(ok("GET", "127.0.0.1:8765", "")[0])
        self.assertTrue(ok("GET", "localhost:8765", "")[0])
        self.assertTrue(ok("POST", "[::1]:8765", "")[0])
        self.assertTrue(ok("POST", "127.0.0.1:8765", "http://127.0.0.1:8765")[0])
        self.assertTrue(ok("POST", "", "")[0])
        self.assertFalse(ok("GET", "evil.example:8765", "")[0])          # DNS rebinding
        self.assertFalse(ok("POST", "127.0.0.1:8765", "http://evil.example")[0])  # CSRF
        self.assertFalse(ok("POST", "127.0.0.1:8765", "null")[0])
        self.assertFalse(ok("POST", "127.0.0.1:8765", "http://127.0.0.1:9999")[0])

    def test_http(self):
        import bus_web as WEB
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), WEB.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            def call(path, body=None, headers=None):
                req = urllib.request.Request(base + path, json.dumps(body).encode() if body is not None else None,
                                             {"Content-Type": "application/json", **(headers or {})})
                try:
                    with urllib.request.urlopen(req, timeout=10) as r:
                        return r.status, json.loads(r.read() or b"{}")
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read() or b"{}")
            self.assertEqual(call("/api/feedback", {"text": "跨站"}, {"Origin": "http://evil.example"})[0], 403)
            self.assertEqual(FB.items(), [])
            code, rec = call("/api/feedback", {"text": "本机正常反馈"}, {"Origin": base})
            self.assertEqual(code, 200, rec)
            self.assertEqual(call("/api/feedback")[1]["counts"]["open"], 1)
            self.assertEqual(call("/api/ping")[0], 200)
            self.assertEqual(call("/api/ping", headers={"Host": "evil.example"})[0], 403)
            code, r = call("/api/intake", {"source": "demo", "items": [{"fingerprint": "1", "title": "t"}]})
            self.assertEqual((code, r["added"]), (200, 1))
            self.assertEqual(call("/api/intake/decide", {"id": r["ids"][0], "action": "nope"})[0], 400)
            self.assertEqual(call("/api/plugins")[0], 200)
            self.assertEqual(call("/api/event-log?limit=5")[0], 200)
            with urllib.request.urlopen(base + "/hub", timeout=10) as h:
                self.assertIn("扩展与来源", h.read().decode("utf-8"))
        finally:
            srv.shutdown()
            srv.server_close()


def load_daily():
    path = os.path.join(ROOT, "plugins", "daily-assistant", "main.py")
    spec = importlib.util.spec_from_file_location("daily_plugin", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DailyAssistantPluginTest(Sandbox):
    def test_is_done(self):
        D = load_daily()
        self.assertTrue(D.is_done({"done": True}))
        self.assertTrue(D.is_done({"状态": "已完成"}))
        self.assertTrue(D.is_done({"status": "Done "}))
        self.assertTrue(D.is_done({"fields": {"状态": "已关闭"}}))
        self.assertFalse(D.is_done({"状态": "进行中"}))
        self.assertFalse(D.is_done({"title": "x"}))

    def test_scan_mtime_baseline(self):
        import datetime as dt
        D = load_daily()
        col = os.path.join(self.tmp, "collected")
        os.makedirs(col)
        today = dt.date(2026, 9, 28)
        f = os.path.join(col, "2026-09-28-quadrant.json")

        def write(items, mt):
            with open(f, "w", encoding="utf-8") as fh:
                json.dump({"ok": True, "items": items}, fh, ensure_ascii=False)
            os.utime(f, (mt, mt))
        write([{"title": "A 待办"}, {"title": "B", "状态": "已完成"}, {"title": "C 关卡"}], 1000)
        cfg = {"collected_dir": col, "sources": ["quadrant"], "keywords": "", "poll_sec": 10, "lookback_days": 1}
        st = D.State(os.path.join(self.tmp, "pd"))
        self.assertTrue(st.first_run)
        first = D.scan(cfg, st, today)
        self.assertEqual(sorted(c["title"] for c in first), ["A 待办", "C 关卡"])   # 已完成的 B 被过滤
        self.assertEqual(D.scan(cfg, st, today), [])                                # mtime 未变不重读
        write([{"title": "A 待办"}, {"title": "C 关卡"}, {"title": "D 新增"}], 2000)
        self.assertEqual([c["title"] for c in D.scan(cfg, st, today)], ["D 新增"])  # 只送新增
        with open(f, "w", encoding="utf-8") as fh:
            fh.write('{"ok": true, "items": [')                                    # 半写入
        os.utime(f, (3000, 3000))
        self.assertEqual(D.scan(cfg, st, today), [])
        self.assertNotEqual(st.d["mtimes"].get("2026-09-28-quadrant"), 3000)       # 下轮重试
        cfg["keywords"] = "关卡"
        write([{"title": "E 关卡"}, {"title": "F 无关"}], 4000)
        self.assertEqual([c["title"] for c in D.scan(cfg, st, today)], ["E 关卡"])


class ReviewFixes6683(Sandbox):
    """审查 6683 的修复：不按 pid 杀进程、环境变量白名单、cursor=now、入队失败回退。"""

    def test_stale_pid_not_killed(self):
        path = PL._pidfile("zz-stale-test")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(str(os.getpid()))      # 指向本测试进程：若被杀，测试不会继续跑
        PL._kill_stale("zz-stale-test")
        self.assertFalse(os.path.exists(path))
        self.assertIn("不强杀", open(os.path.join(PL.LOGS, "zz-stale-test.log"), encoding="utf-8").read())

    def test_env_whitelist(self):
        os.environ["ZZ_SECRET_TOKEN"] = "x"
        try:
            env = PL._base_env()
            self.assertNotIn("ZZ_SECRET_TOKEN", env)
            self.assertTrue(any(k.upper() == "PATH" for k in env))
        finally:
            del os.environ["ZZ_SECRET_TOKEN"]

    def test_cursor_now(self):
        E.emit("test.before", {"n": 1})
        cur = E.read("now")["cursor"]
        self.assertTrue(cur)
        E.emit("test.after", {"n": 2})
        types = [e.get("type") for e in E.read(cur)["events"]]
        self.assertIn("test.after", types)
        self.assertNotIn("test.before", types)

    def test_intake_push_failure_reverts(self):
        IN.add("zz-src", [{"fingerprint": "rv-1", "title": "回退测试"}])
        iid = IN.items("pending")[0]["id"]

        def boom(kind, payload):
            raise RuntimeError("queue down")
        P._push_command_impl = boom
        with self.assertRaises(RuntimeError):
            IN.decide(iid, "accept")
        self.assertEqual(json.load(open(IN._path(iid), encoding="utf-8"))["status"], "pending")


if __name__ == "__main__":
    unittest.main(verbosity=2)
