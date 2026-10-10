import json
import multiprocessing
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bus_core as B
import bus_plugin_jobs as PJ
import bus_projects as P
import bus_reassign as RA
import bus_web as W


def _process_submit(root, payload, plugins, queue):
    B.ROOT = B.WORKSPACE = root
    B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(root, n) for n in ("peers", "inbox", "outbox", "log"))
    B.PRIORITY = os.path.join(root, "console", "priority.json")
    queue.put(PJ.submit(payload, plugins))


class PluginJobsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="plugin-jobs-")
        self.saved = [(mod, name, getattr(mod, name)) for mod, names in (
            (B, ("ROOT", "PEERS", "INBOX", "OUTBOX", "LOG", "WORKSPACE", "PRIORITY")),
            (P, ("PROJECTS", "COMMANDS", "DONE"))) for name in names]
        B.ROOT = B.WORKSPACE = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, n) for n in ("peers", "inbox", "outbox", "log"))
        B.PRIORITY = os.path.join(self.tmp, "console", "priority.json")
        P.PROJECTS = os.path.join(self.tmp, "projects")
        P.COMMANDS = os.path.join(self.tmp, "console", "commands")
        P.DONE = os.path.join(self.tmp, "console", "done")
        B.ensure_dirs()
        P.ensure()
        self.plugins = [{"id": "level-design", "enabled": True, "errors": []}]
        self.peer("worker", listening=True)
        self.peer("spare", listening=True)

    def tearDown(self):
        for mod, name, value in self.saved:
            setattr(mod, name, value)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def peer(self, name, listening=True):
        B.write_json(os.path.join(B.PEERS, name + ".json"),
                     {"peer": name, "label": name.upper(), "model": "test-model", "last_seen": B.now_iso(),
                      "activity": "idle", "wait_started": B.now_iso() if listening else "",
                      "wait_timeout": 300 if listening else 0, "secret": "must-not-leak"})

    def payload(self, request_id="req1", **updates):
        body = {"source": "level-design", "request_id": request_id, "peer": "worker", "question": "设计关卡", "ttl_min": 30}
        body.update(updates)
        return body

    def test_agents_whitelist_and_listening(self):
        self.peer("controller", listening=True)
        self.peer("offline", listening=False)
        rows = PJ.agents()["agents"]
        self.assertEqual({r["peer"] for r in rows}, {"worker", "spare", "offline"})
        self.assertEqual(set(rows[0]), {"peer", "label", "model", "online", "listening", "activity", "age_sec"})
        self.assertFalse(next(r for r in rows if r["peer"] == "offline")["listening"])

    def test_dispatch_and_deduplicate(self):
        self.assertEqual(PJ.lookup("level-design", "req1")["state"], "missing")
        first = PJ.submit(self.payload(), self.plugins)
        self.assertEqual(first["state"], "submitted")
        tid = first["task_id"]
        self.assertEqual(first["peer"], "worker")
        meta = B.read_json(os.path.join(B.OUTBOX, tid + ".meta.json"))
        task = B.read_json(os.path.join(B.INBOX, "worker", tid + ".json"))
        self.assertEqual(meta["external_owner"], task["external_owner"])
        self.assertEqual(meta["peers"], ["worker"])
        self.assertEqual(meta["min_answers"], 1)
        self.assertEqual(meta["context_files"], [])
        self.assertEqual(meta["substituted"], {})
        self.assertTrue(meta["question"].startswith("[plugin-job source=level-design request_id=req1]\n"))
        self.assertEqual(PJ.submit(self.payload(), self.plugins)["task_id"], tid)
        self.assertTrue(PJ.submit(self.payload(), self.plugins)["deduplicated"])
        self.assertEqual(len([f for f in os.listdir(B.OUTBOX) if f.endswith(".meta.json")]), 1)
        with self.assertRaises(PJ.JobError) as caught:
            PJ.submit(self.payload(question="改变内容"), self.plugins)
        self.assertEqual(caught.exception.status, 409)

    def test_validation_and_no_task_for_unavailable_peer(self):
        for update in ({"source": "../bad"}, {"request_id": "../bad"}, {"source": "missing"},
                       {"peer": "controller"}, {"peer": "auto"}, {"peer": "ghost"},
                       {"question": " "}, {"question": "x" * 20001}, {"ttl_min": 0}, {"ttl_min": 121}):
            with self.subTest(update=str(update)[:30]), self.assertRaises(PJ.JobError):
                PJ.submit(self.payload(**update), self.plugins)
        self.peer("worker", listening=False)
        with self.assertRaises(PJ.JobError) as caught:
            PJ.submit(self.payload(), self.plugins)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(PJ.lookup("level-design", "req1")["state"], "missing")
        self.assertFalse(os.listdir(B.OUTBOX))

    def test_unknown_result_never_redispatches_and_recovers_from_meta(self):
        real_dispatch = B.dispatch
        def dispatched_then_lost(*args, **kwargs):
            real_dispatch(*args, **kwargs)
            raise ConnectionError("lost response")
        with mock.patch.object(B, "dispatch", side_effect=dispatched_then_lost):
            first = PJ.submit(self.payload(), self.plugins)
        self.assertEqual(first["state"], "unknown")
        self.assertEqual(PJ.lookup("level-design", "req1")["state"], "submitted")
        with mock.patch.object(B, "dispatch", side_effect=AssertionError("duplicate dispatch")):
            replay = PJ.submit(self.payload(), self.plugins)
        self.assertTrue(replay["deduplicated"])
        self.assertEqual(replay["task_id"], PJ.lookup("level-design", "req1")["task_id"])
        # 中断后没有聚合记录时维持 unknown，不能推断任务一定未建立。
        path = os.path.join(self.tmp, ".runtime", "plugin-jobs", "level-design", "req2.json")
        B.write_json(path, {"source": "level-design", "request_id": "req2", "peer": "worker",
                            "fingerprint": PJ._fingerprint("worker", "设计关卡", 30), "state": "dispatching", "task_id": None})
        with mock.patch.object(B, "dispatch", side_effect=AssertionError("duplicate dispatch")):
            self.assertEqual(PJ.submit(self.payload("req2"), self.plugins)["state"], "unknown")

    def test_cross_thread_deduplication(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: PJ.submit(self.payload(), self.plugins), range(2)))
        self.assertEqual(results[0]["task_id"], results[1]["task_id"])
        self.assertEqual(sum(bool(r.get("deduplicated")) for r in results), 1)

    def test_cross_process_deduplication(self):
        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        procs = [ctx.Process(target=_process_submit, args=(self.tmp, self.payload(), self.plugins, queue)) for _ in range(2)]
        for proc in procs:
            proc.start()
        results = [queue.get(timeout=20) for _ in procs]
        for proc in procs:
            proc.join(timeout=20)
            self.assertEqual(proc.exitcode, 0)
        self.assertEqual(results[0]["task_id"], results[1]["task_id"])
        self.assertEqual(sum(bool(r.get("deduplicated")) for r in results), 1)

    def test_recovery_from_archived_meta(self):
        created = PJ.submit(self.payload(), self.plugins)
        tid = created["task_id"]
        B.archive(tid)
        path = os.path.join(self.tmp, ".runtime", "plugin-jobs", "level-design", "req1.json")
        record = B.read_json(path)
        record.update(state="dispatching", task_id=None)
        B.write_json(path, record)
        self.assertEqual(PJ.lookup("level-design", "req1")["task_id"], tid)

    def test_guard_and_leader_isolation(self):
        tid = PJ.submit(self.payload(), self.plugins)["task_id"]
        B.write_json(os.path.join(B.PEERS, "worker.json"), {"peer": "worker", "last_seen": "2000-01-01T00:00:00",
                                                             "activity": "idle", "model": "test-model"})
        self.assertEqual(RA.held_items(), [])
        self.assertEqual(RA.stalled_claims(min_age=0), [])
        self.assertEqual(B.auto_reassign(), [])
        self.assertEqual(B.close_stale(0), [])
        self.assertEqual(B.auto_seal(0), [])
        B.write_text(os.path.join(B.OUTBOX, f"{tid}.worker.md"), "回答")
        self.assertEqual(P.complete_tasks(), [])
        self.assertNotIn(tid, P.notify_note(0, 0))

    def test_external_work_preserves_other_peers_idle_wait(self):
        PJ.submit(self.payload(), self.plugins)
        self.assertEqual(B.suggest_timeout(), 300)
        B.dispatch("project", ["worker"], "independent", "普通协作任务", [])
        self.assertEqual(B.suggest_timeout(), 30)


