"""催审 = 立刻派审查（2026-10-01）。以前只往 Leader 队列塞一句话，「进行中」里的待审单一直卡着。
派审本身打桩（BB.dispatch_review），只测「派谁、跳过谁、卡住的换人重派」。
跑法：py -3 tests/test_review_now_20261001.py
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_core as B      # noqa: E402
import bus_build as BB    # noqa: E402
import bus_review as RV   # noqa: E402


class ReviewNow(Base):
    def setUp(self):
        super().setUp()
        self.builds = os.path.join(self.tmp, "console", "builds")
        os.makedirs(self.builds)
        self.sent = []
        self.patches = [mock.patch.object(BB, "BUILD_DIR", self.builds),
                        mock.patch.object(BB, "dispatch_review", self.fake_dispatch)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def fake_dispatch(self, task_id, teammate, ttl_min=20, reviewers=None, by="controller"):
        self.sent.append((task_id, teammate))
        return {"review_task": f"R-{task_id}", "reviewers": ["rev1"], "deadline": "", "task_id": task_id, "teammate": teammate}

    def build(self, tid, **mates):
        tms = {}
        for name, kw in mates.items():
            tms[name] = {"commit": "abc123", "changed_files": ["a.py"], "gates": {"ok": True}, "review": "", "merged": False, **kw}
        BB.save_record(tid, {"task_id": tid, "status": "building", "teammates": tms})

    def review_task(self, rid, target, reviewer="rev1", online=True, copy=True):
        B.write_json(os.path.join(B.OUTBOX, f"{rid}.meta.json"), {"id": rid, "mode": "review", "peers": [reviewer], "review_of": target})
        if copy:
            os.makedirs(os.path.join(B.INBOX, reviewer), exist_ok=True)
            dl, ts = B.deadline_after(30)
            B.write_json(os.path.join(B.INBOX, reviewer, f"{rid}.json"), {"id": rid, "deadline": dl, "deadline_ts": ts})
        if online:
            B.heartbeat(reviewer, "idle", "m")

    def test_pending_submissions_are_dispatched(self):
        self.build("T1", b1={}, b2={})
        r = RV.review_now(by="test")
        self.assertEqual(sorted(self.sent), [("T1", "b1"), ("T1", "b2")])
        self.assertEqual(len(r["dispatched"]), 2)

    def test_not_ready_submissions_are_skipped(self):
        self.build("T1", merged={"merged": True}, reviewed={"review": "PASS"}, empty={"changed_files": []},
                   unbound={"commit_unbound": True}, gate_fail={"gates": {"ok": False}}, nocommit={"commit": ""})
        RV.review_now()
        self.assertEqual(self.sent, [])

    def test_live_review_is_not_duplicated(self):
        self.build("T1", b1={})
        self.review_task("R1", {"task_id": "T1", "teammate": "b1", "commit": "abc123"})
        r = RV.review_now()
        self.assertEqual(self.sent, [])
        self.assertIn("R1", r["skipped"][0]["reason"])

    def test_review_with_offline_reviewer_does_not_block(self):
        self.build("T1", b1={})
        self.review_task("R1", {"task_id": "T1", "teammate": "b1"}, online=False)
        RV.review_now()
        self.assertEqual(self.sent, [("T1", "b1")])

    def test_stuck_review_task_is_replaced_and_archived(self):
        self.build("T1", b1={"review": "RETURN"})   # 施工单这一位已经不在「待审」里；按审查单号点催审
        self.review_task("R1", {"task_id": "T1", "teammate": "b1"}, online=False)
        r = RV.review_now("R1")
        self.assertEqual(self.sent, [("T1", "b1")])
        self.assertFalse(os.path.exists(os.path.join(B.OUTBOX, "R1.meta.json")), "旧审查单归档")
        self.assertFalse(os.path.exists(os.path.join(B.INBOX, "rev1", "R1.json")), "旧副本撤掉")
        self.assertEqual(len(r["dispatched"]), 1)

    def test_live_review_task_is_left_alone(self):
        self.build("T1", b1={"review": "RETURN"})
        self.review_task("R1", {"task_id": "T1", "teammate": "b1"})
        r = RV.review_now("R1")
        self.assertEqual(self.sent, [])
        self.assertTrue(r["skipped"])

    def test_one_failure_does_not_stop_others(self):
        self.build("T1", b1={}, b2={})
        calls = []

        def flaky(task_id, teammate, **kw):
            calls.append(teammate)
            if teammate == "b1":
                raise ValueError("没有在线的Reviewer")
            return {"review_task": "R", "reviewers": ["rev1"], "task_id": task_id, "teammate": teammate}
        with mock.patch.object(BB, "dispatch_review", flaky):
            r = RV.review_now()
        self.assertEqual(len(r["dispatched"]), 1)
        self.assertIn("没有在线的Reviewer", r["errors"][0]["error"])

    def test_burst_of_reviews_spreads_across_reviewers(self):
        # 只按 idle 排时 21 单全进同一个人的收件箱；按收件箱负载排就轮流分
        roles = {"assignments": {"reviewer_groups": ["g"]}, "policy": {}}
        with mock.patch.object(B, "read_roles", lambda *a, **k: roles), mock.patch.object(B, "peer_group", lambda p: "g"):
            for r in ("rev1", "rev2"):
                B.heartbeat(r, "idle", "m")
            got = []
            for _ in range(4):
                pick = B.pick_reviewers(exclude=["builder"])
                got += pick
                B.dispatch("controller", pick, "review", "包", [], 15, None, 1, review_of={"task_id": "T", "teammate": "b"})
        self.assertEqual(sorted(got), ["rev1", "rev1", "rev2", "rev2"])

    def test_dispatch_records_review_target_in_meta(self):
        B.heartbeat("rev1", "idle", "m")
        r = B.dispatch("controller", ["rev1"], "review", "审查包", [], 15, None, 1, review_of={"task_id": "T1", "teammate": "b1", "commit": "abc"})
        meta = B.read_json(os.path.join(B.OUTBOX, f"{r['task_id']}.meta.json"), {})
        self.assertEqual(meta.get("review_of", {}).get("teammate"), "b1")


if __name__ == "__main__":
    unittest.main()
