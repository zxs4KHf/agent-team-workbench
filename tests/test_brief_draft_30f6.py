"""决策 20260929-181437-30f6 · brief 落地第 3 步：草稿 suggest_brief 与 brief_draft 一键采用。

- 草稿 = 目的短语 + 动作短语 + 状态短语，只拼结构化字段；必须过 check_brief；
  元数据缺失 / 拼出残句 / 草稿自己过不了校验，一律不出（None，卡面继续走兜底句）；
  历史记录（没有 brief 槽位）永远不出、也不倒灌；草稿不上屏、不进 state、不落盘，
  只有 Leader 带 brief_draft=true 才采用（与自己写的 brief 二选一）。
- 卡片类型规则（brief_kind_of）与前端 briefKindOf 共用 tests/brief_kinds_fixture.json（前端那一半见 smoke_brief_kinds_30f6.mjs）。
全部在临时目录里跑，不碰真实 console/ peers/ inbox/ outbox/ projects/ settings.json。
"""
import ast
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_core as B  # noqa: E402
import bus_projects as P  # noqa: E402
import bus_settings as SET  # noqa: E402

TITLE = "示例改表 · 澄清文件落单"
GOOD = "招标方发来澄清文件，设备清单要逐条改三十多处，已做工具自动改并自检。"
NEW = "招标方改了三十多处尺寸，已重新核对并改完，请你过目一遍。"
FALLBACK = "Agent 已把方案整理好了。打开看看：满意就采纳，还要改就继续讨论。"  # 兜底句只在前端有一份：这里只当「用户眼前那句话」传进来
PEERS3 = ["opus1", "glm1", "qwen1"]
FIXTURE = os.path.join(REPO, "tests", "brief_kinds_fixture.json")


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def isolate(obj, tmp):
    """把 bus_core / bus_projects / bus_settings 的落盘位置全指到临时目录，返回还原用的清单。"""
    saved = [(m, n, getattr(m, n)) for m, names in (
        (B, ("ROOT", "PEERS", "INBOX", "OUTBOX", "LOG", "WORKSPACE", "LOCK", "PRIORITY")),
        (P, ("PROJECTS", "COMMANDS", "DONE", "BUILDS", "ROLES", "NUDGES", "NOTIFIED", "STALE_MARK")),
        (SET, ("SETTINGS", "WORKSPACE_CFG"))) for n in names]
    B.ROOT = B.WORKSPACE = tmp
    B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(tmp, d) for d in ("peers", "inbox", "outbox", "log"))
    B.LOCK = os.path.join(tmp, "console", "controller.lock")
    B.PRIORITY = os.path.join(tmp, "console", "priority.json")
    P.PROJECTS = os.path.join(tmp, "projects")
    for name in ("COMMANDS", "DONE", "BUILDS"):
        setattr(P, name, os.path.join(tmp, "console", name.lower()))
    P.ROLES = os.path.join(tmp, "console", "roles.json")
    P.NUDGES = os.path.join(tmp, "console", "nudges.json")
    P.NOTIFIED = os.path.join(tmp, "console", "notified.json")
    P.STALE_MARK = os.path.join(tmp, "console", "stale_notified.json")
    SET.SETTINGS = os.path.join(tmp, "settings.json")
    SET.WORKSPACE_CFG = os.path.join(tmp, "workspace.json")
    os.makedirs(os.path.join(tmp, "console"), exist_ok=True)
    B.ensure_dirs()
    P.ensure()
    P._STATE_CACHE.update({"at": 0.0, "data": None})
    return saved


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brief30f6r-")
        self._saved = isolate(self, self.tmp)
        self.slug = P.create_project("示例", [], "independent", [], "")["slug"]

    def tearDown(self):
        for m, n, v in self._saved:
            setattr(m, n, v)
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        shutil.rmtree(self.tmp, ignore_errors=True)

    def card(self, tid, status="merged", title=TITLE, **extra):
        """造一条「新卡」：upsert_decision 新建的记录自带 brief 槽位；元数据（mode / peers）默认齐全。"""
        fields = dict(status=status, title=title, mode="discuss", peers=list(PEERS3))
        fields.update(extra)
        return P.upsert_decision(self.slug, tid, **fields)

    def rec(self, tid):
        return next(x for x in P.decisions(self.slug) if x["task_id"] == tid)

    def queued(self, kind="brief_rewrite"):
        return [c for c in P.pending_commands() if c.get("kind") == kind]


