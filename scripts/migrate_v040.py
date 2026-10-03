"""v0.4.0 一次性迁移：把审过的分支合入 main。py -3 scripts/migrate_v040.py <precheck|backup|apply TARGET|verify>
（2026-09-28 实际执行时的副本在 tmp/leader_merge.py，内容相同。以后的合并走正常 git merge，不再需要本脚本。）

为什么不用 git merge --ff-only：主目录里的运行数据（projects/ peers/ log/…）在旧 main 上仍被跟踪且一直被服务改写，
ff 会因"本地改动会被覆盖"拒绝；即便成功也会删掉这些文件（新版本已不跟踪）。
做法：分支指针直接移到审过的提交（与快进结果逐字节相同）→ 只更新索引 → 只检出新版本跟踪的代码文件 → 运行数据一字不碰。
"""
import os, shutil, subprocess, sys, time
MAIN = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RUNTIME = ("log/", "inbox/", "outbox/", "peers/", "attachments/", "console/", "projects/", "events/", "intake/", "feedback/",
           ".runtime/", "adapters/daily_assistant.state.json", "plugins.local.json")
BACKUP = os.path.join(os.path.dirname(MAIN), "AgentTeamWorkbench_backup_20260928")
EXPECT_OLD = "de7978e"
# 主目录里允许存在的未提交代码改动：已在 chore/track-live-code 以提交形式入库，合入时以提交版为准（旧工作副本先备份）
ALLOW_DIRTY = {"bus_guard.py": "exc.code == 404"}

