#!/usr/bin/env python3
"""一次性历史纠正：给「已采纳但 execution 仍为 pending（或缺失）」的决策出分类建议表；默认 dry-run 只打印，不写任何文件。

用法（仓库根目录）：
  py -3 tools/fix_execution_5054.py                 # dry-run：打印全部待落地项的建议表
  py -3 tools/fix_execution_5054.py --apply         # 只落「强证据」与「cancelled 却 pending」两类：改 execution + 追加 correction 记录
  py -3 tools/fix_execution_5054.py --apply --include-suggested   # 连「建议证据」（artifacts 非空 / 标题写明已落地）一起落，需本人确认后再用
  --root <目录>  指定运行态根（默认脚本所在仓库根）；--project <slug> 只看一个项目

证据强弱：
  强：console/builds/<task>.json 里任一 teammates.<peer>.merged == true；git log --grep <task_id> 在 --root 仓库命中（提交主题含该 task_id 的 merge 提交）
  建议：decisions 的 artifacts 非空；标题 / summary 写明「已落地 / 已合并 / 已上线」
  直接改：status ∈ cancelled / superseded / rejected 却带 execution=pending → n-a
写库只改 execution，并在该决策追加 correction: {time, from, to, evidence, by}。不碰 console/、peers/、priority.json。
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

TITLE_RE = re.compile(r"已落地|已合并(?:进|到)?主线|已上线|已实施")


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def git_hits(root, task_id):
    try:
        r = subprocess.run(["git", "log", "--all", "--oneline", "--grep", task_id], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        return [l for l in r.stdout.splitlines() if l.strip()] if r.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        return []


def classify(root, slug, rec, builds):
    tid = rec["task_id"]
    ex = rec.get("execution") or "pending"
    status = rec.get("status")
    evidence, strong, suggest = [], False, ""
    if status in ("cancelled", "superseded", "rejected"):
        return {"to": "n-a", "kind": "direct", "evidence": [f"status={status} 却 execution={ex}"]}
    b = builds.get(tid) or {}
    merged = [(p, t) for p, t in (b.get("teammates") or {}).items() if t.get("merged")]
    if merged:
        strong = True
        evidence.append("builds merged: " + "、".join(f"{p}@{str(t.get('merge_commit') or '')[:7]}" for p, t in merged))
    hits = git_hits(root, tid)
    merge_hits = [h for h in hits if re.search(r"\bmerge\b", h, re.I)]
    if merge_hits:
        strong = True
        evidence.append("git log: " + merge_hits[0][:80])
    elif hits:
        evidence.append("git log（非 merge 提交，仅供参考）: " + hits[0][:80])
    if rec.get("artifacts"):
        suggest = "released"
        evidence.append(f"artifacts {len(rec['artifacts'])} 项")
    if TITLE_RE.search(str(rec.get("title") or "") + " " + str(rec.get("summary") or "")):
        suggest = "released"
        evidence.append("标题/摘要写明已落地")
    if strong:
        return {"to": "released", "kind": "strong", "evidence": evidence}
    if suggest:
        return {"to": suggest, "kind": "suggested", "evidence": evidence}
    return {"to": "pending", "kind": "keep", "evidence": evidence or ["无证据，保持待落地"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--project", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--include-suggested", action="store_true")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    projects_dir, builds_dir = os.path.join(root, "projects"), os.path.join(root, "console", "builds")
    builds = {}
    if os.path.isdir(builds_dir):
        for f in os.listdir(builds_dir):
            if f.endswith(".json"):
                rec = read_json(os.path.join(builds_dir, f), {}) or {}
                builds[str(rec.get("task_id") or f[:-5])] = rec
    rows, changed = [], 0
    for slug in sorted(os.listdir(projects_dir)) if os.path.isdir(projects_dir) else []:
        if a.project and slug != a.project:
            continue
        path = os.path.join(projects_dir, slug, "decisions.json")
        items = read_json(path, None)
        if not isinstance(items, list):
            continue
        dirty = False
        for rec in items:
            ex = rec.get("execution") or "pending"
            if ex != "pending":
                continue
            if rec.get("status") not in ("adopted", "cancelled", "superseded", "rejected"):
                continue
            c = classify(root, slug, rec, builds)
            apply_this = a.apply and c["to"] != "pending" and (c["kind"] in ("direct", "strong") or (a.include_suggested and c["kind"] == "suggested"))
            rows.append((slug, rec["task_id"], rec.get("status"), rec.get("execution", "<缺失>"), c["to"], c["kind"], "; ".join(c["evidence"]), "写入" if apply_this else "-"))
            if apply_this:
                rec.setdefault("correction", []).append({"time": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "from": rec.get("execution", None), "to": c["to"],
                                                         "evidence": c["evidence"], "by": "tools/fix_execution_5054"})
                rec["execution"] = c["to"]
                dirty = True
                changed += 1
        if dirty:
            write_json(path, items)
    w = [max(len(str(r[i])) for r in rows + [("项目", "task_id", "status", "execution", "建议", "证据级", "证据", "本次")]) for i in range(8)]
    hdr = ("项目", "task_id", "status", "execution", "建议", "证据级", "证据", "本次")
    print(" | ".join(str(h).ljust(w[i]) for i, h in enumerate(hdr)))
    for r in rows:
        print(" | ".join(str(x).ljust(w[i]) for i, x in enumerate(r)))
    kinds = {}
    for r in rows:
        kinds[r[5]] = kinds.get(r[5], 0) + 1
    print(f"\n共 {len(rows)} 项：" + "，".join(f"{k} {v}" for k, v in sorted(kinds.items())) + (f"；已写入 {changed} 项" if a.apply else "；dry-run，未写任何文件（加 --apply 才落库）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