class PluginJobsHttpCase(PluginJobsCase):
    def setUp(self):
        super().setUp()
        self.plugin_patch = mock.patch.object(W.PL, "discover", return_value=self.plugins)
        self.plugin_patch.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), W.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.plugin_patch.stop()
        super().tearDown()

    def request(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as result:
                return result.status, json.loads(result.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_http_forwarding(self):
        with mock.patch.object(W.P, "full_state", side_effect=AssertionError("large state read")):
            status, agents = self.request("GET", "/api/plugin-jobs/agents")
        self.assertEqual(status, 200)
        self.assertEqual(len(agents["agents"]), 2)
        status, missing = self.request("GET", "/api/plugin-jobs?source=level-design&request_id=req1")
        self.assertEqual((status, missing["state"]), (200, "missing"))
        status, created = self.request("POST", "/api/plugin-jobs", self.payload())
        self.assertEqual((status, created["state"]), (200, "submitted"))
        status, replay = self.request("POST", "/api/plugin-jobs", self.payload())
        self.assertEqual((status, replay["task_id"], replay["deduplicated"]), (200, created["task_id"], True))
        status, conflict = self.request("POST", "/api/plugin-jobs", self.payload(question="另一个任务"))
        self.assertEqual(status, 409)
        self.assertIn("error", conflict)
        self.peer("worker", listening=False)
        status, refused = self.request("POST", "/api/plugin-jobs", self.payload("req2"))
        self.assertEqual(status, 409)
        self.assertIn("error", refused)

    def test_team_options_submit_cancel_http(self):
        P.create_project("Atlas", ["worker", "spare"], "independent", [], default_team=P.PROJECT_TEAM)
        slug = "atlas"
        status, opts = self.request("GET", "/api/plugin-jobs/options?source=level-design")
        self.assertEqual(status, 200)
        self.assertEqual(opts["default_mode"], "race")
        self.assertIn(slug, [p["slug"] for p in opts["projects"]])
        body = {"source": "level-design", "request_id": "http-team", "project": slug,
                "question": "分析关卡", "ttl_min": 30}
        status, created = self.request("POST", "/api/plugin-jobs", body)
        self.assertEqual((status, created["mode"], created["peers"]), (200, "race", ["worker", "spare"]))
        self.assertEqual(created["task_url"], f"/?p={slug}&view=running&t={created['task_id']}")
        status, cancelled = self.request("POST", "/api/plugin-jobs/cancel",
                                         {"source": "level-design", "request_id": "http-team"})
        self.assertEqual((status, cancelled["state"]), (200, "cancelled"))
        status, looked_up = self.request("GET", "/api/plugin-jobs?source=level-design&request_id=http-team")
        self.assertEqual((status, looked_up["state"]), (200, "cancelled"))


if __name__ == "__main__":
    unittest.main()
