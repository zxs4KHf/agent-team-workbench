# ac1a R4 必修1 自测：_full_state 的 dirty 落盘不得把派生键 lineage / bucket 写进 decisions.json。
# 覆盖：派发投影仍带全两键（前端 stateKey / 四分页要用）、真实业务字段与状态同步照常落盘、
#       历史泄漏文件在下次真实写入时规范化、只读 GET 不改盘（不跑生产迁移）、剥离用的是副本而非原地 pop。
# 跑法：py -3 -X utf8 tests/test_derived_keys_ac1a.py（仓库根目录；全程临时目录，不碰生产 projects/）
import json, os, shutil, sys, tempfile, time, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_core as B       # noqa: E402
import bus_lineage as L    # noqa: E402
import bus_projects as P   # noqa: E402

TID = "20260924-022708-ac1a"
FULL_HANDOFF = {"user_step": "双击 tools\\run.bat", "reason": "需要管理员权限", "verify": "8791 返回 200", "evidence": ["commit 1234567"], "state": "ready"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="busac1a-")
        self._saved = (B.ROOT, B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.LOCK, B.PRIORITY, B.WORKSPACE, P.PROJECTS, P.COMMANDS, P.DONE)
        B.ROOT = B.WORKSPACE = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.LOCK = os.path.join(self.tmp, "console", "controller.lock")
        B.PRIORITY = os.path.join(self.tmp, "console", "priority.json")
        P.PROJECTS, P.COMMANDS, P.DONE = (os.path.join(self.tmp, *d) for d in (("projects",), ("console", "commands"), ("console", "done")))
        for d in (B.PEERS, B.INBOX, B.OUTBOX, B.LOG, P.PROJECTS, P.COMMANDS, P.DONE, os.path.join(self.tmp, "console")):
            os.makedirs(d, exist_ok=True)
        self.proj = P.create_project("验收项目", ["glm1"], "independent", [])
        self.file = os.path.join(P.PROJECTS, self.proj["slug"], "decisions.json")

    def tearDown(self):
        (B.ROOT, B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.LOCK, B.PRIORITY, B.WORKSPACE,
         P.PROJECTS, P.COMMANDS, P.DONE) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seed(self, status="dispatched"):
        # 真实持久字段一起写：派生键剥离不能顺手剥掉谱系与交接
        P.upsert_decision(self.proj["slug"], TID, status=status, title="修复派生键落盘", topic="前端闭环", root_task=TID,
                          parent_task="20260924-020829-5556", round=4, kind="fix", handoff=dict(FULL_HANDOFF),
                          children=["20260924-030000-beef"], needs_decision=False, merge_path="log/x.md")

    def raw(self):
        with open(self.file, encoding="utf-8") as f:
            return f.read()

    def rec_from_file(self):
        return json.loads(self.raw())[0]

    def decision_from_state(self, st):
        proj = next(p for p in st["projects"] if p["slug"] == self.proj["slug"])
        return next(d for d in proj["decisions"] if d["task_id"] == TID)


class TestDirtySaveStripsDerived(Base):
    def test_dirty_full_state_keeps_disk_clean_and_response_full(self):
        self.seed()
        st = P._full_state()          # outbox 无任务包 → 状态同步 → dirty → 落盘
        rec = self.decision_from_state(st)
        self.assertIn("lineage", rec, "响应投影必须带 lineage，前端四分页与显示标题要用")
        self.assertEqual(rec["bucket"], "", "非采纳项的 bucket 口径为空，但键必须在")
        self.assertEqual(rec["status"], "cancelled", "僵尸行状态同步是本轮真实业务更新")
        raw = self.raw()
        self.assertNotIn('"lineage"', raw, "派生键不得落盘")
        self.assertNotIn('"bucket"', raw, "派生键不得落盘")
        on = self.rec_from_file()
        self.assertEqual(on["status"], "cancelled")
        self.assertTrue(on["summary"] and on["history"], "状态同步的摘要与历史要保留")
        for k in ("topic", "root_task", "parent_task", "round", "kind", "handoff", "children"):
            self.assertIn(k, on, "真实持久字段不得被剥离")
        self.assertEqual(on["round"], 4)
        self.assertEqual(on["children"], ["20260924-030000-beef"])
        self.assertEqual(on["handoff"]["user_step"], FULL_HANDOFF["user_step"])

    def test_adopted_dirty_bucket_user_persists_without_bucket(self):
        self.seed("adopted")
        P.upsert_decision(self.proj["slug"], TID, status="adopted", execution="pending", needs_decision=True)
        st = P._full_state()
        rec = self.decision_from_state(st)
        self.assertEqual(rec["bucket"], "decide", "等待决策口径")
        self.assertNotIn('"bucket"', self.raw())
        self.assertNotIn('"lineage"', self.raw())
        self.assertTrue(self.rec_from_file()["needs_decision"])


class TestReadOnlyDoesNotMigrate(Base):
    def test_full_state_leaves_polluted_file_untouched(self):
        self.seed("adopted")   # 采纳项不触发 dirty 同步：只读一轮必须一个字都不写
        polluted = [dict(self.rec_from_file(), lineage=L.lineage_of({"task_id": TID}), bucket="agent")]
        B.write_json(self.file, polluted)
        mtime = os.stat(self.file).st_mtime_ns
        st = P._full_state()
        self.assertEqual(os.stat(self.file).st_mtime_ns, mtime, "只读 GET 不写盘，也不做生产迁移")
        self.assertIn("lineage", json.loads(self.raw())[0], "盘上的历史键本轮不主动清理")
        self.assertIn("lineage", self.decision_from_state(st), "响应投影仍按当前真值给出")

    def test_next_real_write_normalizes_legacy_file(self):
        self.seed()
        B.write_json(self.file, [dict(self.rec_from_file(), lineage={"round": 99}, bucket="user", progress={"answered": ["x"]})])
        P.upsert_decision(self.proj["slug"], TID, summary="真实业务写入")
        raw = self.raw()
        self.assertNotIn('"lineage"', raw)
        self.assertNotIn('"bucket"', raw)
        self.assertEqual(self.rec_from_file()["summary"], "真实业务写入")
        self.assertEqual(self.rec_from_file()["round"], 4, "规范化只去派生键")


class TestStripIsCopyNotPop(Base):
    def test_persistable_all_leaves_projection_in_place(self):
        self.seed()
        items = P.decisions(self.proj["slug"])
        for rec in items:
            L.decorate(rec)
        out = L.persistable_all(items)
        self.assertIn("lineage", items[0], "剥离不得原地 pop 掉客户端投影需要的键")
        self.assertIn("bucket", items[0])
        self.assertNotIn("lineage", out[0])
        self.assertNotIn("bucket", out[0])
        self.assertEqual({k: v for k, v in out[0].items() if k not in L.DERIVED_KEYS},
                         {k: v for k, v in items[0].items() if k not in L.DERIVED_KEYS})
        self.assertEqual(L.persistable_all(items)[0]["task_id"], TID)

    def test_derived_keys_are_only_lineage_and_bucket(self):
        self.assertEqual(L.DERIVED_KEYS, ("lineage", "bucket", "progress", "implementation"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
