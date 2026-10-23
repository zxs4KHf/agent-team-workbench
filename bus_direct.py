"""直连 Agent（2026-10-21）：用户在外部窗口（网页版 AI、Claude / Codex 桌面等）里直接对话、派活的 Agent。

它不是 Leader，也不是队员：不领团队任务、不需要循环待命。它用自己的令牌连 MCP，只看得到 direct_* 工具，
能碰的文件夹、权限模式、可见背景、自动放行的命令都由用户在工作台「Agent → 直连 Agent」里逐个配置。

权限模式（仿 Codex / Claude Code）：
- readonly  只读：只能看文件、跑只读命令（git status 之类），写文件和其它命令一律拒绝；
- ask       逐项审批：写文件、跑非只读命令都先在工作台排队等用户批准；
- auto_edit 自动改文件：可写文件夹里直接改；命令除只读和「自动放行」清单外仍要批准；
- bypass    完全放开：不弹审批（外发类命令也不拦），只保留文件夹边界与凭据拒读。

边界说实话：文件工具受文件夹清单约束；命令执行的是宿主等权代码，不是沙箱——cwd 只是起点，
命令本身能动到哪里，取决于用户批准了什么（审批看到的就是完整命令原文）。

审批 = 绑定内容的一次性凭据（ARCHITECTURE「借来的四条模式」第 2 条）：记录里存完整操作和它的 sha256，
批准后由工作台按记录原样执行一次；执行中进程没了就落 unknown，不自动重试（第 3 条）。

运行数据全在 console/direct/（已 .gitignore）：agents.json、approvals/、tasks/、activity/。
"""
import asyncio
import datetime as dt
import difflib
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shlex
import sys
import threading
import time

import bus_core as B
import bus_settings as SET
import named_workspace_io as W
from bus_locks import file_mutex

DIRECT_DIR = os.environ.get("BUS_DIRECT_DIR") or os.path.join(B.ROOT, "console", "direct")   # 环境变量只给测试用
AGENTS = os.path.join(DIRECT_DIR, "agents.json")
APPROVALS = os.path.join(DIRECT_DIR, "approvals")
TASKS = os.path.join(DIRECT_DIR, "tasks")
ACTIVITY = os.path.join(DIRECT_DIR, "activity")
IDENTITY_PREFIX = "direct:"

MODES = {
    "readonly": ("只读", "只能看文件、跑只读命令；写文件和其它命令一律拒绝。"),
    "ask": ("逐项审批", "看文件不用批；写文件、跑非只读命令都先在工作台等你批准。"),
    "auto_edit": ("自动改文件", "可写文件夹里直接改文件；命令除只读和自动放行清单外仍要你批准，外发类命令总要批准。"),
    "bypass": ("完全放开", "不弹审批，外发类命令也不拦；只保留文件夹边界和凭据拒读。"),
}
DEFAULT_MODE = "auto_edit"
# 任何模式都直接放行的只读命令（仍要过元字符检查，防止「git status; rm …」借道）
READONLY_COMMANDS = ("git status", "git diff", "git log", "git show", "git rev-parse", "git branch --list", "git remote -v",
                     "ls", "dir", "where", "gh pr view", "gh pr list", "gh pr status", "gh pr diff", "gh pr checks",
                     "gh run list", "gh run view", "gh issue list", "gh issue view")
DEFAULT_AUTO_COMMANDS = ["git add", "git commit", "git switch", "git checkout -b", "git stash list",
                         "py -3 scripts/run_tests.py", "py -3 -m pytest", "python -m pytest", "npm test", "node --test"]
# 外发 / 不可逆：除完全放开外一律要批准（推送、PR、Release、仓库与议题写操作、gh api）
OUTBOUND_RE = re.compile(r"^\s*(git\s+push\b|git\s+reset\s+--hard\b|git\s+clean\b|gh\s+(pr\s+(create|merge|close|comment|review|edit|ready|reopen)"
                         r"|release\b|repo\s+(create|delete|edit|fork|rename|archive)|issue\s+(create|close|comment|edit|delete|reopen)"
                         r"|api\b|secret\b|variable\b|workflow\s+(run|enable|disable)|gist\b))", re.I)
SHELL_META = set(";&|<>`$(){}\r\n")
MAX_FOLDERS, MAX_AUTO, MAX_INSTR, MAX_FILES = 20, 50, 8000, 30
WAIT_SEC = 45            # 工具调用等审批的最长阻塞；网页版宿主的工具超时多在 60 秒上下
RUN_TAIL = 16 * 1024
PREVIEW_LINES = 160
PENDING_TTL_H = 24
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".wt", "Library", "Temp", "obj"}

_lock_path = os.path.join(DIRECT_DIR, "locks", "agents.mutex")


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _read(path, default):
    try:
        return B.read_json(path, default)
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------- 配置 ----
def _load_all() -> dict:
    data = _read(AGENTS, {}) or {}
    return data.get("agents") if isinstance(data.get("agents"), dict) else {}


def _save_all(agents: dict) -> None:
    os.makedirs(DIRECT_DIR, exist_ok=True)
    B.write_json(AGENTS, {"agents": agents})


def get(agent_id: str) -> dict | None:
    a = _load_all().get(str(agent_id or ""))
    return dict(a) if isinstance(a, dict) else None


