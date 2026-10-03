# 任务 903f 自测：谱系字段（topic/root_task/parent_task/round/kind）、显示标题、按 root_task 分组、四分页归类、人工交接门槛、关联子单去重 / 限额、旧记录兼容与 stateKey 稳定。
# 跑法：py -3 tests/test_lineage_903f.py（仓库根目录）。全部状态建在临时目录，不碰生产 projects/、console/、peers/。
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_core as B  # noqa: E402
import bus_projects as P  # noqa: E402
import bus_lineage as L  # noqa: E402


def iso(sec_ago=0):
    return (dt.datetime.now() - dt.timedelta(seconds=sec_ago)).strftime("%Y-%m-%dT%H:%M:%S")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bus903f-")
        B.ROOT = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.LOCK = os.path.join(self.tmp, "console", "controller.lock")
        B.PRIORITY = os.path.join(self.tmp, "console", "priority.json")
        P.PROJECTS = os.path.join(self.tmp, "projects")
        P.COMMANDS = os.path.join(self.tmp, "console", "commands")
        P.DONE = os.path.join(self.tmp, "console", "done")
        P.BUILDS = os.path.join(self.tmp, "console", "builds")
        P.ROLES = os.path.join(self.tmp, "console", "roles.json")
        P.NUDGES = os.path.join(self.tmp, "console", "nudges.json")
        for d in (B.PEERS, B.INBOX, B.OUTBOX, B.LOG, P.PROJECTS, P.COMMANDS, P.DONE, P.BUILDS):
            os.makedirs(d, exist_ok=True)
        self._mg = B.model_groups
        B.model_groups = lambda: [{"id": "claude-fable-5", "match": ["claude-fable-5"], "order": 1}]
        B.write_json(P.ROLES, {"assignments": {"builder_groups": ["claude-fable-5"], "reviewer_groups": []}})
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        self.slug = "p1"
        os.makedirs(os.path.join(P.PROJECTS, self.slug))
        B.write_json(os.path.join(P.PROJECTS, self.slug, "project.json"), {"slug": self.slug, "name": "p1", "peers": [], "mode": "independent", "context_files": [], "next": [], "notes": []})
        B.write_json(os.path.join(P.PROJECTS, self.slug, "decisions.json"), [])

    def tearDown(self):
        B.model_groups = self._mg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def decision(self, tid, **kw):
        items = B.read_json(os.path.join(P.PROJECTS, self.slug, "decisions.json"), [])
        items.append({"task_id": tid, "status": "adopted", "title": "旧标题 " + tid, "created": iso(), "history": [], **kw})
        B.write_json(os.path.join(P.PROJECTS, self.slug, "decisions.json"), items)

    def rec(self, tid):
        return next(x for x in P.decisions(self.slug) if x["task_id"] == tid)

    def state_recs(self):
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        proj = next(p for p in P._full_state()["projects"] if p["slug"] == self.slug)
        return proj["decisions"], proj["threads"]


class TestLineage(Base):
    def test_old_record_self_root_and_original_title(self):
        self.decision("20260923-000001-aaaa")
        recs, threads = self.state_recs()
        lin = recs[0]["lineage"]
        self.assertEqual(lin["root_task"], "20260923-000001-aaaa")
        self.assertTrue(lin["self_root"])
        self.assertFalse(lin["has_lineage"])
        self.assertEqual(lin["display_title"], "旧标题 20260923-000001-aaaa")
        self.assertEqual(lin["round"], 0)
        self.assertEqual(threads[0]["count"], 1)

    def test_set_lineage_whitelist_and_title(self):
        self.decision("20260923-000001-aaaa")
        self.decision("20260923-000002-bbbb", status="dispatched")
        L.set_lineage(self.slug, "20260923-000001-aaaa", topic="Antigravity代理", round=1, kind="discuss", evil="x")
        L.set_lineage(self.slug, "20260923-000002-bbbb", parent_task="20260923-000001-aaaa", round=2, kind="build")
        a, b = self.rec("20260923-000001-aaaa"), self.rec("20260923-000002-bbbb")
        self.assertNotIn("evil", a)
        self.assertEqual(L.display_title(a), "Antigravity代理 · R1 · 讨论")
        self.assertEqual(L.display_title(b), "Antigravity代理 · R2 · 施工")  # topic / root 从父单继承
        self.assertEqual(b["root_task"], "20260923-000001-aaaa")
        recs, threads = self.state_recs()
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0]["task_ids"], ["20260923-000001-aaaa", "20260923-000002-bbbb"])
        self.assertEqual(threads[0]["topic"], "Antigravity代理")

    def test_lineage_validation(self):
        self.decision("20260923-000001-aaaa")
        with self.assertRaises(ValueError):
            L.set_lineage(self.slug, "20260923-000001-aaaa", kind="nope")
        with self.assertRaises(ValueError):
            L.set_lineage(self.slug, "20260923-000001-aaaa", round=100)
        with self.assertRaises(ValueError):
            L.set_lineage(self.slug, "20260923-000001-aaaa", parent_task="20260923-000001-aaaa")
        with self.assertRaises(ValueError):
            L.set_lineage(self.slug, "20260923-000001-aaaa", parent_task="20260923-999999-zzzz")
        with self.assertRaises(ValueError):
            L.set_lineage(self.slug, "20260923-000001-aaaa", topic="x" * 25)
        self.assertNotIn("kind", self.rec("20260923-000001-aaaa"))  # 失败不写半截

    def test_implementation_task_id_untouched(self):
        self.decision("20260923-000001-aaaa", implementation_task_id="20260923-000009-bbbb")
        L.set_lineage(self.slug, "20260923-000001-aaaa", topic="T", kind="build", round=3)
        self.assertEqual(self.rec("20260923-000001-aaaa")["implementation_task_id"], "20260923-000009-bbbb")


