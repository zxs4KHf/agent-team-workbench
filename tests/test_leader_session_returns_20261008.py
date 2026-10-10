"""Leader event responses must preserve session identity across wait calls."""
import os
import re
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base, CTL
import bus_projects as P


class SessionReturns(Base):
    def test_command_response_returns_session_and_reuses_one_leader(self):
        self.mkcmd("first")
        with patch.object(P, "notify_note", return_value=""):
            first = P.console_wait(0, name="same display name")
            match = re.search(r"SESSION=([a-z0-9]+)", first)
            self.assertIsNotNone(match, first)
            sid = match.group(1)
            self.mkcmd("second")
            second = P.console_wait(0, session=sid, name="same display name")
        self.assertIn("SESSION=" + sid, second)
        self.assertEqual([r["session"] for r in P.leaders_view()["leaders"]], [sid])

    def test_notifications_return_session(self):
        for note in ("ANSWERS_COMPLETE: task", "MERGE_REQUESTED: task", "STALE_MERGE: task"):
            with self.subTest(note=note), patch.object(P, "notify_note", return_value=note):
                result = P.console_wait(0, session="stable001")
                self.assertIn(note, result)
                self.assertIn("SESSION=stable001", result)

    def test_first_notification_returns_allocated_session(self):
        with patch.object(P, "notify_note", return_value="ANSWERS_COMPLETE: task"):
            result = P.console_wait(0, name="leader")
        sid = P.leaders_view()["leaders"][0]["session"]
        self.assertIn("SESSION=" + sid, result)

    def test_command_preserves_attached_merge_notification(self):
        self.mkcmd("with-note")
        with patch.object(P, "notify_note", return_value="MERGE_REQUESTED: task"):
            result = P.console_wait(0, session="stable001")
        self.assertIn("with-note", result)
        self.assertIn("MERGE_REQUESTED: task", result)
        self.assertIn("SESSION=stable001", result)

    def test_lease_held_returns_controller_session(self):
        with patch.object(P.B, "acquire_lease", return_value={"holder": "reviewer"}):
            result = P.console_wait(0, session="stable001")
        self.assertIn("LEASE_HELD_BY reviewer", result)
        self.assertIn("SESSION=stable001", result)

    def test_relay_response_does_not_claim_controller_session(self):
        with patch.object(P.B, "acquire_lease", return_value={"holder": CTL}):
            result = P.console_wait(0, holder="reviewer", session="relay001")
        self.assertIn("LEASE_HELD_BY", result)
        self.assertNotIn("SESSION=", result)

    def test_distinct_sessions_with_same_label_remain_distinct(self):
        with patch.object(P, "notify_note", return_value=""):
            P.console_wait(0, session="leader01", name="same name")
            P.console_wait(0, session="leader02", name="same name")
        self.assertEqual(len(P.leaders_view()["leaders"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
