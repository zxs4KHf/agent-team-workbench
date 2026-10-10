"""红队清单（aca6d5b，8aa1）还没落地的三条：零内容合并、快进失败不留原因、审查写了不存在的成员。临时 git 仓库。"""
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_build as BB             # noqa: E402
import bus_build_safety as BS      # noqa: E402
import bus_core as B               # noqa: E402


class MergeEdges(Base):
    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.tmp, "repo"); os.makedirs(self.repo)
        for a in (["init", "-b", "main"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"], ["config", "commit.gpgsign", "false"]):
            self.g(*a)
        self.write(".gitignore", ".wt/\n"); self.write("a.txt", "base\n")
        self.g("add", "."); self.g("commit", "-m", "base")
        self.p = mock.patch.object(BB, "BUILD_DIR", os.path.join(self.tmp, "builds")); self.p.start()

    def tearDown(self):
        self.p.stop()
        super().tearDown()

    def g(self, *a):
        return subprocess.run(["git", *a], cwd=self.repo, capture_output=True, text=True, check=True).stdout.strip()

    def write(self, name, text, root=None):
        with open(os.path.join(root or self.repo, name), "w", encoding="utf-8") as f:
            f.write(text)

    def built_and_passed(self, bid, fname="b.txt", text="new\n"):
        BB.open_worktrees(bid, ["w1"], self.repo)
        self.write(fname, text, BB.record(bid)["teammates"]["w1"]["path"])
        BB.commit_work(bid, "w1", f"work {bid}")
        tm = BB.record(bid)["teammates"]["w1"]
        BS.set_review(bid, "w1", "PASS", "rev1", "ok", expected_commit=tm["commit"])

    def test_merge_with_no_change_is_not_marked_merged(self):
        self.built_and_passed("T1")
        self.write("b.txt", "new\n"); self.g("add", "."); self.g("commit", "-m", "same content landed elsewhere")
        head = self.g("rev-parse", "HEAD")
        r = BB.merge("T1", "w1")
        self.assertFalse(r["ok"])
        self.assertIn("no change", r["reason"])
        self.assertFalse(BB.record("T1")["teammates"]["w1"].get("merged"), "零内容不写 merged=True")
        self.assertEqual(self.g("rev-parse", "HEAD"), head)

    def test_fast_forward_failure_is_recorded_not_raised(self):
        self.built_and_passed("T2")
        real = BS._git

        def git(repo, *args, check=True):
            if args[:2] == ("merge", "--ff-only"):
                return subprocess.CompletedProcess(args, 1, "", "error: Your local changes would be overwritten")
            return real(repo, *args, check=check)
        head = self.g("rev-parse", "HEAD")
        with mock.patch.object(BS, "_git", git):
            r = BB.merge("T2", "w1")
        self.assertFalse(r["ok"])
        tm = BB.record("T2")["teammates"]["w1"]
        self.assertIn("would be overwritten", tm["merge_error"])
        self.assertFalse(tm.get("merged"))
        self.assertEqual(self.g("rev-parse", "HEAD"), head, "main 不动")

    def test_review_for_unknown_teammate_is_value_error(self):
        BB.open_worktrees("T3", ["w1"], self.repo)
        with self.assertRaises(ValueError) as cm:
            BS.set_review("T3", "ghost", "RETURN", "rev1", "x", expected_commit="abc")
        self.assertIn("没有成员 ghost", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
