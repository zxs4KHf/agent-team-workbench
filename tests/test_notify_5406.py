# 5406 / 8c2b：五级 toast + notify 门面 + ApiError 分类的真实 DOM / 真实计时回归。
# 用宿主 Python Playwright + Chromium，起一个只读的临时 http.server 服务本 worktree（不碰真实接口、不改用户浏览器偏好，浏览器上下文全新且用后即弃）。
# 跑法：py -3 -X utf8 tests/test_notify_5406.py
import http.server, os, socketserver, threading, unittest, functools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class NotifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        handler = functools.partial(Quiet, directory=ROOT)
        socketserver.ThreadingTCPServer.daemon_threads = True
        cls.httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.httpd.shutdown(); cls.httpd.server_close()

    def setUp(self):
        self.ctx = self.browser.new_context(permissions=["clipboard-read", "clipboard-write"])
        self.page = self.ctx.new_page()
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.goto(f"http://127.0.0.1:{self.port}/tests/fixture_notify_5406.html")
        self.page.wait_for_function("window.ready === true")

    def tearDown(self):
        self.assertEqual([], self.errors, "页面 JS 报错")
        self.ctx.close()

    def js(self, code):
        # 注意：Playwright 对字符串表达式的求值结果若是函数会直接调用它；toast() 返回 close 函数，裸调用会被误关。统一包进 IIFE 并返回 0。
        return self.page.evaluate("(() => { " + code + "; return 0; })()")

    def toasts(self):
        return self.page.evaluate("[...document.querySelectorAll('#toasts .toast:not(.leaving)')].map(n => ({ text: n.querySelector('.msg').textContent, count: n.querySelector('.count').textContent, role: n.getAttribute('role'), hidden: n.getAttribute('aria-hidden'), cls: n.className, key: n.dataset.key || '', buttons: [...n.querySelectorAll('button')].map(b => b.textContent.trim()) }))")

    def test_copy_x5_merges_into_one_transient(self):
        self.js("for (let i = 0; i < 5; i++) T.notify('copied')")
        t = self.toasts()
        self.assertEqual(1, len(t)); self.assertEqual('已复制', t[0]['text']); self.assertEqual(' ×5', t[0]['count'])
        self.assertEqual('presentation', t[0]['role']); self.assertEqual('true', t[0]['hidden'])   # 读屏静默
        self.page.wait_for_timeout(1500)                                                            # 1200ms 后自动消失
        self.assertEqual(0, len(self.toasts()))

    def test_hover_pauses_and_leave_resumes_with_remaining(self):
        self.js("T.toast('悬停测试', 'ok', { ms: 900, key: 'h' })")
        self.page.wait_for_timeout(300)
        self.page.hover('#toasts .toast')
        self.page.wait_for_timeout(1200)                                                            # 早已超过 900ms，仍在
        self.assertEqual(1, len(self.toasts()), 'hover 期间不应消失')
        self.page.mouse.move(600, 600)                                                              # 离开
        self.page.wait_for_timeout(250)
        self.assertEqual(1, len(self.toasts()), '离开后应按剩余 ~600ms 续跑，而不是立刻消失')
        self.page.wait_for_timeout(700)
        self.assertEqual(0, len(self.toasts()), '剩余时间走完应消失')

    def test_stack_limit_three_and_sticky_kept(self):
        self.js("for (let i = 1; i <= 4; i++) T.toast('普通' + i, 'ok', { ms: 9000 }); T.toast('警告', 'bad')")
        t = self.toasts()
        self.assertEqual(3, len(t)); self.assertIn('警告', [x['text'] for x in t]); self.assertNotIn('普通1', [x['text'] for x in t])
        self.assertEqual('alert', [x for x in t if x['text'] == '警告'][0]['role'])
        self.assertEqual('status', [x for x in t if x['text'] == '普通4'][0]['role'])
        # 屏上已有 sticky「警告」；S1/S2 先挤掉两条 normal，之后三条全是 sticky：S3/S4 不上屏、不丢，进收件箱并出现可访问入口
        self.js("T.toast('S1', 'bad'); T.toast('S2', 'bad'); T.toast('S3', 'bad'); T.toast('S4', 'bad')")
        texts = [x['text'] for x in self.toasts()]
        self.assertEqual(['警告', 'S1', 'S2'], texts)
        self.assertEqual(['S4', 'S3'], self.page.evaluate("T.notifyInbox().map(n => n.message)"))
        entry = self.page.locator('#toasts [data-inbox-entry]')
        # R2-1：溢出的是两条 sticky（重要），入口文案与 aria-label 都要标出「重要」，否则读屏用户无从得知有重要消息被挤下屏
        self.assertEqual('未读通知 2 条（2 条重要）', entry.text_content())
        self.assertIn('收件箱', entry.get_attribute('aria-label')); self.assertIn('2 条重要', entry.get_attribute('aria-label'))
        self.assertEqual('status', entry.get_attribute('role')); self.assertEqual('polite', entry.get_attribute('aria-live'))

    def test_400_keeps_server_reason_and_no_retry(self):
        self.page.route('**/api/x400*', lambda r: r.fulfill(status=400, content_type='application/json', body='{"error":"业务原因：名字重复"}'))
        info = self.page.evaluate("T.api('/api/x400?token=SECRET123').catch(e => ({ kind: e.kind, status: e.status, message: e.message, name: e.name, safe: e.safeDetail() }))")
        self.assertEqual('client', info['kind']); self.assertEqual(400, info['status']); self.assertEqual('业务原因：名字重复', info['message']); self.assertEqual('ApiError', info['name'])
        self.assertNotIn('SECRET123', info['safe']['path']); self.assertIn('token=***', info['safe']['path'])
        self.js("T.api('/api/x400?token=SECRET123').catch(e => T.notify.error(e, { retry: () => T.api('/api/x400') }))")
        self.page.wait_for_timeout(100)
        t = self.toasts(); self.assertEqual(1, len(t)); self.assertEqual('业务原因：名字重复', t[0]['text']); self.assertNotIn('重试', t[0]['buttons']); self.assertIn('复制详情', t[0]['buttons'])
        self.page.locator('#toasts .toast details summary').click()
        detail = self.page.locator('#toasts .toast details pre').text_content()
        self.assertNotIn('SECRET123', detail); self.assertIn('"kind": "client"', detail)

    def test_network_failure_retry_once_only_when_provided(self):
        calls = {'n': 0}
        def handle(route):
            calls['n'] += 1; route.abort('connectionrefused')
        self.page.route('**/api/net', handle)
        self.js("window.retried = 0; T.api('/api/net').catch(e => T.notify.error(e, { retry: () => { window.retried++; return Promise.resolve(); } }))")
        self.page.wait_for_timeout(200)
        t = self.toasts(); self.assertEqual(1, len(t)); self.assertIn('网络不可达', t[0]['text']); self.assertIn('重试', t[0]['buttons']); self.assertEqual('alert', t[0]['role'])
        self.page.wait_for_timeout(1500)
        self.assertEqual(1, calls['n'], '不得自动重发'); self.assertEqual(0, self.page.evaluate('window.retried'))
        self.page.locator('#toasts .toast .act').click()
        self.page.wait_for_timeout(300)
        self.assertEqual(1, self.page.evaluate('window.retried'), '点一次重试只调用一次'); self.assertEqual(0, len(self.toasts()))
        # 不传 retry：没有重试按钮
        self.js("T.api('/api/net').catch(e => T.notify.error(e))")
        self.page.wait_for_timeout(200)
        t = self.toasts(); self.assertEqual(1, len(t)); self.assertNotIn('重试', t[0]['buttons'])
        self.assertEqual(2, calls['n'])

    def test_center_level_goes_to_inbox_and_opens(self):
        self.js("T.toast('后台消息', '', { level: 'center', detail: 'd1' })")
        self.assertEqual(0, len(self.toasts()))
        self.page.locator('#toasts [data-inbox-entry]').click()
        self.assertTrue(self.page.evaluate("document.querySelector('#drawer').classList.contains('show')"))
        self.assertIn('后台消息', self.page.locator('#drawerBody').text_content()); self.assertIn('d1', self.page.locator('#drawerBody').text_content())
        self.assertEqual('notif', self.page.evaluate("document.querySelector('#drawer').dataset.kind"))

    def test_close_x_idempotent_and_no_secret_in_storage(self):
        self.js("window.exp = 0; window.c = T.toast('可关', 'bad', { onExpire: () => window.exp++ })")
        self.page.locator('#toasts .toast [aria-label="关闭"]').click()
        self.js("window.c(); window.c()")
        self.page.wait_for_timeout(300)
        self.assertEqual(0, self.page.evaluate("document.querySelectorAll('#toasts .toast').length")); self.assertEqual(0, self.page.evaluate('window.exp'))
        self.page.route('**/api/s500*', lambda r: r.fulfill(status=500, content_type='application/json', body='{"error":"boom","authorization":"Bearer TOPSECRET"}'))
        self.js("T.api('/api/s500?api_key=KEY999', { method: 'POST', headers: { Authorization: 'Bearer TOPSECRET' }, body: '{\"pw\":\"BODYSECRET\"}' }).catch(e => T.notify.error(e, { retry: () => Promise.resolve() }))")
        self.page.wait_for_timeout(200)
        t = self.toasts(); self.assertEqual(1, len(t)); self.assertIn('服务端错误 500', t[0]['text']); self.assertIn('重试', t[0]['buttons'])
        dump = self.page.evaluate("JSON.stringify(Object.entries(localStorage)) + document.querySelector('#toasts').innerHTML")
        for s in ('TOPSECRET', 'BODYSECRET', 'KEY999'):
            self.assertNotIn(s, dump)
        self.assertIn('api_key=***', self.page.locator('#toasts .toast details pre').text_content())


if __name__ == '__main__':
    unittest.main(verbosity=2)
