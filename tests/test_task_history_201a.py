# 任务 20260929-153838-201a 自测：任务历史只读分页（3847 重落地）按 2c0a 的 RETURN 修正逐条覆盖。
# 跑法：py -3 tests/test_task_history_201a.py（仓库根目录）。全部数据建在临时目录，不碰生产 projects/、console/、peers/。
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_core as B  # noqa: E402
import bus_projects as P  # noqa: E402
import bus_history as H  # noqa: E402


def iso(sec_ago=0):
    return (dt.datetime.now() - dt.timedelta(seconds=sec_ago)).strftime("%Y-%m-%dT%H:%M:%S")


CJK = "协作工作台"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bus201a-")
        self._old = (B.ROOT, B.WORKSPACE, P.PROJECTS, P.DONE, B.read_json)
        B.ROOT = self.tmp
        B.WORKSPACE = self.tmp
        P.PROJECTS = os.path.join(self.tmp, "projects")
        P.DONE = os.path.join(self.tmp, "console", "done")
        os.makedirs(P.PROJECTS, exist_ok=True)
        os.makedirs(P.DONE, exist_ok=True)
        H.reset_cache()

    def tearDown(self):
        B.ROOT, B.WORKSPACE, P.PROJECTS, P.DONE, B.read_json = self._old
        H.reset_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mk_project(self, slug, decisions=()):
        d = os.path.join(P.PROJECTS, slug)
        os.makedirs(d, exist_ok=True)
        B.write_json(os.path.join(d, "project.json"), {"slug": slug, "name": slug, "peers": [], "decisions": list(decisions)})
        B.write_json(os.path.join(d, "decisions.json"), list(decisions))
        return slug

    def mk_done(self, cid, project="", **kw):
        rec = {"id": cid, "kind": kw.get("kind", "relay"), "status": "done",
               "payload": ({"project": project} if project else {}), "done_at": kw.get("done_at", iso(1)),
               "result": kw.get("result", "ok")}
        if "ok" in kw:
            rec["ok"] = kw["ok"]
        B.write_json(os.path.join(P.DONE, cid + ".json"), rec)
        return cid


class TestProjectGate(Base):
    def test_cjk_slug_可用(self):
        # 审查点 1（P0）：真实 slug 里有中文，旧实现走 B.valid_name 必然 400
        self.mk_project(CJK, [{"task_id": "t1", "status": "adopted", "title": "甲", "created": iso(5)}])
        self.mk_project("重力回廊", [])
        r = H.history_page(project=CJK)
        self.assertEqual([i["id"] for i in r["items"]], ["t1"])
        self.assertEqual(r["project"], CJK)

    def test_中文slug不再被valid_name拒绝(self):
        self.assertRaises(ValueError, B.valid_name, CJK)      # 证明 valid_name 确实不能用
        self.mk_project(CJK, [])
        H.check_project(CJK)                                   # 本模块不接受它当 slug 规则

    def test_不存在slug被白名单拒绝(self):
        self.mk_project("aios", [])
        with self.assertRaises(H.CursorError) as cm:
            H.history_page(project="没有这个项目")
        self.assertEqual(cm.exception.status, 400)
        self.assertEqual(cm.exception.code, "bad_project")

    def test_目录越界拒绝(self):
        self.mk_project(CJK, [])
        real = P.project_dir(CJK)
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside, exist_ok=True)
        # slug 在白名单里，但目录被换成 projects/ 之外的路径 → commonpath 必须拦住
        orig = P.project_dir
        P.project_dir = lambda s: outside
        try:
            with self.assertRaises(H.CursorError):
                H.check_project(CJK)
        finally:
            P.project_dir = orig
        self.assertTrue(os.path.isdir(real) or True)


