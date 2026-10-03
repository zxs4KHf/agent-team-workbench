#!/usr/bin/env python3
"""只读脱敏扫描器 v2：对显式指定的目录做结构化 + 模式匹配，默认只输出计数、规则 id 与行号。

本版按 20260924-023211-07fb 最终方案修订 ce941 版本，逐条对应：

- 只读。不写任何文件，不在目标目录留痕，不做「写后恢复」，不发任何网络请求。
- **默认输出绝不包含文件名、路径、原文片段、匹配值**（07fb#9）。只给：规则 id、类别、
  命中次数、行号、文件类型（扩展名）。文件名本身可能就是敏感信息。
- **fail-closed**（07fb#6、#10）：凡是无法可靠解析的东西——二进制、非 UTF-8、超大文件、
  解不开的嵌套编码、密文、未展开的模板变量——一律计入 `unknown`，退出码 3。
  **「零命中」不等于「安全」；有 unknown 时这份报告本身就不可信。**
- **项目特有 upstreams 结构**（07fb#7）：host 已经替换成占位符，同一行或同一块里残留的
  user / password / token 仍然命中。这是本项目最常见的真实泄漏形态，正则式「先看 host」
  的写法会整条漏掉。
- **短凭据与 TunnelSecret**（07fb#8）：3 字符以内的密码、`TunnelSecret` / `tunnel-secret` /
  `TUNNEL_SECRET` 等大小写与分隔符变体都要抓；同时靠「键值结构」做上下文约束，
  避免把散文里的 password / secret 普通词误报。

退出码：
    0  clean    —— 无命中且无 unknown
    1  finding  —— 有命中、无 unknown
    3  unknown  —— 存在无法可靠解析的内容（优先级最高：报告不可信时不允许报「干净」）
    2  用法错误

`--ack-unknown N`：人工已逐项复核过这 N 个 unknown 时，用**精确数字**认领。
数字对不上仍然是 3 —— 不提供「一律忽略」的开关，避免这道闸门被顺手绕过。
"""

import argparse
import base64
import binascii
import json
import os
import re
import sys

VERSION = "2.0"

# ---------------------------------------------------------------- 规则

# (类别名, 正则)。保持 2 元组形状：tests/test_redact_scan_40e0.py 里按 `for name, _ in RULES` 遍历。
RULES = [
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("credential_url", re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^/\s:@]+:[^/\s@]+@")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}")),
    ("secret_assign", re.compile(
        r"(?i)\b(password|passwd|pwd|secret|token|api[_\-]?key|access[_\-]?key"
        r"|private[_\-]?key|client[_\-]?secret|authorization)\b"
        r"\s*[:=]\s*[\"']?[^\s\"',;]{4,}")),
    ("order_id", re.compile(r"(?i)\b(?:order|订单)[\s_#\-]*(?:no\.?|id|number|#)?\s*[:#]?\s*\d{6,}")),
    ("ipv6", re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){1,7}:(?:[0-9A-Fa-f]{1,4})?\b")),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("host_port", re.compile(
        r"\b[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?)+"
        r"\.(?:com|net|org|io|xyz|top|cc|live|online|site|dev|app|tech|pro|me|info)(?::\d{1,5})\b",
        re.I)),
    # 07fb#8：TunnelSecret 的大小写 / 分隔符变体。要求键值结构，散文里出现不算。
    ("tunnel_secret", re.compile(r"(?i)\btunnel[\s._\-]*secret\b\s*[:=]\s*\S+")),
]

# 结构化规则（不走 RULES 的整文匹配，需要行上下文）
STRUCT_CATEGORIES = ["upstreams_cred", "short_secret", "nested_encoded"]
# 输出顺序里 unknown 单独成段
UNKNOWN_CATEGORIES = ["binary", "non_utf8", "too_large", "unreadable",
                      "excluded_suffix", "undecodable_blob", "template_value", "ciphertext"]

RULE_IDS = {}
for _i, (_n, _r) in enumerate(RULES, 1):
    RULE_IDS[_n] = "R%02d" % _i
