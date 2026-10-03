"""scripts/gc_builds.py：临时 git 仓库里造四类施工位（已合入干净 / 已合入有改动 / 未合入 / 合并冲突中），
验证预演不改、执行后可恢复，以及"待合并 / 正在做 / 近期有活动"三种情况会被跳过。"""
import datetime as dt, importlib.util, json, os, shutil, subprocess, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("gc_builds", os.path.join(ROOT, "scripts", "gc_builds.py"))
G = importlib.util.module_from_spec(spec); spec.loader.exec_module(G)


def git(cwd, *a, check=True):
    return subprocess.run(["git", *a], cwd=cwd, check=check, capture_output=True, text=True)


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class GcBuilds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wbgc-")
        self.repo = os.path.join(self.tmp, "repo"); os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main"); git(self.repo, "config", "user.email", "t@t"); git(self.repo, "config", "user.name", "t")
        write(os.path.join(self.repo, "a.txt"), "a\n"); git(self.repo, "add", "."); git(self.repo, "commit", "-qm", "base")
        git(self.repo, "branch", "side"); git(self.repo, "switch", "-q", "side")
        write(os.path.join(self.repo, "a.txt"), "side\n"); git(self.repo, "commit", "-qam", "side"); git(self.repo, "switch", "-q", "main")
        self.builds = os.path.join(self.tmp, "builds"); os.makedirs(self.builds)
        self.archive = os.path.join(self.tmp, "archive")
        self.state = os.path.join(self.tmp, "state")
        tms = {}
        for name in ("m", "d", "u", "c"):
            br, wt = f"task/T1/{name}", os.path.join(self.tmp, "wt", name)
            git(self.repo, "worktree", "add", "-q", wt, "-b", br, "main")
            if name == "u":
                write(os.path.join(wt, "u.txt"), "keep me\n"); git(wt, "add", "."); git(wt, "commit", "-qm", "work")
            if name == "d":
                write(os.path.join(wt, "a.txt"), "changed\n"); git(wt, "add", "a.txt")
                write(os.path.join(wt, "new.txt"), "untracked\n")
                os.makedirs(os.path.join(wt, "资料")); write(os.path.join(wt, "资料", "笔记.md"), "中文路径\n")
            if name == "c":
                write(os.path.join(wt, "a.txt"), "mine\n"); git(wt, "commit", "-qam", "mine")
                git(wt, "merge", "side", check=False)   # 制造冲突：UU a.txt
            tms[name] = {"path": wt, "branch": br, "merged": False}
        self.rec = os.path.join(self.builds, "T1.json")
        self.tms = tms
        self.save({"task_id": "T1", "repo": self.repo, "created": "2026-09-20T10:00:00", "teammates": tms, "status": "building"})
        self.now = dt.datetime.now() + dt.timedelta(days=30)

    def save(self, rec):
        with open(self.rec, "w", encoding="utf-8") as f:
            json.dump(rec, f)

    def load(self):
        with open(self.rec, encoding="utf-8") as f:
            return json.load(f)

    def tearDown(self):
        subprocess.run(["git", "worktree", "prune"], cwd=self.repo, capture_output=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_gc(self, apply, **kw):
        return G.run(repo=self.repo, builds=self.builds, apply=apply, archive=self.archive, now=kw.pop("now", self.now), state_root=self.state, **kw)

    def test_dry_run_changes_nothing(self):
        rep = self.run_gc(False)
        m = rep["done"][0]["members"]
        self.assertTrue(m["m"]["merged"]); self.assertEqual(m["d"]["dirty"], 3); self.assertFalse(m["u"]["merged"]); self.assertEqual(m["u"]["ahead"], 1)
        self.assertEqual(m["c"]["conflicted"], ["a.txt"])
        self.assertEqual(self.load()["status"], "building")
        self.assertTrue(os.path.isdir(m["u"]["path"]))

    def test_apply_archives_and_closes(self):
        self.run_gc(True)
        rec = self.load()
        self.assertEqual(rec["status"], "closed"); self.assertIn("gc", rec)
        for n in ("m", "d", "u", "c"):
            self.assertFalse(os.path.isdir(os.path.join(self.tmp, "wt", n)))
        self.assertEqual(git(self.repo, "branch", "--list", "task/*").stdout.strip(), "")
        self.assertEqual(git(self.repo, "show", "archive/build/T1/u:u.txt").stdout, "keep me\n")
        self.assertIn("gc T1/u", git(self.repo, "tag", "-n", "archive/build/T1/u").stdout)   # 带注释
        d = os.path.join(self.archive, "T1", "d")
        with open(os.path.join(d, "changes.patch"), encoding="utf-8") as f:
            self.assertIn("changed", f.read())
        self.assertTrue(os.path.isfile(os.path.join(d, "untracked", "new.txt")))
        with open(os.path.join(d, "untracked", "资料", "笔记.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "中文路径\n")   # 中文名的未跟踪文件不能漏存
        with open(os.path.join(d, "base_commit.txt"), encoding="utf-8") as f:
            base = f.read().strip()
        git(self.repo, "worktree", "add", "-q", os.path.join(self.tmp, "restore"), base)
        git(os.path.join(self.tmp, "restore"), "apply", "--check", os.path.join(d, "changes.patch"))   # 基线 + 补丁可恢复
        with open(os.path.join(self.archive, "T1", "c", "conflicted", "a.txt"), encoding="utf-8") as f:
            self.assertIn("<<<<<<<", f.read())   # 冲突中的文件原样保住
        self.assertEqual(self.run_gc(True)["done"], [])   # 已关闭的单不再处理

    def test_recent_activity_skipped(self):
        rep = self.run_gc(False, now=dt.datetime.now())
        self.assertEqual(rep["done"], []); self.assertIn("活动", rep["skipped"][0]["reason"])

    def test_pass_pending_merge_skipped(self):
        rec = self.load(); rec["teammates"]["u"]["review"] = "PASS @abc"; self.save(rec)
        rep = self.run_gc(True)
        self.assertEqual(rep["done"], []); self.assertIn("待合并", rep["skipped"][0]["reason"])
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "wt", "u")))

    def test_taken_in_inbox_skipped(self):
        os.makedirs(os.path.join(self.state, "inbox", "u")); write(os.path.join(self.state, "inbox", "u", "T1.taken"), "{}")
        t = self.now.timestamp() - 3600
        os.utime(os.path.join(self.state, "inbox", "u", "T1.taken"), (t, t))   # 1 小时前领取：算在做
        rep = self.run_gc(False)
        self.assertEqual(rep["done"], []); self.assertIn("正在做", rep["skipped"][0]["reason"])

    def test_stale_taken_not_active(self):
        os.makedirs(os.path.join(self.state, "inbox", "u")); write(os.path.join(self.state, "inbox", "u", "T1.taken"), "{}")
        t = self.now.timestamp() - 5 * 86400
        os.utime(os.path.join(self.state, "inbox", "u", "T1.taken"), (t, t))   # 5 天前领取、无进展：遗留，不算在做
        self.assertEqual(len(self.run_gc(False)["done"]), 1)

    def test_pass_but_recorded_merged_not_pending(self):
        rec = self.load(); rec["teammates"]["u"].update(review="PASS @abc", merged=True); self.save(rec)
        self.assertEqual(len(self.run_gc(False)["done"]), 1)


if __name__ == "__main__":
    unittest.main()
