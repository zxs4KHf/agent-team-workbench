# -*- coding: utf-8 -*-
"""冒烟：roles.json 缺失/模板/实例 三种来源下的可预期行为（审查 6683 分歧点 2 + fe87 返修 M1-M3 / N1-N5）

覆盖四件事：
  1. read_roles 的三段回退：console 实例 → tools 模板 → 都没有时返回 {}（不抛）；损坏实例 fail-closed。
  2. reviewer_config_error 对三种来源给出的指引**各自可执行**：
     missing 不能让人去复制一个按定义不存在的文件（M1）；template 要指向实例、并明说别改仓库模板（M2）。
  3. bus_tool 派审分支**真实调用**：pick_reviewers 返回 [] 时，抛出的 ValueError 必须严格等于
     reviewer_config_error() 的返回值；配置没问题时才回落「没有在线的Reviewer」原文案（M3）。
     这条用哨兵串断言「严格相等」，所以把 bus_tool 里的 cfg 删掉、或把 or 两侧颠倒，都会立刻变红。
  4. _kill_stale 只删 pid 文件、绝不调用 taskkill（打桩证明，不在本机杀任何进程）。
全程只用临时目录与打桩，不碰 console/ peers/ inbox/ outbox/ projects/ roles/ 的运行状态，不读凭据。
    py -3 tests\test_roles_config_smoke.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bus_core as B  # noqa: E402


class RolesFallback(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="roles_smoke_")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)   # N1：别留临时目录
        self.inst = os.path.join(self.d, "console", "roles.json")
        self.tpl = os.path.join(self.d, "tools", "roles.json")
        os.makedirs(os.path.dirname(self.inst))
        os.makedirs(os.path.dirname(self.tpl))
        self._tpl_bak = B.ROLES_TEMPLATE
        B.ROLES_TEMPLATE = self.tpl
        self.addCleanup(setattr, B, "ROLES_TEMPLATE", self._tpl_bak)

    def write(self, path, obj):
        with open(path, "w", encoding="utf-8") as f:                 # N2：with
            json.dump(obj, f, ensure_ascii=False)

    # ---- 1 三段回退 ----
    def test_instance_wins(self):
        self.write(self.tpl, {"assignments": {"reviewer_groups": ["tpl"]}})
        self.write(self.inst, {"assignments": {"reviewer_groups": ["inst"]}})
        self.assertEqual(B.roles_source(self.inst), ("instance", self.inst))
        self.assertEqual(B.read_roles(self.inst)["assignments"]["reviewer_groups"], ["inst"])

    def test_template_fallback(self):
        self.write(self.tpl, {"assignments": {"reviewer_groups": ["tpl"]}})
        self.assertEqual(B.roles_source(self.inst), ("template", self.tpl))
        self.assertEqual(B.read_roles(self.inst)["assignments"]["reviewer_groups"], ["tpl"])
        self.assertEqual(B.reviewer_config_error(self.inst), "", "模板可用且配了组时不该报配置缺失")

    def test_both_missing_is_predictable(self):
        src, path = B.roles_source(self.inst)
        self.assertEqual((src, path), ("missing", self.inst))
        self.assertEqual(B.read_roles(self.inst), {}, "两者都缺时返回空字典，不抛异常")

    def test_broken_instance_fails_closed(self):
        with open(self.inst, "w", encoding="utf-8") as f:
            f.write("{ 这不是 json")
        self.write(self.tpl, {"assignments": {"reviewer_groups": ["tpl"]}})
        with self.assertRaises(ValueError):
            B.read_roles(self.inst)          # 损坏必须报错，不能静默换成模板
        with self.assertRaises(ValueError):
            B.reviewer_config_error(self.inst)   # 自检也必须 fail-closed，不许把损坏当"没配"

    # ---- 2 三种来源的指引各自可执行 ----
    def test_missing_message_does_not_point_at_a_nonexistent_file(self):
        """M1 + N3：进入 missing 分支的前提就是模板不存在，指引不能让人去复制它。"""
        msg = B.reviewer_config_error(self.inst)
        self.assertIn("配置缺失", msg)
        self.assertIn("这不是「无人在线」", msg)
        self.assertIn("从仓库恢复", msg)
        self.assertIn(self.inst, msg, "要告诉用户新建哪个实例文件")
        self.assertNotIn("复制为", msg, "不得再指示复制一个按定义不存在的文件")

    def test_template_message_points_at_instance_not_repo_template(self):
        """M2：来源是模板时，指引必须指向实例文件，并明说不要改仓库模板。"""
        self.write(self.tpl, {"assignments": {"builder_groups": ["x"]}})   # 模板存在但没配 reviewer_groups
        msg = B.reviewer_config_error(self.inst)
        self.assertIn("配置缺失", msg)
        self.assertIn("reviewer_groups", msg)
        self.assertIn(self.inst, msg, "应指引去补实例 console/roles.json")
        self.assertIn("不要改仓库模板", msg)

    def test_instance_message_points_at_the_instance_file(self):
        self.write(self.inst, {"assignments": {"builder_groups": ["x"]}})
        msg = B.reviewer_config_error(self.inst)
        self.assertIn("配置缺失", msg)
        self.assertIn(self.inst, msg)
        self.assertIn("reviewer_groups", msg)

    def test_pick_reviewers_returns_empty_when_roles_empty(self):
        """N5：测的是「空配置」，与缺文件无关；连 list_peers 一并打桩，换台机器结果也确定。"""
        bak_roles, bak_peers = B.read_roles, B.list_peers
        try:
            B.read_roles = lambda path="": {}
            B.list_peers = lambda: [{"peer": "x1", "online": True}]
            self.assertEqual(B.pick_reviewers(), [], "空配置下应返回空列表，而不是抛未知异常")
        finally:
            B.read_roles, B.list_peers = bak_roles, bak_peers

    # ---- 3 priority() 缺文件时的默认值可预期 ----
    def test_priority_defaults_when_file_missing(self):
        bak = B.PRIORITY
        try:
            B.PRIORITY = os.path.join(self.d, "does_not_exist.json")
            p = B.priority()
            self.assertEqual(p["relay_order"], [])
            self.assertIsNone(p["relay_order_meta"])
            self.assertEqual(p["rev"], 0)
            self.assertEqual(p["agent_weight"], {})
            self.assertEqual(p["task_order"], [])
            self.assertEqual(p["version"], B.PRIORITY_VERSION)
            self.assertEqual(p["relay_policy"], {**B.RELAY_POLICY_DEFAULT})
        finally:
            B.PRIORITY = bak


class BusToolReviewBranch(unittest.TestCase):
    """M3：真实跑 bus_tool 的派审分支，断言它确实先取 reviewer_config_error()。

    旧版 test_bus_tool_prefers_config_message 在测试里重抄了一遍 `cfg or "没有在线的…"` 再断言，
    等于「自己写的式子等于自己写的式子」——把 bus_tool 里的 cfg 漏传或 or 写反，它照样绿。
    这里改成：打桩 pick_reviewers 返回 []、reviewer_config_error 返回哨兵串，真正调用 bus_tool.main()，
    断言抛出的 ValueError **严格等于**哨兵串。变异检查见 test_变异_删掉cfg取值会变红 的说明。
    """

    SENTINEL = "【哨兵】配置缺失：这串只可能来自 reviewer_config_error"
    FALLBACK = "没有在线的Reviewer（roles.json assignments.reviewer_groups 里的模型组无人在线）"

    def _run_review(self, cfg_value):
        import bus_tool
        import bus_build as BB
        baks = {
            (B, "pick_reviewers"): B.pick_reviewers,
            (B, "reviewer_config_error"): B.reviewer_config_error,
            (B, "peer_group"): B.peer_group,
            (BB, "review_target"): BB.review_target,
            (BB, "review_pack"): BB.review_pack,
        }
        bak_argv, bak_stdin = sys.argv, sys.stdin
        try:
            B.pick_reviewers = lambda **kw: []                 # 没挑到人 → 走报错分支
            B.reviewer_config_error = lambda path="": cfg_value
            B.peer_group = lambda peer: "grp"
            BB.review_target = lambda task_id, teammate: "t"
            BB.review_pack = lambda task_id, teammate, target=None: "pack"
            sys.argv = ["bus_tool.py", "build"]
            sys.stdin = io.StringIO(json.dumps(
                {"action": "review", "task_id": "T1", "teammate": "builder1"}))
            with self.assertRaises(ValueError) as cm:
                bus_tool.main()
            return str(cm.exception)
        finally:
            for (mod, name), fn in baks.items():
                setattr(mod, name, fn)
            sys.argv, sys.stdin = bak_argv, bak_stdin

    def test_配置有问题时抛的就是配置自检原文(self):
        self.assertEqual(self._run_review(self.SENTINEL), self.SENTINEL)

    def test_配置没问题时回落无人在线原文案(self):
        self.assertEqual(self._run_review(""), self.FALLBACK)

    def test_变异_删掉cfg取值会变红(self):
        """说明用：上面两条合起来钉死了 `cfg or 原文案` 这一个表达式。

        - 删掉 cfg（直接抛原文案）→ 第 1 条红（拿到的是原文案，不等于哨兵）；
        - 把 or 两侧颠倒（`原文案 or cfg`）→ 第 1 条红（永远拿原文案）；
        - 把 cfg 换成别的函数 → 第 1 条红（拿不到哨兵）。
        本条只做静态兜底：源码里该分支必须先引用 reviewer_config_error，再 or 回落。
        2026-10-01：派审逻辑从 bus_tool 移到 bus_build.dispatch_review（Leader 与网页「催审」共用），
        bus_tool 只是转调；上面两条行为测试仍经 bus_tool.main() 真跑，这里改查代码所在的 bus_build.py。
        """
        with open(os.path.join(ROOT, "bus_build.py"), encoding="utf-8") as f:
            src = f.read()
        i = src.find("if not reviewers:")
        self.assertGreater(i, 0)
        seg = src[i:i + 400]
        self.assertIn("reviewer_config_error", seg)
        self.assertLess(seg.index("reviewer_config_error"), seg.index("没有在线的Reviewer"),
                        "必须先取配置自检，再 or 回落原文案")


class KillStaleNeverKills(unittest.TestCase):
    """B-1：只删 pid 文件、不杀进程。用打桩证明，绝不在本机真的执行 tasklist/taskkill。"""

    def test_only_removes_pidfile(self):
        import subprocess as _sp
        import bus_plugins as P
        d = tempfile.mkdtemp(prefix="kill_stale_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)        # N1
        bak_data, bak_logs = P.DATA, P.LOGS
        bak_run, bak_popen, bak_call = _sp.run, _sp.Popen, _sp.check_output
        calls = []
        try:
            P.DATA = os.path.join(d, "data")
            P.LOGS = os.path.join(d, "log")
            pid_dir = os.path.join(P.DATA, "demo")
            os.makedirs(pid_dir)
            pidfile = os.path.join(pid_dir, "process.pid")
            with open(pidfile, "w", encoding="utf-8") as f:
                f.write("4")                      # 4 号进程在 Windows 上是 System，误杀后果极严重

            def boom(*a, **k):
                calls.append(a)
                raise AssertionError("不该调用子进程：%r" % (a,))
            _sp.run = boom
            _sp.Popen = boom
            _sp.check_output = boom
            P._kill_stale("demo")
            self.assertFalse(os.path.exists(pidfile), "pid 文件应被删除")
            self.assertEqual(calls, [], "不得调用 tasklist / taskkill")
            log = os.path.join(P.LOGS, "demo.log")
            self.assertTrue(os.path.isfile(log))
            with open(log, encoding="utf-8") as f:                   # N2
                self.assertIn("不强杀", f.read())
        finally:
            _sp.run, _sp.Popen, _sp.check_output = bak_run, bak_popen, bak_call
            P.DATA, P.LOGS = bak_data, bak_logs

    def test_source_has_no_kill_call(self):
        """N4：只断言**代码行**不调用 taskkill/tasklist，注释里提到不算（原断言会被一句注释搞红）。"""
        with open(os.path.join(ROOT, "bus_plugins.py"), encoding="utf-8") as f:
            lines = f.read().splitlines()
        code = [l.split("#", 1)[0] for l in lines]
        bad = [n + 1 for n, l in enumerate(code) if "taskkill" in l or "tasklist" in l]
        self.assertEqual(bad, [], "bus_plugins.py 这些行真的在调用杀进程命令：%s" % bad)

    def test_missing_pidfile_is_noop(self):
        import bus_plugins as P
        d = tempfile.mkdtemp(prefix="kill_stale2_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)        # N1
        bak = P.DATA
        try:
            P.DATA = os.path.join(d, "data")
            P._kill_stale("nope")                 # 不存在 pid 文件时静默返回，不建目录、不抛
            self.assertFalse(os.path.exists(os.path.join(P.DATA, "nope")))
        finally:
            P.DATA = bak


if __name__ == "__main__":
    unittest.main(verbosity=2)
