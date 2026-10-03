# -*- coding: utf-8 -*-
"""Agent 阵列与自定义（cd14 / 任务 455c）后端校验自测。

只用临时项目目录，不碰生产 projects / console / peers：把 bus_projects.PROJECTS 指到 tmp。
    py -3 tests/test_agents_455c.py
"""
import os
import shutil
import sys
import tempfile
import io
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bus_projects as P
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frontsrc import front_src  # noqa: E402  ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」


class AgentMetaAndTeams455c(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wb455c-")
        self._old = P.PROJECTS
        P.PROJECTS = os.path.join(self.tmp, "projects")
        os.makedirs(P.PROJECTS, exist_ok=True)
        self.p = P.create_project("测试项目455c", ["alpha1", "beta2"], "independent", [])
        self.slug = self.p["slug"]

    def tearDown(self):
        P.PROJECTS = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def upd(self, patch):
        return P.update_project(self.slug, patch)

    # ---- agent_meta 白名单 ----
    def test_接受五个允许字段(self):
        p = self.upd({"agent_meta": {"alpha1": {
            "display_name": "阿尔法", "note": "擅长后端", "model_label": "opus-5（别名）",
            "tags": ["后端", "审查"], "paused_in_project": True}}})
        meta = p["agent_meta"]["alpha1"]
        self.assertEqual(meta["display_name"], "阿尔法")
        self.assertEqual(meta["tags"], ["后端", "审查"])
        self.assertIs(meta["paused_in_project"], True)
        # 暂停只是项目里的元数据：不改 peers 名单，也不写 peer 文件
        self.assertEqual(p["peers"], ["alpha1", "beta2"])

    def test_拒绝未知键(self):
        with self.assertRaises(ValueError):
            self.upd({"agent_meta": {"alpha1": {"enabled": False}}})
        with self.assertRaises(ValueError):
            self.upd({"agent_meta": {"alpha1": {"model": "claude-opus-5"}}})

    def test_字段类型与上限(self):
        with self.assertRaises(ValueError):
            self.upd({"agent_meta": {"alpha1": {"paused": "yes"}}})
        with self.assertRaises(ValueError):
            self.upd({"agent_meta": {"alpha1": {"tags": "后端"}}})
        with self.assertRaises(ValueError):
            self.upd({"agent_meta": {"alpha1": {"tags": ["t%d" % i for i in range(P.TAGS_MAX + 1)]}}})
        with self.assertRaises(ValueError):
            self.upd({"agent_meta": {"alpha1": {"tags": ["x" * (P.TAG_LEN_MAX + 1)]}}})
        with self.assertRaises(ValueError):
            self.upd({"agent_meta": {"alpha1": {"model_label": "x" * 41}}})
        with self.assertRaises(ValueError):
            self.upd({"agent_meta": {"alpha1": {"display_name": "x" * 81}}})

    def test_结构字段仍不可改(self):
        for bad in ({"decisions": []}, {"status": "merged"}, {"repo_dir": "x"}):
            with self.assertRaises(ValueError):
                self.upd(bad)
        # slug 是身份回显，历史上允许原样带上但从不落库：既不报错，也改不动
        p = self.upd({"slug": "hack", "name": "改个名"})
        self.assertEqual(p["slug"], self.slug)
        self.assertEqual(p["name"], "改个名")

    # ---- default_team ----
    def test_默认团队必须是已存在的团队(self):
        self.upd({"teams": [{"name": "前端组", "peers": ["alpha1"], "mode": "independent", "note": ""}]})
        p = self.upd({"default_team": "前端组"})
        self.assertEqual(p["default_team"], "前端组")
        # 指向不存在的团队 → 回退空串（= 内置动态「全员在线」）
        p = self.upd({"default_team": "不存在的组"})
        self.assertEqual(p["default_team"], "")

    def test_动态全员在线存成空串(self):
        p = self.upd({"default_team": P.DYNAMIC_TEAM})
        self.assertEqual(p["default_team"], "")

    def test_团队改名或删除后默认值不悬空(self):
        self.upd({"teams": [{"name": "前端组", "peers": ["alpha1"]}]})
        self.upd({"default_team": "前端组"})
        p = self.upd({"teams": [{"name": "前端小组", "peers": ["alpha1"]}]})   # 改名
        self.assertEqual(p["default_team"], "")
        self.upd({"teams": [{"name": "甲组", "peers": ["alpha1"]}]})
        self.upd({"default_team": "甲组"})
        p = self.upd({"teams": []})                                            # 删除
        self.assertEqual(p["default_team"], "")

    def test_default_team_类型(self):
        with self.assertRaises(ValueError):
            self.upd({"default_team": ["前端组"]})

    # ---- teams ----
    def test_团队形状校验(self):
        with self.assertRaises(ValueError):
            self.upd({"teams": {"name": "x"}})
        with self.assertRaises(ValueError):
            self.upd({"teams": [{"name": ""}]})
        with self.assertRaises(ValueError):
            self.upd({"teams": [{"name": "x" * 41}]})
        with self.assertRaises(ValueError):
            self.upd({"teams": [{"name": "组", "peers": "alpha1"}]})
        with self.assertRaises(ValueError):
            self.upd({"teams": [{"name": "组", "peers": ["../etc"]}]})
        with self.assertRaises(ValueError):
            self.upd({"teams": [{"name": "组%d" % i} for i in range(P.TEAMS_MAX + 1)]})

    # ---- 30 秒撤销：用保存前快照再发一次同样校验的 patch ----
    def test_撤销只还原允许字段(self):
        self.upd({"agent_meta": {"alpha1": {"display_name": "旧名", "paused_in_project": False}}})
        before = {"display_name": "旧名", "paused_in_project": False}
        self.upd({"agent_meta": {"alpha1": {"display_name": "新名", "paused_in_project": True}}})
        p = self.upd({"agent_meta": {"alpha1": before}})      # 撤销 = 同一条通道回写快照
        self.assertEqual(p["agent_meta"]["alpha1"]["display_name"], "旧名")
        self.assertIs(p["agent_meta"]["alpha1"]["paused_in_project"], False)
        self.assertEqual(p["peers"], ["alpha1", "beta2"])     # 结构字段不受撤销影响


class ReservedTeamNames(unittest.TestCase):
    """e4e0 必改 3：自定义团队不得占用内置保留名，否则会顶掉动态组且永远当不上默认团队。"""

    def test_reserved_names_rejected(self):
        for name in P.RESERVED_TEAM_NAMES:
            with self.assertRaises(ValueError, msg=name):
                P.check_teams([{"name": name, "peers": []}])
            with self.assertRaises(ValueError, msg=name):   # 首尾空格也不能绕过
                P.check_teams([{"name": "  %s  " % name, "peers": []}])

    def test_reserved_list_matches_frontend(self):
        self.assertEqual(P.RESERVED_TEAM_NAMES, (P.DYNAMIC_TEAM, "项目团队"))

    def test_normal_team_still_accepted(self):
        P.check_teams([{"name": "前端小队", "peers": []}])


class EndToEndWritePath(unittest.TestCase):
    """e51b 验收 4：表单 FormData → POST body → 后端校验 → 读回判定，整条缝都要通。
    今天已第五次出现「测试绿但用户走不到」，所以这里不 stub 后端，也不 stub 前端源码。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="e51b_")
        self._bak = P.PROJECTS
        P.PROJECTS = self.tmp
        self.slug = P.create_project("端到端", "", [], "independent", [])["slug"]
        P.update_project(self.slug, {"teams": [{"name": "前端组", "peers": []}]})

    def tearDown(self):
        P.PROJECTS = self._bak
        shutil.rmtree(self.tmp, ignore_errors=True)

    # 前端表单会提交什么：直接读源码，避免「后端支持但没人写」再次发生
    def _web(self, name):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return io.open(os.path.join(root, "web", "v2", "js", name), encoding="utf-8").read()

    def test_paused_in_project_form_to_disk_to_read_side(self):
        app, prof, model = front_src("app.js"), self._web("profiles_ui.js"), self._web("model.js")
        self.assertIn('name="paused_in_project"', prof)                       # 勾选框存在
        self.assertIn("paused_in_project: fd.get('paused_in_project') === 'on'", app)  # 表单会提交
        self.assertIn("agentMeta(name, p).paused_in_project === true", model)  # 判定侧读同一个键
        # POST body 等价物（app.js 的 agent-meta 分支构造的就是这个形状）
        body = {"slug": self.slug, "agent_meta": {"alpha1": {"display_name": "", "note": "", "paused_in_project": True}}}
        P.update_project(body["slug"], {k: v for k, v in body.items() if k != "slug"})
        back = P.load_project(self.slug)["agent_meta"]["alpha1"]
        self.assertIs(back["paused_in_project"], True)                        # 读回来判定侧要用的那个键
        P.update_project(self.slug, {"agent_meta": {"alpha1": {"paused_in_project": False}}})
        self.assertIs(P.load_project(self.slug)["agent_meta"]["alpha1"]["paused_in_project"], False)

    def test_old_paused_key_is_rejected(self):
        with self.assertRaises(ValueError):    # 旧键不再放行，避免两个同名字段再次分叉
            P.update_project(self.slug, {"agent_meta": {"alpha1": {"paused": True}}})

    def test_project_team_roundtrip_44f1(self):
        P.update_project(self.slug, {"default_team": P.PROJECT_TEAM})
        self.assertEqual(P.load_project(self.slug)["default_team"], P.PROJECT_TEAM)
        P.update_project(self.slug, {"description": "改别的字段"})      # 再存一次不能把它当悬空名清掉
        self.assertEqual(P.load_project(self.slug)["default_team"], P.PROJECT_TEAM)
        p = P.create_project("新项目44f1", [], "independent", [], "", default_team=P.PROJECT_TEAM)
        self.assertEqual(p["default_team"], P.PROJECT_TEAM, "新建时也要存")
        p = P.create_project("新项目44f1b", [], "independent", [], "", default_team="不存在的组")
        self.assertEqual(p["default_team"], "", "新建时还没有自定义团队，悬空名回退空值")

    def test_default_team_form_to_disk(self):
        app = front_src("app.js")
        self.assertIn('name="default_team"', app)
        self.assertIn("default_team: String(fd.get('default_team')", app)
        P.update_project(self.slug, {"default_team": "前端组"})
        self.assertEqual(P.load_project(self.slug)["default_team"], "前端组")
        for reserved in P.RESERVED_TEAM_NAMES:                                 # 两个保留名都折成空
            P.update_project(self.slug, {"default_team": reserved})
            # 44f1：全员在线存空值；项目团队按原名存、读回同义
            self.assertEqual(P.load_project(self.slug)["default_team"], "" if reserved == P.DYNAMIC_TEAM else P.PROJECT_TEAM, reserved)
        P.update_project(self.slug, {"default_team": "不存在的队"})
        self.assertEqual(P.load_project(self.slug)["default_team"], "")

    def test_check_teams_rejects_non_list(self):
        for bad in (5, None, "x", {"name": "a"}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                P.check_teams(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
