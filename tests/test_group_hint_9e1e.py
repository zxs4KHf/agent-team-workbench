"""group_hint 后端一期（20261001-090838-9e1e）：状态机、四级优先级、归一化、建议/探针、并发与旧文件兼容。"""
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bus_core as B  # noqa: E402

GROUPS = [
    {"id": "glm", "match": ["glm"]},
    {"id": "glm-5.3", "match": ["glm-5.3"]},
    {"id": "deepseek", "match": ["deepseek"]},
    {"id": "deepseek-flash", "match": ["deepseek-flash"]},
    {"id": "qwen", "match": ["qwen"]},
    {"id": "arena-agent", "match": ["arena-agent"]},
    {"id": "workbuddy", "match": ["workbuddy"]},
]


class HintBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bus-test-9e1e-")
        old = B.PEERS
        B.PEERS = self.tmp
        self.addCleanup(setattr, B, "PEERS", old)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patcher = mock.patch.object(B, "model_groups", return_value=GROUPS)
        patcher.start()
        self.addCleanup(patcher.stop)
        # 读取路径（peer_state/list_peers）只看本地缓存（7755 P2-3）：把组表灌进缓存模拟生产态，
        # 旧值留在 cleanup 里恢复；「缓存为空也不发请求」由 TestReadPathNoArena 单独断言
        self.old_cache = dict(B._GROUPS_CACHE)
        B._GROUPS_CACHE["groups"] = [dict(g) for g in GROUPS]
        self.addCleanup(B._GROUPS_CACHE.update, self.old_cache)
        B.ensure_dirs()

    def peer(self, name, model="", **extra):
        data = {"peer": name, "status": "idle", "activity": "idle", "last_seen": B.now_iso(), "model": model}
        data.update(extra)
        B.write_json(os.path.join(self.tmp, f"{name}.json"), data)
        return data

    def path(self, name):
        return os.path.join(self.tmp, f"{name}.json")

    def set_model(self, name, model):
        """模拟心跳改模型：读改写只动 model 字段，其余（含 group_hint）原样保留。"""
        p = self.path(name)
        data = B.read_json(p, {}) or {}
        data["model"] = model
        B.write_json(p, data)


class TestPriority(HintBase):
    """② 四级优先级：显式 group > hint(confirmed 或 conf≥0.8 且非 rejected) > group_of_model > 空。"""

    def test_chain_from_empty_to_explicit(self):
        self.peer("p1", model="")                       # 什么都没有 → 空串
        self.assertEqual(B.peer_group("p1"), "")
        B.set_group_hint("p1", {"group": "glm", "source": "manual", "confidence": 1.0, "state": "confirmed"})
        self.assertEqual(B.peer_group("p1"), "glm")     # hint 生效
        data = self.peer("p1", model="", group="deepseek")
        data.update({"group": "deepseek"})
        B.write_json(self.path("p1"), data)
        self.assertEqual(B.peer_group("p1"), "deepseek")  # 显式 group 最优先

    def test_low_confidence_hint_not_effective(self):
        self.peer("p2", model="")                       # 模型推断也无果
        B.set_group_hint("p2", {"group": "qwen", "source": "normalize", "confidence": 0.4, "state": "auto"})
        self.assertEqual(B.peer_group("p2"), "")        # 0.4 不生效
        B.set_group_hint("p2", {"confidence": 0.9})
        self.assertEqual(B.peer_group("p2"), "qwen")    # ≥0.8 生效

    def test_rejected_hint_not_effective(self):
        self.peer("p3", model="mystery-model")
        B.set_group_hint("p3", {"group": "glm", "source": "probe", "confidence": 0.9, "state": "auto"})
        self.assertEqual(B.peer_group("p3"), "glm")
        B.reject_group("p3", "glm")
        self.assertEqual(B.peer_group("p3"), "")        # rejected 不生效，模型推断也无果


