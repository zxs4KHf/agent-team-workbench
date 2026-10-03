import os
import re
import json
import shutil
import subprocess
import datetime as dt

# 施工流程：每个 build 任务给每个Agent一棵 git worktree，Agent的 ws_* 根目录指向自己的 worktree；
# 回复时自动 commit；Reviewer看 diff 与检查门禁；Leader merge --no-ff 进主线。
import bus_core as B

ROLES_PATH = os.path.join(B.ROOT, "console", "roles.json")
BUILD_DIR = os.path.join(B.ROOT, "console", "builds")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def roles() -> dict:
    return B.read_roles(ROLES_PATH)


def policy(key: str, default=None):
    return (roles().get("policy") or {}).get(key, default)


def git(repo: str, *args, check: bool = True, timeout: int = 120) -> str:
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed ({r.returncode}): {out.strip()[-600:]}")
    return out


def repo_root(path: str) -> str:
    try:
        return git(path, "rev-parse", "--show-toplevel").strip().replace("/", os.sep)
    except Exception:
        return ""


def ensure_repo(path: str) -> str:
    # 项目目录没有 git 仓库时就地初始化，保证任何目录都能进入施工流程
    top = repo_root(path)
    if top:
        return top
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "workbench")
    git(path, "config", "user.email", "workbench@localhost")
    gi = os.path.join(path, ".gitignore")
    if not os.path.exists(gi):
        open(gi, "w", encoding="utf-8", newline="\n").write(".wt/\n__pycache__/\nnode_modules/\n")
    git(path, "add", "-A")
    git(path, "commit", "-m", "workbench: initial snapshot", "--allow-empty")
    return path


def safe_branch(task_id: str, teammate: str) -> str:
    return f"{policy('branch_prefix', 'task/')}{task_id}/{re.sub(r'[^a-z0-9_-]', '-', teammate.lower())}"


def record_path(task_id: str) -> str:
    return os.path.join(BUILD_DIR, f"{task_id}.json")


def record(task_id: str) -> dict:
    return B.read_json(record_path(task_id), None) or {}


def save_record(task_id: str, data: dict):
    os.makedirs(BUILD_DIR, exist_ok=True)
    B.write_json(record_path(task_id), data)


def open_worktrees(task_id: str, teammates: list, project_dir: str) -> dict:
    # 为每个Agent建 worktree：<repo>/.wt/<task_id>/<teammate>，分支 task/<task_id>/<teammate>，基于当前 HEAD
    repo = ensure_repo(project_dir)
    base = git(repo, "rev-parse", "HEAD").strip()
    wt_root = os.path.join(repo, policy("worktree_dir", ".wt"), task_id)
    os.makedirs(wt_root, exist_ok=True)
    rec = {"task_id": task_id, "repo": repo, "base": base, "created": B.now_iso(), "teammates": {}, "status": "building"}
    for name in teammates:
        path = os.path.join(wt_root, name)
        branch = safe_branch(task_id, name)
        if not os.path.isdir(path):
            git(repo, "worktree", "add", "-b", branch, path, base)
        rec["teammates"][name] = {"path": path, "branch": branch, "commit": "", "review": "", "merged": False}
    save_record(task_id, rec)
    return rec


def add_teammate(task_id: str, teammate: str) -> dict:
    """施工中途换人：给新成员按本单同一基线开 worktree，加进施工记录。
    基线必须用 rec["base"]：提交、门禁、审查包都按 base..commit 算差异，换成新的 main 会把这期间 main 的改动全算进来。
    原成员的 worktree 与分支原样保留（里面可能有没提交的半成品），不删。"""
    from bus_locks import file_mutex
    with file_mutex(record_path(task_id) + ".mutex"):
        rec = record(task_id)
        if not rec:
            raise ValueError(f"{task_id} 不是施工任务")
        if rec.get("status") not in (None, "building") or rec.get("closed"):
            raise ValueError(f"施工 {task_id} 已结束（{rec.get('status')}），不能再加人")
        tms = rec.setdefault("teammates", {})
        if teammate in tms:
            return tms[teammate]
        path = os.path.join(rec["repo"], policy("worktree_dir", ".wt"), task_id, teammate)
        branch = safe_branch(task_id, teammate)
        if not os.path.isdir(path):
            git(rec["repo"], "worktree", "add", "-b", branch, path, rec["base"])
        tms[teammate] = {"path": path, "branch": branch, "commit": "", "review": "", "merged": False}
        save_record(task_id, rec)
        return tms[teammate]


