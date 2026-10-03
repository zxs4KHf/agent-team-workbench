"""已恢复的 Leader 交接通知只在概览顶部挂 30 分钟（2026-10-03：顶部挂着 9 小时前早已恢复的交接）。不改 acked，正在发生的与不稳定告警照常显示。"""
import datetime as dt
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bus_core as B            # noqa: E402
import bus_lead_notify as LN    # noqa: E402


def ago(minutes):
    return (dt.datetime.now().astimezone() - dt.timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


class Quiet(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="leadquiet-")
        os.makedirs(os.path.join(self.root, "console"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def ids(self, lease):
        with mock.patch.object(B, "lease", lambda: lease):
            return sorted(e["epoch"] for e in LN.lead_events_open(self.root))

    def test_old_resolved_hidden_recent_and_current_kept(self):
        LN.record_lead_event(self.root, "controller", "glm1", 3, "takeover", "x", now=ago(600))   # 10 小时前，早已恢复
        LN.record_lead_event(self.root, "glm1", "controller", 7, "handback", "x", now=ago(10))    # 10 分钟前恢复：还挂着
        self.assertEqual(self.ids({"holder": "controller", "epoch": 9}), [7])
        self.assertEqual(self.ids({"holder": "glm1", "epoch": 3}), [3, 7], "租约还停在事件那一轮：没恢复，照常显示")
        self.assertEqual(self.ids({}), [3, 7], "没人持有租约：照常显示")
        self.assertEqual(len([e for e in LN._load(self.root) if not e.get("acked")]), 2, "只是不显示，不改 acked")


if __name__ == "__main__":
    unittest.main()