def identity_agent(identity: str) -> dict | None:
    """MCP 连接身份 → 直连 Agent 记录（停用的也返回，由调用方判断）。"""
    if not str(identity or "").startswith(IDENTITY_PREFIX):
        return None
    return get(identity[len(IDENTITY_PREFIX):])


def token_identities() -> list:
    """[(token, identity)]：给 MCP 鉴权用，只列启用的、令牌够长的。"""
    out = []
    for aid, a in _load_all().items():
        tok = str(a.get("token") or "")
        if a.get("enabled", True) and len(tok) >= 32:
            out.append((tok, IDENTITY_PREFIX + aid))
    return out


def normalize_folders(items, *, allow_empty=True) -> list:
    """[{path, access: read|write, label}] → 校验后的绝对路径清单。项目文件夹与直连 Agent 共用这一份规则：
    目录必须存在，不能是系统目录或整个磁盘根（同 bus_settings.validate_workspace）；同一路径只留一条。"""
    if items is None:
        items = []
    if not isinstance(items, list):
        raise ValueError("文件夹清单必须是数组")
    if len(items) > MAX_FOLDERS:
        raise ValueError(f"文件夹最多 {MAX_FOLDERS} 个")
    out, seen = [], set()
    for it in items:
        if isinstance(it, str):
            it = {"path": it}
        if not isinstance(it, dict):
            raise ValueError("文件夹条目格式不对")
        raw = str(it.get("path") or "").strip().strip('"')
        if not raw:
            continue
        if not os.path.isabs(raw):
            raise ValueError(f"文件夹要写绝对路径：{raw}")
        path = SET.validate_workspace(raw)
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        access = it.get("access") if it.get("access") in ("read", "write") else "write"
        out.append({"path": path, "access": access, "label": str(it.get("label") or "").strip()[:40]})
    if not out and not allow_empty:
        raise ValueError("至少要给一个文件夹")
    return out


def _clean_list(values, limit, item_max=200) -> list:
    if values is None:
        return []
    if isinstance(values, str):
        values = values.splitlines()
    if not isinstance(values, list):
        raise ValueError("清单必须是数组或多行文本")
    out = []
    for v in values:
        s = str(v or "").strip()
        if s and s not in out:
            out.append(s[:item_max])
    return out[:limit]


def _new_id(agents: dict) -> str:
    while True:
        aid = "d" + secrets.token_hex(3)
        if aid not in agents:
            return aid


def save(payload: dict) -> dict:
    """新建或更新一个直连 Agent。payload 里没有 id = 新建（发令牌）；有 id = 只改传了的字段。"""
    if not isinstance(payload, dict):
        raise ValueError("参数必须是对象")
    with file_mutex(_lock_path):
        agents = _load_all()
        aid = str(payload.get("id") or "").strip()
        if aid and aid not in agents:
            raise ValueError(f"没有这个直连 Agent：{aid}")
        cur = dict(agents.get(aid) or {})
        creating = not cur
        if creating:
            aid = _new_id(agents)
            cur = {"id": aid, "created": now(), "token": secrets.token_urlsafe(36), "enabled": True, "mode": DEFAULT_MODE,
                   "folders": [], "projects": [], "context_files": [], "instructions": "", "auto_commands": list(DEFAULT_AUTO_COMMANDS),
                   "run_timeout_max": 600, "model": ""}
        if "name" in payload or creating:
            name = str(payload.get("name") or "").strip()
            if not name:
                raise ValueError("名字不能为空")
            cur["name"] = name[:40]
        if "model" in payload:
            cur["model"] = str(payload.get("model") or "").strip()[:60]
        if "mode" in payload:
            if payload["mode"] not in MODES:
                raise ValueError("权限模式只能是 " + " / ".join(MODES))
            cur["mode"] = payload["mode"]
        if "folders" in payload:
            cur["folders"] = normalize_folders(payload["folders"])
        if "projects" in payload:
            import bus_projects as P
            known = {p.get("slug") for p in P.list_projects()}
            projects = _clean_list(payload["projects"], 20, 80)
            bad = [s for s in projects if s not in known]
            if bad:
                raise ValueError("没有这些项目：" + "、".join(bad))
            cur["projects"] = projects
        if "context_files" in payload:
            cur["context_files"] = _clean_list(payload["context_files"], MAX_FILES, 400)
        if "instructions" in payload:
            text = str(payload.get("instructions") or "")
            if len(text) > MAX_INSTR:
                raise ValueError(f"说明最多 {MAX_INSTR} 字")
            cur["instructions"] = text
        if "auto_commands" in payload:
            cur["auto_commands"] = _clean_list(payload["auto_commands"], MAX_AUTO)
        if "run_timeout_max" in payload:
            cur["run_timeout_max"] = max(10, min(int(payload.get("run_timeout_max") or 600), 1800))
        if "enabled" in payload:
            cur["enabled"] = bool(payload["enabled"])
        cur["updated"] = now()
        agents[aid] = cur
        _save_all(agents)
    _activity(aid, "config", "新建" if creating else "改了配置", "ok")
    return public(cur)


def delete(agent_id: str) -> dict:
    with file_mutex(_lock_path):
        agents = _load_all()
        if agent_id not in agents:
            raise ValueError(f"没有这个直连 Agent：{agent_id}")
        agents.pop(agent_id)
        _save_all(agents)
    # 还在排队的审批随之作废，免得删了以后被误批
    for ap in list_approvals(agent_id, statuses=("pending",)):
        _update_approval(ap["id"], lambda r: r.update(status="cancelled", decided_at=now(), note="直连 Agent 已删除"))
    return {"ok": True, "id": agent_id}


