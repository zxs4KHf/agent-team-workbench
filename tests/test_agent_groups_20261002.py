"""Agent 分组由工作台自己管（2026-10-02）。跑法：py -3 tests/test_agent_groups_20261002.py

以前组名单从 Arena 拉：组名 / 「最强、常用」标签在 Agent 页改不了，Arena 没有的模型（Sonnet）归不了组、进不了 Reviewer 组。
"""
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_core as B             # noqa: E402
import bus_agent_groups as AG    # noqa: E402

ARENA = {"groups": [{"id": "claude-opus-5", "name": "Claude Opus 5", "tier": "strong", "match": ["claude-opus-5"], "order": 1},
                    {"id": "glm-5.3", "name": "GLM 5.3", "tier": "daily", "match": ["glm-5.3"], "order": 2}]}


def fake_arena(*a, **k):
    return io.BytesIO(json.dumps(ARENA).encode())


class AgentGroups(Base):
    def setUp(self):
        super().setUp()
        self.roles = os.path.join(self.tmp, "console", "roles.json")
        B.write_json(self.roles, {"assignments": {"reviewer_groups": ["claude-opus-5"], "builder_groups": ["claude-opus-5", "glm-5.3"]}})
        self.arena = mock.patch("urllib.request.urlopen", side_effect=fake_arena)
        self.arena.start()
        AG._CACHE.update(mtime=None, groups=[])

    def tearDown(self):
        self.arena.stop()
        super().tearDown()

    def ids(self):
        return [g["id"] for g in AG.view()]

    def test_defaults_without_file_and_no_network(self):
        # 2026-10-02：不再向 Arena 要组表（Arena 是插件；Windows 连没人监听的端口要等约 2 秒，新装机器每次刷新卡 10 秒）
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("读分组不得发网络请求")):
            v = AG.view()
        ids = [g["id"] for g in v]
        self.assertIn("claude-opus-5", ids)
        self.assertIn("glm-5.3", ids)
        g = next(x for x in v if x["id"] == "claude-opus-5")
        self.assertTrue(g["reviewer"] and g["builder"], "默认 id 与 roles.json 对得上，角色开箱生效")
        self.assertEqual(B.group_of_model("claude-sonnet-5"), "claude-sonnet")
        self.assertEqual(B.group_of_model("glm-5.3"), "glm-5.3")

    def test_defaults_are_not_persisted_and_are_copies(self):
        v = AG.load()
        v[0]["match"].append("zzz")
        self.assertNotIn("zzz", AG.load()[0]["match"], "返回副本，调用方改了不污染缓存")
        self.assertFalse(os.path.exists(AG._path()))

    def test_rename_relabel_and_prefix_match_covers_new_version(self):
        AG.save({"id": "claude-opus-5", "name": "Claude Opus 系列", "label": "主力", "match": "claude-opus"})
        g = next(x for x in AG.view() if x["id"] == "claude-opus-5")
        self.assertEqual((g["name"], g["label"], g["match"]), ("Claude Opus 系列", "主力", ["claude-opus"]))
        self.assertEqual(B.group_of_model("claude-opus-5.5"), "claude-opus-5", "5.5 自动归进同一组")

    def test_new_sonnet_group_can_review_and_hold_agents(self):
        AG.save({"name": "Claude Sonnet 系列", "id": "claude-sonnet", "match": "sonnet\nclaude-sonnet", "reviewer": True, "builder": True})
        self.assertIn("claude-sonnet", B.read_roles()["assignments"]["reviewer_groups"])
        B.heartbeat("sonnet1", "idle", "sonnet", "", B.now_iso(), 60)
        self.assertEqual(B.peer_group("sonnet1"), "claude-sonnet", "模型名 sonnet 按新组匹配")
        self.assertEqual(B.pick_reviewers(exclude=["x"]), ["sonnet1"])

    def test_manual_group_accepts_workbench_groups(self):
        AG.save({"name": "Claude Sonnet 系列", "id": "claude-sonnet"})
        B.heartbeat("s2", "idle", "weird-model")
        B.manual_group("s2", "claude-sonnet")
        self.assertEqual(B.peer_group("s2"), "claude-sonnet")

    def test_merge_moves_match_agents_and_roles(self):
        AG.save({"name": "Opus 5.5", "id": "claude-opus-5-5", "match": "claude-opus-5-5", "reviewer": True})
        B.heartbeat("o55", "idle", "odd")
        B.manual_group("o55", "claude-opus-5-5")
        r = AG.merge("claude-opus-5-5", "claude-opus-5")
        self.assertEqual(r["moved_agents"], ["o55"])
        self.assertNotIn("claude-opus-5-5", self.ids())
        self.assertIn("claude-opus-5-5", next(g for g in AG.view() if g["id"] == "claude-opus-5")["match"])
        self.assertEqual(B.peer_group("o55"), "claude-opus-5")
        self.assertNotIn("claude-opus-5-5", B.read_roles()["assignments"]["reviewer_groups"])

    def test_chinese_only_name_creates_group(self):
        # 2026-10-02 用户：新建分组填了「便宜模型」，报「分组需要名称」
        g = AG.save({"id": "", "name": "便宜模型", "match": "", "label": ""})
        self.assertEqual(g["name"], "便宜模型")
        self.assertTrue(g["id"])
        g2 = AG.save({"id": "", "name": "国产模型", "match": "glm, kimi"})
        self.assertEqual(g2["id"], "glm")
        g3 = AG.save({"id": "", "name": "另一组便宜的"})
        self.assertNotEqual(g3["id"], g["id"])
        with self.assertRaises(ValueError):
            AG.save({"id": "", "name": "  "})

    def test_new_group_never_overwrites_existing_same_id(self):
        before = next(x for x in AG.view() if x["id"] == "glm-5.3")["name"]
        g = AG.save({"id": "", "name": "GLM 5.3"})
        self.assertNotEqual(g["id"], "glm-5.3")
        self.assertEqual(next(x for x in AG.view() if x["id"] == "glm-5.3")["name"], before)

    def test_delete_removes_group_and_role_entries(self):
        AG.delete("glm-5.3")
        self.assertNotIn("glm-5.3", self.ids())
        self.assertNotIn("glm-5.3", B.read_roles()["assignments"]["builder_groups"])

    def test_reorder(self):
        AG.reorder(["glm-5.3", "claude-opus-5"])
        self.assertEqual(self.ids()[:2], ["glm-5.3", "claude-opus-5"], "排过的两组排最前（默认分组有十几组）")


if __name__ == "__main__":
    unittest.main()
