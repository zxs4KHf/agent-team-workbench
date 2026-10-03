"""任务档位（docs/adr/0008）：网页派发、重新派发时自动记录 tier；Leader 回执换真实 task_id 后保留；统计脚本分开真实与推断。"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import bus_core as B  # noqa: E402
import bus_projects as P  # noqa: E402
import bus_web as W  # noqa: E402
import tier_stats  # noqa: E402


class DispatchTier(unittest.TestCase):
    def setUp(self):
        from http.server import ThreadingHTTPServer
        self.temp = tempfile.TemporaryDirectory(prefix="wb-tier-")
        root = Path(self.temp.name)
        self.patches = [mock.patch.object(B, "ROOT", str(root)), mock.patch.object(B, "WORKSPACE", str(root)),
                        mock.patch.object(P, "PROJECTS", str(root / "projects")),
                        mock.patch.object(P, "COMMANDS", str(root / "console" / "commands")),
                        mock.patch.object(P, "DONE", str(root / "console" / "done")),
                        mock.patch.object(B, "OUTBOX", str(root / "outbox"))]
        for p in self.patches:
            p.start()
        P.ensure()
        P.create_project("p1", [], "independent", [])
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), W.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        P.invalidate_state()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def command(self, kind, payload):
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.server_address[1]}/api/command",
                                     data=json.dumps({"kind": kind, "payload": payload}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def rec(self, task_id):
        return next(x for x in P.decisions("p1") if x["task_id"] == task_id)

    def test_tier_of(self):
        self.assertEqual(P.tier_of(["a"]), "fast")
        self.assertEqual(P.tier_of(["a", "b"]), "multi")
        self.assertEqual(P.tier_of([]), "multi")
        self.assertEqual(P.tier_of(["a", ""]), "fast")
        self.assertEqual(P.tier_of(None), "multi")

    def test_web_dispatch_records_tier_and_survives_ack(self):
        peers = [{"peer": "controller", "online": True}, {"peer": "a", "online": True}, {"peer": "b", "online": True}, {"peer": "c", "online": False}]
        mock.patch.object(B, "list_peers", return_value=peers).start()
        self.addCleanup(mock.patch.stopall)
        one = self.command("dispatch", {"project": "p1", "question": "单做", "peers": ["a"], "mode": "independent"})
        two = self.command("dispatch", {"project": "p1", "question": "对读", "peers": ["a", "b"], "mode": "independent"})
        self.assertEqual((self.rec("pending-" + one["id"])["tier"], self.rec("pending-" + one["id"])["tier_source"]), ("fast", "auto"))
        self.assertEqual(self.rec("pending-" + two["id"])["tier"], "multi")
        self.assertEqual(self.rec("pending-" + one["id"])["online_n"], 2)   # 不算 controller、不算离线
        # b342：ack 只接受磁盘上验证得了的 task_id，所以先造出 dispatch 本该写下的 outbox meta
        os.makedirs(B.OUTBOX, exist_ok=True)
        B.write_json(os.path.join(B.OUTBOX, "20260928-120000-abcd.meta.json"), {"task_id": "20260928-120000-abcd", "peers": ["a"]})
        P.ack_command(one["id"], "task_id: 20260928-120000-abcd")
        self.assertEqual(self.rec("20260928-120000-abcd")["tier"], "fast")

    def test_redispatch_recomputes_tier(self):
        P.upsert_decision("p1", "20260928-110000-0001", status="dispatched", peers=["a", "b"], tier="multi")
        fake = {"task_id": "20260928-110500-0002", "peers": ["b"], "question": "q", "mode": "independent"}
        with mock.patch.object(B, "redispatch", return_value=fake):
            self.command("redispatch", {"project": "p1", "task_id": "20260928-110000-0001", "peers": ["b"]})
        self.assertEqual(self.rec("20260928-110500-0002")["tier"], "fast")
        self.assertEqual(self.rec("20260928-110000-0001")["status"], "superseded")

    def test_online_n_never_blocks(self):
        with mock.patch.object(B, "list_peers", side_effect=OSError("peers 目录坏了")):
            self.assertEqual(P.online_agents_n(), -1)
            one = self.command("dispatch", {"project": "p1", "question": "q", "peers": ["a"], "mode": "independent"})
        self.assertEqual(self.rec("pending-" + one["id"])["online_n"], -1)

    def test_stats_separates_inferred(self):
        P.upsert_decision("p1", "t-fast", status="dispatched", peers=["a"], tier="fast", online_n=3)
        P.upsert_decision("p1", "t-sparse", status="dispatched", peers=["a"], tier="fast", online_n=1)
        P.upsert_decision("p1", "t-nodata", status="dispatched", peers=["a"], tier="fast", online_n=-1)
        P.upsert_decision("p1", "t-old-date", status="dispatched", peers=["a"], tier="fast", created="2020-01-01 00:00:00")
        now = tier_stats.dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        P.upsert_decision("p1", "t-tz", status="dispatched", peers=["a", "b"], tier="multi", created=now + " +08:00")
        P.upsert_decision("p1", "t-old", status="dispatched", peers=["a", "b"])
        P.upsert_decision("p1", "t-sup", status="superseded", peers=["a"], tier="fast")
        P.upsert_decision("p1", "t-bus", status="dispatched")
        self.assertEqual(tier_stats.stats(30)["p1"], {"fast": 2, "fast_sparse": 1, "multi": 1, "inferred_fast": 0, "inferred_multi": 1, "unknown": 1})


if __name__ == "__main__":
    unittest.main()
