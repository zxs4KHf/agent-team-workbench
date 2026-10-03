# 任务 5054 自测（验收 A / B / G）：_full_state 为待落地决策合成 implementation 六态；目录读取常数级；读取路径不写库；历史纠正脚本 dry-run / --apply。
# 跑法：py -3 tests/test_implementation_state_5054.py（仓库根目录）。全部状态建在临时目录，不碰生产 projects/、console/、peers/。
import datetime as dt
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_core as B  # noqa: E402
import bus_projects as P  # noqa: E402


def iso(sec_ago=0):
    return (dt.datetime.now() - dt.timedelta(seconds=sec_ago)).strftime("%Y-%m-%dT%H:%M:%S")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bus5054-")
        B.ROOT = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.LOCK = os.path.join(self.tmp, "console", "controller.lock")
        B.PRIORITY = os.path.join(self.tmp, "console", "priority.json")
        P.PROJECTS = os.path.join(self.tmp, "projects")
        P.COMMANDS = os.path.join(self.tmp, "console", "commands")
        P.DONE = os.path.join(self.tmp, "console", "done")
        P.BUILDS = os.path.join(self.tmp, "console", "builds")
        P.ROLES = os.path.join(self.tmp, "console", "roles.json")
        P.NUDGES = os.path.join(self.tmp, "console", "nudges.json")
        for d in (B.PEERS, B.INBOX, B.OUTBOX, B.LOG, P.PROJECTS, P.COMMANDS, P.DONE, P.BUILDS):
            os.makedirs(d, exist_ok=True)
        self._mg = B.model_groups
        B.model_groups = lambda: [{"id": "claude-fable-5", "match": ["claude-fable-5"], "order": 1}]
        B.write_json(P.ROLES, {"assignments": {"builder_groups": ["claude-fable-5"], "reviewer_groups": []}})
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        self.slug = "p1"
        os.makedirs(os.path.join(P.PROJECTS, self.slug))
        B.write_json(os.path.join(P.PROJECTS, self.slug, "project.json"), {"slug": self.slug, "name": "p1", "peers": [], "mode": "independent", "context_files": [], "next": [], "notes": []})
        B.write_json(os.path.join(P.PROJECTS, self.slug, "decisions.json"), [])

    def tearDown(self):
        B.model_groups = self._mg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def peer(self, name, ago=0, status="idle", model="claude-fable-5"):
        B.write_json(os.path.join(B.PEERS, f"{name}.json"), {"peer": name, "model": model, "status": status, "last_seen": iso(ago)})

    def decision(self, tid, **kw):
        items = B.read_json(os.path.join(P.PROJECTS, self.slug, "decisions.json"), [])
        items.append({"task_id": tid, "status": "adopted", "title": tid, "created": iso(), "history": [], **kw})
        B.write_json(os.path.join(P.PROJECTS, self.slug, "decisions.json"), items)

    def command(self, tid, taken=False, age=0):
        cid = (dt.datetime.now() - dt.timedelta(seconds=age)).strftime("%Y%m%d-%H%M%S") + "-ab12"
        path = os.path.join(P.COMMANDS, cid + (".taken" if taken else ".json"))
        B.write_json(path, {"id": cid, "kind": "implement", "payload": {"project": self.slug, "task_id": tid}, "created": iso(age), "status": "queued"})
        os.utime(path, (time.time() - age, time.time() - age))
        return cid

    def build(self, tid, teammates):
        B.write_json(os.path.join(P.BUILDS, tid + ".json"), {"task_id": tid, "repo": self.tmp, "base": "abc", "created": iso(120), "status": "building", "teammates": teammates})

    def impl(self, tid):
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        recs = next(p for p in P._full_state()["projects"] if p["slug"] == self.slug)["decisions"]
        return next(x for x in recs if x["task_id"] == tid).get("implementation")


