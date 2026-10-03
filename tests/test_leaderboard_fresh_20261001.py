"""榜单自动保鲜（2026-10-01 实测本地快照停在 09-21，上游已到 10-01）：看榜单时快照过旧就在后台拉，不重复拉。
不联网：ST.load 与 start_leaderboard 都打桩。跑法：py -3 tests/test_leaderboard_fresh_20261001.py
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "arena", "server"))
import arena_core as C  # noqa: E402


def stamp(age_sec):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - age_sec))


class LeaderboardFresh(unittest.TestCase):
    def setUp(self):
        self.store = {"leaderboard": {}, "sessions": {"sessions": []}, "settings": {"leaderboard_source": "https://example.invalid"}}
        self.started = []
        self.patches = [
            mock.patch.object(C.ST, "load", lambda name: self.store.get(name, {})),
            mock.patch.object(C, "start_leaderboard", lambda: self.started.append(1) or {"id": "j"}),
            mock.patch.object(C, "stats_view", lambda: {"by_model": {}}),
            mock.patch.object(C, "routes_view", lambda: {"routes": []}),
        ]
        for p in self.patches:
            p.start()
        C.JOBS.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        C.JOBS.clear()

    def test_stale_snapshot_triggers_one_background_fetch(self):
        self.store["leaderboard"] = {"boards": {}, "fetched_at": stamp(13 * 3600), "date": "2026-09-21"}
        v = C.leaderboard_view()
        self.assertEqual(len(self.started), 1)
        self.assertTrue(v["stale"] and v["refreshing"])
        self.assertAlmostEqual(v["age_hours"], 13, delta=0.2)

    def test_fresh_snapshot_is_left_alone(self):
        self.store["leaderboard"] = {"boards": {}, "fetched_at": stamp(3600), "date": "2026-10-01"}
        v = C.leaderboard_view()
        self.assertEqual(self.started, [])
        self.assertFalse(v["stale"] or v["refreshing"])

    def test_never_fetched_counts_as_stale(self):
        v = C.leaderboard_view()
        self.assertEqual(len(self.started), 1)
        self.assertIsNone(v["age_hours"])

    def test_running_fetch_is_not_duplicated(self):
        self.store["leaderboard"] = {"boards": {}, "fetched_at": stamp(30 * 3600)}
        C.JOBS["x"] = {"kind": "leaderboard", "status": "running"}
        v = C.leaderboard_view()
        self.assertEqual(self.started, [])
        self.assertTrue(v["refreshing"])

    def test_fetch_error_does_not_block_view(self):
        self.store["leaderboard"] = {"boards": {}, "fetched_at": stamp(30 * 3600)}
        with mock.patch.object(C, "start_leaderboard", side_effect=RuntimeError("offline")):
            v = C.leaderboard_view()
        self.assertTrue(v["stale"])
        self.assertFalse(v["refreshing"])


if __name__ == "__main__":
    unittest.main()