class TestBuckets(Base):
    def test_default_agent_even_when_builder_offline_or_returned(self):
        self.decision("20260923-000001-aaaa")  # 无 builder 在线、无 build：仍是 Agent 处理中
        self.decision("20260923-000002-bbbb", implementation_task_id="20260923-000003-cccc")
        B.write_json(os.path.join(P.BUILDS, "20260923-000003-cccc.json"), {"task_id": "20260923-000003-cccc", "repo": self.tmp, "base": "abc", "created": iso(120), "status": "building",
                                                                            "teammates": {"f1": {"commit": "abc", "review": "RETURN", "merged": False}}})
        recs, _ = self.state_recs()
        self.assertEqual([r["bucket"] for r in recs], ["agent", "agent"])
        self.assertTrue(recs[0]["implementation"]["blocked"])

    def test_done_user_decide(self):
        self.decision("20260923-000001-aaaa", execution="released")
        self.decision("20260923-000002-bbbb", handoff={"user_step": "双击 run.bat", "reason": "需要管理员权限", "verify": "看到 OK", "evidence": ["commit abc"], "prepared_by": "fable6", "state": "ready"})
        self.decision("20260923-000003-cccc", needs_decision=True)
        self.decision("20260923-000004-dddd", status="dispatched")
        recs, _ = self.state_recs()
        self.assertEqual([r["bucket"] for r in recs], ["done", "user", "decide", ""])

    def test_handoff_requires_all_evidence(self):
        self.decision("20260923-000001-aaaa")
        for bad in ({"user_step": "", "reason": "r", "verify": "v", "evidence": ["e"]},
                    {"user_step": "s", "reason": "", "verify": "v", "evidence": ["e"]},
                    {"user_step": "s", "reason": "r", "verify": "", "evidence": ["e"]},
                    {"user_step": "s", "reason": "r", "verify": "v", "evidence": []}):
            with self.assertRaises(ValueError):
                L.set_handoff(self.slug, "20260923-000001-aaaa", bad["user_step"], bad["reason"], bad["verify"], bad["evidence"], by="fable6")
        self.assertEqual(L.landing_bucket(self.rec("20260923-000001-aaaa")), "agent")
        rec = L.set_handoff(self.slug, "20260923-000001-aaaa", "双击 tools\\run.bat", "需要本机管理员权限写 hosts", "浏览器打开 http://127.0.0.1:8791 返回 200", ["commit 1234567", "tests/x.py 3 OK"], by="fable6")
        self.assertEqual(L.landing_bucket(rec), "user")
        # 半成品字段直接写进记录也进不了「等你一步」
        self.decision("20260923-000002-bbbb", handoff={"user_step": "s", "state": "ready"})
        self.assertEqual(L.landing_bucket(self.rec("20260923-000002-bbbb")), "agent")

    def test_finish_handoff(self):
        self.decision("20260923-000001-aaaa")
        L.set_handoff(self.slug, "20260923-000001-aaaa", "s", "r", "v", ["e"], by="fable6")
        with self.assertRaises(ValueError):
            L.finish_handoff(self.slug, "20260923-000001-aaaa", "maybe")
        rec = L.finish_handoff(self.slug, "20260923-000001-aaaa", "failed", by="user", note="报错")
        self.assertEqual(L.landing_bucket(rec), "agent")
        self.assertEqual(rec["execution"] if "execution" in rec else "pending", "pending")
        L.set_handoff(self.slug, "20260923-000001-aaaa", "s", "r", "v", ["e2"], by="fable6")
        rec = L.finish_handoff(self.slug, "20260923-000001-aaaa", "done")
        self.assertEqual(rec["execution"], "released")
        self.assertEqual(L.landing_bucket(rec), "done")


