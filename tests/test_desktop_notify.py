"""desktop-notify 插件的纯函数：事件描述、合并、免打扰、限流。不弹窗。"""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("dn", Path(__file__).resolve().parents[1] / "plugins" / "desktop-notify" / "main.py")
N = importlib.util.module_from_spec(spec); spec.loader.exec_module(N)
ev = lambda t, **d: {"type": t, "data": d}
ALL = list(N.LABELS)


class DesktopNotify(unittest.TestCase):
    def test_describe(self):
        self.assertEqual(N.describe(ev("decision.merged", title="字号缩放", project="协作工作台")), ("decision.merged", "字号缩放（协作工作台）"))
        self.assertEqual(N.describe(ev("intake.added", count=3, source="daily-assistant"))[1], "3 条，来自 daily-assistant")
        self.assertEqual(N.describe(ev("intake.added", count="x"))[1], "若干 条，来自 未知来源", "计数不合法不显示假数字")
        self.assertIsNone(N.describe(ev("decision.dispatched", title="x")), "只通知值得打扰你的几类")
        self.assertIsNone(N.describe({}))

    def test_summarize_single_and_batch(self):
        self.assertEqual(N.summarize([ev("decision.landed", title="计数校验")], ALL), ("工作台 · 施工已落地", "计数校验"))
        t, b = N.summarize([ev("decision.merged", title="A"), ev("decision.merged", title="B"), ev("intake.added", count=2, source="s")], ALL)
        self.assertEqual(t, "工作台 · 待你拍板 2 · 收件箱新增 1"); self.assertEqual(b.count("\n"), 2)
        many = [ev("decision.merged", title=str(i)) for i in range(7)]
        self.assertIn("……共 7 条", N.summarize(many, ALL)[1])

    def test_type_filter(self):
        self.assertIsNone(N.summarize([ev("intake.added", count=1)], ["decision.merged"]))
        self.assertIsNone(N.summarize([], ALL))

    def test_quiet_hours(self):
        self.assertTrue(N.in_quiet("23-8", 23)); self.assertTrue(N.in_quiet("23-8", 3)); self.assertFalse(N.in_quiet("23-8", 8)); self.assertFalse(N.in_quiet("23-8", 12))
        self.assertTrue(N.in_quiet("12-14", 13)); self.assertFalse(N.in_quiet("12-14", 14))
        for bad in ("", "abc", "25-3", "8-8", None): self.assertFalse(N.in_quiet(bad, 3), bad)

    def test_rate_limiter(self):
        r = N.RateLimiter(2)
        self.assertTrue(r.allow(0)); self.assertTrue(r.allow(10)); self.assertFalse(r.allow(20))
        self.assertTrue(r.allow(3601), "一小时窗口滑过后恢复")
        self.assertFalse(N.RateLimiter(0).allow(0))

    def test_toast_passes_text_via_env_not_argv(self):
        self.assertIn("$env:WB_TOAST_TITLE", N.PS); self.assertIn("$env:WB_TOAST_BODY", N.PS)

    def test_manifest(self):
        import json
        m = json.loads((Path(__file__).resolve().parents[1] / "plugins" / "desktop-notify" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual((m["id"], m["kind"], m["api"]), ("desktop-notify", "notifier", "workbench-plugin/1"))
        self.assertEqual(set(m["config"]), set(m["config_help"]), "每个配置项都有说明")
        self.assertTrue(set(m["config"]["types"]) <= set(N.LABELS))


class MainLoop(unittest.TestCase):
    """主循环：首次 cursor=now 不补发历史；一批事件合成一条通知；工作台 5 分钟不可达自行退出。假时钟，不弹窗。"""
    def test_loop(self):
        import json, os, tempfile
        state_dir = tempfile.TemporaryDirectory(prefix="notify-legacy-loop-")
        self.addCleanup(state_dir.cleanup)
        from unittest import mock
        clock = [1000.0]; urls = []; toasts = []
        seq = [{"events": [], "cursor": "2026-10-13:1"},
               {"events": [dict(ev("decision.merged", title="A", project="p"), id="a"), dict(ev("decision.dispatched", title="noise"), id="noise")], "cursor": "2026-10-13:2"},
               {"events": [dict(ev("decision.merged", title="B", project="p"), id="b")], "cursor": "2026-10-13:3"}]
        def fake_get(url, timeout=10):
            urls.append(url)
            if seq: return seq.pop(0)
            raise OSError("down")
        env = {"WORKBENCH_URL": "http://127.0.0.1:8765", "WORKBENCH_PLUGIN_DATA": state_dir.name, "WORKBENCH_PLUGIN_CONFIG": json.dumps({"batch_sec": 10, "poll_sec": 5, "max_per_hour": 5})}
        with mock.patch.dict(os.environ, env), mock.patch.object(N, "get", fake_get), mock.patch.object(N, "toast", lambda t, b: toasts.append((t, b)) or True), \
             mock.patch.object(N.time, "time", lambda: clock[0]), mock.patch.object(N.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s)):
            self.assertEqual(N.main([]), 0)
        self.assertIn("cursor=now", urls[0]); self.assertIn("cursor=2026-10-13%3A1", urls[1]); self.assertIn("cursor=2026-10-13%3A2", urls[2])
        self.assertEqual(len(toasts), 1, toasts)
        self.assertEqual(toasts[0][0], "工作台 · 待你拍板 2"); self.assertNotIn("noise", toasts[0][1])
        self.assertGreater(clock[0] - 1000, 300, "持续不可达超过 5 分钟才退出")


class LandedEvent(unittest.TestCase):
    """事件层：post(landed=True) 额外发 decision.landed；没带 landed 或 landed=False 不发"""
    def test_landed_emits(self):
        import sys
        from unittest import mock
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import bus_projects as P
        seen = []
        with mock.patch.object(P, "_post_impl", lambda *a, **k: "ok"), mock.patch.object(P._E, "emit", lambda t, d=None, subject="": seen.append((t, d, subject))):
            P.post("proj", "t-1", "adopted", "标题", landed=True)
            P.post("proj", "t-2", landed=False)
            P.post("proj", "t-3")
        types = [s[0] for s in seen]
        self.assertEqual(types, ["decision.adopted", "decision.landed"], seen)
        self.assertEqual(seen[1][1], {"project": "proj", "task_id": "t-1", "title": "标题"}); self.assertEqual(seen[1][2], "t-1")

    def test_landed_positional(self):
        # fable2 a180：按原函数签名绑定，调用方按位置传 landed 也要发事件（原来只看 kwargs，静默漏发）
        import sys
        from unittest import mock
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import bus_projects as P
        def impl(slug, task_id, status="", title="", summary="", landed=None, **extra): return "ok"
        seen = []
        with mock.patch.object(P, "_post_impl", impl), mock.patch.object(P._E, "emit", lambda t, d=None, subject="": seen.append(t)):
            P.post("proj", "t-9", "adopted", "标题", "摘要", True)
            P.post("proj", "t-8", status="merged", note="x")
        self.assertEqual(seen, ["decision.adopted", "decision.landed", "decision.merged"])

if __name__ == "__main__":
    unittest.main()