def rotate_token(agent_id: str) -> dict:
    with file_mutex(_lock_path):
        agents = _load_all()
        if agent_id not in agents:
            raise ValueError(f"没有这个直连 Agent：{agent_id}")
        agents[agent_id]["token"] = secrets.token_urlsafe(36)
        agents[agent_id]["updated"] = now()
        _save_all(agents)
    _activity(agent_id, "config", "换了令牌（旧连接失效）", "ok")
    return {"ok": True}


def reveal_token(agent_id: str) -> str:
    a = get(agent_id)
    if not a:
        raise ValueError(f"没有这个直连 Agent：{agent_id}")
    return a.get("token") or ""


def public(a: dict) -> dict:
    """给网页看的视图：令牌只露尾巴。"""
    out = {k: v for k, v in a.items() if k != "token"}
    tok = str(a.get("token") or "")
    out["token_hint"] = ("…" + tok[-4:]) if tok else ""
    out["mode_label"] = MODES.get(a.get("mode"), ("", ""))[0]
    return out


def summary() -> dict:
    """工作台整页状态里的一小块：Agent 页的直连卡片要每人的模式、启停、待批、待办和最近活动；详情仍走 GET /api/direct。"""
    pend = list_approvals(statuses=("pending",))
    rows = []
    for aid, a in sorted(_load_all().items(), key=lambda kv: kv[1].get("created", "")):
        acts = activity(aid, 30)
        fs = a.get("folders") or []
        rows.append({"id": aid, "name": a.get("name") or aid, "model": a.get("model") or "", "mode": a.get("mode") or "",
                     "mode_label": MODES.get(a.get("mode"), ("", ""))[0], "enabled": a.get("enabled") is not False,
                     "folders": len(fs), "writable": sum(1 for f in fs if f.get("access") != "read"),
                     "pending": sum(1 for x in pend if x.get("agent") == aid),
                     "open_tasks": sum(1 for t in tasks(aid) if t.get("status") == "open"),
                     "last_seen": next((x["time"] for x in acts if x.get("tool") not in ("config", "user")), "")})
    return {"agents": len(rows), "pending": len(pend), "list": rows}


def overview() -> dict:
    """工作台 GET /api/direct：全部直连 Agent + 待批审批 + 每人最近活动与任务。"""
    expire_stale()
    agents = []
    for aid, a in sorted(_load_all().items(), key=lambda kv: kv[1].get("created", "")):
        item = public(a)
        acts = activity(aid, 30)
        item["activity"] = acts
        item["last_seen"] = next((x["time"] for x in acts if x.get("tool") not in ("config", "user")), "")
        item["tasks"] = tasks(aid)
        agents.append(item)
    return {"agents": agents, "approvals": list_approvals(statuses=("pending",)) + list_approvals(statuses=("running",)),
            "recent_approvals": [x for x in list_approvals(limit=40) if x["status"] not in ("pending", "running")][:20],
            "modes": [{"key": k, "label": v[0], "detail": v[1]} for k, v in MODES.items()],
            "readonly_commands": list(READONLY_COMMANDS), "default_auto_commands": DEFAULT_AUTO_COMMANDS}


