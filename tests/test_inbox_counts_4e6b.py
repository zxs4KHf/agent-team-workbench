# -*- coding: utf-8 -*-
"""20260929-171258-4e6b · inbox 三分计数（queued / claimed / expired）与孤儿 .taken 盘点。

fb-8a7c79854b：`.taken`（已被领走但没回复）被并进 queued，界面显示成「idle 却排队 7」。
本套件锁住：queued 只数 .json、claimed 只数 .taken、.json.expired 不算进 queued，
以及拆字段后 stale_with_queue 的既有语义（离线且活没干完）不被破坏。

全部在 tempfile 临时目录里跑，不碰真实 peers/ inbox/ outbox/。
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
from scripts import orphan_taken_report as OTR  # noqa: E402


def iso(hours_ago=0):
    return (dt.datetime.now() - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S")


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = [(B, n, getattr(B, n)) for n in ("ROOT", "PEERS", "INBOX", "OUTBOX", "LOG")]
        self.tmp = tempfile.mkdtemp(prefix="4e6b-")
        B.ROOT = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.ensure_dirs()

    def tearDown(self):
        for m, n, v in self._saved:
            setattr(m, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, peer, name, body=None, hours_ago=0.0):
        d = os.path.join(B.INBOX, peer)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(body if body is not None else {"task_id": name.split(".")[0]}, f)
        if hours_ago:
            t = dt.datetime.now().timestamp() - hours_ago * 3600
            os.utime(p, (t, t))
        return p


class TestInboxCounts(Base):
    def test_三类文件分开计数(self):
        self.put("p1", "t1.json")
        self.put("p1", "t2.json")
        self.put("p1", "t3.taken")
        self.put("p1", "t4.taken")
        self.put("p1", "t5.taken")
        self.put("p1", "t6.json.expired")
        c = B.inbox_counts("p1")
        self.assertEqual(c, {"queued": 2, "claimed": 3, "expired": 1},
                         "queued 只能数 .json，claimed 只能数 .taken")

    def test_expired_不被算进_queued(self):
        self.put("p1", "t1.json.expired")
        self.assertEqual(B.inbox_counts("p1")["queued"], 0,
                         ".json.expired 以 .expired 结尾，不得混进待领")
        self.assertEqual(B.inbox_counts("p1")["expired"], 1)

    def test_目录不存在或不是目录(self):
        self.assertEqual(B.inbox_counts("never-seen"), {"queued": 0, "claimed": 0, "expired": 0})
        with open(os.path.join(B.INBOX, "a-file"), "w", encoding="utf-8") as f:
            f.write("x")
        self.assertEqual(B.inbox_counts("a-file"), {"queued": 0, "claimed": 0, "expired": 0})

    def test_inbox_queued_仍是总量(self):
        self.put("p1", "t1.json")
        self.put("p1", "t2.taken")
        self.assertEqual(B.inbox_queued("p1"), 2,
                         "relay 的 skip_stale_queue 依赖总量，拆字段不能改它")

    def test_status_分开返回(self):
        self.put("p1", "t1.json")
        self.put("p1", "t2.taken")
        self.put("p2", "t3.taken")
        self.put("p3", "t4.json.expired")
        st = B.status()
        self.assertEqual(st["queued"], {"p1": 1})
        self.assertEqual(st["claimed"], {"p1": 1, "p2": 1})
        self.assertEqual(st["expired"], {"p3": 1})
        self.assertEqual(st["queued_total"], {"p1": 2, "p2": 1})


class TestStaleSemantics(Base):
    """验收 4：stale_with_queue 的既有语义（离线且队列非空）不被破坏。"""

    def test_离线且只有已领未回_仍是_stale_with_queue(self):
        st = {"online": False, "queued": 0, "claimed": 3, "status": "idle"}
        self.assertEqual(B.agent_state(st), "stale_with_queue",
                         "拆字段后 queued=0，但 .taken 同样说明活没干完")

    def test_离线且只有待领_仍是_stale_with_queue(self):
        self.assertEqual(B.agent_state({"online": False, "queued": 2, "claimed": 0}), "stale_with_queue")

    def test_离线且两者皆空_是_offline(self):
        self.assertEqual(B.agent_state({"online": False, "queued": 0, "claimed": 0}), "offline")

    def test_在线不受影响(self):
        self.assertEqual(B.agent_state({"online": True, "status": "idle", "queued": 0, "claimed": 9}), "idle")

    def test_full_state_字段齐备且口径正确(self):
        import bus_projects as P
        self.put("p1", "t1.json")
        self.put("p1", "t2.taken")
        self.put("p1", "t3.json.expired")
        B.write_json(os.path.join(B.PEERS, "p1.json"), {"peer": "p1", "model": "m", "status": "idle", "last_seen": iso(0)})
        pe = next(x for x in P._full_state()["peers"] if x["peer"] == "p1")
        self.assertEqual((pe["queued"], pe["claimed"], pe["expired"], pe["queued_total"]), (1, 1, 1, 2))
        self.assertFalse(pe["stale_with_queue"], "在线的 peer 不该被标成离线有排队")


class TestOrphanReport(Base):
    """验收 3：dry-run 盘点脚本。默认只打印，绝不改动文件。"""

    def test_盘点内容与排序(self):
        self.put("opus4", "T-old.taken", {"task_id": "T-old", "mode": "review", "deadline": iso(100)}, hours_ago=137.2)
        self.put("opus4", "T-new.taken", {"task_id": "T-new", "mode": "build", "deadline": iso(-5)}, hours_ago=1.5)
        self.put("opus4", "T-queued.json")          # 待领的不算孤儿
        B.write_json(os.path.join(B.OUTBOX, "T-new.meta.json"), {"id": "T-new", "peers": ["opus4"]})
        rows = OTR.scan(root=self.tmp)
        self.assertEqual([r["task_id"] for r in rows], ["T-old", "T-new"], "按滞留时长倒序")
        self.assertAlmostEqual(rows[0]["stale_hours"], 137.2, delta=0.2)
        self.assertEqual(rows[0]["past_deadline"], "是")
        self.assertFalse(rows[0]["task_open"], "outbox 没有 meta = 任务已归档")
        self.assertTrue(rows[1]["task_open"])
        self.assertFalse(rows[1]["this_peer_answered"])

    def test_已回答的孤儿被标出来(self):
        self.put("p1", "T1.taken", hours_ago=10)
        os.makedirs(B.OUTBOX, exist_ok=True)
        with open(os.path.join(B.OUTBOX, "T1.p1.md"), "w", encoding="utf-8") as f:
            f.write("ans")
        self.assertTrue(OTR.scan(root=self.tmp)[0]["this_peer_answered"])

    def test_min_hours_过滤(self):
        self.put("p1", "T1.taken", hours_ago=1)
        self.put("p1", "T2.taken", hours_ago=50)
        self.assertEqual([r["task_id"] for r in OTR.scan(min_hours=24, root=self.tmp)], ["T2"])

    def test_脚本只读_不动任何文件(self):
        self.put("p1", "T1.taken", hours_ago=10)
        self.put("p1", "T2.json")
        before = {}
        for root, _, fs in os.walk(self.tmp):
            for f in fs:
                p = os.path.join(root, f)
                before[p] = os.path.getmtime(p)
        OTR.main(["--root", self.tmp, "--json"])
        after = {}
        for root, _, fs in os.walk(self.tmp):
            for f in fs:
                p = os.path.join(root, f)
                after[p] = os.path.getmtime(p)
        self.assertEqual(before, after, "盘点脚本绝不允许新增/删除/改写任何文件")


if __name__ == "__main__":
    unittest.main(verbosity=2)
