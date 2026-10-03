# 507e 第一切片（f218 返修 c396）：profile 数据层 / 推荐 / 收件破平 / API / state 聚合。
# 跑法（两种入口都必须过，宿主门禁走第一种）：
#   python tests/test_profile_507e.py
#   python -m unittest tests.test_profile_507e
# 全部状态建在临时目录：不读写生产 profiles/ priority.json peers/ inbox/ outbox/ console/builds。
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
if REPO not in sys.path:
    sys.path.insert(0, REPO)  # 直接以脚本运行时 sys.path[0] 是 tests/，没有它就是门禁里那句 No module named 'bus_core'
import bus_core as B  # noqa: E402
import bus_projects as P  # noqa: E402

GROUPS = [{"id": "m-a", "match": ["m-a"], "order": 1}]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="profile_507e_")
        self._saved = (B.ROOT, B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.LOCK, B.PRIORITY, B.PROFILES, B.WORKSPACE,
                       P.PROJECTS, P.COMMANDS, P.DONE, B.model_groups)
        B.ROOT = B.WORKSPACE = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.LOCK = os.path.join(self.tmp, "console", "controller.lock")
        B.PRIORITY = os.path.join(self.tmp, "console", "priority.json")
        B.PROFILES = os.path.join(self.tmp, "profiles")
        P.PROJECTS, P.COMMANDS, P.DONE = (os.path.join(self.tmp, *d) for d in (("projects",), ("console", "commands"), ("console", "done")))
        for d in (B.PEERS, B.INBOX, B.OUTBOX, B.LOG, os.path.dirname(B.LOCK), P.PROJECTS, P.COMMANDS, P.DONE, os.path.join(self.tmp, "console", "builds")):
            os.makedirs(d, exist_ok=True)
        B.model_groups = lambda: GROUPS  # 不碰 Arena 控制面
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        for peer, model in (("alpha", "m-a"), ("beta", "m-b"), ("gamma", "m-c"), ("delta", "m-d")):
            B.write_json(os.path.join(B.PEERS, f"{peer}.json"),
                         {"peer": peer, "model": model, "status": "idle", "last_seen": B.now_iso()})

    def tearDown(self):
        (B.ROOT, B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.LOCK, B.PRIORITY, B.PROFILES, B.WORKSPACE,
         P.PROJECTS, P.COMMANDS, P.DONE, B.model_groups) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def inbox_task(self, peer, tid, mode):
        d = os.path.join(B.INBOX, peer)
        os.makedirs(d, exist_ok=True)
        B.write_json(os.path.join(d, f"{tid}.json"), {"id": tid, "from": "controller", "to": peer, "mode": mode, "question": f"q-{tid}",
                                                      "context": [], "created": B.now_iso(), "deadline": "9999-01-01T00:00:00"})

    def picked(self, peer):
        # wait_task 真跑：有任务时立刻领取并返回渲染文本；从文本里取任务 id
        text = B.wait_task(peer, 1, "m-x")
        return text.split("# 任务 ", 1)[1].split("\n", 1)[0].strip() if "# 任务 " in text else text[:80]


