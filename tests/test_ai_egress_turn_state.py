# -*- coding: utf-8 -*-
"""任务 69f3：tools/ai_egress/turn_state.py 的单元测试。

不读本机凭据 / 配置，不发网络，不写任何工作区文件（只用内存与临时目录）。
跑法（仓库根目录）：py -3 -m unittest tests.test_ai_egress_turn_state -v
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.ai_egress.turn_state import (  # noqa: E402
    HEADER_NAME,
    CODEX_UPSTREAM,
    HttpTurnStateAdapter,
    TurnStateConfig,
    TurnStateStore,
    WsTurnStateAdapter,
    length_bucket,
    scrub,
)

A, T = "auth-1", "turn-1"
V = "opaque-state-value"


def on_store(**kw):
    return TurnStateStore(TurnStateConfig(enabled=True, **kw))


class TestLifecycle(unittest.TestCase):
    def test_capture_then_replay_same_turn(self):
        s = on_store()
        self.assertTrue(s.capture(A, T, V))
        # sampling / tool continuation / inline compaction 都是同一个 turn，回放同一个值
        for _ in range(3):
            self.assertEqual(s.replay(A, T), V)

    def test_first_write_wins(self):
        s = on_store()
        self.assertTrue(s.capture(A, T, V))
        self.assertFalse(s.capture(A, T, "second-value"))
        self.assertEqual(s.replay(A, T), V)

    def test_new_turn_starts_empty(self):
        s = on_store()
        s.capture(A, T, V)
        self.assertIsNone(s.replay(A, "turn-2"))
        s.begin_turn(A, "turn-2")
        self.assertIsNone(s.replay(A, "turn-2"))
        self.assertEqual(s.replay(A, T), V, "开新 turn 不影响旧 turn 自己的回放")

    def test_end_turn_clears(self):
        s = on_store()
        s.capture(A, T, V)
        s.end_turn(A, T)
        self.assertIsNone(s.replay(A, T))
        self.assertEqual(s.size(), 0)

    def test_auth_switch_clears_old_auth(self):
        s = on_store()
        s.switch_auth(A)
        s.capture(A, T, V)
        s.switch_auth("auth-2")
        self.assertIsNone(s.replay(A, T), "切 auth 后旧 auth 的状态必须消失")
        self.assertEqual(s.size(), 0)

    def test_fresh_process_instance_is_empty(self):
        """进程重启 = 新实例；状态只在内存，不可能从别处恢复。"""
        s = on_store()
        s.capture(A, T, V)
        self.assertIsNone(on_store().replay(A, T))

    def test_fresh_interpreter_is_empty(self):
        code = (
            "import sys;sys.path.insert(0, %r);"
            "from tools.ai_egress.turn_state import TurnStateStore, TurnStateConfig;"
            "s=TurnStateStore(TurnStateConfig(enabled=True));"
            "print(s.replay('auth-1','turn-1'), s.size())"
        ) % os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "None 0")


class TestValidation(unittest.TestCase):
    def test_missing_ids_never_cached(self):
        s = on_store()
        for auth, turn in [(None, T), (A, None), ("", T), (A, "  "), (None, None)]:
            self.assertFalse(s.capture(auth, turn, V))
            self.assertIsNone(s.replay(auth, turn))
        self.assertEqual(s.size(), 0)

    def test_empty_blank_malformed_oversize_ignored(self):
        s = on_store(max_len=32)
        for bad in ["", "   ", "\t\n", None, 123, {"x": 1}, "has\nnewline", "ctl\x00char", "x" * 33]:
            self.assertFalse(s.capture(A, T, bad), repr(bad))
        self.assertIsNone(s.replay(A, T))
        self.assertTrue(s.capture(A, T, "ok"), "忽略坏值之后仍能接受好值，说明没有中断流程")

    def test_bad_response_does_not_raise(self):
        s = on_store()
        h = HttpTurnStateAdapter(s)
        for headers in [None, {}, {HEADER_NAME: ""}, {HEADER_NAME: None}, {"other": V}]:
            self.assertFalse(h.observe_response(A, T, headers))
        self.assertEqual(h.request_headers(A, T), {}, "没有状态就不附加头")

    def test_length_never_classifies(self):
        """反向测试：312 与 780 不得产生任何不同分类。"""
        s = on_store(max_len=4096)
        v312, v780 = "a" * 312, "b" * 780
        self.assertEqual(length_bucket(len(v312)), length_bucket(len(v780)))
        s.capture(A, "t-312", v312)
        s.capture(A, "t-780", v780)
        f1 = s.log_fields(A, "t-312", with_digest=True)
        f2 = s.log_fields(A, "t-780", with_digest=True)
        self.assertEqual(f1["turn_state_present"], f2["turn_state_present"])
        self.assertEqual(f1["turn_state_len_bucket"], f2["turn_state_len_bucket"])
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "ai_egress", "turn_state.py"), encoding="utf-8").read()
        for n in ("312", "292", "332", "780"):
            self.assertNotIn(n, src, f"源码里不得出现长度白名单 {n}")


class TestIsolation(unittest.TestCase):
    def test_cross_turn_injection_fails(self):
        s = on_store()
        s.capture(A, T, V)
        self.assertIsNone(s.replay(A, "turn-2"), "跨轮读取必须拿不到")
        self.assertEqual(HttpTurnStateAdapter(s).request_headers(A, "turn-2"), {})
        ev = WsTurnStateAdapter(s).decorate_response_create({"type": "response.create", "response": {}}, A, "turn-2")
        self.assertNotIn("client_metadata", ev["response"])

    def test_cross_auth_injection_fails(self):
        s = on_store()
        s.capture(A, T, V)
        self.assertIsNone(s.replay("auth-2", T), "跨 auth 读取必须拿不到")
        self.assertEqual(HttpTurnStateAdapter(s).request_headers("auth-2", T), {})

    def test_concurrent_first_write(self):
        s = on_store()
        vals = [f"value-{i}" for i in range(24)]
        winners = []
        start = threading.Barrier(len(vals))

        def run(v):
            start.wait()
            if s.capture(A, T, v):
                winners.append(v)

        ts = [threading.Thread(target=run, args=(v,)) for v in vals]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(winners), 1, "并发下只能有一个 first write")
        self.assertEqual(s.replay(A, T), winners[0])


class TestAdapters(unittest.TestCase):
    def test_http_positions(self):
        s = on_store()
        h = HttpTurnStateAdapter(s)
        self.assertTrue(h.observe_response(A, T, {"X-Codex-Turn-State": V, "content-type": "application/json"}))
        self.assertEqual(h.request_headers(A, T), {HEADER_NAME: V})
        self.assertFalse(h.observe_response(A, T, {HEADER_NAME: "later"}))
        self.assertEqual(h.request_headers(A, T)[HEADER_NAME], V)

    def test_ws_positions(self):
        s = on_store()
        w = WsTurnStateAdapter(s)
        self.assertFalse(w.observe_event({"type": "response.created", "metadata": {HEADER_NAME: V}}, A, T), "只认 response.metadata")
        self.assertTrue(w.observe_event({"type": "response.metadata", "metadata": {HEADER_NAME: V}}, A, T))
        ev = w.decorate_response_create({"type": "response.create", "response": {"modalities": ["text"]}}, A, T)
        self.assertEqual(ev["response"]["client_metadata"][HEADER_NAME], V)
        self.assertEqual(ev["response"]["modalities"], ["text"])
        other = w.decorate_response_create({"type": "session.update", "response": {}}, A, T)
        self.assertNotIn("client_metadata", other.get("response", {}))

    def test_flag_off_and_non_codex(self):
        off = TurnStateStore(TurnStateConfig())  # 默认 off
        self.assertFalse(off.enabled, "feature flag 默认必须是 off")
        self.assertFalse(off.capture(A, T, V))
        self.assertIsNone(off.replay(A, T))
        self.assertEqual(HttpTurnStateAdapter(off).request_headers(A, T), {})
        s = on_store()
        s.capture(A, T, V)
        self.assertEqual(HttpTurnStateAdapter(s).request_headers(A, T, upstream="anthropic"), {}, "非 Codex 上游不发送")
        self.assertFalse(HttpTurnStateAdapter(s).observe_response(A, T, {HEADER_NAME: "x"}, upstream="anthropic"))
        ev = WsTurnStateAdapter(s).decorate_response_create({"type": "response.create", "response": {}}, A, T, upstream="gemini")
        self.assertNotIn("client_metadata", ev["response"])


class TestNoLeak(unittest.TestCase):
    def test_log_fields_have_no_value(self):
        s = on_store()
        s.capture(A, T, V)
        f = s.log_fields(A, T)
        self.assertEqual(f, {"turn_state_present": True}, "默认日志字段只有 present")
        d = s.log_fields(A, T, with_digest=True)
        self.assertNotIn(V, json.dumps(d))
        self.assertNotIn(str(len(V)), json.dumps(d), "连精确长度都不输出")

    def test_scrub_and_no_persistence(self):
        s = on_store()
        s.capture(A, T, V)
        self.assertNotIn(V, scrub(f"upstream failed with {V}", [V]))
        # 模块不写任何文件：在空临时目录里跑完整流程，目录必须仍为空
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            try:
                os.chdir(d)
                s2 = on_store()
                h = HttpTurnStateAdapter(s2)
                h.observe_response(A, T, {HEADER_NAME: V})
                h.request_headers(A, T)
                s2.log_fields(A, T, with_digest=True)
                s2.end_turn(A, T)
            finally:
                os.chdir(cwd)
            self.assertEqual(os.listdir(d), [], "运行过程不得落任何盘")

    def test_source_has_no_persistence_calls(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "ai_egress", "turn_state.py"), encoding="utf-8").read()
        for bad in ("open(", "logging", "requests", "httpx", "socket", "xlsx", "openpyxl", "json.dump", "os.environ"):
            self.assertNotIn(bad, src, f"模块不得出现 {bad}（不落盘 / 不记日志 / 不发网络 / 不读配置）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
