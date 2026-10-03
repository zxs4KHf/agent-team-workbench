# 任务 5054 自测（验收 F）：采纳时的「已交付」判定收紧——正文叙述里的「已落地」不再误判为 released；行首明示或 artifacts 非空才 released。
# 跑法：py -3 tests/test_adopt_implemented_regex_5054.py（仓库根目录）。全部状态建在临时目录。
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_core as B  # noqa: E402
import bus_projects as P  # noqa: E402
import bus_web as W  # noqa: E402

NARRATIVE = "# 合并稿\n\n## 本人问题与结论\n- 不需要本人写代码。采纳这一步已经排给 Leader；上一轮 27d5 已落地，本轮补口。\n\n## 最终方案\n1. 改 views.js。\n\n## 验证依据\n- 已实施并上线的旧版不算\n"
EXPLICIT = "# 合并稿\n\n## 结论\n已实施并上线：commit 3445eeb，本轮无需再派。\n\n## 最终方案\n略\n"
NOT_NEEDED = "# 合并稿\n\n## 结论\n- **本任务不需要改动**，现状已满足。\n"


class TestAdoptRegex(unittest.TestCase):
    def setUp(self):
        from http.server import ThreadingHTTPServer
        self.tmp = tempfile.mkdtemp(prefix="bus5054r-")
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
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        self.slug = "p1"
        os.makedirs(os.path.join(P.PROJECTS, self.slug))
        B.write_json(os.path.join(P.PROJECTS, self.slug, "project.json"), {"slug": self.slug, "name": "p1", "peers": [], "mode": "independent", "context_files": [], "next": [], "notes": []})
        docs = {"narr": NARRATIVE, "explicit": EXPLICIT, "notneeded": NOT_NEEDED}
        items = []
        for k, txt in docs.items():
            with open(os.path.join(self.tmp, "log", f"{k}.merge.md"), "w", encoding="utf-8") as f:
                f.write(txt)
            items.append({"task_id": k, "status": "merged", "title": k, "merge_path": f"log/{k}.merge.md", "created": "2026-09-23T00:00:00", "history": []})
        items.append({"task_id": "arts", "status": "merged", "title": "arts", "merge_path": "log/narr.merge.md", "artifacts": [{"path": "web/x.html", "label": "x", "kind": "page"}], "created": "2026-09-23T00:00:00", "history": []})
        B.write_json(os.path.join(P.PROJECTS, self.slug, "decisions.json"), items)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), W.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        B.WORKSPACE = self._ws
        shutil.rmtree(self.tmp, ignore_errors=True)

    def adopt(self, tid):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/command", data=json.dumps({"kind": "adopt", "payload": {"project": self.slug, "task_id": tid}}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def rec(self, tid):
        return next(x for x in P.decisions(self.slug) if x["task_id"] == tid)

    def implement_cmds(self):
        out = []
        for f in os.listdir(P.COMMANDS):
            c = B.read_json(os.path.join(P.COMMANDS, f), {}) or {}
            if c.get("kind") == "implement":
                out.append(c["payload"]["task_id"])
        for f in os.listdir(P.DONE):
            c = B.read_json(os.path.join(P.DONE, f), {}) or {}
            if c.get("kind") == "implement":
                out.append(c["payload"]["task_id"])
        return sorted(out)

    def test_helper(self):
        self.assertFalse(W.declares_implemented(NARRATIVE))
        self.assertTrue(W.declares_implemented(EXPLICIT))
        self.assertTrue(W.declares_implemented(NOT_NEEDED))
        self.assertFalse(W.declares_implemented("## 验证依据\n已实施并上线"), "验证依据段之后不算")
        self.assertFalse(W.declares_implemented("我们认为已上线的功能不需要改"), "句中出现不算")
        self.assertTrue(W.declares_implemented("结论：本次不需要改动"))

    def test_narrative_mention_still_queues_implement(self):
        code, r = self.adopt("narr")
        self.assertEqual(code, 200, r)
        self.assertEqual(self.rec("narr")["execution"], "pending")
        self.assertEqual(self.implement_cmds(), ["narr"])

    def test_explicit_delivery_released_without_implement(self):
        self.adopt("explicit")
        self.assertEqual(self.rec("explicit")["execution"], "released")
        self.adopt("notneeded")
        self.assertEqual(self.rec("notneeded")["execution"], "released")
        self.assertEqual(self.implement_cmds(), [])

    def test_no_plan_without_declaration_warns_not_released(self):
        # 缺「## 最终方案」且没有明示：不判 released、不排 implement，只记 merge_read_state 告警
        p = os.path.join(self.tmp, "log", "bare.merge.md")
        open(p, "w", encoding="utf-8").write("# 合并稿\n\n## 结论\n- 还在讨论。\n")
        P.upsert_decision(self.slug, "bare", status="merged", title="bare", merge_path="log/bare.merge.md")
        self.adopt("bare")
        rec = self.rec("bare")
        self.assertNotEqual(rec.get("execution"), "released")
        self.assertEqual(rec.get("merge_read_state"), P.MERGE_NO_FINAL_PLAN_SECTION)

    def test_artifacts_alone_do_not_prove_delivery(self):
        self.adopt("arts")
        self.assertEqual(self.rec("arts")["execution"], "pending")
        self.assertEqual(self.implement_cmds(), ["arts"])

    def test_repeated_adopt_deduplicates(self):
        self.adopt("narr")
        self.adopt("narr")
        self.assertEqual(self.implement_cmds(), ["narr"])

    def test_released_status_survives_repeated_adopt(self):
        P.upsert_decision(self.slug, "arts", execution="released")
        self.adopt("arts")
        self.assertEqual(self.rec("arts")["execution"], "released")
        self.assertEqual(self.implement_cmds(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
