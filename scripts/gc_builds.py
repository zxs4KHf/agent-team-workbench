"""清理停滞的施工（build）：工作副本、task/* 分支、施工记录。

    py -3 scripts/gc_builds.py                  预演（默认，不改任何东西）
    py -3 scripts/gc_builds.py --apply          执行
    py -3 scripts/gc_builds.py --days 3         停滞满 3 天才处理（默认 2 天）
    py -3 scripts/gc_builds.py --task <id>      只处理某一单（建议先单试一单）

"停滞"必须同时满足（任何一条不满足就跳过，并打印原因）：
- 没有成员处于"审查已通过、还没进 main"（那是待合并，不是僵尸）；记录里已标 merged 的不算
  （Leader 用临时副本合入时，分支本身不是 main 的祖先，但内容已经进去了）；
- 没有成员正在做：没有 --days 以内的 inbox/<成员>/<task>.taken，且没有 1 天内心跳的 task_id 等于本单
  （更早的 .taken 是施工者掉线留下的遗留领取，不算在做）；
- 最近活动早于 --days：取施工单建立时间、分支最后一次提交时间、工作副本里改动文件的最新修改时间，三者中最晚的一个。

判断"是否已进 main"以 git 事实为准（分支是否已是 main 的祖先），不依赖记录里的 merged 标记。

每个施工位的处理，全部可恢复（归档在仓库外 ../<仓库名>_archive/builds/<task>/<成员>/，仓库搬家时要一起搬）：
- 有未提交改动 → changes.patch（git diff HEAD --binary，含已暂存）、untracked/、conflicted/（冲突中的文件原样复制）、
  base_commit.txt 与 branch.txt（补丁的基线）、ignored.txt（被忽略、未保存的文件清单）；
- 分支有 main 没有的提交 → 打带注释的标签 archive/build/<task>/<成员>；
- 然后删除工作副本；删除成功才删 task/* 分支；施工记录在记录锁内标为 closed，并写 gc 字段。
恢复：`git switch -c <新分支> <base_commit>` 后 `git apply changes.patch`；提交用 `git switch -c <新分支> archive/build/<task>/<成员>`。
"""
import argparse, datetime as dt, json, os, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bus_locks import atomic_write_json, file_mutex  # noqa: E402  与 bus_build_safety 共用记录锁

CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def git(cwd, *a, check=True):
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a)} @ {cwd}: {(r.stdout + r.stderr).strip()[-300:]}")
    return r


def git_bytes(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True).stdout


def z_entries(cwd, *a):
    """以 -z 调 git 并按 \\0 切分：路径原样输出，不会把中文转义成 "\\347..."（否则会找不到文件、静默漏存）。"""
    return [e for e in git(cwd, *a, "-z").stdout.split("\0") if e]


def status_z(cwd, *extra):
    """git status --porcelain -z → [(XY, 路径)]；重命名/复制记录后面跟着旧路径，跳过。"""
    items, it = [], iter(z_entries(cwd, "status", "--porcelain", *extra))
    for e in it:
        code, rel = e[:2], e[3:]
        items.append((code, rel))
        if code[0] in "RC":
            next(it, None)
    return items


def long_path(p):
    # Windows 路径超过 260 字符时 copy2/makedirs 会报 FileNotFoundError，加 \\?\ 前缀绕开
    p = os.path.abspath(p)
    return "\\\\?\\" + p if os.name == "nt" and not p.startswith("\\\\?\\") else p


def copy_into(src, dest):
    os.makedirs(os.path.dirname(long_path(dest)), exist_ok=True)
    shutil.copy2(long_path(src), long_path(dest))


