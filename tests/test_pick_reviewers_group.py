"""pick_reviewers 同组回避（软规则）：有别组在线先挑别组；只有同组在线时回退同组；开关关掉时按原顺序。"""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bus_core as B  # noqa: E402

PEERS = [{"peer": "fable2", "online": True, "status": "idle"}, {"peer": "fable6", "online": True, "status": "idle"},
         {"peer": "opus1", "online": True, "status": "idle"}, {"peer": "controller", "online": True, "status": "idle"}]
GROUP = {"fable2": "claude-fable-5", "fable6": "claude-fable-5", "opus1": "claude-opus-5"}


def roles(policy=None):
    return {"assignments": {"reviewer_groups": ["claude-fable-5", "claude-opus-5"]}, "policy": policy or {}}


class PickReviewersCrossGroup(unittest.TestCase):
    def pick(self, peers=PEERS, policy=None, **kw):
        with mock.patch.object(B, "list_peers", return_value=peers), mock.patch.object(B, "read_roles", return_value=roles(policy)), \
                mock.patch.object(B, "peer_group", side_effect=lambda p: GROUP.get(p, "")):
            return B.pick_reviewers(**kw)

    def test_prefers_other_group(self):
        self.assertEqual(self.pick(exclude=["fable6"], avoid_group="claude-fable-5"), ["opus1"])

    def test_falls_back_to_same_group(self):
        peers = [p for p in PEERS if p["peer"] != "opus1"]
        self.assertEqual(self.pick(peers=peers, exclude=["fable6"], avoid_group="claude-fable-5"), ["fable2"])

    def test_policy_off_keeps_old_order(self):
        self.assertEqual(self.pick(policy={"review_prefer_other_group": False}, exclude=["fable6"], avoid_group="claude-fable-5"), ["fable2"])

    def test_no_avoid_group_is_backward_compatible(self):
        self.assertEqual(self.pick(exclude=["fable6"]), ["fable2"])
        self.assertNotIn("controller", self.pick(n=5))


if __name__ == "__main__":
    unittest.main()