# ======================= 第 3 步：草稿 =======================
class TestSuggestBrief(Base):
    def test_draft_is_purpose_action_state_and_passes_the_write_validation(self):
        draft = P.suggest_brief(self.card("t-1"))
        self.assertEqual(draft, "关于「示例改表 · 澄清文件落单」：3 位 Agent 讨论后整理成了一份方案，等你采纳或退回。")
        self.assertEqual(P.check_brief(draft, TITLE), draft, "草稿本身必须过 9be220b 的写入校验")
        self.assertLessEqual(len(draft), P.BRIEF_MAX)
        self.assertNotEqual("".join(draft.split()), "".join(TITLE.split()), "不等于 title")

    def test_every_phrase_combination_yields_a_valid_draft(self):
        # 任何一个模板词撞上禁用词表，对应组合会静默变成「没有草稿」——这里逐个组合断言「该出就出」
        title = "改表事项"
        cases = []
        for mode in ("discuss", "independent", "redteam", "relay"):
            cases += [dict(mode=mode, peers=["a", "b"], status=s) for s in ("merged", "adopted")]
        for ex in ("pending", "released", "partial", "n-a", None):
            cases.append(dict(mode="discuss", peers=["a"], status="adopted", execution=ex))
        for mode in ("build", "review"):
            cases += [dict(mode=mode, status=s) for s in ("merged", "adopted")]
        cases += [dict(mode="discuss", status="merged", artifacts=[{"path": "x.md"}]),
                  dict(mode="discuss", status="adopted", artifacts=[{"path": "x.md"}, {"path": "y.md"}])]
        for c in cases:
            rec = {"brief": "", "title": title, **{k: v for k, v in c.items() if v is not None}}
            draft = P.suggest_brief(rec)
            self.assertIsNotNone(draft, f"该出草稿却没出：{c}")
            self.assertEqual(P.check_brief(draft, title), draft, c)
            for word in P.BRIEF_BANNED:
                self.assertNotIn(word.lower(), draft.lower(), f"{c} 的草稿含禁用词 {word}")
            self.assertTrue(draft.endswith("。") and draft.startswith("关于「"), draft)

    def test_missing_metadata_gives_no_draft(self):
        base = {"brief": "", "title": TITLE, "status": "merged", "mode": "discuss", "peers": list(PEERS3)}
        self.assertIsNotNone(P.suggest_brief(base))
        for name, patch in {
            "没有标题": {"title": ""}, "标题只有空白": {"title": "   "}, "标题只剩标点": {"title": "…—"},
            "标题只有一个字": {"title": "改"}, "标题是派发占位": {"title": "pending-20261001-141252-aa24"},
            "标题是任务号": {"title": "20261001-141252-aa24"},
            "没有 mode": {"mode": ""}, "mode 不认识": {"mode": "weird"},
            "要报人数却没有参与者": {"peers": []}, "参与者全是空串": {"peers": ["", "  "]},
            "adopted 但落地状态不认识": {"status": "adopted", "execution": "weird"},
        }.items():
            self.assertIsNone(P.suggest_brief({**base, **patch}), name)
        for status in ("draft", "dispatched", "collecting", "ready_for_merge", "rejected", "cancelled", "superseded", "archived"):
            self.assertIsNone(P.suggest_brief({**base, "status": status}), f"{status} 还没摆到用户面前，没有「等你什么」可说")
        for junk in (None, "x", 7, [], ()):
            self.assertIsNone(P.suggest_brief(junk))

    def test_fragments_and_unvalidatable_drafts_are_never_emitted(self):
        base = {"brief": "", "title": TITLE, "status": "merged", "mode": "discuss", "peers": list(PEERS3)}
        self.assertIsNone(P.suggest_brief({**base, "title": "改" * 40}), "拼出来超过 60 字：不截断、不出")
        self.assertIsNone(P.suggest_brief({**base, "title": "顺手优化了写法"}), "标题带禁用词：草稿过不了校验")
        self.assertIsNone(P.suggest_brief({**base, "title": "R2 复核"}))
        self.assertIsNone(P.suggest_brief({**base, "title": "提交 a1b2c3d4e5f 之后的改动"}), "标题带提交号")
        # 模板被改坏（占位符没填上 / 键写错 / 整句太短）也必须退回「没有草稿」，而不是把残句或异常交出去
        with mock.patch.dict(P._BRIEF_ACTION, {"discuss": "{{n}} 位 Agent 讨论"}):
            self.assertIsNone(P.suggest_brief(base))
        with mock.patch.dict(P._BRIEF_ACTION, {"discuss": "{x} 位 Agent 讨论"}):
            self.assertIsNone(P.suggest_brief(base), "KeyError 不能逃出去拖垮 post 回执")
        with mock.patch.object(P, "BRIEF_DRAFT_MIN", 200):
            self.assertIsNone(P.suggest_brief(base), "过短视为残句")

    def test_history_gets_no_draft_and_nothing_is_backfilled(self):
        for i in range(4):
            self.card(f"t-old-{i}")
        items = P.decisions(self.slug)
        for it in items:
            it.pop("brief", None)               # 模拟升级前落盘的老记录：没有 brief 槽位
        P.save_decisions(self.slug, items)
        path = os.path.join(P.project_dir(self.slug), "decisions.json")
        with open(path, encoding="utf-8") as f:
            before = f.read()
        for it in P.decisions(self.slug):
            self.assertIsNone(P.suggest_brief(it), "历史记录只走兜底句，元数据再齐也不出草稿")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before, "只读调用不得改盘")
        out = P.post(self.slug, "t-old-0", "merged", TITLE, "续写流水账")
        self.assertNotIn("草稿", out)
        self.assertNotIn("brief", self.rec("t-old-0"), "post 老记录不倒灌 brief")
        with self.assertRaises(ValueError):
            P.post(self.slug, "t-old-1", "merged", TITLE, brief_draft=True)
        self.assertNotIn("brief", self.rec("t-old-1"))

    def test_draft_is_only_a_suggestion_never_published(self):
        rec = self.card("t-pure")
        snapshot = copy.deepcopy(rec)
        draft = P.suggest_brief(rec)
        self.assertEqual(rec, snapshot, "suggest_brief 不改入参")
        out = P.post(self.slug, "t-pure", "merged", TITLE, "只写流水账")
        self.assertIn("brief 草稿", out)
        self.assertIn(draft, out, "post 回执把草稿亮给 Leader")
        self.assertIn("未发布", out)
        self.assertEqual(self.rec("t-pure")["brief"], "", "草稿不得直接发布：落盘的 brief 仍是空")
        proj = next(p for p in P._full_state()["projects"] if p["slug"] == self.slug)
        d = next(x for x in proj["decisions"] if x["task_id"] == "t-pure")
        self.assertEqual(d["brief"], "", "state 里 brief 也是空，卡面继续显示兜底句")
        self.assertFalse([k for k in d if "draft" in k], "草稿不进 state")

    def test_post_hint_only_when_there_is_a_draft_and_no_brief(self):
        out = P.post(self.slug, "t-nodata", "merged", TITLE, "x")          # post 自己不带 mode / peers：拼不出
        self.assertNotIn("草稿", out)
        self.card("t-hint")
        self.assertIn("brief_draft=true", P.post(self.slug, "t-hint", "merged", TITLE, "x"))
        self.assertNotIn("草稿", P.post(self.slug, "t-hint", brief=GOOD), "自己写了 brief 就不再提示")
        self.assertNotIn("草稿", P.post(self.slug, "t-hint", "merged", TITLE, "再记一笔"), "已有 brief 不提示")