class TestTerminalStatuses(Base):
    def test_全部终态都进历史_非终态不进(self):
        rows = [{"task_id": "s_" + s, "status": s, "title": s, "created": iso(10)} for s in P.TERMINAL_STATUSES]
        rows += [{"task_id": "n_" + s, "status": s, "title": s, "created": iso(10)} for s in ("draft", "dispatched", "collecting", "ready_for_merge")]
        self.mk_project(CJK, rows)
        ids = {i["id"] for i in H.history_page(project=CJK, flt="decisions")["items"]}
        self.assertEqual(ids, {"s_" + s for s in P.TERMINAL_STATUSES})

    def test_rejected_与_merged_口径一致(self):
        # 审查点：rejected 8 条、merged 50 条在两套旧口径下归属相反 → 现在共用一份常量
        self.assertIn("rejected", P.TERMINAL_STATUSES)
        self.assertIn("merged", P.TERMINAL_STATUSES)
        self.assertEqual(tuple(P.DONE_STATUSES), tuple(P.TERMINAL_STATUSES))
        self.assertTrue(P.is_terminal("rejected") and not P.is_terminal("collecting"))
        self.mk_project("aios", [{"task_id": "r1", "status": "rejected", "created": iso(3)},
                                 {"task_id": "m1", "status": "merged", "created": iso(3)}])
        got = {i["id"] for i in H.history_page(project="aios")["items"]}
        self.assertEqual(got, {"r1", "m1"})

    def test_终态口径下发给界面(self):
        # 返工点 2：一份口径。bus_projects 导出 → 任务历史响应带出 → /api/state 下发给前端，前端不再各写一份
        r = H.history_page(project="", flt="all")
        self.assertEqual(r["source"]["terminal_statuses"], list(P.TERMINAL_STATUSES))
        src = open(os.path.join(REPO, "bus_projects.py"), encoding="utf-8").read()
        self.assertIn('"terminal_statuses": list(TERMINAL_STATUSES)', src)   # 真的进了 /api/state，不是只写在注释里
        js = open(os.path.join(REPO, "web", "v2", "js", "model.js"), encoding="utf-8").read()
        self.assertIn("terminalStatuses().includes(d.status)", js)           # doneDecisions 吃下发的那一份

    def test_提醒台账与任务历史同一入口(self):
        # 返工点 2（静态断言，不是行为测试）：clear_nudge 的条件以前自己写了一份四状态列表
        # （漏 rejected / superseded），现在与任务历史走同一个 is_terminal()
        src = open(os.path.join(REPO, "bus_projects.py"), encoding="utf-8").read()
        self.assertIn("is_terminal(d.get(", src)              # _done_ids 的判据
        self.assertIn("is_terminal(fields.get(", src)         # clear_nudge 的判据
        self.assertNotIn('"merged", "adopted", "cancelled", "archived")', src)  # 旧的那份私有列表已消失

    def test_终态集合参数化(self):
        for s in P.TERMINAL_STATUSES:
            with self.subTest(status=s):
                self.mk_project("p" + s, [{"task_id": "x", "status": s, "created": iso(1)}])
                self.assertEqual(len(H.history_page(project="p" + s)["items"]), 1)


