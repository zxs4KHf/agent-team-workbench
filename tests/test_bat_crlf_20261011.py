"""批处理文件必须是 CRLF：cmd 读 LF 换行的 UTF-8 .bat 会把行切错，tools/grant_admin.bat 曾因此每行报错后闪退（2026-10-11）。"""
import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class BatLineEndings(unittest.TestCase):
    def test_tracked_bat_files_are_crlf(self):
        out = subprocess.run(["git", "ls-files", "*.bat", "*.cmd"], cwd=ROOT, capture_output=True, text=True).stdout.split()
        # 不钉 tools/grant_admin.bat：公开版导出不带这台机器专用的授权脚本，GitHub CI 上它不在
        if not out:
            self.skipTest("没有批处理文件")
        for rel in out:
            with open(os.path.join(ROOT, rel), "rb") as f:
                data = f.read()
            self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), f"{rel} 有不是 CRLF 的换行")

    def test_gitattributes_pins_crlf(self):
        with open(os.path.join(ROOT, ".gitattributes"), encoding="utf-8") as f:
            self.assertIn("*.bat text eol=crlf", f.read())


if __name__ == "__main__":
    unittest.main()
