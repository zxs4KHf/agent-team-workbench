"""「假在线 / 领了不做」治理（2026-10-02）。跑法：py -3 tests/test_real_online_20261002.py

实况：tmp/glm3_keepalive.py 直接覆盖写 peers/glm3.json 续命（不消费任务）；tmp/q1_heartbeat.py 用脚本代调
bus_wait_task 把任务包存成文件就退出——任务被领走却没人做；ws_* 调用不心跳，真干活的人 3 分钟后反而显示离线；
27 份已关任务的副本留在 13 个 Agent 的收件箱里，让空闲的人看上去压着活。
"""
import asyncio
import datetime as dt
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_core as B        # noqa: E402
import bus_reassign as RA   # noqa: E402


def ago(sec):
    return (dt.datetime.now().astimezone() - dt.timedelta(seconds=sec)).replace(microsecond=0).isoformat()


class RealOnline(Base):
    def setUp(self):
        super().setUp()
        self.g = mock.patch.object(B, "peer_group", lambda p: "g")
        self.g.start()

    def tearDown(self):
        self.g.stop()
        super().tearDown()

    def task(self, tid, peer, claimed_ago, mode="independent"):
        B.write_json(os.path.join(B.OUTBOX, f"{tid}.meta.json"), {"id": tid, "mode": mode, "peers": [peer], "question": "q", "from": "controller"})
        os.makedirs(os.path.join(B.INBOX, peer), exist_ok=True)
        dl, ts = B.deadline_after(60)
        B.write_json(os.path.join(B.INBOX, peer, f"{tid}.taken"), {"id": tid, "mode": mode, "question": "q", "from": "controller",
                                                                   "to": peer, "deadline": dl, "deadline_ts": ts, "taken_at": ago(claimed_ago)})

    # ---- 在线判定只认总线心跳 ----
    def test_script_kept_alive_agent_is_not_listening_and_its_queue_moves(self):
        # glm3_keepalive.py 那种：脚本直接写 peers 文件，看上去在线，但从不 wait；排给它的单 30 分钟后收走
        B.write_json(os.path.join(B.PEERS, "glm3.json"), {"peer": "glm3", "status": "idle", "model": "m", "last_seen": B.now_iso(), "task_id": ""})
        st = B.peer_state("glm3")
        self.assertEqual((st["online"], st["listening"], st["beat_source"]), (True, False, "file"))
        B.write_json(os.path.join(B.OUTBOX, "Q1.meta.json"), {"id": "Q1", "mode": "independent", "peers": ["glm3"], "question": "q", "from": "controller"})
        os.makedirs(os.path.join(B.INBOX, "glm3"), exist_ok=True)
        dl, ts = B.deadline_after(60)
        B.write_json(os.path.join(B.INBOX, "glm3", "Q1.json"), {"id": "Q1", "mode": "independent", "question": "q", "from": "controller",
                                                                "to": "glm3", "created": ago(3600), "deadline": dl, "deadline_ts": ts})
        old = os.path.join(B.INBOX, "glm3", "Q1.json")
        os.utime(old, (os.path.getmtime(old) - 3600,) * 2)          # 进它收件箱已经一小时
        B.heartbeat("real1", "idle", "m", "", B.now_iso(), 60)
        B.heartbeat("fake2", "idle", "m")                             # 在线但不在 wait：不能当接收人
        r = RA.free_stalled()
        self.assertEqual([(m["task_id"], m["to"]) for m in r["moved"]], [("Q1", "real1")])
        self.assertEqual(B.strikes("glm3"), 0, "没领的单不记「领了不做」")
        # 2026-10-02 实测回归：刚改派过去的单，下一圈不能因为「任务创建很久了」又被收走（来回弹）
        B.heartbeat("real1", "idle", "m")                             # 哪怕接收人这会儿不在 wait
        self.assertEqual(RA.free_stalled()["moved"], [], "刚到收件箱的单不算超时")

    def test_non_listening_online_agent_never_receives(self):
        self.task("T1", "fake1", 3600)
        B.heartbeat("fake2", "idle", "m")                             # 在线、空闲、但不在 wait
        r = RA.free_stalled()
        self.assertEqual(r["moved"], [])
        self.assertTrue(os.path.exists(os.path.join(B.INBOX, "fake1", "T1.json.expired")), "没有真在等的人：释放，不硬塞给假在线的")

    def test_freed_tasks_spread_across_listeners(self):
        for i in range(4):
            self.task(f"T{i}", "fake1", 3600)
        for n in ("r1", "r2"):
            B.heartbeat(n, "idle", "m", "", B.now_iso(), 60)
        got = sorted(m["to"] for m in RA.free_stalled()["moved"])
        self.assertEqual(got, ["r1", "r1", "r2", "r2"])

    def test_script_refreshing_file_after_bus_beat_does_not_keep_online(self):
        B.heartbeat("glm3", "idle", "m")
        B.write_json(B._beat_path("glm3"), {"at": ago(600), "activity": "idle", "task_id": ""})   # 总线上次心跳在 10 分钟前
        rec = B.read_json(os.path.join(B.PEERS, "glm3.json"), {})
        rec["last_seen"] = B.now_iso()                                                               # 脚本把文件刷新成「刚刚」
        B.write_json(os.path.join(B.PEERS, "glm3.json"), rec)
        self.assertFalse(B.peer_state("glm3")["online"])

    def test_bus_heartbeat_is_online_and_listening_only_while_waiting(self):
        B.heartbeat("w1", "idle", "m", "", B.now_iso(), 60)
        st = B.peer_state("w1")
        self.assertTrue(st["online"] and st["listening"])
        B.heartbeat("w2", "idle", "m")                       # 在线但不在 wait 里
        self.assertFalse(B.peer_state("w2")["listening"])

    # ---- 干活的证据 ----
    def test_ws_call_marks_working_on_claimed_task(self):
        self.task("T1", "w1", 3600)
        B.touch_work("w1")
        st = B.peer_state("w1")
        self.assertEqual((st["activity"], st["task_id"]), ("think", "T1"))
        self.assertEqual(RA.stalled_claims(), [], "一直在调 ws_* 的不算领了不做")

    # ---- 领了不做：收回改派 + 记次 ----
    def test_stalled_claim_moves_to_listening_agent_and_strikes(self):
        self.task("T1", "fake1", 3600)
        B.heartbeat("busy1", "think", "m", task_id="X")
        B.heartbeat("idle1", "idle", "m", "", B.now_iso(), 60)       # 正在 wait：优先派给它
        r = RA.free_stalled()
        self.assertEqual([(m["task_id"], m["to"]) for m in r["moved"]], [("T1", "idle1")])
        self.assertEqual(B.strikes("fake1"), 1)
        self.assertTrue(os.path.exists(os.path.join(B.INBOX, "idle1", "T1.json")))

    def test_young_claim_is_left_alone(self):
        self.task("T1", "w1", 600)
        self.assertEqual(RA.stalled_claims(), [])

    def test_never_returned_to_same_holder(self):
        self.task("T1", "fake1", 3600)
        B.heartbeat("fake1", "idle", "m", "", B.now_iso(), 60)     # 它自己在线且在 wait，也不还给它
        r = RA.free_stalled()
        self.assertEqual(r["moved"], [])
        self.assertTrue(os.path.exists(os.path.join(B.INBOX, "fake1", "T1.json.expired")), "没人可接就释放成过期")

    def test_two_strikes_bench_until_reply(self):
        B.add_strike("fake1", "a"); B.add_strike("fake1", "b")
        B.heartbeat("fake1", "idle", "m", "", B.now_iso(), 60)
        self.assertTrue(B.benched("fake1"))
        self.assertEqual(B.pick_substitute("other", exclude=[]), "", "被罚下的不派")
        self.task("T9", "fake1", 10)
        B.reply("T9", "fake1", "答完了")
        self.assertFalse(B.benched("fake1"))

    # ---- 已关任务的残留副本 ----
    def test_closed_task_copies_are_moved_aside(self):
        os.makedirs(os.path.join(B.INBOX, "w1"), exist_ok=True)
        B.write_json(os.path.join(B.INBOX, "w1", "OLD.taken"), {"id": "OLD"})
        self.task("LIVE", "w1", 60)
        self.assertEqual(RA.clean_closed_copies(), 1)
        self.assertTrue(os.path.exists(os.path.join(B.INBOX, "w1", "_closed", "OLD.taken")))
        self.assertEqual(B.inbox_counts("w1")["claimed"], 1, "只剩还开着的那单")


