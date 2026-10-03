"""催审派不出去 / 改派后「已领 1500 分钟」再过期（2026-10-02 用户反馈）。跑法：py -3 tests/test_review_fallback_20261002.py

实况：Reviewer 组是 opus / fable / astra / gpt-5.6，在线的只有 glm / qwen / sonnet → 「一键催审」一单都派不出去；
改派后页面按任务创建时间算「已领 N 分钟」，新人一接手就显示 1500 多分钟；改派只续 30 分钟，接手人没在等就过期。
"""
import datetime as dt
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_core as B        # noqa: E402
import bus_projects as P    # noqa: E402

ROLES = {"assignments": {"reviewer_groups": ["opus"]}, "policy": {}}
GROUP = {"author": "glm", "glm2": "glm", "qwen1": "qwen", "opus1": "opus"}


class ReviewFallback(Base):
    def setUp(self):
        super().setUp()
        self.p = [mock.patch.object(B, "read_roles", lambda *a, **k: ROLES),
                  mock.patch.object(B, "peer_group", lambda p: GROUP.get(p, ""))]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        super().tearDown()

    def wait(self, *names):
        for n in names:
            B.heartbeat(n, "idle", "m", "", B.now_iso(), 60)

    def test_no_reviewer_group_online_falls_back_to_listening_non_author(self):
        self.wait("author", "qwen1")
        self.assertEqual(B.pick_reviewers(exclude=["author"], avoid_group="glm"), ["qwen1"])

    def test_reviewer_group_still_preferred_when_online(self):
        self.wait("author", "qwen1", "opus1")
        self.assertEqual(B.pick_reviewers(exclude=["author"], avoid_group="glm"), ["opus1"])

    def test_fallback_can_be_turned_off(self):
        self.wait("qwen1")
        with mock.patch.object(B, "read_roles", lambda *a, **k: {**ROLES, "policy": {"review_fallback_any": False}}):
            self.assertEqual(B.pick_reviewers(exclude=["author"]), [])

    def test_temp_reviewer_is_marked_in_pack(self):
        import bus_build as BB
        self.wait("qwen1")
        with mock.patch.object(BB, "review_target", lambda t, m: {"task_id": t, "teammate": m, "commit": "c"}), \
                mock.patch.object(BB, "review_pack", lambda t, m, target=None: "PACK"):
            r = BB.dispatch_review("T1", "author")
        task = B.read_json(os.path.join(B.INBOX, "qwen1", f"{r['review_task']}.json"), {})
        self.assertIn("临时 Reviewer", task["question"])


class ClaimAges(Base):
    def test_age_counts_from_claim_not_task_creation(self):
        B.write_json(os.path.join(B.OUTBOX, "T1.meta.json"), {"id": "T1", "mode": "independent", "peers": ["w1"], "question": "q",
                                                              "created": (dt.datetime.now().astimezone() - dt.timedelta(days=1)).isoformat()})
        os.makedirs(os.path.join(B.INBOX, "w1"), exist_ok=True)
        B.write_json(os.path.join(B.INBOX, "w1", "T1.taken"), {"id": "T1", "taken_at": B.now_iso()})
        pr = P.task_progress("T1")
        self.assertEqual(pr["states"]["w1"], "working")
        self.assertLess(pr["ages"]["w1"], 60, "刚接手：不是任务建了多久")

    def test_reassign_extends_60_min_and_reports_listening(self):
        B.write_json(os.path.join(B.OUTBOX, "T2.meta.json"), {"id": "T2", "mode": "independent", "peers": ["gone"], "question": "q", "from": "controller"})
        B.heartbeat("idle1", "idle", "m")                      # 在线但不在 wait
        with mock.patch.object(B, "peer_group", lambda p: "g"):
            r = B.reassign("T2", "gone", to_peer="idle1")
        self.assertFalse(r["listening"])
        left = B.read_json(os.path.join(B.INBOX, "idle1", "T2.json"), {})["deadline_ts"] - dt.datetime.now().timestamp()
        self.assertGreater(left, 55 * 60)


if __name__ == "__main__":
    unittest.main()