class ProfileTests(Base):
    def test_1_missing_file_returns_default(self):
        prof = B.get_profile("nobody")
        self.assertEqual(prof, B.default_profile())
        self.assertEqual(prof["strengths"], {"tags": [], "note": ""})
        self.assertFalse(prof["paused"])

    def test_2_unknown_field_rejected_and_dedup(self):
        with self.assertRaises(ValueError):
            B.set_profile("alpha", {"backstory": "不该进来的字段"})
        with self.assertRaises(ValueError):
            B.set_profile("alpha", {"paused": "yes"})
        with self.assertRaises(ValueError):
            B.set_profile("alpha", {"answer_style": "诗人"})
        with self.assertRaises(ValueError):
            B.set_profile("../evil", {"paused": True})
        saved = B.set_profile("alpha", {"task_prefs": ["调研", "评审", "调研", "P0 阻塞", "评审"]})
        self.assertEqual(saved["task_prefs"], ["调研", "评审", "P0 阻塞"])
        self.assertFalse(os.path.exists(os.path.join(B.PROFILES, "evil.json")))

    def test_3_atomic_write_readback(self):
        B.set_profile("beta", {"display_name": "乙", "scope_paths": ["workbench/bus"], "paused": True})
        self.assertFalse([f for f in os.listdir(B.PROFILES) if f.endswith(".tmp")])
        with open(os.path.join(B.PROFILES, "beta.json"), encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["display_name"], "乙")
        self.assertEqual(B.get_profile("beta")["scope_paths"], ["workbench/bus"])

    def test_4_suggest_orders_hit_first_but_keeps_unmatched(self):
        B.set_profile("alpha", {"strengths": {"tags": ["文档", "飞书"], "note": ""}})
        out = P.suggest_peers("帮我整理飞书文档清单", [], "independent")
        peers_in = [x["peer"] for x in out]
        self.assertIn("beta", peers_in)  # 未命中者仍在返回列表中（只排序不过滤）
        self.assertEqual(peers_in[0], "alpha")
        self.assertGreater(out[0]["score"], 0)

    def test_5_redteam_ignores_strengths(self):
        B.set_profile("alpha", {"strengths": {"tags": ["文档"], "note": ""}, "task_prefs": ["评审"]})
        out = {x["peer"]: x for x in P.suggest_peers("帮我整理文档", [], "redteam")}
        self.assertEqual(out["alpha"]["score"], 0.0)
        self.assertEqual(out["alpha"]["matched"], 0)

    def test_6_at_least_one_unmatched_agent(self):
        for p in ("alpha", "beta", "gamma"):
            B.set_profile(p, {"strengths": {"tags": ["文档"], "note": ""}})
        out = P.suggest_peers("整理文档", [], "independent")
        self.assertEqual(len(out), 4)
        self.assertFalse(all(x["matched"] > 0 for x in out[:3]), "前 3 名全为有匹配者，异质性保底未生效")
        self.assertIn("异质性保底", out[2]["reason"])

    def test_7_paused_leaves_recommendation_but_stays_dispatchable(self):
        B.set_profile("gamma", {"paused": True})
        out = [x["peer"] for x in P.suggest_peers("随便什么问题", [], "independent")]
        self.assertNotIn("gamma", out)
        # paused 不是权限：点名照样派到它的收件箱，也照样能被 wait_task 领走
        r = B.dispatch("controller", ["gamma"], "independent", "点名问题", [], ttl_min=5)
        self.assertEqual(r["peers"], ["gamma"])
        self.assertTrue(os.path.exists(os.path.join(B.INBOX, "gamma", f"{r['task_id']}.json")))
        self.assertEqual(self.picked("gamma"), r["task_id"])

    def test_8_recommendation_does_not_read_or_invent_global_type_order(self):
        # 反例：往 priority.json 塞一个「task_type_order」不能改变推荐分；推荐里没有任何全局任务类型序，task_prefs 只是软信号
        B.set_profile("alpha", {"task_prefs": ["调研", "评审"]})
        B.set_profile("beta", {"task_prefs": ["评审", "调研"]})
        base = {x["peer"]: x["score"] for x in P.suggest_peers("评审这份设计", [], "review")}
        B.write_json(B.PRIORITY, {"version": 2, "relay_order": [], "agent_weight": {}, "task_order": [], "rev": 0,
                                  "task_type_order": ["调研", "评审"]})
        after = {x["peer"]: x["score"] for x in P.suggest_peers("评审这份设计", [], "review")}
        self.assertEqual(base, after)
        self.assertGreater(after["beta"], after["alpha"], "偏好位次靠前者分略高（软信号）")
        self.assertLess(after["beta"] - after["gamma"], 3.0, "偏好加分必须低于一次 tags 命中（3.0），不能主导排序")
        # 一次 tags 命中就翻过偏好分：偏好不是硬序
        B.set_profile("gamma", {"strengths": {"tags": ["设计"], "note": ""}})
        top = P.suggest_peers("评审这份设计", [], "review")[0]["peer"]
        self.assertEqual(top, "gamma")

    def test_9_pick_order_global_task_order_beats_personal_prefs(self):
        # 真实消费层：wait_task 收件顺序。alpha 偏好「代码实现」优先于「评审」；t1（review，更早）与 t2（build）同在收件箱
        B.set_profile("alpha", {"task_prefs": ["代码实现", "评审"]})
        self.inbox_task("alpha", "20260924-000001-t1", "review")
        self.inbox_task("alpha", "20260924-000002-t2", "build")
        self.assertEqual(self.picked("alpha"), "20260924-000002-t2", "没有全局序时，个人偏好在同级任务间破平")
        self.assertEqual(self.picked("alpha"), "20260924-000001-t1")
        # 反例：全局 task_order 点了 t1，个人偏好翻不了它
        self.inbox_task("alpha", "20260924-000001-t1", "review")
        self.inbox_task("alpha", "20260924-000002-t2", "build")
        B.write_json(B.PRIORITY, {"version": 2, "relay_order": [], "agent_weight": {}, "task_order": ["20260924-000001-t1"], "rev": 1})
        self.assertEqual(self.picked("alpha"), "20260924-000001-t1", "全局序优先：个人偏好不推翻")
        self.assertEqual(self.picked("alpha"), "20260924-000002-t2")

    def test_10_pick_order_without_prefs_is_time_order_and_unknown_type_last(self):
        self.inbox_task("beta", "20260924-000001-t1", "review")
        self.inbox_task("beta", "20260924-000002-t2", "build")
        self.assertEqual(self.picked("beta"), "20260924-000001-t1", "没有偏好就按时间")
        self.assertEqual(self.picked("beta"), "20260924-000002-t2")
        # 偏好里没有的类型排在有的后面，但仍会被领走（不过滤）
        B.set_profile("beta", {"task_prefs": ["评审"]})
        self.inbox_task("beta", "20260924-000003-t3", "build")
        self.inbox_task("beta", "20260924-000004-t4", "review")
        self.assertEqual(self.picked("beta"), "20260924-000004-t4")
        self.assertEqual(self.picked("beta"), "20260924-000003-t3")

    def test_11_priority_keys_semantics_untouched(self):
        B.write_json(B.PRIORITY, {"version": 2, "relay_order": ["m-a"], "agent_weight": {"alpha": 2}, "task_order": ["x"], "rev": 3})
        B.set_profile("alpha", {"task_prefs": ["评审"]})
        P.suggest_peers("评审", [], "review")
        self.inbox_task("alpha", "20260924-000001-t1", "review")
        self.picked("alpha")
        on_disk = B.read_json(B.PRIORITY)
        self.assertEqual(on_disk, {"version": 2, "relay_order": ["m-a"], "agent_weight": {"alpha": 2}, "task_order": ["x"], "rev": 3},
                         "推荐与收件都只读 priority.json，不回写、不新增键")
        self.assertNotIn("task_type_order", json.dumps(B.priority(), ensure_ascii=False))

    def test_12_concurrent_patches_lose_nothing(self):
        errs = []

        def worker(field, values):
            try:
                for v in values:
                    B.set_profile("delta", {field: v})
            except Exception as ex:  # noqa: BLE001
                errs.append(ex)

        ts = [threading.Thread(target=worker, args=("display_name", [f"名{i}" for i in range(40)])),
              threading.Thread(target=worker, args=("scope_paths", [[f"p{i}"] for i in range(40)])),
              threading.Thread(target=worker, args=("task_prefs", [[f"类{i}"] for i in range(40)]))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [])
        prof = B.get_profile("delta")
        self.assertEqual((prof["display_name"], prof["scope_paths"], prof["task_prefs"]), ("名39", ["p39"], ["类39"]),
                         "三个线程各改各的字段，读改写不加锁会互相覆盖")

    def test_role_line_injection_reaches_task_pack(self):
        B.set_profile("delta", {"scope_paths": ["workbench/bus"], "strengths": {"tags": ["架构", "审查"], "note": ""}})
        line = B.role_line_for("delta")
        self.assertTrue(line.startswith("你的角色：负责 workbench/bus，擅长 架构、审查"))
        self.assertLessEqual(len(line), 200)
        self.assertEqual(B.role_line_for("nobody"), "")
        r = B.dispatch("controller", ["delta", "beta"], "independent", "问题", [], ttl_min=5)
        task = B.read_json(os.path.join(B.INBOX, "delta", f"{r['task_id']}.json"))
        self.assertEqual(task["role_line"], line)
        self.assertNotIn("role_line", B.read_json(os.path.join(B.INBOX, "beta", f"{r['task_id']}.json")))
        self.assertIn(line, B.render_task(task))

    def test_migrate_team_tags(self):
        teams = [{"name": "文档组", "peers": ["alpha"], "tags": ["写作"]}]
        prof = B.migrate_team_tags("alpha", teams)
        self.assertEqual(prof["strengths"]["tags"], ["写作"])
        B.set_profile("beta", {"strengths": {"tags": ["原创"], "note": ""}})
        self.assertEqual(B.migrate_team_tags("beta", teams)["strengths"]["tags"], ["原创"])


