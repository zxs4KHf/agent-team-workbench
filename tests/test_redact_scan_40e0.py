# 任务 20260924-010610-40e0 / 修订 20260924-023211-07fb 自测：
# 只读脱敏扫描器的正例、反例、对抗用例与错误处理，以及 handoff 文档的红线检查。
# 跑法：py -3 -X utf8 tests/test_redact_scan_40e0.py（仓库根目录）。
# 全部用临时目录与构造的假数据，不读任何生产目录 / 用户目录 / 真实配置，不发网络请求，
# 不写任何被测目录之外的东西。文中所有「凭据」都是现编的合成字符串。
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "tools", "redact_scan.py")
DOCS = os.path.join(REPO, "docs", "handoff", "ai-egress")
sys.path.insert(0, REPO)
import tools.redact_scan as RS  # noqa: E402

PY = sys.executable


def run_cli(*args):
    p = subprocess.run([PY, "-X", "utf8", TOOL] + list(args), capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rs40e0-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, rel, body):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p


# ---------------------------------------------------------------- 原有 16 条

class TestPositive(Base):
    def test_dirty_file_counts_every_category(self):
        self.write("dirty.txt", "\n".join([
            "-----BEGIN RSA PRIVATE KEY-----",
            "http://user:hunter2@example-node.com:12323",
            "Authorization: Bearer abcdefghijklmnop1234",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghij",
            'password = "sup3r-s3cret-value"',
            "Order #83203150",
            "2001:0db8:85a3::8a2e:0370:7334",
            "161.77.53.186",
            "node.example-isp.net:12323",
            "TunnelSecret: c2FtcGxlLXNlY3JldA",      # 07fb#8 新增类别，合成串
        ]))
        rc, out, _ = run_cli("--dir", self.tmp)
        self.assertEqual(rc, 1, "有命中且无 unknown 时退出码应为 1")
        for name, _ in RS.RULES:
            self.assertIn(name, out, "类别 %s 应出现在输出里" % name)
        self.assertIn("合计", out)

    def test_stdout_never_leaks_the_secret_itself(self):
        secret = "sup3r-s3cret-value-ZZZ"
        self.write("leak.txt", 'password = "%s"\n' % secret)
        rc, out, _ = run_cli("--dir", self.tmp)
        self.assertEqual(rc, 1)
        self.assertNotIn(secret, out, "stdout 不得输出命中片段")
        self.assertIn("secret_assign", out)

    def test_stdout_never_leaks_filenames(self):
        self.write("config-with-real-credentials.yaml", "password = abcdefgh1234\n")
        rc, out, _ = run_cli("--dir", self.tmp)
        self.assertEqual(rc, 1)
        self.assertNotIn("config-with-real-credentials.yaml", out)


class TestNegative(Base):
    def test_clean_file_reports_zero(self):
        self.write("clean.py", "\n".join([
            "import os", "PORT = 12323", "HOST = '127.0.0.1'", "print('hello world')"]))
        rc, out, _ = run_cli("--dir", self.tmp)
        self.assertEqual(rc, 0, "无命中时退出码应为 0")
        self.assertIn("（无命中）", out)

    def test_loopback_and_reserved_ipv4_are_not_hits(self):
        self.write("local.txt", "127.0.0.1\n0.0.0.0\n169.254.1.1\n192.0.2.10\n")
        rc, out, _ = run_cli("--dir", self.tmp)
        self.assertEqual(rc, 0, "回环与保留地址不算对外暴露的 IP")
        self.assertNotIn("ipv4", out)

    def test_bare_domain_without_port_is_not_a_node(self):
        self.write("doc.md", "参考 github.com 与 openai.com 上的文档。\n")
        rc, out, _ = run_cli("--dir", self.tmp)
        self.assertEqual(rc, 0, "不带端口的普通域名不算节点")


