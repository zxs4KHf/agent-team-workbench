"""提交防重复（2026-10-02 接手积压：重派弹层连点派出两次，da1c 系列；全站表单原来都没防）。
真实浏览器 + 临时目录起 bus_web；把接口响应故意放慢，连续提交，数实际发出的请求。不碰运行数据。"""
import os
import shutil
import sys
import tempfile
import threading
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_core as B      # noqa: E402
import bus_projects as P  # noqa: E402
import bus_web as W       # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, "playwright 未安装")
class SingleFlight(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.tmp = tempfile.mkdtemp(prefix="single-flight-")
        cls._saved = (B.ROOT, B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.LOCK, B.PRIORITY, B.WORKSPACE, P.PROJECTS, P.COMMANDS, P.DONE, P.BRIDGE)
        B.ROOT = B.WORKSPACE = cls.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(cls.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.LOCK = os.path.join(cls.tmp, "console", "controller.lock"); B.PRIORITY = os.path.join(cls.tmp, "console", "priority.json")
        P.PROJECTS, P.COMMANDS, P.DONE = (os.path.join(cls.tmp, *d) for d in (("projects",), ("console", "commands"), ("console", "done")))
        P.BRIDGE = os.path.join(cls.tmp, "console", "bridge.json")
        for d in (B.PEERS, B.INBOX, B.OUTBOX, B.LOG, P.PROJECTS, P.COMMANDS, P.DONE, os.path.join(cls.tmp, "console")):
            os.makedirs(d, exist_ok=True)
        os.makedirs(os.path.join(P.PROJECTS, "p1"))
        B.write_json(os.path.join(P.PROJECTS, "p1", "project.json"), {"slug": "p1", "name": "p1", "peers": ["alpha1"], "mode": "independent", "context_files": []})
        B.write_json(os.path.join(P.PROJECTS, "p1", "decisions.json"), [])
        B.heartbeat("controller", "idle", "controller"); B.heartbeat("alpha1", "idle", "m-a")
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), W.Handler); cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start(); cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close(); cls.pw.stop()
        finally:
            cls.srv.shutdown(); cls.srv.server_close()
            (B.ROOT, B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.LOCK, B.PRIORITY, B.WORKSPACE, P.PROJECTS, P.COMMANDS, P.DONE, P.BRIDGE) = cls._saved
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, path):
        pg = self.browser.new_page(viewport={"width": 1366, "height": 900})
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        pg.goto(f"http://127.0.0.1:{self.port}/?view=overview&p=p1", wait_until="networkidle")
        hits, self.pending = [], []

        def hold(route):
            # 先挂起不回：连按全部发生在第一次请求还没回来的时候（同步接口里 sleep 会卡住事件分发，测不出并发）
            hits.append(route.request.url); self.pending.append(route)
        pg.route(f"**{path}", hold)
        return pg, hits

    def release(self, pg):
        pg.wait_for_timeout(500)
        for r in self.pending:
            r.fulfill(status=500, content_type="application/json", body='{"error":"测试里故意不成功"}')
        self.pending = []
        pg.wait_for_timeout(300)

    def test_double_submit_form_sends_once(self):
        pg, hits = self.open("/api/project")
        pg.evaluate("async () => { const m = await import(document.querySelector('script[src*=\"app.js\"]').src); m.openProjectForm(false); }")
        pg.wait_for_selector("#projectName")
        pg.fill("#projectName", "新项目")
        pg.press("#projectName", "Enter"); pg.press("#projectName", "Enter"); pg.press("#projectName", "Enter")
        self.release(pg)
        self.assertEqual(len(hits), 1, f"连按三次回车只发一次（实际 {len(hits)} 次）")
        pg.close()

    def test_busy_button_ignores_keyboard_activation(self):
        pg, _ = self.open("/api/nothing")
        opened = pg.evaluate("""async () => {
            const mk = busy => { const b = document.createElement('button'); b.type = 'button'; b.dataset.action = 'new-project';
                                 if (busy) b.className = 'busy'; document.body.append(b); return b; };
            const open = () => !!document.getElementById('projectName');
            mk(true).click(); await new Promise(r => setTimeout(r, 300)); const busy = open();
            mk(false).click(); await new Promise(r => setTimeout(r, 300)); return [busy, open()]; }""")
        self.assertEqual(opened, [False, True], "正在处理的按钮被键盘 / 程序点击不执行动作；同样的按钮不在处理中就照常执行（对照）")
        pg.close()

    def test_ctrl_enter_twice_dispatches_once(self):
        pg, hits = self.open("/api/command")
        pg.keyboard.press("n")
        pg.wait_for_selector("#qdQuestion")
        pg.fill("#qdQuestion", "测试问题")
        pg.focus("#qdQuestion")
        pg.keyboard.press("Control+Enter"); pg.keyboard.press("Control+Enter"); pg.keyboard.press("Control+Enter")
        self.release(pg)
        self.assertLessEqual(len(hits), 1, f"Ctrl+Enter 连按最多发一次（实际 {len(hits)} 次）")
        pg.close()


if __name__ == "__main__":
    unittest.main()