def _still_assigned(task_id: str, teammate: str) -> bool:
    # 总线任务还开着、该成员仍在名单里、还没回执、收件箱里还有它的副本（待领或已领）。
    # 任务归档 / 被顶替 / 已回复 / 领取被 reclaim_claims 释放后，这棵 worktree 就不再是它的工作区。
    # 只看副本在不在、不看截止：超时仍在做的施工不能半路把根目录切回主工作区。
    meta = B.read_json(os.path.join(B.OUTBOX, f"{task_id}.meta.json"), None)
    if not isinstance(meta, dict) or teammate not in (meta.get("peers") or []):
        return False
    if os.path.exists(os.path.join(B.OUTBOX, f"{task_id}.{teammate}.md")):
        return False
    inbox = os.path.join(B.INBOX, teammate)
    return any(os.path.exists(os.path.join(inbox, f"{task_id}{ext}")) for ext in (".json", ".taken"))


def teammate_root(teammate: str) -> str:
    # Agent当前正在施工的 worktree；没有则返回空串（调用方回退到默认工作区）。
    # 旧写法只看 status == "building" 且未提交，取文件名最新的一单：而施工记录合并/放弃后从不改状态，
    # 掉线留下的旧单会一直「在建」，同一成员手上新旧几单并存时 ws_* 根目录就漂到别的单上（9fb1 报告的漂移）。
    # 现在只认任务仍在派给它的单；多单并存时以心跳报告的 task_id 为准。
    if not os.path.isdir(BUILD_DIR):
        return ""
    live = []
    for f in sorted(os.listdir(BUILD_DIR), reverse=True):
        if not f.endswith(".json"):
            continue
        rec = B.read_json(os.path.join(BUILD_DIR, f), None) or {}
        t = (rec.get("teammates") or {}).get(teammate)
        if rec.get("status") != "building" or rec.get("closed") or not t or t.get("commit") or t.get("merged"):
            continue
        task_id = rec.get("task_id") or f[:-5]
        if os.path.isdir(t.get("path", "")) and _still_assigned(task_id, teammate):
            live.append((task_id, t["path"]))
    if not live:
        return ""
    current = (B.read_json(os.path.join(B.PEERS, f"{teammate}.json"), None) or {}).get("task_id")
    for task_id, path in live:
        if task_id == current:
            return path
    return live[0][1]


def commit_work(task_id: str, teammate: str, message: str) -> dict:
    # Agent回复时调用：把 worktree 里的全部改动提交；没有改动也允许（讨论型回复）
    rec = record(task_id)
    t = (rec.get("teammates") or {}).get(teammate)
    if not t:
        return {}
    path = t["path"]
    git(path, "add", "-A")
    status = git(path, "status", "--porcelain").strip()
    if status:
        git(path, "-c", "user.name=" + teammate, "-c", "user.email=" + teammate + "@workbench", "commit", "-m", f"{task_id} {teammate}: {message[:72]}")
    t["commit"] = git(path, "rev-parse", "HEAD").strip()
    # 以 base..HEAD 的差异为准：builder 常在 worktree 内自行 commit，此时 status 为空，旧写法会让门禁全部跳过（「无需门禁」→ Reviewer 必 RETURN）
    t["changed_files"] = [l.strip() for l in git(path, "diff", "--name-only", rec["base"], t["commit"]).splitlines() if l.strip()] or ([l[3:] for l in status.splitlines()] if status else [])
    t["diffstat"] = git(path, "diff", "--stat", rec["base"], t["commit"]).strip()[-2000:]
    # 返工点 7（commit_unbound）：讨论型回复允许零改动，但那时 HEAD 还停在任务基线上。
    # 「提交号 = 基线」不能当被审对象：PASS 绑上去等于给一份没动过的代码背书。
    t["commit_unbound"] = bool(t["commit"]) and t["commit"] == rec["base"] and not t["changed_files"]
    t["gates"] = run_gates(path, t["changed_files"])
    rec["teammates"][teammate] = t
    save_record(task_id, rec)
    return t


