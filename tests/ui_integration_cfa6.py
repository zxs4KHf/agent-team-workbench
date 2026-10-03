# cfa6 真实 DOM 验收（Python Playwright + Chromium）：静态文件来自本 worktree 的 web/，全部 /api 走 page.route mock（无真实派发 / 采纳 / priority 写入）。
# 覆盖：真实 Idiomorph 渲染；过滤后的键盘调序把可见子集合回完整 task_order（不可见项不丢）；鼠标点别处→Tab→Enter 展开行（detail=0）；展开箭头可点；
# 深链 ?d= 打开抽屉、后退关闭；断线→恢复后 connected 回 true。跑法：py -3 -X utf8 tests/ui_integration_cfa6.py
import json, os, sys, threading, time, unittest
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None

NOW = time.strftime("%Y-%m-%dT%H:%M:%S")
def dec(tid, status="dispatched", **kw):
    return {"task_id": tid, "status": status, "title": "任务 " + tid, "peers": ["glm1"], "mode": "independent", "created": NOW, "updated": NOW, "history": [],
            "progress": {"expected": ["glm1"], "answered": [], "states": {"glm1": "waiting"}, "meta": {"created": NOW, "ttl_min": 30, "id": tid, "peers": ["glm1"]}}, **kw}
STATE = {"time": NOW, "connected": True, "peers": [{"peer": "glm1", "model": "glm-5.3", "status": "idle", "online": True, "last_seen": NOW, "label": ""}],
         "controller": {"peer": "controller", "online": True, "status": "idle", "last_seen": NOW}, "bridge_url": "", "lease": {"holder": "controller", "epoch": 3, "expires": NOW},
         "priority": {"rev": 7, "task_order": ["t-a", "t-b", "t-c", "hidden-x"], "agent_weight": {}, "relay_order": [], "relay_order_meta": [], "relay_policy": {}},
         "relay_order_effective": [], "relay_resolution": {"items": [], "pick": ""}, "commands": [], "recent_done": [], "open_tasks": [], "nudges": {}, "builders": {"groups": [], "members": [], "online_n": 0},
         "projects": [{"slug": "p1", "name": "p1", "peers": ["glm1"], "mode": "independent", "context_files": [], "next": [], "notes": [], "threads": [],
                        "decisions": [dec("t-a"), dec("t-b"), dec("t-c"), dec("d-adopted", "adopted", execution="pending", merge_path="log/x.md", bucket="agent", lineage={"topic": "主题甲", "root_task": "d-adopted", "parent_task": "", "round": 1, "kind": "build", "kind_label": "施工", "display_title": "主题甲 · R1 · 施工", "has_lineage": True, "self_root": True, "children": []})]}]}


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass


