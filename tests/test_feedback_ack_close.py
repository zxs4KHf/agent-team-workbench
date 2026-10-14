"""体验反馈闭环：记录 → 转任务（生成 Leader 指令）→ Leader 回执只推进到待验证或暂缓。全部在临时目录里跑。"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bus_events as E  # noqa: E402
import bus_feedback as FB  # noqa: E402
import bus_projects as P  # noqa: E402


class FeedbackAckClose(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wbfb-")
        self.saved = {(E, "EVENTS"): E.EVENTS, (FB, "FEEDBACK"): FB.FEEDBACK, (P, "COMMANDS"): P.COMMANDS,
                      (P, "DONE"): P.DONE, (FB, "update"): FB.update}
        E.EVENTS = os.path.join(self.tmp, "events")
        FB.FEEDBACK = os.path.join(self.tmp, "feedback")
        P.COMMANDS = os.path.join(self.tmp, "commands")
        P.DONE = os.path.join(self.tmp, "done")
        os.makedirs(P.COMMANDS)
        os.makedirs(P.DONE)

    def tearDown(self):
        for (mod, name), val in self.saved.items():
            setattr(mod, name, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def converted(self, text="派发后找不到任务"):
        f = FB.create(text, kind="ux", view="概览")
        rec = FB.update(f["id"], "converted")
        self.assertTrue(rec["command_id"])
        return f["id"], rec["command_id"]

    def status(self, fid):
        return next(x for x in FB.items() if x["id"] == fid)

    def test_ok_awaits_verification_with_result(self):
        fid, cid = self.converted()
        cmd = P.pending_commands()[0]
        self.assertEqual(cmd["payload"]["feedback_id"], fid)
        self.assertIn("回执本指令 ok=true", cmd["payload"]["text"])
        self.assertEqual(P.ack_command(cid, "已修复 abc1234", ok=True), f"ACK {cid}")
        rec = self.status(fid)
        self.assertEqual(rec["status"], "awaiting_verification")
        self.assertIn("abc1234", rec["history"][-1]["note"])
        self.assertIn(cid, rec["history"][-1]["note"])
        self.assertIn("feedback.awaiting_verification", [e["type"] for e in E.read()["events"]])

    def test_not_ok_defers(self):
        fid, cid = self.converted()
        P.ack_command(cid, "复现不了，不改", ok=False)
        self.assertEqual(self.status(fid)["status"], "deferred")

    def test_repeat_ack_and_manual_close_untouched(self):
        fid, cid = self.converted()
        FB.update(fid, "done", "手动关了")
        P.ack_command(cid, "x", ok=False)
        rec = self.status(fid)
        self.assertEqual(rec["status"], "done")
        self.assertEqual(len(rec["history"]), 2)
        self.assertIn("already done", P.ack_command(cid, "x", ok=True))
        self.assertEqual(len(self.status(fid)["history"]), 2)

    def test_plain_command_unaffected(self):
        cid = P.push_command("custom", {"text": "普通指令"})["id"]
        self.assertEqual(P.ack_command(cid, "ok"), f"ACK {cid}")
        self.assertEqual(FB.items(), [])

    def test_feedback_failure_never_blocks_ack(self):
        fid, cid = self.converted()

        def boom(*a, **k):
            raise OSError("disk full")
        FB.update = boom
        self.assertEqual(P.ack_command(cid, "done"), f"ACK {cid}")
        self.assertTrue(os.path.exists(os.path.join(P.DONE, f"{cid}.json")))
        ev = [e for e in E.read()["events"] if e["type"] == "feedback.close_failed"]
        self.assertEqual(ev[0]["data"]["id"], fid)
        FB.update = self.saved[(FB, "update")]
        self.assertEqual(self.status(fid)["status"], "converted")
        # 崩溃 / 失败后再回执一次即可补关（重复回执返回 already done，但仍会尝试关闭）
        self.assertIn("already done", P.ack_command(cid, "补回执 def5678"))
        rec = self.status(fid)
        self.assertEqual(rec["status"], "awaiting_verification")
        self.assertIn("done", rec["history"][-1]["note"])  # replay uses persisted original ACK, not new prose

    def test_malformed_done_record_never_blocks_ack(self):
        cid = "20260928-000000-beef"
        with open(os.path.join(P.DONE, cid + ".json"), "w", encoding="utf-8") as f:
            f.write('{"id": "%s", "kind": "custom", "payload": "旧格式字符串"}' % cid)
        self.assertIn("already done", P.ack_command(cid, "x"))
        with open(os.path.join(P.DONE, cid + ".json"), "w", encoding="utf-8") as f:
            f.write('["不是对象"]')
        self.assertIn("already done", P.ack_command(cid, "x"))

    def test_stale_command_cannot_close_reopened_round(self):
        fid, old = self.converted()
        P.ack_command(old, "第一轮结论", ok=True)
        FB.update(fid, "open", "重开")
        new = FB.update(fid, "converted")["command_id"]
        self.assertNotEqual(old, new)
        P.ack_command(old, "旧指令迟到的回执", ok=False)
        rec = self.status(fid)
        self.assertEqual((rec["status"], rec["command_id"]), ("converted", new))
        P.ack_command(new, "第二轮结论 9abc", ok=True)
        rec = self.status(fid)
        self.assertEqual(rec["status"], "awaiting_verification")
        self.assertIn("第二轮结论", rec["history"][-1]["note"])

    def test_update_expect_guard(self):
        f = FB.create("随便一条")
        FB.update(f["id"], "dismissed", "手动")
        before = self.status(f["id"])
        out = FB.update(f["id"], "done", "迟到的回执", expect="converted")
        self.assertEqual(out["status"], "dismissed")
        self.assertEqual(self.status(f["id"]), before)
        self.assertNotIn("feedback.done", [e["type"] for e in E.read()["events"]])


if __name__ == "__main__":
    unittest.main()