def classify(repo, tm):
    path, br = tm.get("path", ""), tm.get("branch", "")
    has_br = bool(br) and git(repo, "rev-parse", "--verify", "--quiet", br, check=False).returncode == 0
    merged = has_br and git(repo, "merge-base", "--is-ancestor", br, "main", check=False).returncode == 0
    ahead = int(git(repo, "rev-list", "--count", f"main..{br}").stdout.strip() or 0) if has_br else 0
    last_commit = int(git(repo, "log", "-1", "--format=%ct", br).stdout.strip() or 0) if has_br else 0
    has_wt = bool(path) and os.path.isdir(path)
    status = status_z(path, "-uall") if has_wt else []
    last_mtime, conflicted = 0, []
    for code, rel in status:
        if code in CONFLICT_CODES:
            conflicted.append(rel)
        try:
            last_mtime = max(last_mtime, int(os.path.getmtime(os.path.join(path, rel))))
        except OSError:
            pass
    return {"path": path, "branch": br, "has_branch": has_br, "merged": merged, "ahead": ahead, "has_worktree": has_wt,
            "dirty": len(status), "conflicted": conflicted, "review": str(tm.get("review") or ""), "merged_flag": bool(tm.get("merged")),
            "last_commit": last_commit, "last_mtime": last_mtime}


def active_task_ids(state_root, now, days=2.0):
    """正在做的施工：inbox 里 days 天内被领取的单 + 1 天内心跳报告的 task_id。"""
    busy = set()
    inbox = os.path.join(state_root, "inbox")
    for peer in os.listdir(inbox) if os.path.isdir(inbox) else []:
        d = os.path.join(inbox, peer)
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith(".taken"):
                    try:
                        fresh = now.timestamp() - os.path.getmtime(os.path.join(d, f)) < days * 86400
                    except OSError:
                        fresh = False
                    if fresh:
                        busy.add(f[:-6])
    peers = os.path.join(state_root, "peers")
    for f in os.listdir(peers) if os.path.isdir(peers) else []:
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(peers, f), encoding="utf-8") as fh:
                hb = json.load(fh) or {}
            seen = dt.datetime.fromisoformat(hb.get("last_seen", ""))
        except (OSError, ValueError, TypeError):
            continue
        if hb.get("task_id") and (now - seen).total_seconds() < 86400:
            busy.add(str(hb["task_id"]))
    return busy


def skip_reason(tid, rec, members, busy, now, days):
    for n, m in members.items():
        if m["review"].upper().startswith("PASS") and m["has_branch"] and not m["merged"] and not m["merged_flag"]:
            return f"{n} 审查已通过、待合并"
    if tid in busy:
        return "有成员正在做（inbox 已领取或心跳报告本单）"
    try:
        created = dt.datetime.fromisoformat(rec.get("created", "")).timestamp()
    except ValueError:
        created = 0
    last = max([created] + [m["last_commit"] for m in members.values()] + [m["last_mtime"] for m in members.values()])
    idle = (now.timestamp() - last) / 86400
    if idle < days:
        return f"最近 {idle:.1f} 天内有活动"
    return ""


def gc_member(repo, task, name, info, archive, now):
    out = dict(info)
    dest = os.path.join(archive, task, name)
    if info["dirty"]:
        os.makedirs(long_path(dest), exist_ok=True)
        wt = info["path"]
        with open(long_path(os.path.join(dest, "changes.patch")), "wb") as f:
            f.write(git_bytes(wt, "diff", "HEAD", "--binary"))
        for rel in z_entries(wt, "ls-files", "-o", "--exclude-standard"):
            if os.path.isfile(os.path.join(wt, rel)):
                copy_into(os.path.join(wt, rel), os.path.join(dest, "untracked", rel))
        for rel in info["conflicted"]:
            if os.path.isfile(os.path.join(wt, rel)):
                copy_into(os.path.join(wt, rel), os.path.join(dest, "conflicted", rel))
        with open(long_path(os.path.join(dest, "base_commit.txt")), "w", encoding="utf-8") as f:
            f.write(git(wt, "rev-parse", "HEAD").stdout.strip() + "\n")
        with open(long_path(os.path.join(dest, "branch.txt")), "w", encoding="utf-8") as f:
            f.write(info["branch"] + "\n")
        ignored = [rel for code, rel in status_z(wt, "--ignored") if code == "!!"]
        with open(long_path(os.path.join(dest, "ignored.txt")), "w", encoding="utf-8") as f:
            f.write("\n".join(ignored) + ("\n" if ignored else ""))
        out["archive"] = dest
    if info["has_branch"] and not info["merged"]:
        tag = f"archive/build/{task}/{name}"
        if git(repo, "rev-parse", "--verify", "--quiet", tag, check=False).returncode != 0:
            git(repo, "tag", "-a", tag, info["branch"], "-m", f"gc {task}/{name} {now:%Y-%m-%d}：未进 main 的施工提交，清理前保留")
        out["tag"] = tag
    if info["has_worktree"]:
        r = git(repo, "worktree", "remove", "--force", info["path"], check=False)
        out["worktree_removed"] = r.returncode == 0
        if r.returncode:
            out["worktree_error"] = (r.stdout + r.stderr).strip()[-200:]
    if info["has_branch"] and out.get("worktree_removed", True):
        out["branch_deleted"] = git(repo, "branch", "-D", info["branch"], check=False).returncode == 0
    return out


