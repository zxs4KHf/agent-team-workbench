"""F01 feedback lifecycle regression tests; state is isolated to a temporary directory."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bus_events as E  # noqa: E402
import bus_feedback as FB  # noqa: E402
import bus_projects as P  # noqa: E402


class FeedbackResolutionF01(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="feedback-f01-")
        self.saved = (E.EVENTS, FB.FEEDBACK, P.COMMANDS, P.DONE)
        E.EVENTS = os.path.join(self.tmp, "events")
        FB.FEEDBACK = os.path.join(self.tmp, "feedback")
        P.COMMANDS = os.path.join(self.tmp, "commands")
        P.DONE = os.path.join(self.tmp, "done")
        os.makedirs(P.COMMANDS)
        os.makedirs(P.DONE)

    def tearDown(self):
        E.EVENTS, FB.FEEDBACK, P.COMMANDS, P.DONE = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def record(self, fid):
        return next(item for item in FB.items() if item["id"] == fid)

    def dispatch(self):
        fid = FB.create("派工之后问题仍待验证")["id"]
        return fid, FB.update(fid, "converted")["command_id"]

    def test_successful_dispatch_ack_only_awaits_verification(self):
        fid, cid = self.dispatch()
        self.assertEqual(P.ack_command(cid, "已修复，全部完成", ok=True), f"ACK {cid}")
        rec = self.record(fid)
        self.assertEqual(rec["status"], "awaiting_verification")
        self.assertEqual(rec["command_id"], cid)
        self.assertEqual(FB.counts()["awaiting_verification"], 1)
        self.assertNotIn("feedback.done", [e["type"] for e in E.read()["events"]])

    def test_negative_ack_is_deferred_not_dismissed(self):
        fid, cid = self.dispatch()
        P.ack_command(cid, "temporarily blocked", ok=False)
        self.assertEqual(self.record(fid)["status"], "deferred")
        self.assertEqual(FB.counts()["actionable"], 1)

    def test_explicit_close_requires_evidence_and_is_idempotent(self):
        fid, cid = self.dispatch()
        P.ack_command(cid, "dispatched", ok=True)
        with self.assertRaises(ValueError):
            FB.update(fid, "done")
        done = FB.update(fid, "done", "reproduced and verified on fixture")
        self.assertEqual(done["resolution"]["command_id"], cid)
        again = FB.update(fid, "done")
        self.assertEqual(done, again)
        self.assertEqual(FB.counts()["actionable"], 0)

    def test_retry_creates_new_round_and_late_ack_cannot_resolve_it(self):
        fid, old = self.dispatch()
        P.ack_command(old, "blocked", ok=False)
        new = FB.update(fid, "converted")["command_id"]
        self.assertNotEqual(old, new)
        P.ack_command(old, "actually success now", ok=True)
        self.assertEqual(self.record(fid)["status"], "converted")
        self.assertEqual(self.record(fid)["command_id"], new)
        P.ack_command(new, "completed dispatch", ok=True)
        self.assertEqual(self.record(fid)["status"], "awaiting_verification")

    def test_resolution_cas_preserves_newer_round(self):
        fid, old = self.dispatch()
        P.ack_command(old, "dispatch", ok=True)
        new = FB.update(fid, "converted")["command_id"]
        before = self.record(fid)
        result = FB.update(fid, "done", "old screen", expect="awaiting_verification", expect_command_id=old)
        self.assertEqual(result, before)
        self.assertEqual(result["command_id"], new)


if __name__ == "__main__":
    unittest.main()