# ---------------------------------------------------------------- 活动 ----
def _activity(agent_id: str, tool: str, summary: str, outcome: str = "ok") -> None:
    try:
        os.makedirs(ACTIVITY, exist_ok=True)
        with open(os.path.join(ACTIVITY, f"{agent_id}.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"time": now(), "tool": tool, "summary": str(summary)[:300], "outcome": outcome}, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 活动记录是旁路，写失败不挡操作


def activity(agent_id: str, limit: int = 50) -> list:
    path = os.path.join(ACTIVITY, f"{agent_id}.jsonl")
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()[-max(1, limit):]
    except OSError:
        return []
    out = []
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------- 路径 ----
def _folders(a: dict) -> list:
    return [f for f in (a.get("folders") or []) if isinstance(f, dict) and f.get("path")]


def locate(a: dict, path: str, write: bool = False):
    """把 Agent 给的路径落到它的某个文件夹里：绝对路径找包含它的最深文件夹；相对路径按第一个文件夹算。
    返回 (folder, 绝对路径, 文件夹内相对路径)。越界、凭据文件、写只读文件夹都抛错。"""
    folders = _folders(a)
    if not folders:
        raise PermissionError("还没给你配置任何文件夹，请用户在工作台里添加")
    raw = str(path or "").strip().strip('"') or "."
    if os.path.isabs(raw):
        full = os.path.normpath(raw)
        hits = [f for f in folders if os.path.normcase(full) == os.path.normcase(f["path"])
                or os.path.normcase(full).startswith(os.path.normcase(f["path"]).rstrip("\\/") + os.sep)]
        if not hits:
            raise PermissionError(f"路径不在你可用的文件夹里：{raw}")
        folder = max(hits, key=lambda f: len(f["path"]))
        rel = os.path.relpath(full, folder["path"])
    else:
        folder, rel = folders[0], raw
    resolved, rel_posix = W.resolve(folder["path"], rel, write=write)
    if write and folder.get("access") != "write":
        raise PermissionError(f"文件夹是只读的：{folder['path']}")
    return folder, str(resolved), rel_posix


def _display(folder: dict, rel: str) -> str:
    return os.path.join(folder["path"], rel.replace("/", os.sep)) if rel not in ("", ".") else folder["path"]


def list_dir(a: dict, path: str = ".", depth: int = 1, limit: int = 300) -> str:
    folder, full, rel = locate(a, path)
    if not os.path.isdir(full):
        raise ValueError(f"不是目录：{_display(folder, rel)}")
    depth = max(1, min(int(depth or 1), 4))
    out, base_depth = [], full.rstrip("\\/").count(os.sep)
    for root, dirs, files in os.walk(full):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        lvl = root.rstrip("\\/").count(os.sep) - base_depth
        for d in dirs:
            out.append(os.path.relpath(os.path.join(root, d), full).replace("\\", "/") + "/")
        for f in sorted(files):
            p = os.path.join(root, f)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = -1
            out.append(f"{os.path.relpath(p, full).replace(chr(92), '/')}  ({size} B)")
        if lvl + 1 >= depth:
            dirs[:] = []
        if len(out) >= limit:
            break
    shown = out[:limit]
    tail = f"\n[只列了前 {limit} 项，缩小范围或加深 depth 再看]" if len(out) > limit else ""
    return f"=== {_display(folder, rel)} ===\n" + ("\n".join(shown) or "(空)") + tail


def search(a: dict, query: str, path: str = ".", glob: str = "", regex: bool = False, limit: int = 100) -> str:
    if not str(query or "").strip():
        raise ValueError("query 不能为空")
    folder, full, rel = locate(a, path)
    pat = re.compile(query if regex else re.escape(query), re.I)
    hits, scanned = [], 0
    walk = [(full, [], [os.path.basename(full)])] if os.path.isfile(full) else os.walk(full)
    for root, dirs, files in walk:
        if dirs:
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if glob and not fnmatch.fnmatch(f, glob):
                continue
            p = os.path.join(root if os.path.isdir(full) else os.path.dirname(full), f)
            try:
                W.resolve(folder["path"], os.path.relpath(p, folder["path"]))
                if os.path.getsize(p) > 1024 * 1024:
                    continue
                with open(p, "rb") as fh:
                    data = fh.read()
                if b"\x00" in data[:4096]:
                    continue
                text = data.decode("utf-8", "ignore")
            except (OSError, ValueError, PermissionError):
                continue
            scanned += 1
            for i, line in enumerate(text.splitlines(), 1):
                if pat.search(line):
                    hits.append(f"{os.path.relpath(p, folder['path']).replace(chr(92), '/')}:{i}: {line.strip()[:240]}")
                    if len(hits) >= limit:
                        break
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break
    head = f"=== 在 {_display(folder, rel)} 里找「{query}」：{len(hits)} 处（扫了 {scanned} 个文件，路径相对 {folder['path']}）==="
    return head + "\n" + ("\n".join(hits) or "(没找到)") + (f"\n[只列了前 {limit} 处]" if len(hits) >= limit else "")


def read(a: dict, paths: list, start_line: int = 1, end_line=None) -> str:
    blocks = []
    for p in paths or []:
        try:
            folder, full, rel = locate(a, p)
            blocks.append(W.read(folder["path"], [rel], start_line, end_line).replace(f"=== {rel} ===", f"=== {full} ===", 1))
        except (PermissionError, ValueError, OSError) as exc:
            blocks.append(f"=== {p} ===\nERROR: {exc}")
    return "\n\n".join(blocks) or "ERROR: 没给 paths"


# ---------------------------------------------------------------- 写入 ----
# 不直接用 named_workspace_io.write / patch：那两个会把整份文件改成 LF（用户的仓库多是 CRLF，一次小改就变成整文件 diff），
# 还会在目标文件夹里建 .workbench-locks/（用户自己的仓库里多出未跟踪目录）。这里保留原文件的换行和 BOM，锁放在 console/direct/locks/。
def _eol(data: bytes) -> str:
    return "\r\n" if data and data.count(b"\r\n") * 2 > data.count(b"\n") else "\n"


def _encode(text: str, eol: str, bom: bool) -> bytes:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if eol == "\r\n":
        text = text.replace("\n", "\r\n")
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


def _atomic_bytes(full: str, body: bytes) -> None:
    import tempfile
    os.makedirs(os.path.dirname(full), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".direct-write-", dir=os.path.dirname(full))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, full)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _file_lock(full: str):
    key = hashlib.sha256(os.path.normcase(full).encode("utf-8")).hexdigest()
    return file_mutex(os.path.join(DIRECT_DIR, "locks", key + ".mutex"))


def write_file(full: str, content: str, expected_version=None) -> str:
    from pathlib import Path
    with _file_lock(full):
        data = W._check(Path(full), expected_version)
        eol = _eol(data) if data else ("\r\n" if "\r\n" in content else "\n")
        body = _encode(content, eol, bool(data and data.startswith(b"\xef\xbb\xbf")))
        _atomic_bytes(full, body)
    return f"OK: {full}\nversion: {W.version(body)}"


def patch_file(full: str, old_text: str, new_text: str, expected_version: str) -> str:
    from pathlib import Path
    if not old_text:
        raise ValueError("old_text 不能为空")
    if not expected_version or expected_version == "missing":
        raise ValueError("改已有文件要带 direct_read 返回的 version")
    with _file_lock(full):
        data = W._check(Path(full), expected_version)
        text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        old = old_text.replace("\r\n", "\n").replace("\r", "\n")
        n = text.count(old)
        if n != 1:
            raise ValueError(f"old_text 要在文件里恰好出现一次（现在 {n} 次）；多带几行上下文再试")
        body = _encode(text.replace(old, new_text.replace("\r\n", "\n"), 1), _eol(data), data.startswith(b"\xef\xbb\xbf"))
        _atomic_bytes(full, body)
    return f"OK: {full}\nversion: {W.version(body)}"


# ---------------------------------------------------------------- 判定 ----
def _cmd_match(cmd: str, prefixes) -> bool:
    c = " ".join(cmd.split()).lower()
    for p in prefixes:
        p = " ".join(str(p).split()).lower()
        if p and (c == p or c.startswith(p + " ")):
            return True
    return False


def classify_command(a: dict, cmd: str) -> tuple:
    """命令 → (allow | approval | deny, 原因)。只看命令文本与模式，不看文件夹（命令本来就不受文件夹约束）。"""
    mode = a.get("mode") or DEFAULT_MODE
    cmd = (cmd or "").strip()
    if not cmd:
        return "deny", "命令为空"
    if mode == "bypass":
        return "allow", "完全放开"
    plain = not (set(cmd) & SHELL_META) and "--output" not in cmd and "--ext-diff" not in cmd
    if plain and _cmd_match(cmd, READONLY_COMMANDS):
        return "allow", "只读命令"
    if mode == "readonly":
        return "deny", "只读模式只能跑只读命令（" + "、".join(READONLY_COMMANDS[:6]) + " 等），不能带 ; | & > $( ) 这类串联符号"
    if OUTBOUND_RE.search(cmd):
        return "approval", "外发 / 不可逆命令（推送、PR、Release 等）需要用户批准"
    if mode == "auto_edit" and plain and _cmd_match(cmd, a.get("auto_commands") or []):
        return "allow", "在自动放行清单里"
    return "approval", "需要用户批准" + ("（命令带串联 / 重定向符号，不能自动放行）" if not plain else "")


def classify_write(a: dict) -> tuple:
    mode = a.get("mode") or DEFAULT_MODE
    if mode == "readonly":
        return "deny", "只读模式不能写文件"
    if mode == "ask":
        return "approval", "逐项审批模式：写文件要用户批准"
    return "allow", "可写文件夹"


def _run_cwd(a: dict, cwd: str) -> str:
    if cwd:
        folder, full, _ = locate(a, cwd)
        if not os.path.isdir(full):
            raise ValueError(f"cwd 不是目录：{full}")
        return full
    folders = _folders(a)
    if not folders:
        raise PermissionError("还没给你配置任何文件夹，请用户在工作台里添加")
    return next((f["path"] for f in folders if f.get("access") == "write"), folders[0]["path"])


# ---------------------------------------------------------------- 执行 ----
def _shell_argv(cmd: str) -> list:
    """Windows：PowerShell 5.1 + -EncodedCommand（UTF-16LE base64），引号、管道原样到位，不经 cmd 的转义；
    输出统一成 UTF-8。其它平台：sh -c。"""
    if os.name != "nt":
        return ["sh", "-c", cmd]
    import base64
    script = ("$ErrorActionPreference='Continue'; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
              "$OutputEncoding=[System.Text.Encoding]::UTF8; $env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'\n"
              + cmd + "\nif ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }")
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc]


