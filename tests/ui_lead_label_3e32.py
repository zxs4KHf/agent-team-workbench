# 3e32 真实 DOM 验收：Python Playwright + Chromium，起一个指向临时目录的 bus_web（token/bridge 打桩，不发生产 token、不改生产 label/事件），
# 走 接入 Leader 抽屉 → 输入显示名 → 保存 → 提示词带名 → 顶栏显示名 → 交接事件 banner → 详情 → × 关闭（ack）→ banner 消失；重复上报 10 次不显示 ×N。
# 跑法：py -3 -X utf8 tests/ui_lead_label_3e32.py（仓库根目录；需要 pip 里的 playwright 与 chromium）
import json, os, shutil, sys, tempfile, threading, time, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_core as B          # noqa: E402
import bus_lead_notify as LN  # noqa: E402
import bus_projects as P      # noqa: E402
import bus_web as W           # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, "playwright 未安装")
class TestLeadLabelUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.tmp = tempfile.mkdtemp(prefix="bus3e32ui-")
        cls._saved = (B.ROOT, B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.LOCK, B.PRIORITY, B.WORKSPACE, P.PROJECTS, P.COMMANDS, P.DONE, P.BRIDGE, W.SET.reveal_token, W.SET.save)
        B.ROOT = B.WORKSPACE = cls.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(cls.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.LOCK = os.path.join(cls.tmp, "console", "controller.lock"); B.PRIORITY = os.path.join(cls.tmp, "console", "priority.json")
        P.PROJECTS, P.COMMANDS, P.DONE = (os.path.join(cls.tmp, *d) for d in (("projects",), ("console", "commands"), ("console", "done")))
        P.BRIDGE = os.path.join(cls.tmp, "console", "bridge.json")
        for d in (B.PEERS, B.INBOX, B.OUTBOX, B.LOG, P.PROJECTS, P.COMMANDS, P.DONE, os.path.join(cls.tmp, "console")):
            os.makedirs(d, exist_ok=True)
        B.write_json(P.BRIDGE, {"url": "http://127.0.0.1:1/mcp", "updated": B.now_iso()})
        with open(os.path.join(cls.tmp, "console", "mcp_token.txt"), "w", encoding="utf-8") as f:
            f.write("TESTTOKEN-not-a-real-token-0000")   # 临时目录里的假 controller token，只给 connect_link 读
        W.SET.reveal_token = lambda peer: "TESTTOKEN0000000000000000"   # 打桩：不生成、不落盘任何真实 token
        W.SET.save = lambda *a, **k: {}
        os.makedirs(os.path.join(P.PROJECTS, "p1"), exist_ok=True)
        B.write_json(os.path.join(P.PROJECTS, "p1", "project.json"), {"slug": "p1", "name": "p1", "peers": [], "mode": "independent", "context_files": [], "next": [], "notes": []})
        B.write_json(os.path.join(P.PROJECTS, "p1", "decisions.json"), [])
        B.heartbeat("controller", "idle", "controller"); B.heartbeat("glm1", "idle", "glm-5.3")
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), W.Handler); cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start(); cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close(); cls.pw.stop()
        finally:
            cls.srv.shutdown(); cls.srv.server_close()
            (B.ROOT, B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.LOCK, B.PRIORITY, B.WORKSPACE, P.PROJECTS, P.COMMANDS, P.DONE, P.BRIDGE, W.SET.reveal_token, W.SET.save) = cls._saved
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def page(self):
        pg = self.browser.new_page(viewport={"width": 1366, "height": 900})
        self.errs = []
        pg.on("console", lambda m: self.errs.append(m.text) if m.type == "error" else None)
        pg.goto(f"http://127.0.0.1:{self.port}/?view=overview&p=p1", wait_until="networkidle")
        pg.wait_for_selector("#service", timeout=10000)
        return pg

    def test_name_leader_then_prompt_then_events(self):
        pg = self.page()
        # 1) 接入 Leader 抽屉：显示名输入 → 保存 → 提示词带名
        loc = pg.locator("[data-action='join-controller']:visible")
        if loc.count() == 0:
            pg.goto(f"http://127.0.0.1:{self.port}/?view=peers&p=p1", wait_until="networkidle")
            loc = pg.locator("[data-action='join-controller']:visible")
        loc.first.click(timeout=8000)
        pg.wait_for_selector("#joinLabel", timeout=8000)
        pg.fill("#joinLabel", "小队长")
        pg.click("[data-action='join-save-label']")
        pg.wait_for_function("() => { const t = document.querySelector('#promptText'); return t && t.value.includes('\"小队长\"'); }", timeout=10000)
        prompt = pg.eval_on_selector("#promptText", "t => t.value")
        self.assertIn('"controller"', prompt); self.assertIn("不是指令", prompt); self.assertNotIn("「小队长", prompt)
        data = B.read_json(os.path.join(B.PEERS, "controller.json"), {})
        self.assertEqual((data["peer"], data["label"]), ("controller", "小队长"), "真实 ID 不变，label 落盘")
        pg.keyboard.press("Escape")
        # 2) 顶栏 Leader 区显示新名 + 改名入口存在
        pg.wait_for_function("() => document.querySelector('#service') && document.querySelector('#service').textContent.includes('小队长')", timeout=10000)
        self.assertTrue(pg.query_selector("#service [data-action='rename-peer']"))
        # 3) 交接事件：同 epoch 上报 10 次 → 一条卡片，无 ×N；详情可开；× 关闭走服务端 ack
        for _ in range(10):
            LN.record_lead_event(self.tmp, "controller", "glm1", 5, "takeover", B.RELAY_TEXT.format(age=200, peer="glm1", epoch=5))
        P._STATE_CACHE.update({"at": 0.0, "data": None})  # 服务端 /api/state 有秒级缓存，测试里直接失效
        pg.reload(wait_until="networkidle")
        pg.wait_for_selector("#leadEvents .lead-event", timeout=10000)
        cards = pg.query_selector_all("#leadEvents .lead-event")
        self.assertEqual(len(cards), 1)
        txt = pg.inner_text("#leadEvents")
        self.assertNotIn("×", txt); self.assertNotIn("不稳定", txt); self.assertIn("小队长", txt)
        pg.click("#leadEvents .lead-event")
        pg.wait_for_selector("#drawer.show", timeout=8000)
        dtxt = pg.inner_text("#drawer")
        self.assertIn("小队长 → glm1", dtxt); self.assertIn("controller → glm1", dtxt, "详情同时展示显示名与真实 peer")
        self.assertIn("不含 token", dtxt); self.assertNotIn("TESTTOKEN", dtxt)
        pg.keyboard.press("Escape")
        pg.click("#leadEvents [data-action='lead-ack']")
        deadline = time.time() + 10
        while time.time() < deadline and not all(e.get("acked") for e in LN._load(self.tmp)):
            time.sleep(0.2)
        P._STATE_CACHE.update({"at": 0.0, "data": None}); pg.reload(wait_until="networkidle")
        pg.wait_for_function("() => { const b = document.querySelector('#leadEvents'); return !b || b.hidden || !b.querySelector('.lead-event'); }", timeout=10000)
        self.assertTrue(all(e.get("acked") for e in LN._load(self.tmp)), "× 调了服务端 ack")
        bad = [e for e in self.errs if "favicon" not in e and "arena" not in e.lower()]
        self.assertEqual(bad, [], f"页面 console 错误：{bad}")
        pg.close()


if __name__ == "__main__":
    unittest.main(verbosity=1)
