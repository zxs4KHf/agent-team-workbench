# 60e8 / R2：真实 index 全页集成。加载 web/v2/index.html 本体（而不是自造的 toast fixture），
# 用 Playwright 路由把 /api/* 全部模拟成本地假数据，验证整页启动后通知链路在真实模块图下成立。
# 不碰生产数据、不发真实派发、不触真实凭据；临时只读 http.server + 一次性浏览器上下文。
# 跑法：py -3 -X utf8 tests/test_notify_page_60e8.py
import functools, http.server, json, os, socketserver, threading, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STATE = {
    "time": "2026-09-24T02:30:00", "peers": [], "projects": [], "commands": [], "recent_done": [],
    "controller": {"peer": "controller", "online": True, "status": "idle", "last_seen": "2026-09-24T02:30:00"},
    "bridge_url": "", "lease": {}, "priority": {"version": 2, "relay_order": [], "relay_order_meta": [], "rev": 1,
                                                "relay_policy": {"strategy": "recent"}, "task_order": [], "agent_weight": {}},
    "relay_order_effective": [], "relay_resolution": {"items": [], "pick": ""}, "open_tasks": [], "nudges": {},
    "builders": {"groups": [], "members": [], "online_n": 0},
}


class Quiet(http.server.SimpleHTTPRequestHandler):
    """真实 index.html 用绝对路径 /v2/js/... 引模块，这里把 /v2/ 映射到 web/v2/，
    与生产 bus_web 的路由一致；否则加载的就不是真实模块图。"""

    def translate_path(self, path):
        p = path.split("?", 1)[0].split("#", 1)[0]
        if p.startswith("/v2/"):
            path = "/web" + p
        return super().translate_path(path)

    def log_message(self, *a):
        pass


