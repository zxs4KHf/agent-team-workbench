"""按积压自动分配（ADR 0012）。跑法：py -3 tests/test_allocator_20261001.py"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_core as B        # noqa: E402
import bus_build as BB      # noqa: E402
import bus_allocate as AL   # noqa: E402


class Allocator(Base):
    def setUp(self):
        super().setUp()
        self.builds = os.path.join(self.tmp, "console", "builds")
        os.makedirs(self.builds)
        roles = {"assignments": {"reviewer_groups": ["g"]}, "policy": {}}
        self.patches = [mock.patch.object(BB, "BUILD_DIR", self.builds), mock.patch.object(B, "peer_group", lambda p: "g"),
                        mock.patch.object(B, "read_roles", lambda *a, **k: roles)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def held(self, tid, holder="gone1", moves=0):
        log = [{"from": "x", "to": "y", "by": "guard", "time": B.now_iso()}] * moves
        B.write_json(os.path.join(B.OUTBOX, f"{tid}.meta.json"), {"id": tid, "mode": "independent", "peers": [holder],
                     "question": "q", "from": "controller", "created": B.now_iso(), "reassign_log": log})
        os.makedirs(os.path.join(B.INBOX, holder), exist_ok=True)
        dl, ts = B.deadline_after(30)
        B.write_json(os.path.join(B.INBOX, holder, f"{tid}.taken"), {"id": tid, "mode": "independent", "question": "q",
                     "from": "controller", "to": holder, "deadline": dl, "deadline_ts": ts})

    def idle(self, *names):
        for n in names:
            B.heartbeat(n, "idle", "m", "", B.now_iso(), 60)   # 2026-10-02 起分配器只算真正在 wait 的人

    def test_default_mode_is_preview_and_changes_nothing(self):
        self.idle("w1")
        self.held("T1")
        r = AL.tick()
        self.assertEqual(r["mode"], "preview")
        self.assertEqual([(a["task_id"], a["to"]) for a in r["actions"]], [("T1", "w1")])
        self.assertTrue(os.path.exists(os.path.join(B.INBOX, "gone1", "T1.taken")), "预演不动任何单")
        self.assertEqual(AL.view()["last_plan"]["actions"][0]["task_id"], "T1")

    def test_on_mode_actually_moves(self):
        self.idle("w1")
        self.held("T1")
        AL.set_mode("on")
        r = AL.tick()
        self.assertTrue(r["actions"][0]["result"]["ok"])
        self.assertTrue(os.path.exists(os.path.join(B.INBOX, "w1", "T1.json")))

    def test_off_does_nothing(self):
        self.idle("w1")
        self.held("T1")
        AL.set_mode("off")
        self.assertEqual(AL.tick(), {"mode": "off"})

    def test_each_idle_agent_gets_at_most_one(self):
        self.idle("w1")
        self.held("T1")
        self.held("T2")
        self.assertEqual(len(AL.plan()["actions"]), 1)

    def test_per_cycle_cap(self):
        self.idle(*[f"w{i}" for i in range(8)])
        for i in range(8):
            self.held(f"T{i}")
        self.assertEqual(len(AL.plan()["actions"]), AL.MAX_PER_CYCLE)

    def test_busy_or_loaded_agents_are_not_idle(self):
        B.heartbeat("w1", "think", "m", task_id="X")
        self.idle("w2")
        self.held("T9", holder="w2")    # w2 在线空闲但收件箱里有单 → 不算空闲
        self.assertEqual(AL.idle_agents(), [])

    def test_slot_moved_too_often_is_left_for_human(self):
        self.idle("w1")
        self.held("T1", moves=AL.MAX_MOVES_PER_SLOT)
        self.assertEqual(AL.plan()["actions"], [])

    def test_pending_review_goes_to_reviewer_not_author(self):
        self.idle("author", "rev1")
        BB.save_record("B1", {"task_id": "B1", "status": "building", "created": B.now_iso(), "teammates": {
            "author": {"commit": "abc", "changed_files": ["a.py"], "gates": {"ok": True}, "review": "", "merged": False}}})
        acts = AL.plan()["actions"]
        self.assertEqual([(a["stage"], a["task_id"], a["to"]) for a in acts], [("review", "B1", "rev1")])

    def test_bad_mode_rejected(self):
        with self.assertRaises(ValueError):
            AL.set_mode("yolo")


if __name__ == "__main__":
    unittest.main()