class TolerantMcpLink(unittest.TestCase):
    TOKEN = "T" * 40

    def call(self, path):
        import bus_mcp as M
        seen = {}

        async def app(scope, receive, send):
            seen["path"], seen["who"] = scope["path"], M.CURRENT_PEER.get()

        async def send(msg):
            seen.setdefault("status", msg.get("status"))
        auth = M.BearerAuth(app, self.TOKEN)
        with mock.patch.object(M, "_live", lambda k, d=None: {"peer_tokens": {"glm1": "G" * 40}, "auth_mode": "link"}.get(k, d)):
            asyncio.run(auth({"type": "http", "path": path, "headers": []}, None, send))
        return seen

    def test_exact_link(self):
        self.assertEqual(self.call(f"/mcp/{self.TOKEN}")["path"], "/mcp")

    def test_trailing_chinese_punctuation_and_words(self):
        r = self.call(f"/mcp/{'G' * 40}，然后连接")
        self.assertEqual((r["path"], r["who"]), ("/mcp", "glm1"))

    def test_trailing_letters_glued_on(self):
        self.assertEqual(self.call(f"/mcp/{self.TOKEN}and")["path"], "/mcp")

    def test_unknown_token_still_rejected(self):
        self.assertEqual(self.call("/mcp/" + "X" * 40 + "，")["status"], 401)


class PromptLink(unittest.TestCase):
    def test_link_is_alone_on_its_line(self):
        import bus_projects as P
        url = "https://mcp.example/mcp/" + "T" * 40
        lines = P.prompt_text("peer", "glm1", "m", link=url).splitlines()
        self.assertIn(url, lines, "地址单独一行，前后不黏字")
        self.assertIn("工作纪律", P.prompt_text("peer", "glm1", "m", link=url))


if __name__ == "__main__":
    unittest.main()