async def _run_async(cmd: str, cwd: str, timeout: int) -> str:
    import runtime_exec as RX
    try:
        res = await RX.run(_shell_argv(cmd), cwd=cwd, timeout=timeout, tail_bytes=RUN_TAIL, lane="run",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "GIT_TERMINAL_PROMPT": "0"})
    except FileNotFoundError as e:
        return f"exit_code: not_found\n{e}"
    return f"cwd: {cwd}\n" + res.render(RUN_TAIL // 1024)


def _do(op: dict) -> str:
    """按记录原样执行一个操作（同步；命令在新事件循环里跑）。op 已通过判定或已被批准。"""
    a = get(op["agent"])
    if not a:
        raise PermissionError("直连 Agent 已删除")
    kind = op["kind"]
    if kind == "write":
        _, full, _ = locate(a, op["path"], write=True)
        return write_file(full, op["content"], op.get("expected_version"))
    if kind == "patch":
        _, full, _ = locate(a, op["path"], write=True)
        return patch_file(full, op["old_text"], op["new_text"], op["expected_version"])
    if kind == "run":
        cwd = _run_cwd(a, op.get("cwd") or "")
        timeout = max(1, min(int(op.get("timeout_sec") or 120), int(a.get("run_timeout_max") or 600)))
        return asyncio.run(_run_async(op["command"], cwd, timeout))
    raise ValueError(f"未知操作 {kind}")


async def do_async(op: dict) -> str:
    """MCP 进程里直接执行（已放行的操作）：命令走当前事件循环，文件操作放线程里。"""
    if op["kind"] == "run":
        a = get(op["agent"])
        cwd = _run_cwd(a, op.get("cwd") or "")
        timeout = max(1, min(int(op.get("timeout_sec") or 120), int(a.get("run_timeout_max") or 600)))
        return await _run_async(op["command"], cwd, timeout)
    return await asyncio.to_thread(_do, op)


# ---------------------------------------------------------------- 审批 ----
def op_hash(op: dict) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(op, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _approval_path(ap_id: str) -> str:
    if not re.fullmatch(r"ap-[0-9a-z-]{6,40}", ap_id or ""):
        raise ValueError("审批编号不对")
    return os.path.join(APPROVALS, ap_id + ".json")


def _preview(a: dict, op: dict) -> str:
    try:
        if op["kind"] == "run":
            return f"在 {op.get('cwd') or _run_cwd(a, '')} 执行：\n{op['command']}"
        _, full, _ = locate(a, op["path"])
        old = ""
        if os.path.isfile(full):
            with open(full, encoding="utf-8-sig", errors="replace") as fh:
                old = fh.read()
        new = op["content"] if op["kind"] == "write" else old.replace(op["old_text"], op["new_text"], 1)
        diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "改前", "改后", lineterm="", n=2))
        more = f"\n…（还有 {len(diff) - PREVIEW_LINES} 行没显示）" if len(diff) > PREVIEW_LINES else ""
        return f"{'新建' if not os.path.isfile(full) else '修改'} {full}\n" + "\n".join(diff[:PREVIEW_LINES]) + more
    except Exception as exc:  # 预览失败不挡审批，执行时还会再查一次
        return f"（预览生成失败：{exc}）"