def git(*a, check=True):
    r = subprocess.run(["git", *a], cwd=MAIN, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        print("GIT FAIL", a, r.stdout[-400:], r.stderr[-400:]); raise SystemExit(1)
    return r.stdout

def is_rt(p): return p.replace("\\", "/").startswith(RUNTIME)

def precheck():
    br = git("rev-parse", "--abbrev-ref", "HEAD").strip(); head = git("rev-parse", "--short", "HEAD").strip()
    print("branch", br, "head", head)
    mods = [l for l in git("status", "--porcelain", "--untracked-files=no").splitlines() if l.strip()]
    rt = [l for l in mods if is_rt(l[3:])]; code = [l for l in mods if not is_rt(l[3:])]
    print("modified tracked runtime:", len(rt), rt[:8]); print("modified tracked CODE:", len(code), code[:30])
    unt = [l for l in git("status", "--porcelain").splitlines() if l.startswith("??")]
    print("untracked (non-ignored):", len(unt), [l[3:] for l in unt][:40])
    bad = [l for l in code if l[3:] not in ALLOW_DIRTY]
    print("dirty code not allowed:", bad)
    ok = br == "main" and head.startswith(EXPECT_OLD) and not bad
    print("PRECHECK", "OK" if ok else "BLOCKED"); return ok

def backup():
    os.makedirs(BACKUP, exist_ok=True); n = 0
    for p in RUNTIME + ("docs/zex-branch-review-20260928.md", "docs/backlog-triage-20260928.md", "workspace.json"):
        src = os.path.join(MAIN, p.rstrip("/"))
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(BACKUP, p.rstrip("/")), dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.mutex"))
            n += sum(len(f) for _, _, f in os.walk(src))
        elif os.path.isfile(src):
            os.makedirs(os.path.dirname(os.path.join(BACKUP, p)), exist_ok=True); shutil.copy2(src, os.path.join(BACKUP, p)); n += 1
    print("backup ->", BACKUP, "files", n)

def counts():
    return {p: sum(len(f) for _, _, f in os.walk(os.path.join(MAIN, p))) for p in ("log", "projects", "peers", "inbox", "outbox", "console", "attachments")}

def apply(target):
    new = git("rev-parse", target).strip()
    if git("rev-parse", "HEAD").strip() == new:
        # 续跑：上次已移动指针但在刷新索引/检出时中断（例如别的进程占着 .git/index.lock）
        print("HEAD already at target → resume: refresh index + checkout + deletions")
        old = git("rev-parse", EXPECT_OLD).strip(); resume = True
    else:
        if not precheck(): raise SystemExit(1)
        old = git("rev-parse", "HEAD").strip(); resume = False
    if not os.path.isdir(BACKUP): print("NO BACKUP"); raise SystemExit(1)
    for f, must in ALLOW_DIRTY.items():
        if must not in git("show", f"{new}:{f}"):
            print("target lacks hotpatch in", f); raise SystemExit(1)
        if not resume:
            os.makedirs(os.path.join(BACKUP, "hotpatch"), exist_ok=True); shutil.copy2(os.path.join(MAIN, f), os.path.join(BACKUP, "hotpatch", f))
    if subprocess.run(["git", "merge-base", "--is-ancestor", old, new], cwd=MAIN).returncode != 0:
        print("target is not a descendant of main → not a fast-forward, abort"); raise SystemExit(1)
    before = counts(); print("runtime before", before)
    # --no-renames：否则被改名的文件算 R 不算 D，旧路径会残留在磁盘（审查 24a8：17 个）
    gone = [p for p in git("diff", "--no-renames", "--name-only", "--diff-filter=D", old, new).splitlines() if p and not is_rt(p)]
    if not resume:
        git("reset", "--soft", new)      # main 指针 → 审过的提交（快进等价，不改写历史）
    git("reset", "-q")                   # 即 reset --mixed：索引 = 新提交；工作区不动
    tracked = [p for p in git("ls-files", "-z").split("\0") if p]
    for i in range(0, len(tracked), 200):
        git("checkout", "HEAD", "--", *tracked[i:i + 200])   # 只检出新版本跟踪的文件（不含运行数据）
    removed = 0
    for p in gone:
        fp = os.path.join(MAIN, p)
        if os.path.isfile(fp):
            os.remove(fp); removed += 1
            d = os.path.dirname(fp)
            while d != MAIN and os.path.isdir(d) and not os.listdir(d):   # 顺手删掉空目录
                os.rmdir(d); d = os.path.dirname(d)
    for d in ("docs/zex-branch-review-20260928.md", "docs/backlog-triage-20260928.md"):   # 已入库到 docs/reviews/
        fp = os.path.join(MAIN, d)
        if os.path.isfile(fp) and os.path.isfile(os.path.join(BACKUP, d)): os.remove(fp)
    print("moved main", old[:7], "->", new[:7], "| code files removed from disk:", removed)
    after = counts(); print("runtime after ", after)
    intact = all(after[k] >= before[k] for k in before)
    print("RUNTIME", "INTACT" if intact else "CHANGED!")
    if not (verify() and intact): raise SystemExit(1)

def verify():
    print("HEAD", git("log", "--oneline", "-1").strip())
    tracked_clean = subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=MAIN).returncode == 0
    diff = git("diff", "--name-status", "HEAD")
    unt = [p for p in git("ls-files", "-o", "--exclude-standard").splitlines() if p]
    print("tracked vs HEAD:", "CLEAN" if tracked_clean else "DIFF\n" + diff[-1500:])
    print("untracked, not ignored:", len(unt), "(杂项，合入后单独整理)", unt[:60])
    print("VERIFY", "PASS" if tracked_clean else "FAIL"); return tracked_clean

def plan(target):
    """只读：列出合入时会从磁盘删除的代码文件。"""
    new = git("rev-parse", target).strip()
    gone = [p for p in git("diff", "--no-renames", "--name-only", "--diff-filter=D", EXPECT_OLD, new).splitlines() if p and not is_rt(p)]
    exist = [p for p in gone if os.path.isfile(os.path.join(MAIN, p))]
    print("will delete from disk:", len(exist)); print("\n".join(exist))

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd in ("apply", "plan"): {"apply": apply, "plan": plan}[cmd](sys.argv[2])
    else: {"precheck": precheck, "backup": backup, "verify": verify}[cmd]()