for _i, _n in enumerate(STRUCT_CATEGORIES, len(RULES) + 1):
    RULE_IDS[_n] = "R%02d" % _i
for _i, _n in enumerate(UNKNOWN_CATEGORIES, 1):
    RULE_IDS[_n] = "U%02d" % _i

ALL_FINDING_CATEGORIES = [n for n, _ in RULES] + STRUCT_CATEGORIES

IPV4_IGNORE = re.compile(r"^(0\.|127\.|169\.254\.|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.|255\.)")

# ---- 结构化解析用 ----

# 进入 upstreams 块：YAML `upstreams:`、JSON `"upstreams": [`、ini `upstreams =`
UPSTREAM_ENTER = re.compile(r"""(?ix) (?:^|[\s"'{,\[]) ["']? upstreams? ["']? \s* [:=]""")
# 凭据类键（用于结构化判定，必须是 键 : 值 / 键 = 值 形态）
# 值只取到下一个分隔符为止：同一行里可能并列多个键值对（JSON / flow 风格 YAML），
# 贪婪到行尾会让第二个键值对被吞掉，正是 ce941 漏报 upstreams 同行残留的原因之一。
CRED_KEY = re.compile(
    r"""(?ix) (?:^|[\s"'{,\[-]) ["']?
        (user(?:name)?|pass(?:word|wd)?|pwd|token|secret|auth|credential|api[_\-]?key)
        ["']? \s* [:=] \s* ([^,}\]]*)""")
HOST_KEY = re.compile(r"""(?ix) (?:^|[\s"'{,\[-]) ["']? (host|server|addr(?:ess)?|endpoint) ["']? \s* [:=]""")

# 显式打码 → 视为已脱敏，clean
REDACTED_VALUE = re.compile(
    r"""(?ix) ^ ["'\s]* (?:
        <[^>]*> | \*{2,} | x{3,} | redacted | \[redacted\] | 占位 | 已脱敏 | 请填写 |
        your[_\s\-]?\w+ | example | changeme_placeholder | null | none | ~ | "" | ''
    ) ["'\s,]* $""")
# 未展开的模板变量 → 无法判定实际渲染结果，fail-closed 记 unknown
# 只认开头标记：值可能被上面的分隔符截断（${VAR} 里的 } 会被切掉），认整对会漏。
TEMPLATE_VALUE = re.compile(r"""(?x) \$\{ | \$\( | \{\{ | %\([^)]*\)s | <%""")
# 密文 / 保险库引用 → 无法解析，记 unknown
CIPHER_VALUE = re.compile(r"""(?ix) ^ ["'\s]* (?: enc\[ | ENC\( | !vault | sops: | age1 | vault: )""")
# 布尔 / 纯开关值，不算凭据
BOOLISH = re.compile(r"""(?ix) ^ ["'\s]* (?: true|false|yes|no|on|off|0|1 ) ["'\s,]* $""")

BASE64_BLOB = re.compile(r"\b[A-Za-z0-9+/]{64,}={0,2}\b")
HEX_BLOB = re.compile(r"\A[0-9a-fA-F]+\Z")

SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
               ".bz2", ".xz", ".7z", ".rar", ".exe", ".dll", ".so", ".dylib", ".pyc",
               ".sqlite", ".sqlite3", ".db", ".woff", ".woff2", ".ttf", ".otf", ".mp4",
               ".mp3", ".pyd", ".node", ".jar", ".class"}

DEFAULT_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}

DISCLAIMER = (
    "声明：本工具只做正则与有限的结构化匹配，存在漏报。计数为 0 仅表示未命中已知模式，"
    "不等于目录中没有秘密——零命中不等于安全。存在 unknown 时，这份报告本身不可信，"
    "必须人工逐项复核。外发、出包或提交前一律需要人工复核。")


def _clean_value(raw):
    """取键值对右侧的值：去掉行尾注释与包裹引号。"""
    v = raw.strip()
    for cut in (" #", "\t#", " //"):
        i = v.find(cut)
        if i > 0:
            v = v[:i].strip()
    v = v.rstrip(",")
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v.strip()


