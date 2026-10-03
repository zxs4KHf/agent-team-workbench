"""已采纳的决策直接派施工（不经 Leader）。2026-10-02 接手积压：Leader 掉线后「实施」指令只能排队，协作工作台项目
116 条已采纳决策没落地；5a9b51f 当时做过一版一直没进 main，这里在当前 main 上重做，并让守护在没有 Leader 时自动接手。
临时 git 仓库 + 临时数据目录。"""
import datetime as dt
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_build as BB        # noqa: E402
import bus_core as B          # noqa: E402
import bus_projects as P      # noqa: E402
import bus_reassign as RA     # noqa: E402

PLAN = "# 合并稿\n\n## 最终方案\n\n在 `a.txt` 末尾加一行。\n\n## 验收\n\n- a.txt 有两行\n"


class ImplementDirect(Base):
    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.tmp, "repo"); os.makedirs(self.repo)
        for a in (["init", "-b", "main"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"], ["config", "commit.gpgsign", "false"]):
            subprocess.run(["git", *a], cwd=self.repo, check=True, capture_output=True)
        open(os.path.join(self.repo, ".gitignore"), "w").write(".wt/\n")
        open(os.path.join(self.repo, "a.txt"), "w").write("base\n")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.repo, check=True, capture_output=True)
        self.p = [mock.patch.object(BB, "BUILD_DIR", os.path.join(self.tmp, "builds")), mock.patch.object(P, "BUILDS", os.path.join(self.tmp, "builds")), mock.patch.object(B, "peer_group", lambda p: "opus"),
                  mock.patch.object(RA, "builder_groups", lambda: ["opus"]), mock.patch.object(B, "WORKSPACE", self.repo)]
        for x in self.p:
            x.start()
        os.makedirs(os.path.join(P.PROJECTS, "p1"))
        B.write_json(os.path.join(P.PROJECTS, "p1", "project.json"), {"slug": "p1", "name": "p1", "peers": [], "mode": "independent", "context_files": [], "repo_dir": self.repo})
        os.makedirs(os.path.join(self.repo, "log", "d"))
        open(os.path.join(self.repo, "log", "d", "D1.merge.md"), "w", encoding="utf-8").write(PLAN)
        B.write_json(os.path.join(P.PROJECTS, "p1", "decisions.json"), [{"task_id": "D1", "status": "adopted", "title": "加一行", "merge_path": "log/d/D1.merge.md",
                                                                         "created": B.now_iso(), "updated": B.now_iso()}])

    def tearDown(self):
        for x in self.p:
            x.stop()
        super().tearDown()

    def dec(self):
        return next(d for d in P.decisions("p1") if d["task_id"] == "D1")

    def test_no_listening_builder_falls_back_to_leader_queue(self):
        r = P.dispatch_implement("p1", "D1")
        self.assertTrue(r["queued_instead"])
        self.assertIn("没有正在等任务的施工 Agent", r["note"])
        self.assertEqual(len([f for f in os.listdir(P.COMMANDS) if f.endswith(".json")]), 1)

    def test_direct_dispatch_to_listening_builder(self):
        B.heartbeat("w1", "idle", "m", "", B.now_iso(), 60)       # 正在等任务
        r = P.dispatch_implement("p1", "D1")
        self.assertEqual((r["direct"], r["builder"]), (True, "w1"))
        self.assertEqual(self.dec()["implementation_task_id"], r["build_task_id"])
        task = B.read_json(os.path.join(B.INBOX, "w1", f"{r['build_task_id']}.json"), {})
        self.assertIn("在 `a.txt` 末尾加一行", task.get("question", ""), "任务包是合并稿的最终方案")
        self.assertTrue(BB.record(r["build_task_id"])["teammates"]["w1"]["path"], "开了工作副本")
        again = P.dispatch_implement("p1", "D1")
        self.assertTrue(again["dedup"], "同一决策不重复建单")

    def test_user_direct_replaces_queued_command(self):
        q = P.request_implement("p1", "D1")
        B.heartbeat("w1", "idle", "m", "", B.now_iso(), 60)
        r = P.dispatch_implement("p1", "D1")
        self.assertTrue(r["direct"])
        self.assertFalse(os.path.exists(os.path.join(P.COMMANDS, q["command_id"] + ".json")), "排队那条被顶替并回执")
        self.assertTrue(os.path.exists(os.path.join(P.DONE, q["command_id"] + ".json")))

    def test_guard_auto_implements_only_when_no_leader_and_old_enough(self):
        q = P.request_implement("p1", "D1")
        B.heartbeat("w1", "idle", "m", "", B.now_iso(), 60)
        self.assertEqual(P.auto_implement(), [], "刚排队：先等 Leader")
        path = os.path.join(P.COMMANDS, q["command_id"] + ".json")
        c = B.read_json(path, {}); c["created"] = (dt.datetime.now().astimezone() - dt.timedelta(minutes=30)).isoformat(); B.write_json(path, c)
        B.heartbeat("controller", "idle", "controller", session="live0001")
        self.assertEqual(P.auto_implement(), [], "有 Leader 在线：交给它")
        with mock.patch.object(P, "live_leader_sessions", lambda: {}):
            done = P.auto_implement()
        self.assertEqual(len(done), 1)
        self.assertTrue(done[0]["direct"])
        self.assertEqual(self.dec()["implementation_task_id"], done[0]["build_task_id"])
        self.assertFalse(os.path.exists(path), "指令已回执")


if __name__ == "__main__":
    unittest.main()
