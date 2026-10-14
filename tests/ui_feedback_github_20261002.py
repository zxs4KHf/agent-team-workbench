"""反馈发到 GitHub（2026-10-02，v0.1）：app.json 配了仓库才出现「发到 GitHub」；预填 Issue 的链接只带用户写的话和环境信息，
不带项目名、不带点过的按钮文字（可能是任务标题）。本地记录照存。临时端口、假接口，不碰运行数据。"""
import json
import os
import sys
import tempfile
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_web as W  # noqa: E402

PAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<select id="projectSelect"><option value="secret-proj" selected>机密项目</option></select>
<button type="button" data-action="open-task">打开「收购某公司」任务</button>
<script src="/v2/js/feedback.js"></script></body></html>"""


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class AppInfo(unittest.TestCase):
    def test_app_info_reads_and_validates_repo(self):
        d = tempfile.mkdtemp()
        old = W.B.ROOT
        try:
            W.B.ROOT = d
            self.assertEqual(W.app_info()["feedback"]["github_repo"], "", "没有 app.json 用默认值")
            for repo, want in (("someone/agent-workbench", "someone/agent-workbench"), ("https://evil.example/x", ""), ("a/b/c", "")):
                with open(os.path.join(d, "app.json"), "w", encoding="utf-8") as fh:
                    json.dump({"name": "X", "version": "0.1.0", "feedback": {"github_repo": repo}}, fh)
                self.assertEqual(W.app_info()["feedback"]["github_repo"], want, repo)
            self.assertEqual(W.app_info()["version"], "0.1.0")
        finally:
            W.B.ROOT = old

    def test_repo_ships_app_json(self):
        with open(os.path.join(REPO, "app.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertRegex(data["version"], r"^\d+\.\d+\.\d+$")
        self.assertIn("github_repo", data["feedback"])


class FeedbackGithubUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=os.path.join(REPO, "web")))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.srv.shutdown(); cls.srv.server_close()

    def page(self, repo):
        pg = self.browser.new_page()
        self.posts = []
        pg.route("**/index.html", lambda r: r.fulfill(status=200, content_type="text/html; charset=utf-8", body=PAGE))
        pg.route("**/api/app", lambda r: r.fulfill(status=200, content_type="application/json",
                                                   body=json.dumps({"name": "Agent 协作台", "version": "0.1.0", "feedback": {"github_repo": repo, "labels": "feedback"}})))

        def fb(r):
            self.posts.append(json.loads(r.request.post_data or "{}"))
            r.fulfill(status=200, content_type="application/json", body='{"ok":true}')
        pg.route("**/api/feedback", fb)
        pg.add_init_script("window.__opened = []; window.open = (u) => { window.__opened.push(u); return null; };")
        pg.goto(self.base + "/index.html")
        pg.wait_for_function("() => !!document.querySelector('.wbfb-btn')", timeout=10000)
        pg.wait_for_timeout(300)   # 等 /api/app 回来
        return pg

    def test_github_button_prefills_issue_without_private_context(self):
        pg = self.page("someone/agent-workbench")
        pg.click("[data-action='open-task']")                 # 最后点击的按钮文字是任务标题，不应外发
        pg.click(".wbfb-btn")
        pg.fill("#wbfbText", "派发后找不到任务\n第二行细节")
        pg.click("[data-x='github']")
        pg.wait_for_function("() => window.__opened.length === 1", timeout=5000)
        url = urlparse(pg.evaluate("window.__opened[0]"))
        q = parse_qs(url.query)
        self.assertEqual(url.netloc + url.path, "github.com/someone/agent-workbench/issues/new")
        self.assertEqual(q["title"][0], "[不好用] 派发后找不到任务")
        self.assertEqual(q["labels"][0], "feedback")
        body = q["body"][0]
        self.assertIn("第二行细节", body)
        self.assertIn("版本：0.1.0", body)
        self.assertIn("button[open-task]", body, "只带元素类型和动作名")
        for leak in ("收购某公司", "机密项目", "secret-proj"):
            self.assertNotIn(leak, body)
        pg.wait_for_function("() => true")
        self.assertEqual(len(self.posts), 1, "本机也存一份")
        # 反馈 fb-abd29e3511：发到 GitHub 后下一步动作在新标签页，面板应自动收起（原先一直留着挡路）
        pg.wait_for_function("() => !document.querySelector('.wbfb-panel')", timeout=6000)
        self.assertEqual(pg.locator(".wbfb-panel").count(), 0, "发到 GitHub 后面板应自动关闭")
        pg.close()

    def test_copy_keeps_panel_open(self):
        """对照：复制内容不收起面板，用户还要回本页面粘贴。防止把两种外发行为一刀切。"""
        pg = self.page("someone/agent-workbench")
        pg.click(".wbfb-btn")
        pg.fill("#wbfbText", "复制这条")
        pg.click("[data-x='copy']")
        pg.wait_for_timeout(2500)
        self.assertEqual(pg.locator(".wbfb-panel").count(), 1, "复制后面板应留着")
        pg.close()

    def test_no_repo_no_github_button_but_copy_exists(self):
        pg = self.page("")
        pg.click(".wbfb-btn")
        self.assertEqual(pg.locator("[data-x='github']").count(), 0)
        self.assertEqual(pg.locator("[data-x='copy']").count(), 1)
        pg.close()


if __name__ == "__main__":
    unittest.main()