class TestStates(Base):
    def test_six_states(self):
        self.peer("f1", ago=10)
        for t in ("t-unassigned", "t-queued", "t-claimed", "t-dispatched", "t-building", "t-review", "t-merged"):
            self.decision(t, execution="pending")
        self.decision("t-nokey")  # execution 键缺失 = pending
        self.decision("t-released", execution="released")
        self.command("t-queued", age=180)
        self.command("t-claimed", taken=True, age=60)
        self.build("t-dispatched", {"f1": {"path": "x", "branch": "b", "commit": "", "review": "", "merged": False}})
        self.build("t-building", {"f1": {"path": "x", "branch": "b", "commit": "", "review": "", "merged": False}})
        os.makedirs(os.path.join(B.INBOX, "f1"), exist_ok=True)
        B.write_json(os.path.join(B.INBOX, "f1", "t-building.taken"), {"task_id": "t-building"})
        self.build("t-review", {"f1": {"path": "x", "branch": "b", "commit": "0123456789abcdef", "review": "", "merged": False}})
        self.build("t-merged", {"f1": {"path": "x", "branch": "b", "commit": "0123456789abcdef", "review": "PASS", "merged": True, "merge_commit": "fedcba9876543210"}})
        self.assertEqual(self.impl("t-unassigned")["state"], "unassigned")
        q = self.impl("t-queued")
        self.assertEqual((q["state"], q["queue_pos"]), ("queued", 1))
        self.assertGreaterEqual(q["wait_sec"], 170)
        self.assertIn("已等 3 分钟", q["label"])
        self.assertEqual(self.impl("t-claimed")["state"], "claimed")
        d = self.impl("t-dispatched")
        self.assertEqual((d["state"], d["label"]), ("dispatched", "已建单，等待 builder 领取"))
        b = self.impl("t-building")
        self.assertEqual((b["state"], b["peer"]), ("building", "f1"))
        r = self.impl("t-review")
        self.assertEqual((r["state"], r["commit"]), ("review", "0123456"))
        m = self.impl("t-merged")
        self.assertEqual((m["state"], m["merge_commit"]), ("merged", "fedcba9"))
        self.assertEqual(self.impl("t-nokey")["state"], "unassigned")
        self.assertIsNone(self.impl("t-released"))
        self.assertFalse(any(x["blocked"] for x in (q, d)), "builder 在线时不标 blocked")

    def test_claimed_over_1200s_counts_as_queued_and_blocked_when_no_builder(self):
        self.peer("f1", ago=600)  # builder 组成员离线
        self.decision("t1", execution="pending")
        self.command("t1", taken=True, age=1300)
        st = P._full_state()
        im = next(x for x in st["projects"][0]["decisions"] if x["task_id"] == "t1")["implementation"]
        self.assertEqual(im["state"], "queued", "超过 1200 秒的 .taken 视为退回队列")
        self.assertTrue(im["blocked"])
        self.assertEqual(im["builders"]["online_n"], 0)
        self.assertEqual([m["peer"] for m in im["builders"]["members"]], ["f1"])
        self.assertEqual(st["builders"]["groups"], ["claude-fable-5"])

    def test_fake_building_is_dispatched_not_building(self):
        self.peer("f1", ago=10)
        self.decision("t1", execution="pending")
        self.build("t1", {"f1": {"path": "x", "branch": "b", "commit": "", "review": "", "merged": False}, "f2": {"path": "y", "branch": "b2", "commit": "", "review": "", "merged": False}})
        os.makedirs(os.path.join(B.INBOX, "f1"), exist_ok=True)
        B.write_json(os.path.join(B.INBOX, "f1", "t1.json"), {"task_id": "t1"})  # 未改名 .taken
        im = self.impl("t1")
        self.assertEqual(im["state"], "dispatched")
        self.assertNotIn("在建", im["label"])

    def test_listdir_calls_constant_in_decisions(self):
        self.peer("f1", ago=10)
        real = os.listdir

        def count(n):
            for i in range(n):
                self.decision(f"d{n}-{i}", execution="pending")
            calls = [0]

            def spy(p="."):
                calls[0] += 1
                return real(p)
            os.listdir = spy
            try:
                P._STATE_CACHE.update({"at": 0.0, "data": None})
                P._full_state()
            finally:
                os.listdir = real
            return calls[0]
        a = count(3)
        b = count(40)
        self.assertEqual(a, b, f"listdir 次数随决策数增长：{a} → {b}")

    def test_read_path_writes_nothing(self):
        self.peer("f1", ago=10)
        self.decision("t1", execution="pending")
        self.command("t1")
        self.build("t1", {"f1": {"path": "x", "branch": "b", "commit": "", "review": "", "merged": False}})
        watched = [os.path.join(P.PROJECTS, self.slug, "decisions.json")]
        for root, _, files in os.walk(os.path.join(self.tmp, "console")):
            watched += [os.path.join(root, f) for f in files]
        before = {p: (os.path.getmtime(p), os.path.getsize(p)) for p in watched}
        P._full_state()
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        P._full_state()
        after = {p: (os.path.getmtime(p), os.path.getsize(p)) for p in watched}
        self.assertEqual(before, after)
        names = set()
        for root, _, files in os.walk(os.path.join(self.tmp, "console")):
            names |= {os.path.join(root, f) for f in files}
        self.assertEqual(names, set(p for p in watched if os.sep + "console" + os.sep in p), "console/ 下没有新增文件")


class TestFixScript(Base):
    def run_script(self, *args):
        r = subprocess.run([sys.executable, os.path.join(REPO, "tools", "fix_execution_5054.py"), "--root", self.tmp, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_dry_run_then_apply(self):
        self.decision("c1", status="cancelled", execution="pending")
        self.decision("c2", status="cancelled", execution="pending")
        self.decision("c3", status="superseded", execution="pending")
        self.decision("m1", execution="pending")
        self.build("m1", {"f1": {"path": "x", "branch": "b", "commit": "0123456789abcdef", "review": "PASS", "merged": True, "merge_commit": "fedcba9876543210"}})
        self.decision("a1", execution="pending", artifacts=[{"path": "x.md"}])
        self.decision("k1", execution="pending")
        path = os.path.join(P.PROJECTS, self.slug, "decisions.json")
        with open(path, encoding="utf-8") as f:
            before = f.read()
        out = self.run_script()
        self.assertIn("dry-run", out)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before, "默认 dry-run 不写库")
        for tid in ("c1", "c2", "c3"):
            self.assertRegex(out, tid + r".*n-a.*direct")
        self.assertRegex(out, r"m1.*released.*strong")
        self.assertRegex(out, r"a1.*released.*suggested")
        self.assertRegex(out, r"k1.*pending.*keep")
        self.run_script("--apply")
        items = {x["task_id"]: x for x in B.read_json(path)}
        self.assertEqual([items[t]["execution"] for t in ("c1", "c2", "c3")], ["n-a"] * 3)
        self.assertEqual(items["m1"]["execution"], "released")
        self.assertEqual(items["a1"]["execution"], "pending", "建议级默认不落")
        self.assertEqual(items["k1"]["execution"], "pending")
        self.assertEqual(items["m1"]["correction"][0]["to"], "released")
        self.assertEqual(set(items["m1"].keys()) - set(items["k1"].keys()), {"correction"}, "只改 execution 与追加 correction")
        self.assertEqual(items["m1"]["status"], "adopted")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "console", "priority.json")))
        self.run_script("--apply", "--include-suggested")
        items = {x["task_id"]: x for x in B.read_json(path)}
        self.assertEqual(items["a1"]["execution"], "released")


if __name__ == "__main__":
    unittest.main(verbosity=2)
