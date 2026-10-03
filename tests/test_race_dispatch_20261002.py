"""抢单（ADR 0013，2026-10-02 用户：「抢单功能为什么还没上线？想快点知道答案，只能单独选一个，又不知道他能不能接到」）。
临时数据目录。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_core as B          # noqa: E402
import bus_projects as P      # noqa: E402


class Race(Base):
    def setUp(self):
        super().setUp()
        for p in ("a1", "a2", "a3"):
            B.heartbeat(p, "idle", "m", "", B.now_iso(), 60)

    def meta(self, tid):
        return B.read_json(os.path.join(B.OUTBOX, f"{tid}.meta.json"), {})

    def test_first_claim_wins_and_others_are_withdrawn(self):
        r = B.dispatch("controller", ["a1", "a2", "a3"], "independent", "问个快问题", [], ttl_min=10, race=True)
        tid = r["task_id"]
        self.assertEqual(self.meta(tid)["min_answers"], 1)
        out = B.wait_task("a2", 1)
        self.assertIn(tid, out, "第一个来领的拿到")
        m = self.meta(tid)
        self.assertEqual((m["race_winner"], m["peers"]), ("a2", ["a2"]))
        self.assertEqual(sorted(m["race_withdrawn"]), ["a1", "a3"])
        for p in ("a1", "a3"):
            self.assertFalse(os.path.exists(os.path.join(B.INBOX, p, f"{tid}.json")), f"{p} 的副本撤回")
            self.assertTrue(os.path.exists(os.path.join(B.INBOX, p, f"{tid}.json{B.RACE_LOST}")), "留痕")
        self.assertNotIn(tid, B.wait_task("a1", 1), "后来的人领不到")

    def test_late_reader_skips_even_if_copy_was_still_there(self):
        r = B.dispatch("controller", ["a1", "a2"], "independent", "q", [], ttl_min=10, race=True)
        tid = r["task_id"]
        self.assertTrue(B._race_claim(tid, "a1"))
        self.assertFalse(B._race_claim(tid, "a2"))
        self.assertTrue(B._race_claim(tid, "a1"), "胜者重连重领照样是他的")

    def test_normal_task_unaffected(self):
        r = B.dispatch("controller", ["a1", "a2"], "independent", "q", [], ttl_min=10)
        tid = r["task_id"]
        self.assertIn(tid, B.wait_task("a1", 1))
        self.assertIn(tid, B.wait_task("a2", 1), "非抢单：每人一份照常")

    def test_web_race_dispatch_is_direct_not_queued_for_leader(self):
        os.makedirs(os.path.join(P.PROJECTS, "p1"), exist_ok=True)
        B.write_json(os.path.join(P.PROJECTS, "p1", "project.json"), {"slug": "p1", "name": "p1", "peers": [], "mode": "independent", "context_files": []})
        B.write_json(os.path.join(P.PROJECTS, "p1", "decisions.json"), [])
        r = P.dispatch_race({"project": "p1", "question": "快问", "peers": ["a1", "a2"], "mode": "race", "ttl_min": 5})
        self.assertTrue(r["direct"])
        self.assertEqual(sorted(r["listening"]), ["a1", "a2"])
        self.assertEqual(os.listdir(P.COMMANDS), [], "不进 Leader 指令队列")
        rec = next(d for d in P.decisions("p1") if d["task_id"] == r["task_id"])
        self.assertEqual((rec["status"], rec["mode"]), ("dispatched", "race"))
        with self.assertRaises(ValueError):
            P.dispatch_race({"project": "p1", "question": "", "peers": ["a1"], "mode": "race"})

    def test_frontend_offers_race(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        v2 = open(os.path.join(root, "web", "v2", "js", "views2.js"), encoding="utf-8").read()
        self.assertIn("race: { label: '抢单", v2)
        web = open(os.path.join(root, "bus_web.py"), encoding="utf-8").read()
        self.assertIn('payload.get("mode") == "race"', web)


if __name__ == "__main__":
    unittest.main()
