"""作者不审自己的提交——顶替 / 点名 / 改派 / 重派四条路径（d5de 事故：Reviewer 离线 → 同组的作者被顶上 → 读完包才被拒收）。
临时数据目录。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_build as BB        # noqa: E402
import bus_core as B          # noqa: E402

AUTHOR, REVIEWER, OTHER = "qwen3", "qoder1", "qoder9"
TARGET = {"task_id": "b1", "teammate": AUTHOR, "commit": "c" * 40, "base": "b" * 40, "main_head": "m" * 40}


class ReviewAuthor(Base):
    def setUp(self):
        super().setUp()
        self.g = mock.patch.object(B, "peer_group", lambda p: "flash"); self.g.start()

    def tearDown(self):
        self.g.stop()
        super().tearDown()

    def online(self, *peers):
        for p in peers:
            B.heartbeat(p, "idle", "m", "", B.now_iso(), 60)

    def meta(self, tid):
        return B.read_json(os.path.join(B.OUTBOX, f"{tid}.meta.json"), {})

    def test_substitute_skips_excluded(self):
        self.online(AUTHOR)
        self.assertEqual(B.substitute_offline([REVIEWER]), {REVIEWER: AUTHOR}, "不传 exclude 行为不变（其它调用点）")
        self.assertEqual(B.substitute_offline([REVIEWER], exclude={AUTHOR}), {})
        self.online(OTHER)
        self.assertEqual(B.substitute_offline([REVIEWER], exclude={AUTHOR}), {REVIEWER: OTHER})

    def test_review_dispatch_never_substitutes_author(self):
        self.online(AUTHOR)                      # 点名的 Reviewer 离线，同组只有作者在线
        r = B.dispatch("controller", [REVIEWER], "review", "审这个", [], 10, None, 1, review_of=TARGET)
        self.assertEqual(r["peers"], [REVIEWER], "宁可派给离线的人等它回来，也不顶成作者")
        self.assertNotIn(AUTHOR, self.meta(r["task_id"])["peers"])

    def test_naming_author_as_reviewer_is_rejected(self):
        self.online(AUTHOR)
        with self.assertRaises(ValueError):
            B.dispatch("controller", [AUTHOR], "review", "审这个", [], 10, None, 1, review_of=TARGET)
        with mock.patch.object(BB, "review_target", lambda *a: TARGET), mock.patch.object(BB, "review_pack", lambda *a, **k: "pack"):
            with self.assertRaises(ValueError) as cm:
                BB.dispatch_review("b1", AUTHOR, reviewers=[AUTHOR])
            self.assertIn("作者", str(cm.exception))
            self.online(OTHER)
            r = BB.dispatch_review("b1", AUTHOR, reviewers=[AUTHOR, OTHER])
        self.assertEqual(r["reviewers"], [OTHER], "点名里混了作者：去掉作者，其余照派")

    def test_no_review_for_commit_already_in_main(self):
        same = {**TARGET, "base": TARGET["commit"]}   # 被审提交就是分叉点：内容已在 main，包里没有差异
        with mock.patch.object(BB, "review_target", lambda *a: same):
            with self.assertRaises(ValueError) as cm:
                BB.dispatch_review("b1", AUTHOR, reviewers=[OTHER])
        self.assertIn("没有差异可审", str(cm.exception))

    def test_reassign_skips_author(self):
        self.online(OTHER)
        r = B.dispatch("controller", [OTHER], "review", "审这个", [], 10, None, 1, review_of=TARGET)
        self.online(AUTHOR)
        with self.assertRaises(ValueError):
            B.reassign(r["task_id"], OTHER, to_peer=AUTHOR)
        out = B.reassign(r["task_id"], OTHER)
        self.assertNotEqual(out["to"], AUTHOR)

    def test_redispatch_drops_author(self):
        self.online(OTHER)
        r = B.dispatch("controller", [OTHER], "review", "审这个", [], 10, None, 1, review_of=TARGET)
        self.online(AUTHOR)
        out = B.redispatch(r["task_id"], peers=[OTHER, AUTHOR])
        self.assertNotIn(AUTHOR, out["peers"], "重派后变成普通任务，作者在挑人这一步就得排除")


if __name__ == "__main__":
    unittest.main()