def _value_kind(value):
    """把值分成 empty / redacted / template / cipher / boolish / real。"""
    if value == "":
        return "empty"
    if REDACTED_VALUE.match(value):
        return "redacted"
    if TEMPLATE_VALUE.search(value):
        return "template"
    if CIPHER_VALUE.match(value):
        return "cipher"
    if BOOLISH.match(value):
        return "boolish"
    return "real"


def _decode_blob(blob):
    """尝试解开一段疑似 base64。返回 (明文, 'ok') / (None, 'undecodable')。"""
    if HEX_BLOB.match(blob):
        return None, "undecodable"          # 十六进制摘要：不是编码载体，也解不出结构
    pad = blob + "=" * (-len(blob) % 4)
    try:
        raw = base64.b64decode(pad, validate=True)
    except (binascii.Error, ValueError):
        return None, "undecodable"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "undecodable"
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
    if not text or printable / len(text) < 0.9:
        return None, "undecodable"
    return text, "ok"


def scan_text(text, _depth=0):
    """返回 (findings, unknowns)。

    findings: [(类别, 行号)]；unknowns: [(原因, 行号)]。行号从 1 起，嵌套解码出的内容
    沿用宿主行号。绝不返回原文片段或匹配值。"""
    findings, unknowns = [], []
    lines = text.splitlines()

    # 1) 先做结构化判定：同一行里凭据值到底是真值、显式打码、模板还是密文。
    #    这一步的结论会反过来抑制第 2 步的正则误报（`password: <REDACTED>` 不是泄漏）。
    in_block, block_indent = False, -1
    real_lines, cred_lines = set(), set()
    for ln, line in enumerate(lines, 1):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        entering = bool(UPSTREAM_ENTER.search(line))
        if entering:
            in_block, block_indent = True, indent
        elif in_block and stripped and indent <= block_indent \
                and not stripped.startswith(("-", "}", "]")):
            in_block = False
        same_line_host = bool(HOST_KEY.search(line))
        for m in CRED_KEY.finditer(line):
            key = m.group(1).lower()
            value = _clean_value(m.group(2))
            kind = _value_kind(value)
            cred_lines.add(ln)
            if kind == "template":
                unknowns.append(("template_value", ln))
                continue
            if kind == "cipher":
                unknowns.append(("ciphertext", ln))
                continue
            if kind != "real":
                continue
            real_lines.add(ln)
            # 07fb#7：host 打码了也要抓同块 / 同行的凭据残留
            if in_block or same_line_host:
                findings.append(("upstreams_cred", ln))
            # 07fb#8：secret_assign 要求值 >=4 字符，1-3 字符的真密码会整条漏掉
            if re.match(r"pass|pwd|secret|token", key) and 1 <= len(value) <= 3:
                findings.append(("short_secret", ln))

    # 2) 逐行跑正则规则（按行才能给出行号）。凭据值已被显式打码的行不再重复报。
    suppressed = {ln for ln in cred_lines if ln not in real_lines}
    for ln, line in enumerate(lines, 1):
        for name, rx in RULES:
            if name in ("secret_assign", "tunnel_secret") and ln in suppressed:
                continue
            if name == "ipv4":
                n = sum(1 for m in rx.finditer(line) if not IPV4_IGNORE.match(m.group(0)))
            else:
                n = len(rx.findall(line))
            findings.extend([(name, ln)] * n)

    # 3) 嵌套编码：能解开就往里再扫一层；解不开一律 unknown（不宣称安全）
    if _depth == 0:
        for ln, line in enumerate(lines, 1):
            for blob in BASE64_BLOB.findall(line):
                inner, status = _decode_blob(blob)
                if status != "ok":
                    unknowns.append(("undecodable_blob", ln))
                    continue
                sub_f, sub_u = scan_text(inner, _depth + 1)
                if sub_f:
                    findings.append(("nested_encoded", ln))
                unknowns.extend([(r, ln) for r, _ in sub_u])

    return findings, unknowns


