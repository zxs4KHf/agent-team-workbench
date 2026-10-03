# 60e8 / R2 返修回归：混合序列硬上限、重要 sticky 不被偷关、凭据脱敏、safeDetail 不当白名单。
# 与 test_notify_5406.py 同样用宿主 Playwright + Chromium + 临时只读 http.server，不碰生产数据与真实派发。
# 跑法：py -3 -X utf8 tests/test_notify_r2_60e8.py
import functools, http.server, os, socketserver, threading, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class Base(unittest.TestCase):
    FIXTURE = "/tests/fixture_notify_5406.html"

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
        self.ctx = self.browser.new_context()
        self.page = self.ctx.new_page()
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.goto(f"http://127.0.0.1:{self.port}{self.FIXTURE}")
        self.page.wait_for_function("window.ready === true")

    def tearDown(self):
        self.assertEqual([], self.errors, "页面 JS 报错")
        self.ctx.close()

    def js(self, code):
        return self.page.evaluate("(() => { " + code + "; return 0; })()")

    def shown(self):
        return self.page.evaluate(
            "[...document.querySelectorAll('#toasts .toast:not(.leaving)')]"
            ".map(n => ({ text: n.querySelector('.msg').textContent, cls: n.className, role: n.getAttribute('role') }))")

    def texts(self):
        return [t["text"] for t in self.shown()]

    def inbox(self):
        return self.page.evaluate("T.notifyInbox().map(n => ({ message: n.message, level: n.level || '', overflow: !!n.overflow }))")