class TestChild(Base):
    def test_spawn_dedup_and_limits(self):
        self.decision("20260923-000001-aaaa", topic="T", round=1, kind="build", implementation_task_id="20260923-000009-bbbb", execution="pending")
        r1 = L.spawn_child(self.slug, "20260923-000001-aaaa", "merge 冲突需要选方案 A 还是 B", kind="redecide", by="fable6")
        r2 = L.spawn_child(self.slug, "20260923-000001-aaaa", "  MERGE  冲突需要选方案 a 还是 b ", kind="redecide", by="fable6")
        self.assertFalse(r1["dedup"]) and self.assertTrue(r2["dedup"])
        self.assertEqual(r1["task_id"], r2["task_id"])
        child = self.rec(r1["task_id"])
        parent = self.rec("20260923-000001-aaaa")
        self.assertEqual((child["status"], child["round"], child["root_task"], child["parent_task"], child["kind"]), ("draft", 2, "20260923-000001-aaaa", "20260923-000001-aaaa", "redecide"))
        self.assertEqual(parent["children"], [r1["task_id"]])
        self.assertTrue(parent["needs_decision"])
        # 父单施工与落地字段原样保留：不取消原施工
        self.assertEqual(parent["implementation_task_id"], "20260923-000009-bbbb")
        self.assertEqual(parent["execution"], "pending")
        self.assertEqual(L.landing_bucket(parent), "decide")
        L.spawn_child(self.slug, "20260923-000001-aaaa", "第二个不同原因 需要授权", by="x")
        L.spawn_child(self.slug, "20260923-000001-aaaa", "第三个不同原因 影响范围变大", by="x")
        with self.assertRaises(ValueError):
            L.spawn_child(self.slug, "20260923-000001-aaaa", "第四个不同原因 超限", by="x")
        # 未定夺前 resolve 不放行；子单驳回后放行 → 父单回到 Agent 处理中
        self.assertFalse(L.resolve_decision(self.slug, "20260923-000001-aaaa")["ok"])
        for tid in self.rec("20260923-000001-aaaa")["children"]:
            P.upsert_decision(self.slug, tid, status="rejected")
        self.assertTrue(L.resolve_decision(self.slug, "20260923-000001-aaaa")["ok"])
        self.assertEqual(L.landing_bucket(self.rec("20260923-000001-aaaa")), "agent")

    def test_depth_limit_and_closed_parent(self):
        self.decision("20260923-000001-aaaa")
        c1 = L.spawn_child(self.slug, "20260923-000001-aaaa", "原因一 需要决策", by="x")["task_id"]
        P.upsert_decision(self.slug, c1, status="adopted")
        c2 = L.spawn_child(self.slug, c1, "原因二 需要决策", by="x")["task_id"]
        P.upsert_decision(self.slug, c2, status="adopted")
        c3 = L.spawn_child(self.slug, c2, "原因三 需要决策", by="x")["task_id"]
        P.upsert_decision(self.slug, c3, status="adopted")
        with self.assertRaises(ValueError):
            L.spawn_child(self.slug, c3, "原因四 太深", by="x")
        self.decision("20260923-000002-bbbb", status="cancelled")
        with self.assertRaises(ValueError):
            L.spawn_child(self.slug, "20260923-000002-bbbb", "已撤销 不开单", by="x")
        recs, threads = self.state_recs()
        self.assertEqual(len(threads), 2)
        self.assertEqual(threads[0]["count"] + threads[1]["count"], 5)
        # pending-child-* 是 draft，不会被 _full_state 当成丢失任务包同步成 cancelled
        self.assertEqual(self.rec(c1)["status"], "adopted")

    def test_state_key_fields_stable_when_absent(self):
        self.decision("20260923-000001-aaaa")
        recs, _ = self.state_recs()
        r = recs[0]
        self.assertEqual((r["bucket"], r["lineage"]["display_title"], r["lineage"]["round"], r["lineage"]["children"]), ("agent", "旧标题 20260923-000001-aaaa", 0, []))
        json.dumps(r)  # 可序列化


if __name__ == "__main__":
    unittest.main(verbosity=1)
