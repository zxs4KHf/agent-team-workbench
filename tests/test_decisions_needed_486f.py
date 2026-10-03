"""fb-486f8ac59c · 待你拍板的问题：decisions_needed 端到端。

四段全连：Leader post -> 落盘 -> 用户提交(answer_decision_needed) -> 指令队列 decision_answer -> Leader ack 后回答记 acked_at。
931d 四条硬要求（回答归系统保管 / 未回执就一直在队列 / 补充说明框永远在 / 两行并存）各有专门用例。
任一段改名必须红。全部在临时目录里跑，不碰真实 console/ peers/ inbox/ outbox/。
fixture 用 Leader 今天真实挂着的两个待决事项（包 21 预缩机、2d14 循环依赖）。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bus_core as B  # noqa: E402
import bus_projects as P  # noqa: E402

PKG21 = {
    "id": "pkg21-preshrink",
    "question": "包 21 预缩机：澄清要松绑死尺寸，怎么改原始设备清单？",
    "options": [
        {"key": "a", "label": "保留原尺寸并在其后追加新句", "detail": "改动最小，评委能对照原文"},
        {"key": "b", "label": "原地改写掉死尺寸", "detail": "文本干净，但与原文差异大"},
    ],
    "allow_free_text": True,
}
DEP2D14 = {
    "id": "2d14-cycle",
    "question": "2d14 循环依赖怎么解？",
    "options": [
        {"key": "jia", "label": "把 keep/2d14-r2 变基到当前 main"},
        {"key": "yi", "label": "在当前 main 上重新实现"},
    ],
    "allow_free_text": True,
}
DECIDED = {
    "id": "orphan-taken",
    "question": "孤儿 .taken 存量 30 条怎么处理？",
    "options": [{"key": "auto", "label": "自动 requeue"}, {"key": "manual", "label": "归档交人工"}],
    "decided": {"choice": "auto", "reason": "30 条全部超过 24 小时且原 peer 已离线，requeue 没有重复执行风险"},
}


class TestDecisionsNeeded(unittest.TestCase):
    def setUp(self):
        self._saved = [(m, n, getattr(m, n)) for m, names in (
            (B, ("ROOT", "PEERS", "INBOX", "OUTBOX", "LOG", "WORKSPACE")),
            (P, ("PROJECTS", "COMMANDS", "DONE", "BUILDS", "ROLES", "NUDGES", "NOTIFIED", "STALE_MARK"))) for n in names]
        self.tmp = tempfile.mkdtemp(prefix="dn486f-")
        B.ROOT = B.WORKSPACE = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        P.PROJECTS = os.path.join(self.tmp, "projects")
        for name in ("COMMANDS", "DONE", "BUILDS"):
            setattr(P, name, os.path.join(self.tmp, "console", name.lower()))
        P.ROLES = os.path.join(self.tmp, "console", "roles.json")
        P.NUDGES = os.path.join(self.tmp, "console", "nudges.json")
        P.NOTIFIED = os.path.join(self.tmp, "console", "notified.json")
        P.STALE_MARK = os.path.join(self.tmp, "console", "stale_notified.json")
        os.makedirs(os.path.join(self.tmp, "console"), exist_ok=True)
        B.ensure_dirs()
        P.ensure()
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        self.p = P.create_project("示例", [], "independent", [], "")
        self.slug = self.p["slug"]

    def tearDown(self):
        for m, n, v in self._saved:
            setattr(m, n, v)
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        shutil.rmtree(self.tmp, ignore_errors=True)

    def item(self, task_id, item_id):
        rec = next(x for x in P.decisions(self.slug) if x["task_id"] == task_id)
        return next(i for i in rec["decisions_needed"] if i["id"] == item_id)

    # ---- 验收 1：四段全连 ----
    def test_end_to_end_post_disk_answer_leader(self):
        # 段一：Leader post（走 P.post 包装，和 bus_tool 同一条路）
        out = P.post(self.slug, "20260929-demo", "merged", "示例澄清改表", decisions_needed=[PKG21, DEP2D14, DECIDED])
        self.assertIn("decision 20260929-demo", out)
        # 段二：落盘
        it = self.item("20260929-demo", "pkg21-preshrink")
        self.assertEqual([o["key"] for o in it["options"]], ["a", "b"])
        self.assertEqual(it["options"][0]["detail"], "改动最小，评委能对照原文")
        self.assertTrue(it["allow_free_text"])
        self.assertNotIn("answer", it)
        self.assertEqual(self.item("20260929-demo", "orphan-taken")["decided"]["choice"], "auto")
        # 段三：用户提交（前端 /api/decision/answer 调的就是这个函数）
        r = P.answer_decision_needed(self.slug, "20260929-demo", "pkg21-preshrink", choice="a", text="标黄，别动数量列")
        self.assertEqual(r["item"]["answer"]["choice"], "a")
        self.assertEqual(self.item("20260929-demo", "pkg21-preshrink")["answer"]["text"], "标黄，别动数量列")
        self.assertEqual(self.item("20260929-demo", "pkg21-preshrink")["answer"]["by"], "user")
        # 段四：进指令队列，Leader 下一轮 wait 能领到
        cmds = self.cmds()
        self.assertEqual(len(cmds), 1)
        pl = cmds[0]["payload"]
        self.assertEqual((pl["project"], pl["task_id"], pl["item_id"], pl["choice"], pl["choice_label"], pl["text"]),
                         (self.slug, "20260929-demo", "pkg21-preshrink", "a", "保留原尺寸并在其后追加新句", "标黄，别动数量列"))
        self.assertEqual(r["command_id"], cmds[0]["id"])
        # 用户改主意再答一次 -> 排队中的旧指令被作废，队列里只剩最新；旧回答进 answers 留档
        r2 = P.answer_decision_needed(self.slug, "20260929-demo", "pkg21-preshrink", choice="b")
        self.assertEqual([(c["payload"]["seq"], c["payload"]["choice"]) for c in self.cmds()], [(2, "b")])
        self.assertEqual(r2["superseded"], [r["command_id"]])
        it = self.item("20260929-demo", "pkg21-preshrink")
        self.assertEqual((it["answer"]["choice"], [x["choice"] for x in it["answers"]]), ("b", ["a"]))

    def cmds(self):
        return [c for c in P.pending_commands(claim=False, limit=99) if c.get("kind") == "decision_answer"]

    # ---- 931d-2：未回执就一直在队列；回执后回答记 acked_at ----
    def test_answer_replays_until_acked(self):
        P.post(self.slug, "20260924-2d14", "merged", "2d14 循环依赖", decisions_needed=[DEP2D14])
        r = P.answer_decision_needed(self.slug, "20260924-2d14", "2d14-cycle", choice="jia")
        for _ in range(3):   # 只读查看多少次都还在
            self.assertEqual([c["id"] for c in self.cmds()], [r["command_id"]])
        self.assertNotIn("acked_at", self.item("20260924-2d14", "2d14-cycle")["answer"])
        P.ack_command(r["command_id"], "已按甲方案变基")
        self.assertEqual(self.cmds(), [])
        ans = self.item("20260924-2d14", "2d14-cycle")["answer"]
        self.assertTrue(ans["acked_at"])
        self.assertEqual(ans["command_id"], r["command_id"])

    def test_ack_of_older_answer_marks_history_entry(self):
        """旧回答已被 Leader 领走、用户又改答：旧指令的回执只标旧回答，新回答仍「等 Leader 确认」。"""
        P.post(self.slug, "t-h", "merged", "改主意", decisions_needed=[DEP2D14])
        r1 = P.answer_decision_needed(self.slug, "t-h", "2d14-cycle", choice="jia")
        self.assertEqual([c["id"] for c in P.pending_commands(claim=True, limit=5)], [r1["command_id"]])
        r2 = P.answer_decision_needed(self.slug, "t-h", "2d14-cycle", choice="yi")
        self.assertEqual(r2["superseded"], [], "已领取的不作废")
        P.ack_command(r1["command_id"], "收到甲")
        it = self.item("t-h", "2d14-cycle")
        self.assertTrue(it["answers"][0]["acked_at"])
        self.assertNotIn("acked_at", it["answer"])   # 新回答还没被确认，界面必须仍显示「等 Leader 确认」

    # ---- sota2 a8d2 RETURN 的复现 ----
    def test_seq_is_monotonic_beyond_history_cap(self):
        P.post(self.slug, "t-seq", "merged", "改很多次", decisions_needed=[DEP2D14])
        for i in range(12):
            P.answer_decision_needed(self.slug, "t-seq", "2d14-cycle", text=f"第 {i + 1} 次")
        it = self.item("t-seq", "2d14-cycle")
        self.assertEqual((it["answer"]["seq"], it["answer_seq"], len(it["answers"])), (12, 12, P.DN_ANSWER_HISTORY))
        seqs = [a["seq"] for a in it["answers"]] + [it["answer"]["seq"]]
        self.assertEqual(seqs, sorted(set(seqs)), "seq 不复用、严格递增")
        self.assertEqual([c["payload"]["seq"] for c in self.cmds()], [12], "队列里只剩最新一条")
        # Leader 重发不重置序号
        P.post(self.slug, "t-seq", "merged", "改很多次", decisions_needed=[DEP2D14])
        P.answer_decision_needed(self.slug, "t-seq", "2d14-cycle", text="第 13 次")
        self.assertEqual(self.item("t-seq", "2d14-cycle")["answer"]["seq"], 13)

    def test_concurrent_answers_latest_wins(self):
        """sota2 33b4：seq=1 停在入队前、seq=2 并发提交——最终队列只剩 seq=2，当前回答也是 seq=2。"""
        import threading, time as _t
        P.post(self.slug, "t-cc", "merged", "并发", decisions_needed=[DEP2D14])
        orig = P.push_command
        first_in = threading.Event()
        def slow_push(kind, payload):
            if kind == "decision_answer" and payload.get("seq") == 1:
                first_in.set(); _t.sleep(0.6)
            return orig(kind, payload)
        P.push_command = slow_push
        try:
            a = threading.Thread(target=P.answer_decision_needed, args=(self.slug, "t-cc", "2d14-cycle"), kwargs={"choice": "jia"})
            a.start(); self.assertTrue(first_in.wait(5))
            b = threading.Thread(target=P.answer_decision_needed, args=(self.slug, "t-cc", "2d14-cycle"), kwargs={"choice": "yi"})
            b.start(); a.join(10); b.join(10)
        finally:
            P.push_command = orig
        self.assertEqual([(c["payload"]["seq"], c["payload"]["choice"]) for c in self.cmds()], [(2, "yi")])
        it = self.item("t-cc", "2d14-cycle")
        self.assertEqual((it["answer"]["seq"], it["answer"]["choice"]), (2, "yi"))

    def _two_queued(self, task):
        """seq=1、seq=2 同时留在队列：模拟 seq=2 入队后、作废前就被领取（作废步骤没机会执行）。"""
        P.post(self.slug, task, "merged", "领取", decisions_needed=[DEP2D14])
        r1 = P.answer_decision_needed(self.slug, task, "2d14-cycle", choice="jia")
        orig = P._dn_supersede_queued
        P._dn_supersede_queued = lambda *a, **k: []
        try:
            P.answer_decision_needed(self.slug, task, "2d14-cycle", choice="yi")
        finally:
            P._dn_supersede_queued = orig
        self.assertEqual(sorted(c["payload"]["seq"] for c in self.cmds()), [1, 2])
        return r1

    def test_claim_drops_answer_older_than_saved(self):
        """sota2 e3fc：一批领取里同时有新旧两版，只交出 seq=2；seq=1 在 done/ 标 superseded。"""
        r1 = self._two_queued("t-cl")
        got = P.pending_commands(claim=True, limit=5)
        self.assertEqual([c["payload"]["seq"] for c in got], [2])
        self.assertEqual(B.read_json(os.path.join(P.DONE, r1["command_id"] + ".json"), {}).get("status"), "superseded")
        self.assertEqual(self.cmds(), [])

    def test_claim_one_by_one_never_yields_old_answer(self):
        """逐条领取（同秒 id 随机后缀，先后不定）：无论哪条先被领，Leader 都只拿到 seq=2。"""
        self._two_queued("t-c1")
        seen = []
        for _ in range(3):
            seen += [c["payload"]["seq"] for c in P.pending_commands(claim=True, limit=1)]
        self.assertEqual(seen, [2])

    def test_claim_keeps_legacy_answer_without_seq(self):
        P.post(self.slug, "t-lg", "merged", "旧指令", decisions_needed=[DEP2D14])
        P.answer_decision_needed(self.slug, "t-lg", "2d14-cycle", choice="jia")
        P.pending_commands(claim=True, limit=5)
        P.push_command("decision_answer", {"project": self.slug, "task_id": "t-lg", "item_id": "2d14-cycle", "choice": "yi"})
        self.assertEqual(len(P.pending_commands(claim=True, limit=5)), 1, "升级前入队、没带 seq 的指令照常投递")

    def test_supersede_never_drops_higher_seq(self):
        """兜底：即使绕过外层锁，作废也按 seq 比较——较旧的调用方作废的是自己。"""
        P.post(self.slug, "t-sq", "merged", "兜底", decisions_needed=[DEP2D14])
        r2 = P.answer_decision_needed(self.slug, "t-sq", "2d14-cycle", choice="yi")       # seq=1，排队中
        fake = P.push_command("decision_answer", {"project": self.slug, "task_id": "t-sq", "item_id": "2d14-cycle", "seq": 0})
        dropped = P._dn_supersede_queued(self.slug, "t-sq", "2d14-cycle", keep=fake["id"], seq=0)
        self.assertEqual(dropped, [fake["id"]])
        self.assertEqual([c["id"] for c in self.cmds()], [r2["command_id"]])

    def test_superseded_command_never_marks_delivered(self):
        P.post(self.slug, "t-sup", "merged", "作废", decisions_needed=[DEP2D14, PKG21])
        r1 = P.answer_decision_needed(self.slug, "t-sup", "2d14-cycle", choice="jia")
        other = P.answer_decision_needed(self.slug, "t-sup", "pkg21-preshrink", choice="a")
        r2 = P.answer_decision_needed(self.slug, "t-sup", "2d14-cycle", choice="yi")
        self.assertEqual(r2["superseded"], [r1["command_id"]], "只作废同一待决项的旧回答")
        self.assertEqual(sorted(c["id"] for c in self.cmds()), sorted([other["command_id"], r2["command_id"]]))
        done = B.read_json(os.path.join(P.DONE, r1["command_id"] + ".json"), {})
        self.assertEqual(done.get("status"), "superseded")
        P.ack_command(r1["command_id"], "迟到的回执")   # 已在 done：幂等返回，不应把旧回答标成已送达
        self.assertNotIn("acked_at", self.item("t-sup", "2d14-cycle")["answers"][0])

    def test_ack_matches_by_answer_id_not_content(self):
        """同一秒、同 choice/text 重答（题面改过）：旧指令回执不得写到新回答上。"""
        P.post(self.slug, "t-id", "merged", "同内容", decisions_needed=[DEP2D14])
        r1 = P.answer_decision_needed(self.slug, "t-id", "2d14-cycle", choice="jia", text="同一句")
        P.pending_commands(claim=True, limit=5)
        P.post(self.slug, "t-id", "merged", "同内容", decisions_needed=[{**DEP2D14, "question": "改过的题面"}])
        P.answer_decision_needed(self.slug, "t-id", "2d14-cycle", choice="jia", text="同一句")
        it = self.item("t-id", "2d14-cycle")
        self.assertNotEqual(it["answer"]["id"], it["answers"][0]["id"])
        P.ack_command(r1["command_id"], "收到")
        it = self.item("t-id", "2d14-cycle")
        self.assertTrue(it["answers"][0]["acked_at"]); self.assertNotIn("acked_at", it["answer"])

    def test_retired_item_reopened_by_same_id_keeps_answer(self):
        P.post(self.slug, "t-ro", "merged", "重开", decisions_needed=[DEP2D14])
        P.answer_decision_needed(self.slug, "t-ro", "2d14-cycle", choice="jia")
        P.post(self.slug, "t-ro", "merged", "重开", decisions_needed=[])
        self.assertTrue(self.item("t-ro", "2d14-cycle")["retired"])
        P.post(self.slug, "t-ro", "merged", "重开", decisions_needed=[DEP2D14])
        it = self.item("t-ro", "2d14-cycle")
        self.assertNotIn("retired", it); self.assertEqual(it["answer"]["choice"], "jia")
        P.answer_decision_needed(self.slug, "t-ro", "2d14-cycle", choice="yi")   # 重开后可以再答
        self.assertEqual(self.item("t-ro", "2d14-cycle")["answer"]["seq"], 2)

    def test_answer_keeps_choice_label_snapshot(self):
        P.post(self.slug, "t-lb", "merged", "改文案", decisions_needed=[DEP2D14])
        P.answer_decision_needed(self.slug, "t-lb", "2d14-cycle", choice="jia")
        relabeled = {**DEP2D14, "options": [{"key": "jia", "label": "完全不同的意思"}, DEP2D14["options"][1]]}
        P.post(self.slug, "t-lb", "merged", "改文案", decisions_needed=[relabeled])
        self.assertEqual(self.item("t-lb", "2d14-cycle")["answer"]["choice_label"], "把 keep/2d14-r2 变基到当前 main")

    # ---- 931d-1：回答归系统保管 ----
    def test_leader_repost_keeps_answer(self):
        P.post(self.slug, "t-re", "merged", "重发", decisions_needed=[DEP2D14, PKG21])
        P.answer_decision_needed(self.slug, "t-re", "2d14-cycle", choice="yi", text="补测试")
        changed = {**DEP2D14, "question": "2d14 循环依赖怎么解？（补充了背景）"}
        P.post(self.slug, "t-re", "merged", "重发", decisions_needed=[changed, PKG21])
        it = self.item("t-re", "2d14-cycle")
        self.assertEqual((it["answer"]["choice"], it["answer"]["text"]), ("yi", "补测试"))
        self.assertEqual(it["question"], changed["question"])
        self.assertEqual(it["answer"]["question"], DEP2D14["question"])   # 答题时的题面快照，界面据此提示「题目改过」

    def test_leader_repost_dropping_answered_item_keeps_it_retired(self):
        P.post(self.slug, "t-drop", "merged", "漏项", decisions_needed=[DEP2D14, PKG21])
        P.answer_decision_needed(self.slug, "t-drop", "2d14-cycle", text="我的意见")
        P.post(self.slug, "t-drop", "merged", "漏项", decisions_needed=[PKG21])
        rec = next(x for x in P.decisions(self.slug) if x["task_id"] == "t-drop")
        self.assertEqual([i["id"] for i in rec["decisions_needed"]], ["pkg21-preshrink", "2d14-cycle"])
        self.assertTrue(self.item("t-drop", "2d14-cycle")["retired"])
        self.assertEqual(self.item("t-drop", "2d14-cycle")["answer"]["text"], "我的意见")
        with self.assertRaises(ValueError):   # 撤下的题只存档，不再接受新回答
            P.answer_decision_needed(self.slug, "t-drop", "2d14-cycle", text="再改")
        # 没回答过的项按新数组正常删除
        P.post(self.slug, "t-drop", "merged", "漏项", decisions_needed=[])
        rec = next(x for x in P.decisions(self.slug) if x["task_id"] == "t-drop")
        self.assertEqual([i["id"] for i in rec["decisions_needed"]], ["2d14-cycle"])

    def test_leader_cannot_forge_answer_or_user_source(self):
        forged = {**DEP2D14, "answer": {"choice": "jia", "text": "用户同意了", "by": "user"},
                  "answers": [{"choice": "yi"}], "retired": True}
        P.post(self.slug, "t-forge", "merged", "伪造", decisions_needed=[forged, {**DECIDED, "decided": {**DECIDED["decided"], "by": "user"}}])
        it = self.item("t-forge", "2d14-cycle")
        for k in ("answer", "answers", "retired"):
            self.assertNotIn(k, it)
        self.assertEqual(self.item("t-forge", "orphan-taken")["decided"]["by"], "leader")
        self.assertEqual(self.cmds(), [])
        # 已有真回答时，Leader 带着伪造 answer 重发也盖不掉
        P.answer_decision_needed(self.slug, "t-forge", "2d14-cycle", choice="yi")
        P.post(self.slug, "t-forge", "merged", "伪造", decisions_needed=[forged])
        self.assertEqual(self.item("t-forge", "2d14-cycle")["answer"]["choice"], "yi")

    def test_user_can_override_leader_decided(self):
        P.post(self.slug, "t-ov", "merged", "推翻", decisions_needed=[DECIDED])
        P.answer_decision_needed(self.slug, "t-ov", "orphan-taken", choice="manual", text="先别自动")
        it = self.item("t-ov", "orphan-taken")
        self.assertEqual((it["decided"]["choice"], it["answer"]["choice"]), ("auto", "manual"))   # 两行并存
        self.assertEqual(self.cmds()[0]["payload"]["decided"]["choice"], "auto")

    def test_old_one_shot_note_removed(self):
        """改名防护（反向）：一次性 USER_DECIDED 提醒已被指令队列取代，不能两条路同时发。"""
        P.post(self.slug, "20260924-2d14", "merged", "2d14 循环依赖", decisions_needed=[DEP2D14])
        P.answer_decision_needed(self.slug, "20260924-2d14", "2d14-cycle", choice="jia")
        self.assertFalse(hasattr(P, "decision_answer_note"))
        self.assertNotIn("USER_DECIDED", P.notify_note(3600, 999999))

    # ---- 验收 3：两种提交方式 ----
    def test_free_text_only(self):
        P.post(self.slug, "t-free", "merged", "只补充不选项", decisions_needed=[DEP2D14])
        P.answer_decision_needed(self.slug, "t-free", "2d14-cycle", text="都不选，先把 main 冻结一天")
        ans = self.item("t-free", "2d14-cycle")["answer"]
        self.assertEqual(ans["choice"], "")
        self.assertEqual(ans["text"], "都不选，先把 main 冻结一天")
        self.assertEqual(self.cmds()[0]["payload"]["choice_label"], "")

    def test_choice_plus_text(self):
        P.post(self.slug, "t-both", "merged", "选项加补充", decisions_needed=[DEP2D14])
        P.answer_decision_needed(self.slug, "t-both", "2d14-cycle", choice="yi", text="重写时顺手补测试")
        ans = self.item("t-both", "2d14-cycle")["answer"]
        self.assertEqual((ans["choice"], ans["text"]), ("yi", "重写时顺手补测试"))

    def test_empty_submission_rejected(self):
        P.post(self.slug, "t-empty", "merged", "空提交", decisions_needed=[DEP2D14])
        with self.assertRaises(ValueError):
            P.answer_decision_needed(self.slug, "t-empty", "2d14-cycle")

    def test_unknown_choice_and_item_rejected(self):
        P.post(self.slug, "t-bad", "merged", "错选项", decisions_needed=[DEP2D14])
        with self.assertRaises(ValueError):
            P.answer_decision_needed(self.slug, "t-bad", "2d14-cycle", choice="bing")
        with self.assertRaises(ValueError):
            P.answer_decision_needed(self.slug, "t-bad", "no-such-item", choice="jia")
        with self.assertRaises(ValueError):
            P.answer_decision_needed(self.slug, "no-such-task", "2d14-cycle", choice="jia")

    def test_free_text_always_allowed(self):
        """931d-3：补充说明框永远在——旧调用传 allow_free_text=False 也不再挡只写字的回答。"""
        item = {**DEP2D14, "allow_free_text": False}
        P.post(self.slug, "t-nofree", "merged", "禁自由文本", decisions_needed=[item])
        self.assertTrue(self.item("t-nofree", "2d14-cycle")["allow_free_text"])
        P.answer_decision_needed(self.slug, "t-nofree", "2d14-cycle", text="我偏要打字")
        self.assertEqual(self.item("t-nofree", "2d14-cycle")["answer"]["text"], "我偏要打字")
        P.post(self.slug, "t-noopt", "merged", "无选项", decisions_needed=[{"id": "x", "question": "q", "options": [], "allow_free_text": False}])
        P.answer_decision_needed(self.slug, "t-noopt", "x", text="直接说")

    # ---- 验收 5：畸形入参一律 ValueError ----
    def test_malformed_rejected(self):
        bad = [
            "不是数组",
            {"id": "x"},
            [{"id": "x"}],                                              # 缺 question
            [{"question": "q", "options": []}],                         # 缺 id
            [{"id": "x", "question": "q", "bogus": 1}],                 # 未知键
            [{"id": "x y", "question": "q"}],                           # id 非法字符
            [{"id": "x", "question": "q", "options": "不是数组"}],
            [{"id": "x", "question": "q", "options": [{"key": "a"}]}],  # 选项缺 label
            [{"id": "x", "question": "q", "options": [{"key": "a", "label": "l", "bogus": 1}]}],
            [{"id": "x", "question": "q", "options": [{"key": "a", "label": "1"}, {"key": "a", "label": "2"}]}],
            [{"id": "x", "question": "q"}, {"id": "x", "question": "q2"}],   # id 重复
            [{"id": "x", "question": "q" * 301}],
            [{"id": "x", "question": "q", "options": [{"key": "k%d" % i, "label": "l"} for i in range(7)]}],
            [{"id": "x", "question": "q"}] * 9,
            [{"id": "x", "question": "q", "decided": {"reason": "r", "choice": "zzz"},
              "options": [{"key": "a", "label": "l"}]}],                # decided.choice 不在 options
            [{"id": "x", "question": "q", "decided": {"choice": "a"}, "options": [{"key": "a", "label": "l"}]}],  # decided 无理由
        ]
        for i, v in enumerate(bad):
            with self.subTest(i=i, value=str(v)[:60]):
                with self.assertRaises(ValueError):
                    P.check_decisions_needed(v)
        # 空列表与 None 是合法的「没有待决项」
        self.assertEqual(P.check_decisions_needed(None), [])
        self.assertEqual(P.check_decisions_needed([]), [])

    def test_post_rejects_malformed(self):
        with self.assertRaises(ValueError):
            P.post(self.slug, "t-x", "merged", "坏的", decisions_needed=[{"id": "a"}])

    # ---- 验收 4：旧决策不受影响 ----
    def test_backward_compatible(self):
        P.post(self.slug, "t-old", "merged", "老决策", "摘要照旧")
        rec = next(x for x in P.decisions(self.slug) if x["task_id"] == "t-old")
        self.assertNotIn("decisions_needed", rec)
        self.assertEqual(rec["summary"], "摘要照旧")
        self.assertEqual(self.cmds(), [])
        with self.assertRaises(ValueError):
            P.answer_decision_needed(self.slug, "t-old", "whatever", choice="a")
        # 再 post 一次别的字段，也不会凭空长出该字段
        P.post(self.slug, "t-old", note="又记一笔")
        self.assertNotIn("decisions_needed", next(x for x in P.decisions(self.slug) if x["task_id"] == "t-old"))


class TestAnswerEndpoint(unittest.TestCase):
    """验收 5 的 HTTP 面：/api/decision/answer 对畸形入参返回 400，不得 500。"""

    def setUp(self):
        import threading
        from http.server import ThreadingHTTPServer
        import bus_web as W
        self.W = W
        self._saved = [(m, n, getattr(m, n)) for m, names in (
            (B, ("ROOT", "PEERS", "INBOX", "OUTBOX", "LOG", "LOCK", "PRIORITY", "WORKSPACE")),
            (P, ("PROJECTS", "COMMANDS", "DONE", "BUILDS", "ROLES", "NUDGES", "NOTIFIED", "STALE_MARK"))) for n in names]
        self.tmp = tempfile.mkdtemp(prefix="dn486f-http-")
        B.ROOT = B.WORKSPACE = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.LOCK = os.path.join(self.tmp, "console", "controller.lock")
        B.PRIORITY = os.path.join(self.tmp, "console", "priority.json")
        P.PROJECTS = os.path.join(self.tmp, "projects")
        P.COMMANDS = os.path.join(self.tmp, "console", "commands")
        P.DONE = os.path.join(self.tmp, "console", "done")
        P.BUILDS = os.path.join(self.tmp, "console", "builds")
        P.ROLES = os.path.join(self.tmp, "console", "roles.json")
        P.NUDGES = os.path.join(self.tmp, "console", "nudges.json")
        P.NOTIFIED = os.path.join(self.tmp, "console", "notified.json")
        P.STALE_MARK = os.path.join(self.tmp, "console", "stale_notified.json")
        for d in (B.PEERS, B.INBOX, B.OUTBOX, B.LOG, P.PROJECTS, P.COMMANDS, P.DONE, P.BUILDS):
            os.makedirs(d, exist_ok=True)
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        self.slug = "p1"
        os.makedirs(os.path.join(P.PROJECTS, self.slug), exist_ok=True)
        B.write_json(os.path.join(P.PROJECTS, self.slug, "project.json"),
                     {"slug": self.slug, "name": "p1", "peers": [], "mode": "independent", "context_files": [], "next": [], "notes": []})
        P.post(self.slug, "t1", "merged", "包 21", decisions_needed=[PKG21])
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), W.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for m, n, v in self._saved:
            setattr(m, n, v)
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, body):
        import json as _json
        import urllib.error
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/decision/answer",
                                     data=_json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, _json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, _json.loads(e.read().decode("utf-8"))

    def test_ok(self):
        code, r = self.post({"project": self.slug, "task_id": "t1", "item_id": "pkg21-preshrink", "choice": "a", "text": "标黄"})
        self.assertEqual((code, r["ok"]), (200, True), r)
        self.assertEqual(r["item"]["answer"]["choice"], "a")
        self.assertEqual(r["command_id"], next(c for c in P.pending_commands(claim=False, limit=99) if c.get("kind") == "decision_answer")["id"])

    def test_free_text_only_http(self):
        code, r = self.post({"project": self.slug, "task_id": "t1", "item_id": "pkg21-preshrink", "text": "两个都不选"})
        self.assertEqual(code, 200, r)
        self.assertEqual(r["item"]["answer"]["choice"], "")

    def test_bad_requests_are_400(self):
        for body in (
            {},                                                                                    # 缺 project/task_id
            {"project": self.slug},                                                                # 缺 task_id
            {"project": self.slug, "task_id": "t1"},                                               # 缺 item_id
            {"project": self.slug, "task_id": "t1", "item_id": "pkg21-preshrink"},                 # 既没选也没写
            {"project": self.slug, "task_id": "t1", "item_id": "nope", "choice": "a"},             # item 不存在
            {"project": self.slug, "task_id": "nope", "item_id": "pkg21-preshrink", "choice": "a"},  # 决策不存在
            {"project": self.slug, "task_id": "t1", "item_id": "pkg21-preshrink", "choice": "zzz"},  # 选项不存在
        ):
            with self.subTest(body=body):
                code, r = self.post(body)
                self.assertEqual(code, 400, (code, r))
                self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
