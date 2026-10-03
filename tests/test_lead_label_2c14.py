# 2c14 显示名测试：label 只进显示层，协议字段 peer 永不被改写；与心跳并发不丢。
# 跑法：py -3 -X utf8 tests/test_lead_label_2c14.py（仓库根目录）。状态全建在临时目录。
import json, os, shutil, sys, tempfile, threading, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_lead_notify as LN


class TestLabel(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="lbl2c14-")
        self.peers = os.path.join(self.d, "peers")
        os.makedirs(self.peers)
        self.path = os.path.join(self.peers, "glm1.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"peer": "glm1", "status": "idle", "model": "glm-5.3", "last_seen": "2026-09-24T01:00:00"}, f)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def rec(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    # ---- 校验 ----
    def test_validate_trims_and_allows_cjk(self):
        self.assertEqual(LN.validate_label("  小队长 "), "小队长")
        self.assertEqual(LN.validate_label(""), "")
        self.assertEqual(LN.validate_label(None), "")

    def test_validate_rejects_control_chars(self):
        for bad in ("a\nb", "a\rb", "a\tb", "a\x00b", "a\x1bb", "a\x7fb"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                LN.validate_label(bad)

    def test_validate_rejects_too_long(self):
        LN.validate_label("x" * LN.LABEL_MAX)
        with self.assertRaises(ValueError):
            LN.validate_label("x" * (LN.LABEL_MAX + 1))

    # ---- 落盘 ----
    def test_set_and_clear(self):
        LN.set_label(self.peers, "glm1", "小队长")
        self.assertEqual(self.rec()["label"], "小队长")
        self.assertIn("label_updated", self.rec())
        LN.set_label(self.peers, "glm1", "")
        self.assertNotIn("label", self.rec())
        self.assertNotIn("label_updated", self.rec())

    def test_peer_field_never_overwritten(self):
        # 最危险的失败模式：把显示名灌进协议字段
        LN.set_label(self.peers, "glm1", "主控甲")
        self.assertEqual(self.rec()["peer"], "glm1")
        self.assertEqual(self.rec()["status"], "idle")
        self.assertEqual(self.rec()["model"], "glm-5.3")

    def test_display_name_falls_back(self):
        self.assertEqual(LN.display_name(self.peers, "glm1"), "glm1")
        LN.set_label(self.peers, "glm1", "小队长")
        self.assertEqual(LN.display_name(self.peers, "glm1"), "小队长")
        self.assertEqual(LN.display_name(self.peers, "nosuch"), "nosuch")

    def test_set_label_on_missing_file_creates_it(self):
        # 3e32：默认不给不存在的 peer 建档（API 404，不造幽灵登记）；只有显式 create=True 才建
        with self.assertRaises(LN.PeerMissing):
            LN.set_label(self.peers, "brandnew", "新来的")
        self.assertFalse(os.path.exists(os.path.join(self.peers, "brandnew.json")))
        LN.set_label(self.peers, "brandnew", "新来的", create=True)
        with open(os.path.join(self.peers, "brandnew.json"), encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual((d["peer"], d["label"]), ("brandnew", "新来的"))

    # ---- 并发：心跳 vs 改名 ----
    def _heartbeat_like(self, n):
        # 复刻 bus_core._heartbeat 的读-改-写，含同一把 per-peer 互斥
        for i in range(n):
            with LN.file_mutex(LN.peer_mutex_path(self.peers, "glm1")):
                d = LN._read(self.path, {}) or {}
                d.update({"peer": "glm1", "status": "busy", "last_seen": f"2026-09-24T01:00:{i:02d}"})
                LN._write(self.path, d)

    def test_label_survives_concurrent_heartbeats(self):
        errs = []

        def rename():
            try:
                for i in range(40):
                    LN.set_label(self.peers, "glm1", f"名{i}")
            except Exception as e:  # noqa: BLE001
                errs.append(e)

        def beat():
            try:
                self._heartbeat_like(40)
            except Exception as e:  # noqa: BLE001
                errs.append(e)

        ts = [threading.Thread(target=rename), threading.Thread(target=beat), threading.Thread(target=beat)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [])
        r = self.rec()
        self.assertEqual(r["peer"], "glm1", "并发后 peer 字段必须仍是真名")
        self.assertTrue(r.get("label", "").startswith("名"), f"label 被心跳抹掉了：{r}")
        self.assertTrue(r.get("last_seen"), "心跳字段被改名抹掉了")

    def test_mutex_is_reentrant_same_thread(self):
        p = LN.peer_mutex_path(self.peers, "glm1")
        with LN.file_mutex(p):
            with LN.file_mutex(p):
                LN.set_label(self.peers, "glm1", "嵌套")
        self.assertEqual(self.rec()["label"], "嵌套")

    def test_different_peers_do_not_block_each_other(self):
        with LN.file_mutex(LN.peer_mutex_path(self.peers, "glm1")):
            LN.set_label(self.peers, "other", "别人", create=True)  # 不同 peer，不同锁，不应超时（3e32：测试里显式建档）
        self.assertEqual(LN.get_label(self.peers, "other"), "别人")


if __name__ == "__main__":
    unittest.main(verbosity=2)