class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        handler = functools.partial(Quiet, directory=ROOT)
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.httpd.shutdown(); cls.httpd.server_close()

    def setUp(self):
        self.ctx = self.browser.new_context()
        self.page = self.ctx.new_page()
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.calls = []
        # 全 API 模拟：真实页面会在启动时打 /api/state、/api/events(SSE)、可能还有 arena
        self.page.route("**/api/events", lambda r: r.fulfill(status=200, content_type="text/event-stream", body=""))
        self.page.route("**/api/state*", self._state)
        self.page.route("**/api/settings*", lambda r: r.fulfill(status=200, content_type="application/json", body="{}"))
        self.page.route("**/api/arena/**", lambda r: r.fulfill(status=200, content_type="application/json", body="{}"))
        with self.page.expect_response(lambda response: "/api/state" in response.url, timeout=15000):
            self.page.goto(f"http://127.0.0.1:{self.port}/v2/index.html")
        self.page.wait_for_selector("#toasts", state="attached")
        self.page.wait_for_timeout(600)

    def _state(self, route):
        self.calls.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(STATE))

    def tearDown(self):
        fatal = [e for e in self.errors if "ResizeObserver" not in e]
        self.assertEqual([], fatal, "整页启动有 JS 报错")
        self.ctx.close()

    def shown(self):
        return self.page.evaluate("[...document.querySelectorAll('#toasts .toast:not(.leaving)')].map(n => n.querySelector('.msg')?.textContent || '')")

    def test_page_boots_with_real_module_graph(self):
        # 真实 index 把 app.js 当 module 入口拉起整张图（core / components / notify / model / views / views2 / arena）
        self.assertTrue(self.page.evaluate("!!document.querySelector('#content')"))
        self.assertTrue(self.calls, "页面启动应真的请求过 /api/state")
        self.assertEqual("", self.page.evaluate("document.querySelector('#toasts').getAttribute('aria-live') || ''"),
                         "8c2b：容器不再整体 aria-live，改为逐条 role")

    def test_refresh_action_uses_transient_notify(self):
        # 点真实刷新按钮，走的是 ACTIONS.refresh → notify('refreshed')（transient，读屏静默、1.2s 自动消失）
        self.page.locator('[data-action="refresh"]').first.click()
        self.page.wait_for_timeout(250)
        t = self.page.evaluate("[...document.querySelectorAll('#toasts .toast')].map(n => ({ msg: n.querySelector('.msg')?.textContent, cls: n.className, role: n.getAttribute('role'), hidden: n.getAttribute('aria-hidden') }))")
        self.assertEqual(1, len(t), t)
        self.assertEqual("已刷新", t[0]["msg"])
        self.assertIn("lv-transient", t[0]["cls"], "已刷新属噪音类，必须是 transient 而不是 normal")
        self.assertEqual("presentation", t[0]["role"]); self.assertEqual("true", t[0]["hidden"])
        self.page.wait_for_timeout(1400)
        self.assertEqual([], self.shown(), "transient 应在 1.2s 后自行消失")

    def test_real_api_500_on_page_produces_sticky_scrubbed(self):
        # 在真实页面的模块实例上制造一次 500：验证 ApiError → notify.error 的链路在整页环境下成立
        self.page.route("**/api/boom*", lambda r: r.fulfill(
            status=500, content_type="application/json",
            body='{"error":"upstream https://u:pw@h/mcp/YYYYYYYYYYYYYYYY7 failed"}'))
        self.page.evaluate("""(async () => {
          const core = await import('/v2/js/core.js');
          const n = await import('/v2/js/notify.js');
          try { await core.api('/api/boom?access_token=PAGESECRET1'); } catch (e) { n.notify.error(e); }
        })()""")
        self.page.wait_for_timeout(400)
        t = self.page.evaluate("[...document.querySelectorAll('#toasts .toast')].map(n => ({ msg: n.querySelector('.msg')?.textContent, cls: n.className, role: n.getAttribute('role'), key: n.dataset.key || '', buttons: [...n.querySelectorAll('button')].map(b => b.textContent.trim()) }))")
        self.assertEqual(1, len(t), t)
        self.assertIn("lv-sticky", t[0]["cls"], "5xx 必须是真 sticky，由用户手动关")
        self.assertEqual("alert", t[0]["role"])
        self.assertEqual(1, self.page.locator('#toasts .toast [aria-label="关闭"]').count(), "sticky 必须带手动关闭按钮")
        self.assertNotIn("重试", t[0]["buttons"], "未传 retry 时不得出现重试按钮")
        for s in ("PAGESECRET1", "YYYYYYYYYYYYYYYY7", "pw@"):
            self.assertNotIn(s, t[0]["msg"], f"{s} 出现在整页可见正文里")
            self.assertNotIn(s, t[0]["key"], f"{s} 出现在整页 data-key 里")
        # sticky 不会自己消失
        self.page.wait_for_timeout(4200)
        self.assertEqual(1, len(self.shown()), "sticky 不得被定时关掉")
        self.page.locator('#toasts .toast [aria-label="关闭"]').click()
        self.page.wait_for_timeout(300)
        self.assertEqual([], self.shown())

    def test_page_hard_cap_with_mixed_levels(self):
        # 整页环境下复核硬上限（fixture 里已测，这里确认真实模块实例行为一致）
        self.page.evaluate("""(async () => {
          const c = await import('/v2/js/components.js');
          c.toast('S1','bad'); c.toast('S2','bad'); c.toast('S3','bad'); c.toast('N1','ok'); c.toast('N2','ok');
          window.__inbox = c.notifyInbox;
        })()""")
        self.page.wait_for_timeout(300)
        self.assertEqual(["S1", "S2", "S3"], self.shown())
        self.assertEqual(["N2", "N1"], self.page.evaluate("window.__inbox().map(n => n.message)"))
        entry = self.page.locator('#toasts [data-inbox-entry]')
        self.assertEqual("status", entry.get_attribute("role"))

    def test_no_secret_in_page_storage_after_errors(self):
        self.page.route("**/api/z*", lambda r: r.fulfill(status=500, content_type="application/json", body='{"error":"z"}'))
        self.page.evaluate("""(async () => {
          const core = await import('/v2/js/core.js');
          const n = await import('/v2/js/notify.js');
          try { await core.api('/api/z?refresh_token=STORESECRET9&sid=SIDSECRET8'); } catch (e) { n.notify.error(e); }
        })()""")
        self.page.wait_for_timeout(300)
        dump = self.page.evaluate("JSON.stringify(Object.entries(localStorage)) + JSON.stringify(Object.entries(sessionStorage)) + document.body.innerHTML")
        for s in ("STORESECRET9", "SIDSECRET8"):
            self.assertNotIn(s, dump, f"{s} 落进了整页 DOM 或存储")


if __name__ == "__main__":
    unittest.main(verbosity=2)
