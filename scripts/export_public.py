"""导出公开版：从固定提交读取文件到隔离快照（不读取暂存区或工作区正文），做隐私扫描，通过后生成只有 1 个提交的新仓库。

为什么不直接推当前仓库：开发历史里有本机路径、私有域名和误提交过的运行数据，改写历史容易漏。
公开版每次都从当前代码重新导出，开发仓库保留完整历史。

    py -3 scripts/export_public.py                         # 导出到 ../<仓库名>-public，扫描不通过就失败
    py -3 scripts/export_public.py --out D:/somewhere/pub  # 指定目录（必须不存在）
    py -3 scripts/export_public.py --scan-only             # 只扫描、只报告，不导出

规则：
- 排除哪些路径：scripts/export_public.json 的 "exclude"（入库，只写通用路径）。
- 隐私扫描两层：内置通用规则（个人目录、邮箱、像密钥的长串）+ 本机私有关键词 privacy.local.txt（不入库，一行一个正则，
  # 开头为注释）。私有关键词放本机文件，是为了「哪些词算隐私」这件事本身也不进仓库。
- 有任何命中：仅列出 文件:行号 和规则名称，不回显匹配正文；退出码 1。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "scripts", "export_public.json")
PRIVATE = os.path.join(ROOT, "privacy.local.txt")

# 内置通用规则：任何人的仓库都不该公开这些
BUILTIN = [
    ("个人目录（Windows）", r"[A-Za-z]:[\\/]+Users[\\/]+(?!Public\b|Default\b|<)[^\\/\s\"'<>]+"),
    ("个人目录（macOS / Linux）", r"/(?:Users|home)/(?!runner\b|<)[a-z][\w.-]+/"),
    ("邮箱", r"\b[\w.+-]+@(?!example\.(?:com|org)|localhost|workbench\b|users\.noreply\.github\.com|anthropic\.com)[\w-]+\.[\w.-]+\b"),
    ("像密钥的串", r"(?i)(?:api[_-]?key|secret|token|password)\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{24,}"),
    ("私钥", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
TEXT_EXT = {".py", ".js", ".mjs", ".css", ".html", ".md", ".json", ".txt", ".bat", ".ps1", ".vbs", ".sh", ".toml", ".yml", ".yaml", ".cfg", ".ini", ".cjs", ".svg", ".xml", ".ts", ".tsx", ".jsx", ""}


def load_config(path: str = CONFIG) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def private_patterns(path: str = PRIVATE) -> list:
    out = []
    try:
        with open(path, encoding="utf-8-sig") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    out.append(("本机私有关键词", ln))
    except FileNotFoundError:
        pass
    return out


def tracked_files(root: str = ROOT) -> list:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
    return [f for f in out.decode("utf-8").split("\0") if f]


def excluded(rel: str, patterns: list) -> bool:
    rel = rel.replace("\\", "/")
    for p in patterns:
        p = p.replace("\\", "/")
        if p.endswith("/") and rel.startswith(p):
            return True
        if rel == p:
            return True
    return False


def scan(files: list, root: str, rules: list, allow: list = (), generic_skip: list = ()) -> list:
    """返回命中 [(文件, 行号, 规则名, 片段)]。allow：整个文件跳过（如本脚本自己的规则说明）。
    generic_skip：测试样本（假邮箱、假密钥是脱敏功能的测试数据）只跳过内置通用规则，本机私有关键词照样扫。"""
    compiled = [(name, re.compile(rx)) for name, rx in rules]
    hits = []
    for rel in files:
        expected = os.path.join(root, rel)
        try:
            if not os.path.isfile(expected):
                raise OSError("Expected snapshot file is not a regular file")
            with open(expected, "rb") as presence:
                presence.read(1)
        except OSError:
            raise OSError("Expected snapshot file is unreadable; scan did not pass") from None
        if rel.replace("\\", "/") in allow or os.path.splitext(rel)[1].lower() not in TEXT_EXT:
            continue
        rules_here = [(n, rx) for n, rx in compiled if n == "本机私有关键词"] if excluded(rel, generic_skip) else compiled
        try:
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    for name, rx in rules_here:
                        m = rx.search(line)
                        if m:
                            hits.append((rel, i, name, m.group(0)[:60]))
        except OSError:
            raise OSError("Expected snapshot file is unreadable; scan did not pass") from None
    return hits


def export(out: str, files: list, root: str, author: str, version: str) -> None:
    if os.path.lexists(out):
        raise SystemExit(f"目标目录已存在：{out}（为了不覆盖任何东西，请换一个目录或先自己清空）")
    for rel in files:
        dst = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dst) or out, exist_ok=True)
        shutil.copy2(os.path.join(root, rel), dst)
    from public_snapshot import git_env
    git = lambda *a: subprocess.run(["git", *a], cwd=out, capture_output=True, text=True, check=True, env=git_env(), timeout=60)
    git("init", "-b", "main")
    git("add", "-A")
    name, _, email = author.partition("<")
    git("-c", f"user.name={name.strip()}", "-c", f"user.email={email.rstrip('>').strip()}", "-c", "commit.gpgsign=false",
        "commit", "-m", f"Agent 协作台 {version}")
    # A snapshot contains raw blobs; the new repository must apply its checkout
    # attributes (notably BAT CRLF) before it is runnable on Windows.
    # Git can consider a freshly-added LF file stat-clean and skip conversion.
    # These files were created above in a new/empty destination and are now in
    # its commit; remove only our generated copies before the real checkout.
    for rel in files:
        os.unlink(os.path.join(out, rel))
    git("checkout-index", "--all")


def main(argv=None) -> int:
    from public_snapshot import materialize
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="固定提交导出公开版（不携带开发历史）")
    ap.add_argument("--out", default="")
    ap.add_argument("--ref", default="HEAD", help="固定提交或可解析引用；启动后立即解析为提交号")
    ap.add_argument("--private-patterns", default="", help="本机私有扫描规则，仅用于检查，不导出")
    ap.add_argument("--scan-only", action="store_true")
    ap.add_argument("--author", default="AgentTeamWorkbench <noreply@example.com>")
    a = ap.parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="wb-public-snapshot-") as tmp:
            snapshot = os.path.join(tmp, "source")
            result = materialize(ROOT, a.ref, snapshot)
            cfg, files = result["config"], list(result["files"])
            private = a.private_patterns or os.path.join(ROOT, "privacy.local.txt")
            if a.private_patterns and not os.path.isfile(private):
                raise OSError("Explicit private-pattern file is missing")
            rules = BUILTIN + private_patterns(private)
            hits = scan(files, snapshot, rules, allow=cfg.get("scan_allow") or [],
                        generic_skip=cfg.get("generic_skip") or [])
            print(f"固定提交 {result['commit']}；待导出 {len(files)} 个文件；排除 {result['excluded_count']} 个")
            if not os.path.exists(private):
                print("提示：没有本机私有规则；当前仅执行内置规则，不能据此宣称全面隐私安全。")
            for rel, ln, name, _fragment in hits:
                print(f"  命中 {rel}:{ln} [{name}]（内容已隐藏）")
            if hits:
                print(f"隐私扫描命中 {len(hits)} 处，未生成公开仓库。")
                return 1
            print("所配置隐私扫描通过；二进制素材及实际发布范围仍需人工或独立审查。")
            if a.scan_only:
                return 0
            out = a.out or os.path.join(os.path.dirname(ROOT), os.path.basename(ROOT) + "-public")
            export(out, files, snapshot, a.author, cfg.get("version") or "v0.1.0")
            print(f"已生成独立公开快照：{out}。未配置远端、未推送。")
            print("已有公开仓库应在其独立分支接纳快照并审查，保留原公开历史；不要强推替换。")
            return 0
    except (ValueError, OSError, subprocess.SubprocessError, re.error, SystemExit) as exc:
        print(f"公开导出未完成（{type(exc).__name__}）；原仓库未改动。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
