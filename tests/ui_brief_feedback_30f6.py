# 决策 20260929-181437-30f6 第 5 / 6 步 · 真实 Chromium 里点「看不懂」（Python Playwright）：静态文件来自本 worktree 的 web/，
# 全部 /api 走 page.route mock（不碰生产数据，也不真的往 Leader 队列排指令）。
# 后端那一半（指令 / 标记 / 幂等 / 重写后清除）见 tests/test_brief_feedback_30f6.py，真 HTTP 服务也在那边；这里验证浏览器里的整条链：
# 1) 决策页点按钮 → POST /api/decision/brief-rewrite 带对项目、任务号与卡面上看到的那句话 → 刷新后这张卡标「待重写」、按钮消失，别的卡不受影响；
# 2) 概览「简介体检」：新卡数 / 缺失数 / 每种兜底句次数 / 老卡数 / 提醒条；提醒线调高后提醒条消失；点完后看板写出「1 张在等 Leader 重写」；
# 3) 详情抽屉里的「简介」也能点，点完抽屉当场出现「待重写」。
# 跑法：py -3 -X utf8 tests/ui_brief_feedback_30f6.py
import json, os, threading, time, unittest
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None

NOW = time.strftime("%Y-%m-%dT%H:%M:%S")
BRIEF = "招标方发来澄清文件，设备清单要逐条改三十多处，已做工具自动改并自检。"


def card(tid, status="merged", **kw):
    return {"task_id": tid, "status": status, "title": "决策 " + tid, "peers": ["glm1"], "mode": "discuss", "created": NOW,
            "updated": NOW, "history": [], "summary": "", "brief": "", **kw}


LEGACY = card("old-1")
del LEGACY["brief"]                                   # 老记录：没有 brief 槽位
STATE = {"time": NOW, "connected": True, "peers": [{"peer": "glm1", "model": "glm-5.3", "status": "idle", "online": True, "last_seen": NOW, "label": ""}],
         "controller": {"peer": "controller", "online": True, "status": "idle", "last_seen": NOW}, "bridge_url": "", "lease": {"holder": "controller", "epoch": 3, "expires": NOW},
         "priority": {"rev": 7, "task_order": [], "agent_weight": {}, "relay_order": [], "relay_order_meta": [], "relay_policy": {}},
         "relay_order_effective": [], "relay_resolution": {"items": [], "pick": ""}, "commands": [], "recent_done": [], "open_tasks": [], "nudges": {},
         "builders": {"groups": [], "members": [], "online_n": 0},
         "projects": [{"slug": "p1", "name": "p1", "peers": ["glm1"], "mode": "independent", "context_files": [], "next": [], "notes": [], "threads": [],
                       "decisions": [card("c1"), card("c2", brief=BRIEF), card("c3"),
                                     card("c4", "adopted", artifacts=[{"path": "web/a.js", "kind": "code"}]), LEGACY]}]}
