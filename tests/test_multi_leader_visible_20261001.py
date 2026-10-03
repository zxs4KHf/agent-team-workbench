"""多活 Leader 看得见、卡住的指令能回收（2026-10-01）。

1. 每个 Leader 会话有自己的显示名；新会话接入是「多一位」，不会把别人的名字换掉；
2. 已断开会话手上的指令在其他在线 Leader 等待时自动退回队列，带 ⚠ 提示；正在处理的会话手上的单不碰；
3. 状态接口给出 leaders 列表与指令队列三分。
跑法：py -3 tests/test_multi_leader_visible_20261001.py
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base, CTL  # noqa: E402  复用同一套临时目录夹具
import bus_core as B      # noqa: E402
import bus_projects as P  # noqa: E402


def ago(sec):
    return (dt.datetime.now().astimezone() - dt.timedelta(seconds=sec)).replace(microsecond=0).isoformat()


class LeadersVisible(Base):
    def sessions(self):
        return (B.read_json(os.path.join(B.PEERS, CTL + ".json"), {}) or {}).get("sessions") or {}

    def test_each_session_keeps_its_own_name(self):
        B.heartbeat(CTL, "idle", "controller", session="aaaa1111", label="opus · A")
        B.heartbeat(CTL, "idle", "controller", session="bbbb2222", label="fable · B")
        B.heartbeat(CTL, "idle", "controller", session="aaaa1111")   # 之后不带名字的心跳不清掉名字
        s = self.sessions()
        self.assertEqual((s["aaaa1111"]["label"], s["bbbb2222"]["label"]), ("opus · A", "fable · B"))
        rows = P.leaders_view()["leaders"]
        self.assertEqual([r["label"] for r in rows], ["opus · A", "fable · B"], "按接入先后排，第二位是新增一行")

    def test_keepalive_is_not_listed_as_leader(self):
        B.heartbeat(CTL, "idle", "controller", session="keepalive")
        B.heartbeat(CTL, "idle", "controller", session="aaaa1111", label="A")
        self.assertEqual([r["session"] for r in P.leaders_view()["leaders"]], ["aaaa1111"])

    def claimed(self, cid, session, age_sec, by=CTL):
        self.mkcmd(cid)
        os.rename(os.path.join(P.COMMANDS, cid + ".json"), os.path.join(P.COMMANDS, cid + ".taken"))
        item = B.read_json(os.path.join(P.COMMANDS, cid + ".taken"), {})
        item.update(claimed=ago(age_sec), claimed_by=by, claimed_session=session)
        B.write_json(os.path.join(P.COMMANDS, cid + ".taken"), item)

    def test_dead_session_claims_are_released_live_ones_kept(self):
        B.heartbeat(CTL, "idle", "controller", session="live0001", label="A")
        self.claimed("c-dead", "gone0001", 3600)       # 领取会话早已不在
        self.claimed("c-legacy", None, 7200)           # 升级前没有会话号的旧领取
        self.claimed("c-live", "live0001", 3600)       # 在线会话还在处理，哪怕很久也不动
        self.claimed("c-young", "gone0002", 60)        # 刚领：会话心跳还没过期窗口，无法判定断开
        q = P.leaders_view()["leader_queue"]
        self.assertEqual((q["orphaned"], q["claimed"]), (2, 2))
        released = P.release_orphan_commands(by="test")
        self.assertEqual(sorted(released), ["c-dead", "c-legacy"])
        for cid in ("c-dead", "c-legacy"):
            item = B.read_json(os.path.join(P.COMMANDS, cid + ".json"), {})
            self.assertEqual(item.get("redelivery_warning"), P.ORPHAN_NOTE)
            self.assertFalse(os.path.exists(os.path.join(P.COMMANDS, cid + ".taken")))
        for cid in ("c-live", "c-young"):
            self.assertTrue(os.path.exists(os.path.join(P.COMMANDS, cid + ".taken")))

    def test_released_even_when_no_leader_is_online(self):
        # 2026-10-02：一个 Leader 都不在线时没人调 wait，原来指令永远卡在「处理中」；现在守护每圈调用同一个函数
        self.claimed("c-relay", None, 3600, by="sol-6-3")   # 代理 Leader 领的，已离线
        self.claimed("c-ctl", "gone0001", 3600)             # 主 Leader 会话已消失
        self.assertEqual(sorted(P.release_orphan_commands(by="guard")), ["c-ctl", "c-relay"])
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bus_guard.py"), encoding="utf-8").read()
        self.assertIn('release_orphan_commands(by="guard")', src, "守护每圈调用")

    def test_relay_claim_released_only_when_relay_offline(self):
        B.heartbeat(CTL, "idle", "controller", session="live0001")
        self.claimed("c-relay", None, 3600, by="opus1")
        self.assertEqual(P.release_orphan_commands(), ["c-relay"])   # opus1 没有心跳 = 离线

    def test_console_wait_releases_and_redelivers_with_warning(self):
        self.claimed("c-dead", "gone0001", 3600)
        out = P.console_wait(1, session="live0001", name="opus · A")
        self.assertIn("c-dead", out)
        self.assertIn("⚠", out)
        self.assertEqual(self.sessions()["live0001"]["label"], "opus · A")
        taken = B.read_json(os.path.join(P.COMMANDS, "c-dead.taken"), {})
        self.assertEqual(taken.get("claimed_session"), "live0001")
        self.assertEqual(len(taken.get("requeue_history") or []), 1, "退回留痕，便于事后核对是否重复执行")


if __name__ == "__main__":
    unittest.main()