@unittest.skipIf(sync_playwright is None, "playwright 未安装")
class TestIntegrationUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=os.path.join(REPO, "web")))
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start(); cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.srv.shutdown(); cls.srv.server_close()

    def page(self, url_suffix, state=None):
        pg = self.browser.new_page(viewport={"width": 1366, "height": 900})
        self.posts, self.errs, self.state, self.state_fail = [], [], json.loads(json.dumps(state or STATE)), False
        pg.on("console", lambda m: self.errs.append(m.text) if m.type == "error" else None)
        with open(os.path.join(REPO, "web", "v2", "index.html"), encoding="utf-8") as f:
            index = f.read()
        def route(r):
            u = r.request.url; p = u.split("?")[0]
            if p.endswith("/") or p.endswith("/index.html"): return r.fulfill(status=200, content_type="text/html; charset=utf-8", body=index)
            if "/api/state" in p:
                if self.state_fail: return r.abort()
                return r.fulfill(status=200, content_type="application/json", body=json.dumps(self.state))
            if "/api/priority" in p and r.request.method == "POST":
                body = json.loads(r.request.post_data or "{}"); self.posts.append(body)
                cur = self.state["priority"]; cur.update({k: v for k, v in body.items() if k in ("task_order", "agent_weight", "relay_order")}); cur["rev"] = int(cur["rev"]) + 1
                return r.fulfill(status=200, content_type="application/json", body=json.dumps(cur))
            if "/api/" in p: return r.fulfill(status=404, content_type="application/json", body='{"error":"mock 404"}')
            return r.continue_()
        pg.route("**/*", route)
        pg.goto(f"http://127.0.0.1:{self.port}/{url_suffix}", wait_until="networkidle")
        pg.wait_for_selector("#content .view", timeout=10000)
        return pg

    def test_keyboard_reorder_keeps_hidden_items(self):
        pg = self.page("?view=running&p=p1")
        pg.wait_for_selector(".task[data-key='t-c'] [data-task-drag]", timeout=10000)
        pg.focus(".task[data-key='t-c'] [data-task-drag]")
        pg.keyboard.press("ArrowUp")
        pg.wait_for_function("() => window.__posted || true"); time.sleep(0.8)
        self.assertEqual(len(self.posts), 1, self.posts)
        self.assertEqual(self.posts[0]["task_order"], ["t-a", "t-c", "t-b", "hidden-x"], "可见三项调序，不可见 hidden-x 原位保留")
        self.assertEqual(self.posts[0]["rev"], 7)
        pg.close()

    def test_keyboard_enter_toggles_row_after_mouse_elsewhere(self):
        pg = self.page("?view=running&p=p1")
        pg.wait_for_selector(".task[data-key='t-a'] .task-main", timeout=10000)
        pg.mouse.click(5, 5)  # 鼠标在别处按下：pointerdown 记录的是页面角落
        pg.focus(".task[data-key='t-a'] .task-main")
        pg.keyboard.press("Enter")
        pg.wait_for_function("() => document.querySelector(\".task[data-key='t-a']\").classList.contains('open')", timeout=5000)
        # 展开箭头（有自己的 data-action）点击可收起
        pg.click(".task[data-key='t-a'] button[data-action='toggle-task']")
        pg.wait_for_function("() => !document.querySelector(\".task[data-key='t-a']\").classList.contains('open')", timeout=5000)
        # 点手柄不展开
        pg.click(".task[data-key='t-b'] [data-task-drag]")
        time.sleep(0.3)
        self.assertFalse(pg.eval_on_selector(".task[data-key='t-b']", "e => e.classList.contains('open')"))
        pg.close()

    def test_deep_link_opens_drawer_and_back_closes(self):
        pg = self.page("?view=decisions&p=p1&d=d-adopted")
        pg.wait_for_selector("#drawer.show", timeout=10000)
        self.assertIn("主题甲 · R1 · 施工", pg.inner_text("#drawer"))
        pg.go_back(); time.sleep(0.5)
        self.assertFalse(pg.eval_on_selector("#drawer", "d => d.classList.contains('show')"))
        self.assertNotIn("d=", pg.url)
        pg.close()

    def test_offline_then_recover_sets_connected(self):
        pg = self.page("?view=overview&p=p1")
        self.state_fail = True
        pg.evaluate("() => import('/v2/js/app.js').then(m => m.refresh()).catch(() => 0)"); pg.evaluate("() => import('/v2/js/app.js').then(m => m.refresh()).catch(() => 0)")
        pg.wait_for_function("() => document.querySelector('#service') && /失联/.test(document.querySelector('#service').textContent)", timeout=8000)
        self.state_fail = False; self.state["priority"]["rev"] = 99  # 只有调度表变化 → minorOnly 路径
        pg.evaluate("() => import('/v2/js/app.js').then(m => m.refresh()).catch(() => 0)")
        pg.wait_for_function("() => document.querySelector('#service') && !/失联/.test(document.querySelector('#service').textContent)", timeout=8000)
        pg.close()


if __name__ == "__main__":
    unittest.main(verbosity=1)