# 新卡 4 张（c1 c2 c3 c4），缺 3 张 = 75%：方案卡 2、交付物卡 1、审查卡 0；老卡 1 张不计入


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@unittest.skipIf(sync_playwright is None, "playwright 未安装")
class TestBriefFeedbackUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=os.path.join(REPO, "web")))
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.srv.shutdown()
        cls.srv.server_close()

    def page(self, url_suffix):
        pg = self.browser.new_page(viewport={"width": 1366, "height": 900})
        self.posts, self.state = [], json.loads(json.dumps(STATE))
        with open(os.path.join(REPO, "web", "v2", "index.html"), encoding="utf-8") as f:
            index = f.read()

        def route(r):
            u = r.request.url
            p = u.split("?")[0]
            if p.endswith("/") or p.endswith("/index.html"):
                return r.fulfill(status=200, content_type="text/html; charset=utf-8", body=index)
            if "/api/state" in p:
                return r.fulfill(status=200, content_type="application/json", body=json.dumps(self.state))
            if p.endswith("/api/decision/brief-rewrite") and r.request.method == "POST":
                body = json.loads(r.request.post_data or "{}")
                self.posts.append(body)
                for d in self.state["projects"][0]["decisions"]:
                    if d["task_id"] == body.get("task_id"):
                        d["brief_rewrite"] = {"command_id": "cmd-1", "original": body.get("shown", ""), "by": "user"}
                return r.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"ok": True, "dedup": False, "task_id": body.get("task_id"), "command_id": "cmd-1", "note": "已请 Leader 重写这句简介"}))
            if "/api/" in p:
                return r.fulfill(status=404, content_type="application/json", body='{"error":"mock 404"}')
            return r.continue_()

        pg.route("**/*", route)
        pg.goto(f"http://127.0.0.1:{self.port}/{url_suffix}", wait_until="networkidle")
        pg.wait_for_selector("#content .view", timeout=10000)
        return pg

    def wait_post(self):
        for _ in range(80):
            if self.posts:
                return self.posts[-1]
            time.sleep(0.1)
        self.fail("按钮没有发出 POST /api/decision/brief-rewrite")

    def refresh(self, pg):
        pg.evaluate("() => import('/v2/js/app.js').then(m => m.refresh(true)).catch(() => 0)")

    def test_click_on_decision_card_posts_and_marks_it(self):
        pg = self.page("?view=decisions&p=p1")
        sel = "article.dcard[data-key='c1'] button[data-action='brief-confusing']"
        pg.wait_for_selector(sel, timeout=10000)
        shown = pg.inner_text("article.dcard[data-key='c1'] p.summary").strip()
        self.assertTrue(shown, "兜底句在卡面上")
        pg.click(sel)
        body = self.wait_post()
        self.assertEqual((body["project"], body["task_id"], body["by"]), ("p1", "c1", "user"))
        self.assertEqual(body["shown"], shown, "带上去的原文就是用户眼前那句话")
        pg.wait_for_selector("article.dcard[data-key='c1'] [data-brief-rewrite]", timeout=8000)
        self.assertEqual(pg.inner_text("article.dcard[data-key='c1'] [data-brief-rewrite]").strip(), "待重写")
        self.assertEqual(pg.locator("article.dcard[data-key='c1'] button[data-action='brief-confusing']").count(), 0, "排上以后不再给按钮")
        self.assertEqual(pg.locator("article.dcard[data-key='c3'] button[data-action='brief-confusing']").count(), 1, "别的卡不受影响")
        self.assertEqual(pg.inner_text("article.dcard[data-key='c1'] p.summary").strip(), shown, "卡面那句话原样不动")
        pg.close()

    def test_overview_board_counts_alert_and_pending_rewrite(self):
        pg = self.page("?view=overview&p=p1")
        # 2026-10-02 概览一屏：简介体检在右栏「简介体检」标签里（超提醒线时默认打开）；点一下确保在显示
        pg.click("[data-action='ov-tab'][data-group='side'][data-tab='brief']")
        pg.wait_for_selector("[data-brief-health]", timeout=10000)
        txt = pg.inner_text("[data-brief-health]")
        self.assertIn("新卡 4 张 · 缺简介 3（75%）", txt)
        self.assertIn("兜底句占 75%，超过提醒线 50%", txt)
        self.assertIn("另有 1 张老卡只走兜底句，不计入占比", txt)
        self.assertTrue(pg.inner_text("[data-brief-kind='plan']").strip().startswith("2"))
        self.assertTrue(pg.inner_text("[data-brief-kind='review']").strip().startswith("0"))
        self.assertTrue(pg.inner_text("[data-brief-kind='artifact']").strip().startswith("1"))
        self.assertTrue(pg.is_visible("[data-brief-alert]"))
        self.state["brief_alert_ratio"] = 0.9          # 服务端下发的提醒线调高：75% 不再超过
        self.refresh(pg)
        pg.wait_for_function("() => !document.querySelector('[data-brief-alert]')", timeout=8000)
        self.assertIn("提醒线 90%", pg.inner_text("[data-brief-health]"))
        # 在决策页点一张，回概览：看板写出有一张在等 Leader 重写
        pg.evaluate("() => { const u = new URL(location.href); u.searchParams.set('view', 'decisions'); history.pushState({}, '', u); dispatchEvent(new PopStateEvent('popstate')); }")
        pg.wait_for_selector("article.dcard[data-key='c3'] button[data-action='brief-confusing']", timeout=10000)
        pg.click("article.dcard[data-key='c3'] button[data-action='brief-confusing']")
        self.wait_post()
        pg.wait_for_selector("article.dcard[data-key='c3'] [data-brief-rewrite]", timeout=8000)
        pg.click("button[data-action='view'][data-view='overview']")
        pg.click("[data-action='ov-tab'][data-group='side'][data-tab='brief']")
        pg.wait_for_selector("[data-brief-health]", timeout=10000)
        self.assertIn("1 张在等 Leader 重写", pg.inner_text("[data-brief-health]"))
        pg.close()

    def test_click_inside_the_detail_drawer_marks_it_in_place(self):
        pg = self.page("?view=decisions&p=p1&d=c2")
        pg.wait_for_selector("#drawer.show", timeout=10000)
        sel = "#drawer button[data-action='brief-confusing']"
        pg.wait_for_selector(sel, timeout=8000)
        pg.click(sel)
        body = self.wait_post()
        self.assertEqual((body["project"], body["task_id"], body["shown"]), ("p1", "c2", BRIEF), "写过 brief 的卡：原文是 brief")
        pg.wait_for_selector("#drawer [data-brief-rewrite]", timeout=8000)
        self.assertEqual(pg.locator("#drawer button[data-action='brief-confusing']").count(), 0)
        pg.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
