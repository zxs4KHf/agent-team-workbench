"""决策 20260929-181437-30f6 · 卡面简介 brief：数据层与写入校验。

brief 是唯一写给用户的一句话；summary 语义不变（给 Leader 的流水账）。
四条校验各有一个拒收用例（长度 / 禁用词 / 编号与提交号 / 与 title 相同），
每条错误信息都必须回显模板，且拒收后记录保持原样——不静默截断、不写半截。
历史记录没有 brief 字段：照常读写其它字段，不报错、不倒灌。
全部在临时目录里跑，不碰真实 console/ peers/ inbox/ outbox/ projects/。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bus_core as B  # noqa: E402
import bus_projects as P  # noqa: E402

TITLE = "示例改表 · 澄清文件落单"
GOOD = "招标方发来澄清文件，设备清单要逐条改三十多处，已做工具自动改并自检。"
# 四条校验各一个反例（都卡在各自那一条上，不互相串味）
TOO_LONG = "招标方发来澄清文件，设备清单要逐条改三十多处，已经做了一个工具自动改并且自己检查有没有改错，还要再请你过目一次才算完，另外还补了对照说明"
BANNED = "这一版把改动落地了，顺手优化了几处写法"
WITH_TASK_ID = "按 20260929-181437-30f6 的结论改完了，等你过目"
WITH_HEX = "改动已经做完，提交号 a1b2c3d4e5f，等你过目"


class TestBrief30f6(unittest.TestCase):
    def setUp(self):
        self._saved = [(m, n, getattr(m, n)) for m, names in (
            (B, ("ROOT", "PEERS", "INBOX", "OUTBOX", "LOG", "WORKSPACE")),
            (P, ("PROJECTS", "COMMANDS", "DONE", "BUILDS", "ROLES", "NUDGES", "NOTIFIED", "STALE_MARK"))) for n in names]
        self.tmp = tempfile.mkdtemp(prefix="brief30f6-")
        B.ROOT = B.WORKSPACE = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        P.PROJECTS = os.path.join(self.tmp, "projects")
        for name in ("COMMANDS", "DONE", "BUILDS"):
            setattr(P, name, os.path.join(self.tmp, "console", name.lower()))
        P.ROLES = os.path.join(self.tmp, "console", "roles.json")
        P.NUDGES = os.path.join(self.tmp, "console", "nudges.json")
        P.NOTIFIED = os.path.join(self.tmp, "console", "notified.json")
        P.STALE_MARK = os.path.join(self.tmp, "console", "stale_notified.json")
        os.makedirs(os.path.join(self.tmp, "console"), exist_ok=True)
        B.ensure_dirs()
        P.ensure()
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        self.p = P.create_project("示例", [], "independent", [], "")
        self.slug = self.p["slug"]

    def tearDown(self):
        for m, n, v in self._saved:
            setattr(m, n, v)
        P._STATE_CACHE.update({"at": 0.0, "data": None})
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rec(self, task_id):
        return next(x for x in P.decisions(self.slug) if x["task_id"] == task_id)

    def exists(self, task_id):
        return any(x["task_id"] == task_id for x in P.decisions(self.slug))

    def reject(self, task_id, brief, title=TITLE):
        """post 一条不合格的 brief，返回异常文本；顺带断言模板被回显。"""
        with self.assertRaises(ValueError) as cm:
            P.post(self.slug, task_id, "merged", title, brief=brief)
        msg = str(cm.exception)
        self.assertIn("brief 模板", msg, "错误信息必须回显模板，不能只说一句不合格")
        self.assertIn("硬上限 60 字", msg)
        return msg

    # ---- 第 1 步 数据层：能写入、能读出 ----
    def test_brief_written_and_read_back(self):
        out = P.post(self.slug, "t-ok", "merged", TITLE, "合并稿四段已核验，R2 全员一致", brief=GOOD)
        self.assertIn("decision t-ok", out)
        rec = self.rec("t-ok")
        self.assertEqual(rec["brief"], GOOD, "brief 原样落盘")
        self.assertEqual(rec["summary"], "合并稿四段已核验，R2 全员一致", "summary 语义不变，照旧收流水账")
        self.assertEqual(rec["title"], TITLE, "title 不受影响")
        # 重新读盘（不吃缓存）也在
        self.assertEqual(next(x for x in P.decisions(self.slug) if x["task_id"] == "t-ok")["brief"], GOOD)

    def test_new_record_has_brief_slot(self):
        P.upsert_decision(self.slug, "t-slot", status="draft")
        self.assertIn("brief", self.rec("t-slot"), "新建记录带 brief 字段，前端不用判 undefined")
        self.assertEqual(self.rec("t-slot")["brief"], "")

    # ---- 第 2 步 写入校验：四条各一个拒收用例 ----
    def test_reject_over_60_chars(self):
        msg = self.reject("t-long", TOO_LONG)
        self.assertIn("超长", msg)
        self.assertIn(str(len(TOO_LONG)), msg, "错误信息要报出实际字数")
        self.assertFalse(self.exists("t-long"), "拒收就是拒收：不截断、不落盘，连记录都不建")

    def test_reject_banned_word(self):
        msg = self.reject("t-banned", BANNED)
        self.assertIn("禁用词", msg)
        self.assertIn("优化", msg, "错误信息要指名是哪个词")
        self.assertFalse(self.exists("t-banned"))

    def test_reject_task_id_and_hex(self):
        msg = self.reject("t-id", WITH_TASK_ID)
        self.assertIn("20260929-181437-30f6", msg)
        self.assertIn("编号", msg)
        msg2 = self.reject("t-hex", WITH_HEX)
        self.assertIn("a1b2c3d4e5f", msg2, "7 位以上十六进制串同样拒收")
        self.assertFalse(self.exists("t-id"))
        self.assertFalse(self.exists("t-hex"))

    def test_reject_same_as_title(self):
        msg = self.reject("t-same", TITLE)
        self.assertIn("title", msg)
        # 去空白后比较：加空格也躲不过
        self.reject("t-same2", " " + TITLE + " ")
        self.assertFalse(self.exists("t-same"))

    def test_banned_words_cover_the_whole_list(self):
        for word in P.BRIEF_BANNED:
            with self.assertRaises(ValueError, msg=f"{word} 应当被拒收"):
                P.check_brief(f"这一版{word}了一下，等你过目")

    def test_60_chars_pass_61_reject(self):
        self.assertEqual(P.check_brief("改" * 60), "改" * 60, "正好 60 字放行")
        with self.assertRaises(ValueError):
            P.check_brief("改" * 61)

    def test_reject_leaves_record_untouched(self):
        P.post(self.slug, "t-keep", "merged", TITLE, "原来的流水账", brief=GOOD)
        with self.assertRaises(ValueError):
            P.post(self.slug, "t-keep", "merged", TITLE, "改过的流水账", brief=BANNED)
        rec = self.rec("t-keep")
        self.assertEqual(rec["brief"], GOOD, "被拒的 brief 不覆盖原来的")
        self.assertEqual(rec["summary"], "原来的流水账", "同一次 post 被拒时 summary 也不落盘，不写半截")

    def test_empty_brief_clears_without_error(self):
        P.post(self.slug, "t-clear", "merged", TITLE, brief=GOOD)
        P.upsert_decision(self.slug, "t-clear", brief="")
        self.assertEqual(self.rec("t-clear")["brief"], "", "显式清空允许，前端走兜底句")

    def test_validation_only_when_brief_given(self):
        # 同一条记录：先写好 brief，再用别的字段更新，不该因为历史 brief 再校验一次
        P.post(self.slug, "t-partial", "merged", TITLE, brief=GOOD)
        P.post(self.slug, "t-partial", "adopted", TITLE, "又记一笔")
        rec = self.rec("t-partial")
        self.assertEqual(rec["brief"], GOOD)
        self.assertEqual(rec["status"], "adopted")
        self.assertEqual(rec["summary"], "又记一笔")

    # ---- 第 7 步范围：历史记录不倒灌 ----
    def test_legacy_record_without_brief_is_fine(self):
        P.upsert_decision(self.slug, "t-legacy", status="merged", title=TITLE, summary="老记录的流水账")
        items = P.decisions(self.slug)
        for it in items:
            it.pop("brief", None)  # 模拟升级前落盘的老数据
        P.save_decisions(self.slug, items)
        self.assertNotIn("brief", self.rec("t-legacy"))
        # 老记录照常更新其它字段：不报错、不被补写 brief
        P.post(self.slug, "t-legacy", "adopted", TITLE, "续写流水账")
        rec = self.rec("t-legacy")
        self.assertEqual(rec["status"], "adopted")
        self.assertEqual(rec["summary"], "续写流水账")
        self.assertNotIn("brief", rec, "历史记录不批量倒灌")
        self.assertEqual(rec.get("brief", ""), "", "前端读到空值走兜底句")

    # ---- summary 零回归 ----
    def test_summary_still_writes_reads_and_is_independent(self):
        P.post(self.slug, "t-sum", "merged", TITLE, "只写摘要")
        self.assertEqual(self.rec("t-sum")["summary"], "只写摘要")
        self.assertEqual(self.rec("t-sum").get("brief", ""), "", "不写 brief 就不编一个")
        # summary 不受 brief 的禁用词表约束：流水账里继续写站内术语
        P.post(self.slug, "t-sum", "merged", TITLE, "R2 合并稿已回归，主线 commit a1b2c3d4 聚合完毕")
        self.assertIn("合并稿", self.rec("t-sum")["summary"])
        P.post(self.slug, "t-sum", brief=GOOD)
        rec = self.rec("t-sum")
        self.assertEqual(rec["brief"], GOOD)
        self.assertIn("合并稿", rec["summary"], "写 brief 不动 summary")

    def test_post_signature_accepts_brief_positionally_safe(self):
        # brief 挂在签名最后，老调用（按位置传到 decisions_needed 为止）行为不变
        P.post(self.slug, "t-pos", "merged", TITLE, "摘要", "", None, None, "", "", None, "controller", None, None)
        self.assertEqual(self.rec("t-pos")["summary"], "摘要")
        self.assertEqual(self.rec("t-pos").get("brief", ""), "")

    def test_post_explicit_empty_clears_and_omitted_keeps(self):
        # astra2 85c1 RETURN：公共 post 链路要区分「没传」和「显式空串」
        P.post(self.slug, "t-chain", "merged", TITLE, brief=GOOD)
        P.post(self.slug, "t-chain", "merged", TITLE, "只改流水账")
        self.assertEqual(self.rec("t-chain")["brief"], GOOD, "没传 brief：保持原值")
        P.post(self.slug, "t-chain", "merged", TITLE, brief="")
        self.assertEqual(self.rec("t-chain")["brief"], "", "显式空串：清空")

    def test_tool_layer_passes_empty_string_through(self):
        # bus_mcp → bus_tool 这一层以前用 `or ""` / `or None` 把空串和未传混成一个值
        import bus_tool
        src = open(bus_tool.__file__, encoding="utf-8").read()
        self.assertIn('brief=a.get("brief")))', src)
        self.assertNotIn('brief=a.get("brief") or ""', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
