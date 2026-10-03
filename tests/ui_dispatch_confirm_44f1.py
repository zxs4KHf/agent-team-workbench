# 44f1 真实浏览器验收：Python Playwright + Chromium，临时目录起 bus_web（不碰生产数据）。
# 覆盖：默认团队「项目团队」保存/读回同义、全员离线时不偷换成全员在线；发送确认一次性——取消不发、
# 确认后改名单不发、确认后清空问题再填不复用旧确认、正常确认只发一条且名单完整。
# 跑法：py -3 -X utf8 tests/ui_dispatch_confirm_44f1.py（仓库根目录）
import glob, json, os, shutil, sys, tempfile, threading, time, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_core as B      # noqa: E402
import bus_projects as P  # noqa: E402
import bus_web as W       # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None

PROJECT_TEAM = "项目团队"


@unittest.skipIf(sync_playwright is None, "playwright 未安装")
class TestDispatchConfirm44f1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.tmp = tempfile.mkdtemp(prefix="bus44f1ui-")
        cls._saved = (B.ROOT, B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.LOCK, B.PRIORITY, B.WORKSPACE, P.PROJECTS, P.COMMANDS, P.DONE, P.BRIDGE)
        B.ROOT = B.WORKSPACE = cls.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(cls.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.LOCK = os.path.join(cls.tmp, "console", "controller.lock"); B.PRIORITY = os.path.join(cls.tmp, "console", "priority.json")
        P.PROJECTS, P.COMMANDS, P.DONE = (os.path.join(cls.tmp, *d) for d in (("projects",), ("console", "commands"), ("console", "done")))
        P.BRIDGE = os.path.join(cls.tmp, "console", "bridge.json")
        for d in (B.PEERS, B.INBOX, B.OUTBOX, B.LOG, P.PROJECTS, P.COMMANDS, P.DONE, os.path.join(cls.tmp, "console")):
            os.makedirs(d, exist_ok=True)
        B.write_json(P.BRIDGE, {"url": "http://127.0.0.1:1/mcp", "updated": B.now_iso()})
        cls.mkproj("p1", peers=["alpha1", "off1"], teams=[{"name": "混合组", "peers": ["alpha1", "off1"]}], default_team="混合组")
        cls.mkproj("p2", peers=["off1"], default_team=PROJECT_TEAM)      # 项目团队全员离线
        B.heartbeat("controller", "idle", "controller"); B.heartbeat("alpha1", "idle", "m-a"); B.heartbeat("off1", "idle", "m-o")
        f = os.path.join(B.PEERS, "off1.json"); d = B.read_json(f, {})
        for k in [k for k in d if k in ("last_seen", "updated", "time", "ts")]:
            d[k] = "2026-01-01T00:00:00"
        B.write_json(f, d)
        # 2026-10-02 起在线只认总线心跳记录：off1 的那份也拨回去，否则它仍按总线心跳算在线
        B.write_json(B._beat_path("off1"), {"at": "2026-01-01T00:00:00", "activity": "idle", "task_id": ""})
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), W.Handler); cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start(); cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def mkproj(cls, slug, peers, teams=None, default_team=""):
        os.makedirs(os.path.join(P.PROJECTS, slug), exist_ok=True)
        B.write_json(os.path.join(P.PROJECTS, slug, "project.json"), {"slug": slug, "name": slug, "peers": peers, "mode": "independent", "context_files": [],
                                                                         "next": [], "notes": [], "teams": teams or [], "default_team": default_team})
        B.write_json(os.path.join(P.PROJECTS, slug, "decisions.json"), [])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close(); cls.pw.stop()
        finally:
            cls.srv.shutdown(); cls.srv.server_close()
            (B.ROOT, B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.LOCK, B.PRIORITY, B.WORKSPACE, P.PROJECTS, P.COMMANDS, P.DONE, P.BRIDGE) = cls._saved
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def page(self, view, slug):
        pg = self.browser.new_page(viewport={"width": 1366, "height": 900})
        self.errs = []
        pg.on("console", lambda m: self.errs.append(m.text) if m.type == "error" else None)
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        pg.goto(f"http://127.0.0.1:{self.port}/?view={view}&p={slug}", wait_until="networkidle")
        return pg

    def dispatches(self):
        out = []
        for f in glob.glob(os.path.join(P.COMMANDS, "*.json")) + glob.glob(os.path.join(P.DONE, "*.json")):
            c = B.read_json(f, {}) or {}
            if c.get("kind") == "dispatch":
                out.append(c)
        return out

    def checked(self, pg):
        return sorted(pg.eval_on_selector_all("[data-agent-pick]:checked", "els => els.map(e => e.value)"))

    def box(self, pg):
        return pg.locator("[data-send-confirm]").count() > 0

    def settle(self, pg, ms=600):
        pg.wait_for_timeout(ms)

    def test_1_confirm_is_one_shot(self):
        pg = self.page("compose", "p1")
        pg.wait_for_selector("#question", timeout=10000)
        self.assertIn("dim", pg.get_attribute("label.pick:has([value='off1'])", "class") or "", "前置：off1 应显示为离线")
        self.assertEqual(self.checked(pg), ["alpha1", "off1"], "前置：默认团队「混合组」展开为两人")
        pg.fill("#question", "问题一")
        # S1 有不可派发者 → 先要求确认，不发
        pg.click("#composerSubmit"); pg.wait_for_selector("[data-send-confirm]", timeout=5000)
        self.assertEqual(len(self.dispatches()), 0, "S1 未确认不得发出")
        # S2 取消 → 不发、提示条消失
        pg.click("[data-action='cancel-send']"); self.settle(pg)
        self.assertFalse(self.box(pg)); self.assertEqual(len(self.dispatches()), 0, "S2 取消不得发出")
        # S3 确认条出现后改名单，再点「仍要发送」→ 不得按旧确认发出
        pg.click("#composerSubmit"); pg.wait_for_selector("[data-send-confirm]", timeout=5000)
        pg.uncheck("[data-agent-pick][value='alpha1']"); self.settle(pg, 300)
        if self.box(pg):
            pg.click("[data-action='confirm-send']"); self.settle(pg)
            self.assertEqual(len(self.dispatches()), 0, "S3 确认后改了名单，旧确认不得放行")
            self.assertTrue(self.box(pg), "S3 应针对新名单重新要求确认")
            pg.click("[data-action='cancel-send']"); self.settle(pg, 300)
        pg.check("[data-agent-pick][value='alpha1']"); self.settle(pg, 300)
        # S4 确认条出现后清空问题再点「仍要发送」（早退），再填回问题、点普通发送 → 必须重新确认
        pg.click("#composerSubmit"); pg.wait_for_selector("[data-send-confirm]", timeout=5000)
        pg.fill("#question", ""); self.settle(pg, 300)
        if self.box(pg):
            pg.click("[data-action='confirm-send']"); self.settle(pg)
        self.assertEqual(len(self.dispatches()), 0, "S4 问题为空不得发出")
        pg.fill("#question", "问题一"); self.settle(pg, 300)
        pg.click("#composerSubmit"); self.settle(pg, 1000)
        self.assertEqual(len(self.dispatches()), 0, "S4 早退留下的旧确认不得复用：普通发送必须重新确认")
        self.assertTrue(self.box(pg), "S4 应重新出现确认条")
        # S5 正常确认 → 只发一条，名单完整（含离线者）
        pg.click("[data-action='confirm-send']")
        deadline = time.time() + 8
        while time.time() < deadline and not self.dispatches():
            time.sleep(0.2)
        ds = self.dispatches()
        self.assertEqual(len(ds), 1, "S5 确认后恰好发一条")
        self.assertEqual(sorted(ds[0]["payload"]["peers"]), ["alpha1", "off1"])
        self.assertEqual(ds[0]["payload"]["question"], "问题一")
        bad = [e for e in self.errs if "favicon" not in e]
        self.assertEqual(bad, [], f"页面 console 错误：{bad}")
        pg.close()

    def test_2_project_team_default_roundtrip(self):
        # 全员离线的「项目团队」作默认值：发起页缺省勾选项目成员，不偷换成全员在线
        pg = self.page("compose", "p2")
        pg.wait_for_selector("#question", timeout=10000)
        self.assertEqual(self.checked(pg), ["off1"], "项目团队全员离线时默认值仍是项目团队成员")
        pg.close()
        # 表单保存「项目团队」→ 落盘同名 → 重开表单仍选中它
        pg = self.page("overview", "p1")
        pg.wait_for_selector("#service", timeout=10000)
        pg.click("[data-action='edit-project']:visible")
        pg.wait_for_selector("#projectDefaultTeam", timeout=8000)
        opts = pg.eval_on_selector_all("#projectDefaultTeam option", "els => els.map(e => e.value)")
        self.assertIn(PROJECT_TEAM, opts); self.assertIn("全员在线", opts); self.assertIn("混合组", opts)
        pg.select_option("#projectDefaultTeam", PROJECT_TEAM)
        pg.click("form[data-form='project'] button[type='submit']")
        deadline = time.time() + 8
        while time.time() < deadline and P.load_project("p1").get("default_team") != PROJECT_TEAM:
            time.sleep(0.2)
        self.assertEqual(P.load_project("p1").get("default_team"), PROJECT_TEAM, "保存「项目团队」后落盘同名")
        P._STATE_CACHE.update({"at": 0.0, "data": None}); pg.reload(wait_until="networkidle"); pg.wait_for_selector("#service", timeout=10000)
        pg.click("[data-action='edit-project']:visible")
        pg.wait_for_selector("#projectDefaultTeam", timeout=8000)
        self.assertEqual(pg.eval_on_selector("#projectDefaultTeam", "s => s.value"), PROJECT_TEAM, "重开表单读回同义")
        pg.close()

    def test_3_no_edit_window_during_upload(self):
        """sota2 审查 4837：确认通过后上传附件期间改名单/问题，不得按改后的内容无确认发出。"""
        import base64
        png = os.path.join(self.tmp, "dot.png")
        with open(png, "wb") as fh:
            fh.write(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
        held = []
        pg = self.page("compose", "p1")
        pg.route("**/api/upload", lambda route: held.append(route))
        pg.wait_for_selector("#question", timeout=10000)
        def attach():
            with pg.expect_file_chooser() as fc:
                pg.click("[data-action='pick-images']")
            fc.value.set_files(png); self.settle(pg, 400)
        def release(required=True):
            deadline = time.time() + (5 if required else 1)
            while time.time() < deadline and not held:
                pg.wait_for_timeout(100)
            if required:
                self.assertTrue(held, "前置：上传请求应被拦住")
            while held:
                held.pop().continue_()
        base = len(self.dispatches())
        # S6 确认 → 上传挂起期间去掉一个收件人 → 放行上传 → 不得发出
        # 不依赖默认团队（test_2 会把 p1 默认改成「项目团队」，只展开在线成员）：显式勾上在线 + 离线各一人
        for v in ("alpha1", "off1"):
            pg.check(f"[data-agent-pick][value='{v}']")
        self.settle(pg, 300)
        self.assertEqual(self.checked(pg), ["alpha1", "off1"], "前置：名单含离线者，发送前必须确认")
        pg.fill("#question", "问题二"); attach()
        pg.click("#composerSubmit"); pg.wait_for_selector("[data-send-confirm]", timeout=5000)
        pg.click("[data-action='confirm-send']"); pg.wait_for_timeout(300)
        pg.uncheck("[data-agent-pick][value='alpha1']"); self.settle(pg, 300)
        release(); self.settle(pg, 1500)
        self.assertEqual(len(self.dispatches()), base, "S6 上传期间改了名单，不得按改后的名单无确认发出")
        # S7 恢复名单，正常确认、上传期间不改 → 恰好发一条，带附件，内容为获准时的内容
        pg.check("[data-agent-pick][value='alpha1']"); self.settle(pg, 300)
        pg.click("#composerSubmit"); pg.wait_for_selector("[data-send-confirm]", timeout=5000)
        pg.click("[data-action='confirm-send']")
        release(required=False)   # S6 已上传成功的附件不会重传
        deadline = time.time() + 8
        while time.time() < deadline and len(self.dispatches()) == base:
            time.sleep(0.2)
        ds = sorted(self.dispatches(), key=lambda c: c.get("id", ""))
        self.assertEqual(len(ds), base + 1, "S7 正常确认后恰好发一条")
        pl = ds[-1]["payload"]
        self.assertEqual((sorted(pl["peers"]), pl["question"]), (["alpha1", "off1"], "问题二"))
        self.assertEqual(len(pl.get("attachments") or []), 1, "S7 附件随派发带上")
        bad = [e for e in self.errs if "favicon" not in e]
        self.assertEqual(bad, [], f"页面 console 错误：{bad}")
        pg.close()

    def test_4_ttl_change_requires_reconfirm(self):
        """sota2 0e85：时限也属于派发内容——确认后 / 上传期间改时限，不得按旧时限静默发出。"""
        import base64
        png = os.path.join(self.tmp, "dot4.png")
        with open(png, "wb") as fh:
            fh.write(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
        held = []
        pg = self.page("compose", "p1")
        pg.route("**/api/upload", lambda route: held.append(route))
        pg.wait_for_selector("#question", timeout=10000)
        for v in ("alpha1", "off1"):
            pg.check(f"[data-agent-pick][value='{v}']")
        pg.fill("#question", "问题四"); pg.fill("#ttl", "30"); self.settle(pg, 300)
        with pg.expect_file_chooser() as fc:
            pg.click("[data-action='pick-images']")
        fc.value.set_files(png); self.settle(pg, 400)
        base = len(self.dispatches())
        # S8 确认 → 上传挂起期间把时限 30 改成 60 → 放行 → 不得发出
        pg.click("#composerSubmit"); pg.wait_for_selector("[data-send-confirm]", timeout=5000)
        pg.click("[data-action='confirm-send']"); pg.wait_for_timeout(300)
        pg.fill("#ttl", "60"); self.settle(pg, 300)
        deadline = time.time() + 5
        while time.time() < deadline and not held:
            pg.wait_for_timeout(100)
        self.assertTrue(held, "前置：上传请求应被拦住")
        while held:
            held.pop().continue_()
        self.settle(pg, 1500)
        self.assertEqual(len(self.dispatches()), base, "S8 上传期间改了时限，不得发出")
        # S9 出现确认条后改时限 → 点「仍要发送」只会重新要求确认；再确认才按新时限发出
        pg.click("#composerSubmit"); pg.wait_for_selector("[data-send-confirm]", timeout=5000)
        pg.fill("#ttl", "45"); self.settle(pg, 300)
        pg.click("[data-action='confirm-send']"); self.settle(pg, 1200)
        self.assertEqual(len(self.dispatches()), base, "S9 确认后改了时限，旧凭据不得放行")
        pg.wait_for_selector("[data-send-confirm]", timeout=5000)
        pg.click("[data-action='confirm-send']")
        deadline = time.time() + 8
        while time.time() < deadline and len(self.dispatches()) == base:
            time.sleep(0.2)
        ds = sorted(self.dispatches(), key=lambda c: c.get("id", ""))
        self.assertEqual(len(ds), base + 1, "S9 重新确认后恰好发一条")
        self.assertEqual(ds[-1]["payload"].get("ttl_min"), 45, "按界面上的新时限发出")
        bad = [e for e in self.errs if "favicon" not in e]
        self.assertEqual(bad, [], f"页面 console 错误：{bad}")
        pg.close()

    def test_5_draft_replaced_during_upload(self):
        """sota3 4485：上传挂起期间切换项目 / 清空草稿 → 旧草稿不得发出。"""
        import base64
        png = os.path.join(self.tmp, "dot5.png")
        with open(png, "wb") as fh:
            fh.write(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
        held = []
        pg = self.page("compose", "p1")
        pg.route("**/api/upload", lambda route: held.append(route))
        def start(question):
            pg.wait_for_selector("#question", timeout=10000)
            for v in ("alpha1", "off1"):
                pg.check(f"[data-agent-pick][value='{v}']")
            pg.fill("#question", question); self.settle(pg, 300)
            with pg.expect_file_chooser() as fc:
                pg.click("[data-action='pick-images']")
            fc.value.set_files(png); self.settle(pg, 400)
            pg.click("#composerSubmit"); pg.wait_for_selector("[data-send-confirm]", timeout=5000)
            pg.click("[data-action='confirm-send']")
            deadline = time.time() + 5
            while time.time() < deadline and not held:
                pg.wait_for_timeout(100)
            self.assertTrue(held, "前置：上传请求应被拦住")
        def release():
            while held:
                held.pop().continue_()
            self.settle(pg, 1500)
        base = len(self.dispatches())
        # S10 上传挂起 → 切到 p2 并写新问题 → 放行 → 不得向 p1 发出旧草稿
        start("old p1 draft")
        pg.select_option("#projectSelect", "p2"); self.settle(pg, 600)
        self.assertEqual(pg.eval_on_selector("#projectSelect", "e => e.value"), "p2")
        if pg.query_selector("#question"):
            pg.fill("#question", "new p2 draft")
        release()
        self.assertEqual(len(self.dispatches()), base, "S10 上传期间切了项目，旧草稿不得发出")
        # S11 回到 p1 重新起草 → 上传挂起 → 清空草稿 → 放行 → 不得发出
        pg.select_option("#projectSelect", "p1"); self.settle(pg, 600)
        start("p1 draft to clear")
        pg.click("[data-action='clear-composer']"); self.settle(pg, 300)
        btn = pg.locator("dialog button, [role=dialog] button, .dialog button").filter(has_text="清空")
        if btn.count():
            btn.last.click(); self.settle(pg, 300)
        release()
        self.assertEqual(len(self.dispatches()), base, "S11 上传期间清空了草稿，不得发出")
        bad = [e for e in self.errs if "favicon" not in e]
        self.assertEqual(bad, [], f"页面 console 错误：{bad}")
        pg.close()

    def test_6_roster_swapped_to_another_offline_during_upload(self):
        """sota1 复现（合并稿 9e38 第 2 条的窗口）：点「仍要发送」获准之后、附件上传完成之前，
        把名单换成另一个离线者 off2 —— 既不得按新名单静默发出，也必须在再次点击时重新要求确认。"""
        import base64
        png = os.path.join(self.tmp, "dot6.png")
        with open(png, "wb") as fh:
            fh.write(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
        # 再造一个离线者：登记心跳后把时间戳拨回过去，它会出现在选人名里且判离线
        B.heartbeat("off2", "idle", "m-o2")
        f2 = os.path.join(B.PEERS, "off2.json"); d2 = B.read_json(f2, {}) or {}
        for k in [k for k in d2 if k in ("last_seen", "updated", "time", "ts")]:
            d2[k] = "2026-01-01T00:00:00"
        B.write_json(f2, d2)
        B.write_json(B._beat_path("off2"), {"at": "2026-01-01T00:00:00", "activity": "idle", "task_id": ""})   # 总线心跳记录同步拨回
        held = []
        pg = self.page("compose", "p1")
        pg.route("**/api/upload", lambda route: held.append(route))
        pg.wait_for_selector("#question", timeout=10000)

        def release(required=True):
            deadline = time.time() + (5 if required else 1)
            while time.time() < deadline and not held:
                pg.wait_for_timeout(100)
            if required:
                self.assertTrue(held, "前置：上传请求应被拦住")
            while held:
                held.pop().continue_()

        base = len(self.dispatches())
        # 获准：alpha1 在线 + off1 离线 → 必须先出提示条，点「仍要发送」才继续
        for v in ("alpha1", "off1"):
            pg.check(f"[data-agent-pick][value='{v}']")
        self.settle(pg, 300)
        pg.fill("#question", "问题六")
        with pg.expect_file_chooser() as fc:
            pg.click("[data-action='pick-images']")
        fc.value.set_files(png); self.settle(pg, 400)
        pg.click("#composerSubmit"); pg.wait_for_selector("[data-send-confirm]", timeout=5000)
        pg.click("[data-action='confirm-send']"); self.settle(pg, 300)
        self.assertEqual(len(self.dispatches()), base, "前置：上传挂起期间还没有任何指令落地")
        # 获准之后、上传放行之前：名单换成另一个离线者 off2
        pg.uncheck("[data-agent-pick][value='off1']")
        pg.check("[data-agent-pick][value='off2']")
        self.settle(pg, 300)
        self.assertEqual(self.checked(pg), ["alpha1", "off2"], "前置：名单已换成 off2")
        release(); self.settle(pg, 1500)
        # S12 中止：既不发出，也不留下可复用的确认凭据
        self.assertEqual(len(self.dispatches()), base, "S12 获准后上传期间换了名单，不得按新名单静默发出")
        self.assertFalse(self.box(pg), "S12 中止后不应残留确认条")
        # 再点一次：必须重新要求确认（「新增确认次数 0」正是缺陷本体的判据）
        pg.click("#composerSubmit")
        pg.wait_for_selector("[data-send-confirm]", timeout=5000)
        self.assertTrue(self.box(pg), "S12 中止后再点发送必须重新确认")
        # 重新点「仍要发送」之后应恰好发一条，名单是重新确认那一刻的 alpha1 + off2
        pg.click("[data-action='confirm-send']")
        release(required=False)
        deadline = time.time() + 8
        while time.time() < deadline and len(self.dispatches()) == base:
            time.sleep(0.2)
        ds = sorted(self.dispatches(), key=lambda c: c.get("id", ""))
        self.assertEqual(len(ds), base + 1, "S12 重新确认后恰好发一条")
        pl = ds[-1]["payload"]
        self.assertEqual(sorted(pl["peers"]), ["alpha1", "off2"], "S12 发出的是重新确认时的名单")
        self.assertEqual(pl["question"], "问题六")
        bad = [e for e in self.errs if "favicon" not in e]
        self.assertEqual(bad, [], f"页面 console 错误：{bad}")
        pg.close()

if __name__ == "__main__":
    unittest.main(verbosity=2)