def run_gates(path: str, files: list) -> dict:
    """Compile exact changed files and run their tests; exceptions are failed gates.

    Browser JS is parsed explicitly as ESM. Auto-detection with node --check on
    this host previously accepted a module that Chromium could not even parse.
    """
    import sys
    out = {'ok': True, 'items': []}
    configured = policy('gates', ['py_compile', 'node_check']) or []
    def execute(file, gate, argv, source=None, timeout=120):
        try:
            p = subprocess.run(argv, input=source, cwd=path, capture_output=True,
                               timeout=timeout, creationflags=NO_WINDOW,
                               env={**os.environ, 'PYTHONIOENCODING':'utf-8', 'PYTHONUTF8':'1'})
            ok = p.returncode == 0
            text = (p.stdout + p.stderr).decode('utf-8', 'replace')
        except Exception as exc:
            ok, text = False, type(exc).__name__ + ': ' + str(exc)
        out['items'].append({'file':file, 'gate':gate, 'ok':ok, 'out':text[-1800:]})
        out['ok'] = out['ok'] and ok
    for file in files:
        full = os.path.join(path, file)
        if not os.path.isfile(full):
            continue
        relative = file.replace('\\', '/')
        if file.endswith('.py') and 'py_compile' in configured:
            execute(file, 'python_compile', [sys.executable, '-X', 'utf8', '-c',
                'import pathlib,sys; p=pathlib.Path(sys.argv[1]); compile(p.read_bytes(),str(p),"exec")', full])
        elif file.endswith(('.js','.mjs','.cjs')) and 'node_check' in configured:
            module = file.endswith('.mjs') or relative.startswith('web/v2/js/')
            with open(full, 'rb') as stream:
                source = stream.read()
            execute(file, 'node_esm_parse' if module else 'node_commonjs_parse',
                    ['node', '--input-type=' + ('module' if module else 'commonjs'), '--check'], source)
    if not out['ok']:
        return out
    for file in files:
        full = os.path.join(path, file)
        base = os.path.basename(file)
        if not os.path.isfile(full) or not file.replace('\\','/').startswith('tests/'):
            continue
        if base.startswith('test_') and base.endswith('.py'):
            execute(file, 'unit_test', [sys.executable, '-X', 'utf8', '-u', file], timeout=240)
        elif base.startswith('test_') and base.endswith('.mjs'):
            # 语法可解析不等于测试通过：node 侧的 test_*.mjs 必须实跑并交回 rc。
            execute(file, 'unit_test', ['node', file], timeout=120)
        elif base.startswith('smoke_') and base.endswith('.mjs'):
            execute(file, 'smoke', ['node', file], timeout=120)
    # ADR 0011：动了界面就过设计护栏。护栏测试自己不在改动列表里，上面的循环跑不到它；
    # 合并时的集成门禁（临时副本里的合并差异）同样会走到这里，拦住「各自没超、合起来超了」
    ratchet = 'tests/smoke_design_ratchet.mjs'
    rels = [f.replace('\\', '/') for f in files]
    if ratchet not in rels and os.path.isfile(os.path.join(path, ratchet)) \
            and any(f.startswith('web/v2/') and '/vendor/' not in f for f in rels):
        execute(ratchet, 'design_ratchet', ['node', ratchet], timeout=60)
    return out


def _builder_answer(task_id: str, teammate: str) -> str:
    # 施工者回复（含其自测输出）随审查包下发，避免 Reviewer 因「看不到运行证据」机械 RETURN
    cands = [os.path.join(B.ROOT, "outbox", f"{task_id}.{teammate}.md")]
    logdir = os.path.join(B.ROOT, "log")
    if os.path.isdir(logdir):
        cands += [os.path.join(logdir, d, f"{task_id}.{teammate}.md") for d in sorted(os.listdir(logdir), reverse=True)[:7]]
    for c in cands:
        if os.path.isfile(c):
            try:
                return open(c, encoding="utf-8", errors="replace").read()[-6000:]
            except OSError:
                pass
    return ""


def review_pack(task_id: str, teammate: str, max_chars: int = 60000, target=None):
    import bus_build_safety as safe
    return safe.review_pack(task_id, teammate, max_chars, target)


