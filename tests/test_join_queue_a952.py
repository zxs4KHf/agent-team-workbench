# -*- coding: utf-8 -*-
"""持久 join 队列（20260929-152248-a952）：全部 stub，不跑真实 groups/join、chat_probe 或额度消费。
arena_store 根目录指向临时目录；上游/Workbench/心跳全部为假对象。"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "arena", "server"))

import arena_core as C  # noqa: E402
import arena_join_queue as JQ  # noqa: E402
import arena_store as ST  # noqa: E402

SECRET_TOKEN = "FAKEtokenQzXwVuTsRqPoNmLkJiHgFeDcBa9876543210_-Zy"  # 合成假值（e5de：不再使用真实总线 token）
PROMPT = f"请连接 MCP https://mcp.example.test/mcp/{SECRET_TOKEN} 并用 Authorization: Bearer sk-live-ABCDEF0123456789 登录，peer=claude"


def _wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="a952-")
        self._old_data = ST.DATA
        ST.DATA = os.path.join(self.tmp, "data")
        os.makedirs(os.path.join(self.tmp, "wb", "peers"), exist_ok=True)
        ST.save("settings", {**ST.DEFAULTS["settings"], "workbench_root": os.path.join(self.tmp, "wb")})
        ST.save("accounts", {"accounts": [
            {"email": "ok@example.com", "has_token": True, "status": "ok", "status_code": 200},
            {"email": "dead@example.com", "has_token": False, "cookie_count": 0, "status": "ok", "status_code": 200},
        ], "active_email": "", "synced_at": ""})
        ST.save("sessions", {"sessions": [
            {"id": "s1", "account": "ok@example.com", "title": "claude-opus-4", "detected_model": "claude-opus-4"},
            {"id": "s2", "account": "ok@example.com", "title": "claude-opus-4 b", "detected_model": "claude-opus-4"},
            {"id": "s3", "account": "dead@example.com", "title": "claude-opus-4 c", "detected_model": "claude-opus-4"},
            {"id": "g1", "account": "ok@example.com", "title": "gpt-5", "detected_model": "gpt-5"},
        ], "synced_at": ""})
        ST.save("routes", {"routes": {}, "updated_at": "", "groups": [
            {"id": "claude", "name": "Claude 组", "match": ["claude-opus-4"], "tier": "strong", "custom": False, "manual_add": [], "manual_remove": [], "key_id": "", "order": 1},
            {"id": "gpt", "name": "GPT 组", "match": ["gpt-5"], "tier": "daily", "custom": False, "manual_add": [], "manual_remove": [], "key_id": "", "order": 2},
        ]})
        JQ._reset_for_tests()
        self._orig = {"join_once": C.join_once, "urlopen": C._urlopen, "route": C.ensure_upstream_route, "probe": C.UP.chat_probe}
        self.calls = []
        C.JOIN_RESERVED.clear()

    def tearDown(self):
        JQ._reset_for_tests()
        C.join_once = self._orig["join_once"]
        C._urlopen = self._orig["urlopen"]
        C.ensure_upstream_route = self._orig["route"]
        C.UP.chat_probe = self._orig["probe"]
        ST.DATA = self._old_data
        shutil.rmtree(self.tmp, ignore_errors=True)

    # 假的单项执行体：记录调用、可阻塞、可指定结果
    def fake_join(self, registered=True, block=None, raise_exc=None, states=("precheck", "sending", "waiting_peer")):
        def fn(gid, sid, peer, wb, job, set_state=None, wait_rounds=None, wait_step=None):
            self.calls.append((gid, sid))
            job["payload"]["peer_name"] = "claude"
            for s in states:
                set_state(s, f"phase {s}")
            if block:
                block.wait(10)
            if raise_exc:
                raise raise_exc
            return {"peer": "claude", "name_source": "server-alloc", "registered": registered, "heartbeat_model": "claude-opus-4",
                    "expected_canon": "claude-opus-4", "first_text": "hi", "reply": "hi", "relay_error": "", "sent_at": ST.now_text()}
        return fn

    def job(self, jid):
        return JQ.get_job(jid)

    def store_file(self):
        with open(os.path.join(ST.DATA, "join_jobs.json"), encoding="utf-8") as f:
            return f.read()


class TestQueueSemantics(Base):
    def test_same_request_id_only_sends_once(self):
        C.join_once = self.fake_join(registered=True)
        a = JQ.enqueue("claude", "s1", request_id="r1")
        b = JQ.enqueue("claude", "s1", request_id="r1")  # 第二个标签页同 request
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(b["dedup"], "same_request")
        self.assertTrue(_wait(lambda: self.job(a["id"])["status"] == "connected"))
        self.assertEqual(self.calls, [("claude", "s1")])
        c = JQ.enqueue("claude", "s1", request_id="r1")  # 完成后再重复提交仍返回原 job，不再发
        self.assertEqual(c["id"], a["id"])
        self.assertEqual(self.calls, [("claude", "s1")])

    def test_inflight_same_group_session_dedup(self):
        gate = threading.Event()
        C.join_once = self.fake_join(block=gate)
        a = JQ.enqueue("claude", "s1", request_id="tab-a")
        b = JQ.enqueue("claude", "s1", request_id="tab-b")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(b["dedup"], "in_flight")
        gate.set()
        self.assertTrue(_wait(lambda: self.job(a["id"])["status"] == "connected"))
        self.assertEqual(len(self.calls), 1)

    def test_max_concurrency_one(self):
        gate = threading.Event()
        C.join_once = self.fake_join(block=gate)
        a = JQ.enqueue("claude", "s1", request_id="c1")
        b = JQ.enqueue("claude", "s2", request_id="c2")
        self.assertTrue(_wait(lambda: self.job(a["id"])["status"] == "waiting_peer"))
        time.sleep(0.8)
        self.assertEqual(self.job(b["id"])["status"], "queued")
        self.assertEqual(len(self.calls), 1)
        gate.set()
        self.assertTrue(_wait(lambda: self.job(b["id"])["status"] == "connected"))
        self.assertEqual(len(self.calls), 2)

    def test_config_bounds(self):
        self.assertEqual(JQ.set_config(max_concurrency=9)["max_concurrency"], 3)
        self.assertEqual(JQ.set_config(max_concurrency=0)["max_concurrency"], 1)
        self.assertEqual(JQ.get_config()["max_concurrency"], 1)

    def test_registered_false_is_unconfirmed_never_done(self):
        C.join_once = self.fake_join(registered=False)
        a = JQ.enqueue("claude", "s1", request_id="u1")
        self.assertTrue(_wait(lambda: self.job(a["id"])["status"] in JQ.TERMINAL))
        j = self.job(a["id"])
        self.assertEqual(j["status"], "unconfirmed")
        self.assertNotIn(j["status"], ("done", "success", "connected"))
        self.assertIsNone(j["binding"])

    def test_connected_binding(self):
        C.join_once = self.fake_join(registered=True)
        a = JQ.enqueue("claude", "s1", request_id="b1")
        self.assertTrue(_wait(lambda: self.job(a["id"])["status"] == "connected"))
        b = self.job(a["id"])["binding"]
        self.assertEqual((b["group"], b["session_id"], b["request_id"], b["peer"]), ("claude", "s1", "b1", "claude"))
        self.assertTrue(b["sent_at"])
        sess = next(s for s in ST.load("sessions")["sessions"] if s["id"] == "s1")
        self.assertEqual(sess["join_meta"]["request_id"], "b1")
        self.assertNotIn("password", json.dumps(sess))

    def test_restart_queued_resumes_sent_becomes_unconfirmed(self):
        gate = threading.Event()
        C.join_once = self.fake_join(block=gate)
        a = JQ.enqueue("claude", "s1", request_id="rs1")
        b = JQ.enqueue("claude", "s2", request_id="rs2")
        self.assertTrue(_wait(lambda: self.job(a["id"])["status"] == "waiting_peer"))
        # 模拟进程被杀：worker 停掉、进程内领取记录清空，文件里 a=waiting_peer、b=queued 原样留着
        stuck = list(JQ._RUNNING.values())
        JQ._reset_for_tests(wait_running=False)
        self.calls.clear()
        C.join_once = self.fake_join(registered=True)
        view = JQ.jobs_view()  # 重启后的任何读取都先做恢复
        st = {j["id"]: j for j in view["jobs"]}
        self.assertEqual(st[a["id"]]["status"], "unconfirmed")
        self.assertEqual(st[a["id"]]["reason_code"], "restarted")
        self.assertEqual(st[b["id"]]["status"], "queued")
        JQ._ensure_worker()
        self.assertTrue(_wait(lambda: self.job(b["id"])["status"] == "connected"))
        self.assertEqual(self.calls, [("claude", "s2")])  # 只有 queued 的继续，waiting 的没有重发
        gate.set()  # 收尾：放掉「已死」线程，保证不在 tearDown 之后写临时目录
        for t in stuck:
            t.join(5)

    def test_cancel_queued_prevents_send(self):
        gate = threading.Event()
        C.join_once = self.fake_join(block=gate)
        a = JQ.enqueue("claude", "s1", request_id="k1")
        b = JQ.enqueue("claude", "s2", request_id="k2")
        self.assertTrue(_wait(lambda: self.job(a["id"])["status"] == "waiting_peer"))
        r = JQ.cancel(b["id"])
        self.assertTrue(r["cancelled"])
        gate.set()
        self.assertTrue(_wait(lambda: self.job(a["id"])["status"] == "connected"))
        time.sleep(0.6)
        self.assertEqual(self.job(b["id"])["status"], "cancelled")
        self.assertEqual(self.calls, [("claude", "s1")])
        with self.assertRaises(ValueError):
            JQ.cancel(b["id"])  # terminal 不可取消

    def test_cancel_after_sent_is_late(self):
        gate = threading.Event()
        C.join_once = self.fake_join(block=gate)
        a = JQ.enqueue("claude", "s1", request_id="k3")
        self.assertTrue(_wait(lambda: self.job(a["id"])["status"] == "waiting_peer"))
        r = JQ.cancel(a["id"])
        self.assertFalse(r["cancelled"])
        self.assertEqual(r["note"], JQ.LATE_CANCEL_NOTE)
        self.assertEqual(self.job(a["id"])["status"], "waiting_peer")
        gate.set()
        self.assertTrue(_wait(lambda: self.job(a["id"])["status"] == "connected"))
        self.assertEqual(self.job(a["id"])["cancel_note"], JQ.LATE_CANCEL_NOTE)
        with self.assertRaises(ValueError):
            JQ.cancel(a["id"])

    def test_cancel_before_sending_phase_blocks_delivery(self):
        # 预检期间取消：join_once 走到 sending 回调时被拒绝，不会投递
        started = threading.Event()
        proceed = threading.Event()

        def fn(gid, sid, peer, wb, job, set_state=None, wait_rounds=None, wait_step=None):
            set_state("precheck", "预检")
            started.set()
            proceed.wait(5)
            set_state("sending", "发送")
            self.calls.append("SENT")
            return {"registered": True, "peer": "x"}
        C.join_once = fn
        a = JQ.enqueue("claude", "s1", request_id="k4")
        self.assertTrue(started.wait(5))
        self.assertTrue(JQ.cancel(a["id"])["cancelled"])
        proceed.set()
        time.sleep(0.5)
        self.assertEqual(self.job(a["id"])["status"], "cancelled")
        self.assertNotIn("SENT", self.calls)

    def test_credential_failure_aborts_rest_of_batch_but_keeps_connected(self):
        C.join_once = self.fake_join(registered=True)
        ok = JQ.enqueue("claude", "s1", request_id="p:claude:s1", batch="p")
        self.assertTrue(_wait(lambda: self.job(ok["id"])["status"] == "connected"))
        gate = threading.Event()
        C.join_once = self.fake_join(block=gate, raise_exc=C.JoinError("no_token", "账号 x 凭据失效"))
        bad = JQ.enqueue("claude", "s2", request_id="p:claude:s2", batch="p")
        rest = JQ.enqueue("gpt", "g1", request_id="p:gpt:g1", batch="p")
        self.assertTrue(_wait(lambda: self.job(bad["id"])["status"] == "waiting_peer"))
        gate.set()
        self.assertTrue(_wait(lambda: self.job(bad["id"])["status"] == "failed"))
        self.assertEqual(self.job(bad["id"])["reason_code"], "no_token")
        self.assertTrue(_wait(lambda: self.job(rest["id"])["status"] == "cancelled"))
        self.assertEqual(self.job(rest["id"])["reason_code"], "aborted_batch")
        self.assertEqual(self.job(ok["id"])["status"], "connected")  # 已连接的保留

    def test_spend_limit_passthrough_does_not_abort_batch(self):
        C.join_once = self.fake_join(raise_exc=C.JoinError("spend_limit", "spend limit reached"))
        bad = JQ.enqueue("claude", "s1", request_id="q:1", batch="q")
        self.assertTrue(_wait(lambda: self.job(bad["id"])["status"] == "failed"))
        self.assertEqual(self.job(bad["id"])["reason_code"], "spend_limit")
        C.join_once = self.fake_join(registered=True)
        nxt = JQ.enqueue("claude", "s2", request_id="q:2", batch="q")
        self.assertTrue(_wait(lambda: self.job(nxt["id"])["status"] == "connected"))

    def test_jobs_persist_across_reads_and_legacy_jobs_untouched(self):
        C.join_once = self.fake_join(registered=True)
        a = JQ.enqueue("claude", "s1", request_id="v1")
        self.assertTrue(_wait(lambda: self.job(a["id"])["status"] == "connected"))
        self.assertIn(a["id"], self.store_file())
        self.assertEqual(C.jobs_view(), {"jobs": []})  # 旧内存 JOBS 不受影响
        pub = JQ.jobs_view()["jobs"][0]
        for k in ("id", "kind", "status", "started_at", "progress", "total", "message", "result", "error", "reason_code", "retryable", "payload"):
            self.assertIn(k, pub)


class TestJoinOnceRealBodyStubbed(Base):
    """跑真实 join_once 逻辑，但 Workbench / 中继 / 心跳全 stub。"""

    def _stub(self, reply=PROMPT + "\nBearer sk-live-ABCDEF0123456789 好的", probe_exc=None, on_probe=None):
        class R:
            def __init__(self, data):
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(self.data).encode("utf-8")

        def urlopen(url, timeout=30, headers=None):
            self.calls.append(("connect_link", urllib.parse.urlparse(url).path))
            return R({"prompt": PROMPT})
        C._urlopen = urlopen
        C.ensure_upstream_route = lambda alias, sid: self.calls.append(("route", alias))

        def probe(alias, prompt, timeout=900):
            self.calls.append(("chat_probe", alias))
            if on_probe:  # 消息「发出」之后模拟 peer 心跳文件出现（此时服务端已分配好名字）
                on_probe(self.job_ref["payload"]["peer_name"])
            if probe_exc:
                raise probe_exc
            return {"choices": [{"message": {"content": reply}}]}
        C.UP.chat_probe = probe

    def _write_peer(self, name, model="claude-opus-4", status="idle", mtime=None):
        p = os.path.join(self.tmp, "wb", "peers", name + ".json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"model": model, "status": status}, f)
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    def _run_once(self, sid="s1", wait_rounds=1, states=None):
        self.job_ref = {"payload": {}}
        res = C.join_once("claude", sid, "", "http://wb.test", self.job_ref, set_state=(lambda s, m: states.append(s)) if states is not None else None, wait_rounds=wait_rounds, wait_step=0)
        return res, self.job_ref

    def test_pre_existing_peer_file_is_never_reused_as_name(self):
        self._stub()
        self._write_peer("claudeopus", mtime=time.time() - 3600)  # 离线 peer 残留文件占着 base 名（base = canon 去符号取前 10 位）
        res, job = self._run_once()
        self.assertFalse(res["registered"])
        self.assertNotEqual(res["peer"], "claudeopus")  # 取名禁区：不会冒名旧文件
        self.assertEqual(res["name_source"], "collision-renamed")
        self.assertIn(("chat_probe", "join@s1"), self.calls)

    def test_old_heartbeat_file_not_accepted(self):
        # 名字分配后、消息发出时出现一个 mtime 早于 sent_at 的旧文件：不得判成功
        self._stub(on_probe=lambda name: self._write_peer(name, mtime=time.time() - 3600))
        res, job = self._run_once(wait_rounds=2)
        self.assertFalse(res["registered"])
        self.assertEqual(job["reason_code"], "no_heartbeat")

    def test_model_mismatch_and_bad_status_not_accepted(self):
        self._stub(on_probe=lambda name: self._write_peer(name, model="gpt-5", mtime=time.time() + 30))
        res, job = self._run_once()
        self.assertFalse(res["registered"])
        self.assertEqual(job["reason_code"], "model_mismatch")
        self.calls.clear()
        self._stub(on_probe=lambda name: self._write_peer(name, model="claude-opus-4", status="offline", mtime=time.time() + 30))
        res, job = self._run_once()
        self.assertFalse(res["registered"])
        self.assertEqual(job["reason_code"], "bad_status")

    def test_fresh_heartbeat_registers_and_output_is_sanitized(self):
        self._stub(on_probe=lambda name: self._write_peer(name, mtime=time.time() + 30))
        states = []
        res, job = self._run_once(states=states)
        name = res["peer"]
        self.assertTrue(res["registered"])
        self.assertEqual(states, ["precheck", "sending", "waiting_peer"])
        blob = json.dumps({"res": res, "job": job}, ensure_ascii=False)
        self.assertNotIn(SECRET_TOKEN, blob)
        self.assertNotIn("sk-live", blob)
        self.assertNotIn("Authorization: Bearer", blob)
        self.assertLessEqual(len(res["reply"]), 200)
        self.assertLessEqual(len(res["relay_error"]), 200)
        sess = next(s for s in ST.load("sessions")["sessions"] if s["id"] == "s1")
        self.assertEqual(sess["peer"], name)

    def test_spend_and_token_errors_passthrough_from_relay(self):
        self._stub(probe_exc=RuntimeError("HTTP 429 spend limit reached"))
        with self.assertRaises(C.JoinError) as cm:
            self._run_once()
        self.assertEqual(cm.exception.reason_code, "spend_limit")
        self._stub(probe_exc=RuntimeError("401 no publicAccessToken for user@example.com"))
        with self.assertRaises(C.JoinError) as cm:
            self._run_once()
        self.assertEqual(cm.exception.reason_code, "no_token")
        self.assertNotIn("user@example.com", str(cm.exception))

    def test_precheck_blocks_dead_account_before_send(self):
        self._stub()
        with self.assertRaises(C.JoinError) as cm:
            self._run_once(sid="s3")
        self.assertEqual(cm.exception.reason_code, "no_token")
        self.assertFalse(any(c[0] == "chat_probe" for c in self.calls))

    def test_queue_end_to_end_with_real_join_once_persists_only_sanitized(self):
        self.job_ref = {"payload": {}}
        self._stub(probe_exc=RuntimeError(f"relay said token={SECRET_TOKEN} https://mcp.example.test/mcp/{SECRET_TOKEN}"))
        JQ.set_config(wait_rounds=1, wait_step_sec=0)
        a = JQ.enqueue("claude", "s1", request_id="e2e")
        self.assertTrue(_wait(lambda: self.job(a["id"])["status"] in JQ.TERMINAL, 10))
        j = self.job(a["id"])
        self.assertEqual(j["status"], "unconfirmed")
        raw = self.store_file()
        self.assertNotIn(SECRET_TOKEN, raw)
        self.assertNotIn(PROMPT[:40], raw)
        self.assertNotIn("sk-live", raw)
        self.assertTrue(j["result"]["relay_error"])


class TestSanitize(unittest.TestCase):
    def test_rules(self):
        s = C.sanitize_text(f"Authorization: Bearer abc.DEF-123 token=sk-abcdef1234 key: KEY123456 https://mcp.x.test/mcp/{SECRET_TOKEN}?t=1 u@ex.com 密码：p@ss 401 no publicAccessToken " + "x" * 500)
        self.assertNotIn("abc.DEF-123", s)
        self.assertNotIn("sk-abcdef1234", s)
        self.assertNotIn("KEY123456", s)
        self.assertNotIn(SECRET_TOKEN, s)
        self.assertNotIn("u@ex.com", s)
        self.assertNotIn("p@ss", s)
        self.assertIn("https://mcp.x.test/[已脱敏]", s)
        self.assertIn("401", s)
        self.assertLessEqual(len(s), 200)
        self.assertEqual(C.sanitize_text(None), "")


class TestCommonPreviewConfirm(Base):
    def setUp(self):
        super().setUp()
        C.join_once = self.fake_join(registered=True)

    def test_common_is_explicit_user_field_only(self):
        rows = {g["id"]: g for g in C.groups_view()["groups"]}
        self.assertFalse(rows["claude"]["common"])  # tier=strong 不等于常用
        self.assertFalse(rows["gpt"]["common"])
        r = C.set_common_groups(["claude", "nope"])
        self.assertEqual(r, {"common": ["claude"], "unknown": ["nope"]})
        self.assertEqual(C.common_group_ids(), ["claude"])
        C.set_common_groups([])
        self.assertEqual(C.common_group_ids(), [])
        C.save_group({"id": "claude", "common": True})
        self.assertEqual(C.common_group_ids(), ["claude"])

    def test_preview_has_no_side_effects_and_defaults_one_per_group(self):
        before = (json.dumps(ST.load("sessions")), json.dumps(ST.load("routes")), json.dumps(ST.load(JQ.KIND)))
        pv = JQ.preview(["claude", "gpt", "missing"])
        after = (json.dumps(ST.load("sessions")), json.dumps(ST.load("routes")), json.dumps(ST.load(JQ.KIND)))
        self.assertEqual(before, after)
        self.assertEqual(self.calls, [])
        self.assertEqual(pv["side_effects"], "none")
        g = {x["group"]: x for x in pv["groups"]}
        self.assertEqual(g["missing"]["skip_reason"], "group_missing")
        sel = [c for c in g["claude"]["candidates"] if c["selected"]]
        self.assertEqual(len(sel), 1)
        self.assertEqual(sel[0]["session_id"], "s1")
        dead = next(c for c in g["claude"]["candidates"] if c["session_id"] == "s3")
        self.assertFalse(dead["eligible"])
        self.assertEqual(dead["skip_reason"], "account_unauthenticated")
        self.assertEqual(g["claude"]["will_send"], 1)
        self.assertEqual(g["claude"]["estimated_cost"]["messages"], 1)
        self.assertEqual(pv["will_send_total"], 2)

    def test_preview_skips_online_and_inflight_but_allows_explicit_second(self):
        # s1 已有在线 Agent（新鲜心跳）
        ST.update("sessions", lambda st: [s.update(peer="claude") for s in st["sessions"] if s["id"] == "s1"])
        with open(os.path.join(self.tmp, "wb", "peers", "claude.json"), "w", encoding="utf-8") as f:
            json.dump({"model": "claude-opus-4", "status": "idle"}, f)
        pv = JQ.preview(["claude"])
        g = pv["groups"][0]
        self.assertEqual(g["skip_reason"], "already_online")
        self.assertEqual(g["will_send"], 0)
        self.assertEqual(g["online_agents"][0]["peer"], "claude")
        pv2 = JQ.preview(["claude"], extra={"claude": ["s2"]})  # 用户明确加选同组第二个
        c = {x["session_id"]: x for x in pv2["groups"][0]["candidates"]}
        self.assertTrue(c["s2"]["selected"])
        self.assertTrue(c["s2"].get("explicit"))
        self.assertEqual(pv2["groups"][0]["will_send"], 1)
        # 在途作业 → 该会话 skip in_flight
        gate = threading.Event()
        C.join_once = self.fake_join(block=gate)
        JQ.enqueue("gpt", "g1", request_id="if1")
        pv3 = JQ.preview(["gpt"])
        self.assertEqual(pv3["groups"][0]["skip_reason"], "in_flight")
        self.assertEqual(pv3["groups"][0]["candidates"][0]["skip_reason"], "in_flight")
        gate.set()

    def test_confirm_requires_token_and_explicit_selection_then_enqueues_idempotently(self):
        with self.assertRaises(ValueError):
            JQ.confirm("bad-token", "req1", {"claude": ["s1"]})
        pv = JQ.preview(["claude", "gpt"])
        with self.assertRaises(ValueError):
            JQ.confirm(pv["preview_token"], "", {"claude": ["s1"]})
        with self.assertRaises(ValueError):
            JQ.confirm(pv["preview_token"], "req1", {})
        r = JQ.confirm(pv["preview_token"], "req1", {"claude": ["s1", "s3"], "gpt": ["g1"], "nope": ["s1"]})
        rows = {(x["group"], x["session_id"]): x for x in r["items"]}
        self.assertTrue(rows[("claude", "s1")]["job"])
        self.assertTrue(rows[("gpt", "g1")]["job"])
        self.assertIn("不在预览候选", rows[("claude", "s3")]["error"])
        self.assertEqual(rows[("nope", "s1")]["error"], "分组不存在")
        self.assertEqual(r["queued"], 2)
        r2 = JQ.confirm(pv["preview_token"], "req1", {"claude": ["s1"], "gpt": ["g1"]})  # 双击/重放同 request_id
        rows2 = {(x["group"], x["session_id"]): x for x in r2["items"]}
        self.assertEqual(rows2[("claude", "s1")]["job"], rows[("claude", "s1")]["job"])
        self.assertEqual(rows2[("claude", "s1")]["dedup"], "same_request")
        self.assertTrue(_wait(lambda: all(self.job(x["job"])["status"] == "connected" for x in rows.values() if x["job"])))
        self.assertEqual(sorted(self.calls), [("claude", "s1"), ("gpt", "g1")])

    def test_confirm_aborts_after_credential_failure_keeps_earlier(self):
        ST.update("sessions", lambda st: st["sessions"].append({"id": "s9", "account": "dead@example.com", "title": "gpt-5 x", "detected_model": "gpt-5"}))
        pv = JQ.preview(["claude", "gpt"], extra={"gpt": ["s9"]})
        # 绕过预览候选校验以模拟「预览后账号刚失效」：直接把 s9 放进 selectable
        JQ._PREVIEWS[pv["preview_token"]]["selectable"]["gpt"].add("s9")
        r = JQ.confirm(pv["preview_token"], "cred", [{"group": "claude", "session_id": "s1"}, {"group": "gpt", "session_id": "s9"}, {"group": "gpt", "session_id": "g1"}])
        it = r["items"]
        self.assertTrue(it[0]["job"])
        self.assertEqual(it[1]["reason_code"], "no_token")
        self.assertEqual(it[2]["skip"], "aborted")
        self.assertTrue(r["aborted"])
        self.assertTrue(_wait(lambda: self.job(it[0]["job"])["status"] == "connected"))
        self.assertEqual(self.calls, [("claude", "s1")])




class TestReworkE5de(Base):
    """e5de：B1（workbench/peer_hint/idem_key 不落盘不外抛）+ B2（无数字 ak- key、短 token、提示词中段 URL）。
    反向对照：本类前两个用例对 f2b73ce 的旧代码必须失败（旧 enqueue 不拒绝带 path 的 workbench；旧正则放过无数字 ak-）。"""

    BAD_WB = f"http://127.0.0.1:8765/mcp/{SECRET_TOKEN}/mcp/{SECRET_TOKEN}"

    def _store_text(self):
        path = os.path.join(ST.DATA, "join_jobs.json")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_b1_path_token_workbench_rejected_never_persisted_never_echoed(self):
        C.join_once = self.fake_join(registered=True)
        with self.assertRaises(ValueError) as cm:
            JQ.enqueue("claude", "s1", request_id="wb1", workbench=self.BAD_WB)
        self.assertNotIn(SECRET_TOKEN, str(cm.exception))
        self.assertNotIn(SECRET_TOKEN[:12], str(cm.exception))
        self.assertEqual(self.calls, [], "被拒绝的输入不得触发任何发送")
        self.assertNotIn(SECRET_TOKEN, self._store_text())
        self.assertEqual(JQ.jobs_view()["jobs"], [])
        # preview / confirm 两个入口同样拒绝，且不会在内存 _PREVIEWS 留下原串
        with self.assertRaises(ValueError) as cm2:
            JQ.preview(["claude"], workbench=self.BAD_WB)
        self.assertNotIn(SECRET_TOKEN, str(cm2.exception))
        self.assertFalse(any(SECRET_TOKEN in str(v.get("workbench", "")) for v in JQ._PREVIEWS.values()))
        pv = JQ.preview(["claude"])
        with self.assertRaises(ValueError):
            JQ.confirm(pv["preview_token"], "cf1", [{"group": "claude", "session_id": "s1"}], workbench=self.BAD_WB)
        self.assertNotIn(SECRET_TOKEN, self._store_text())
        self.assertEqual(JQ.jobs_view()["jobs"], [])
        # 用户名密码 / query / fragment 形态一并拒绝；合法形态原样保留端口，不被截断
        for bad in ("http://u:p@127.0.0.1:8765", "http://127.0.0.1:8765/?t=1", "http://127.0.0.1:8765/#x", "ftp://127.0.0.1", "http://", ""):
            if bad == "":
                self.assertEqual(JQ.check_workbench(bad), JQ.DEFAULT_WORKBENCH)
                continue
            with self.assertRaises(ValueError):
                JQ.check_workbench(bad)
        self.assertEqual(JQ.check_workbench("https://wb.example.test:8443/"), "https://wb.example.test:8443")

    def test_b2_new_key_shape_without_digits_and_short_tokens_are_redacted(self):
        letters_only = "ak-" + "abcdefghijklmnopqrstuvwxyzABCDEF"          # 32 位随机段、一个数字都没有（旧正则漏网的形态）
        out = C.sanitize_text(f"key {letters_only} rest")
        self.assertNotIn(letters_only, out)
        self.assertNotIn(letters_only[3:15], out)
        # 真实 new_key() 的生成逻辑：ak- + token_urlsafe(24)。只用 secrets，不碰账号组 store，不打印完整值
        import secrets as _secrets
        leaked = 0
        for _ in range(400):
            tok = "ak-" + _secrets.token_urlsafe(24)
            if tok[3:15] in C.sanitize_text(f"x-api-key {tok} tail"):
                leaked += 1
        self.assertEqual(leaked, 0, f"{leaked}/400 把 new_key 形态的 key 漏出（只报计数，不打印值）")
        # 较短的前缀 key 与无前缀的 urlsafe 长串（Bridge/MCP path token 形态）
        for short in ("sk-abcdEFGHijkl", "pk-ZZZZyyyyXXXX", "bt-QwErTyUiOp12"):
            self.assertNotIn(short, C.sanitize_text(f"token {short} end"))
        bare = "QzXwVuTsRqPoNmLkJiHgFeDcBaZyXwVuTs"                        # 34 位、无数字
        self.assertNotIn(bare, C.sanitize_text(f"got {bare} back"))
        # 普通词与短标识不受影响
        s = C.sanitize_text("connected peer claude-opus-4 status 200 wait 5s request r-12")
        self.assertIn("claude-opus-4", s)
        self.assertIn("r-12", s)

    def test_b2_mid_prompt_url_echo_is_redacted_even_without_prefix_match(self):
        # 模型只复述提示词中段（带 token 的 MCP URL 那一段），不触发 prompt[:60] 前缀剔除，仍须由 sanitize 兜住
        mid = f"…然后连接 https://mcp.example.test/mcp/{SECRET_TOKEN}/mcp/{SECRET_TOKEN} 并调用 bus_wait_task…"
        out = C.sanitize_text(mid)
        self.assertNotIn(SECRET_TOKEN, out)
        self.assertNotIn(SECRET_TOKEN[:10], out)
        self.assertIn("https://mcp.example.test/[已脱敏]", out)

    def test_b1_public_shape_whitelisted_and_worker_gets_validated_target(self):
        seen = {}

        def fn(gid, sid, peer, wb, job, set_state=None, wait_rounds=None, wait_step=None):
            seen.update(peer=peer, wb=wb)
            self.calls.append((gid, sid))
            job["payload"]["peer_name"] = "claude"
            set_state("sending", "phase sending")
            return {"peer": "claude", "name_source": "server-alloc", "registered": True, "heartbeat_model": "claude-opus-4",
                    "expected_canon": "claude-opus-4", "first_text": "hi", "reply": "hi", "relay_error": "", "sent_at": ST.now_text()}
        C.join_once = fn
        r = JQ.enqueue("claude", "s1", request_id="shape1", peer="fable5", workbench="HTTP://127.0.0.1:8765/",
                       source={"client": f"ui token={SECRET_TOKEN}", "Bad Key!": "x", "token": SECRET_TOKEN})
        self.assertTrue(_wait(lambda: self.job(r["id"])["status"] == "connected"))
        self.assertEqual(seen["wb"], "http://127.0.0.1:8765", "worker 拿到的是核验后的真实目标，不是打码/截断后的字符串")
        self.assertEqual(seen["peer"], "fable5")
        allowed = set(JQ.PUBLIC_FIELDS) | {"queue", "dedup"}
        for shape in (r, self.job(r["id"]), JQ.jobs_view()["jobs"][0]):
            self.assertLessEqual(set(shape), allowed, f"多出的字段：{set(shape) - allowed}")
            self.assertNotIn("idem_key", shape)
            self.assertNotIn(SECRET_TOKEN, json.dumps(shape, ensure_ascii=False))
        self.assertEqual(self.job(r["id"])["source"], {"client": "ui token=[已脱敏]"})
        self.assertNotIn(SECRET_TOKEN, self._store_text())
        # peer_hint / request_id 自由字段：非法即拒，文案不回显
        for kw in ({"peer": "bad name\n"}, {"peer": "x" * 65}, {"request_id": "has space"}, {"request_id": f"u={SECRET_TOKEN}?"}):
            with self.assertRaises(ValueError) as cm:
                JQ.enqueue("claude", "s2", **kw)
            self.assertNotIn(SECRET_TOKEN, str(cm.exception))

    def test_b1_legacy_persisted_raw_workbench_is_scrubbed_on_first_load(self):
        # 修复前落盘的 job（原始 workbench 带 token）：首次加载即改写持久文件，API 也只看到脱敏形态
        ST.save(JQ.KIND, {**ST.DEFAULTS[JQ.KIND], "jobs": [{
            "id": "legacy1", "kind": "join", "status": "failed", "idem_key": "claude|s1|old", "group": "claude", "session_id": "s1",
            "request_id": "old", "batch": "old", "peer_hint": "", "workbench": self.BAD_WB, "created_at": ST.now_text(), "started_at": "",
            "updated_at": ST.now_text(), "finished_at": ST.now_text(), "progress": 0, "total": 3, "message": "x", "result": None,
            "error": "", "reason_code": "x", "retryable": False, "payload": {}, "binding": None, "cancel_requested": False, "cancel_note": "", "source": {}}]})
        JQ._reset_for_tests()
        view = JQ.jobs_view()
        self.assertNotIn(SECRET_TOKEN, json.dumps(view, ensure_ascii=False))
        self.assertNotIn(SECRET_TOKEN, self._store_text())
        # 0ff2 返修：_redact_url 只去掉 userinfo 与 path/query，端口保留（本机 8765/8791/9091 靠端口区分）
        self.assertEqual(view["jobs"][0]["workbench"], "http://127.0.0.1:8765/[已脱敏]")


if __name__ == "__main__":
    unittest.main()
