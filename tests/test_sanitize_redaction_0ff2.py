# -*- coding: utf-8 -*-
"""20260929-185334-0ff2 返修 · arena_core.sanitize_text 的对抗输入必须打掉。

opus9 对 commit 3b7b813a 的 RETURN 给了四条「原样漏出」的输入，这里逐条钉成负向用例；
另加正向用例证明误伤可控（普通句子、含端口的本机 URL、邮箱）。只用合成假值，不碰真实凭据与运行数据。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "arena", "server"))

import arena_core as C  # noqa: E402

# 公开示例（RFC 7617 的 Aladdin/open sesame）与本仓自造的假串，不是任何真实凭据
BASIC_PAIR = "QWxhZGRpbjpvcGVuIHNlc2FtZQ=="
API_KEY_VALUE = "supersecretvalue"                     # 16 位纯字母，正好是空白分隔的取值门槛
SK_LIVE = "sk_live_abcdefghijklmnop"
DIGITS_34 = "1234567890123456789012345678901234"
DIGITS_31 = "1234567890123456789012345678901"         # 31 位：低于门槛，保留
ADVERSARIAL = (
    f"authorization=Basic {BASIC_PAIR}",
    f"X-Api-Key\t{API_KEY_VALUE}",
    f"upstream said {SK_LIVE} and stopped",
    f"retry after {DIGITS_34} seconds",
)
# sonnet1 RETURN @d4da3fe83a：scheme 词后面紧跟 - . : 时也算单词边界（\b），
# 「scheme + 空白」式负向断言被误触发，键值规则整条跳过 → 这四条值原样漏出。
SCHEME_LEAK = (
    "password=basic-hunter2",
    "secret=digest.abcdef99",
    "api_key=negotiate:ab12cd34",
    "token=Bearer-abc123xyz",
)


class TestAdversarialInputs(unittest.TestCase):
    """审查表格里那四条：脱敏后原文一个子都不许留。"""

    def test_basic_scheme_credential_is_redacted(self):
        out = C.sanitize_text(ADVERSARIAL[0])
        self.assertNotIn(BASIC_PAIR, out)
        self.assertNotIn(BASIC_PAIR[:12], out)
        self.assertIn("[已脱敏]", out)
        self.assertIn("Basic", out)                   # scheme 名保留，排障要看是哪一种认证

    def test_whitespace_separated_header_value_is_redacted(self):
        out = C.sanitize_text(ADVERSARIAL[1])
        self.assertNotIn(API_KEY_VALUE, out)
        self.assertIn("[已脱敏]", out)

    def test_underscore_prefixed_key_is_redacted(self):
        out = C.sanitize_text(ADVERSARIAL[2])
        self.assertNotIn(SK_LIVE, out)
        self.assertNotIn("abcdefghijklmnop", out)

    def test_long_pure_digit_run_is_redacted(self):
        out = C.sanitize_text(ADVERSARIAL[3])
        self.assertNotIn(DIGITS_34, out)
        self.assertIn("retry after [已脱敏] seconds", out)

    def test_hyphen_prefixed_key_still_redacted(self):
        # 老写法不许倒退（e5de/B2 那一轮钉住的形态）
        for tok in ("sk-abcdefghijklmnop", "pk-abcdefghijklmnop", "ak-abcdefghijklmnop",
                    "rk-abcdefghijklmnop", "bt-abcdefghijklmnop"):
            out = C.sanitize_text(f"got {tok} back")
            self.assertNotIn(tok, out)
            self.assertNotIn("abcdefghijklmnop", out)
            self.assertIn("got [已脱敏] back", out)

    def test_all_schemes_do_not_need_a_key_value_shape(self):
        for scheme in ("Bearer", "basic", "DIGEST", "Negotiate"):
            out = C.sanitize_text(f"Proxy-Authentication: {scheme} dXNlcjpzZWNyZXQ=")
            self.assertNotIn("dXNlcjpzZWNyZXQ=", out, scheme)


class TestSchemePrefixedValueIsStillRedacted(unittest.TestCase):
    """sonnet1 RETURN @d4da3fe83a：排除项只能是「scheme + 空白」，写成 \\b 会把 - . : 也算进去而漏掉整条值。"""

    def test_scheme_word_then_symbol_does_not_skip_the_key_value_rule(self):
        for s in SCHEME_LEAK:
            with self.subTest(case=s):
                out = C.sanitize_text(s)
                key, _, value = s.partition("=")
                self.assertNotIn(value, out, s)
                self.assertEqual(out, f"{key}=[已脱敏]", s)

    def test_scheme_then_space_is_still_rule_one_and_not_double_scanned(self):
        # 标准写法不许倒退：scheme 名留着，base64 打掉，第二次扫不动
        out = C.sanitize_text(f"authorization=Basic {BASIC_PAIR}")
        self.assertEqual(out, "authorization=Basic [已脱敏]")
        self.assertEqual(C.sanitize_text(out), out)

    def test_leak_inputs_are_idempotent(self):
        for s in SCHEME_LEAK:
            once = C.sanitize_text(s)
            self.assertEqual(C.sanitize_text(once), once, s)

    def test_plain_words_still_survive_after_the_tighter_lookahead(self):
        # 断言收紧只影响「scheme 词 + 空白」这一种排除，普通排障文案照旧原样留着
        for s in ("setup, token expired, key not found",
                  "digestion note: retry in 5s for peer glm1"):
            self.assertEqual(C.sanitize_text(s), s, s)

    def test_scheme_word_followed_by_any_word_is_rule_one_tradeoff(self):
        # 既有取舍（4f1aaf4 起）：规则 1 不看值形状，scheme 词后面紧跟的那个词一律当凭据打码，
        # 所以 "basic setup" 会被吃。这是宁过不漏的侧，本轮没改它，钉在这里防止当成新回归。
        self.assertEqual(C.sanitize_text("basic setup"), "basic [已脱敏]")


class TestFalsePositiveControl(unittest.TestCase):
    """误伤可控：这些是排障要看的原文，必须原样留着。"""

    def test_plain_words_after_keywords_survive(self):
        for s in ("token expired, please reconnect later",
                  "key not found in registry",
                  "secretariat reply is pending",
                  "connected peer claude-opus-4 status 200 wait 5s request r-12"):
            self.assertEqual(C.sanitize_text(s), s, s)

    def test_short_numbers_and_ids_survive(self):
        self.assertIn("8765", C.sanitize_text("port 8765 refused"))
        self.assertIn(DIGITS_31, C.sanitize_text(f"order {DIGITS_31} ok"))     # 31 位不达标
        self.assertNotIn(DIGITS_31 + "0", C.sanitize_text(f"order {DIGITS_31}0 ok"))

    def test_local_url_keeps_port_and_drops_path_query(self):
        out = C.sanitize_text("http://127.0.0.1:8765/api/state?token=abc")
        self.assertEqual(out, "http://127.0.0.1:8765/[已脱敏]")
        self.assertNotIn("abc", out)

    def test_url_userinfo_is_dropped_but_host_port_kept(self):
        out = C.sanitize_text("http://admin:hunter2@127.0.0.1:9091/mcp/secret?x=1")
        self.assertNotIn("hunter2", out)
        self.assertNotIn("admin", out)
        self.assertIn("127.0.0.1:9091", out)

    def test_email_is_masked(self):
        self.assertEqual(C.sanitize_text("contact ops@example.test for access"),
                         "contact [邮箱已脱敏] for access")

    def test_bare_host_without_port_is_unchanged_shape(self):
        self.assertEqual(C.sanitize_text("https://mcp.example.test"), "https://mcp.example.test")


class TestReplacedSpanIsNotRescanned(unittest.TestCase):
    """审查第 6 条：已替换的区间不能被后面的规则二次扫射。幂等就是这条的可检验形式。"""

    def test_sanitizing_twice_changes_nothing(self):
        for s in list(ADVERSARIAL) + ["http://127.0.0.1:8765/api/state?token=abc",
                                      "token expired", "contact ops@example.test",
                                      "密码：p@ss 401 no publicAccessToken"]:
            once = C.sanitize_text(s)
            self.assertEqual(C.sanitize_text(once), once, s)

    def test_marker_itself_is_never_eaten(self):
        # 「键=占位符」不该再被键值规则当成一个值吞掉
        self.assertIn("[已脱敏]", C.sanitize_text("authorization=Basic " + BASIC_PAIR))
        out = C.sanitize_text("token=abc123 password=QWxhZGRpbj secret=x-y-z cookie: aa-bb-cc")
        self.assertEqual(out.count("[已脱敏]"), 4, out)
        for secret in ("abc123", "QWxhZGRpbj", "x-y-z", "aa-bb-cc"):
            self.assertNotIn(secret, out)


class TestTruncationIsNotProtection(unittest.TestCase):
    """审查第 7 条：limit 是显示宽度，不是安全边界——凭据在 200 字之外也必须先被打掉。"""

    def test_secret_beyond_the_limit_is_redacted_not_merely_cut(self):
        s = "connected peer claude-opus-4 " * 8 + f"authorization=Basic {BASIC_PAIR}"
        full = C.sanitize_text(s, limit=10000)          # 不给截断当挡箭牌：放宽 limit 也必须打掉
        self.assertNotIn(BASIC_PAIR, full)
        self.assertIn("authorization=Basic [已脱敏]", full)
        self.assertLessEqual(len(C.sanitize_text(s, limit=60)), 60)


class TestModuleLevelImport(unittest.TestCase):
    def test_redact_url_does_not_import_per_call(self):
        import inspect
        src = inspect.getsource(C._redact_url)
        self.assertNotIn("import urllib", src)
        self.assertTrue(hasattr(C.urllib.parse, "urlsplit"))


if __name__ == "__main__":
    unittest.main()
