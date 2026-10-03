# 93db / R3：9216 三个阻断的回归。坏形状用「直接写临时 JSON」制造，不经 set_profile；
# 评分按真实 teammates 字典 key；锁边界只断言实际覆盖范围。
# 跑法（两种入口都必须过，门禁走第一种）：
#   python tests/test_profile_r3_93db.py
#   python -m unittest tests.test_profile_r3_93db
# 全部状态建在临时目录：不读写生产 profiles/ priority.json peers/ inbox/ console/builds。
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
import bus_core as B          # noqa: E402
import bus_lead_notify as LN  # noqa: E402
import bus_projects as P      # noqa: E402

GROUPS = [{"id": "m-a", "match": ["m-a"], "order": 1}]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="profile_r3_")
        self._saved = (B.ROOT, B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.LOCK, B.PRIORITY, B.PROFILES, B.WORKSPACE,
                       P.PROJECTS, P.COMMANDS, P.DONE, B.model_groups)
        B.ROOT = B.WORKSPACE = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.LOCK = os.path.join(self.tmp, "console", "controller.lock")
        B.PRIORITY = os.path.join(self.tmp, "console", "priority.json")
        B.PROFILES = os.path.join(self.tmp, "profiles")
        P.PROJECTS, P.COMMANDS, P.DONE = (os.path.join(self.tmp, *d) for d in (("projects",), ("console", "commands"), ("console", "done")))
        for d in (B.PEERS, B.INBOX, B.OUTBOX, B.LOG, os.path.dirname(B.LOCK), B.PROFILES,
                  P.PROJECTS, P.COMMANDS, P.DONE, os.path.join(self.tmp, "console", "builds")):
            os.makedirs(d, exist_ok=True)
        B.model_groups = lambda: GROUPS
        if hasattr(P, "_STATE_CACHE"):
            P._STATE_CACHE.update({"at": 0.0, "data": None})
        for peer, model in (("alpha", "m-a"), ("beta", "m-b"), ("gamma", "m-c"), ("delta", "m-d")):
            B.write_json(os.path.join(B.PEERS, f"{peer}.json"),
                         {"peer": peer, "model": model, "status": "idle", "last_seen": B.now_iso()})

    def tearDown(self):
        (B.ROOT, B.PEERS, B.INBOX, B.OUTBOX, B.LOG, B.LOCK, B.PRIORITY, B.PROFILES, B.WORKSPACE,
         P.PROJECTS, P.COMMANDS, P.DONE, B.model_groups) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def raw_profile(self, peer, obj):
        """绕开 set_profile 直接落盘：模拟旧版本文件 / 手改 / 别的脚本写坏。"""
        os.makedirs(B.PROFILES, exist_ok=True)
        with open(os.path.join(B.PROFILES, f"{peer}.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def build_rec(self, name, teammates):
        # Deliberately create legacy malformed numeric JSON; production writes reject NaN/Inf.
        with open(os.path.join(self.tmp, "console", "builds", f"{name}.json"), "w", encoding="utf-8") as stream:
            json.dump({"teammates": teammates}, stream, ensure_ascii=False, allow_nan=True)


# ───────────────── 阻断 1：坏形状不得漏给下游 ─────────────────

BAD_SHAPES = [
    ("strengths 为字符串", {"strengths": "架构"}),
    ("strengths 为空对象", {"strengths": {}}),
    ("strengths 为 null", {"strengths": None}),
    ("strengths 为数组", {"strengths": ["a"]}),
    ("strengths.tags 为字符串", {"strengths": {"tags": "架构", "note": ""}}),
    ("strengths.tags 含非字符串", {"strengths": {"tags": ["ok", 3, None], "note": ""}}),
    ("strengths.note 为数字", {"strengths": {"tags": [], "note": 42}}),
    ("scope_paths 为字符串", {"scope_paths": "a/b"}),
    ("scope_paths 为 null", {"scope_paths": None}),
    ("scope_paths 含非字符串", {"scope_paths": ["a/b", 1, {}]}),
    ("task_prefs 为 null", {"task_prefs": None}),
    ("task_prefs 为对象", {"task_prefs": {"a": 1}}),
    ("task_prefs 含非字符串", {"task_prefs": ["评审", 7]}),
    ("paused 为字符串", {"paused": "yes"}),
    ("paused 为数字", {"paused": 1}),
    ("answer_style 非法值", {"answer_style": "诗人"}),
    ("answer_style 为数字", {"answer_style": 9}),
    ("display_name 为数字", {"display_name": 123}),
    ("整体为数组", ["nope"]),
    ("整体为字符串", "nope"),
    ("整体为 null", None),
]


class BadShapeTests(Base):
    def test_get_profile_normalizes_every_bad_shape(self):
        d = B.default_profile()
        for label, obj in BAD_SHAPES:
            with self.subTest(label):
                self.raw_profile("alpha", obj)
                prof = B.get_profile("alpha")
                self.assertEqual(set(prof), set(d), f"{label}：键集合被污染")
                self.assertIsInstance(prof["strengths"], dict, label)
                self.assertIsInstance(prof["strengths"]["tags"], list, label)
                self.assertTrue(all(isinstance(x, str) for x in prof["strengths"]["tags"]), label)
                self.assertIsInstance(prof["strengths"]["note"], str, label)
                self.assertIsInstance(prof["scope_paths"], list, label)
                self.assertTrue(all(isinstance(x, str) for x in prof["scope_paths"]), label)
                self.assertIsInstance(prof["task_prefs"], list, label)
                self.assertTrue(all(isinstance(x, str) for x in prof["task_prefs"]), label)
                self.assertIsInstance(prof["paused"], bool, label)
                self.assertIn(prof["answer_style"], ("",) + B.ANSWER_STYLES, label)
                self.assertIsInstance(prof["display_name"], str, label)

    def test_good_fields_survive_alongside_bad_ones(self):
        # 局部坏不应连累局部好：只有坏的那个字段退默认
        self.raw_profile("alpha", {"strengths": "坏", "scope_paths": ["workbench/bus"], "paused": True})
        prof = B.get_profile("alpha")
        self.assertEqual(prof["strengths"], {"tags": [], "note": ""})
        self.assertEqual(prof["scope_paths"], ["workbench/bus"], "好字段不该被牵连")
        self.assertTrue(prof["paused"])

    def test_role_line_never_raises_on_bad_shapes(self):
        for label, obj in BAD_SHAPES:
            with self.subTest(label):
                self.raw_profile("alpha", obj)
                self.assertIsInstance(B.role_line_for("alpha"), str, label)

    def test_bad_profile_does_not_break_dispatch_for_other_agents(self):
        # 这是阻断 1 的核心：一个坏文件曾让整次 dispatch 抛错，四个 Agent 一起收不到任务
        self.raw_profile("alpha", {"strengths": "坏掉了"})
        B.set_profile("beta", {"scope_paths": ["workbench/bus"]})
        r = B.dispatch("controller", ["alpha", "beta", "gamma", "delta"], "independent", "问题", [], ttl_min=5)
        self.assertEqual(sorted(r["peers"]), ["alpha", "beta", "delta", "gamma"])
        for peer in ("alpha", "beta", "gamma", "delta"):
            self.assertTrue(os.path.exists(os.path.join(B.INBOX, peer, f"{r['task_id']}.json")), f"{peer} 没收到任务")
        # 坏 profile 的 alpha 不带 role_line（降级为默认），好 profile 的 beta 正常带
        self.assertNotIn("role_line", B.read_json(os.path.join(B.INBOX, "alpha", f"{r['task_id']}.json")))
        self.assertIn("role_line", B.read_json(os.path.join(B.INBOX, "beta", f"{r['task_id']}.json")))

    def test_bad_profile_does_not_break_suggest_or_inbox_order(self):
        self.raw_profile("alpha", {"strengths": None, "task_prefs": "坏"})
        out = P.suggest_peers("整理文档", [], "independent")
        self.assertEqual(4, len(out), "推荐不得因一个坏 profile 少人或抛错")
        self.raw_profile("beta", {"task_prefs": {"x": 1}})
        self.inbox("beta", "20260924-000001-t1", "review")
        self.inbox("beta", "20260924-000002-t2", "build")
        self.assertEqual(["20260924-000001-t1.json", "20260924-000002-t2.json"],
                         B.inbox_pick_order("beta", os.path.join(B.INBOX, "beta"),
                                            sorted(os.listdir(os.path.join(B.INBOX, "beta")))),
                         "坏 task_prefs 应降级为无偏好、按时间")

    def inbox(self, peer, tid, mode):
        d = os.path.join(B.INBOX, peer)
        os.makedirs(d, exist_ok=True)
        B.write_json(os.path.join(d, f"{tid}.json"),
                     {"id": tid, "from": "controller", "to": peer, "mode": mode, "question": "q",
                      "context": [], "created": B.now_iso(), "deadline": "9999-01-01T00:00:00"})


class RoleLineTests(Base):
    def test_role_line_is_strictly_single_line(self):
        # 换行注入：scope_paths 里塞换行不能在任务包里凭空多出几行
        self.raw_profile("alpha", {"scope_paths": ["a\nb", "c\r\nd"],
                                   "strengths": {"tags": ["x\u2028y", "z\tw"], "note": ""}})
        line = B.role_line_for("alpha")
        self.assertNotIn("\n", line); self.assertNotIn("\r", line)
        self.assertNotIn("\t", line); self.assertNotIn("\u2028", line)
        self.assertEqual(1, len(line.splitlines()))

    def test_role_line_truncated_and_still_single_line(self):
        self.raw_profile("alpha", {"scope_paths": ["x" * 300 + "\n" + "y" * 300]})
        line = B.role_line_for("alpha")
        self.assertLessEqual(len(line), 200)
        self.assertEqual(1, len(line.splitlines()))

    def test_rendered_task_keeps_structure(self):
        self.raw_profile("alpha", {"scope_paths": ["a\nb"]})
        r = B.dispatch("controller", ["alpha"], "independent", "问题", [], ttl_min=5)
        task = B.read_json(os.path.join(B.INBOX, "alpha", f"{r['task_id']}.json"))
        text = B.render_task(task)
        self.assertIn(task["role_line"], text)
        self.assertEqual(1, sum(1 for l in text.splitlines() if l.startswith("你的角色：")))


# ───────────────── 阻断 2：评分按真实 teammates key ─────────────────

class ScoreTests(Base):
    def test_score_reads_teammates_dict_key(self):
        # peer 名是 key，记录内部没有 peer 字段（bus_build.open_worktrees 的真实结构）
        self.build_rec("b1", {"alpha": {"path": "p", "branch": "b", "review_score": 8}})
        self.assertEqual(8.0, P._recent_avg_score("alpha"))
        self.assertEqual(0.0, P._recent_avg_score("beta"))

    def test_score_averages_across_builds(self):
        self.build_rec("b1", {"alpha": {"review_score": 6}})
        self.build_rec("b2", {"alpha": {"review_score": 10}, "beta": {"review_score": 2}})
        self.assertEqual(8.0, P._recent_avg_score("alpha"))
        self.assertEqual(2.0, P._recent_avg_score("beta"))

    def test_score_ignores_bad_values(self):
        for bad in ("9", None, True, False, [], {}, float("nan"), float("inf"), float("-inf")):
            with self.subTest(repr(bad)):
                self.build_rec("bx", {"alpha": {"review_score": bad}})
                self.assertEqual(0.0, P._recent_avg_score("alpha"), f"{bad!r} 不该计入均分")

    def test_score_survives_broken_build_records(self):
        self.build_rec("b1", {"alpha": {"review_score": 4}})
        B.write_json(os.path.join(self.tmp, "console", "builds", "b2.json"), {"teammates": "坏"})
        B.write_json(os.path.join(self.tmp, "console", "builds", "b3.json"), {"teammates": {"alpha": "坏"}})
        B.write_json(os.path.join(self.tmp, "console", "builds", "b4.json"), [])
        self.assertEqual(4.0, P._recent_avg_score("alpha"), "坏记录应跳过而不是抛错")

    def test_score_feeds_suggest_reason(self):
        self.build_rec("b1", {"alpha": {"review_score": 8}})
        out = {x["peer"]: x for x in P.suggest_peers("随便", [], "independent")}
        self.assertEqual(4.0, out["alpha"]["score"], "均分 8 → +4.0")
        self.assertIn("近期均分 8.0", out["alpha"]["reason"])
        self.assertEqual(0.0, out["beta"]["score"])

    def test_no_fake_scores_when_field_absent(self):
        # 诚实口径：目前没有任何写入方，字段缺席时必须恒 0，不能凭空造分
        self.build_rec("b1", {"alpha": {"path": "p", "branch": "b", "commit": "c", "merged": True}})
        self.assertEqual(0.0, P._recent_avg_score("alpha"))
        self.assertEqual(0.0, P.suggest_peers("随便", [], "independent")[0]["score"])


# ───────────────── 锁边界：只断言实际覆盖范围 ─────────────────

class LockTests(Base):
    def test_concurrent_threads_lose_nothing(self):
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
        self.assertEqual(("名39", ["p39"], ["类39"]), (prof["display_name"], prof["scope_paths"], prof["task_prefs"]))

    def test_mutex_file_is_separate_from_peers_and_lease(self):
        B.set_profile("alpha", {"display_name": "甲"})
        mp = B._profile_mutex_path("alpha")
        self.assertTrue(os.path.exists(mp), "锁文件应在 profiles/ 下")
        self.assertNotEqual(mp, LN.peer_mutex_path(B.PEERS, "alpha"), "不得与心跳锁共用")
        self.assertNotIn("controller.lock", mp, "不得与全局租约耦合")
        # 锁文件不是 profile：profiles_all 不能把它当成一个 peer
        self.assertNotIn(".alpha", B.profiles_all())
        self.assertEqual(["alpha"], sorted(B.profiles_all()))

    def test_lock_is_cross_process(self):
        """真·跨进程：另起一个 python 子进程去抢同一把锁，本进程持锁期间它必须拿不到。

        注意不能用线程来测「跨进程」：file_mutex 里先取的是进程内 RLock（无超时），
        同进程第二个线程会阻塞在 Python 锁上而不是走到 OS 字节锁的超时分支 —— 那样测的是另一回事。
        """
        import subprocess, textwrap
        B.set_profile("alpha", {"display_name": "初始"})
        mp = B._profile_mutex_path("alpha")
        code = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {REPO!r})
            import bus_lead_notify as LN
            try:
                with LN.file_mutex({mp!r}, timeout=0.3):
                    print("ACQUIRED")
            except Exception as e:
                print("BLOCKED", type(e).__name__)
        """)
        with LN.file_mutex(mp):
            r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertIn("BLOCKED", r.stdout, f"持锁期间子进程竟拿到了锁：{r.stdout!r} {r.stderr[-300:]!r}")
        self.assertNotIn("ACQUIRED", r.stdout)
        # 释放之后同一个子进程调用应当能拿到，证明刚才的阻塞不是环境本身坏了
        r2 = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertIn("ACQUIRED", r2.stdout, f"释放后仍拿不到锁：{r2.stdout!r} {r2.stderr[-300:]!r}")
        self.assertEqual("初始", B.get_profile("alpha")["display_name"])

    def test_invalid_peer_creates_no_lock_file(self):
        with self.assertRaises(ValueError):
            B.set_profile("../evil", {"paused": True})
        self.assertEqual([], [f for f in os.listdir(B.PROFILES) if "evil" in f])


# ───────────────── 裁决项：自定义 task_prefs 必须继续允许 ─────────────────

class DecisionTests(Base):
    def test_custom_task_prefs_allowed(self):
        saved = B.set_profile("alpha", {"task_prefs": ["自定义类型 A", "评审", "另一个自定义"]})
        self.assertEqual(["自定义类型 A", "评审", "另一个自定义"], saved["task_prefs"],
                         "507e 明确允许自定义项，PROFILE_PREFS 是预设而非白名单")
        self.assertEqual(saved["task_prefs"], B.get_profile("alpha")["task_prefs"])

    def test_paused_is_not_a_permission(self):
        B.set_profile("gamma", {"paused": True})
        self.assertNotIn("gamma", [x["peer"] for x in P.suggest_peers("q", [], "independent")])
        r = B.dispatch("controller", ["gamma"], "independent", "点名", [], ttl_min=5)
        self.assertEqual(["gamma"], r["peers"])
        self.assertTrue(os.path.exists(os.path.join(B.INBOX, "gamma", f"{r['task_id']}.json")))

    def test_no_profile_agent_unchanged(self):
        # 无 profile 的老用户：不带 role_line，收件顺序仍是纯时间序
        d = os.path.join(B.INBOX, "beta")
        os.makedirs(d, exist_ok=True)
        for tid, mode in (("20260924-000002-t2", "build"), ("20260924-000001-t1", "review")):
            B.write_json(os.path.join(d, f"{tid}.json"),
                         {"id": tid, "from": "controller", "to": "beta", "mode": mode, "question": "q",
                          "context": [], "created": B.now_iso(), "deadline": "9999-01-01T00:00:00"})
        self.assertEqual(["20260924-000001-t1.json", "20260924-000002-t2.json"],
                         B.inbox_pick_order("beta", d, sorted(os.listdir(d))))
        self.assertEqual("", B.role_line_for("beta"))

    def test_priority_not_rewritten(self):
        B.write_json(B.PRIORITY, {"version": 2, "relay_order": ["m-a"], "agent_weight": {"alpha": 2},
                                  "task_order": ["x"], "rev": 3})
        self.raw_profile("alpha", {"strengths": "坏"})
        P.suggest_peers("q", [], "review")
        B.role_line_for("alpha")
        self.assertEqual({"version": 2, "relay_order": ["m-a"], "agent_weight": {"alpha": 2},
                          "task_order": ["x"], "rev": 3}, B.read_json(B.PRIORITY),
                         "读侧规范化不得回写任何文件")


# ───────────────── 越界路径与跨项目 ─────────────────

class PathTests(Base):
    BAD_PEERS = ["../evil", "..\\evil", "a/b", "a\\b", "/abs", "C:evil", ".", "..", "", " ", "Alpha", "1abc",
                 "x" * 40, "al\npha", "al\x00pha", "al pha", "al;pha"]

    def test_write_rejects_every_out_of_bound_peer(self):
        for peer in self.BAD_PEERS:
            with self.subTest(repr(peer)):
                with self.assertRaises(ValueError):
                    B.set_profile(peer, {"paused": True})
        # 目录里只应有合法名字产生的文件，没有任何逃逸痕迹
        leaked = [f for f in os.listdir(B.PROFILES) if "evil" in f or ".." in f]
        self.assertEqual([], leaked)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.tmp), "evil.json")),
                         "不得在临时目录之外落文件")

    def test_read_of_out_of_bound_peer_is_default_not_raise(self):
        for peer in self.BAD_PEERS:
            with self.subTest(repr(peer)):
                self.assertEqual(B.default_profile(), B.get_profile(peer))
                self.assertEqual("", B.role_line_for(peer))

    def test_profiles_all_ignores_non_profile_files(self):
        B.set_profile("alpha", {"display_name": "甲"})
        # 锁文件、临时残留、子目录、非 json 都不能被当成 peer
        open(os.path.join(B.PROFILES, "note.txt"), "w", encoding="utf-8").close()
        open(os.path.join(B.PROFILES, "alpha.json.tmp"), "w", encoding="utf-8").close()
        os.makedirs(os.path.join(B.PROFILES, "sub.json"), exist_ok=True)
        self.assertEqual(["alpha"], sorted(B.profiles_all()))

    def test_scope_paths_are_not_a_permission_boundary(self):
        # 507e 定性：scope_paths 是软推荐，不是 ws_read 边界。写入不做路径存在性/越界校验，
        # 但它只进推荐分与 role_line，不参与任何文件访问决策。
        B.set_profile("alpha", {"scope_paths": ["../../etc", "D:/somewhere"]})
        self.assertEqual(["../../etc", "D:/somewhere"], B.get_profile("alpha")["scope_paths"])
        line = B.role_line_for("alpha")
        self.assertIn("负责", line)
        self.assertEqual(1, len(line.splitlines()))

    def test_cross_project_context_files_do_not_cross_match(self):
        B.set_profile("alpha", {"scope_paths": ["projA/src"]})
        B.set_profile("beta", {"scope_paths": ["projB/src"]})
        out = {x["peer"]: x for x in P.suggest_peers("改一下", ["projB/src/main.py"], "independent")}
        self.assertEqual(4.0, out["beta"]["score"], "命中自己项目的路径")
        self.assertEqual(0.0, out["alpha"]["score"], "别的项目的路径不得命中")
        # 前缀必须是路径前缀，不能是子串巧合
        out2 = {x["peer"]: x for x in P.suggest_peers("改", ["projA-other/src/x.py"], "independent")}
        self.assertEqual(0.0, out2["alpha"]["score"], "projA-other 不是 projA/src 的前缀")


if __name__ == "__main__":
    unittest.main(verbosity=2)
