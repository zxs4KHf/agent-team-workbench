# -*- coding: utf-8 -*-
"""tests/test_peer_prune.py · 测试 prune_peers 安全清理失效 Agent。

验收标准：
1. 离线且距今超过 max_age_sec（默认 12h = 43200s）、且收件箱无未完成任务的 peer 被清理；
2. 在线 peer 绝不被清理；
3. 离线但未满 max_age_sec 的 peer 绝不被清理；
4. 离线满 12h 但收件箱仍有待领 (.json) 或已领未回 (.taken) 任务的 peer 绝不被清理；
5. controller 与当前 Leader 租约持有者绝不被清理。
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bus_core as B  # noqa: E402


def iso(hours_ago=0):
    return (dt.datetime.now() - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S")


class TestPeerPrune(unittest.TestCase):
    def setUp(self):
        self._saved = [(B, n, getattr(B, n)) for n in ("ROOT", "PEERS", "INBOX", "OUTBOX", "LOG", "LOCK")]
        self.tmp = tempfile.mkdtemp(prefix="prune-test-")
        B.ROOT = self.tmp
        B.PEERS = os.path.join(self.tmp, "peers")
        B.INBOX = os.path.join(self.tmp, "inbox")
        B.OUTBOX = os.path.join(self.tmp, "outbox")
        B.LOG = os.path.join(self.tmp, "log")
        B.LOCK = os.path.join(self.tmp, "console", "controller.lock")
        os.makedirs(os.path.dirname(B.LOCK), exist_ok=True)
        B.ensure_dirs()

    def tearDown(self):
        for m, n, v in self._saved:
            setattr(m, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_peer(self, peer: str, hours_ago: float = 0.0, status: str = "idle"):
        data = {
            "peer": peer,
            "status": status,
            "activity": status,
            "last_seen": iso(hours_ago),
            "model": "test-model",
        }
        p = os.path.join(B.PEERS, f"{peer}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return p

    def put_inbox(self, peer: str, filename: str):
        d = os.path.join(B.INBOX, peer)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, filename)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"task_id": filename.split(".")[0]}, f)
        return p

    def test_prune_offline_safe(self):
        # 离线 24 小时，inbox 为空 -> 成功清理
        self.make_peer("agent_old", hours_ago=24)
        pruned = B.prune_peers(max_age_sec=43200)
        self.assertIn("agent_old", pruned)
        self.assertFalse(os.path.exists(os.path.join(B.PEERS, "agent_old.json")))

    def test_prune_online_preserved(self):
        # 在线（最近 10 秒心跳）-> 绝不清理
        self.make_peer("agent_live", hours_ago=0.001)
        pruned = B.prune_peers(max_age_sec=43200)
        self.assertNotIn("agent_live", pruned)
        self.assertTrue(os.path.exists(os.path.join(B.PEERS, "agent_live.json")))

    def test_prune_recent_offline_preserved(self):
        # 离线 2 小时（小于 12 小时）-> 绝不清理
        self.make_peer("agent_recent", hours_ago=2)
        pruned = B.prune_peers(max_age_sec=43200)
        self.assertNotIn("agent_recent", pruned)
        self.assertTrue(os.path.exists(os.path.join(B.PEERS, "agent_recent.json")))

    def test_prune_with_queued_preserved(self):
        # 离线 24 小时，但 inbox 有待领任务 .json -> 绝不清理
        self.make_peer("agent_queued", hours_ago=24)
        self.put_inbox("agent_queued", "task1.json")
        pruned = B.prune_peers(max_age_sec=43200)
        self.assertNotIn("agent_queued", pruned)
        self.assertTrue(os.path.exists(os.path.join(B.PEERS, "agent_queued.json")))

    def test_prune_with_claimed_preserved(self):
        # 离线 24 小时，但 inbox 有已领未回任务 .taken -> 绝不清理
        self.make_peer("agent_claimed", hours_ago=24)
        self.put_inbox("agent_claimed", "task2.taken")
        pruned = B.prune_peers(max_age_sec=43200)
        self.assertNotIn("agent_claimed", pruned)
        self.assertTrue(os.path.exists(os.path.join(B.PEERS, "agent_claimed.json")))

    def test_prune_with_only_expired_can_be_pruned(self):
        # 离线 24 小时，inbox 只有 .expired 历史过期任务 -> 可以清理
        self.make_peer("agent_expired_only", hours_ago=24)
        self.put_inbox("agent_expired_only", "task3.json.expired")
        pruned = B.prune_peers(max_age_sec=43200)
        self.assertIn("agent_expired_only", pruned)
        self.assertFalse(os.path.exists(os.path.join(B.PEERS, "agent_expired_only.json")))

    def test_controller_and_lease_holder_preserved(self):
        # controller 离线 24 小时 -> 保护不删
        self.make_peer("controller", hours_ago=24)
        # 租约持有者 peer_leader 离线 24 小时 -> 保护不删
        self.make_peer("peer_leader", hours_ago=24)
        lease_data = {
            "holder": "peer_leader",
            "expires": (dt.datetime.now() + dt.timedelta(seconds=200)).strftime("%Y-%m-%dT%H:%M:%S"),
            "epoch": 1,
        }
        with open(B.LOCK, "w", encoding="utf-8") as f:
            json.dump(lease_data, f)

        pruned = B.prune_peers(max_age_sec=43200)
        self.assertNotIn("controller", pruned)
        self.assertNotIn("peer_leader", pruned)
        self.assertTrue(os.path.exists(os.path.join(B.PEERS, "controller.json")))
        self.assertTrue(os.path.exists(os.path.join(B.PEERS, "peer_leader.json")))

    def test_clock_skew_preserved(self):
        # 心跳落在未来（时钟/时区异常，clock_skew=True）-> 绝不清理
        skewed_time = (dt.datetime.now() + dt.timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S")
        data = {"peer": "agent_skewed", "status": "idle", "last_seen": skewed_time, "model": "m"}
        with open(os.path.join(B.PEERS, "agent_skewed.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        pruned = B.prune_peers(max_age_sec=43200)
        self.assertNotIn("agent_skewed", pruned)
        self.assertTrue(os.path.exists(os.path.join(B.PEERS, "agent_skewed.json")))

    def test_bad_timestamp_preserved(self):
        # last_seen 损坏无法解析 -> 绝不清理
        data = {"peer": "agent_badts", "status": "idle", "last_seen": "corrupted-date-xyz", "model": "m"}
        with open(os.path.join(B.PEERS, "agent_badts.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        pruned = B.prune_peers(max_age_sec=43200)
        self.assertNotIn("agent_badts", pruned)
        self.assertTrue(os.path.exists(os.path.join(B.PEERS, "agent_badts.json")))

    def test_dry_run_does_not_delete(self):
        # dry_run=True 探测出候选人但不实际删除文件
        self.make_peer("agent_dry", hours_ago=24)
        pruned = B.prune_peers(max_age_sec=43200, dry_run=True)
        self.assertIn("agent_dry", pruned)
        self.assertTrue(os.path.exists(os.path.join(B.PEERS, "agent_dry.json")))

    def test_archival_on_prune(self):
        # 实际清理时归档到 log/<日期>/pruned-peers/ 留存用户别名等元数据
        self.make_peer("agent_arch", hours_ago=24)
        p = os.path.join(B.PEERS, "agent_arch.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"peer": "agent_arch", "last_seen": iso(24), "label": "核心助手", "status": "idle"}, f)
        pruned = B.prune_peers(max_age_sec=43200)
        self.assertIn("agent_arch", pruned)
        self.assertFalse(os.path.exists(p))
        today = dt.datetime.now().strftime("%Y-%m-%d")
        arch_file = os.path.join(B.LOG, today, "pruned-peers", "agent_arch.json")
        self.assertTrue(os.path.exists(arch_file))
        with open(arch_file, "r", encoding="utf-8") as f:
            arch_data = json.load(f)
        self.assertEqual(arch_data.get("label"), "核心助手")


if __name__ == "__main__":
    unittest.main(verbosity=2)
