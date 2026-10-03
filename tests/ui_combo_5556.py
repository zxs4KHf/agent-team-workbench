# 5556 组合验收（Python Playwright + Chromium，静态文件来自本 worktree，全部 /api 走 page.route mock；不改名、不派发、不采纳、不写 priority / 租约 / ack）。
# 1) 真实鼠标拖动（HTML5 DnD 事件序列）过滤后的 task 行 → 提交的 task_order 仍保留不可见项；
# 2) 发起页输入 + IME 合成中 + 心跳 / 宏计数更新 → 表单内容与焦点不丢；
# 3) handoff 四字段：缺项的不进「等你一步」，齐全的进且「已完成」提交到 /api/decision/handoff-finish；
# 4) 命名交接卡与四桶 / 优先级 / 谱系同页共存，事件 ack 后只局部刷新（焦点不丢、页面不整页重建）。
# 跑法：py -3 -X utf8 tests/ui_combo_5556.py
import json, os, sys, threading, time, unittest
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None

NOW = time.strftime("%Y-%m-%dT%H:%M:%S")
def running(tid):
    return {"task_id": tid, "status": "dispatched", "title": "任务 " + tid, "peers": ["glm1"], "mode": "independent", "created": NOW, "updated": NOW, "history": [],
            "progress": {"expected": ["glm1"], "answered": [], "states": {"glm1": "waiting"}, "meta": {"created": NOW, "ttl_min": 30, "id": tid, "peers": ["glm1"]}}}
def adopted(tid, bucket, handoff=None, topic="主题甲", rnd=1):
    return {"task_id": tid, "status": "adopted", "execution": "pending", "title": "决策 " + tid, "peers": ["glm1"], "mode": "independent", "created": NOW, "updated": NOW, "history": [], "merge_path": "log/x.md",
            "bucket": bucket, "handoff": handoff, "implementation": {"state": "dispatched", "label": "已建单，等待 builder 领取", "blocked": False, "wait_sec": 60},
            "lineage": {"topic": topic, "root_task": tid, "parent_task": "", "round": rnd, "kind": "build", "kind_label": "施工", "display_title": f"{topic} · R{rnd} · 施工", "has_lineage": True, "self_root": True, "children": []}}
FULL = {"user_step": "双击 tools\\run.bat", "reason": "需要管理员权限", "verify": "浏览器打开 8791 返回 200", "evidence": ["commit 1234567"], "prepared_by": "glm1", "prepared_at": NOW, "state": "ready"}
PARTIAL = {"user_step": "重启服务", "reason": "", "verify": "", "evidence": [], "prepared_by": "", "state": "ready"}   # 服务端 handoff_ready 判 false → bucket=agent
def state():
    return {"time": NOW, "connected": True,
            "peers": [{"peer": "glm1", "model": "glm-5.3", "status": "idle", "online": True, "last_seen": NOW, "label": ""}, {"peer": "controller", "model": "controller", "status": "idle", "online": True, "last_seen": NOW, "label": "小队长"}],
            "controller": {"peer": "controller", "online": True, "status": "idle", "last_seen": NOW, "label": "小队长"}, "bridge_url": "", "lease": {"holder": "controller", "epoch": 3, "expires": NOW},
            "priority": {"rev": 7, "task_order": ["t-a", "t-b", "t-c", "hidden-x", "d-user"], "agent_weight": {}, "relay_order": [], "relay_order_meta": [], "relay_policy": {}},
            "relay_order_effective": [], "relay_resolution": {"items": [], "pick": ""}, "commands": [], "recent_done": [], "open_tasks": [], "nudges": {}, "builders": {"groups": [], "members": [], "online_n": 0},
            "features": {"lead_label": True, "lead_events": True}, "pending_labels": {},
            "lead_events_open": [{"id": "ev1", "at": NOW, "first_at": NOW, "from": "controller", "to": "glm1", "reason": "takeover", "epoch": 3, "prompt": "RELAY_TAKEOVER ...", "count": 1, "seen": 5, "acked": False, "unstable": False, "recent_total": 1}],
            "projects": [{"slug": "p1", "name": "p1", "peers": ["glm1"], "mode": "independent", "context_files": [], "next": [], "notes": [], "threads": [],
                           "decisions": [running("t-a"), running("t-b"), running("t-c"), adopted("d-user", "user", FULL), adopted("d-partial", "agent", PARTIAL, "主题乙")]}]}


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass


