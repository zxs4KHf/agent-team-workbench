"""代理 Leader 回写的两条边界（2026-10-03 接手积压实测）：sol-6-3 把用户早已采纳 / 取消的决策重新收集，状态打回「待拍板」；
又给 20 个施工 / 审查任务各建了一条「施工结果已汇总」挤进待拍板。真 Leader 的行为不变。临时数据目录。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_core as B          # noqa: E402
import bus_projects as P      # noqa: E402

RELAY = "sol-6-3"


class RelayPost(Base):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(P.PROJECTS, "p1"))
        B.write_json(os.path.join(P.PROJECTS, "p1", "project.json"), {"slug": "p1", "name": "p1", "peers": [], "mode": "independent", "context_files": []})
        B.write_json(os.path.join(P.PROJECTS, "p1", "decisions.json"), [
            {"task_id": "A1", "status": "adopted", "title": "已采纳", "execution": "released", "created": B.now_iso(), "updated": B.now_iso()},
            {"task_id": "C1", "status": "cancelled", "title": "已取消", "created": B.now_iso(), "updated": B.now_iso()}])
        B.heartbeat(RELAY, "idle", "m", "", B.now_iso(), 60)
        self.assertEqual(B.acquire_lease(RELAY).get("holder"), RELAY)

    def dec(self, tid):
        return next(d for d in P.decisions("p1") if d["task_id"] == tid)

    def meta(self, tid, mode):
        B.write_json(os.path.join(B.OUTBOX, f"{tid}.meta.json"), {"id": tid, "mode": mode, "peers": ["w1"]})

    def test_relay_cannot_reopen_user_decisions(self):
        P.post("p1", "A1", status="ready_for_merge", summary="占位", holder=RELAY)
        P.post("p1", "C1", status="merged", holder=RELAY)
        a, c = self.dec("A1"), self.dec("C1")
        self.assertEqual((a["status"], a["execution"]), ("adopted", "released"))
        self.assertEqual(c["status"], "cancelled")
        self.assertIn("已忽略", a["relay_note"])
        self.assertFalse(a.get("relay_draft"), "用户定过的不打代理草稿标记")

    def test_relay_build_and_review_summaries_skip_the_decision_queue(self):
        self.meta("B1", "build"); self.meta("R1", "review"); self.meta("Q1", "independent")
        for tid in ("B1", "R1", "Q1"):
            P.post("p1", tid, status="ready_for_merge", title="施工结果已汇总", holder=RELAY)
        self.assertEqual(self.dec("B1")["status"], "archived")
        self.assertEqual(self.dec("R1")["status"], "archived")
        self.assertIn("落地推进器", self.dec("B1")["archive_reason"])
        self.assertEqual(self.dec("Q1")["status"], "ready_for_merge", "普通讨论任务照常进待拍板")

    def test_real_leader_unchanged(self):
        B.release_lease(RELAY)
        self.meta("B2", "build")
        P.post("p1", "B2", status="ready_for_merge", title="施工完成待审")
        self.assertEqual(self.dec("B2")["status"], "ready_for_merge", "真 Leader 的施工结果照旧给用户看")


if __name__ == "__main__":
    unittest.main()
