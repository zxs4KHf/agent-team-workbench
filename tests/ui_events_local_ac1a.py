# ac1a R4 必修2 验收：交接通知的单独变化不得触发 #content 整页 morph。
# 用真实 Chromium + 注入到 core.morph 的计数器做旧红新绿：
#   新代码 → 纯事件变化 / ack / feature 关闭 / unstable 变化时 contentMorph=0，banner 仍然及时更新，视图根节点与输入焦点保持；
#   旧 40a176e 的 app.js → 同一实验 contentMorph>0（证明计数器真的抓得到整页重绘）；
#   label 真变化仍然整页重绘（不能靠删 key 换取不 morph）。
# 跑法：py -3 -X utf8 tests/ui_events_local_ac1a.py（仓库根目录；全部 /api 走 mock，不碰生产状态 / 租约 / ack）
import json, os, subprocess, sys, threading, time, unittest
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None

NOW = time.strftime("%Y-%m-%dT%H:%M:%S")
NEEDLE = "export function morph(node, html, opts = {}) {"
OLD_HEAD = "40a176e234a781c03b6593178061d97bcce04b17"


def running(tid):
    return {"task_id": tid, "status": "dispatched", "title": "任务 " + tid, "peers": ["glm1"], "mode": "independent",
            "created": NOW, "updated": NOW, "history": [],
            "progress": {"expected": ["glm1"], "answered": [], "states": {"glm1": "waiting"}}}


def event(**kw):
    e = {"id": "ev1", "at": NOW, "first_at": NOW, "from": "controller", "to": "glm1", "reason": "takeover",
         "epoch": 3, "prompt": "RELAY_TAKEOVER ...", "count": 1, "seen": 5, "acked": False, "unstable": False, "recent_total": 1}
    e.update(kw)
    return e


def state():
    return {"time": NOW, "connected": True,
            "peers": [{"peer": "glm1", "model": "glm-5.3", "status": "idle", "online": True, "last_seen": NOW, "label": ""},
                      {"peer": "controller", "model": "controller", "status": "idle", "online": True, "last_seen": NOW, "label": "小队长"}],
            "controller": {"peer": "controller", "online": True, "status": "idle", "last_seen": NOW, "label": "小队长"},
            "bridge_url": "", "lease": {"holder": "controller", "epoch": 3, "expires": NOW},
            "priority": {"rev": 7, "task_order": ["t-a", "t-b"], "agent_weight": {}, "relay_order": [], "relay_order_meta": [], "relay_policy": {}},
            "relay_order_effective": [], "relay_resolution": {"items": [], "pick": ""}, "commands": [], "recent_done": [],
            "open_tasks": [], "nudges": {}, "builders": {"groups": [], "members": [], "online_n": 0},
            "features": {"lead_label": True, "lead_events": True}, "pending_labels": {}, "lead_events_open": [event()],
            "projects": [{"slug": "p1", "name": "p1", "peers": ["glm1"], "mode": "independent", "context_files": [],
                          "next": [], "notes": [], "threads": [], "decisions": [running("t-a"), running("t-b")]}]}


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass


@unittest.skipIf(sync_playwright is None, "playwright 未安装")
class TestEventsLocal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=os.path.join(REPO, "web")))
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start(); cls.browser = cls.pw.chromium.launch(headless=True)
        with open(os.path.join(REPO, "web", "v2", "index.html"), encoding="utf-8") as f:
            cls.index = f.read()
        with open(os.path.join(REPO, "web", "v2", "js", "core.js"), encoding="utf-8") as f:
            core = f.read()
        assert core.count(NEEDLE) == 1, "core.morph 计数注入点丢失"
        # 只给测试服务内的 core.morph 加计数器，不落盘、不改候选源码
        cls.core = core.replace(NEEDLE, NEEDLE + "\n  if (node?.id === 'content') window.__contentMorphCalls = (window.__contentMorphCalls || 0) + 1;")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.srv.shutdown(); cls.srv.server_close()

    @classmethod
    def old_app(cls):
        if not hasattr(cls, "_old_app"):
            cls._old_app = subprocess.run(["git", "show", "%s:web/v2/js/app.js" % OLD_HEAD], cwd=REPO,
                                          capture_output=True, text=True, encoding="utf-8").stdout
            assert "function stateKey(s)" in cls._old_app, "取不到旧 head 的 app.js"
        return cls._old_app

    @classmethod
    def old_file(cls, rel):
        return subprocess.run(["git", "show", "%s:%s" % (OLD_HEAD, rel)], cwd=REPO,
                              capture_output=True, text=True, encoding="utf-8").stdout

    def page(self, app_src=None):
        pg = self.browser.new_page(viewport={"width": 1366, "height": 900})
        self.errs, self.posts, self.state = [], [], state()

        def route(r):
            p = r.request.url.split("?")[0]
            if p.endswith("/") or p.endswith("/index.html"):
                return r.fulfill(status=200, content_type="text/html; charset=utf-8", body=self.index)
            if p.endswith("/v2/js/core.js"):
                return r.fulfill(status=200, content_type="text/javascript", body=self.core)
            if p.endswith("/v2/js/app.js") and app_src:
                return r.fulfill(status=200, content_type="text/javascript", body=app_src)
            if p.endswith("/v2/js/arena.js") and app_src:
                # 旧 head 的 app.js 还 import ./arena.js；Arena 已搬成插件（ADR 0014），对照组从同一旧 head 取它
                return r.fulfill(status=200, content_type="text/javascript", body=self.old_file("web/v2/js/arena.js"))
            if "/api/state" in p:
                return r.fulfill(status=200, content_type="application/json", body=json.dumps(self.state))
            if r.request.method == "POST" and "/api/" in p:
                self.posts.append((p.split("/api/")[1], json.loads(r.request.post_data or "{}")))
                if p.endswith("/api/lead_event/ack"):
                    self.state["lead_events_open"] = []
                return r.fulfill(status=200, content_type="application/json", body='{"ok":true,"changed":1,"open":[]}')
            if "/api/" in p:
                return r.fulfill(status=404, content_type="application/json", body='{"error":"mock 404"}')
            return r.continue_()

        pg.on("console", lambda m: self.errs.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        pg.route("**/*", route)
        pg.goto("http://127.0.0.1:%d/?view=running&p=p1" % self.port, wait_until="networkidle")
        pg.wait_for_selector("#content .view", timeout=10000)
        pg.evaluate("() => { window.__contentMorphCalls = 0; window.__savedView = document.querySelector('#content .view'); }")
        return pg

    def refresh(self, pg):
        pg.evaluate("() => import('/v2/js/app.js').then(m => m.refresh())")
        pg.wait_for_timeout(350)

    def probe(self, pg):
        return pg.evaluate("""() => ({
          morphs: window.__contentMorphCalls || 0,
          viewKept: window.__savedView === document.querySelector('#content .view'),
          bannerVisible: !document.querySelector('#leadEvents').hidden,
          eventId: document.querySelector('#leadEvents .lead-event')?.dataset.id || '',
          head: document.querySelector('.lead-events-head b')?.textContent || '',
          active: document.activeElement?.dataset?.filter || document.activeElement?.tagName || '',
          typed: document.querySelector('input[data-filter=running]')?.value || '',
          shell: document.querySelector('#sideStats')?.textContent || ''
        })""")

    def store_view(self, pg):
        # 直接读浏览器里的 store：局部补丁是否真把新数据落进状态，一眼可验
        return pg.evaluate("""async () => { const m = await import('/v2/js/model.js'); const s = m.S();
          return { time: s.time, events: (s.lead_events_open || []).map(e => e.id), projects: s.projects.length, peers: s.peers.length, connected: s.connected }; }""")

    # ---------- 新绿 ----------
    def test_1_pure_event_change_does_not_morph_content(self):
        pg = self.page()
        pg.click("input[data-filter=running]"); pg.keyboard.type("t-")
        pg.evaluate("() => { window.__contentMorphCalls = 0; window.__savedView = document.querySelector('#content .view'); }")
        self.state["lead_events_open"] = [event(id="ev2", epoch=4, count=2)]
        self.state["time"] = "2026-09-24T23:59:59"
        self.refresh(pg)
        r = self.probe(pg)
        self.assertEqual(r["morphs"], 0, "纯事件变化不应 morph #content：%s" % r)
        self.assertTrue(r["viewKept"], "视图根节点保持")
        self.assertEqual(r["eventId"], "ev2", "新事件及时出现在 banner")
        self.assertTrue(r["bannerVisible"])
        self.assertEqual(r["active"], "running", "输入焦点保持")
        self.assertEqual(r["typed"], "t-", "已输入内容保持")
        self.assertIn("同步", r["shell"], "外壳侧栏仍在更新")
        sv = self.store_view(pg)
        self.assertEqual(sv["time"], "2026-09-24T23:59:59", "局部补丁同样落新 time")
        self.assertEqual(sv["events"], ["ev2"], "store 里已是新事件")
        self.assertTrue(sv["connected"])
        self.assertEqual(sv["projects"], 1, "项目投影未被局部补丁清空")
        self.assertEqual(self.errs, [], "无脚本报错（/api 与 SSE 的 mock 404 不计）")
        pg.close()

    def test_2_unstable_and_ack_and_feature_off_stay_local(self):
        pg = self.page()
        self.state["lead_events_open"] = [event(unstable=True, recent_total=6, count=5)]
        pg.evaluate("() => { window.__contentMorphCalls = 0; }")
        self.refresh(pg)
        r = self.probe(pg)
        self.assertEqual((r["morphs"], r["eventId"]), (0, "ev1"), "unstable 变化只局部更新 banner")
        self.assertIn("Leader 不稳定", r["head"], "不稳定口径仍然显示")
        pg.click("#leadEvents [data-action='lead-ack']")
        pg.wait_for_timeout(600)
        r = self.probe(pg)
        self.assertEqual([k for k, _ in self.posts], ["lead_event/ack"])
        self.assertFalse(r["bannerVisible"], "ack 后 banner 收起")
        self.assertEqual(r["morphs"], 0, "ack 不触发整页 morph")
        self.state["features"] = {"lead_label": True, "lead_events": False}
        self.state["lead_events_open"] = [event(id="ev3")]
        pg.evaluate("() => { window.__contentMorphCalls = 0; }")
        self.refresh(pg)
        r = self.probe(pg)
        self.assertEqual(r["morphs"], 0, "feature 关闭只收起 banner")
        self.assertFalse(r["bannerVisible"], "lead_events=false 时 banner 降级")
        self.assertEqual(self.errs, [], "无控制台报错")
        pg.close()

    def test_3_real_label_change_still_redraws(self):
        pg = self.page()
        self.state["peers"][1]["label"] = "新显示名"
        pg.evaluate("() => { window.__contentMorphCalls = 0; }")
        self.refresh(pg)
        r = self.probe(pg)
        self.assertGreater(r["morphs"], 0, "显示名真变化仍需整页更新需要名字的区域")
        self.assertIn("新显示名", pg.text_content("#service"), "顶栏显示名跟进")
        pg.close()

    def test_4_priority_and_lease_stay_scoped(self):
        pg = self.page()
        self.state["priority"] = dict(self.state["priority"], rev=8, task_order=["t-b", "t-a"])
        pg.evaluate("() => { window.__contentMorphCalls = 0; }")
        self.refresh(pg)
        self.assertEqual(self.probe(pg)["morphs"], 0, "调度表变化仍是局部刷新")
        self.state["lease"] = {"holder": "glm1", "epoch": 4, "expires": NOW}
        pg.evaluate("() => { window.__contentMorphCalls = 0; }")
        self.refresh(pg)
        r = self.probe(pg)
        self.assertEqual(r["morphs"], 0, "租约变化仍是局部刷新")
        self.assertEqual(self.errs, [], "无控制台报错")
        pg.close()

    # ---------- 旧红 ----------
    def test_5_old_head_morphs_content_on_event_change(self):
        pg = self.page(app_src=self.old_app())
        self.state["lead_events_open"] = [event(id="ev2", epoch=4)]
        pg.evaluate("() => { window.__contentMorphCalls = 0; }")
        self.refresh(pg)
        r = self.probe(pg)
        self.assertGreater(r["morphs"], 0, "旧 head 40a176e 必须被计数器抓到整页 morph（红）：%s" % r)
        self.assertEqual(r["eventId"], "ev2", "旧 head 也能显示事件，只是重绘了整页")
        pg.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