class TestHardCap(Base):
    """R2-1：上限 3 对每种 level 都是硬的；重要 sticky 不得被定时偷关；溢出不丢。"""

    def test_three_sticky_then_normal_stays_at_three(self):
        self.js("T.toast('S1','bad'); T.toast('S2','bad'); T.toast('S3','bad')")
        self.assertEqual(['S1', 'S2', 'S3'], self.texts())
        self.js("T.toast('N1','ok')")
        self.assertEqual(3, len(self.shown()), "三条 sticky 之后 normal 必须不上屏，屏上仍为 3")
        self.assertEqual(['S1', 'S2', 'S3'], self.texts(), "normal 不得挤掉任何 sticky")
        self.assertEqual([{'message': 'N1', 'level': 'normal', 'overflow': True}], self.inbox())

    def test_three_sticky_then_transient_also_capped(self):
        self.js("T.toast('S1','bad'); T.toast('S2','bad'); T.toast('S3','bad'); T.notify('copied')")
        self.assertEqual(3, len(self.shown()))
        self.assertEqual(['S1', 'S2', 'S3'], self.texts())
        self.assertEqual(['已复制'], [n['message'] for n in self.inbox()])

    def test_sticky_never_auto_closed_by_timer(self):
        self.js("T.toast('S1','bad'); T.toast('S2','bad'); T.toast('S3','bad')")
        self.js("for (let i = 0; i < 6; i++) T.toast('N' + i, 'ok', { ms: 100 })")
        self.page.wait_for_timeout(1200)
        self.assertEqual(['S1', 'S2', 'S3'], self.texts(), "重要 sticky 在大量 normal 冲击与计时之后仍必须在屏上")
        self.assertEqual(6, len(self.inbox()), "6 条 normal 全部入收件箱，一条都不能静默丢")

    def test_mixed_normal_then_sticky_evicts_normal_only(self):
        self.js("T.toast('N1','ok',{ms:9000}); T.toast('N2','ok',{ms:9000}); T.toast('N3','ok',{ms:9000})")
        self.assertEqual(['N1', 'N2', 'N3'], self.texts())
        self.js("T.toast('S1','bad')")
        self.assertEqual(['N2', 'N3', 'S1'], self.texts(), "sticky 进来应挤掉最旧的 normal")
        self.assertEqual([], self.inbox(), "被挤掉的 normal 是被 close 而非溢出，不入收件箱")
        self.js("T.toast('S2','bad'); T.toast('S3','bad')")
        self.assertEqual(['S1', 'S2', 'S3'], self.texts())
        self.js("T.toast('S4','bad')")
        self.assertEqual(['S1', 'S2', 'S3'], self.texts(), "满屏 sticky 时第四条 sticky 不得顶替任何一条")
        self.assertEqual([{'message': 'S4', 'level': 'sticky', 'overflow': True}], self.inbox())

    def test_alternating_sequence(self):
        self.js("T.toast('N1','ok',{ms:9000}); T.toast('S1','bad'); T.toast('N2','ok',{ms:9000}); T.toast('S2','bad'); T.toast('N3','ok',{ms:9000}); T.toast('S3','bad')")
        t = self.texts()
        self.assertEqual(3, len(t))
        self.assertEqual(['S1', 'S2', 'S3'], t, "交替序列最终屏上应只剩三条 sticky")
        # N1/N2/N3 依次被后来的 sticky 挤掉（close，不是溢出）：屏上始终 3 条，且从头到尾没有任何 sticky 被顶替
        self.assertEqual([], self.inbox(), "被 sticky 挤掉的 normal 走 close 路径，不进收件箱")
        self.js("T.toast('S4','bad')")
        self.assertEqual(['S1', 'S2', 'S3'], self.texts(), "满屏 sticky 后第四条才进收件箱")
        self.assertEqual(['S4'], [n['message'] for n in self.inbox()])

    def test_overflow_entry_is_announced(self):
        self.js("T.toast('S1','bad'); T.toast('S2','bad'); T.toast('S3','bad'); T.toast('S4','bad')")
        e = self.page.locator('#toasts [data-inbox-entry]')
        self.assertEqual('status', e.get_attribute('role'))
        self.assertEqual('polite', e.get_attribute('aria-live'))
        self.assertIn('1 条重要', e.get_attribute('aria-label'))
        self.assertEqual('未读通知 1 条（1 条重要）', e.text_content())

    def test_overflow_is_recoverable_from_inbox(self):
        self.js("T.toast('S1','bad'); T.toast('S2','bad'); T.toast('S3','bad'); T.toast('要紧事','bad',{detail:'d'})")
        self.page.locator('#toasts [data-inbox-entry]').click()
        body = self.page.locator('#drawerBody').text_content()
        self.assertIn('要紧事', body, "被挤下屏的重要消息必须能从收件箱读回")
        self.assertIn('d', body)

    def test_key_dedup_does_not_consume_cap(self):
        self.js("T.toast('S1','bad'); T.toast('S2','bad'); T.toast('S3','bad',{key:'k'})")
        self.js("for (let i = 0; i < 4; i++) T.toast('S3','bad',{key:'k'})")
        self.assertEqual(3, len(self.shown()), "同 key 合并不应触发溢出")
        self.assertEqual([], self.inbox())


