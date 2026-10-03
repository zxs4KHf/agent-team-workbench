# 2c14 交接事件测试：五种 reason、epoch 去重、持久化、幂等 ack、并发不丢、秘密不出库、未 ack 不因上限丢失。
# 跑法：py -3 -X utf8 tests/test_lead_events_2c14.py（仓库根目录）。状态全建在临时目录。
import json, os, shutil, sys, tempfile, threading, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_lead_notify as LN


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ev2c14-")
        os.makedirs(os.path.join(self.root, "console"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def raw(self):
        p = LN.events_path(self.root)
        if not os.path.isfile(p):
            return []
        with open(p, encoding="utf-8") as f:
            return json.load(f)["events"]


class TestRecord(Base):
    def test_all_five_reasons(self):
        for i, r in enumerate(LN.REASONS):
            LN.record_lead_event(self.root, "controller", "glm1", i + 1, r, f"{r} 说明")
        self.assertEqual(len(self.raw()), 5)
        self.assertEqual({e["reason"] for e in self.raw()}, set(LN.REASONS))

    def test_unknown_reason_rejected(self):
        with self.assertRaises(ValueError):
            LN.record_lead_event(self.root, "a", "b", 1, "whatever")

    def test_dedup_by_from_to_reason_epoch(self):
        LN.record_lead_event(self.root, "controller", "glm1", 7, "takeover", "x", now="2026-09-24T01:00:00")
        LN.record_lead_event(self.root, "controller", "glm1", 7, "takeover", "x", now="2026-09-24T01:00:30")
        evs = self.raw()
        self.assertEqual(len(evs), 1, "同 epoch 重复只合并成一条")
        self.assertEqual(evs[0]["count"], 1, "3e32：轮询重复上报不累计交接次数，count 只表示真实次数")
        self.assertEqual(evs[0]["seen"], 2, "重复上报只记 seen")
        self.assertEqual(evs[0]["at"], "2026-09-24T01:00:30", "at 应刷新")
        self.assertEqual(evs[0]["first_at"], "2026-09-24T01:00:00", "first_at 应保留")

    def test_different_epoch_is_a_new_event(self):
        LN.record_lead_event(self.root, "controller", "glm1", 7, "takeover")
        LN.record_lead_event(self.root, "controller", "glm1", 8, "takeover")
        self.assertEqual(len(self.raw()), 2)

    def test_non_int_epoch_normalized(self):
        e = LN.record_lead_event(self.root, "a", "b", None, "handback")
        self.assertEqual(e["epoch"], 0)
        e2 = LN.record_lead_event(self.root, "a", "b", "bad", "handback")
        self.assertEqual(e2["epoch"], 0)
        self.assertEqual(len(self.raw()), 1, "None 与 'bad' 都归一到 0，应命中同一条")

    def test_persists_across_reload(self):
        LN.record_lead_event(self.root, "controller", "glm1", 7, "takeover")
        self.assertEqual(len(LN.lead_events_open(self.root)), 1)
        self.assertEqual(len(LN.lead_events_open(self.root)), 1, "重复读取不应改变状态")

    def test_missing_file_and_broken_shapes(self):
        self.assertEqual(LN.lead_events_open(self.root), [])
        p = LN.events_path(self.root)
        for bad in ("not json", "[]", '{"events": "oops"}', '{"events": [1, 2, null]}', "{}"):
            with open(p, "w", encoding="utf-8") as f:
                f.write(bad)
            self.assertEqual(LN.lead_events_open(self.root), [], f"坏形状应降级为空：{bad}")
            LN.record_lead_event(self.root, "a", "b", 1, "takeover")
            self.assertEqual(len(LN.lead_events_open(self.root)), 1)


class TestSecrets(Base):
    def test_prompt_never_stores_credentials(self):
        dirty = ("连接 https://mcp.example.com/mcp/W28JpcsdKybTE6jqKMmU8Cod0iy5PKsx2sz4gCalL \n"
                 "请求头 Authorization: Bearer abcdef0123456789abcdef0123456789\n"
                 "另有 sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 与 deadbeefdeadbeefdeadbeefdeadbeef")
        LN.record_lead_event(self.root, "controller", "glm1", 7, "takeover", dirty)
        blob = open(LN.events_path(self.root), encoding="utf-8").read()
        for secret in ("Bearer abcdef", "W28Jpcsd", "sk-proj-ABCDEF", "deadbeefdeadbeef"):
            self.assertNotIn(secret, blob, f"落盘文件含凭据：{secret}")
        self.assertIn("<redacted>", blob)
        self.assertGreaterEqual(self.raw()[0]["redacted"], 4)

    def test_clean_prompt_untouched(self):
        clean = "RELAY_TAKEOVER: Leader已离线超过 200 秒，你（glm1）现在是代理 Leader（epoch 7）。"
        LN.record_lead_event(self.root, "controller", "glm1", 7, "takeover", clean)
        self.assertEqual(self.raw()[0]["prompt"], clean)
        self.assertEqual(self.raw()[0]["redacted"], 0)

    def test_has_secret_helper(self):
        self.assertTrue(LN.has_secret("Authorization: Bearer xyz123456"))
        self.assertFalse(LN.has_secret("普通中文说明，没有凭据"))


class TestAck(Base):
    def test_ack_one_then_open_empty(self):
        e = LN.record_lead_event(self.root, "controller", "glm1", 7, "takeover")
        self.assertEqual(LN.ack_lead_event(self.root, e["id"]), 1)
        self.assertEqual(LN.lead_events_open(self.root), [])

    def test_ack_is_idempotent(self):
        e = LN.record_lead_event(self.root, "controller", "glm1", 7, "takeover")
        self.assertEqual(LN.ack_lead_event(self.root, e["id"]), 1)
        first_at = self.raw()[0]["acked_at"]
        self.assertEqual(LN.ack_lead_event(self.root, e["id"]), 0, "重复 ack 应返回 0 且不报错")
        self.assertEqual(self.raw()[0]["acked_at"], first_at, "acked_at 不应被二次 ack 改写")

    def test_ack_unknown_id_is_noop(self):
        LN.record_lead_event(self.root, "controller", "glm1", 7, "takeover")
        self.assertEqual(LN.ack_lead_event(self.root, "no-such-id"), 0)
        self.assertEqual(len(LN.lead_events_open(self.root)), 1)

    def test_ack_all(self):
        for i in range(5):
            LN.record_lead_event(self.root, "controller", "glm1", i, "takeover")
        self.assertEqual(LN.ack_lead_event(self.root, all_events=True), 5)
        self.assertEqual(LN.lead_events_open(self.root), [])
        self.assertEqual(LN.ack_lead_event(self.root, all_events=True), 0, "全部 ack 后再来一次是幂等的")

    def test_concurrent_record_and_ack_lose_nothing(self):
        errs = []
        N = 30

        def rec():
            try:
                for i in range(N):
                    LN.record_lead_event(self.root, "controller", "glm1", i, "takeover")
            except Exception as ex:  # noqa: BLE001
                errs.append(ex)

        def ack():
            try:
                for _ in range(N):
                    LN.ack_lead_event(self.root, all_events=True)
            except Exception as ex:  # noqa: BLE001
                errs.append(ex)

        ts = [threading.Thread(target=rec), threading.Thread(target=ack), threading.Thread(target=ack)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [])
        epochs = {int(e["epoch"]) for e in self.raw()}
        self.assertEqual(epochs, set(range(N)), f"并发下丢了事件：缺 {set(range(N)) - epochs}")

    def test_concurrent_record_same_key_counts_exactly(self):
        errs = []

        def rec():
            try:
                for _ in range(25):
                    LN.record_lead_event(self.root, "controller", "glm1", 7, "takeover")
            except Exception as ex:  # noqa: BLE001
                errs.append(ex)

        ts = [threading.Thread(target=rec) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [])
        self.assertEqual(len(self.raw()), 1)
        self.assertEqual(self.raw()[0]["count"], 1, "3e32：同 key 重复上报 count 恒为 1")
        self.assertEqual(self.raw()[0]["seen"], 100, "互斥失效会让 seen 少于 100")


class TestCap(Base):
    def test_cap_never_drops_unacked(self):
        for i in range(LN.EVENTS_CAP + 30):
            LN.record_lead_event(self.root, "controller", "glm1", i, "takeover")
        evs = self.raw()
        self.assertEqual(len(evs), LN.EVENTS_CAP + 30, "未 ack 的事件不能因上限被静默丢弃")
        self.assertEqual(len(LN.lead_events_open(self.root)), LN.EVENTS_CAP + 30)

    def test_cap_trims_acked_first(self):
        for i in range(40):
            LN.record_lead_event(self.root, "controller", "glm1", i, "takeover")
        LN.ack_lead_event(self.root, all_events=True)
        for i in range(100, 130):
            LN.record_lead_event(self.root, "controller", "glm1", i, "handback")
        evs = self.raw()
        self.assertEqual(len([e for e in evs if not e["acked"]]), 30, "未 ack 的 30 条必须全在")
        self.assertLessEqual(len(evs), LN.EVENTS_CAP, f"总数应收敛到上限，实际 {len(evs)}")


class TestUnstable(Base):
    def test_unstable_flag(self):
        for i in range(2):
            LN.record_lead_event(self.root, "controller", "glm1", i, "takeover")
        self.assertFalse(LN.lead_events_open(self.root)[0]["unstable"], "2 次不算不稳定")
        for i in range(2, 5):
            LN.record_lead_event(self.root, "controller", "glm1", i, "takeover")
        out = LN.lead_events_open(self.root)
        self.assertTrue(out[0]["unstable"], f"窗口内 {out[0]['recent_total']} 次应判不稳定")
        self.assertEqual(out[0]["recent_total"], 5)

    def test_open_sorted_newest_first(self):
        LN.record_lead_event(self.root, "c", "a", 1, "takeover", now="2026-09-24T01:00:00")
        LN.record_lead_event(self.root, "c", "b", 2, "takeover", now="2026-09-24T03:00:00")
        LN.record_lead_event(self.root, "c", "d", 3, "takeover", now="2026-09-24T02:00:00")
        self.assertEqual([e["to"] for e in LN.lead_events_open(self.root)], ["b", "d", "a"])


class TestFeatures(Base):
    def test_defaults_and_override(self):
        self.assertEqual(LN.features(self.root), LN.FEATURE_DEFAULTS)
        with open(os.path.join(self.root, "console", "features.json"), "w", encoding="utf-8") as f:
            json.dump({"lead_events": False, "bogus_key": True}, f)
        got = LN.features(self.root)
        self.assertFalse(got["lead_events"])
        self.assertTrue(got["lead_label"], "未提及的开关保持默认")
        self.assertNotIn("bogus_key", got, "未知键不得混入")


if __name__ == "__main__":
    unittest.main(verbosity=2)
