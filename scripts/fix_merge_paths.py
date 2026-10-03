# -*- coding: utf-8 -*-
"""盘点并（可选）回填 decisions.json 里读不到的 merge_path。默认 dry-run，只打印不写。

    py -3 scripts/fix_merge_paths.py                 # dry-run，输出 TSV + 汇总
    py -3 scripts/fix_merge_paths.py --root D:\\path\\to\\AgentTeamWorkbench
    py -3 scripts/fix_merge_paths.py --apply         # 本单不许执行；写前自动备份 .bak-<时间戳>

只读 projects/，绝不碰 console/ peers/ inbox/ outbox/。
反查优先级（命中唯一才给建议，命中多个只标 needs_review 不自动选）：
  1 bus_build 记 merged=true   2 git log --all --grep=<task_id> 或短 id   3 剥前缀后文件存在
  4 合并稿正文含「已合入/已合主线/已发布/已上线」——仅候选，不自动回填
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

LEGACY_PREFIXES = ("workbench/bus/", "AgentTeamWorkbench/")
DONE_WORDS = ("已合入", "已合主线", "已发布", "已上线")
# 已知必须人工、脚本不得猜的 3 条（合并稿从未写过 / 指向另一个 task_id 的文件）
MANUAL_ONLY = {"20260924-011750-ae70", "20260924-012742-5406", "20260928-043423-711c"}


def _utf8_stdio():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def normalize(mp: str) -> str:
    rel = str(mp or "").replace("\\", "/").strip().lstrip("/")
    for p in LEGACY_PREFIXES:
        if rel.startswith(p):
            rel = rel[len(p):]
    return re.sub(r"^log/(\d{4})(\d{2})(\d{2})/", r"log/\1-\2-\3/", rel)


def exists_under(root: str, rel: str) -> str:
    full = os.path.normpath(os.path.join(root, rel))
    return full if rel and os.path.isfile(full) else ""


def git_merge_commit(root: str, task_id: str) -> str:
    short = task_id.split("-")[-1]
    for grep in (task_id, short):
        try:
            r = subprocess.run(["git", "log", "--all", "--oneline", "--grep=" + grep],
                               capture_output=True, cwd=root, text=True,
                               encoding="utf-8", errors="replace", timeout=20)
        except Exception:  # noqa: BLE001 - 没有 git 也要能跑
            return ""
        line = ((r.stdout or "").strip().splitlines() or [""])[0]
        if line:
            return "%s (grep=%s)" % (line[:60], grep)
    return ""


def build_merged(root: str, task_id: str) -> str:
    """bus_build 的 builds/<task_id>.json 里 merged=true。"""
    p = os.path.join(root, "console", "builds", task_id + ".json")
    if not os.path.isfile(p):
        return ""
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f) or {}
    except Exception:  # noqa: BLE001
        return ""
    return "builds/%s.json merged=true" % task_id if d.get("merged") else ""


def scan_log_dir(root: str, task_id: str) -> list:
    """在 log/ 下按 task_id 找候选。**.merge.md 必须优先于 <task_id>.<peer>.md**：
    有多条记录的 merge_path 指向的是 peer 应答文件，别把应答当合并稿。"""
    hits = []
    for base in (os.path.join(root, "log"), os.path.join(root, "workbench", "bus", "log")):
        for dirpath, _dirs, files in os.walk(base) if os.path.isdir(base) else []:
            for fn in files:
                if task_id in fn:
                    hits.append(os.path.join(dirpath, fn))
    merge = [h for h in hits if h.endswith(".merge.md")]
    return merge or []          # 只认 .merge.md，peer 应答一律不作为候选


def declares_done(path: str) -> bool:
    try:
        head = open(path, encoding="utf-8", errors="ignore").read(4000)
    except OSError:
        return False
    return any(w in head for w in DONE_WORDS)


def rel_to(root: str, full: str) -> str:
    try:
        rel = os.path.relpath(full, root)
    except ValueError:
        return full
    return rel.replace("\\", "/")


def main(argv=None) -> int:
    _utf8_stdio()
    ap = argparse.ArgumentParser(description="盘点 decisions.json 的 merge_path，默认 dry-run")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--apply", action="store_true", help="回填（本单不许执行；写前自动备份）")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    projects = os.path.join(root, "projects")
    if not os.path.isdir(projects):
        print("找不到 %s" % projects, file=sys.stderr)
        return 2

    print("task_id\trule\tevidence\taction")
    n = {"ok": 0, "legacy_prefix": 0, "merge_missing": 0, "needs_review": 0, "empty": 0, "manual": 0}
    changes = []
    for slug in sorted(os.listdir(projects)):
        dec = os.path.join(projects, slug, "decisions.json")
        if not os.path.isfile(dec):
            continue
        try:
            with open(dec, encoding="utf-8") as f:
                items = json.load(f) or []
        except Exception as exc:  # noqa: BLE001
            print("%s\tread_error\t%r\tskip" % (slug, exc))
            continue
        for rec in items:
            if rec.get("status") not in ("merged", "adopted"):
                continue
            tid, mp = rec.get("task_id", ""), rec.get("merge_path") or ""
            if not mp:
                n["empty"] += 1
                hint = "运维/占位类，本就不该有合并稿" if re.match(r"^(maintenance|pending)-", tid) else ""
                print("%s\tempty_merge_path\t%s\t标 merge_expected=false / execution=n-a（人工确认）" % (tid, hint))
                continue
            if exists_under(root, mp):
                n["ok"] += 1
                continue
            norm = normalize(mp)
            if tid in MANUAL_ONLY:
                n["manual"] += 1
                print("%s\tmanual_only\t已知无合并稿或指向别的 task_id\t人工处理，脚本不猜" % tid)
                continue
            full = exists_under(root, norm)
            if full:
                n["legacy_prefix"] += 1
                print("%s\tstrip_prefix\t%s\t回填 %s" % (tid, mp, norm))
                changes.append((dec, tid, norm))
                continue
            cands = scan_log_dir(root, tid)
            ev = build_merged(root, tid) or git_merge_commit(root, tid)
            if len(cands) == 1:
                rel = rel_to(root, cands[0])
                n["legacy_prefix"] += 1
                print("%s\tlog_scan\t%s%s\t回填 %s" % (tid, rel, ("；" + ev) if ev else "", rel))
                changes.append((dec, tid, rel))
            elif len(cands) > 1:
                n["needs_review"] += 1
                print("%s\tneeds_review\t候选 %s\t人工选，不自动回填" % (tid, [rel_to(root, c) for c in cands]))
            else:
                n["merge_missing"] += 1
                print("%s\tmerge_missing\t原 merge_path=%s%s\t保留原值取证，不清空" % (tid, mp, ("；" + ev) if ev else ""))

    print("\n汇总：可救回(剥前缀/日志扫描) %d｜真·缺失 %d｜待人工选 %d｜已知人工 %d｜空 merge_path %d｜本来就读得到 %d"
          % (n["legacy_prefix"], n["merge_missing"], n["needs_review"], n["manual"], n["empty"], n["ok"]))
    if not a.apply:
        print("dry-run：一个字节都没写。确认后再加 --apply（会先备份 decisions.json）")
        return 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for dec in sorted({c[0] for c in changes}):
        with open(dec, encoding="utf-8") as f:
            raw = f.read()
        with open(dec + ".bak-" + stamp, "w", encoding="utf-8") as f:
            f.write(raw)
        items = json.loads(raw)
        for rec in items:
            for _d, tid, rel in changes:
                if rec.get("task_id") == tid:
                    rec["merge_path"] = rel
        with open(dec, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        print("已回填 %s（备份 .bak-%s）" % (dec, stamp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
