"""两个运维脚本与带偏移的时间戳（ADR 0010）：gc_builds 原来遇到带偏移的心跳直接 TypeError 跑不起来（2026-10-02 实测）；
orphan_taken_report 比截止时间同样会崩，且刚写的文件约一半概率被漏掉（b66e）。临时目录。"""
import datetime as dt
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)
import bus_core as B            # noqa: E402
import gc_builds as G           # noqa: E402
import orphan_taken_report as O  # noqa: E402


class ScriptsTz(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="scripts-tz-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_gc_builds_reads_offset_heartbeats(self):
        os.makedirs(os.path.join(self.tmp, "peers"))
        B.write_json(os.path.join(self.tmp, "peers", "a1.json"), {"peer": "a1", "task_id": "T1", "last_seen": B.now_iso()})
        B.write_json(os.path.join(self.tmp, "peers", "a2.json"), {"peer": "a2", "task_id": "T2", "last_seen": "2026-01-01T00:00:00+08:00"})
        for now in (dt.datetime.now(), B.now_dt()):     # 调用方传无时区 / 带时区的「现在」都要能算
            busy = G.active_task_ids(self.tmp, now, 2.0)
            self.assertIn("T1", busy)
            self.assertNotIn("T2", busy, "一天前的心跳不算正在做")

    def test_orphan_report_offset_deadline_and_fresh_file(self):
        inbox = os.path.join(self.tmp, "inbox", "p1")
        os.makedirs(inbox); os.makedirs(os.path.join(self.tmp, "outbox"))
        for i in range(20):   # 刚写的文件：mtime 可能比随后的「现在」晚一点点，min_hours=0 不能漏
            B.write_json(os.path.join(inbox, f"T{i}.taken"), {"id": f"T{i}", "deadline": B.iso_after(-60)})
        rows = O.scan(0.0, root=self.tmp)
        self.assertEqual(len(rows), 20)
        self.assertTrue(all(r["past_deadline"] == "是" for r in rows), "带偏移的截止时间能比较")


if __name__ == "__main__":
    unittest.main()
