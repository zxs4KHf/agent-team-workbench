"""落地推进器（bus_land，2026-10-02）。临时 git 仓库 + 临时数据目录，不碰真实仓库与运行数据。

用户：「已采纳待落地卡了非常多的待审，根本推不动。不需要我决策的地方他们自己推，需要我决策的地方卡在那儿让我决策。」
实测 143 条「待审」里没有一条真在等审查：退回 / 空交付 / 已关 / 审查通过没合并。"""
import datetime as dt
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_build as BB        # noqa: E402
import bus_core as B          # noqa: E402
import bus_land as LD         # noqa: E402
import bus_projects as P      # noqa: E402
import bus_reassign as RA     # noqa: E402


def ago(hours):
    return (dt.datetime.now().astimezone() - dt.timedelta(hours=hours)).replace(microsecond=0).isoformat()


class LandDriver(Base):
    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.g("init", "-b", "main"); self.g("config", "user.email", "t@example.com"); self.g("config", "user.name", "t")
        self.g("config", "commit.gpgsign", "false")
        open(os.path.join(self.repo, ".gitignore"), "w").write(".wt/\n")
        open(os.path.join(self.repo, "a.txt"), "w").write("base\n")
        self.g("add", "."); self.g("commit", "-m", "base")
        self.builds = os.path.join(self.tmp, "builds")
        self.p1 = mock.patch.object(BB, "BUILD_DIR", self.builds); self.p1.start()
        self.p2 = mock.patch.object(B, "peer_group", lambda p: "opus"); self.p2.start()
        self.p3 = mock.patch.object(RA, "builder_groups", lambda: ["opus"]); self.p3.start()
        os.makedirs(os.path.join(P.PROJECTS, "p1"), exist_ok=True)
        B.write_json(os.path.join(P.PROJECTS, "p1", "project.json"), {"slug": "p1", "name": "p1", "repo_dir": self.repo, "peers": [], "mode": "independent", "context_files": []})
        B.write_json(os.path.join(P.PROJECTS, "p1", "decisions.json"), [])

    def tearDown(self):
        self.p3.stop(); self.p2.stop(); self.p1.stop()
        super().tearDown()

    def g(self, *a, cwd=None):
        return subprocess.run(["git", *a], cwd=cwd or self.repo, capture_output=True, text=True, check=True).stdout.strip()

    def built(self, bid, peer="w1", content="new\n", fname="b.txt", review="", review_age_h=1):
        """开一张施工单、交一次提交，按需写审查结论；决策 bid 已采纳并关联它。"""
        BB.open_worktrees(bid, [peer], self.repo)
        wt = BB.record(bid)["teammates"][peer]["path"]
        if content is not None:
            open(os.path.join(wt, fname), "w").write(content)
        BB.commit_work(bid, peer, f"work {bid}")
        rec = BB.record(bid)
        if review:
            tm = rec["teammates"][peer]
            tm.update(review=review, review_at=ago(review_age_h), reviewed_commit=tm["commit"], review_note=f"{review} 原因")
            BB.save_record(bid, rec)
        items = P.decisions("p1") + [{"task_id": bid, "status": "adopted", "title": f"决策 {bid}", "execution": "pending", "created": B.now_iso(), "updated": B.now_iso()}]
        B.write_json(os.path.join(P.PROJECTS, "p1", "decisions.json"), items)

    def cats(self):
        return {it["task_id"]: it["cat"] for it in LD.scan(["p1"])}

    def test_classify_each_case(self):
        self.built("D1", review="PASS", review_age_h=1)
        self.built("D2", review="PASS", review_age_h=72, fname="c.txt")
        self.built("D3", review="RETURN", fname="d.txt")
        self.built("D4", content=None)                       # 空交付：提交里没有改动
        self.built("D5", fname="e.txt")                      # 有提交、门禁过、没审 → 真待审
        self.assertEqual(self.cats(), {"D1": "merge_ready", "D2": "stale", "D3": "returned", "D4": "empty", "D5": "reviewing"})
        s = LD.summary(LD.scan(["p1"]))
        self.assertEqual((s["merge_ready"]["n"], s["reviewing"]["n"]), (1, 1))

    def test_auto_merge_fresh_pass_into_main(self):
        self.built("D1", review="PASS", review_age_h=1)
        with mock.patch.object(P, "list_projects", lambda: [{"slug": "p1"}]):
            done = LD.auto_merge()
        self.assertEqual([(d["task_id"], d["result"]["ok"]) for d in done], [("D1", True)])
        self.assertTrue(os.path.exists(os.path.join(self.repo, "b.txt")), "合进了临时仓库的 main")
        with mock.patch.object(P, "list_projects", lambda: [{"slug": "p1"}]):
            self.assertEqual(LD.scan(), [], "合并后不再出现在待落地里")

    def test_conflicting_merge_leaves_main_untouched_and_marks_blocked(self):
        self.built("D1", review="PASS", review_age_h=1, fname="a.txt", content="branch\n")
        open(os.path.join(self.repo, "a.txt"), "w").write("main moved\n")
        self.g("commit", "-am", "main moved")
        head = self.g("rev-parse", "HEAD")
        with mock.patch.object(P, "list_projects", lambda: [{"slug": "p1"}]):
            done = LD.auto_merge()
            self.assertFalse(done[0]["result"]["ok"])
            self.assertEqual(self.g("rev-parse", "HEAD"), head, "合并失败 main 不动")
            self.assertEqual(self.cats(), {"D1": "blocked"}, "下一圈归到「合并受阻」，不再反复撞")

    def test_stale_review_is_not_auto_merged(self):
        self.built("D2", review="PASS", review_age_h=72)
        with mock.patch.object(P, "list_projects", lambda: [{"slug": "p1"}]):
            self.assertEqual(LD.auto_merge(), [])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "b.txt")))

    def test_drop_moves_decision_to_done(self):
        self.built("D3", review="RETURN")
        with mock.patch.object(P, "list_projects", lambda: [{"slug": "p1"}]):
            r = LD.bulk("returned", "drop")
        self.assertEqual(len(r["done"]), 1)
        rec = next(d for d in P.decisions("p1") if d["task_id"] == "D3")
        self.assertEqual(rec["execution"], "n-a")
        self.assertEqual(self.cats(), {})

    def test_rework_only_to_listening_builder_and_relinks(self):
        self.built("D3", review="RETURN")
        with mock.patch.object(P, "list_projects", lambda: [{"slug": "p1"}]):
            r = LD.bulk("returned", "rework")
            self.assertEqual(r["done"], [])
            self.assertIn("没有正在等任务的施工 Agent", r["failed"][0]["error"])
            B.heartbeat("w2", "idle", "m", "", B.now_iso(), 60)       # 正在等任务
            with mock.patch.object(B, "WORKSPACE", self.repo):
                r = LD.bulk("returned", "rework")
        self.assertEqual(len(r["done"]), 1)
        new_bid = r["done"][0]["build_id"]
        rec = next(d for d in P.decisions("p1") if d["task_id"] == "D3")
        self.assertEqual(rec["implementation_task_id"], new_bid)
        task = B.read_json(os.path.join(B.INBOX, "w2", f"{new_bid}.json"), {})
        self.assertIn("审查退回", task.get("question", ""))
        self.assertIn("RETURN 原因", task.get("question", ""))
        self.assertEqual(BB.record("D3").get("status"), "closed", "旧施工单关闭")

    def test_auto_release_only_when_content_already_in_main(self):
        self.built("D3", review="RETURN", fname="same.txt", content="landed\n")       # 内容后来被别的提交带进 main
        self.built("D4", review="RETURN", fname="new.txt", content="still new\n")     # 真有新内容
        self.built("D5", review="RETURN", fname="a.txt", content="branch\n")          # 与 main 冲突
        open(os.path.join(self.repo, "same.txt"), "w").write("landed\n")
        open(os.path.join(self.repo, "a.txt"), "w").write("main moved\n")
        self.g("add", "."); self.g("commit", "-m", "other branch landed same content")
        head = self.g("rev-parse", "HEAD")
        with mock.patch.object(P, "list_projects", lambda: [{"slug": "p1"}]):
            done = LD.auto_release()
            self.assertEqual([d["task_id"] for d in done], ["D3"])
            self.assertEqual(self.cats(), {"D4": "returned", "D5": "returned"}, "新内容和冲突的留给人判断")
        rec = next(d for d in P.decisions("p1") if d["task_id"] == "D3")
        self.assertEqual((rec["execution"], rec["execution_by"]), ("released", "land"))
        self.assertIn("不产生任何变化", rec["execution_note"])
        self.assertEqual(self.g("rev-parse", "HEAD"), head, "只改决策记录，不碰 main")

    def test_in_main_handles_bad_input(self):
        self.assertFalse(LD.in_main(self.repo, "0" * 40), "对象不存在：交给人判断")
        self.assertFalse(LD.in_main("", "abc"))

    def adopt(self, tid, **kw):
        items = P.decisions("p1") + [{"task_id": tid, "status": "adopted", "title": f"决策 {tid}", "execution": "pending", "created": B.now_iso(), "updated": B.now_iso(), **kw}]
        B.write_json(os.path.join(P.PROJECTS, "p1", "decisions.json"), items)

    def test_every_unfinished_adopted_decision_has_a_category(self):
        # 2026-10-02 接手积压：这几类原来在待落地页里看不见
        self.adopt("U1")                                                 # 采纳后没开施工单
        BB.open_worktrees("S1", ["w1"], self.repo); self.adopt("S1")     # 开了单没交提交
        rec = BB.record("S1"); rec["created"] = ago(30); BB.save_record("S1", rec)
        BB.open_worktrees("N1", ["w2"], self.repo); self.adopt("N1")     # 刚开单：施工中，不打扰
        BB.open_worktrees("A1", ["w3"], self.repo); self.adopt("A1")
        rec = BB.record("A1"); rec["status"] = "closed"; BB.save_record("A1", rec)   # 关了也从没交过
        self.built("M1", review="PASS", fname="m.txt")
        rec = BB.record("M1"); rec["teammates"]["w1"]["merged"] = True; BB.save_record("M1", rec)   # 早已合入，决策没回写
        self.assertEqual(self.cats(), {"U1": "unbuilt", "S1": "stalled", "N1": "building", "A1": "abandoned", "M1": "merged"})

    def test_merged_and_auto_merged_decisions_are_written_back(self):
        self.built("M1", review="PASS", fname="m.txt")
        rec = BB.record("M1"); rec["teammates"]["w1"]["merged"] = True; BB.save_record("M1", rec)
        self.built("D1", review="PASS", review_age_h=1, fname="d.txt")
        with mock.patch.object(P, "list_projects", lambda: [{"slug": "p1"}]):
            LD.auto_release()
            LD.auto_merge()
        ex = {d["task_id"]: d.get("execution") for d in P.decisions("p1")}
        self.assertEqual((ex["M1"], ex["D1"]), ("released", "released"), "合入了就回写已落地，不再永远挂着「待实施」")

    def test_unbuilt_direct_needs_listening_builder(self):
        self.adopt("U1", merge_path="log/U1.merge.md")
        with mock.patch.object(P, "list_projects", lambda: [{"slug": "p1"}]):
            r = LD.bulk("unbuilt", "direct")
            self.assertIn("没有正在等任务", r["failed"][0]["error"])
            with self.assertRaises(ValueError):
                LD.bulk("unbuilt", "rework")
            with self.assertRaises(ValueError):
                LD.bulk("returned", "direct")

    def test_bulk_rejects_unknown(self):
        with self.assertRaises(ValueError):
            LD.bulk("merge_ready", "drop")
        with self.assertRaises(ValueError):
            LD.bulk("returned", "rereview")


if __name__ == "__main__":
    unittest.main()
