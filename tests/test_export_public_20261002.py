"""v0.1 去个人化（2026-10-02）：导出公开版的隐私扫描与新历史、隧道改为本机可选配置、MCP 对外主机名不写死、前端不写死本机目录。
只用临时目录。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import export_public as X  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class Export(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wb-export-")

    def test_exclude_matches_dirs_and_files(self):
        pats = ["docs/history/", "CHANGELOG.md"]
        self.assertTrue(X.excluded("docs/history/a.md", pats))
        self.assertTrue(X.excluded("CHANGELOG.md", pats))
        self.assertFalse(X.excluded("docs/historyx.md", pats))
        self.assertFalse(X.excluded("docs/CHANGELOG.md", pats))

    def test_builtin_rules_catch_personal_dirs_and_emails(self):
        write(os.path.join(self.tmp, "a.md"), "路径 C:\\Users\\someone\\x\n邮箱 me@corp.cn\n正常 you@example.com\n")
        hits = X.scan(["a.md"], self.tmp, X.BUILTIN)
        self.assertEqual(sorted(h[2] for h in hits), ["个人目录（Windows）", "邮箱"])

    def test_private_keywords_apply_even_in_test_fixtures(self):
        write(os.path.join(self.tmp, "tests", "t.py"), "fake = 'a@b.cn'\nproj = 'SecretProj'\n")
        rules = X.BUILTIN + [("本机私有关键词", "SecretProj")]
        hits = X.scan(["tests/t.py"], self.tmp, rules, generic_skip=["tests/"])
        self.assertEqual([(h[1], h[2]) for h in hits], [(2, "本机私有关键词")], "测试样本跳过通用规则，但私有关键词照扫")

    def test_private_pattern_file(self):
        p = os.path.join(self.tmp, "privacy.local.txt")
        write(p, "# 注释\nfoo\n\n(?i)bar\n")
        self.assertEqual([r for _, r in X.private_patterns(p)], ["foo", "(?i)bar"])
        self.assertEqual(X.private_patterns(os.path.join(self.tmp, "none.txt")), [])

    def test_export_makes_single_commit_and_refuses_non_empty_target(self):
        src = os.path.join(self.tmp, "src")
        write(os.path.join(src, "a.txt"), "hello\n")
        write(os.path.join(src, "d", "b.txt"), "world\n")
        out = os.path.join(self.tmp, "out")
        X.export(out, ["a.txt", "d/b.txt"], src, "Tester <t@example.com>", "v0.1.0")
        log = subprocess.run(["git", "log", "--format=%an|%s"], cwd=out, capture_output=True, text=True).stdout.strip().splitlines()
        self.assertEqual(log, ["Tester|Agent 协作台 v0.1.0"])
        with self.assertRaises(SystemExit):
            X.export(out, ["a.txt"], src, "T <t@example.com>", "v0.1.0")

    def test_repo_config_excludes_internal_docs(self):
        cfg = X.load_config()
        for p in ("docs/history/", "docs/reviews/", "CHANGELOG.md", "tools/jihua/"):
            self.assertIn(p, cfg["exclude"])


class NoHardcodedPersonalSetup(unittest.TestCase):
    def test_guard_tunnel_only_with_local_config(self):
        import bus_guard as G
        self.assertFalse({"cloudflared", "cf_replica", "tunnel"} & set(G.SERVICES) and not G.TUNNEL)
        d = tempfile.mkdtemp()
        p = os.path.join(d, "g.json")
        write(p, json.dumps({"tunnel": {"public_url": "https://t.example.org"}}))
        self.assertEqual(G.tunnel_config(p)["public_url"], "https://t.example.org")
        write(p, json.dumps({"tunnel": {"public_url": "http://plain.example.org"}}))
        self.assertEqual(G.tunnel_config(p), {}, "只认 https 公网地址")
        self.assertEqual(G.tunnel_config(os.path.join(d, "none.json")), {})

    def test_mcp_public_hosts_come_from_bridge_or_env(self):
        import bus_mcp as M
        d = tempfile.mkdtemp()
        write(os.path.join(d, "console", "bridge.json"), json.dumps({"url": "https://relay.example.org/mcp/x"}))
        old, M.HERE = M.HERE, d
        try:
            os.environ["BUS_PUBLIC_HOST"] = "a.example.net, localhost"
            self.assertEqual(M.public_hosts(), ["a.example.net", "relay.example.org"])
        finally:
            M.HERE = old
            os.environ.pop("BUS_PUBLIC_HOST", None)

    def test_frontend_has_no_machine_paths(self):
        web = os.path.join(ROOT, "web", "v2", "js")
        for f in os.listdir(web):
            if f.endswith(".js"):
                src = open(os.path.join(web, f), encoding="utf-8").read()
                self.assertNotRegex(src, r"[A-Za-z]:\\\\[A-Za-z]", f"{f} 里写死了本机盘符路径")
                self.assertNotIn("D:/", src, f)
        model = open(os.path.join(web, "model.js"), encoding="utf-8").read()
        self.assertIn("S().workspace_root", model)

    def test_start_scripts_are_relative(self):
        for f in os.listdir(ROOT):
            if f.endswith((".bat", ".vbs")):
                src = open(os.path.join(ROOT, f), encoding="utf-8", errors="replace").read()
                self.assertNotRegex(src, r"[A-Za-z]:\\(?:vibecoding|Users)", f)


if __name__ == "__main__":
    unittest.main()