def set_review(task_id: str, teammate: str, verdict: str, reviewer: str, note: str = "", expected_commit: str = ""):
    import bus_build_safety as safe
    return safe.set_review(task_id, teammate, verdict, reviewer, note, expected_commit)


def dispatch_review(task_id: str, teammate: str, ttl_min: int = 20, reviewers: list = None, by: str = "controller") -> dict:
    """把某成员的提交打包派给 Reviewer（作者不审自己；优先别的模型组）。Leader 的 bus_build review 与网页「催审」共用这一条路径。"""
    target = review_target(task_id, teammate)
    if isinstance(target, dict) and target.get("base") and target.get("base") == target.get("commit"):
        # 被审提交就是它和 main 的分叉点 = 内容已经在 main 里，包里没有任何差异（9397 / c204 三张空包重复单）
        raise ValueError(f"{teammate} 的提交 {str(target['commit'])[:8]} 已经在 main 里，没有差异可审")
    pack = review_pack(task_id, teammate, target=target)
    builder_group = B.peer_group(teammate)
    if reviewers and teammate in reviewers:
        # 显式点名也要过作者过滤（Leader 的 bus_build review 可以直接给名单）
        reviewers = [r for r in reviewers if r != teammate]
        if not reviewers:
            raise ValueError(f"点名的 Reviewer 只有作者 {teammate} 本人；作者不能审自己的提交，换一位或留空让系统挑")
    reviewers = reviewers or B.pick_reviewers(exclude=[teammate], avoid_group=builder_group)
    if not reviewers:
        # 先问配置自检：配置缺失/没配 reviewer_groups 时报配置问题，真·无人在线时才报原文案
        cfg = B.reviewer_config_error()
        raise ValueError(cfg or "没有在线的Reviewer（roles.json assignments.reviewer_groups 里的模型组无人在线）")
    groups = (B.read_roles().get("assignments") or {}).get("reviewer_groups") or []
    temp = [r for r in reviewers if B.peer_group(r) not in groups]
    if temp:
        pack = (f"> 临时 Reviewer：{'、'.join(temp)} 不在 Reviewer 组（Reviewer 组当前无人在线）。照常严格审：先跑测试与边界输入，"
                "看不懂或没把握的地方写进 RETURN 理由，不要因为不熟悉就 PASS。\n\n") + pack
    same = [r for r in reviewers if builder_group and B.peer_group(r) == builder_group]
    if same:
        pack = (f"> 注意：审查者（{', '.join(same)}）与施工者（{teammate}）同属模型组 {builder_group}。"
                "同一模型的盲点相同，请刻意换角度：先跑测试和边界输入，再读实现；不要只顺着作者的思路看。\n\n") + pack
    r = B.dispatch(by, reviewers, "review", pack, [], int(ttl_min), None, 1, review_of=target)
    return {"review_task": r["task_id"], "reviewers": list(reviewers), "deadline": r["deadline"], "task_id": task_id, "teammate": teammate}


def merge(task_id: str, teammate: str, by: str = "controller"):
    import bus_build_safety as safe
    return safe.merge(task_id, teammate, by)


def close(task_id: str, remove_worktrees: bool = True):
    import bus_build_safety as safe
    return safe.close(task_id, remove_worktrees)


def summary(task_id: str) -> str:
    rec = record(task_id)
    if not rec:
        return f"{task_id} 不是施工任务"
    lines = [f"# 施工 {task_id}  状态 {rec['status']}  仓库 {rec['repo']}  基线 {rec['base'][:10]}", ""]
    for name, t in rec["teammates"].items():
        g = t.get("gates", {})
        lines.append(f"- {name}: 提交 {t.get('commit', '')[:10] or '无'} · 门禁 {'通过' if g.get('ok', True) else '失败'} · 审查 {t.get('review') or '未审'} · 合并 {'是' if t.get('merged') else '否'}"
                     + (f" · 冲突 {t['conflicts']}" if t.get("conflicts") else ""))
        if t.get("diffstat"):
            lines.append("  " + t["diffstat"].splitlines()[-1].strip())
    return "\n".join(lines)


def review_target(task_id, teammate):
    import bus_build_safety as safe
    return safe.review_target(task_id, teammate)