def request_approval(a: dict, op: dict, reason: str, why: str = "") -> dict:
    os.makedirs(APPROVALS, exist_ok=True)
    ap_id = "ap-" + dt.datetime.now().strftime("%m%d%H%M%S") + "-" + secrets.token_hex(2)
    summary = (f"运行 {op['command'][:120]}" if op["kind"] == "run" else f"{'写' if op['kind'] == 'write' else '改'} {op['path']}")
    rec = {"id": ap_id, "agent": a["id"], "agent_name": a.get("name", ""), "kind": op["kind"], "op": op, "op_hash": op_hash(op),
           "summary": summary, "why": str(why or "")[:500], "reason": reason, "preview": _preview(a, op),
           "status": "pending", "created": now()}
    B.write_json(_approval_path(ap_id), rec)
    _activity(a["id"], op["kind"], f"申请批准：{summary}", "pending")
    try:
        import bus_events as EV
        EV.emit("direct.approval_requested", {"id": ap_id, "agent": a["id"], "agent_name": a.get("name", ""), "summary": summary})
    except Exception:
        pass
    return rec


def get_approval(ap_id: str) -> dict | None:
    return _read(_approval_path(ap_id), None)


def _update_approval(ap_id: str, fn) -> dict:
    path = _approval_path(ap_id)
    with file_mutex(os.path.join(DIRECT_DIR, "locks", ap_id + ".mutex")):
        rec = _read(path, None)
        if not rec:
            raise ValueError(f"没有这条审批：{ap_id}")
        fn(rec)
        B.write_json(path, rec)
        return rec


def list_approvals(agent_id: str = "", statuses=(), limit: int = 200) -> list:
    try:
        names = sorted((n for n in os.listdir(APPROVALS) if n.endswith(".json")), reverse=True)
    except OSError:
        return []
    out = []
    for n in names:
        rec = _read(os.path.join(APPROVALS, n), None)
        if not rec or (agent_id and rec.get("agent") != agent_id) or (statuses and rec.get("status") not in statuses):
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def expire_stale() -> None:
    """待批超过 24 小时作废；执行中超过时限还没落结果（进程没了）落 unknown，不自动重试；处理完超过 30 天的记录清掉。"""
    cutoff = time.time() - PENDING_TTL_H * 3600
    for n in (os.listdir(APPROVALS) if os.path.isdir(APPROVALS) else []):
        full = os.path.join(APPROVALS, n)
        try:
            if n.endswith(".json") and os.path.getmtime(full) < time.time() - 30 * 86400:
                rec = _read(full, None) or {}
                if rec.get("status") not in ("pending", "running"):
                    os.remove(full)
        except OSError:
            pass
    for rec in list_approvals(statuses=("pending", "running")):
        try:
            created = dt.datetime.fromisoformat(rec.get("started_at") or rec["created"]).timestamp()
        except (KeyError, ValueError):
            continue
        if rec["status"] == "pending" and created < cutoff:
            _update_approval(rec["id"], lambda r: r.update(status="expired", decided_at=now()) if r["status"] == "pending" else None)
        elif rec["status"] == "running" and created < time.time() - 1800 - 60:
            _update_approval(rec["id"], lambda r: r.update(status="unknown", result="执行中工作台进程退出，结果不明；没有自动重试，请人工核对后再决定") if r["status"] == "running" else None)


def decide(ap_id: str, decision: str, note: str = "", background: bool = True) -> dict:
    """用户在工作台批准 / 拒绝。批准 = 按记录原样执行一次（后台线程），记录 op 被改过就拒绝执行。"""
    if decision not in ("approve", "deny"):
        raise ValueError("decision 只能是 approve / deny")
    state = {}

    def apply(r):
        if r["status"] != "pending":
            raise ValueError(f"这条审批已是「{r['status']}」，不能再处理")
        if op_hash(r["op"]) != r.get("op_hash"):
            r.update(status="failed", decided_at=now(), result="审批记录里的操作被改动过，拒绝执行")
            state["tampered"] = True
            return
        r.update(status="running" if decision == "approve" else "denied", decided_at=now(), started_at=now(), note=str(note or "")[:300])
    rec = _update_approval(ap_id, apply)
    _activity(rec["agent"], "user", f"{'批准' if decision == 'approve' else '拒绝'}：{rec['summary']}", "ok")
    if rec["status"] == "running":
        if background:
            threading.Thread(target=_execute, args=(ap_id,), daemon=True, name=f"direct-{ap_id}").start()
        else:
            _execute(ap_id)
    return rec


