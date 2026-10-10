"""bus_mcp 导入时算工作台相对工作区的前缀（2026-10-10 CI 实测：仓库在 D:、工作区在 C: 时导入就崩，MCP 起不来）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bus_mcp as M  # noqa: E402


class BusPrefix(unittest.TestCase):
    def test_cases(self):
        j = os.path.join
        ws = os.path.abspath(os.sep + "ws")
        self.assertEqual(M.bus_prefix(ws, ws), "")
        self.assertEqual(M.bus_prefix(j(ws, "tools", "bench"), ws), "tools/bench/")
        self.assertIsNone(M.bus_prefix(os.path.abspath(os.sep + "AgentTeamWorkbench"), ws), "工作区外：不能被剥成 AgentTeamWorkbench/")
        if os.name == "nt":
            self.assertIsNone(M.bus_prefix("D:\repo", "C:\ws"), "不同盘：不崩，返回 None")

    def test_write_deny_never_has_bare_console(self):
        if M._BUS_PREFIX is None:
            self.assertNotIn("console/", M.WRITE_DENY)
        else:
            self.assertIn(M._BUS_PREFIX + "console/", M.WRITE_DENY)


if __name__ == "__main__":
    unittest.main()
