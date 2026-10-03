"""给某一位 Leader 会话改名（多活）：只动这一位，不动别人、不动顶层 label。
跑法：py -3 tests/test_leader_rename_20261001.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base, CTL  # noqa: E402
import bus_core as B      # noqa: E402
import bus_projects as P  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frontsrc import front_src  # noqa: E402  ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」


class LeaderRename(Base):
    def setUp(self):
        super().setUp()
        B.heartbeat(CTL, "idle", "controller", session="aaaa1111")
        B.heartbeat(CTL, "idle", "controller", session="bbbb2222", label="fable · B")

    def labels(self):
        return {r["session"]: r["label"] for r in P.leaders_view()["leaders"]}

    def test_rename_one_session_only(self):
        B.set_session_label(CTL, "aaaa1111", "opus · A")
        self.assertEqual(self.labels(), {"aaaa1111": "opus · A", "bbbb2222": "fable · B"})
        self.assertNotIn("label", B.read_json(os.path.join(B.PEERS, CTL + ".json"), {}), "顶层 label 不动")

    def test_name_survives_later_heartbeats_without_name(self):
        B.set_session_label(CTL, "aaaa1111", "opus · A")
        B.heartbeat(CTL, "idle", "controller", session="aaaa1111")
        self.assertEqual(self.labels()["aaaa1111"], "opus · A")

    def test_empty_clears(self):
        B.set_session_label(CTL, "bbbb2222", "")
        self.assertEqual(self.labels()["bbbb2222"], "")

    def test_unknown_session_rejected(self):
        with self.assertRaises(KeyError):
            B.set_session_label(CTL, "nope0000", "x")

    def test_label_validated(self):
        with self.assertRaises(ValueError):
            B.set_session_label(CTL, "aaaa1111", "x" * 40)

    def test_board_has_rename_button_per_leader(self):
        src = front_src("views2.js")
        self.assertIn('data-action="rename-leader" data-session=', src)


if __name__ == "__main__":
    unittest.main()