def _execute(ap_id: str) -> None:
    rec = get_approval(ap_id) or {}
    try:
        result, status = _do(rec["op"]), "done"
    except Exception as exc:
        result, status = f"ERROR: {type(exc).__name__}: {exc}", "failed"
    _update_approval(ap_id, lambda r: r.update(status=status, result=str(result)[-RUN_TAIL:], finished_at=now()))
    _activity(rec.get("agent", ""), rec.get("kind", ""), f"已执行（批准后）：{rec.get('summary', '')}", "ok" if status == "done" else "failed")


def approval_text(rec: dict) -> str:
    st = rec["status"]
    if st == "pending":
        return (f"PENDING {rec['id']}：「{rec['summary']}」在等用户批准（{rec['reason']}）。"
                f"请告诉用户去工作台「Agent → 直连 Agent」批准，然后调用 direct_approval(approval_id=\"{rec['id']}\") 取结果；"
                "不要换个写法绕开审批。")
    if st == "running":
        return f"RUNNING {rec['id']}：用户已批准，正在执行。稍后再调用 direct_approval(approval_id=\"{rec['id']}\") 取结果。"
    if st == "denied":
        return f"DENIED {rec['id']}：用户拒绝了「{rec['summary']}」。" + (f"用户留言：{rec['note']}" if rec.get("note") else "先问用户想怎么做。")
    if st in ("expired", "cancelled"):
        return f"{st.upper()} {rec['id']}：这条审批已作废，没有执行。"
    return f"{'DONE' if st == 'done' else st.upper()} {rec['id']}（批准后执行）：\n{rec.get('result', '')}"


async def wait_approval(ap_id: str, timeout: int = WAIT_SEC) -> str:
    deadline = time.monotonic() + max(1, min(int(timeout), WAIT_SEC))
    while True:
        rec = get_approval(ap_id)
        if not rec:
            return f"ERROR: 没有这条审批：{ap_id}"
        if rec["status"] not in ("pending", "running") or time.monotonic() >= deadline:
            return approval_text(rec)
        await asyncio.sleep(1)


async def perform(a: dict, op: dict, why: str = "") -> str:
    """direct_write / direct_patch / direct_run 的共同入口：判定 → 放行就执行 / 要批准就排队并等一会儿 / 拒绝就说明。"""
    if op["kind"] == "run":
        verdict, reason = classify_command(a, op["command"])
        _run_cwd(a, op.get("cwd") or "")            # cwd 越界当场报错，不进审批
    else:
        locate(a, op["path"], write=True)            # 越界 / 只读文件夹 / 凭据文件当场报错
        verdict, reason = classify_write(a)
    op = {**op, "agent": a["id"]}
    if verdict == "deny":
        _activity(a["id"], op["kind"], f"拒绝：{reason}", "denied")
        return f"DENIED：{reason}"
    if verdict == "allow":
        result = await do_async(op)
        brief = op["command"][:120] if op["kind"] == "run" else op["path"]
        _activity(a["id"], op["kind"], brief, "failed" if result.startswith("ERROR") else "ok")
        return result
    rec = request_approval(a, op, reason, why)
    return await wait_approval(rec["id"])


# ---------------------------------------------------------------- 任务 ----
def _tasks_path(agent_id: str) -> str:
    return os.path.join(TASKS, f"{agent_id}.json")


def tasks(agent_id: str) -> list:
    return list(_read(_tasks_path(agent_id), []) or [])


def add_task(agent_id: str, text: str) -> dict:
    if not get(agent_id):
        raise ValueError(f"没有这个直连 Agent：{agent_id}")
    text = str(text or "").strip()
    if not text:
        raise ValueError("任务内容不能为空")
    os.makedirs(TASKS, exist_ok=True)
    path = _tasks_path(agent_id)
    with file_mutex(os.path.join(DIRECT_DIR, "locks", "tasks-" + agent_id + ".mutex")):
        items = tasks(agent_id)
        item = {"id": "t" + dt.datetime.now().strftime("%m%d%H%M%S") + secrets.token_hex(1), "text": text[:8000], "status": "open", "created": now()}
        items.insert(0, item)
        B.write_json(path, items[:100])
    _activity(agent_id, "user", f"派了任务：{text[:80]}", "ok")
    return item


def update_task(agent_id: str, task_id: str, status: str, result: str = "") -> dict:
    if status not in ("open", "done", "cancelled"):
        raise ValueError("status 只能是 open / done / cancelled")
    path = _tasks_path(agent_id)
    with file_mutex(os.path.join(DIRECT_DIR, "locks", "tasks-" + agent_id + ".mutex")):
        items = tasks(agent_id)
        hit = next((t for t in items if t.get("id") == task_id), None)
        if not hit:
            raise ValueError(f"没有这个任务：{task_id}")
        hit["status"] = status
        if result:
            hit["result"] = str(result)[:20000]
        hit["updated"] = now()
        B.write_json(path, items)
    return hit