class TestNormalize(HintBase):
    """③ 归一化器与逐层匹配。"""

    def test_normalize_five_samples(self):
        cases = {"workbuddy1": "workbuddy1", "workbuddy2": "workbuddy2", "ark-deepseek": "ark-deepseek",
                 "arena-agent": "arena-agent", "Arena.ai agent": "arena.ai-agent"}
        for raw, want in cases.items():
            self.assertEqual(B._normalize_model_key(raw), want, raw)
        self.assertEqual(B._normalize_model_key("GLM/v4.1 Flash"), "4.1-flash")

    def test_longest_prefix_wins(self):
        self.peer("p1", model="glm-5.3-flash")
        self.assertEqual(B.peer_group("p1"), "glm-5.3")   # 最具体的层命中，不落回 glm
        self.peer("p2", model="deepseek-flash")
        self.assertEqual(B.peer_group("p2"), "deepseek-flash")  # 精确组优先于家族组
        self.peer("p3", model="glm-4.1-thinking-preview")
        self.assertEqual(B.peer_group("p3"), "glm")       # 逐层剥 preview/thinking 后命中边界

    def test_family_suggestion_not_effective(self):
        self.peer("p4", model="qwen3-max")
        sug = B.suggest_group("p4")
        self.assertEqual((sug["candidate"], sug["confidence"]), ("qwen", 0.4))
        self.assertEqual(B.get_group_hint("p4"), {})      # 0.4 不落盘
        self.assertEqual(B.peer_group("p4"), "")          # 也不生效

    def test_unmatchable_model_reports_unclassified(self):
        self.peer("p5", model="workbuddy1")
        sug = B.suggest_group("p5")
        # 数字后缀越过边界（workbuddy1 不满足 workbuddy- 边界）→ 只到家族词 0.4，不落盘不生效
        self.assertEqual((sug["candidate"], sug["confidence"]), ("workbuddy", 0.4))
        self.assertEqual(B.get_group_hint("p5"), {})
        self.assertEqual(B.peer_group("p5"), "")
        self.peer("p6", model="totally-unknown-thing")    # 连家族词都没有
        sug6 = B.suggest_group("p6")
        self.assertEqual(sug6["candidate"], "")
        self.assertEqual(B.get_group_hint("p6"), {})


class TestStateMutation(HintBase):
    """⑤ rejected 记忆、manual 覆盖 rejected、未知组拒绝。"""

    def test_reject_remembered_against_auto_suggest(self):
        self.peer("p1", model="mystery-model")
        B.probe_group("p1", "s1", adapter=lambda peer, sid: "glm")
        self.assertEqual(B.peer_group("p1"), "glm")
        B.reject_group("p1", "glm")
        hint = B.get_group_hint("p1")
        self.assertEqual(hint["state"], "rejected")
        self.assertIn("glm", hint["rejected"])
        self.assertEqual(B.peer_group("p1"), "")
        out = B.probe_group("p1", "s1", adapter=lambda peer, sid: "glm")
        self.assertFalse(out["ok"])                       # probe 不得再自动给已拒绝候选
        self.assertIn("已被拒绝", out["error"])
        sug = B.suggest_group("p1")
        self.assertEqual(sug["candidate"], "")

    def test_manual_overrides_rejected(self):
        self.peer("p2", model="mystery-model")
        B.reject_group("p2", "glm")
        B.manual_group("p2", "glm")
        hint = B.get_group_hint("p2")
        self.assertEqual((hint["state"], hint["source"], hint["confidence"]), ("confirmed", "manual", 1.0))
        self.assertNotIn("glm", hint["rejected"])
        self.assertEqual(B.peer_group("p2"), "glm")

    def test_unknown_group_only_suggested(self):
        self.peer("p3", model="mystery-model")
        with self.assertRaises(ValueError):
            B.confirm_group("p3", "no-such-group")
        with self.assertRaises(ValueError):
            B.manual_group("p3", "no-such-group")
        with self.assertRaises(ValueError):
            B.reject_group("p3", "no-such-group")
        self.assertEqual(B.get_group_hint("p3"), {})


