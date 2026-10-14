# tests/test_unbound_visibility_20261011.py
# 体验反馈 fb-28bb2a1d9a 自动化测试：
# 1. 验证 _unbound_tasks_view 投影字段、数量与截断逻辑；
# 2. 负对照：被决策引用的 task 不进入 unbound；
# 3. _infer_project_for_peer 归属推断逻辑（显式、peer登记、peer当前任务、parent_task、无法推断时为空）；
# 4. bus_core.dispatch 派发时写入 meta.project。
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import bus_core as B  # noqa: E402
import bus_projects as P  # noqa: E402


class TestUnboundVisibility(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bus-unbound-")
        self.orig_root = B.ROOT
        B.ROOT = self.tmp
        B.PEERS = os.path.join(self.tmp, "peers")
        B.INBOX = os.path.join(self.tmp, "inbox")
        B.OUTBOX = os.path.join(self.tmp, "outbox")
        B.LOG = os.path.join(self.tmp, "log")
        B.WORKSPACE = os.path.join(self.tmp, "ws")
        B.LOCK = os.path.join(self.tmp, "console", "controller.lock")
        B.PRIORITY = os.path.join(self.tmp, "console", "priority.json")

        P.PROJECTS = os.path.join(self.tmp, "projects")
        P.COMMANDS = os.path.join(self.tmp, "console", "commands")
        P.DONE = os.path.join(self.tmp, "console", "done")
        P.BUILDS = os.path.join(self.tmp, "console", "builds")
        P.ROLES = os.path.join(self.tmp, "console", "roles.json")
        P.NUDGES = os.path.join(self.tmp, "console", "nudges.json")
        P.BRIDGE = os.path.join(self.tmp, "console", "bridge.json")

        for d in (B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.WORKSPACE,
                  P.PROJECTS, P.COMMANDS, P.DONE, P.BUILDS,
                  os.path.join(self.tmp, "console")):
            os.makedirs(d, exist_ok=True)

        P._PFT_CACHE.clear()
        P._STATE_CACHE.update({"at": 0.0, "data": None})

    def tearDown(self):
        B.ROOT = self.orig_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unbound_tasks_view_and_negative_control(self):
        # 1. 建立 3 个任务 meta
        long_q = "这是一段非常长的问题描述，用来测试未归属任务投影里的截断逻辑，超过六十个字之后应该自动截断并加上省略号，看看效果如何。这里再多加一些文字确保长度远远大于六十个字。"
        t1 = {
            "id": "t1-orphan",
            "mode": "build",
            "from": "main",
            "peers": ["gpt2"],
            "created": "2026-10-11T12:00:00",
            "question": long_q,
        }
        B.write_json(os.path.join(B.OUTBOX, "t1-orphan.meta.json"), t1)
        # 写一份回答
        with open(os.path.join(B.OUTBOX, "t1-orphan.gpt2.md"), "w", encoding="utf-8") as f:
            f.write("answer 1")

        t2 = {
            "id": "t2-with-build",
            "mode": "build",
            "from": "opus4",
            "peers": ["opus1"],
            "created": "2026-10-11T12:05:00",
            "question": "短问题",
        }
        B.write_json(os.path.join(B.OUTBOX, "t2-with-build.meta.json"), t2)
        # 写 build 数据
        build_data = {
            "status": "ready_for_review",
            "merge_commit": "abc1234567890",
            "teammates": {
                "opus1": {
                    "commit": "c0ffee123456",
                    "review": "PASS @c0ffee",
                }
            }
        }
        B.write_json(os.path.join(P.BUILDS, "t2-with-build.json"), build_data)

        t3 = {
            "id": "t3-in-decision",
            "mode": "review",
            "from": "controller",
            "peers": ["gemini2"],
            "created": "2026-10-11T12:10:00",
            "question": "已在决策中",
        }
        B.write_json(os.path.join(B.OUTBOX, "t3-in-decision.meta.json"), t3)

        # 负对照：传入已在决策中的 t3-in-decision
        dec_set = {"t3-in-decision"}
        unbound = P._unbound_tasks_view(dec_set)

        # 验证孤儿数量与负对照
        tids = [u["task_id"] for u in unbound]
        self.assertIn("t1-orphan", tids)
        self.assertIn("t2-with-build", tids)
        self.assertNotIn("t3-in-decision", tids, "负对照失败：已在决策中的任务不应出现在 unbound")

        # 验证 t1 字段
        u1 = next(u for u in unbound if u["task_id"] == "t1-orphan")
        self.assertEqual(u1["mode"], "build")
        self.assertEqual(u1["from"], "main")
        self.assertEqual(u1["peers"], ["gpt2"])
        self.assertEqual(u1["answers_count"], 1)
        self.assertTrue(u1["question"].endswith("…"))
        self.assertLessEqual(len(u1["question"]), 62)

        # 验证 t2 build 字段
        u2 = next(u for u in unbound if u["task_id"] == "t2-with-build")
        self.assertEqual(u2["answers_count"], 0)
        self.assertEqual(u2["build_status"], "ready_for_review")
        self.assertEqual(u2["commit"], "c0ffee1234")
        self.assertEqual(u2["review"], "PASS @c0ffee")
        self.assertEqual(u2["merge_commit"], "abc1234567890")

    def test_full_state_unbound_projection(self):
        # 建立项目
        proj_dir = os.path.join(P.PROJECTS, "test-p")
        os.makedirs(proj_dir, exist_ok=True)
        B.write_json(os.path.join(proj_dir, "project.json"), {"name": "Test P", "slug": "test-p"})
        B.write_json(os.path.join(proj_dir, "decisions.json"), [
            {"task_id": "dec-task-1", "title": "已定决策", "status": "adopted"}
        ])

        # 在 outbox 建立一个决策任务和一个孤儿任务
        B.write_json(os.path.join(B.OUTBOX, "dec-task-1.meta.json"), {"id": "dec-task-1", "peers": []})
        B.write_json(os.path.join(B.OUTBOX, "orphan-1.meta.json"), {"id": "orphan-1", "peers": []})

        state = P._full_state()
        self.assertIn("unbound", state)
        unbound_ids = [u["task_id"] for u in state["unbound"]]
        self.assertIn("orphan-1", unbound_ids)
        self.assertNotIn("dec-task-1", unbound_ids)

        # 验证只读：decisions.json 绝对没有被修改
        decs = B.read_json(os.path.join(proj_dir, "decisions.json"))
        self.assertEqual(len(decs), 1)
        self.assertEqual(decs[0]["task_id"], "dec-task-1")

    def test_infer_project_for_peer(self):
        # 1. 建立项目与决策
        proj_dir = os.path.join(P.PROJECTS, "proj-a")
        os.makedirs(proj_dir, exist_ok=True)
        B.write_json(os.path.join(proj_dir, "project.json"), {"slug": "proj-a"})
        B.write_json(os.path.join(proj_dir, "decisions.json"), [
            {"task_id": "task-in-a", "title": "A任务"}
        ])

        # Peer 1: 直接在 peer.json 登记了 project
        B.write_json(os.path.join(B.PEERS, "peer1.json"), {"peer": "peer1", "project": "proj-a"})
        self.assertEqual(B._infer_project_for_peer("peer1"), "proj-a")

        # Peer 2: peer.json 没有 project，但当前 task_id 属于 proj-a
        B.write_json(os.path.join(B.PEERS, "peer2.json"), {"peer": "peer2", "task_id": "task-in-a"})
        self.assertEqual(B._infer_project_for_peer("peer2"), "proj-a")

        # Peer 3: 只有 parent_task 属于 proj-a
        self.assertEqual(B._infer_project_for_peer("peer3", parent_task="task-in-a"), "proj-a")

        # Peer 4: 无任何线索 -> 必须返回空串，不许瞎猜
        B.write_json(os.path.join(B.PEERS, "peer4.json"), {"peer": "peer4"})
        self.assertEqual(B._infer_project_for_peer("peer4"), "")
        self.assertEqual(B._infer_project_for_peer(""), "")

    def test_dispatch_records_project(self):
        # 注册一个在线 peer
        B.write_json(os.path.join(B.PEERS, "worker.json"), {"peer": "worker", "online": True})

        # 1. 显式指定 project
        r1 = B.dispatch(from_peer="controller", peers=["worker"], mode="independent", question="q1", context_files=[], project="my-explicit-proj")
        tid1 = r1["task_id"]
        meta1 = B.read_json(os.path.join(B.OUTBOX, f"{tid1}.meta.json"))
        self.assertEqual(meta1.get("project"), "my-explicit-proj")

        # 2. 推断 project
        B.write_json(os.path.join(B.PEERS, "dispatcher.json"), {"peer": "dispatcher", "project": "inferred-proj"})
        r2 = B.dispatch(from_peer="dispatcher", peers=["worker"], mode="independent", question="q2", context_files=[])
        tid2 = r2["task_id"]
        meta2 = B.read_json(os.path.join(B.OUTBOX, f"{tid2}.meta.json"))
        self.assertEqual(meta2.get("project"), "inferred-proj")

        # 3. 无法推断 -> 为空
        r3 = B.dispatch(from_peer="unknown-agent", peers=["worker"], mode="independent", question="q3", context_files=[])
        tid3 = r3["task_id"]
        meta3 = B.read_json(os.path.join(B.OUTBOX, f"{tid3}.meta.json"))
        self.assertEqual(meta3.get("project") or "", "")


if __name__ == "__main__":
    unittest.main()