class TestSkipAndErrors(Base):
    def test_symlink_skipped_by_default(self):
        self.write("real.txt", "password = abcdefgh1234\n")
        link = os.path.join(self.tmp, "link.txt")
        try:
            os.symlink(os.path.join(self.tmp, "real.txt"), link)
        except (OSError, NotImplementedError):
            self.skipTest("当前环境无法创建符号链接")
        if not os.path.islink(link):
            self.skipTest("当前平台创建的链接不被 os.path.islink 识别")
        rc, out, _ = run_cli("--dir", self.tmp)
        self.assertEqual(rc, 1, "真实文件本身仍应被扫到")
        self.assertIn("符号链接 1", out)

    def test_binary_and_oversized_are_skipped(self):
        p = os.path.join(self.tmp, "blob.bin")
        with open(p, "wb") as f:
            f.write(b"\x00\x01\x02password = abcdefgh1234")
        self.write("big.txt", "password = abcdefgh1234\n" + "x" * 5000)
        rc, out, _ = run_cli("--dir", self.tmp, "--max-bytes", "1000")
        self.assertIn("二进制 1", out)
        self.assertIn("超大 1", out)

    def test_excluded_suffix_not_scanned(self):
        p = os.path.join(self.tmp, "shot.png")
        with open(p, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        rc, out, _ = run_cli("--dir", self.tmp)
        self.assertIn("排除后缀 1", out)

    def test_default_exclude_dirs(self):
        self.write("node_modules/pkg/index.js", "password = abcdefgh1234\n")
        rc, out, _ = run_cli("--dir", self.tmp)
        self.assertEqual(rc, 0, "node_modules 默认排除")

    def test_missing_dir_returns_2(self):
        rc, _, err = run_cli("--dir", os.path.join(self.tmp, "no-such-dir"))
        self.assertEqual(rc, 2)
        self.assertIn("不是一个目录或不存在", err)

    def test_dir_argument_is_required(self):
        rc, _, err = run_cli()
        self.assertEqual(rc, 2, "不显式指定目录必须拒绝")

    def test_escape_outside_root_is_skipped(self):
        outside = os.path.join(self.tmp, "..", "outside-secret.txt")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("password = abcdefgh1234\n")
        self.addCleanup(os.remove, outside)
        sub = os.path.join(self.tmp, "sub")
        os.makedirs(sub, exist_ok=True)
        link = os.path.join(sub, "escape.txt")
        try:
            os.symlink(os.path.realpath(outside), link)
        except (OSError, NotImplementedError):
            self.skipTest("当前环境无法创建符号链接")
        if not os.path.islink(link):
            self.skipTest("当前平台创建的链接不被 os.path.islink 识别")
        rc, out, _ = run_cli("--dir", sub)
        self.assertEqual(rc, 0, "根目录之外的链接不应被扫到")
        self.assertIn("符号链接 1", out)


class TestJsonOutput(Base):
    def test_json_shape_and_disclaimer(self):
        self.write("dirty.txt", "161.77.53.186\npassword = abcdefgh1234\n")
        rc, out, _ = run_cli("--dir", self.tmp, "--json")
        self.assertEqual(rc, 1)
        d = json.loads(out)
        self.assertEqual(d["version"], RS.VERSION)
        self.assertIn("counts", d)
        self.assertIn("skipped", d)
        self.assertIn("漏报", d["disclaimer"])
        self.assertGreater(d["counts"]["ipv4"], 0)


class TestClassifyUnit(Base):
    def test_classify_counts_occurrences_not_lines(self):
        text = "161.77.53.186 and 161.77.10.90 and 161.77.53.186"
        self.assertEqual(RS.classify(text)["ipv4"], 3)

    def test_scanner_writes_nothing(self):
        self.write("a.txt", "password = abcdefgh1234\n")
        before = sorted(os.listdir(self.tmp))
        run_cli("--dir", self.tmp)
        self.assertEqual(sorted(os.listdir(self.tmp)), before)


# ---------------------------------------------------------------- 新增：对抗用例

class TestUpstreamsStructure(Base):
    """07fb#7：host 已占位，同行 / 同块的 user / password / token 残留仍必须命中。"""

    def test_placeholder_host_same_line_still_hits(self):
        self.write("up.yaml", 'upstreams: [{host: "<REDACTED>", user: alice9, password: Zq7mKp2w}]\n')
        c = RS.classify(open(os.path.join(self.tmp, "up.yaml"), encoding="utf-8").read())
        self.assertGreaterEqual(c.get("upstreams_cred", 0), 2, "同行残留的 user 与 password 都要命中")

    def test_placeholder_host_in_block_still_hits(self):
        self.write("up2.yaml", "\n".join([
            "upstreams:", "  - host: <REDACTED>", "    port: 1080",
            "    user: alice9", "    password: Zq7mKp2w", "other:", "  host: <REDACTED>", ""]))
        rc, out, _ = run_cli("--dir", self.tmp)
        self.assertEqual(rc, 1)
        self.assertIn("upstreams_cred", out)

    def test_fully_redacted_upstreams_is_clean(self):
        self.write("up3.yaml", "\n".join([
            "upstreams:", "  - host: <REDACTED>", "    user: <REDACTED>",
            "    password: <REDACTED>", ""]))
        rc, out, _ = run_cli("--dir", self.tmp)
        self.assertEqual(rc, 0, "全部显式打码后应判 clean")


class TestShortAndVariantCredentials(Base):
    def test_short_password_is_caught(self):
        """secret_assign 要求值 ≥4 字符，1–3 字符的真密码会整条漏掉（07fb#8）。"""
        self.write("s.yaml", "host: <REDACTED>\npassword: ab\n")
        c = RS.classify(open(os.path.join(self.tmp, "s.yaml"), encoding="utf-8").read())
        self.assertGreaterEqual(c.get("short_secret", 0), 1)

    def test_tunnel_secret_case_and_separator_variants(self):
        for line in ["TunnelSecret: aGVsbG8x", "tunnel-secret = aGVsbG8x",
                     "TUNNEL_SECRET: aGVsbG8x", "Tunnel.Secret : aGVsbG8x"]:
            c = RS.classify(line)
            self.assertGreaterEqual(c.get("tunnel_secret", 0), 1, "漏掉变体：%s" % line)

    def test_plain_words_do_not_false_positive(self):
        """散文里的 password / secret / tunnel secret 没有键值结构，不得误报。"""
        text = ("这段文字讲 password 的管理策略，也提到 tunnel secret 的轮换，"
                "以及 token 的有效期。\nenabled: true\ndebug = false\nuse_pass: no\n")
        c = RS.classify(text)
        for k in ("secret_assign", "short_secret", "tunnel_secret", "upstreams_cred"):
            self.assertNotIn(k, c, "普通词 / 布尔值不应命中 %s" % k)


class TestFailClosed(Base):
    def test_non_utf8_file_is_unknown_not_clean(self):
        p = os.path.join(self.tmp, "weird.txt")
        with open(p, "wb") as f:
            f.write("干净的中文".encode("gbk"))          # 坏编码：解不开就不许报干净
        rc, out, _ = run_cli("--dir", self.tmp)
        self.assertEqual(rc, 3, "无法可靠解析必须 fail-closed，退出码 3")
        self.assertIn("unknown", out)

    def test_undecodable_blob_is_unknown(self):
        self.write("b.txt", "blob = " + "Zg" * 40 + "!!\n")   # 不是合法 base64
        rc, out, _ = run_cli("--dir", self.tmp)
        self.assertEqual(rc, 3)
        self.assertIn("undecodable_blob", out)

    def test_nested_encoded_secret_is_found(self):
        import base64 as _b
        inner = ("password = nested-s3cret-value-000000000000\n" * 2)
        self.write("n.txt", "data: " + _b.b64encode(inner.encode()).decode() + "\n")
        c = RS.classify(open(os.path.join(self.tmp, "n.txt"), encoding="utf-8").read())
        self.assertGreaterEqual(c.get("nested_encoded", 0), 1, "能解开的嵌套编码要往里再扫一层")

    def test_unresolved_template_value_is_unknown(self):
        self.write("t.yaml", "host: <REDACTED>\npassword: ${VAULT_PW}\n")
        rc, out, _ = run_cli("--dir", self.tmp)
        self.assertEqual(rc, 3, "未展开的模板变量无法判定渲染结果，必须 unknown")
        self.assertIn("template_value", out)

    def test_three_exit_codes_are_distinct(self):
        clean = os.path.join(self.tmp, "c"); os.makedirs(clean)
        find = os.path.join(self.tmp, "f"); os.makedirs(find)
        unk = os.path.join(self.tmp, "u"); os.makedirs(unk)
        open(os.path.join(clean, "a.txt"), "w", encoding="utf-8").write("hello world\n")
        open(os.path.join(find, "a.txt"), "w", encoding="utf-8").write("password = abcdefgh1234\n")
        open(os.path.join(unk, "a.bin2"), "wb").write(b"\x00\x01binary")
        self.assertEqual(run_cli("--dir", clean)[0], 0)
        self.assertEqual(run_cli("--dir", find)[0], 1)
        self.assertEqual(run_cli("--dir", unk)[0], 3)

    def test_ack_unknown_requires_exact_count(self):
        open(os.path.join(self.tmp, "a.bin2"), "wb").write(b"\x00\x01binary")
        self.assertEqual(run_cli("--dir", self.tmp, "--ack-unknown", "0")[0], 3, "数字对不上仍是 3")
        self.assertEqual(run_cli("--dir", self.tmp, "--ack-unknown", "2")[0], 3, "数字对不上仍是 3")
        self.assertEqual(run_cli("--dir", self.tmp, "--ack-unknown", "1")[0], 0, "精确认领后才降级")


class TestOutputRedaction(Base):
    """07fb#9：默认失败输出只给计数、规则 id、行号 / 类型。"""

    def test_default_output_has_no_path_name_or_value(self):
        self.write("very-secret-filename.yaml",
                   "\n".join(["# 第一行", "host: <REDACTED>", "password: Zq7mKp2wXX", ""]))
        rc, out, _ = run_cli("--dir", self.tmp)
        self.assertEqual(rc, 1)
        self.assertNotIn("very-secret-filename", out, "不得输出文件名")
        self.assertNotIn(self.tmp, out, "不得输出路径")
        self.assertNotIn("Zq7mKp2wXX", out, "不得输出匹配值")
        self.assertIn("R", out, "应给出规则 id")
        self.assertIn("行号 3", out, "应给出行号")
        self.assertIn(".yaml", out, "应给出文件类型")

    def test_json_output_also_redacted_by_default(self):
        self.write("very-secret-filename.yaml", "host: <REDACTED>\npassword: Zq7mKp2wXX\n")
        rc, out, _ = run_cli("--dir", self.tmp, "--json")
        self.assertEqual(rc, 1)
        self.assertNotIn("very-secret-filename", out)
        self.assertNotIn("Zq7mKp2wXX", out)
        self.assertNotIn('"root"', out, "默认不输出根目录")
        d = json.loads(out)
        self.assertIn("rule_ids", d)
        self.assertIn("lines", d)

    def test_show_root_is_opt_in(self):
        self.write("a.txt", "hello\n")
        rc, out, _ = run_cli("--dir", self.tmp, "--show-root")
        self.assertIn(os.path.abspath(self.tmp), out, "显式要求时才打印根目录")


class TestHandoffDocs(unittest.TestCase):
    """文档红线：不得推荐改系统代理，不得写失败直连降级（07fb#1、#2）。"""

    FILES = ["RECOMMENDATIONS.md", "SHADOWSOCKS-OFFICIAL.md"]
    BANNED = ["设为系统代理", "设置系统代理", "修改系统代理为", "全局代理模式",
              "回退到 DIRECT", "DIRECT 直连降级", "失败后直连", "降级为直连", "TUN 模式开启"]
    REQUIRED = ["loopback", "不修改系统代理", "第一跳", "最终出口", "未核验"]

    def _read(self, name):
        p = os.path.join(DOCS, name)
        self.assertTrue(os.path.isfile(p), "缺少文档 %s" % name)
        with open(p, encoding="utf-8") as f:
            return f.read()

    def test_docs_have_no_banned_directives(self):
        for name in self.FILES:
            body = self._read(name)
            for w in self.BANNED:
                self.assertNotIn(w, body, "%s 不得出现「%s」" % (name, w))

    def test_recommendations_cover_required_points(self):
        body = self._read("RECOMMENDATIONS.md")
        for w in self.REQUIRED:
            self.assertIn(w, body, "RECOMMENDATIONS.md 应覆盖「%s」" % w)

    def test_docs_scan_clean_with_the_tool(self):
        """文档本身不得含真实配置 / 凭据 / 订阅 / 节点列表（07fb#5）：用本工具自查。"""
        rc, out, _ = run_cli("--dir", DOCS)
        self.assertIn(rc, (0,), "handoff 文档应扫描为 clean，实际输出：\n%s" % out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
