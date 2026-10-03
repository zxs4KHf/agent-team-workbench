"""导出公开版：把当前仓库里**已提交**的文件复制到一个干净目录，做隐私扫描，通过后生成只有 1 个提交的新仓库。

为什么不直接推当前仓库：开发历史里有本机路径、私有域名和误提交过的运行数据，改写历史容易漏。
公开版每次都从当前代码重新导出，开发仓库保留完整历史。

    py -3 scripts/export_public.py                         # 导出到 ../<仓库名>-public，扫描不通过就失败
    py -3 scripts/export_public.py --out D:/somewhere/pub  # 指定目录（必须不存在或为空）
    py -3 scripts/export_public.py --scan-only             # 只扫描、只报告，不导出

规则：
- 排除哪些路径：scripts/export_public.json 的 "exclude"（入库，只写通用路径）。
- 隐私扫描两层：内置通用规则（个人目录、邮箱、像密钥的长串）+ 本机私有关键词 privacy.local.txt（不入库，一行一个正则，
  # 开头为注释）。私有关键词放本机文件，是为了「哪些词算隐私」这件事本身也不进仓库。
- 有任何命中：列出 文件:行号 和命中的规则，退出码 1，不生成仓库。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

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
TEXT_EXT = {".py", ".js", ".mjs", ".css", ".html", ".md", ".json", ".txt", ".bat", ".ps1", ".vbs", ".sh", ".toml", ".yml", ".yaml", ".cfg", ".ini", ""}


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
    except OSError:
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
            continue
    return hits


def export(out: str, files: list, root: str, author: str, version: str) -> None:
    if os.path.exists(out) and os.listdir(out):
        raise SystemExit(f"目标目录不是空的：{out}（为了不覆盖任何东西，请换一个目录或先自己清空）")
    for rel in files:
        dst = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dst) or out, exist_ok=True)
        shutil.copy2(os.path.join(root, rel), dst)
    git = lambda *a: subprocess.run(["git", *a], cwd=out, capture_output=True, text=True, check=True)
    git("init", "-b", "main")
    git("add", "-A")
    name, _, email = author.partition("<")
    git("-c", f"user.name={name.strip()}", "-c", f"user.email={email.rstrip('>').strip()}", "-c", "commit.gpgsign=false",
        "commit", "-m", f"Agent 协作台 {version}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="导出公开版（新历史 + 隐私扫描）")
    ap.add_argument("--out", default="")
    ap.add_argument("--scan-only", action="store_true")
    ap.add_argument("--author", default="AgentTeamWorkbench <noreply@example.com>", help='公开仓库首个提交的作者，如 "你的名字 <you@users.noreply.github.com>"')
    a = ap.parse_args(argv)
    cfg = load_config()
    files = [f for f in tracked_files() if not excluded(f, cfg.get("exclude") or [])]
    rules = BUILTIN + private_patterns()
    hits = scan(files, ROOT, rules, allow=cfg.get("scan_allow") or [], generic_skip=cfg.get("generic_skip") or [])
    print(f"待导出 {len(files)} 个文件；隐私规则 {len(rules)} 条（其中本机私有 {len(rules) - len(BUILTIN)} 条）")
    if not os.path.exists(PRIVATE):
        print("提示：没有 privacy.local.txt，只用了内置通用规则。把你的私有关键词（域名、目录名、项目名）一行一个写进去再扫一次。")
    for rel, ln, name, frag in hits:
        print(f"  ✗ {rel}:{ln}  [{name}]  {frag}")
    if hits:
        print(f"\n隐私扫描命中 {len(hits)} 处，不导出。改掉这些内容，或把整个文件加进 scripts/export_public.json 的 exclude。")
        return 1
    print("隐私扫描通过。")
    if a.scan_only:
        return 0
    out = a.out or os.path.join(os.path.dirname(ROOT), os.path.basename(ROOT) + "-public")
    export(out, files, ROOT, a.author, cfg.get("version") or "v0.1.0")
    print(f"已导出到 {out}（1 个提交）。检查无误后：cd 到该目录，git remote add origin <你的仓库地址>，git push -u origin main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
