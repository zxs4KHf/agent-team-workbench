# -*- coding: utf-8 -*-
"""980a：safe_workspace_path 字面优先 + 全仓单一解析 + 错误码可区分。
只用临时目录，不起服务、不碰运行状态。
    py -3 tests\\test_path_resolve_980a.py
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bus_core as B      # noqa: E402
import bus_projects as P  # noqa: E402
import bus_web as WEB     # noqa: E402

LITERAL = "workbench/bus/log/20260929/20260929-154744-f0ca.merge.md"
CANON = "log/2026-09-29/20260929-164442-0410.merge.md"
LEGACY_ONLY = "workbench/bus/log/20260929/legacy-only.merge.md"


def old_safe_workspace_path(rel: str) -> str:
    """修复前的实现，逐字照抄，只用于红绿对照。"""
    rel = str(rel or "").replace("\\", "/").lstrip("/")
    for prefix in ("workbench/bus/", "AgentTeamWorkbench/"):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
    if not os.path.exists(os.path.normpath(os.path.join(B.WORKSPACE, rel))) and os.path.exists(os.path.join(B.ROOT, rel)):
        return os.path.normpath(os.path.join(B.ROOT, rel))
    full = os.path.normpath(os.path.join(B.WORKSPACE, rel))
    if not full.startswith(os.path.normpath(B.WORKSPACE)):
        raise ValueError("路径越出工作区")
    return full


class PathResolve(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="path980a_")
        self.p = [mock.patch.object(B, "ROOT", self.d), mock.patch.object(B, "WORKSPACE", self.d)]
        for x in self.p:
            x.start()
        self.write(LITERAL, "# 字面路径的合并稿\n## 最终方案\nok\n")
        self.write(CANON, "# 规范路径的合并稿\n")
        self.write("log/20260929/legacy-only.merge.md", "# 只存在于剥前缀落点\n")
        self.write("console/mcp_token.txt", "secret")
        self.write("workspace.json", '{"extra_roots": []}')

    def tearDown(self):
        for x in self.p:
            x.stop()

    def write(self, rel, text):
        full = os.path.join(self.d, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)
        return full

    # ---- 验收 1：字面路径可读 ----
    def test_literal_path_resolves(self):
        full, src = P.resolve_workspace_path(LITERAL)
        self.assertEqual(src, "canonical")
        self.assertTrue(os.path.isfile(full))
        self.assertEqual(full, os.path.join(self.d, LITERAL.replace("/", os.sep)))
        self.assertEqual(WEB.safe_workspace_path(LITERAL), full)
        self.assertIn("最终方案", open(full, encoding="utf-8").read())

    # ---- 验收 6：红绿对照 ----
    def test_red_green_old_vs_new(self):
        old = old_safe_workspace_path(LITERAL)
        self.assertFalse(os.path.exists(old), "旧实现应解析到不存在的剥前缀落点（红）")
        self.assertTrue(os.path.isfile(WEB.safe_workspace_path(LITERAL)), "新实现应命中字面文件（绿）")
        self.assertNotEqual(old, WEB.safe_workspace_path(LITERAL))

    # ---- 验收 2：规范路径可读 ----
    def test_canonical_path_resolves(self):
        full, src = P.resolve_workspace_path(CANON)
        self.assertEqual(src, "canonical")
        self.assertTrue(os.path.isfile(full))

    # ---- 验收 3：剥前缀兼容仍在，且有 legacy_prefix 日志 ----
    def test_legacy_prefix_still_works_and_is_loud(self):
        full, src = P.resolve_workspace_path(LEGACY_ONLY)
        self.assertEqual(src, "legacy_prefix")
        self.assertTrue(os.path.isfile(full))
        self.assertTrue(full.endswith(os.path.join("log", "20260929", "legacy-only.merge.md")))
        err = io.StringIO()
        with redirect_stderr(err):
            WEB.safe_workspace_path(LEGACY_ONLY)
        self.assertIn("legacy_prefix", err.getvalue())

    def test_canonical_hit_is_silent(self):
        err = io.StringIO()
        with redirect_stderr(err):
            WEB.safe_workspace_path(LITERAL)
        self.assertNotIn("legacy_prefix", err.getvalue(), "字面命中不该打降级日志")

    # ---- 验收 4：不存在的文件 → 返回字面候选 + miss，交给 /api/file 报 404 ----
    def test_missing_file_returns_literal_and_miss(self):
        rel = "workbench/bus/log/20260929/nope.merge.md"
        full, src = P.resolve_workspace_path(rel)
        self.assertEqual(src, "miss")
        self.assertEqual(full, os.path.join(self.d, rel.replace("/", os.sep)),
                         "miss 时必须返回字面候选，报错才说得清找的是哪条路径")
        self.assertFalse(os.path.isfile(WEB.safe_workspace_path(rel)))
        # /api/file 分支的判定条件：isfile 为假即 404 + code=file_not_found
        self.assertFalse(os.path.isfile(full))

    def test_send_raw_raises_filenotfound(self):
        src = open(os.path.join(ROOT, "bus_web.py"), encoding="utf-8").read()
        self.assertIn('raise FileNotFoundError', src)
        self.assertIn('except FileNotFoundError as e:', src)
        self.assertIn('"code": "file_not_found"', src)
        self.assertIn('except PathError as e:', src)
        self.assertLess(src.index("except PathError as e:"),
                        src.index("except Exception as e:\n            return self.send_json({\"error\": str(e)}, 400)"),
                        "PathError 必须排在兜底 Exception 之前，否则又被吞成 400")

    # ---- 验收 5：安全用例 ----
    def test_traversal_rejected(self):
        with self.assertRaises(WEB.PathError) as cm:
            WEB.safe_workspace_path("../x")
        self.assertEqual((cm.exception.code, cm.exception.status), ("path_out_of_scope", 403))
        with self.assertRaises(ValueError):
            P.resolve_workspace_path("../x")

    def test_traversal_inside_rejected(self):
        with self.assertRaises(WEB.PathError):
            WEB.safe_workspace_path("log/../../x")

    def test_token_denied(self):
        with self.assertRaises(WEB.PathError) as cm:
            WEB.safe_workspace_path("console/mcp_token.txt")
        self.assertEqual((cm.exception.code, cm.exception.status), ("path_denied", 403))

    def test_token_denied_via_legacy_prefix(self):
        """兼容分支不得成为绕过拒绝名单的通道。"""
        with self.assertRaises(WEB.PathError) as cm:
            WEB.safe_workspace_path("workbench/bus/console/mcp_token.txt")
        self.assertEqual(cm.exception.code, "path_denied")

    def test_workspace_json_denied(self):
        with self.assertRaises(WEB.PathError) as cm:
            WEB.safe_workspace_path("workspace.json")
        self.assertEqual((cm.exception.code, cm.exception.status), ("path_denied", 403))

    def test_env_denied(self):
        self.write(".env", "A=1")
        with self.assertRaises(WEB.PathError) as cm:
            WEB.safe_workspace_path(".env")
        self.assertEqual((cm.exception.code, cm.exception.status), ("path_denied", 403))

    # ---- 必改 2：单一解析 ----
    def test_single_resolver(self):
        full_a = WEB.safe_workspace_path(LITERAL)
        full_b, _ = P.resolve_merge_path(LITERAL)
        self.assertEqual(full_a, full_b, "/api/file 与 resolve_merge_path 必须解析到同一个文件")
        src = open(os.path.join(ROOT, "bus_web.py"), encoding="utf-8").read()
        self.assertIn("P.resolve_workspace_path", src)
        self.assertNotIn('for prefix in ("workbench/bus/", "AgentTeamWorkbench/")', src,
                         "bus_web 里不应再留第二套剥前缀实现")

    def test_resolve_merge_path_miss(self):
        self.assertEqual(P.resolve_merge_path("log/2026-09-29/nope.md"), ("", "miss"))
        self.assertEqual(P.resolve_merge_path(""), ("", "miss"))
        self.assertEqual(P.resolve_merge_path("../x"), ("", "miss"))

    def test_directory_prefix_still_works(self):
        full = WEB.safe_workspace_path(".")
        self.assertEqual(os.path.normpath(full), os.path.normpath(self.d))


if __name__ == "__main__":
    unittest.main(verbosity=2)