class TestRedact(Base):
    """R2-3：默认隐藏 query 值、剥 userinfo/hash、路径段凭据、message 不当白名单。"""

    def red(self, p):
        return self.page.evaluate("p => T.redactPath(p)", p)

    def test_query_values_hidden_by_default(self):
        # 不靠猜键名：任意键的值都打码
        self.assertEqual('/api/x?whatever=***', self.red('/api/x?whatever=SECRET'))
        self.assertEqual('/api/x?a=***&b=***', self.red('/api/x?a=1&b=2'))

    def test_known_credential_keys_all_covered(self):
        for k in ('access_token', 'refresh_token', 'bearer', 'session', 'pwd', 'sid', 'api_key', 'authorization'):
            out = self.red(f'/api/x?{k}=SUPERSECRET123')
            self.assertNotIn('SUPERSECRET123', out, k)
            self.assertIn(f'{k}=***', out, k)

    def test_userinfo_and_hash_stripped(self):
        out = self.red('https://user:pw@host.example/api/x?token=T#frag')
        self.assertNotIn('user', out); self.assertNotIn('pw@', out); self.assertNotIn('frag', out); self.assertNotIn('T', out.split('=')[-1].replace('***', ''))
        self.assertTrue(out.startswith('https://host.example/api/x'), out)

    def test_credential_looking_path_segment(self):
        out = self.red('/mcp/W28JpcsdKybTE6jqKMmU8Cod0iy5PKsx2sz4gCalL')
        self.assertNotIn('W28Jpcsd', out)
        self.assertEqual('/mcp/***', out)
        self.assertEqual('/api/state', self.red('/api/state'), '正常路径段不得被误伤')
        self.assertEqual('/api/task-pack', self.red('/api/task-pack'))

    def test_hex_path_segment(self):
        self.assertEqual('/x/***', self.red('/x/deadbeefdeadbeefdeadbeef'))

    def test_safe_detail_scrubs_message(self):
        info = self.page.evaluate(
            "(() => { const e = new T.ApiError('failed with Authorization: Bearer TOPSECRET999 at https://u:p@h/mcp/QQQQQQQQQQQQQQQQ1', "
            "{ kind: 'server', status: 500, path: '/api/x?api_key=KEY999', body: { pw: 'BODYSECRET' } }); return e.safeDetail(); })()")
        blob = str(info)
        for s in ('TOPSECRET999', 'KEY999', 'BODYSECRET', 'QQQQQQQQQQQQQQQQ1'):
            self.assertNotIn(s, blob, f'safeDetail 泄漏了 {s}')
        self.assertNotIn('body', info, 'safeDetail 不得包含 body / 原始响应')
        self.assertEqual({'kind', 'status', 'path', 'message', 'time'}, set(info.keys()), '只暴露有限结构字段')

    def test_nothing_secret_reaches_dom_or_storage(self):
        self.page.route('**/api/s500*', lambda r: r.fulfill(
            status=500, content_type='application/json',
            body='{"error":"boom at https://u:pw@h/mcp/ZZZZZZZZZZZZZZZZ9","authorization":"Bearer TOPSECRET"}'))
        self.js("T.api('/api/s500?access_token=AAA111BBB222&refresh_token=RRR333', { method:'POST', body: '{\"pwd\":\"BODYSECRET\"}' })"
                ".catch(e => T.notify.error(e, { retry: () => Promise.resolve() }))")
        self.page.wait_for_timeout(200)
        dump = self.page.evaluate(
            "JSON.stringify(Object.entries(localStorage)) + JSON.stringify(Object.entries(sessionStorage)) "
            "+ document.querySelector('#toasts').innerHTML "
            "+ [...document.querySelectorAll('[data-key]')].map(n => n.dataset.key).join('|')")
        for s in ('AAA111BBB222', 'RRR333', 'BODYSECRET', 'TOPSECRET', 'ZZZZZZZZZZZZZZZZ9', 'pw@'):
            self.assertNotIn(s, dump, f'{s} 出现在 DOM / data-key / 存储里')
        self.page.locator('#toasts .toast details summary').click()
        detail = self.page.locator('#toasts .toast details pre').text_content()
        self.assertIn('access_token=***', detail)
        self.assertNotIn('AAA111BBB222', detail)
        # 屏上可见正文也必须脱敏：服务端错误文案里回带的 URL 与凭据不能原样显示
        msg = self.page.locator('#toasts .toast .msg').text_content()
        self.assertNotIn('ZZZZZZZZZZZZZZZZ9', msg); self.assertNotIn('pw@', msg)
        self.assertIn('/mcp/***', msg)

    def test_toast_dedup_key_carries_no_secret(self):
        self.page.route('**/api/k*', lambda r: r.fulfill(status=500, content_type='application/json', body='{"error":"x"}'))
        self.js("T.api('/api/k?token=KEYSECRET42').catch(e => T.notify.error(e))")
        self.page.wait_for_timeout(200)
        key = self.page.evaluate("document.querySelector('#toasts .toast')?.dataset.key || ''")
        self.assertNotIn('KEYSECRET42', key, '去重 key 会进 DOM 属性，不能含凭据')
        self.assertIn('***', key)


if __name__ == '__main__':
    unittest.main(verbosity=2)