class TestAdoptDraft(Base):
    def test_one_click_adopt_writes_exactly_the_draft_and_leaves_summary_alone(self):
        self.card("t-adopt")
        want = P.suggest_brief(self.rec("t-adopt"))
        out = P.post(self.slug, "t-adopt", "merged", TITLE, "给 Leader 的流水账", brief_draft=True)
        self.assertEqual(self.rec("t-adopt")["brief"], want)
        self.assertIn("已一键采用", out)
        self.assertEqual(self.rec("t-adopt")["summary"], "给 Leader 的流水账", "summary 语义不变")

    def test_draft_is_built_from_the_record_as_this_post_leaves_it(self):
        self.card("t-flow", status="collecting")
        self.assertIsNone(P.suggest_brief(self.rec("t-flow")), "还在收集：拼不出")
        P.post(self.slug, "t-flow", "merged", TITLE, "x", brief_draft=True)    # 这次 post 把它推到 merged
        self.assertTrue(self.rec("t-flow")["brief"].endswith("等你采纳或退回。"))

    def test_adopt_and_own_brief_are_mutually_exclusive(self):
        self.card("t-both")
        with self.assertRaises(ValueError) as cm:
            P.post(self.slug, "t-both", "merged", TITLE, "x", brief=GOOD, brief_draft=True)
        self.assertIn("二选一", str(cm.exception))
        self.assertEqual(self.rec("t-both")["brief"], "")
        self.assertEqual(self.rec("t-both")["summary"], "", "被拒的 post 不写半截")

    def test_adopt_without_a_draft_is_rejected_loudly_with_the_template(self):
        P.upsert_decision(self.slug, "t-nomode", status="merged", title=TITLE)     # 没有 mode / peers
        with self.assertRaises(ValueError) as cm:
            P.post(self.slug, "t-nomode", "merged", TITLE, brief_draft=True)
        self.assertIn("brief 模板", str(cm.exception))
        self.assertEqual(self.rec("t-nomode")["brief"], "", "拼不出就拒收，不静默写一句凑数的")

    def test_adopt_needs_a_task_id(self):
        with self.assertRaises(ValueError):
            P.post(self.slug, "", brief_draft=True)


# ======================= 工具层 / MCP 层 =======================
class TestToolLayer(unittest.TestCase):
    def test_tool_layer_passes_brief_draft_and_keeps_the_empty_string_semantics(self):
        import bus_tool
        src = read_text(bus_tool.__file__)
        self.assertIn('brief_draft=bool(a.get("brief_draft"))', src)
        self.assertIn('brief=a.get("brief")))', src, "85c1 的源码断言必须继续成立：None=未传 / 空串=清空")
        self.assertNotIn('brief=a.get("brief") or ""', src)

    def test_mcp_description_tells_the_leader_about_draft(self):
        src = read_text(os.path.join(REPO, "bus_mcp.py"))
        self.assertIn("brief_draft=true", src)
        self.assertIn("brief_draft", {a.arg for a in next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "bus_console").args.args})


class TestKindContract(unittest.TestCase):
    def test_python_rule_matches_the_fixture_the_frontend_also_reads(self):
        with open(FIXTURE, encoding="utf-8") as f:
            cases = json.load(f)["cases"]
        self.assertGreaterEqual(len(cases), 8)
        for c in cases:
            self.assertEqual(P.brief_kind_of(c["d"]), c["kind"], c["name"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
