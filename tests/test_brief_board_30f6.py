"""决策 20260929-181437-30f6 · brief 落地第 5 步：看板提醒线（后端一半）。

settings.json 的 brief_alert_ratio 可配，缺失 / 非法 / 越界回默认 0.5，随 /api/state 下发，settings.save 带校验。
（看板的统计与提醒在前端算，见 tests/smoke_brief_board_30f6.mjs。）
全部在临时目录里跑，不碰真实 console/ peers/ inbox/ outbox/ projects/ settings.json。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

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


# ======================= 第 5 步：提醒线 =======================
class TestAlertRatio(Base):
    def write_settings(self, **kw):
        with open(SET.SETTINGS, "w", encoding="utf-8") as f:
            json.dump(kw, f)

    def test_default_is_half_and_lives_in_the_settings_defaults(self):
        self.assertEqual(P.brief_alert_ratio(), 0.5)
        self.assertEqual(SET.DEFAULTS["brief_alert_ratio"], 0.5)
        self.assertEqual(SET.load()["brief_alert_ratio"], 0.5)

    def test_override_from_settings_file(self):
        for v in (0.3, 0, 1, 0.75):
            self.write_settings(brief_alert_ratio=v)
            self.assertEqual(P.brief_alert_ratio(), float(v))

    def test_bad_values_fall_back_to_default(self):
        for bad in (1.5, -0.1, "abc", None, True, False, [0.2], {"a": 1}, float("nan"), float("inf")):
            self.write_settings(brief_alert_ratio=bad)
            self.assertEqual(P.brief_alert_ratio(), 0.5, f"{bad!r} 应当回默认")
        with open(SET.SETTINGS, "w", encoding="utf-8") as f:
            f.write("{这不是 json")
        self.assertEqual(P.brief_alert_ratio(), 0.5, "settings.json 损坏也回默认，状态快照不崩")

    def test_state_carries_the_threshold_for_the_board(self):
        self.assertEqual(P._full_state()["brief_alert_ratio"], 0.5)
        self.write_settings(brief_alert_ratio=0.2)
        self.assertEqual(P._full_state()["brief_alert_ratio"], 0.2)

    def test_settings_save_validates_and_keeps_the_old_value_on_error(self):
        SET.save({"brief_alert_ratio": 0.4})
        self.assertEqual(P.brief_alert_ratio(), 0.4)
        for bad in (2, -1, "x", float("nan")):
            with self.assertRaises(ValueError, msg=repr(bad)):
                SET.save({"brief_alert_ratio": bad})
        self.assertEqual(P.brief_alert_ratio(), 0.4, "校验失败不改已存的值")


if __name__ == "__main__":
    unittest.main(verbosity=2)
