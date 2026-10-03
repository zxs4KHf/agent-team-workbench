"""首次启动准备（first_run，v0.1 发布）：新克隆的仓库能自己建运行目录、生成主令牌；已有的不动。临时目录。"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import first_run as FR  # noqa: E402


class FirstRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="firstrun-")
        self.p = [mock.patch.object(FR, "HERE", self.tmp), mock.patch.object(FR, "TOKEN", os.path.join(self.tmp, "console", "mcp_token.txt"))]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def token(self):
        with open(FR.TOKEN, encoding="utf-8") as f:
            return f.read().strip()

    def test_fresh_checkout_gets_dirs_and_token(self):
        r = FR.ensure(quiet=True)
        self.assertTrue(r["token_created"])
        self.assertGreaterEqual(len(self.token()), 32, "MCP 启动要求至少 32 位")
        for d in FR.RUNTIME_DIRS:
            self.assertTrue(os.path.isdir(os.path.join(self.tmp, d)))

    def test_existing_token_is_kept(self):
        FR.ensure(quiet=True)
        first = self.token()
        self.assertFalse(FR.ensure(quiet=True)["token_created"])
        self.assertEqual(self.token(), first, "重复调用不换令牌（换了所有接入链接都会失效）")

    def test_too_short_token_is_replaced(self):
        os.makedirs(os.path.dirname(FR.TOKEN))
        with open(FR.TOKEN, "w", encoding="utf-8") as f:
            f.write("short")
        self.assertTrue(FR.ensure(quiet=True)["token_created"])
        self.assertNotEqual(self.token(), "short")

    def test_token_file_is_gitignored(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".gitignore"), encoding="utf-8") as f:
            self.assertIn("console/", f.read())


if __name__ == "__main__":
    unittest.main()
