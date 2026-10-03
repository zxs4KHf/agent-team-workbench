"""20260929-154744-f0ca · Agent 状态双写映射层 + online 解耦。

全部在临时目录里跑：不碰真实 peers/ inbox/ outbox/ console/。
"""
import datetime as dt
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bus_core as B  # noqa: E402


def iso(sec_ago=0):
    return (dt.datetime.now() - dt.timedelta(seconds=sec_ago)).strftime("%Y-%m-%dT%H:%M:%S")


class TestActivityMapping(unittest.TestCase):
    def setUp(self):
        self._saved = [(B, n, getattr(B, n)) for n in ("ROOT", "PEERS", "INBOX", "OUTBOX", "LOG")]
        self.tmp = tempfile.mkdtemp(prefix="f0ca-")
        B.ROOT = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.ensure_dirs()

    def tearDown(self):
        for m, n, v in self._saved:
            setattr(m, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rec(self, peer="p1"):
        return B.read_json(os.path.join(B.PEERS, peer + ".json"), {})

    # ---- 词表 ----
    def test_normalize_activity(self):
        self.assertEqual(B.normalize_activity("busy"), "think")
        self.assertEqual(B.normalize_activity("relay"), "think")
        self.assertEqual(B.normalize_activity("idle"), "idle")
        self.assertEqual(B.normalize_activity("build"), "build")
        self.assertEqual(B.normalize_activity("discuss"), "discuss")
        self.assertEqual(B.normalize_activity("nonsense"), "idle")   # 认不出一律 idle，不返回未知值
        self.assertEqual(B.normalize_activity(None), "idle")

    def test_vocab_is_complete(self):
        v = B.agent_vocab()
        for k in v["states"]:
            self.assertIn(k, v["label"])
            self.assertIn(k, v["tone"])
        self.assertEqual(set(v["activities"]), set(B.ACTIVITIES))
        self.assertEqual(v["role_label"]["relay"], "代理 Leader")

    # ---- 双写：旧 status 字段值必须与改造前一致 ----
    def test_double_write_legacy_status(self):
        for sent, old_status, act in (("idle", "idle", "idle"), ("busy", "busy", "think"),
                                      ("relay", "busy", "think"), ("build", "busy", "build"),
                                      ("discuss", "busy", "discuss")):
            B.heartbeat("p1", sent, "m")
            self.assertEqual(self.rec()["status"], old_status, sent)
            self.assertEqual(self.rec()["activity"], act, sent)
            st = B.peer_state("p1")
            self.assertEqual(st["status"], old_status, sent)
            self.assertEqual(st["activity"], act, sent)

    def test_role_is_orthogonal(self):
        B.heartbeat("p1", "think", "m", role="relay")
        self.assertEqual(self.rec()["role"], "relay")
        self.assertEqual(self.rec()["activity"], "think")
        self.assertEqual(self.rec()["status"], "busy")
        B.heartbeat("p1", "idle", "m")           # 下一拍没带 role：角色清空，不残留
        self.assertEqual(self.rec()["role"], "")
        B.heartbeat("p1", "idle", "m", role="bogus")
        self.assertEqual(self.rec()["role"], "")  # 非白名单角色不落盘

    # ---- online 解耦 ----
    def test_online_only_depends_on_age(self):
        B.heartbeat("p1", "relay", "m")          # 旧口径：status=relay 不在 (idle,busy) 里 -> 曾判离线
        self.assertTrue(B.peer_state("p1")["online"], "relay 期间必须在线")
        B.heartbeat("p2", "build", "m")
        self.assertTrue(B.peer_state("p2")["online"])

    def test_stale_is_offline(self):
        d = {"peer": "p3", "status": "idle", "activity": "idle", "last_seen": iso(B.HEARTBEAT_STALE_SEC + 60)}
        B.write_json(os.path.join(B.PEERS, "p3.json"), d)
        self.assertFalse(B.peer_state("p3")["online"])
        self.assertEqual(B.agent_state(B.peer_state("p3")), "offline")

    def test_unknown_when_record_missing(self):
        st = B.peer_state("never-seen")
        self.assertFalse(st["online"])
        self.assertEqual(B.agent_state(st), "unknown")

    def test_stale_with_queue(self):
        d = {"peer": "p4", "status": "idle", "last_seen": iso(B.HEARTBEAT_STALE_SEC + 60)}
        B.write_json(os.path.join(B.PEERS, "p4.json"), d)
        st = B.peer_state("p4")
        st["queued"] = 2
        self.assertEqual(B.agent_state(st), "stale_with_queue")

    def test_agent_state_online_keys(self):
        for act, key in (("idle", "idle"), ("think", "think"), ("build", "build"), ("discuss", "discuss")):
            B.heartbeat("p5", act, "m")
            st = B.peer_state("p5")
            st["queued"] = 0
            self.assertEqual(B.agent_state(st), key)

    # ---- 旧值消费方：空闲优先的排序不能失效 ----
    def test_idle_first_sort(self):
        rows = [{"peer": "a", "status": "busy", "activity": "build"},
                {"peer": "b", "status": "idle", "activity": "idle"},
                {"peer": "c", "activity": "discuss"},
                {"peer": "d", "status": "relay"}]          # 只有旧字段：也要认出「非空闲」
        rows.sort(key=lambda p: (B.activity_of(p) != "idle", p["peer"]))
        self.assertEqual([r["peer"] for r in rows], ["b", "a", "c", "d"])

    def test_activity_of_falls_back_to_status(self):
        self.assertEqual(B.activity_of({"status": "busy"}), "think")
        self.assertEqual(B.activity_of({"status": "relay"}), "think")
        self.assertEqual(B.activity_of({}), "idle")
        self.assertEqual(B.activity_of({"activity": "build", "status": "busy"}), "build")


if __name__ == "__main__":
    unittest.main(verbosity=2)
