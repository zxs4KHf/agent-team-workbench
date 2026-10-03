"""data-keep 只保护「同一个对象」（2026-10-02 用户：「选了之后，后面的问题全部变成我做过的决策」）。
真实浏览器 + 真实 core.js 的 morph：三张待决表单 A/B/C，回答 A 后列表变成 B/C，第一个位置必须是 B，不能留着 A 的旧表单；
同一张表单原地重绘时，用户打到一半的字照样保留。只起临时静态服务，不碰运行数据。"""
import os
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body><div id="list"></div>
<script type="module">
import { morph } from '/v2/js/core.js';
const form = id => `<form data-keep data-keep-id="dn:${id}" data-item="${id}"><b class="q">问题 ${id}</b><textarea name="text"></textarea></form>`;
window.render = ids => morph(document.getElementById('list'), ids.map(form).join(''));
window.render(['A', 'B', 'C']);
window.ready = true;
</script></body></html>"""


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class KeepIdentity(unittest.TestCase):
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

    def page(self):
        pg = self.browser.new_page()
        pg.route("**/keep.html", lambda r: r.fulfill(status=200, content_type="text/html; charset=utf-8", body=PAGE))
        pg.goto(self.base + "/keep.html")
        pg.wait_for_function("() => window.ready === true", timeout=10000)
        return pg

    def items(self, pg):
        return pg.eval_on_selector_all("#list form", "fs => fs.map(f => [f.dataset.item, f.querySelector('.q').textContent])")

    def test_list_shift_after_answer_shows_next_question(self):
        pg = self.page()
        pg.evaluate("render(['B', 'C'])")                 # A 被回答后移走，B 上移到第一个位置
        self.assertEqual(self.items(pg), [["B", "问题 B"], ["C", "问题 C"]], "第一个位置必须换成 B，不能留着 A 的表单")
        pg.close()

    def test_same_form_keeps_typed_text_on_rerender(self):
        pg = self.page()
        pg.fill("#list form[data-item='B'] textarea", "打到一半的字")
        pg.evaluate("render(['A', 'B', 'C'])")            # 心跳重绘：同一批题目
        self.assertEqual(pg.input_value("#list form[data-item='B'] textarea"), "打到一半的字")
        pg.close()

    def test_pagination_replaces_whole_page(self):
        pg = self.page()
        pg.evaluate("render(['D', 'E', 'F'])")            # 翻到下一页：三个位置都换成别的题
        self.assertEqual([i[0] for i in self.items(pg)], ["D", "E", "F"])
        pg.close()


if __name__ == "__main__":
    unittest.main()