class TestDispatchChain(HintBase):
    """②/⑧ 未分类仍可派发：relay 替补链、substitute_offline 不崩。"""

    def test_unclassified_peer_survives_relay_chain(self):
        self.peer("mystery1", model="workbuddy1")         # 无显式组、无 hint、模型无匹配
        pr = {"relay_policy": dict(B.RELAY_POLICY_DEFAULT), "relay_order": ["glm", "deepseek"]}
        with mock.patch.object(B, "priority", return_value=pr):
            r = B.relay_resolution()
        self.assertTrue(all(it["kind"] == "unknown" for it in r["items"]))  # 组解析不到但不崩
        self.assertEqual(r["pick"], "mystery1")           # 未分类者进兜底名单，仍可派发
        with mock.patch.object(B, "priority", return_value=pr):
            self.assertEqual(B.substitute_offline(["mystery1"]), {})        # 无组不替补、不抛

    def test_hint_flows_into_relay_resolution(self):
        self.peer("hinted", model="workbuddy1", group_hint={"group": "glm", "source": "manual", "confidence": 1.0, "state": "confirmed"})
        pr = {"relay_policy": dict(B.RELAY_POLICY_DEFAULT), "relay_order": ["glm"]}
        with mock.patch.object(B, "priority", return_value=pr):
            r = B.relay_resolution()
        glm = next(it for it in r["items"] if it["item"] == "glm")
        self.assertEqual(glm["kind"], "group")
        self.assertEqual(glm["pick"], "hinted")           # peer_group 改造后 hint 自然生效


class TestProbe(HintBase):
    """⑥ probe 最多 2 次、失败不泄密、未接线如实上报。"""

    def test_probe_caps_at_two_attempts_without_leak(self):
        self.peer("p1", model="mystery-model")

        def boom(peer, sid):
            raise RuntimeError("token=sk-secret-abc password=hunter2")

        out = B.probe_group("p1", "s1", adapter=boom)
        self.assertFalse(out["ok"])
        self.assertEqual(out["attempts"], 2)
        self.assertNotIn("sk-secret", out["error"])
        self.assertNotIn("hunter2", out["error"])
        self.assertNotIn("sk-secret", B.get_group_hint("p1").get("probe", {}).get("error", ""))

        def empty(peer, sid):
            return ""

        out2 = B.probe_group("p1", "s1", adapter=empty)
        self.assertEqual((out2["ok"], out2["attempts"], out2["error"]), (False, 2, "probe 无结果"))
        sug = B.suggest_group("p1")
        self.assertEqual(sug["candidate"], "")            # probe 失败不产生 hint

    def test_probe_success_writes_auto_hint(self):
        self.peer("p2", model="mystery-model")
        calls = []

        def adapter(peer, sid):
            calls.append(sid)
            return "deepseek"

        out = B.probe_group("p2", "sess-7", adapter=adapter)
        self.assertEqual(out, {"ok": True, "candidate": "deepseek", "attempts": 1, "error": ""})
        self.assertEqual(calls, ["sess-7"])               # session_id 原样传给 adapter
        hint = B.get_group_hint("p2")
        self.assertEqual((hint["group"], hint["source"], hint["state"]), ("deepseek", "probe", "auto"))
        self.assertEqual(B.peer_group("p2"), "deepseek")

    def test_probe_not_wired_reports_honestly(self):
        self.peer("p3", model="mystery-model")
        out = B.probe_group("p3", "s1")
        self.assertFalse(out["ok"])
        self.assertIn("未接线", out["error"])
        self.assertEqual(out["attempts"], 1)


