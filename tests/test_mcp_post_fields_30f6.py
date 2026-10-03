"""bus_console 的 args 字典回归：行尾注释把同一行后面的 merge_path / risks / next / note 一起注释掉了，
这四个字段经 MCP 回写会被静默丢弃。声明了的参数必须都进 args。纯源码断言，不碰任何运行数据。
"""
import ast
import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestMcpForwardsPostFields(unittest.TestCase):
    def test_console_forwards_every_parameter_it_declares(self):
        # 回归：args 字典里「brief」后面的行尾注释把同一行的 merge_path / risks / next / note 一起注释掉了，
        # 这四个字段经 MCP 回写会被静默丢弃。凡是声明了的参数都必须出现在 args 里。
        src = read_text(os.path.join(REPO, "bus_mcp.py"))
        fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "bus_console")
        keys = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Dict):
                keys |= {k.value for k in node.keys if isinstance(k, ast.Constant)}
        params = {a.arg for a in fn.args.args}
        for k in ("merge_path", "risks", "next", "note", "brief"):
            self.assertIn(k, keys, f"bus_console 没把 {k} 传下去")
        self.assertEqual(sorted(params - keys), [], "声明了却没进 args 的参数")


if __name__ == "__main__":
    unittest.main(verbosity=2)