def tasks_text(agent_id: str) -> str:
    open_items = [t for t in tasks(agent_id) if t.get("status") == "open"]
    if not open_items:
        return "工作台上没有给你的待办任务。"
    return "工作台给你的待办任务（做完用 direct_tasks(action=\"done\", task_id=…, result=…) 回报）：\n" + "\n\n".join(
        f"- [{t['id']}] {t['created']}\n{t['text']}" for t in reversed(open_items))


# ---------------------------------------------------------------- 提示词 ----
def _project_brief(slug: str) -> str:
    try:
        import bus_projects as P
        p = P.load_project(slug)
    except Exception:
        return f"- {slug}（读不到项目记录）"
    lines = [f"- {p.get('name') or slug}（{slug}）：{p.get('description') or '没有简介'}"]
    if p.get("repo_dir"):
        lines.append(f"  施工仓库：{p['repo_dir']}")
    for f in p.get("folders") or []:
        lines.append(f"  项目文件夹：{f.get('path')}（{'可写' if f.get('access') == 'write' else '只读'}）{f.get('label') or ''}")
    nxt = [str(x) for x in (p.get("next") or [])][:5]
    if nxt:
        lines.append("  下一步：" + "；".join(nxt))
    try:
        decs = sorted(P.decisions(slug), key=lambda d: d.get("updated") or d.get("created") or "", reverse=True)[:5]
        for d in decs:
            lines.append(f"  最近：{d.get('title') or d.get('task_id')}（{d.get('status')}）")
    except Exception:
        pass
    return "\n".join(lines)


def system_prompt(a: dict) -> str:
    """direct_info 的正文 = 这个直连 Agent 的工作守则。外部窗口连上后第一件事就是读它。"""
    mode = a.get("mode") or DEFAULT_MODE
    label, detail = MODES.get(mode, MODES[DEFAULT_MODE])
    folders = _folders(a)
    fl = "\n".join(f"- {f['path']}  [{'可写' if f.get('access') == 'write' else '只读'}]" + (f"  {f['label']}" if f.get("label") else "")
                   for f in folders) or "- （还没有。请用户在工作台「Agent → 直连 Agent → 设置」里添加，没有文件夹时所有文件工具都会拒绝）"
    auto = "、".join(a.get("auto_commands") or []) or "（无）"
    shell = "Windows PowerShell 5.1（用 ; 串联，不支持 &&）" if os.name == "nt" else "sh"
    parts = [
        f"你是「{a.get('name')}」，Agent 协作台的**直连 Agent**。用户直接在这个窗口里和你对话、给你派活。"
        "你不属于 Leader 带的团队：不领团队任务、不调用 bus_* 工具、不需要循环待命，答完就等用户下一句话。",
        "## 你能碰的范围\n文件夹（文件工具只能读写这些目录；路径写绝对路径，或相对第一个文件夹）：\n" + fl,
        f"权限模式：**{label}** —— {detail}\n只读命令（任何模式都直接执行）：{'、'.join(READONLY_COMMANDS)}\n"
        + (f"自动放行的命令前缀：{auto}\n" if mode == "auto_edit" else "")
        + f"命令在 {shell} 里执行，起始目录默认是第一个可写文件夹；单条命令最长 {a.get('run_timeout_max') or 600} 秒。",
    ]
    if a.get("projects"):
        parts.append("## 相关项目\n" + "\n".join(_project_brief(s) for s in a["projects"]))
    if a.get("instructions"):
        parts.append("## 用户写给你的说明\n" + a["instructions"].strip())
    if a.get("context_files"):
        parts.append("## 建议先读的文件\n" + "\n".join(f"- {x}" for x in a["context_files"]) + "\n（需要时用 direct_read 读，不必一次全读。）")
    parts.append(
        "## 工作方式\n"
        "1. 动手前先用 direct_list / direct_search / direct_read 弄清现状，不凭印象改。改已有文件必须带 direct_read 返回的 version；"
        "新建文件 expected_version 填 \"missing\"。小改动优先 direct_patch。\n"
        "2. 写文件、跑命令可能要用户批准。工具返回 PENDING 时，用一句话告诉用户你在等什么批准，然后调用 direct_approval 取结果；"
        "被拒绝（DENIED）就停下问用户，**不要换个写法、换条路径绕开审批或拒绝**。\n"
        "3. git：提交前先 git status / git diff 自查，提交信息写清改了什么；推送、建 PR、合并、发 Release、删分支这类对外或不可逆的动作，"
        "即使工具放行，也先在对话里讲清楚要做什么并等用户说可以。\n"
        "4. 不读凭据文件（令牌、密钥、.env 等，工具会拒绝）；不把令牌、密码写进文件或回复。\n"
        "5. 跑测试、跑脚本后，如实报告真实输出；失败就说失败，不删断言、不改测试来刷绿。\n"
        "6. 回报：中文，先说结论——做了什么、改了哪些文件、怎么验证的；区分 [已验证]（真跑过 / 真看过）和 [推断]。需要用户拍板的事集中列出来。\n"
        "7. 工作台上用户也可能给你派任务：用户说「看看任务」时调用 direct_tasks；做完用 direct_tasks(action=\"done\") 回报结果。")
    pending = [t for t in tasks(a["id"]) if t.get("status") == "open"]
    if pending:
        parts.append(f"## 工作台上有 {len(pending)} 个待办任务\n调用 direct_tasks 查看。")
    return "\n\n".join(parts)
