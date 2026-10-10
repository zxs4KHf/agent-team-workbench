"""发布前检查（v0.2.0）：从旧目录结构（<工作区>/workbench/bus/）搬出来后残留的路径。接入提示词里的命令行路径、
MCP 工具说明里的合并稿位置都必须指向独立仓库里真实存在的位置，新用户照着做不能扑空。"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bus_projects as P  # noqa: E402


class ReleasePaths(unittest.TestCase):
    def test_prompt_cli_path_exists(self):
        text = P.prompt_text("peer", "p1", "m", "", "http://127.0.0.1:8791/mcp/x", "")
        paths = re.findall(r"py -3 (\S+bus_cli\.py)", text)
        self.assertTrue(paths, "提示词里有命令行备选路径")
        for p in paths:
            self.assertTrue(os.path.isfile(p), f"提示词里的 {p} 不存在")

    def test_no_legacy_layout_in_agent_facing_text(self):
        with open(os.path.join(ROOT, "bus_mcp.py"), encoding="utf-8") as f:
            mcp = f.read()
        self.assertNotIn("合并稿写入 workbench/bus/", mcp, "MCP 工具说明不能叫 Leader 往旧路径写合并稿")
        with open(os.path.join(ROOT, "PROTOCOL.md"), encoding="utf-8") as f:
            self.assertNotIn("workbench/bus/", f.read())


if __name__ == "__main__":
    unittest.main()
