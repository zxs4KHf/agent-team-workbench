# 卡死判定修正（2026-10-01）：pending 年龄须结合领取活跃度。
# 退回队列的老指令 mtime 也是老的，只看 pending 年龄会在 Leader 消化积压时误判失联、
# 守护踢租约、保活退场，慢处理窗口里代理接管造成重复处理。
# 跑法：py -3 -X utf8 tests/test_stuck_heuristic_20261001.py（仓库根目录）
import os
import shutil
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_core as B          # noqa: E402
import bus_projects as P      # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="busstuck-")
        self._saved = (B.ROOT, B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.LOCK, B.PRIORITY, B.WORKSPACE,
                       P.PROJECTS, P.COMMANDS, P.DONE)
        B.ROOT = B.WORKSPACE = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.LOCK = os.path.join(self.tmp, "console", "controller.lock")
        B.PRIORITY = os.path.join(self.tmp, "console", "priority.json")
        P.PROJECTS, P.COMMANDS, P.DONE = (os.path.join(self.tmp, *d) for d in (("projects",), ("console", "commands"), ("console", "done")))
        for d in (B.PEERS, B.INBOX, B.OUTBOX, B.LOG, P.PROJECTS, P.COMMANDS, P.DONE, os.path.join(self.tmp, "console")):
            os.makedirs(d, exist_ok=True)

    def tearDown(self):
        (B.ROOT, B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.LOCK, B.PRIORITY, B.WORKSPACE,
         P.PROJECTS, P.COMMANDS, P.DONE) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def taken(self, cid, claimed_iso):
        B.write_json(os.path.join(P.COMMANDS, cid + ".taken"),
                     {"id": cid, "kind": "custom", "payload": {}, "created": claimed_iso,
                      "claimed": claimed_iso, "claimed_by": "controller"})


class TestFreshestClaimAge(Base):
    def test_empty_queue_returns_none(self):
        self.assertIsNone(P.freshest_claim_age())

    def test_fresh_and_old_claims_report_fresh(self):
        # 一条 40 小时前的死领取 + 一条刚发生的领取：活跃证据必须取最近的那条
        self.taken("20260929-000000-aaaa", "2026-09-29T12:00:00")
        self.taken("20261001-000000-bbbb", B.now_iso())
        age = P.freshest_claim_age()
        self.assertIsNotNone(age)
        self.assertLess(age, 10, "最近领取应反映新领取，而不是最老的那条")

    def test_guard_stuck_requires_no_consumption(self):
        # 模拟守护判定：pending 超过阈值但最近有领取 → 不得踢租约
        import bus_guard as G
        self.taken("20261001-000000-cccc", B.now_iso())
        stuck = 9999
        claim_age = G.freshest_claim_age()
        consuming = claim_age is not None and claim_age < G.STUCK_SEC
        self.assertTrue(consuming, "最近有领取时必须判定为「在消化积压」")
        self.assertFalse(stuck >= G.STUCK_SEC and not consuming, "该情形下守护不得释放租约")

    def test_guard_stuck_when_truly_dead(self):
        self.taken("20260929-000000-dddd", "2026-09-29T12:00:00")
        claim_age = P.freshest_claim_age()
        import bus_guard as G
        consuming = claim_age is not None and claim_age < G.STUCK_SEC
        self.assertFalse(consuming, "领取全是几小时前的死领取时才能判失联")


if __name__ == "__main__":
    unittest.main(verbosity=2)