class TestConcurrency(HintBase):
    """①⑧ 并发写不丢 heartbeat、未知字段保留。"""

    def test_concurrent_hint_and_heartbeat(self):
        self.peer("p1", model="m1")
        B.set_group_hint("p1", {"group": "glm", "source": "manual", "state": "confirmed"})

        def beat():
            for i in range(40):
                B.heartbeat("p1", "think", f"m{i}", "t1")

        t = threading.Thread(target=beat)
        t.start()
        for i in range(40):
            B.set_group_hint("p1", {"confidence": 0.9, "note_seq": i})
        t.join()
        data = B.read_json(self.path("p1"), {})
        self.assertTrue(data.get("last_seen"))            # 心跳没丢
        self.assertEqual(data.get("activity"), "think")
        self.assertEqual(data["group_hint"]["group"], "glm")   # 先前写入的 hint 没被心跳覆盖
        self.assertEqual(data["group_hint"]["state"], "confirmed")
        self.assertIn("note_seq", data["group_hint"])     # 并发期间写的字段仍在

    def test_unknown_fields_preserved(self):
        self.peer("p2", model="m2", custom_top={"a": 1})
        B.set_group_hint("p2", {"group": "glm", "custom_hint": "keep-me"})
        B.set_group_hint("p2", {"state": "confirmed"})
        data = B.read_json(self.path("p2"), {})
        self.assertEqual(data["custom_top"], {"a": 1})            # 顶层未知字段保留
        self.assertEqual(data["group_hint"]["custom_hint"], "keep-me")  # hint 内未知字段保留
        self.assertEqual(data["group_hint"]["group"], "glm")
        self.assertEqual(data["model"], "m2")                      # 心跳/模型字段未被触碰


class TestExposure(HintBase):
    """⑦ peer_state 净化暴露 + 旧文件兼容。"""

    def test_peer_state_sanitized_exposure(self):
        self.peer("p1", model="mystery-model")
        B.probe_group("p1", "s1", adapter=lambda peer, sid: "glm")
        st = B.peer_state("p1")
        self.assertEqual(st["effective_group"], "glm")
        self.assertEqual(st["classification_state"], "auto")
        self.assertEqual(set(st["group_hint"]), {"group", "source", "confidence", "state", "at"})  # probe 簿记被剥掉
        self.assertNotIn("probe", st["group_hint"])

    def test_legacy_file_without_hint(self):
        self.peer("p2", model="glm-5.3-flash")            # 旧文件：无 group_hint、无显式 group
        st = B.peer_state("p2")
        self.assertEqual(st["effective_group"], "glm-5.3")
        self.assertEqual(st["classification_state"], "unclassified")
        self.assertEqual(st["group_hint"], {})
        self.assertEqual(B.peer_group("p2"), "glm-5.3")

    def test_partial_hint_tolerated(self):
        self.peer("p3", model="glm-5.3-flash", group_hint={"group": "qwen"})  # 缺 state/confidence 的半截 hint
        st = B.peer_state("p3")
        self.assertEqual(st["effective_group"], "glm-5.3")   # hint 不完整 → 不生效，回退模型推断
        self.assertEqual(st["classification_state"], "unclassified")


class TestGhostPeer(HintBase):
    """68e6 审查阻断项：写入口拒绝为不存在的 peer 建档；读出口对残缺文件兜底，顶替链不崩。"""

    def test_write_apis_reject_missing_peer_and_create_no_file(self):
        for call in (
            lambda: B.manual_group("gh0st", "glm"),
            lambda: B.confirm_group("gh0st", "glm"),
            lambda: B.reject_group("gh0st", "glm"),
            lambda: B.set_group_hint("gh0st", {"group": "glm"}),
        ):
            with self.assertRaises(ValueError):
                call()
            self.assertFalse(os.path.exists(self.path("gh0st")))   # 不许凭空造残缺 peers 文件

    def test_partial_peer_file_does_not_break_relay(self):
        B.write_json(self.path("ghosty"), {"group_hint": {"group": "glm", "state": "confirmed", "confidence": 1.0}})
        self.peer("real1", model="glm-5.3")                        # 有一个正常 peer
        st = B.peer_state("ghosty")
        self.assertEqual(st["peer"], "ghosty")                     # 读出口兜底补键
        peers = B.list_peers()
        self.assertIn("ghosty", [p["peer"] for p in peers])        # list_peers 不抛异常
        out = B.relay_resolution()                                 # 顶替链（p["peer"] 直接下标）不得 KeyError
        self.assertIsInstance(out, dict)


