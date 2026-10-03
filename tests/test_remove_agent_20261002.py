"""在 Agent 页点名移除一个 Agent（2026-10-02）。跑法：py -3 tests/test_remove_agent_20261002.py"""
import glob
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_core as B           # noqa: E402
import bus_settings as SET     # noqa: E402
import bus_peer_admin as PA    # noqa: E402


class RemoveAgent(Base):
    def setUp(self):
        super().setUp()
        self.g = mock.patch.object(B, "peer_group", lambda p: "g")
        self.g.start()
        SET.save({"peer_tokens": {"old1": "x" * 40}})

    def tearDown(self):
        self.g.stop()
        super().tearDown()

    def task(self, tid, peer, mode="independent"):
        B.write_json(os.path.join(B.OUTBOX, f"{tid}.meta.json"), {"id": tid, "mode": mode, "peers": [peer], "question": "q", "from": "controller"})
        os.makedirs(os.path.join(B.INBOX, peer), exist_ok=True)
        dl, ts = B.deadline_after(30)
        B.write_json(os.path.join(B.INBOX, peer, f"{tid}.taken"), {"id": tid, "mode": mode, "question": "q", "from": "controller",
                                                                   "to": peer, "deadline": dl, "deadline_ts": ts, "taken_at": B.now_iso()})

    def test_remove_moves_tasks_archives_and_revokes(self):
        B.heartbeat("old1", "idle", "m")
        B.add_strike("old1", "x")
        B.heartbeat("live1", "idle", "m", "", B.now_iso(), 60)
        self.task("T1", "old1")
        self.assertEqual([t["task_id"] for t in PA.remove_peer("old1", dry_run=True)["tasks"]], ["T1"])
        self.assertTrue(os.path.exists(os.path.join(B.PEERS, "old1.json")), "预演不动任何东西")
        r = PA.remove_peer("old1")
        self.assertTrue(r["ok"])
        self.assertEqual([(m["task_id"], m["to"]) for m in r["moved"]], [("T1", "live1")])
        self.assertFalse(os.path.exists(os.path.join(B.PEERS, "old1.json")))
        self.assertFalse(os.path.exists(B._beat_path("old1")))
        self.assertTrue(glob.glob(os.path.join(B.LOG, "*", "removed-peers", "old1.json")), "登记归档留证")
        self.assertEqual(B.strikes("old1"), 0)
        self.assertTrue(r["revoked"])
        self.assertEqual(SET.reveal_token("old1"), "")

    def test_unmovable_task_blocks_unless_forced(self):
        B.heartbeat("old1", "idle", "m")
        self.task("T1", "old1")                       # 没有任何其他在线的人
        r = PA.remove_peer("old1")
        self.assertFalse(r["ok"])
        self.assertTrue(os.path.exists(os.path.join(B.PEERS, "old1.json")), "挪不走就不删")
        r = PA.remove_peer("old1", force=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["stuck"][0]["task_id"], "T1")

    def test_keep_token_when_asked(self):
        B.heartbeat("old1", "idle", "m")
        r = PA.remove_peer("old1", revoke=False)
        self.assertFalse(r["revoked"])
        self.assertTrue(SET.reveal_token("old1"))

    def test_leader_and_lease_holder_protected(self):
        with self.assertRaises(ValueError):
            PA.remove_peer("controller")
        with mock.patch.object(B, "lease", lambda: {"holder": "relay1"}):
            with self.assertRaises(ValueError):
                PA.remove_peer("relay1")


if __name__ == "__main__":
    unittest.main()
