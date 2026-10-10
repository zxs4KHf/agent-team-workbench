import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bus_core as B
import bus_plugin_jobs as PJ
import bus_projects as P


class TeamQueueCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="plugin-team-queue-")
        self.saved = [(mod, name, getattr(mod, name)) for mod, names in (
            (B, ("ROOT", "PEERS", "INBOX", "OUTBOX", "LOG", "WORKSPACE", "PRIORITY", "PROFILES")),
            (P, ("PROJECTS", "COMMANDS", "DONE"))) for name in names]
        B.ROOT = B.WORKSPACE = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, n) for n in ("peers", "inbox", "outbox", "log"))
        B.PRIORITY = os.path.join(self.tmp, "console", "priority.json")
        B.PROFILES = os.path.join(self.tmp, "profiles")
        P.PROJECTS, P.COMMANDS, P.DONE = (os.path.join(self.tmp, n) for n in
                                           ("projects", "console/commands", "console/done"))
        B.ensure_dirs()
        P.ensure()
        self.plugin = [{"id": "level-design", "enabled": True, "errors": [], "config": {"project": "atlas"}}]
        for name in ("alice", "bob", "charlie"):
            self.peer(name)
        P.create_project("Atlas", ["alice", "bob"], "independent", [])
        P.update_project("atlas", {"default_team": P.PROJECT_TEAM,
                                   "teams": [{"name": "调研", "peers": ["bob", "charlie"]}]})

    def tearDown(self):
        for mod, name, value in self.saved:
            setattr(mod, name, value)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def peer(self, name, online=True):
        B.write_json(os.path.join(B.PEERS, name + ".json"),
                     {"peer": name, "label": name, "last_seen": B.now_iso() if online else "2000-01-01T00:00:00",
                      "model": "test", "activity": "idle", "wait_started": B.now_iso() if online else "",
                      "wait_timeout": 300 if online else 0})

    def payload(self, request_id="team1", **patch):
        body = {"source": "level-design", "request_id": request_id, "project": "atlas", "question": "分析关卡空间",
                "ttl_min": 1}
        body.update(patch)
        return body

    def test_options_and_paused_dynamic_team(self):
        B.write_json(os.path.join(B.PROFILES, "alice.json"), {"paused": True})
        P.update_project("atlas", {"agent_meta": {"charlie": {"paused_in_project": True}}})
        result = PJ.options("level-design", self.plugin)
        self.assertEqual((result["default_project"], result["default_mode"]), ("atlas", "race"))
        self.assertEqual([m["value"] for m in result["modes"]], list(PJ.MODE_LABELS))
        teams = {t["name"]: t for t in result["projects"][0]["teams"]}
        self.assertEqual(teams[P.DYNAMIC_TEAM]["peers"], ["bob"])
        self.assertEqual(teams[P.PROJECT_TEAM]["peers"], ["alice", "bob"])
        self.assertEqual(teams["调研"]["peers"], ["bob", "charlie"])
        self.assertEqual(teams["调研"]["kind"], "custom")

    def test_offline_default_race_claim_and_deadline_from_claim(self):
        self.peer("alice", online=False)
        self.peer("bob", online=False)
        result = PJ.submit(self.payload(), self.plugin)
        self.assertEqual(result["state"], "submitted")
        self.assertEqual(result["peers"], ["alice", "bob"])
        self.assertEqual((result["team"], result["mode"]), (P.PROJECT_TEAM, "race"))
        tid = result["task_id"]
        self.assertEqual(P.decisions("atlas")[0]["task_id"], tid)
        meta = B.read_json(os.path.join(B.OUTBOX, tid + ".meta.json"))
        self.assertEqual(meta["deadline_ts"], 0)
        self.assertEqual(meta["min_answers"], 1)
        for name in ("alice", "bob"):
            task = B.read_json(os.path.join(B.INBOX, name, tid + ".json"))
            self.assertTrue(task["queue_until_claim"])
            self.assertEqual((task["project"], task["team"], task["plugin_request_id"]),
                             ("atlas", P.PROJECT_TEAM, "team1"))
            task["created"] = "2000-01-01T00:00:00"
            B.write_json(os.path.join(B.INBOX, name, tid + ".json"), task)
        with ThreadPoolExecutor(max_workers=2) as pool:
            answers = list(pool.map(lambda p: B.wait_task(p, 0), ("alice", "bob")))
        self.assertEqual(sum("分析关卡空间" in answer for answer in answers), 1)
        winner = B.read_json(os.path.join(B.OUTBOX, tid + ".meta.json"))["race_winner"]
        taken = B.read_json(os.path.join(B.INBOX, winner, tid + ".taken"))
        self.assertGreater(taken["deadline_ts"], __import__("time").time() + 50)
        loser = "bob" if winner == "alice" else "alice"
        self.assertTrue(os.path.exists(os.path.join(B.INBOX, loser, tid + ".json" + B.RACE_LOST)))

    def test_guard_cycle_preserves_queued_task_and_claim_starts_expiry(self):
        self.peer("alice", online=False)
        self.peer("bob", online=False)
        result = PJ.submit(self.payload("guard"), self.plugin)
        tid = result["task_id"]
        self.assertEqual(B.mark_expired(), [])
        self.assertEqual(B.auto_reassign(), [])
        self.assertFalse(B.deadline_passed(B.read_json(os.path.join(B.OUTBOX, tid + ".meta.json"))))
        for peer in ("alice", "bob"):
            self.assertTrue(os.path.isfile(os.path.join(B.INBOX, peer, tid + ".json")))
        self.assertIn("分析关卡空间", B.wait_task("alice", 0))
        taken_path = os.path.join(B.INBOX, "alice", tid + ".taken")
        taken = B.read_json(taken_path)
        self.assertGreater(taken["deadline_ts"], time.time())
        taken["deadline_ts"] = time.time() - 1
        B.write_json(taken_path, taken)
        self.assertTrue(B.deadline_passed(B.read_json(taken_path)))
        _pending, released = B.reclaim_claims("alice")
        self.assertEqual(released, [tid])
        self.assertTrue(os.path.isfile(os.path.join(B.INBOX, "alice", tid + ".json.expired")))
        self.assertTrue(B.deadline_passed({"deadline_ts": 0}))  # 普通任务的既有截止语义

    def test_same_peer_concurrent_claim_returns_once(self):
        result = PJ.submit(self.payload("same", peers=["alice"]), self.plugin)
        with ThreadPoolExecutor(max_workers=2) as pool:
            answers = list(pool.map(lambda _: B.wait_task("alice", 0), range(2)))
        self.assertEqual(sum("分析关卡空间" in answer for answer in answers), 1)
        self.assertTrue(os.path.isfile(os.path.join(B.INBOX, "alice", result["task_id"] + ".taken")))
        self.assertNotIn("分析关卡空间", B.wait_task("alice", 0))

    def test_team_scope_validation_and_frozen_replay(self):
        with self.assertRaises(PJ.JobError):
            PJ.submit(self.payload("outside", team="调研", peers=["alice"]), self.plugin)
        first = PJ.submit(self.payload("freeze", team="调研"), self.plugin)
        P.update_project("atlas", {"teams": [{"name": "调研", "peers": ["alice"]}]})
        replay = PJ.submit(self.payload("freeze", team="调研"), self.plugin)
        self.assertEqual((replay["task_id"], replay["peers"]), (first["task_id"], ["bob", "charlie"]))
        self.assertTrue(replay["deduplicated"])
        with self.assertRaises(PJ.JobError) as caught:
            PJ.submit(self.payload("freeze", team="调研", mode="discuss"), self.plugin)
        self.assertEqual(caught.exception.status, 409)

    def test_nonrace_requires_every_reply_even_with_offline_member(self):
        self.peer("bob", online=False)
        result = PJ.submit(self.payload("all", mode="discuss"), self.plugin)
        tid = result["task_id"]
        self.assertIn("分析关卡空间", B.wait_task("alice", 0))
        B.reply(tid, "alice", "第一份")
        progress = P.task_progress(tid)
        self.assertEqual(progress["quorum"], 2)
        self.assertFalse(progress["complete"])
        self.assertIn("分析关卡空间", B.wait_task("bob", 0))
        B.reply(tid, "bob", "第二份")
        self.assertTrue(P.task_progress(tid)["complete"])

    def test_association_and_lost_dispatch_response_recover(self):
        real_dispatch = B.dispatch

        def dispatch_then_lost(*args, **kwargs):
            real_dispatch(*args, **kwargs)
            raise ConnectionError("response lost")

        with mock.patch.object(B, "dispatch", side_effect=dispatch_then_lost):
            first = PJ.submit(self.payload("lost"), self.plugin)
        self.assertEqual(first["state"], "unknown")
        recovered = PJ.lookup("level-design", "lost")
        self.assertEqual(recovered["state"], "submitted")
        self.assertEqual(P.decisions("atlas")[0]["task_id"], recovered["task_id"])
        with mock.patch.object(B, "dispatch", side_effect=AssertionError("duplicate")):
            self.assertEqual(PJ.submit(self.payload("lost"), self.plugin)["task_id"], recovered["task_id"])

        with mock.patch.object(P, "upsert_decision", side_effect=OSError("disk unavailable")):
            partial = PJ.submit(self.payload("associate"), self.plugin)
        self.assertEqual(partial["state"], "unknown")
        repaired = PJ.lookup("level-design", "associate")
        self.assertEqual(repaired["state"], "submitted")
        self.assertIn(repaired["task_id"], [d["task_id"] for d in P.decisions("atlas")])

    def test_cancel_and_claim_race(self):
        first = PJ.submit(self.payload("cancel"), self.plugin)
        cancelled = PJ.cancel({"source": "level-design", "request_id": "cancel"}, self.plugin)
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(PJ.lookup("level-design", "cancel")["state"], "cancelled")
        self.assertEqual(PJ.cancel({"source": "level-design", "request_id": "cancel"}, self.plugin)["task_id"], first["task_id"])
        self.assertFalse(any(name.endswith(".json") for peer in ("alice", "bob") for name in os.listdir(os.path.join(B.INBOX, peer))))
        self.assertEqual(next(d for d in P.decisions("atlas") if d["task_id"] == first["task_id"])["status"], "cancelled")

        second = PJ.submit(self.payload("racing", peers=["alice"]), self.plugin)
        gate = threading.Barrier(2)

        def claim():
            gate.wait()
            return B.wait_task("alice", 0)

        def cancel():
            gate.wait()
            try:
                return PJ.cancel({"source": "level-design", "request_id": "racing"}, self.plugin)
            except PJ.JobError as exc:
                return exc.status

        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = pool.submit(claim)
            cancelled = pool.submit(cancel)
            task_text, cancel_result = claimed.result(), cancelled.result()
        if "分析关卡空间" in task_text:
            self.assertEqual(cancel_result, 409)
        else:
            self.assertEqual(cancel_result["state"], "cancelled")
        self.assertEqual(PJ.lookup("level-design", "racing")["task_id"], second["task_id"])


if __name__ == "__main__":
    unittest.main()