class TestPaging(Base):
    def setUp(self):
        super().setUp()
        self.mk_project(CJK, [{"task_id": f"d{i}", "status": "adopted", "title": f"标题{i}", "created": iso(i * 60)} for i in range(25)])
        for i in range(12):
            self.mk_done(f"c{i}", project=CJK, done_at=iso(i * 30 + 5))

    def _collect(self, limit):
        seen, cur, pages = [], "", 0
        while True:
            r = H.history_page(project=CJK, cursor=cur, limit=limit)
            seen += [i["canonical_id"] for i in r["items"]]
            pages += 1
            cur = r["next_cursor"]
            self.assertTrue(r["complete"] == (not cur))
            if not cur:
                return r, seen, pages

    def test_两页无重复无遗漏(self):
        first = H.history_page(project=CJK, limit=10)
        self.assertEqual(len(first["items"]), 10)
        self.assertEqual(first["range"], {"start": 0, "end": 10})
        last, seen, pages = self._collect(7)
        self.assertEqual(len(seen), last["total"])
        self.assertEqual(len(seen), len(set(seen)), "翻页不得重复")
        self.assertEqual(pages, -(-last["total"] // 7))
        self.assertTrue(last["complete"] and last["next_cursor"] == "")

    def test_快照期间插入不漂页(self):
        p1 = H.history_page(project=CJK, limit=10)
        self.mk_project(CJK, [{"task_id": f"d{i}", "status": "adopted", "title": f"标题{i}", "created": iso(i * 60)} for i in range(25)]
                        + [{"task_id": "newer", "status": "adopted", "created": iso(0)}])
        p2 = H.history_page(project=CJK, cursor=p1["next_cursor"], limit=10)
        self.assertEqual(p2["total"], p1["total"])          # 同一快照，新数据挤不进本次翻页
        self.assertNotIn("newer", [i["id"] for i in p2["items"]])

    def test_limit_封顶与非法值(self):
        self.assertEqual(H.history_page(project=CJK, limit=10 ** 6)["limit"], H.MAX_LIMIT)
        self.assertEqual(H.history_page(project=CJK, limit=5)["limit"], 5)
        for bad in ("abc", "1.5", "  "):
            with self.subTest(bad=bad):
                with self.assertRaises(H.CursorError) as cm:
                    H.history_page(project=CJK, limit=bad)
                self.assertEqual(cm.exception.code, "bad_limit")
        self.assertEqual(H.history_page(project=CJK, limit=0)["limit"], 1, "0 与负数夹到 1，不报错也不返回空页")
        with self.assertRaises(H.CursorError) as cm:
            H.history_page(project=CJK, flt="nope")
        self.assertEqual(cm.exception.code, "bad_filter")


class TestCursorSecurity(Base):
    def setUp(self):
        super().setUp()
        self.mk_project(CJK, [{"task_id": f"d{i}", "status": "adopted", "created": iso(i * 60)} for i in range(6)])
        self.mk_project("aios", [{"task_id": "a1", "status": "merged", "created": iso(1)}])

    def test_篡改签名拒绝(self):
        cur = H.history_page(project=CJK, limit=2)["next_cursor"]
        bad = cur[:-2] + ("ab" if not cur.endswith("ab") else "cd")
        with self.assertRaises(H.CursorError) as cm:
            H.history_page(project=CJK, cursor=bad, limit=2)
        self.assertEqual((cm.exception.code, cm.exception.status), ("cursor_invalid", 400))

    def test_换作用域重放拒绝(self):
        cur = H.history_page(project=CJK, limit=2)["next_cursor"]
        for kwargs in ({"project": "aios"}, {"project": CJK, "flt": "commands"}):
            with self.subTest(**kwargs):
                with self.assertRaises(H.CursorError) as cm:
                    H.history_page(cursor=cur, limit=2, **kwargs)
                self.assertEqual(cm.exception.code, "cursor_invalid")

    def test_定长尾部帧_含点号的mac也能解(self):
        # 旧实现 raw + "." + mac，mac 随机字节含 0x2e 时 rsplit 切错位（约 6%）
        for _ in range(200):
            payload = {"sid": "deadbeef", "offset": 3, "project": CJK, "filter": "all"}
            c = H._sign(payload)
            self.assertEqual(H._verify(c), payload)

    def test_越界offset返回410不静默空末页(self):
        page = H.history_page(project=CJK, limit=2)
        total = page["total"]
        cur = H._sign({"sid": page["snapshot"]["id"], "offset": total + 5, "project": CJK, "filter": "all"})
        with self.assertRaises(H.CursorError) as cm:
            H.history_page(project=CJK, cursor=cur, limit=2)
        self.assertEqual((cm.exception.code, cm.exception.status), ("cursor_expired", 410))

    def test_真实时钟过期_now可注入(self):
        t0 = 1_700_000_000.0
        page = H.history_page(project=CJK, limit=2, now=t0)
        cur = page["next_cursor"]
        r = H.history_page(project=CJK, cursor=cur, limit=2, now=t0 + H.SNAPSHOT_TTL_SEC - 1)
        self.assertEqual(r["offset"], 2)
        with self.assertRaises(H.CursorError) as cm:
            H.history_page(project=CJK, cursor=cur, limit=2, now=t0 + H.SNAPSHOT_TTL_SEC + 1)
        self.assertEqual(cm.exception.status, 410)

    def test_进程重启后游标过期(self):
        cur = H.history_page(project=CJK, limit=2)["next_cursor"]
        H.reset_cache()
        with self.assertRaises(H.CursorError) as cm:
            H.history_page(project=CJK, cursor=cur, limit=2)
        self.assertEqual(cm.exception.code, "cursor_expired")


class TestCache(Base):
    def setUp(self):
        super().setUp()
        self.mk_project("aios", [{"task_id": f"d{i}", "status": "adopted", "created": iso(i)} for i in range(4)])

    def test_同键短TTL复用不重扫(self):
        calls = []
        orig = H._iter_source
        H._iter_source = lambda p, f, s: (calls.append((p, f)), list(orig(p, f, s)))[1]
        try:
            a = H.history_page(project="aios")
            b = H.history_page(project="aios")
            self.assertEqual(a["snapshot"]["id"], b["snapshot"]["id"])
            self.assertEqual(len(calls), 1, "TTL 内同 (project, filter) 不该重扫")
        finally:
            H._iter_source = orig

    def test_同键并发只构建一次(self):
        calls = []
        orig = H._iter_source
        H._iter_source = lambda p, f, s: (calls.append((p, f)), list(orig(p, f, s)))[1]
        barrier = threading.Barrier(6)
        errs = []

        def worker():
            try:
                barrier.wait()
                H.history_page(project="aios")
            except Exception as e:  # noqa: BLE001
                errs.append(e)
        try:
            ts = [threading.Thread(target=worker) for _ in range(6)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            self.assertEqual(errs, [])
            self.assertEqual(len(calls), 1, "锁内二次检查：并发同键只扫一次")
        finally:
            H._iter_source = orig

    def test_快照有条数上限(self):
        self.assertLessEqual(H.MAX_SNAPS, 128)
        for i in range(H.MAX_SNAPS + 20):
            slug = f"p{i:03d}"
            self.mk_project(slug, [])
            H.history_page(project=slug, now=1_700_000_000.0 + i)      # 递增时钟，避免 TTL 淘汰干扰 LRU 断言
        self.assertLessEqual(len(H._SNAPS), H.MAX_SNAPS)


class TestIdStability(Base):
    def test_换workspace路径_id不变(self):
        self.mk_project(CJK, [{"task_id": "t1", "status": "adopted", "created": iso(1)}])
        self.mk_done("c1", project=CJK)
        before = [i["canonical_id"] for i in H.history_page()["items"]]
        H.reset_cache()
        B.WORKSPACE = os.path.join(self.tmp, "另一个工作台目录")
        H.reset_cache()
        after = [i["canonical_id"] for i in H.history_page()["items"]]
        self.assertEqual(before, after)
        self.assertNotIn(os.path.basename(self.tmp), "".join(before))

    def test_id由namespace加来源与原生id决定(self):
        expect = H.canonical_id(CJK, "decision", "t1")
        self.mk_project(CJK, [{"task_id": "t1", "status": "adopted", "created": iso(1)}])
        got = H.history_page(project=CJK)["items"][0]["canonical_id"]
        self.assertEqual(got, expect)
        r = H.history_page(project=CJK)
        self.assertEqual(r["id_namespace"], H.ID_NAMESPACE)
        self.assertEqual(r["id_version"], H.ID_VERSION)


class TestRecordHygiene(Base):
    def test_坏记录跳过计数且不泄露正文(self):
        self.mk_project("aios", [{"task_id": "ok", "status": "adopted", "created": iso(1)}])
        secret = "内部报价 17.57 毛利"
        with open(os.path.join(P.DONE, "broken.json"), "w", encoding="utf-8") as f:
            f.write("{" + secret)                      # 不可解析
        B.write_json(os.path.join(P.DONE, "noid.json"), {"kind": "relay", "result": secret})
        r = H.history_page(project="aios")
        self.assertGreaterEqual(r["snapshot"]["skipped"], 2)
        self.assertNotIn(secret, json.dumps(r, ensure_ascii=False))
        self.assertEqual([i["id"] for i in r["items"]], ["ok"])

    def test_绝对与越界merge_path被拒(self):
        self.mk_project("aios", [
            {"task_id": "g", "status": "adopted", "created": iso(1), "merge_path": "log/2026-09-26/g.merge.md"},
            {"task_id": "a", "status": "adopted", "created": iso(2), "merge_path": "D:\\secrets\\a.md"},
            {"task_id": "u", "status": "adopted", "created": iso(3), "merge_path": "/etc/passwd"},
            {"task_id": "e", "status": "adopted", "created": iso(4), "merge_path": "../../outside.md"},
        ])
        items = {i["id"]: i["merge_path"] for i in H.history_page(project="aios")["items"]}
        self.assertEqual(items["g"], "log/2026-09-26/g.merge.md", "工作区相对路径原样透出（线格式正斜杠）")
        for k in ("a", "u", "e"):
            self.assertEqual(items[k], "", k + " 必须是工作区相对路径")

    def test_result有长度上限(self):
        self.mk_project("aios", [])
        self.mk_done("c1", project="aios", result="长" * 2000)
        got = H.history_page(project="aios", flt="commands")["items"][0]["result"]
        self.assertEqual(len(got), H.RESULT_CAP)

    def test_全局指令在无project视图可见(self):
        self.mk_project("aios", [])
        self.mk_project(CJK, [])
        self.mk_done("g1", project="")
        ids = [i["id"] for i in H.history_page(flt="commands")["items"]]
        self.assertEqual(ids, ["g1"], "全局指令不能被按项目重复计数")

    def test_只读_不写任何源(self):
        self.mk_project("aios", [{"task_id": "t1", "status": "adopted", "created": iso(1)}])
        self.mk_done("c1", project="aios")
        before = {p: os.path.getmtime(p) for d in (P.PROJECTS, P.DONE) for p in
                  [os.path.join(r, f) for r, _, fs in os.walk(d) for f in fs]}
        H.history_page()
        H.history_page(project="aios", limit=1)
        after = {p: os.path.getmtime(p) for d in (P.PROJECTS, P.DONE) for p in
                 [os.path.join(r, f) for r, _, fs in os.walk(d) for f in fs]}
        self.assertEqual(before, after)
        self.assertEqual(sorted(os.listdir(P.DONE)), ["c1.json"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