def classify(text):
    """兼容旧接口：只返回 {类别: 次数}。"""
    findings, _ = scan_text(text)
    counts = {}
    for name, _ln in findings:
        counts[name] = counts.get(name, 0) + 1
    return counts


def is_probably_binary(head):
    if b"\x00" in head:
        return True
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def scan_file(path, max_bytes):
    """返回 (findings, unknowns, reason)。reason 非空表示本文件没能完整文本扫描。"""
    try:
        st = os.stat(path)
    except OSError:
        return [], [], "unreadable"
    if not os.path.isfile(path):
        return [], [], "not_file"
    if st.st_size > max_bytes:
        return [], [], "too_large"
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
        if is_probably_binary(head):
            return [], [], "binary"
        with open(path, "r", encoding="utf-8", errors="strict") as f:
            text = f.read()
    except UnicodeDecodeError:
        return [], [], "non_utf8"
    except (OSError, ValueError):
        return [], [], "unreadable"
    findings, unknowns = scan_text(text)
    return findings, unknowns, ""


def scan_dir(root, max_bytes, exclude_dirs, follow_links=False):
    root_real = os.path.realpath(root)
    counts, lines_by_rule = {}, {}
    unknown_counts, unknown_lines = {}, {}
    exts = set()
    # 主动拒绝（安全策略，不是「解析不了」）：不影响退出码，但必须报出来
    rejected = {"symlink": 0, "escape": 0, "not_file": 0}
    scanned = 0

    def add(bucket, lines_map, name, ln):
        bucket[name] = bucket.get(name, 0) + 1
        lines_map.setdefault(name, []).append(ln)

    for dirpath, dirnames, filenames in os.walk(root_real, followlinks=follow_links):
        dirnames[:] = [d for d in dirnames
                       if d not in exclude_dirs and not os.path.islink(os.path.join(dirpath, d))]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            if os.path.islink(p):
                rejected["symlink"] += 1
                continue
            real = os.path.realpath(p)
            if real != root_real and not real.startswith(root_real + os.sep):
                rejected["escape"] += 1
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in SKIP_SUFFIX:
                # 二进制载体里可能就藏着秘密，扫不了就不能算干净 → unknown
                add(unknown_counts, unknown_lines, "excluded_suffix", 0)
                exts.add(ext or "(无扩展名)")
                continue
            findings, unknowns, reason = scan_file(p, max_bytes)
            if reason == "not_file":
                rejected["not_file"] += 1
                continue
            if reason:
                add(unknown_counts, unknown_lines, reason, 0)
                exts.add(ext or "(无扩展名)")
                continue
            scanned += 1
            if findings or unknowns:
                exts.add(ext or "(无扩展名)")
            for name, ln in findings:
                add(counts, lines_by_rule, name, ln)
            for name, ln in unknowns:
                add(unknown_counts, unknown_lines, name, ln)

    # 旧字段 skipped 保留：老测试与老脚本按它读跳过统计
    skipped = {"symlink": rejected["symlink"], "escape": rejected["escape"],
               "not_file": rejected["not_file"],
               "binary": unknown_counts.get("binary", 0),
               "too_large": unknown_counts.get("too_large", 0),
               "unreadable": unknown_counts.get("unreadable", 0),
               "non_utf8": unknown_counts.get("non_utf8", 0),
               "excluded_suffix": unknown_counts.get("excluded_suffix", 0)}
    return {"scanned": scanned, "rejected": rejected, "skipped": skipped,
            "counts": counts, "lines": {k: sorted(set(v)) for k, v in lines_by_rule.items()},
            "unknown": unknown_counts,
            "unknown_lines": {k: sorted(set(v)) for k, v in unknown_lines.items()},
            "types": sorted(exts)}


def verdict(result, ack_unknown):
    """0 clean / 1 finding / 3 unknown。unknown 优先级最高：报告不可信时不许报干净。"""
    n_unknown = sum(result["unknown"].values())
    n_find = sum(result["counts"].values())
    if n_unknown and n_unknown != ack_unknown:
        return 3
    return 1 if n_find else 0