class TestSota3Rework(HintBase):
    """sota3（7755）RETURN 项：路径穿越、confirmed 不被覆盖、读取零网络、模型指纹、ark-deepseek。"""

    def test_escaped_path_traversal_rejected(self):
        before = sorted(os.listdir(self.tmp))
        for call in (
            lambda: B.manual_group("../escaped", "glm"),
            lambda: B.confirm_group("../escaped", "glm"),
            lambda: B.reject_group("../escaped", "glm"),
            lambda: B.probe_group("../escaped", "s1", adapter=lambda p, s: "glm"),
        ):
            with self.assertRaises(ValueError):
                call()                                             # valid_name 在锁外拦截，不进锁不建 .mutex
        after = sorted(os.listdir(self.tmp))
        self.assertEqual(before, after)                            # 目录无新文件（无 .json 也无 .mutex）

    def test_non_dict_peer_file_tolerated(self):
        with open(self.path("odd"), "w", encoding="utf-8") as f:
            f.write('["not", "a", "dict"]')
        st = B.peer_state("odd")                                   # 文件根不是 dict → 与缺失同待遇，不崩
        self.assertFalse(st["online"])
        self.assertEqual(st["peer"], "odd")
        self.assertIn("odd", [p["peer"] for p in B.list_peers()])

    def test_suggest_and_probe_never_override_confirmed(self):
        self.peer("p1", model="deepseek-v3.1-thinking")            # 模型会命中 deepseek 前缀
        B.manual_group("p1", "glm")                                # 人工确认 glm
        sug = B.suggest_group("p1")                                # suggest 候选是 deepseek ≠ glm
        self.assertEqual(sug["candidate"], "deepseek")
        self.assertIn("人工确认在效", sug["reason"])
        out = B.probe_group("p1", "s1", adapter=lambda p, s: "deepseek")
        self.assertFalse(out["ok"])
        self.assertIn("人工确认", out["error"])
        hint = B.get_group_hint("p1")
        self.assertEqual((hint["group"], hint["state"], hint["source"]), ("glm", "confirmed", "manual"))
        self.assertEqual(B.peer_group("p1"), "glm")                # 人工确认原样生效

    def test_read_path_never_touches_arena(self):
        self.peer("p1", model="glm-5.3-flash")
        B._GROUPS_CACHE["groups"] = [dict(g) for g in GROUPS]      # 缓存有组表
        B._GROUPS_CACHE["at"] = 0.0                                # 缓存已「过期」：读取路径也不得拉取
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("读取路径不得访问 Arena")) as ur:
            st = B.peer_state("p1")
            B.list_peers()
            B.get_group_hint("p1")
            B._hint_effective_group({"group_hint": {"group": "glm", "state": "confirmed", "confidence": 1.0}})
        ur.assert_not_called()
        self.assertEqual(st["effective_group"], "glm-5.3")         # 缓存照样把组归出来

    def test_auto_hint_binds_model_fingerprint(self):
        self.peer("p1", model="mystery-model")
        B.probe_group("p1", "s1", adapter=lambda p, s: "glm")
        self.assertEqual(B.peer_group("p1"), "glm")                # 模型未变：probe hint 生效
        self.set_model("p1", "another-model")                      # 模型变了（心跳式只改 model）
        self.assertEqual(B.peer_group("p1"), "")                   # 自动 hint 随旧模型失效
        self.peer("p2", model="glm-5.3-flash")
        B.suggest_group("p2")                                      # normalize 落盘，同样绑指纹
        self.set_model("p2", "changed-model")
        self.assertEqual(B.peer_group("p2"), "")
        self.peer("p3", model="mystery-model")
        B.manual_group("p3", "glm")                                # manual confirmed 不受指纹约束
        self.set_model("p3", "changed-model")
        self.assertEqual(B.peer_group("p3"), "glm")

    def test_ark_deepseek_family_suggestion(self):
        self.peer("p1", model="ark-deepseek")
        sug = B.suggest_group("p1")
        self.assertEqual((sug["candidate"], sug["confidence"]), ("deepseek", 0.4))  # 分段家族词命中
        self.assertEqual(B.get_group_hint("p1"), {})               # 0.4 只提示不落盘
        self.assertEqual(B.peer_group("p1"), "")


if __name__ == "__main__":
    unittest.main()
