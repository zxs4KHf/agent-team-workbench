# 任务 20260929-153838-201a 的界面体检：真实 Chromium 里跑「任务历史」视图（201a 返工点 8/9/12）。
# 后端不是 mock：临时目录里造 projects/decisions，由 bus_history.history_page 现算响应，
# 经 page.route 交给真实前端模块（app.js / history.js / model.js）。只读，不派发、不采纳、不碰生产数据。
# 1) 视图在真实浏览器渲染、分页口径与「未到底不写完整历史」的文案；hub#inbox（外部来源收件箱）同浏览器照常、不被抢占；
# 2) 键盘：条目本身可聚焦 → ArrowDown/ArrowUp 移动 → Enter 开决策抽屉 → Esc 关并回到原条目；
# 3) 下一页走 pushState（网址带 hpage），浏览器后退回到第 1 页同一批内容；
# 4) 冻结页：重新取本页后有新增只出提示条、列表不自动漂，点「应用」才换；被移除的条目带着标题留在列表里。
# 跑法：py -3 -X utf8 tests/ui_history_201a.py
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None


def iso(sec_ago=0):
    return (datetime.now() - timedelta(seconds=sec_ago)).strftime("%Y-%m-%dT%H:%M:%S")


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@unittest.skipIf(sync_playwright is None, "playwright 未安装")
class TestHistoryUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # —— 真后端：临时 workspace + 真 bus_history，不读生产 projects/ ——
        cls.tmp = tempfile.mkdtemp(prefix="bus201a-ui-")
        import bus_core as B
        import bus_projects as P
        import bus_history as H
        cls.B, cls.P, cls.H = B, P, H
        cls.old = (B.ROOT, B.WORKSPACE, P.PROJECTS, P.DONE)
        B.ROOT = cls.tmp
        B.WORKSPACE = cls.tmp
        P.PROJECTS = os.path.join(cls.tmp, "projects")
        P.DONE = os.path.join(cls.tmp, "console", "done")
        os.makedirs(P.PROJECTS, exist_ok=True)
        os.makedirs(P.DONE, exist_ok=True)
        H.reset_cache()

        dec = []
        for i in range(1, 26):                     # p1 有 25 条终态决策 → 每页 20 条 = 2 页
            tid = "d-%02d" % i
            dec.append({"task_id": tid, "status": "merged" if i == 1 else "adopted",
                        "title": "历史决策 %02d" % i, "summary": "",
                        "created": iso(3600 - i), "updated": iso(600 - i),
                        "merge_path": "log/%s.md" % tid})
        cls.mk_project("p1", dec)

        # —— /api/state：同一批决策也在项目数据里，抽屉才找得到（openDecision 读 state）——
        cls.state = {
            "time": iso(), "connected": True,
            "peers": [{"peer": "qwenflash2", "model": "m", "status": "idle", "online": True, "last_seen": iso(), "label": ""}],
            "controller": {"peer": "controller", "online": True, "status": "idle", "last_seen": iso(), "label": "小队长"},
            "bridge_url": "", "lease": {"holder": "controller", "epoch": 1, "expires": iso()},
            "priority": {"rev": 1, "task_order": [], "agent_weight": {}, "relay_order": [], "relay_order_meta": [], "relay_policy": {}},
            "relay_order_effective": [], "relay_resolution": {"items": [], "pick": ""},
            "commands": [], "recent_done": [], "open_tasks": [], "nudges": {}, "features": {}, "pending_labels": {},
            "terminal_statuses": list(P.TERMINAL_STATUSES),
            "projects": [{"slug": "p1", "name": "p1", "peers": ["qwenflash2"], "mode": "independent",
                          "context_files": [], "next": [], "notes": [], "threads": [], "decisions": dec}]}

        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=os.path.join(REPO, "web")))
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        with open(os.path.join(REPO, "web", "v2", "index.html"), encoding="utf-8") as f:
            cls.index = f.read()
        with open(os.path.join(REPO, "web", "v2", "hub.html"), encoding="utf-8") as f:
            cls.hub = f.read()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.B.ROOT, cls.B.WORKSPACE, cls.P.PROJECTS, cls.P.DONE = cls.old
        cls.H.reset_cache()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def mk_project(cls, slug, decisions):
        d = os.path.join(cls.P.PROJECTS, slug)
        os.makedirs(d, exist_ok=True)
        cls.B.write_json(os.path.join(d, "project.json"), {"slug": slug, "name": slug, "peers": [], "decisions": decisions})
        cls.B.write_json(os.path.join(d, "decisions.json"), decisions)

    def set_records(self, add=None, drop=None):
        """改的是临时目录里的 decisions.json，响应由真后端重算；bust=True 让快照重建（等价服务重启）。"""
        dec = list(self.state["projects"][0]["decisions"])
        if add:
            dec = [add] + [d for d in dec if d["task_id"] != add["task_id"]]
        if drop:
            dec = [d for d in dec if d["task_id"] != drop]
        self.state["projects"][0]["decisions"] = dec
        self.mk_project("p1", dec)
        self.H.reset_cache()

    def page(self, suffix, wait_sel="#content .view"):
        pg = self.browser.new_page(viewport={"width": 1366, "height": 900})
        self.hist_calls, self.errs = [], []
        outer = self

        def route(r):
            url = r.request.url
            path = url.split("?")[0]
            q = parse_qs(urlparse(url).query)
            if path.endswith("/index.html") or path == "http://127.0.0.1:%d/" % self.port:
                return r.fulfill(status=200, content_type="text/html; charset=utf-8", body=self.index)
            if path.endswith("/hub.html"):
                return r.fulfill(status=200, content_type="text/html; charset=utf-8", body=self.hub)
            if path.endswith("/api/state"):
                return r.fulfill(status=200, content_type="application/json", body=json.dumps(self.state))
            if path.endswith("/api/inbox/history"):
                outer.hist_calls.append(urlparse(url).query)
                try:
                    out = self.H.history_page(q.get("project", [""])[0], q.get("filter", ["all"])[0],
                                              q.get("cursor", [""])[0], q.get("limit", ["20"])[0])
                except self.H.CursorError as e:
                    return r.fulfill(status=e.status, content_type="application/json",
                                     body=json.dumps({"error": e.code, "message": str(e)}, ensure_ascii=False))
                return r.fulfill(status=200, content_type="application/json", body=json.dumps(out, ensure_ascii=False))
            if path.endswith("/api/intake"):          # hub#inbox 的数据源，与任务历史互不相干
                return r.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"items": [{"id": "in-1", "title": "外部来源的一条待归集", "source": "demo",
                                                  "status": "pending", "created": iso()}]}))
            if r.request.method == "POST" and "/api/" in path:
                return r.fulfill(status=200, content_type="application/json", body='{"ok":true}')
            if "/api/" in path:
                return r.fulfill(status=404, content_type="application/json", body='{"error":"mock 404"}')
            return r.continue_()
        pg.on("console", lambda m: self.errs.append(m.text) if m.type == "error" else None)
        pg.route("**/*", route)
        pg.goto("http://127.0.0.1:%d/%s" % (self.port, suffix), wait_until="networkidle")
        pg.wait_for_selector(wait_sel, timeout=10000)
        return pg

    def open_history(self):
        pg = self.page("?view=history&p=p1")
        pg.wait_for_selector("#historyList .hist-item", timeout=10000)
        return pg

    def hist_ids(self, pg):
        return pg.eval_on_selector_all("#historyList .hist-item", "els => els.map(e => e.dataset.histId)")

    def test_direct_open_without_project_param_still_loads(self):
        # 2026-10-02：地址里不带 p=，首个历史请求发生在项目确定之前（project=''），数据回来后口径变了没人再取，列表一直空、
        # 「只看未读」显示 undefined。现在当前口径没数据就补取一次。
        pg = self.page("?view=history")
        pg.wait_for_function("() => document.querySelectorAll('#historyList .hist-item').length > 0", timeout=10000)
        self.assertNotIn("undefined", pg.inner_text("[data-action='hist-unread']"))
        pg.close()

    def test_1_视图渲染与_hub_收件箱共存(self):
        pg = self.open_history()
        self.assertEqual(len(self.hist_ids(pg)), 20, "第一页 20 条")
        self.assertIn("第 1 / 2 页", pg.inner_text("[data-hist-pagestatus]"))
        self.assertIn("共 25 条", pg.inner_text("[data-hist-pagestatus]"))
        rng = pg.inner_text("[data-hist-range]")
        self.assertIn("已加载第 1–20 条", rng)
        self.assertIn("还不是完整历史", rng)
        self.assertIn("任务历史", pg.inner_text("#nav"))
        self.assertEqual(pg.get_attribute('#nav [data-view="history"]', "aria-current"), "page")
        self.assertTrue(any("project=p1" in c for c in self.hist_calls), self.hist_calls)
        hub = self.page("hub.html", wait_sel="#tab-inbox")
        hub.click("#tab-inbox")
        hub.wait_for_function("() => /外部来源的一条待归集/.test((document.querySelector('#hubMain')||{}).innerText || '')", timeout=10000)
        self.assertEqual(len(hub.query_selector_all(".hist-item")), 0, "hub 页不应出现任务历史条目")
        self.assertEqual(self.hist_calls, [], "hub 不请求 /api/inbox/history")
        pg.close(); hub.close()

    def test_2_键盘浏览并打开决策(self):
        pg = self.open_history()
        ids = self.hist_ids(pg)
        pg.focus("#historyList .hist-item")
        self.assertEqual(pg.evaluate("() => document.activeElement.classList.contains('hist-item')"), True, "条目本身可聚焦")
        pg.keyboard.press("ArrowDown")
        self.assertEqual(pg.evaluate("() => document.activeElement.dataset.histId"), ids[1], "ArrowDown 移到下一条")
        pg.keyboard.press("ArrowDown")
        pg.keyboard.press("ArrowUp")
        self.assertEqual(pg.evaluate("() => document.activeElement.dataset.histId"), ids[1])
        self.assertEqual(pg.evaluate("() => document.activeElement.tagName"), "LI")
        pg.keyboard.press("Enter")
        pg.wait_for_selector("#drawer.show", timeout=10000)
        self.assertIn("历史决策", pg.inner_text("#drawerTitle") + pg.inner_text("#drawerBody"))
        pg.keyboard.press("Escape")
        pg.wait_for_function("() => !document.querySelector('#drawer').classList.contains('show')", timeout=10000)
        self.assertEqual(pg.evaluate("() => document.activeElement.dataset.histId"), ids[1], "关抽屉后焦点回到原条目")
        pg.click('#historyList .hist-item:first-child [data-action="hist-open"]')
        pg.wait_for_selector("#drawer.show", timeout=10000)
        pg.keyboard.press("Escape")
        pg.wait_for_function("() => !document.querySelector('#drawer').classList.contains('show')", timeout=10000)
        pg.wait_for_function("() => !document.querySelector('#historyList .hist-item:first-child').classList.contains('unread')", timeout=10000)
        self.assertEqual(pg.evaluate("() => document.querySelectorAll('#historyList .hist-item.unread').length"), 18,
                         "这一页 20 条里看过 2 条，其余仍标未读")
        pg.close()

    def test_3_下一页走网址后退回第一页(self):
        pg = self.open_history()
        first = self.hist_ids(pg)
        self.assertEqual(pg.evaluate("() => document.querySelector('[data-action=\"hist-page\"][data-dir=\"-1\"]').disabled"), True,
                         "第 1 页时上一页禁用")
        pg.click('[data-action="hist-page"][data-dir="1"]')
        pg.wait_for_function("() => /hpage=1/.test(location.search)", timeout=10000)
        pg.wait_for_function("() => document.querySelectorAll('#historyList .hist-item').length === 5", timeout=10000)
        self.assertIn("第 2 / 2 页", pg.inner_text("[data-hist-pagestatus]"))
        self.assertIn("已全部载入", pg.inner_text("[data-hist-range]"))
        self.assertEqual(pg.evaluate("() => document.querySelector('[data-action=\"hist-page\"][data-dir=\"1\"]').disabled"), True,
                         "到底了下一页禁用")
        pg.go_back()
        pg.wait_for_function("() => !/hpage=1/.test(location.search)", timeout=10000)
        pg.wait_for_function("() => document.querySelectorAll('#historyList .hist-item').length === 20", timeout=10000)
        self.assertEqual(self.hist_ids(pg), first, "后退回到第 1 页同一批条目")
        pg.close()

    def test_4_冻结页新增只提示点应用才换(self):
        pg = self.open_history()
        before = self.hist_ids(pg)
        new = {"task_id": "d-new", "status": "adopted", "title": "刚落地的一条", "summary": "",
               "created": iso(), "updated": iso()}
        self.set_records(add=new)
        pg.click('[data-action="hist-reload"]')
        pg.wait_for_selector('[data-action="hist-apply-new"]', timeout=10000)
        self.assertIn("有 1 条新内容", pg.inner_text('[data-action="hist-apply-new"]'))
        self.assertEqual(self.hist_ids(pg), before, "只提示，不自动把用户正在读的那一页换掉")
        self.assertNotIn("刚落地的一条", pg.inner_text("#historyList"), "新条目还没进用户正在读的那一页")
        pg.click('[data-action="hist-apply-new"]')
        pg.wait_for_function("() => !document.querySelector('[data-action=\"hist-apply-new\"]')", timeout=10000)
        after = self.hist_ids(pg)
        self.assertEqual(len(after), 20)
        self.assertNotEqual(after, before, "点应用之后换成本次快照")
        self.assertIn("刚落地的一条", pg.inner_text("#historyList"))
        # 被移除的条目仍带着标题与时间留在列表里，只多一个标记
        self.set_records(drop="d-08")
        pg.click('[data-action="hist-reload"]')
        pg.wait_for_selector('[data-action="hist-apply-new"]', timeout=10000)
        self.assertIn("已移除", pg.inner_text('[data-action="hist-apply-new"]'))
        txt = pg.inner_text(".hist-item.removed")   # 应用之前，被移除的那条仍留在用户正在读的页面上
        self.assertIn("历史决策 08", txt, "移除项保留原快照里的标题，不是一行空白")
        self.assertIn("已从最新结果移除", txt)
        self.assertEqual(len(pg.query_selector_all("#historyList .hist-item")), 20, "还是那一页 20 条，其中 1 条标为已移除")
        pg.click('[data-action="hist-apply-new"]')
        pg.wait_for_function("() => !document.querySelector('.hist-item.removed')", timeout=10000)
        self.assertNotIn("历史决策 08", pg.inner_text("#historyList"), "应用之后回到最新快照")
        self.assertEqual(len(pg.query_selector_all("#historyList .hist-item")), 20)
        pg.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
