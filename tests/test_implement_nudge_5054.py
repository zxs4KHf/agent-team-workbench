# 任务 5054 自测（验收 D / E）：implement_nudge 按 (implement, task_id) 幂等；导出任务包只追加 project note、不写 console/。
# 跑法：py -3 tests/test_implement_nudge_5054.py（仓库根目录）。全部状态建在临时目录。
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_core as B  # noqa: E402
import bus_projects as P  # noqa: E402
import bus_web as W  # noqa: E402


def iso(sec_ago=0):
    return (dt.datetime.now() - dt.timedelta(seconds=sec_ago)).strftime("%Y-%m-%dT%H:%M:%S")


class TestNudgeAndPack(unittest.TestCase):
    def setUp(self):
        from http.server import ThreadingHTTPServer
        # 下面改的模块级全局在 tearDown 里全部还原（以前只还原 2 个；和别的测试同进程跑时会指向已删的临时目录）
        self._saved = [(m, n, getattr(m, n)) for m, names in (
            (B, ("ROOT", "PEERS", "INBOX", "OUTBOX", "LOG", "LOCK", "PRIORITY", "WORKSPACE", "model_groups")),
            (P, ("PROJECTS", "COMMANDS", "DONE", "BUILDS", "ROLES", "NUDGES"))) for n in names]
        self.tmp = tempfile.mkdtemp(prefix="bus5054n-")
        B.ROOT = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.LOCK = os.path.join(self.tmp, "console", "controller.lock")
        B.PRIORITY = os.path.join(self.tmp, "console", "priority.json")
        self._ws = B.WORKSPACE
        B.WORKSPACE = self.tmp
        P.PROJECTS = os.path.join(self.tmp, "projects")
        P.COMMANDS = os.path.join(self.tmp, "console", "commands")
        P.DONE = os.path.join(self.tmp, "console", "done")
        P.BUILDS = os.path.join(self.tmp, "console", "builds")
        P.ROLES = os.path.join(self.tmp, "console", "roles.json")
        P.NUDGES = os.path.join(self.tmp, "console", "nudges.json")
        for d in (B.PEERS, B.INBOX, B.OUTBOX, B.LOG, P.PROJECTS, P.COMMANDS, P.DONE, P.BUILDS):
            os.makedirs(d, exist_ok=True)
        self._mg = B.model_groups
        B.model_groups = lambda: []
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        self.slug = "p1"
        os.makedirs(os.path.join(P.PROJECTS, self.slug))
        B.write_json(os.path.join(P.PROJECTS, self.slug, "project.json"), {"slug": self.slug, "name": "p1", "peers": [], "mode": "independent", "context_files": [], "next": [], "notes": []})
        os.makedirs(os.path.join(self.tmp, "log"), exist_ok=True)
        with open(os.path.join(self.tmp, "log", "t1.merge.md"), "w", encoding="utf-8") as f:
            f.write("# 合并稿 t1\n\n## 共识要点\n- x\n\n## 最终方案\n1. 改 `bus_web.py:470` 与 `web/v2/js/app.js`。\n2. 加 `tests/test_x_5054.py`。\n\n## 验收\n- A 两次点击只一条指令\n\n## 验证依据\n- 已落地的旧功能不算\n")
        B.write_json(os.path.join(P.PROJECTS, self.slug, "decisions.json"), [
            {"task_id": "t1", "status": "adopted", "title": "任务一", "execution": "pending", "merge_path": "log/t1.merge.md", "created": iso(), "history": []},
            {"task_id": "t2", "status": "adopted", "title": "任务二", "execution": "pending", "created": iso(), "history": []},
            {"task_id": "t3", "status": "merged", "title": "任务三", "created": iso(), "history": []},
        ])
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

    def post(self, path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def nudge(self, tid):
        return self.post("/api/command", {"kind": "implement_nudge", "payload": {"project": self.slug, "task_id": tid}})

    def console_files(self):
        out = set()
        for root, _, files in os.walk(os.path.join(self.tmp, "console")):
            out |= {os.path.relpath(os.path.join(root, f), self.tmp) for f in files}
        return out

    def test_double_click_single_command(self):
        code, r1 = self.nudge("t1")
        self.assertEqual((code, r1["dedup"], r1["state"], r1["queue_pos"]), (200, False, "queued", 1), r1)
        code, r2 = self.nudge("t1")
        self.assertEqual((code, r2["dedup"], r2["command_id"]), (200, True, r1["command_id"]), r2)
        cmds = [f for f in os.listdir(P.COMMANDS) if f.endswith(".json")]
        self.assertEqual(len(cmds), 1)
        c = B.read_json(os.path.join(P.COMMANDS, cmds[0]))
        self.assertEqual((c["kind"], c["payload"]["task_id"], c["payload"]["merge_path"]), ("implement", "t1", "log/t1.merge.md"))
        # Leader 领取后（.taken）再点：仍返回同一 id，不新建
        os.replace(os.path.join(P.COMMANDS, cmds[0]), os.path.join(P.COMMANDS, cmds[0][:-5] + ".taken"))
        code, r3 = self.nudge("t1")
        self.assertEqual((r3["dedup"], r3["command_id"], r3["state"]), (True, r1["command_id"], "processing"))
        self.assertEqual([f for f in os.listdir(P.COMMANDS) if f.endswith(".json")], [])
        # 第二条任务排第 2 位
        code, r4 = self.nudge("t2")
        self.assertEqual((r4["dedup"], r4["state"]), (False, "queued"))

    def test_existing_build_not_merged_means_no_new_command(self):
        B.write_json(os.path.join(P.BUILDS, "t1.json"), {"task_id": "t1", "repo": self.tmp, "base": "x", "created": iso(), "status": "building",
                                                        "teammates": {"f1": {"path": "x", "branch": "b", "commit": "", "review": "", "merged": False}}})
        code, r = self.nudge("t1")
        self.assertEqual((code, r["dedup"], r["state"]), (200, True, "building"), r)
        self.assertIn("f1", r["note"])
        self.assertEqual(os.listdir(P.COMMANDS), [])
        st = P._full_state()
        im = next(x for x in st["projects"][0]["decisions"] if x["task_id"] == "t1")["implementation"]
        self.assertEqual(im["state"], "dispatched")

    def test_not_adopted_rejected(self):
        code, r = self.nudge("t3")
        self.assertEqual(code, 400)
        self.assertIn("已采纳", r["error"])
        self.assertEqual(os.listdir(P.COMMANDS), [])

    def test_export_task_pack(self):
        before = self.console_files()
        dec_before = os.path.getmtime(os.path.join(P.PROJECTS, self.slug, "decisions.json"))
        code, r = self.post("/api/task-pack", {"project": self.slug, "task_id": "t1"})
        self.assertEqual(code, 200, r)
        md = r["markdown"]
        self.assertIn("## 最终方案", md)
        self.assertIn("bus_web.py:470", md)
        self.assertIn("## 验收", md)
        self.assertIn("两次点击只一条指令", md)
        self.assertNotIn("已落地的旧功能不算", md, "验证依据段不进任务包")
        self.assertEqual(sorted(r["files"]), ["bus_web.py", "tests/test_x_5054.py", "web/v2/js/app.js"])
        self.assertEqual(r["filename"], "task-pack-t1.md")
        self.assertEqual(self.console_files(), before, "导出不写 console/")
        self.assertEqual(os.path.getmtime(os.path.join(P.PROJECTS, self.slug, "decisions.json")), dec_before, "不改 decisions / execution")
        notes = P.load_project(self.slug)["notes"]
        self.assertEqual(len(notes), 1)
        self.assertIn("t1", notes[0]["text"])
        rec = next(x for x in P.decisions(self.slug) if x["task_id"] == "t1")
        self.assertEqual(rec["execution"], "pending")
        # 没有合并稿的项：9a96 起改为可区分的 422 + merge_path_missing，不再 200 + 占位文案
        code, r = self.post("/api/task-pack", {"project": self.slug, "task_id": "t2"})
        self.assertEqual(code, 422, r)
        self.assertEqual(r["code"], P.MERGE_PATH_MISSING)


if __name__ == "__main__":
    unittest.main(verbosity=2)
