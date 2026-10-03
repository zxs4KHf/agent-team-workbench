"""F4：人工退回卡住的已领取指令。只用临时目录，不碰生产 console。"""
import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bus_core as B
import bus_projects as P


class RequeueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wb-requeue-")
        self.saved = (P.COMMANDS, P.DONE)
        P.COMMANDS = os.path.join(self.tmp.name, "commands")
        P.DONE = os.path.join(self.tmp.name, "done")
        os.makedirs(P.COMMANDS)
        os.makedirs(P.DONE)

    def tearDown(self):
        P.COMMANDS, P.DONE = self.saved
        self.tmp.cleanup()

    def put(self, cid, kind="custom"):
        B.write_json(os.path.join(P.COMMANDS, cid + ".json"), {"id": cid, "kind": kind, "payload": {}, "created": B.now_iso()})

    def age_claim(self, cid, seconds):
        path = os.path.join(P.COMMANDS, cid + ".taken")
        item = B.read_json(path)
        item["claimed"] = (dt.datetime.now() - dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S")
        B.write_json(path, item)

    def test_stuck_claim_requeues_and_can_be_claimed_again(self):
        self.put("c1")
        got = P.pending_commands(claim=True, holder="controller")
        self.assertEqual([c["id"] for c in got], ["c1"])
        self.age_claim("c1", 3600)
        stuck = P.claimed_commands()
        self.assertEqual(stuck[0]["status"], "processing")
        self.assertGreaterEqual(stuck[0]["claimed_age_sec"], 3500)
        r = P.requeue_command("c1", by="user")
        self.assertEqual(r["requeued_times"], 1)
        self.assertFalse(os.path.exists(os.path.join(P.COMMANDS, "c1.taken")))
        back = B.read_json(os.path.join(P.COMMANDS, "c1.json"))
        self.assertNotIn("claimed", back)
        self.assertNotIn("claimed_by", back)
        self.assertEqual(back["requeue_history"][0]["claimed_by"], "controller")
        again = P.pending_commands(claim=True, holder="controller")
        self.assertEqual([c["id"] for c in again], ["c1"])

    def test_fresh_claim_refused_without_force(self):
        self.put("c2")
        P.pending_commands(claim=True)
        with self.assertRaisesRegex(ValueError, "刚被领取"):
            P.requeue_command("c2")
        self.assertTrue(os.path.exists(os.path.join(P.COMMANDS, "c2.taken")))
        P.requeue_command("c2", force=True)
        self.assertTrue(os.path.exists(os.path.join(P.COMMANDS, "c2.json")))

    def test_queued_missing_and_bad_ids_are_refused(self):
        self.put("c3")
        with self.assertRaisesRegex(ValueError, "本来就在排队"):
            P.requeue_command("c3")
        with self.assertRaisesRegex(ValueError, "没有找到"):
            P.requeue_command("nope")
        for bad in ("../x", "", "a/b", "x" * 101):
            with self.assertRaisesRegex(ValueError, "不合法"):
                P.requeue_command(bad)

    def test_read_only_listing_never_requeues(self):
        self.put("c4")
        P.pending_commands(claim=True)
        self.age_claim("c4", 7200)
        P.pending_commands(claim=False)
        P.claimed_commands()
        self.assertTrue(os.path.exists(os.path.join(P.COMMANDS, "c4.taken")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