class ApiTests(Base):
    def setUp(self):
        super().setUp()
        import bus_web as W
        from http.server import ThreadingHTTPServer
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), W.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def call(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers={"Content-Type": "application/json"},
                                     method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_post_profile_validates_and_state_aggregates(self):
        code, r = self.call("/api/profile", {"peer": "alpha", "backstory": "x"})
        self.assertEqual(code, 400)
        self.assertIn("未知字段", r["error"])
        code, r = self.call("/api/profile", {"peer": "", "paused": True})
        self.assertEqual(code, 400)
        code, r = self.call("/api/profile", {"peer": "alpha", "strengths": {"tags": ["文档"], "note": "写得快"}, "task_prefs": ["评审"], "answer_style": "设计"})
        self.assertEqual(code, 200)
        self.assertEqual(r["strengths"], {"tags": ["文档"], "note": "写得快"})
        self.assertTrue(os.path.exists(os.path.join(B.PROFILES, "alpha.json")))
        code, st = self.call("/api/state")
        self.assertEqual(code, 200)
        self.assertEqual(st["profiles"]["alpha"]["task_prefs"], ["评审"])
        self.assertNotIn("beta", st["profiles"], "没写过的 peer 不出现在聚合里")
        # 并发 POST 不丢更新
        threads = [threading.Thread(target=self.call, args=("/api/profile", {"peer": "beta", f: v}))
                   for f, v in (("display_name", "乙"), ("scope_paths", ["a/b"]), ("paused", True))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        prof = B.get_profile("beta")
        self.assertEqual((prof["display_name"], prof["scope_paths"], prof["paused"]), ("乙", ["a/b"], True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