def run(repo=ROOT, builds=None, days=2.0, task=None, apply=False, archive=None, now=None, state_root=None):
    builds = builds or os.path.join(repo, "console", "builds")
    archive = archive or os.path.join(os.path.dirname(repo), os.path.basename(repo) + "_archive", "builds")
    state_root = state_root or repo
    now = now or dt.datetime.now()
    busy = active_task_ids(state_root, now, days)
    report, skipped = [], []
    for f in sorted(os.listdir(builds)) if os.path.isdir(builds) else []:
        if not f.endswith(".json"):
            continue
        p = os.path.join(builds, f)
        with open(p, encoding="utf-8") as fh:
            rec = json.load(fh)
        tid = rec.get("task_id") or f[:-5]
        if rec.get("closed") or rec.get("status") == "closed" or (task and tid != task):
            continue
        members = {n: classify(repo, tm) for n, tm in (rec.get("teammates") or {}).items()}
        why = skip_reason(tid, rec, members, busy, now, days)
        if why:
            skipped.append({"task_id": tid, "reason": why})
            continue
        row = {"task_id": tid, "members": members}
        if apply:
            done = {}
            for n, i in members.items():
                try:
                    done[n] = gc_member(repo, tid, n, i, archive, now)
                except Exception as exc:  # 一个成员归档出错：记录并继续其它单；该成员工作副本未删，本单不标 closed
                    done[n] = dict(i, error=str(exc)[:300], worktree_removed=False)
            ok = all(d.get("worktree_removed", True) for d in done.values())
            with file_mutex(p + ".mutex"):
                with open(p, encoding="utf-8") as fh:
                    cur = json.load(fh)
                cur["gc"] = {"at": now.isoformat(timespec="seconds"), "by": "scripts/gc_builds.py", "members": done}
                if ok:
                    cur.update(status="closed", closed=now.isoformat(timespec="seconds"), worktrees_retained=False)
                atomic_write_json(p, cur)
            row["members"], row["closed"] = done, ok
        report.append(row)
    if apply:
        git(repo, "worktree", "prune", check=False)
        os.makedirs(archive, exist_ok=True)
        atomic_write_json(os.path.join(archive, f"gc-report-{now:%Y%m%d-%H%M%S}.json"), {"done": report, "skipped": skipped})
    return {"done": report, "skipped": skipped}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--days", type=float, default=2.0)
    ap.add_argument("--task")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    rep = run(days=a.days, task=a.task, apply=a.apply)
    kinds = {"已进 main·干净": 0, "已进 main·有未提交改动": 0, "未进 main（打标签保留）": 0}
    fmt = lambda ts: dt.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else "-"
    for row in rep["done"]:
        for n, m in row["members"].items():
            k = "未进 main（打标签保留）" if m["has_branch"] and not m["merged"] else ("已进 main·有未提交改动" if m["dirty"] else "已进 main·干净")
            kinds[k] += 1
            print(f"{row['task_id']}  {n:12s} {k:22s} ahead={m['ahead']} dirty={m['dirty']} conflict={len(m['conflicted'])} "
                  f"last_commit={fmt(m['last_commit'])} last_edit={fmt(m['last_mtime'])}" + (f"  closed={row.get('closed')}" if a.apply else ""))
    for s in rep["skipped"]:
        print(f"跳过 {s['task_id']}：{s['reason']}")
    errors = [(r["task_id"], n, m["error"]) for r in rep["done"] for n, m in r["members"].items() if m.get("error")]
    for e in errors:
        print("出错（未删除，可重跑）：", *e)
    print(("已执行" if a.apply else "预演（加 --apply 执行）"), f"清理 {len(rep['done'])} 单，跳过 {len(rep['skipped'])} 单：", kinds)
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
