# -*- coding: utf-8 -*-
"""Leader 在线名单过滤过路调用者（fb-1b5ec6d4cf / 任务 20261011-155643-1491）。

任务包 5 条不变量的对应测试（每条都有能变红的负对照）：
1. 在线 Leader 数 == 渲染行数（同一会话不算两次）→ test_同一会话不重复计数
2. 只 wait、无名、未领指令、未持租约的会话不进 leaders 不计人数 → test_过路wait不留痕（负对照：修复前会有 1 行无名 Leader）
3. 真 Leader 不受影响：1 个显示 1 行带名，2 个显示 2 行 → test_真Leader单行 / test_两个真Leader并行两行
4. 过路会话不无限堆积：临时会话 120 秒短 TTL 清除 → test_临时会话短TTL清理
5. 孤儿判定不受过滤影响：临时会话领的指令在其心跳新鲜时不退回，断开后正常回收 → test_临时会话领的指令不误判孤儿（负对照）+ 断开后回收

全部跑在临时目录，不碰生产 peers/ console/。
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bus_core as B        # noqa: E402
import bus_projects as P    # noqa: E402


class LeaderSessionsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="leadsess_")
        self.p = [mock.patch.object(B, "PEERS", os.path.join(self.tmp, "peers")),
                  mock.patch.object(B, "ROOT", self.tmp),
                  mock.patch.object(P, "COMMANDS", os.path.join(self.tmp, "commands")),
                  mock.patch.object(P, "DONE", os.path.join(self.tmp, "done")),
                  mock.patch.object(P, "PROJECTS", os.path.join(self.tmp, "projects"))]
        for x in self.p:
            x.start()
        os.makedirs(os.path.join(self.tmp, "peers"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp, "commands"), exist_ok=True)
        P.ensure()
        P._PFT_CACHE.clear()

    def tearDown(self):
        for x in reversed(self.p):
            x.stop()

    def _wait(self, timeout=1, **kw):
        return P.console_wait(timeout, **kw)

    def _sessions(self):
        return (B.read_json(os.path.join(B.PEERS, "controller.json"), None) or {}).get("sessions") or {}

    def _backdate_claim(self, cid, sec=700):
        path = os.path.join(P.COMMANDS, cid + ".taken")
        c = B.read_json(path, None) or {}
        c["claimed"] = (datetime.now() - timedelta(seconds=sec)).isoformat(timespec="seconds")
        B.write_json(path, c)

    def _expire_session(self, sid, sec=700):
        with B.LN.file_mutex(B.LN.peer_mutex_path(B.PEERS, "controller")):
            path = os.path.join(B.PEERS, "controller.json")
            data = B.read_json(path, {}) or {}
            if sid in (data.get("sessions") or {}):
                old = (datetime.now() - timedelta(seconds=sec)).isoformat(timespec="seconds")
                data["sessions"][sid]["last_seen"] = old
                B.write_json(path, data)


class PassiveWaitersFiltered(LeaderSessionsBase):
    def test_过路wait不留痕_负对照(self):
        # 修复前：无名 wait 注册的会话会出现在 leaders_view 里（负对照基线，修复后必须为空）
        out = self._wait(1)
        self.assertIn("SESSION=", out)
        sid = out.split("SESSION=")[1].split("（")[0].split("\n")[0].strip()
        self.assertTrue(self._sessions().get(sid, {}).get("temp"), "过路会话必须带 temp 标记留在 sessions 供回收")
        v = P.leaders_view()
        self.assertEqual(v["leaders"], [], "无名、未领指令的过路会话不得进 Leader 名单")
        self.assertEqual(len(v["leaders"]), len(P.real_leader_sessions()), "不变量1：人数==行数")

    def test_真Leader单行(self):
        self._wait(1, session="s1", name="leader-x")
        v = P.leaders_view()
        self.assertEqual([r["label"] for r in v["leaders"]], ["leader-x"], "单个真 Leader 一行、带自己的显示名")
        self.assertEqual(len(v["leaders"]), len(P.real_leader_sessions()))

    def test_两个真Leader并行两行(self):
        self._wait(1, session="s1", name="leader-x")
        self._wait(1, session="s2", name="leader-y")
        labels = sorted(r["label"] for r in P.leaders_view()["leaders"])
        self.assertEqual(labels, ["leader-x", "leader-y"], "两个真 Leader 两行、各带显示名")

    def test_同一会话不重复计数(self):
        self._wait(1, session="s1", name="leader-x")
        self._wait(1, session="s1", name="leader-x")   # 同一会话再次心跳
        self.assertEqual(len(P.leaders_view()["leaders"]), 1, "同一 Leader 不得被算两次")

    def test_报显示名即转正(self):
        out = self._wait(1)                       # 过路：分配 sid，temp=True
        sid = out.split("SESSION=")[1].split("（")[0].split("\n")[0].strip()
        self.assertEqual(P.leaders_view()["leaders"], [])
        self._wait(1, session=sid, name="leader-z")   # 同一会话回头报了名
        self.assertFalse(self._sessions()[sid].get("temp"), "报了显示名必须清除临时标记")
        self.assertEqual([r["label"] for r in P.leaders_view()["leaders"]], ["leader-z"])

    def test_临时会话短TTL清理(self):
        B.heartbeat(P.CONTROLLER, session="tmp1", temp=True)      # 过路会话
        B.heartbeat(P.CONTROLLER, session="real1", label="boss")  # 真会话
        # 把两者 last_seen 都拨回 200 秒前：超过临时 TTL(120) 但未超过全量 TTL(600)
        self._expire_session("tmp1", 200)
        self._expire_session("real1", 200)
        B.heartbeat(P.CONTROLLER, session="real1", label="boss")  # 任意一次心跳触发清理
        sessions = self._sessions()
        self.assertNotIn("tmp1", sessions, "临时会话 120 秒后必须被清掉，不无限堆积")
        self.assertIn("real1", sessions, "真会话 600 秒 TTL 内不受影响")


class OrphanSafety(LeaderSessionsBase):
    def test_临时会话领的指令不误判孤儿_负对照(self):
        # 不变量 5 的负对照：如果把 live 过滤成「只有真 Leader」，这里的指令会被误判孤儿退回
        cid = P.push_command("dispatch", {"question": "q", "peers": ["p1"]})["id"]
        out = self._wait(3)     # 无名 wait：temp 会话，但真领走了指令
        self.assertIn(cid, out, "过路 wait 确实会领指令（这是要修的原始病灶的输入面）")
        sid = out.split("SESSION=")[1].split("（")[0].split("\n")[0].strip()
        self._backdate_claim(cid, 700)              # 领取已超过 600 秒
        self._expire_session(sid, 10)               # 会话心跳拨旧但远未过期
        B.heartbeat(P.CONTROLLER, session=sid, temp=True)   # 会话还活着（心跳新鲜）
        live = P.live_leader_sessions()
        self.assertIn(sid, live, "孤儿判定用的 live 必须包含临时会话（不能谁都不算活着）")
        self.assertEqual(P.release_orphan_commands(), [], "领取者活着时，指令绝不退回队列")
        self.assertTrue(os.path.exists(os.path.join(P.COMMANDS, cid + ".taken")))

    def test_会话断开后指令正常回收(self):
        cid = P.push_command("dispatch", {"question": "q", "peers": ["p1"]})["id"]
        out = self._wait(3)
        sid = out.split("SESSION=")[1].split("（")[0].split("\n")[0].strip()
        self._backdate_claim(cid, 700)
        self._expire_session(sid, 700)              # 会话真的死了（超过全量 TTL）
        requeued = P.release_orphan_commands()
        self.assertIn(cid, requeued, "领取会话消失后指令必须回收")
        self.assertFalse(os.path.exists(os.path.join(P.COMMANDS, cid + ".taken")))

    def test_领了指令的临时会话转正进名单(self):
        cid = P.push_command("dispatch", {"question": "q", "peers": ["p1"]})["id"]
        out = self._wait(3)
        sid = out.split("SESSION=")[1].split("（")[0].split("\n")[0].strip()
        rows = P.leaders_view()["leaders"]
        self.assertEqual([r["session"] for r in rows], [sid], "领了指令的临时会话在干 Leader 的活，必须转正可见")


if __name__ == "__main__":
    unittest.main()