def _lines_str(lines, cap=20):
    if not lines or lines == [0]:
        return "-"
    shown = [str(x) for x in lines[:cap] if x]
    more = "" if len(lines) <= cap else " …(+%d)" % (len(lines) - cap)
    return (",".join(shown) or "-") + more


def render(result, root, as_json, rc, reveal=False, show_root=False):
    if as_json:
        out = {k: v for k, v in result.items()}
        out["version"] = VERSION
        out["disclaimer"] = DISCLAIMER
        out["exit_code"] = rc
        out["rule_ids"] = RULE_IDS
        if show_root or reveal:
            out["root"] = root
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print("redact_scan %s · 只读 · 默认不输出文件名/路径/原文片段/匹配值" % VERSION)
    if show_root or reveal:
        print("扫描目录：%s" % root)
    print("已扫描文件：%d" % result["scanned"])
    rj = result["rejected"]
    print("主动拒绝：%d（符号链接 %d / 越界 %d / 非普通文件 %d）"
          % (sum(rj.values()), rj["symlink"], rj["escape"], rj["not_file"]))
    sk = result["skipped"]
    print("跳过合计：%d（符号链接 %d / 越界 %d / 二进制 %d / 超大 %d / 不可读 %d / 非普通文件 %d / 排除后缀 %d）"
          % (sum(sk.values()), sk["symlink"], sk["escape"], sk["binary"], sk["too_large"],
             sk["unreadable"], sk["not_file"], sk["excluded_suffix"]))
    print("涉及文件类型：%s" % ("、".join(result["types"]) if result["types"] else "-"))
    print("类别计数：")
    if result["counts"]:
        for name in ALL_FINDING_CATEGORIES:
            if name in result["counts"]:
                print("  %-4s %-18s %4d  行号 %s"
                      % (RULE_IDS[name], name, result["counts"][name],
                         _lines_str(result["lines"].get(name, []))))
        print("  %-4s %-18s %4d" % ("", "合计", sum(result["counts"].values())))
    else:
        print("  （无命中）")
    if result["unknown"]:
        print("未知（fail-closed：这份报告不可信，必须人工复核）：")
        for name in UNKNOWN_CATEGORIES:
            if name in result["unknown"]:
                print("  %-4s %-18s %4d  行号 %s"
                      % (RULE_IDS[name], name, result["unknown"][name],
                         _lines_str(result["unknown_lines"].get(name, []))))
        print("  %-4s %-18s %4d" % ("", "unknown 合计", sum(result["unknown"].values())))
    print("退出码：%d（0=clean / 1=finding / 3=unknown）" % rc)
    print(DISCLAIMER)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="只读脱敏扫描器：默认只输出规则 id、类别计数、行号与文件类型。")
    ap.add_argument("--dir", required=True, help="要扫描的目录，必须显式指定")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, help="单文件上限，默认 2 MiB")
    ap.add_argument("--exclude-dir", action="append", default=[], help="额外排除的目录名，可重复")
    ap.add_argument("--follow-links", action="store_true", help="跟随符号链接（默认拒绝，有越界风险）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument("--ack-unknown", type=int, default=-1, metavar="N",
                    help="人工已逐项复核过恰好 N 个 unknown；数字对不上仍然退出 3")
    ap.add_argument("--show-root", action="store_true", help="额外打印扫描根目录（默认不打印）")
    ap.add_argument("--unsafe-reveal", action="store_true",
                    help="【仅限本机排查】打印根目录。本工具任何模式都不打印匹配值与片段")
    a = ap.parse_args(argv)

    root = a.dir
    if not os.path.isdir(root):
        print("错误：--dir 不是一个目录或不存在：%s" % root, file=sys.stderr)
        return 2
    exclude = set(DEFAULT_EXCLUDE_DIRS) | set(a.exclude_dir)
    result = scan_dir(root, a.max_bytes, exclude, a.follow_links)
    rc = verdict(result, a.ack_unknown)
    render(result, os.path.abspath(root), a.json, rc,
           reveal=a.unsafe_reveal, show_root=a.show_root)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
