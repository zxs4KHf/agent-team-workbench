"""daily-assistant 插件：输入契约（ok 缺失 / 显式 false / 坏文件）、退出码、基线与重判语义。

只用临时目录，不读写生产 console/、peers/、inbox/、outbox/、projects/、roles/，不碰 adapters/daily_assistant.state.json，
不发网络请求（main() 在全源失败时于发请求之前退出）。
"""
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "plugins" / "daily-assistant" / "main.py"
spec = importlib.util.spec_from_file_location("da", MAIN)
N = importlib.util.module_from_spec(spec); spec.loader.exec_module(N)
# 业务日固定，并用 `--as-of` 显式传给 --check 子进程、用参数传给常驻 main()：三者必须是同一天。
#
# 历史：曾写死日期但子进程按系统日期找文件 → 次日起「找不到文件 → 连 127.0.0.1:9 重试 5 分钟」全量超时
# （2026-10-01）；改成 date.today() 后仍有跨午夜错开的窗口（sota4 审查 3e56）。
#
# 本轮（f77c 返工）把注入做成**显式参数**：`--as-of` 优先于 DAILY_ASSISTANT_TODAY 优先于系统日期。
# 为什么这能在任意时刻（含 23:59:59）跑：整个文件里**没有任何一处把两次独立的 date.today() 拿来互相比较**，
# 也没有任何断言依赖「现在是哪天」——所有涉及日期的路径都显式吃 TODAY/`--as-of`。
# 唯一需要知道系统日期的两处（回退行为）现在都先快照一次 `date.today()` 再比，不会在两次调用之间跨过午夜。
# 故意不用真实日期：注入一旦失效，下面的用例立刻红。
TODAY = dt.date(2026, 9, 29)
AS_OF = ["--as-of", TODAY.isoformat()]          # 首选：命令行显式注入
DAY_ENV = {"DAILY_ASSISTANT_TODAY": TODAY.isoformat()}   # 兼容路径，仍保留一条用例覆盖


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "collected"; self.root.mkdir()
        self.data = Path(self.tmp.name) / "data"; self.data.mkdir()

    def cfg(self, **kw):
        c = {"collected_dir": str(self.root), "sources": ["s1"], "keywords": "", "poll_sec": 10, "lookback_days": 0, "ok_missing": "fail", "fail_rounds": 3}
        c.update(kw); return c

    def write(self, source, payload, day=TODAY, raw=None, bump=0):
        p = self.root / f"{day.isoformat()}-{source}.json"
        p.write_text(raw if raw is not None else json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        t = 1_700_000_000 + bump; os.utime(p, (t, t))   # 显式 mtime，避免同秒写入不被识别为变化
        return p

    def run_check(self, cfg, args=None, env_extra=None):
        """跑 --check 子进程。默认用 `--as-of` 显式注入业务日（不靠环境变量继承）。"""
        env = dict(os.environ, WORKBENCH_PLUGIN_CONFIG=json.dumps(cfg), WORKBENCH_PLUGIN_DATA=str(self.data),
                   WORKBENCH_URL="http://127.0.0.1:9")
        env.pop("DAILY_ASSISTANT_TODAY", None)     # 确保测到的是 --as-of 这条路径，不是继承来的环境变量
        env.update(env_extra or {})
        cmd = [sys.executable, str(MAIN), "--check"] + (AS_OF if args is None else args)
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
        return r.returncode, (r.stdout + r.stderr)

    def scan(self, cfg, st=None):
        st = st or N.State(None)
        return N.scan_report(cfg, st, TODAY), st

    @staticmethod
    def item(title, **kw):
        d = {"title": title, "text": "正文 " + title}; d.update(kw); return d


class InputContract(Base):
    def test_ok_missing_default_is_collect_failed(self):
        """ok 字段缺失：默认口径 fail → collect_failed(ok_missing)，不入队，--check 退出 3。"""
        self.write("s1", {"items": [self.item("A")]})
        rep, _ = self.scan(self.cfg())
        self.assertEqual(rep["files"][f"{TODAY}-s1"][:2], ("collect_failed", "ok_missing"))
        self.assertEqual(rep["candidates"], []); self.assertEqual(rep["collect_failed"], ["s1"]); self.assertTrue(N.all_failed(rep))
        rc, out = self.run_check(self.cfg())
        self.assertEqual(rc, 3, out); self.assertIn("ok_missing", out); self.assertNotIn("正文 A", out, "--check 不打印条目正文")

    def test_ok_missing_accept_policy_is_explicit_opt_in(self):
        """只有显式 ok_missing=accept 才把缺失当成功；纯列表同口径。"""
        self.write("s1", {"items": [self.item("A")]})
        cfg = self.cfg(ok_missing="accept")
        rep, st = self.scan(cfg)
        self.assertEqual(rep["files"][f"{TODAY}-s1"][:2], ("ok", "ok_missing:baseline")); self.assertEqual(rep["ok"], ["s1"])
        self.write("s1", {"items": [self.item("A"), self.item("B")]}, bump=1)
        rep, _ = self.scan(cfg, st)
        self.assertEqual([c["title"] for c in rep["candidates"]], ["B"])
        self.assertEqual(N.classify([self.item("A")], "accept"), ("ok", "list_legacy"))
        self.assertEqual(N.classify([self.item("A")], "fail"), ("collect_failed", "ok_missing"))
        self.assertEqual(self.run_check(cfg)[0], 0)

    def test_ok_false_is_collect_failed(self):
        """ok 显式 false → collect_failed(ok_false)，不入队，--check 退出 3。"""
        self.write("s1", {"ok": False, "error": "login expired", "items": [self.item("A")]})
        rep, _ = self.scan(self.cfg())
        self.assertEqual(rep["files"][f"{TODAY}-s1"][:2], ("collect_failed", "ok_false")); self.assertEqual(rep["candidates"], [])
        rc, out = self.run_check(self.cfg()); self.assertEqual(rc, 3, out); self.assertIn("ok_false", out)

    def test_ok_false_with_unsupported_mark_is_not_a_failure(self):
        """unsupported 与 collect_failed 分离：不入队、不算失败；唯一来源 unsupported 时 --check 退出 4。"""
        for payload in ({"ok": False, "reason": "unsupported on this host"}, {"ok": False, "unsupported": True}, {"skipped": True}, {"ok": False, "error": "该来源不支持"}):
            self.assertEqual(N.classify(payload), ("unsupported", "unsupported"), payload)
        self.assertEqual(N.classify({"ok": True, "message": "unsupported fields ignored", "items": []}), ("ok", "ok_true"), "ok:true 优先")
        self.write("s1", {"ok": False, "reason": "unsupported"})
        rep, _ = self.scan(self.cfg())
        self.assertEqual(rep["unsupported"], ["s1"]); self.assertFalse(N.all_failed(rep)); self.assertEqual(rep["candidates"], [])
        rc, out = self.run_check(self.cfg()); self.assertEqual(rc, 4, out)

    def test_bad_json_and_non_object(self):
        """文件损坏 / 非 JSON / 非对象 → collect_failed，不入队，--check 退出 3。"""
        self.write("s1", None, raw="{\"ok\": true, \"items\": [")
        rep, _ = self.scan(self.cfg())
        self.assertEqual(rep["files"][f"{TODAY}-s1"][:2], ("collect_failed", "bad_json")); self.assertEqual(rep["candidates"], [])
        rc, out = self.run_check(self.cfg()); self.assertEqual(rc, 3, out); self.assertIn("bad_json", out)
        self.write("s1", None, raw="\"just a string\"", bump=1)
        rep, _ = self.scan(self.cfg())
        self.assertEqual(rep["files"][f"{TODAY}-s1"][:2], ("collect_failed", "not_object"))
        self.assertEqual(N.classify({"ok": "true"}), ("collect_failed", "ok_not_bool"))
        self.assertIsNone(N.read_items(str(self.root / f"{TODAY}-s1.json")), "旧签名：失败返回 None")

    def test_no_files_exits_4_in_check(self):
        rc, out = self.run_check(self.cfg()); self.assertEqual(rc, 4, out)

    def test_partial_failure_keeps_good_sources_and_exits_0(self):
        """只有部分来源失败：好来源照常入队，失败来源列出且下轮重读；--check 退出 0 并列出失败来源。"""
        cfg = self.cfg(sources=["good", "bad"])
        self.write("good", {"ok": True, "items": [self.item("A")]})
        self.write("bad", None, raw="not json")
        rep, st = self.scan(cfg)
        self.assertEqual(rep["ok"], ["good"]); self.assertEqual(rep["collect_failed"], ["bad"]); self.assertFalse(N.all_failed(rep))
        self.write("good", {"ok": True, "items": [self.item("A"), self.item("B")]}, bump=1)
        rep, st = self.scan(cfg, st)
        self.assertEqual([c["title"] for c in rep["candidates"]], ["B"])
        self.assertEqual(rep["files"][f"{TODAY}-bad"][0], "collect_failed", "失败文件不记 mtime，每轮重读")
        rc, out = self.run_check(cfg); self.assertEqual(rc, 0, out); self.assertIn("部分来源 collect_failed", out)

    def test_daemon_exits_nonzero_when_all_sources_fail(self):
        """常驻模式：全源 collect_failed 连续 fail_rounds 轮 → SystemExit(3)，且不写状态文件、不发请求。"""
        self.write("s1", {"ok": False, "error": "x"})
        os.environ["WORKBENCH_PLUGIN_CONFIG"] = json.dumps(self.cfg(fail_rounds=1))
        os.environ["WORKBENCH_PLUGIN_DATA"] = str(self.data)
        os.environ["WORKBENCH_URL"] = "http://127.0.0.1:9"
        os.environ.pop("DAILY_ASSISTANT_TODAY", None)   # 业务日改用参数传入，不靠环境变量
        try:
            with self.assertRaises(SystemExit) as cm:
                N.main(TODAY)                              # 常驻与 --check 吃同一个 as_of
        finally:
            for k in ("WORKBENCH_PLUGIN_CONFIG", "WORKBENCH_PLUGIN_DATA", "WORKBENCH_URL"):
                os.environ.pop(k, None)
        self.assertEqual(cm.exception.code, 3)
        self.assertFalse((self.data / "state.json").exists(), "失败退出不落状态，下次启动仍按未打基线处理")

    def test_business_day_injection(self):
        """`--as-of` 子进程按注入的业务日找文件：只给 TODAY 写了文件，系统日期那天没有——仍能读到。"""
        self.assertNotEqual(TODAY, dt.date.today(), "前置：TODAY 必须不是真实今天，才能区分注入与系统时钟")
        self.write("s1", {"ok": False, "error": "x"})
        rc, out = self.run_check(self.cfg())            # 默认就带 --as-of
        self.assertEqual(rc, 3, out)
        self.assertIn(f"{TODAY}-s1", out, "--check 必须按 --as-of 指定的那天找文件")

    def test_as_of_等号写法与环境变量兼容路径(self):
        self.write("s1", {"ok": False, "error": "x"})
        rc, out = self.run_check(self.cfg(), args=["--as-of=" + TODAY.isoformat()])
        self.assertEqual(rc, 3, out); self.assertIn(f"{TODAY}-s1", out, "--as-of=YYYY-MM-DD 同样有效")
        rc, out = self.run_check(self.cfg(), args=[], env_extra=DAY_ENV)
        self.assertEqual(rc, 3, out); self.assertIn(f"{TODAY}-s1", out, "环境变量兼容路径仍然有效")

    def test_as_of_优先于环境变量(self):
        """两者都给且互相矛盾时，命令行赢——优先级必须是确定的，否则还是「谁在钉哪天」说不清。"""
        other = TODAY - dt.timedelta(days=3)
        self.write("s1", {"ok": False, "error": "x"})                 # 只有 TODAY 那天有文件
        rc, out = self.run_check(self.cfg(), args=AS_OF, env_extra={"DAILY_ASSISTANT_TODAY": other.isoformat()})
        self.assertEqual(rc, 3, out)
        self.assertIn(f"{TODAY}-s1", out)
        self.assertNotIn(f"{other}-s1", out)
        self.assertEqual(N.business_day(as_of=TODAY), TODAY, "显式参数直接生效，不看环境")

    def test_as_of_格式错当配置错退出2(self):
        """不要静默回退：命令行写错日期是人的输入错误，必须当场报错，而不是悄悄用系统日期跑出另一天的结果。"""
        for bad in ("2026-13-40", "20260929", "2026-09-29T00:00", "", "不是日期"):
            rc, out = self.run_check(self.cfg(), args=["--as-of", bad])
            self.assertEqual(rc, 2, f"{bad!r} → {out}")
            self.assertIn("--as-of", out)

    def test_parse_day_与环境变量回退(self):
        """parse_day 只认 YYYY-MM-DD；环境变量格式错时回退系统日期。

        回退路径是唯一需要看系统日期的地方。这里用**前后夹逼**而不是 `assertEqual(x, date.today())`：
        在调用前后各取一次 today()，断言结果落在这两者之中。无论中间跨不跨午夜、跨几次，
        这个断言都恒成立且仍然有判别力（23:59:59 跑也不会假红）。
        """
        self.assertEqual(N.parse_day("2026-09-29"), TODAY)
        for bad in ("2026-13-40", "20260929", "2026-09-29T00:00", " ", "", None, "不是日期"):
            self.assertIsNone(N.parse_day(bad), repr(bad))

        def falls_back(call):
            before = dt.date.today()
            got = call()
            after = dt.date.today()
            self.assertIn(got, {before, after}, "应回退到系统日期（前后夹逼，跨午夜也成立）")

        os.environ["DAILY_ASSISTANT_TODAY"] = TODAY.isoformat()
        try:
            self.assertEqual(N.business_day(), TODAY)
            for bad in ("2026-13-40", "20260929", "2026-09-29T00:00", " "):
                os.environ["DAILY_ASSISTANT_TODAY"] = bad
                falls_back(N.business_day)
        finally:
            os.environ.pop("DAILY_ASSISTANT_TODAY", None)
        falls_back(N.business_day)
        falls_back(lambda: N.business_day(as_of="不是日期"))   # as_of 非法同样回退，不抛

    def test_as_of_from_argv(self):
        self.assertIsNone(N.as_of_from_argv([]))
        self.assertIsNone(N.as_of_from_argv(["--check"]))
        self.assertEqual(N.as_of_from_argv(["--check", "--as-of", "2026-09-29"]), TODAY)
        self.assertEqual(N.as_of_from_argv(["--as-of=2026-09-29", "--once"]), TODAY)
        for bad in (["--as-of"], ["--as-of", "x"], ["--as-of="], ["--as-of", "20260929"]):
            with self.assertRaises(ValueError, msg=bad):
                N.as_of_from_argv(bad)

    def test_常驻与check共用同一天(self):
        """同一个 as_of 同时喂给 scan_report（常驻那条路）与 --check 子进程，两边看到同一个文件名。"""
        self.write("s1", {"ok": True, "items": [self.item("A")]})
        rep = N.scan_report(self.cfg(), N.State(None), TODAY, baseline=False)
        self.assertIn(f"{TODAY}-s1", rep["files"])
        rc, out = self.run_check(self.cfg())
        self.assertIn(f"{TODAY}-s1", out)
        self.assertEqual(rc, 0, out)

    def test_bad_config_exits_2(self):
        rc, out = self.run_check(self.cfg(ok_missing="maybe")); self.assertEqual(rc, 2, out)
        rc, out = self.run_check(self.cfg(fail_rounds=0)); self.assertEqual(rc, 2, out)


class DoneAndBaseline(Base):
    def test_done_rows_never_queue(self):
        """四象限「状态=已完成」行与布尔 done/completed 不入队（复核已有实现）。"""
        self.assertTrue(N.is_done({"状态": "已完成"})); self.assertTrue(N.is_done({"fields": {"状态": " 已关闭 "}}))
        self.assertTrue(N.is_done({"status": "Done"})); self.assertTrue(N.is_done({"done": True})); self.assertFalse(N.is_done({"状态": "进行中"}))
        self.write("s1", {"ok": True, "items": []})
        rep, st = self.scan(self.cfg())                                   # 先打空基线
        self.write("s1", {"ok": True, "items": [self.item("做完的", **{"状态": "已完成"}), self.item("在做的", **{"状态": "进行中"})]}, bump=1)
        rep, _ = self.scan(self.cfg(), st)
        self.assertEqual([c["title"] for c in rep["candidates"]], ["在做的"])

    def test_baseline_is_per_source_and_waits_for_a_clean_read(self):
        """基线按来源：首次成功读到的条目只记已见不入队；读失败的来源不算已打基线，修好后再打基线，不导入历史项。"""
        cfg = self.cfg(sources=["a", "b"])
        self.write("a", {"ok": True, "items": [self.item("a1"), self.item("a2")]})
        self.write("b", None, raw="{broken")
        rep, st = self.scan(cfg)
        self.assertEqual(rep["candidates"], []); self.assertEqual(rep["baselined_now"], ["a"]); self.assertEqual(st.d["baselined"], ["a"])
        self.assertEqual(rep["files"][f"{TODAY}-a"], ("ok", "ok_true:baseline", 2))
        self.write("a", {"ok": True, "items": [self.item("a1"), self.item("a2"), self.item("a3")]}, bump=1)
        self.write("b", {"ok": True, "items": [self.item("b-old1"), self.item("b-old2")]}, bump=1)
        rep, st = self.scan(cfg, st)
        self.assertEqual([c["title"] for c in rep["candidates"]], ["a3"], "b 的历史项不导入")
        self.assertEqual(rep["baselined_now"], ["b"])
        self.write("b", {"ok": True, "items": [self.item("b-old1"), self.item("b-new")]}, bump=2)
        rep, _ = self.scan(cfg, st)
        self.assertEqual([c["title"] for c in rep["candidates"]], ["b-new"])

    def test_first_run_all_failed_leaves_no_baseline(self):
        """首次运行全源失败：不记基线、不记 mtime；修好后第一次成功读仍按基线处理。"""
        self.write("s1", {"ok": False})
        rep, st = self.scan(self.cfg())
        self.assertEqual(st.d["baselined"], []); self.assertEqual(st.d["mtimes"], {})
        self.write("s1", {"ok": True, "items": [self.item("old")]}, bump=1)
        rep, st = self.scan(self.cfg(), st)
        self.assertEqual(rep["candidates"], []); self.assertEqual(st.d["baselined"], ["s1"])

    def test_legacy_state_without_baselined_counts_as_baselined(self):
        """旧状态文件（只有 seen/mtimes）视为整体已打基线，新条目立即入队而不是再打一次基线。"""
        (self.data / "state.json").write_text(json.dumps({"seen": {}, "mtimes": {}}), encoding="utf-8")
        st = N.State(str(self.data)); self.assertEqual(st.d["baselined"], N.BASELINE_ALL)
        self.write("s1", {"ok": True, "items": [self.item("new")]})
        rep, st = self.scan(self.cfg(), st)
        self.assertEqual([c["title"] for c in rep["candidates"]], ["new"]); self.assertEqual(st.d["baselined"], ["s1"])
        st.save(); self.assertIn("baselined", json.loads((self.data / "state.json").read_text(encoding="utf-8")))


class Rejudge(Base):
    """README「基线与重判」承诺的三条触发条件与一条不触发条件。"""

    def baselined(self, **kw):
        cfg = self.cfg(**kw)
        self.write("s1", {"ok": True, "items": []})
        rep, st = self.scan(cfg)
        return cfg, st

    def test_text_change_makes_new_fingerprint(self):
        cfg, st = self.baselined()
        self.write("s1", {"ok": True, "items": [self.item("T")]}, bump=1)
        self.assertEqual(len(self.scan(cfg, st)[0]["candidates"]), 1)
        self.write("s1", {"ok": True, "items": [{"title": "T", "text": "正文改了"}]}, bump=2)
        self.assertEqual(len(self.scan(cfg, st)[0]["candidates"]), 1, "标题或正文前 200 字变了 → 新指纹 → 重新入队")

    def test_seen_expires_after_60_days(self):
        cfg, st = self.baselined()
        it = self.item("长期待办"); fp = N.fingerprint("s1", it)
        st.d["seen"][fp] = (TODAY - dt.timedelta(days=61)).isoformat()
        self.write("s1", {"ok": True, "items": [it]}, bump=1)
        self.assertEqual(len(self.scan(cfg, st)[0]["candidates"]), 1, "已见记录保留 60 天，过期后仍未完成的条目会再次入队")
        st.d["seen"][fp] = (TODAY - dt.timedelta(days=59)).isoformat()
        self.write("s1", {"ok": True, "items": [it]}, bump=2)
        self.assertEqual(len(self.scan(cfg, st)[0]["candidates"]), 0)

    def test_keyword_change_does_not_rejudge_seen_items(self):
        cfg, st = self.baselined(keywords="派给")
        self.write("s1", {"ok": True, "items": [self.item("普通笔记")]}, bump=1)
        self.assertEqual(self.scan(cfg, st)[0]["candidates"], [], "未命中关键词也记为已见")
        cfg["keywords"] = "普通"
        self.write("s1", {"ok": True, "items": [self.item("普通笔记")]}, bump=2)
        self.assertEqual(self.scan(cfg, st)[0]["candidates"], [], "改关键词不重判已见条目；要重判需删 state.json 或等 60 天过期")

    def test_delete_state_rebaselines(self):
        cfg = self.cfg()
        self.write("s1", {"ok": True, "items": [self.item("x")]})
        rep, st = self.scan(cfg, N.State(str(self.data))); st.save()
        rep, _ = self.scan(cfg, N.State(str(self.data)))
        self.assertEqual(rep["files"][f"{TODAY}-s1"][1], "unchanged")
        (self.data / "state.json").unlink()
        rep, _ = self.scan(cfg, N.State(str(self.data)))
        self.assertEqual(rep["files"][f"{TODAY}-s1"][1], "ok_true:baseline", "删掉状态文件 = 重新打基线，不导入")


if __name__ == "__main__":
    unittest.main(verbosity=2)