@unittest.skipIf(sync_playwright is None, "playwright 未安装")
class TestCombo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=os.path.join(REPO, "web"))); cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start(); cls.browser = cls.pw.chromium.launch(headless=True)
        with open(os.path.join(REPO, "web", "v2", "index.html"), encoding="utf-8") as f:
            cls.index = f.read()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.srv.shutdown(); cls.srv.server_close()

    def page(self, suffix):
        pg = self.browser.new_page(viewport={"width": 1366, "height": 900})
        self.posts, self.errs, self.state = [], [], state()
        pg.on("console", lambda m: self.errs.append(m.text) if m.type == "error" else None)
        def route(r):
            p = r.request.url.split("?")[0]
            if p.endswith("/") or p.endswith("/index.html"): return r.fulfill(status=200, content_type="text/html; charset=utf-8", body=self.index)
            if "/api/state" in p: return r.fulfill(status=200, content_type="application/json", body=json.dumps(self.state))
            if r.request.method == "POST" and "/api/" in p:
                body = json.loads(r.request.post_data or "{}"); self.posts.append((p.split("/api/")[1], body))
                if p.endswith("/api/priority"):
                    cur = self.state["priority"]; cur.update({k: v for k, v in body.items() if k in ("task_order", "agent_weight")}); cur["rev"] += 1
                    return r.fulfill(status=200, content_type="application/json", body=json.dumps(cur))
                if p.endswith("/api/lead_event/ack"):
                    self.state["lead_events_open"] = []; return r.fulfill(status=200, content_type="application/json", body='{"ok":true,"changed":1,"open":[]}')
                if p.endswith("/api/decision/handoff-finish"):
                    return r.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "task_id": body.get("task_id"), "execution": "released"}))
                return r.fulfill(status=200, content_type="application/json", body='{"ok":true}')
            if "/api/" in p: return r.fulfill(status=404, content_type="application/json", body='{"error":"mock 404"}')
            return r.continue_()
        pg.route("**/*", route)
        pg.goto(f"http://127.0.0.1:{self.port}/{suffix}", wait_until="networkidle"); pg.wait_for_selector("#content .view", timeout=10000)
        return pg

    def dnd(self, pg, src_sel, dst_sel):
        # HTML5 DnD：真实指针按下手柄 → 派发 dragstart/dragover/drop/dragend（Chromium headless 对原生拖动不稳定，事件序列等价于 app.js 监听的路径）
        pg.hover(src_sel); pg.mouse.down()
        pg.dispatch_event(src_sel, "dragstart", {"dataTransfer": pg.evaluate_handle("() => new DataTransfer()")})
        # app.js 在 requestAnimationFrame 里才给源行加 .dragging，dragover 找不到 .dragging 会直接返回。
        # 真实拖动会连续触发几十次 dragover，测试只派发一次，所以要先等 .dragging 出现（fable2 审查 b0a3 定位）。
        pg.wait_for_function("() => !!document.querySelector('.task.dragging')", timeout=5000)
        pg.dispatch_event(dst_sel, "dragover", {"dataTransfer": pg.evaluate_handle("() => new DataTransfer()"), "clientY": 0})
        pg.dispatch_event(dst_sel, "drop", {"dataTransfer": pg.evaluate_handle("() => new DataTransfer()")})
        pg.dispatch_event(src_sel, "dragend", {"dataTransfer": pg.evaluate_handle("() => new DataTransfer()")})
        pg.mouse.up()

    def test_1_mouse_drag_filtered_keeps_hidden_order(self):
        pg = self.page("?view=running&p=p1&q=t-")
        pg.wait_for_selector(".task[data-key='t-c'] [data-task-drag]", timeout=10000)
        self.assertEqual(pg.eval_on_selector_all(".task", "els => els.map(e => e.dataset.key)"), ["t-a", "t-b", "t-c"], "过滤后只显示三条 running（d-user/hidden-x 不可见）")
        self.dnd(pg, ".task[data-key='t-c'] [data-task-drag]", ".task[data-key='t-a']")
        time.sleep(0.8)
        pri = [b for k, b in self.posts if k == "priority"]
        self.assertEqual(len(pri), 1, self.posts)
        order = pri[0]["task_order"]
        self.assertEqual(sorted(order), sorted(["t-a", "t-b", "t-c", "hidden-x", "d-user"]), "整表一个不丢")
        self.assertEqual(order[3:], ["hidden-x", "d-user"], "不可见项与相对位置原样保留")
        self.assertNotEqual(order[:3], ["t-a", "t-b", "t-c"], "可见三项确实重排了")
        self.assertFalse(pg.eval_on_selector(".task[data-key='t-c']", "e => e.classList.contains('open')"), "拖完不误展开行")
        pg.close()

    def test_2_compose_typing_ime_and_heartbeat_keep_draft_and_focus(self):
        pg = self.page("?view=compose&p=p1")
        pg.wait_for_selector("#composerForm [data-field='question']", timeout=10000)
        pg.click("#composerForm [data-field='question']"); pg.keyboard.type("先写一段 123")
        # IME 合成中：compositionstart 后按 3 不切视图、不丢字
        pg.dispatch_event("#composerForm [data-field='question']", "compositionstart")
        pg.keyboard.press("3"); pg.keyboard.press("7")
        pg.dispatch_event("#composerForm [data-field='question']", "compositionend")
        # 心跳 / 状态变化：time 与 peers 都变 → 一次整页 renderView
        self.state["time"] = "2026-09-24T23:59:59"; self.state["peers"][0]["status"] = "busy"
        pg.evaluate("() => import('/v2/js/app.js').then(m => m.refresh(true))"); time.sleep(0.8)
        self.assertIn("view=compose", pg.url)
        self.assertEqual(pg.eval_on_selector("#composerForm [data-field='question']", "t => t.value"), "先写一段 12337")
        self.assertEqual(pg.evaluate("() => document.activeElement && document.activeElement.dataset.field"), "question", "焦点仍在题目框")
        # Team 宏：计数更新、草稿不丢
        n0 = pg.eval_on_selector_all("#composerForm [data-agent-pick]:checked", "els => els.length")
        pg.click("#composerForm [data-action='composer-team'][data-team='全员在线']"); time.sleep(0.3)
        self.assertIn("发给 1 人", pg.inner_text("#composerSubmit"))   # e020：按钮数字 = 实发人数，预计份数措辞分开
        self.assertEqual(pg.eval_on_selector("#composerForm [data-field='question']", "t => t.value"), "先写一段 12337")
        pg.close()

    def test_3_handoff_gate_in_ui_and_submit(self):
        pg = self.page("?view=overview&p=p1")
        pg.wait_for_selector("[data-landing] [data-bucket='user']", timeout=10000)
        user_items = pg.eval_on_selector_all("[data-bucket-item='user']", "els => els.length")
        self.assertEqual(user_items, 1, "只有四字段齐全的 d-user 进「等你一步」")
        self.assertIn("主题甲 · R1 · 施工", pg.inner_text("[data-bucket='user']"))
        self.assertIn("主题乙 · R1 · 施工", pg.inner_text("[data-bucket='agent']"), "缺 reason/verify/evidence 的留在 Agent 处理中")
        self.assertNotIn("重启服务", pg.inner_text("[data-bucket='user']"))
        pg.click("[data-bucket='user'] [data-action='handoff-finish'][data-result='done']")
        pg.wait_for_selector("#dialog.show", timeout=5000); pg.click("#dialog [data-r='1']")
        time.sleep(0.6)
        done = [b for k, b in self.posts if k == "decision/handoff-finish"]
        self.assertEqual(done, [{"project": "p1", "task_id": "d-user", "result": "done", "by": "user"}])
        pg.close()

    def test_4_naming_card_coexists_and_ack_is_local(self):
        pg = self.page("?view=running&p=p1")
        pg.wait_for_selector("#leadEvents .lead-event", timeout=10000)
        self.assertIn("小队长", pg.inner_text("#service"), "顶栏显示名（3e32）")
        self.assertIn("小队长", pg.inner_text("#leadEvents"), "交接卡用显示名")
        self.assertNotIn("×5", pg.inner_text("#leadEvents"), "seen=5 不显示 ×N")
        self.assertTrue(pg.query_selector(".task[data-key='t-a'] [data-task-drag]"), "优先级手柄同页存在")
        pg.evaluate("() => { window.__mark = document.querySelector('#content .view'); window.__row = document.querySelector(\".task[data-key='t-b']\"); }")
        pg.click("#leadEvents [data-action='lead-ack']")
        pg.wait_for_function("() => { const b = document.querySelector('#leadEvents'); return !b || b.hidden || !b.querySelector('.lead-event'); }", timeout=8000)
        self.assertEqual([k for k, _ in self.posts], ["lead_event/ack"])
        self.assertTrue(pg.evaluate("() => window.__mark === document.querySelector('#content .view')"), "ack 只局部刷新，视图根节点未重建")
        self.assertTrue(pg.evaluate("() => window.__row === document.querySelector(\".task[data-key='t-b']\")"), "任务行 DOM 节点未被重建（Idiomorph 按 data-key 复用）")
        # 只有交接事件变化（不点任何东西）：聚焦中的手柄不丢焦点
        pg.focus(".task[data-key='t-b'] [data-task-drag]")
        self.state["lead_events_open"] = [dict(state()["lead_events_open"][0], id="ev2", epoch=4)]
        pg.evaluate("() => import('/v2/js/app.js').then(m => m.refresh())"); time.sleep(0.8)
        self.assertTrue(pg.query_selector("#leadEvents .lead-event"), "新事件卡片出现")
        self.assertTrue(pg.evaluate("() => document.activeElement && document.activeElement.hasAttribute('data-task-drag')"), "事件刷新后手柄焦点未丢")
        pg.close()


if __name__ == "__main__":
    unittest.main(verbosity=1)