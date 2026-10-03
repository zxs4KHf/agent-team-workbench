"""分组推荐：对没接入过的 Agent 调用直接报错，不进锁、不在 peers/ 下留 .mutex（e8f5 系列里唯一还没落地的一条）。临时目录。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_core as B          # noqa: E402


class GhostPeer(Base):
    def test_unknown_peer_leaves_no_lock_file(self):
        with self.assertRaises(ValueError):
            B._mutate_group_hint("ghost1", lambda h: h)   # 确认 / 拒绝 / 自动归类最终都走这里
        self.assertEqual([f for f in os.listdir(B.PEERS) if "ghost1" in f], [], "没接入过的 Agent 不留任何文件")

    def test_known_peer_still_works(self):
        B.heartbeat("real1", "idle", "claude-opus-5", "", B.now_iso(), 60)
        self.assertIn("candidate", B.suggest_group("real1"))


if __name__ == "__main__":
    unittest.main()
