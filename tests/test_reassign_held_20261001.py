"""一键改派被占着不动的任务（2026-10-01）。跑法：py -3 tests/test_reassign_held_20261001.py"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_core as B          # noqa: E402
import bus_reassign as RA     # noqa: E402
import bus_review as RV       # noqa: E402


class ReassignHeld(Base):
    def setUp(self):
        super().setUp()
        self.group = mock.patch.object(B, "peer_group", lambda p: "g")
        self.group.start()
        self.roles = mock.patch.object(RA, "builder_groups", lambda: [])   # 不限施工组：这里测的是改派本身，组过滤见 test_stuck_build_20261002
        self.roles.start()

    def tearDown(self):
        self.roles.stop()
        self.group.stop()
        super().tearDown()

    def task(self, tid, peer, mode="independent", kind="taken"):
        B.write_json(os.path.join(B.OUTBOX, f"{tid}.meta.json"),
                     {"id": tid, "mode": mode, "peers": [peer], "question": "q", "from": "controller"})
        os.makedirs(os.path.join(B.INBOX, peer), exist_ok=True)
        dl, ts = B.deadline_after(30)
        B.write_json(os.path.join(B.INBOX, peer, f"{tid}.{'taken' if kind == 'taken' else 'json'}"),
                     {"id": tid, "mode": mode, "question": "q", "from": "controller", "to": peer, "deadline": dl, "deadline_ts": ts})

    def test_offline_agents_tasks_move_to_online_agent(self):
        B.heartbeat("live1", "idle", "m")
        self.task("T1", "gone1")                 # gone1 从没心跳 = 离线
        self.task("T2", "gone1", kind="queued")
        r = RA.reassign_held()
        self.assertEqual(sorted((m["task_id"], m["to"]) for m in r["moved"]), [("T1", "live1"), ("T2", "live1")])
        self.assertTrue(os.path.exists(os.path.join(B.INBOX, "live1", "T1.json")))
        self.assertFalse(os.path.exists(os.path.join(B.INBOX, "gone1", "T1.taken")))

    def test_busy_agent_on_this_task_is_not_touched(self):
        self.task("T1", "w1")
        B.heartbeat("w1", "think", "m", task_id="T1")
        self.assertEqual(RA.held_items(), [])

    def test_idle_agent_holding_old_claim_counts_as_held(self):
        self.task("T1", "w1")
        B.heartbeat("w1", "idle", "m")
        self.assertEqual([i["task_id"] for i in RA.held_items()], ["T1"])

    def test_online_agents_unclaimed_queue_is_not_held(self):
        self.task("T1", "w1", kind="queued")
        B.heartbeat("w1", "idle", "m")
        self.assertEqual(RA.held_items(), [], "待领的单它下一圈就会领，不算占着")

    def test_build_moves_to_new_worktree_on_same_base(self):
        import subprocess
        import bus_build as BB
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        g = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()
        g("init", "-b", "main"); g("config", "user.email", "t@l"); g("config", "user.name", "t"); g("config", "commit.gpgsign", "false")
        open(os.path.join(repo, ".gitignore"), "w").write(".wt/\n")
        open(os.path.join(repo, "a.txt"), "w").write("base\n")
        g("add", "."); g("commit", "-m", "base")
        base = g("rev-parse", "HEAD")
        builds = os.path.join(self.tmp, "builds")
        with mock.patch.object(BB, "BUILD_DIR", builds):
            BB.open_worktrees("B1", ["gone1"], repo)
            open(os.path.join(repo, "a.txt"), "w").write("main moved\n")
            g("commit", "-am", "main moved")               # main 前进：新施工者仍要从本单基线开
            old_wt = BB.record("B1")["teammates"]["gone1"]["path"]
            open(os.path.join(old_wt, "half.txt"), "w").write("半成品\n")
            self.task("B1", "gone1", mode="build")
            B.heartbeat("live1", "idle", "m", "", B.now_iso(), 60)   # 正在 wait：施工单只派给等任务的人（2026-10-02）
            r = RA.reassign_held()
            self.assertEqual([(m["task_id"], m["to"]) for m in r["moved"]], [("B1", "live1")])
            tm = BB.record("B1")["teammates"]["live1"]
            self.assertEqual(g("rev-parse", "HEAD", cwd=tm["path"]), base, "新 worktree 从本单基线开")
            self.assertTrue(os.path.exists(os.path.join(old_wt, "half.txt")), "原施工者的半成品保留")
            moved = B.read_json(os.path.join(B.INBOX, "live1", "B1.json"), {})
            self.assertEqual((moved.get("worktree"), moved.get("branch")), (tm["path"], tm["branch"]))
            self.assertEqual(BB.teammate_root("live1"), tm["path"], "新施工者的 ws 根目录指向自己的 worktree")

    def test_review_tasks_go_through_review_now(self):
        self.task("R1", "gone1", mode="review")
        with mock.patch.object(RV, "review_now", lambda tid, by="user": {"dispatched": [{"reviewers": ["rv2"], "review_task": "R9"}], "skipped": [], "errors": []}):
            r = RA.reassign_held()
        self.assertEqual((r["moved"][0]["to"], r["moved"][0]["new_task"]), ("rv2", "R9"))

    def test_peer_filter_and_answered_tasks(self):
        B.heartbeat("live1", "idle", "m")
        self.task("T1", "gone1")
        self.task("T2", "gone2")
        open(os.path.join(B.OUTBOX, "T2.gone2.md"), "w").close()     # 已回复的不算
        self.assertEqual([i["task_id"] for i in RA.held_items("gone1")], ["T1"])
        self.assertEqual([i["task_id"] for i in RA.held_items()], ["T1"])

    def test_no_online_agent_reports_error(self):
        self.task("T1", "gone1")
        r = RA.reassign_held()
        self.assertEqual(r["moved"], [])
        self.assertIn("没有任何在线 Agent", r["errors"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
