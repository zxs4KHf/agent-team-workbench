"""Runtime settings surfaced in the workbench UI.

Settings live in settings.json next to this file. Two rules shape this module:

1. Nothing here can tighten ws_run. It executes host-equivalent code, so a UI
   toggle cannot contain it; the honest thing is to label that clearly rather
   than imply a sandbox that does not exist.
2. Changing the served workspace only affects ws_read / ws_write / the context
   file picker. It takes effect after the MCP server restarts, because
   bus_core.WORKSPACE is resolved at import time.
"""
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HERE, "settings.json")
WORKSPACE_CFG = os.path.join(HERE, "workspace.json")
AUDIT_LOG = os.path.join(HERE, "console", "ws_run_audit.log")

DEFAULTS = {
    "workspace": ".",
    "run_enabled": True,
    "run_allow": ["py -3 ", "python ", "node ", "npm ", "npx ", "pip ",
                  "git status", "git diff", "git log", "git add", "git commit",
                  "git restore", "git show", "ls", "dir", "where", "tasklist", "taskkill"],
    "run_timeout_max": 120,
    "audit_enabled": True,
    "write_protected": ["console", "peers"],
    "peer_tools": {},
    # Per-agent credentials and roots. peer_tokens: {peer: token}; a request that
    # authenticates with one of these is bound to that peer identity. peer_roots:
    # {peer: directory}; ws_read/ws_write/ws_patch for that peer resolve inside it.
    "peer_tokens": {},
    "peer_roots": {},
    # Leader 通知节流：complete 未合并任务每 notify_cooldown_sec 再提醒一次；
    # ready_for_merge / complete 超过 stale_merge_min 分钟未合并 → wait 返回体附 STALE_MERGE
    "notify_cooldown_sec": 300,
    "stale_merge_min": 15,
    # 概览「简介体检」的提醒线：新卡里用兜底句的占比超过它，就提醒 Leader 补写 brief（0–1，默认 0.5）
    "brief_alert_ratio": 0.5,
}
MCP_PORT = 8791
MCP_HEALTH = f"http://127.0.0.1:{MCP_PORT}/health"
MCP_LAUNCHER = os.path.join(HERE, "start_named_mcp.py")
PEER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# A relocated workbench must never hand agents a parent directory by accident.
FORBIDDEN_ROOTS = ("c:\\windows", "c:\\program files", "c:\\program files (x86)",
                   "c:\\users\\default", "/etc", "/usr", "/bin", "/sbin")


def load() -> dict:
    data = dict(DEFAULTS)
    data["_config_error"] = ""
    if os.path.exists(SETTINGS):
        try:
            with open(SETTINGS, encoding="utf-8-sig") as fh:
                stored = json.load(fh) or {}
            for key in DEFAULTS:
                if key in stored:
                    data[key] = stored[key]
        except (OSError, ValueError) as exc:
            # Fail closed. A corrupt settings file must never silently restore
            # the permissive defaults -- that would turn a typo into a security
            # downgrade. Deny command execution until a human fixes the file.
            data["run_enabled"] = False
            data["run_allow"] = []
            data["_config_error"] = "settings.json 无法解析（%s）；已临时禁用命令执行。" % exc
    # workspace.json stays the single source of truth for the served root.
    try:
        with open(WORKSPACE_CFG, encoding="utf-8-sig") as fh:
            target = (json.load(fh) or {}).get("workspace")
        if target:
            data["workspace"] = target
    except (OSError, ValueError):
        pass
    return data


def _atomic_write(path: str, payload: dict) -> None:
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def validate_workspace(value: str) -> str:
    """Return an absolute path or raise ValueError explaining the refusal."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("工作区不能为空")
    target = raw if os.path.isabs(raw) else os.path.join(HERE, raw)
    target = os.path.abspath(target)
    if not os.path.isdir(target):
        raise ValueError(f"目录不存在：{target}")
    low = target.lower().replace("/", "\\")
    for bad in FORBIDDEN_ROOTS:
        if low == bad.replace("/", "\\") or low.startswith(bad.replace("/", "\\") + "\\"):
            raise ValueError(f"拒绝把系统目录设为工作区：{target}")
    drive, tail = os.path.splitdrive(target)
    if drive and tail.strip("\\/") == "":
        raise ValueError("拒绝把整个磁盘根目录设为工作区")
    return target


def save(patch: dict) -> dict:
    """Apply a partial update. Unknown keys are ignored rather than stored.
    整个读-改-写在跨进程互斥锁内完成：工作台多线程（/api/prompt 自动轮换 peer token）
    与多 Leader 并发保存时，无锁会后写覆盖先写，已发出的 token 静默失效。"""
    from bus_locks import file_mutex
    with file_mutex(SETTINGS + ".mutex"):
        return _save_locked(patch)


def _save_locked(patch: dict) -> dict:
    current = load()
    if current.get("_config_error"):
        # Refuse to write on top of a file we could not read: saving now would
        # silently discard whatever the user actually had in there.
        raise ValueError(current["_config_error"] + " 请先手工修复该文件再保存。")
    workspace_changed = False

    if "workspace" in patch:
        target = validate_workspace(patch["workspace"])
        raw = (patch["workspace"] or "").strip()
        workspace_changed = target != validate_workspace(current["workspace"])
        _atomic_write(WORKSPACE_CFG, {
            "_comment": "Workspace served to agents via ws_read/ws_write and the context-file "
                        "picker. Relative paths resolve against this project. Override with the "
                        "BUS_WORKSPACE environment variable.",
            "workspace": raw,
        })
        current["workspace"] = raw

    for key in ("run_enabled", "audit_enabled"):
        if key in patch:
            current[key] = bool(patch[key])
    if "brief_alert_ratio" in patch:
        ratio = float(patch["brief_alert_ratio"])
        if not 0.0 <= ratio <= 1.0:   # NaN 也过不了这条
            raise ValueError("brief_alert_ratio 要在 0 到 1 之间（例如 0.5 = 兜底句超过一半就提醒）")
        current["brief_alert_ratio"] = ratio
    if "run_timeout_max" in patch:
        current["run_timeout_max"] = max(5, min(int(patch["run_timeout_max"]), 600))
    if "run_allow" in patch:
        items = [str(x) for x in (patch["run_allow"] or []) if str(x).strip()]
        # An empty allowlist is never what the user meant; it would silently make
        # every command fail. Fall back to the defaults and say so.
        if not items:
            items = list(DEFAULTS["run_allow"])
            current["_notice"] = "白名单为空，已恢复默认白名单。"
        current["run_allow"] = items
    if "write_protected" in patch:
        current["write_protected"] = [str(x).strip().strip("/") for x in (patch["write_protected"] or []) if str(x).strip()]
    if "peer_tools" in patch and isinstance(patch["peer_tools"], dict):
        current["peer_tools"] = {str(k): list(v) for k, v in patch["peer_tools"].items()}
    if "peer_roots" in patch and isinstance(patch["peer_roots"], dict):
        roots = {}
        for name, root in patch["peer_roots"].items():
            name = _peer_name(name)
            root = (root or "").strip()
            if not root:
                continue  # empty = remove
            validate_workspace(root)
            roots[name] = root
        current["peer_roots"] = roots
    if "peer_tokens" in patch and isinstance(patch["peer_tokens"], dict):
        tokens = dict(current.get("peer_tokens") or {})
        for name, tok in patch["peer_tokens"].items():
            name = _peer_name(name)
            tok = (tok or "").strip()
            if tok == "":
                tokens.pop(name, None)          # empty = revoke
            elif tok == "__rotate__":
                tokens[name] = secrets.token_urlsafe(36)
            elif tok.startswith("****"):
                continue                        # masked value echoed back: keep as is
            else:
                if len(tok) < 32:
                    raise ValueError(f"{name} 的 token 至少 32 个字符")
                tokens[name] = tok
        current["peer_tokens"] = tokens

    stored = {k: current[k] for k in DEFAULTS if k != "workspace"}
    _atomic_write(SETTINGS, stored)
    result = load()
    result["_workspace_changed"] = workspace_changed
    if current.get("_notice"):
        result["_notice"] = current["_notice"]
    return result


def _peer_name(name) -> str:
    name = str(name or "").strip().lower()
    if not PEER_NAME_RE.match(name):
        raise ValueError(f"peer 名称非法：{name!r}（小写字母数字、下划线、连字符，不超过 32 位）")
    return name


def mask_token(tok: str) -> str:
    tok = tok or ""
    return "****" + tok[-4:] if len(tok) >= 8 else ("****" if tok else "")


def reveal_token(peer: str) -> str:
    """Return the full token once, for the user to copy into an agent window.
    Only the workbench (loopback) calls this; it is never included in describe()."""
    return (load().get("peer_tokens") or {}).get(_peer_name(peer), "")


def mcp_health(timeout: float = 2.5) -> dict:
    """Probe the loopback MCP server. Never raises."""
    t0 = time.time()
    try:
        with urllib.request.urlopen(MCP_HEALTH, timeout=timeout) as resp:
            data = json.load(resp)
        data["reachable"] = True
        data["latency_ms"] = int((time.time() - t0) * 1000)
        return data
    except Exception as exc:  # connection refused, timeout, bad JSON
        return {"reachable": False, "error": str(exc), "url": MCP_HEALTH}


def _graceful_shutdown() -> bool:
    """请 MCP 进程自行优雅退出（停止接收新连接、在途请求 bounded 收尾、子进程不残留）。
    旧版本进程没有 /admin/shutdown 端点 → False，调用方回退硬杀。"""
    try:
        with open(os.path.join(HERE, "console", "mcp_token.txt"), encoding="utf-8-sig") as fh:
            token = fh.read().strip()
        if len(token) < 32:
            return False
        req = urllib.request.Request("http://127.0.0.1:%d/admin/shutdown" % MCP_PORT, data=b"{}", method="POST",
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def mcp_restart(wait_sec: float = 12.0) -> dict:
    """Stop the bus_mcp process on MCP_PORT (if any) and start a fresh one via the
    launcher, without ever showing a console window. Returns the new health.
    先尝试优雅关停（在途的 wait 调用有 bounded 收尾窗口），失败或超时再硬杀。"""
    before = mcp_health()
    stopped = []
    graceful = False
    pid = before.get("pid")
    if before.get("reachable") and pid:
        graceful = _graceful_shutdown()
        if graceful:
            deadline = time.time() + 10
            while time.time() < deadline and _port_owners(MCP_PORT):
                time.sleep(0.3)
        if _port_owners(MCP_PORT):
            stopped = _kill_tree(int(pid))
    else:
        # Not answering /health but maybe still holding the port: find the owner.
        for owner in _port_owners(MCP_PORT):
            stopped += _kill_tree(owner)
    deadline = time.time() + 6
    while time.time() < deadline and _port_owners(MCP_PORT):
        time.sleep(0.3)
    launch = subprocess.run([sys.executable, "-X", "utf8", MCP_LAUNCHER, "--port", str(MCP_PORT)],
                            cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=60, **_quiet_kwargs())
    deadline = time.time() + wait_sec
    after = mcp_health()
    while time.time() < deadline and not after.get("reachable"):
        time.sleep(0.5)
        after = mcp_health()
    return {"stopped_pids": stopped, "graceful": graceful, "launcher_stdout": launch.stdout.strip()[-400:],
            "launcher_stderr": launch.stderr.strip()[-400:], "launcher_rc": launch.returncode,
            "before": before, "after": after, "ok": bool(after.get("reachable"))}


def _quiet_kwargs() -> dict:
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"creationflags": 0x08000000, "startupinfo": si, "stdin": subprocess.DEVNULL}  # CREATE_NO_WINDOW


def _port_owners(port: int) -> list:
    if os.name != "nt":
        return []
    try:
        # netstat prints in the console code page (GBK here), so decode bytes leniently.
        raw = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, timeout=15,
                             **_quiet_kwargs()).stdout or b""
        out = raw.decode("utf-8", "replace")
    except Exception:
        return []
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3] == "LISTENING":
            try:
                pids.add(int(parts[4]))
            except ValueError:
                pass
    return sorted(p for p in pids if p != os.getpid())


def _kill_tree(pid: int) -> list:
    if pid <= 0 or pid == os.getpid():
        return []
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=20, **_quiet_kwargs())
        else:
            os.kill(pid, 9)
        return [pid]
    except Exception:
        return []


def audit_tail(limit: int = 60) -> list:
    """Most recent ws_run invocations, newest first."""
    try:
        with open(AUDIT_LOG, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines[-limit:]:
        cols = line.split("\t")
        if len(cols) >= 3:   # new format: time, peer, command[, outcome]
            rows.append({"time": cols[0], "peer": "" if cols[1] == "-" else cols[1], "command": cols[2],
                         "outcome": cols[3] if len(cols) > 3 else ""})
        else:                # legacy two-column lines
            stamp, _, cmd = line.partition("\t")
            rows.append({"time": stamp, "peer": "", "command": cmd, "outcome": ""})
    rows.reverse()
    return rows


def describe(workspace_runtime: str) -> dict:
    """Everything the settings screen needs, including the honest caveats."""
    cfg = load()
    try:
        resolved = validate_workspace(cfg["workspace"])
        error = ""
    except ValueError as exc:
        resolved = ""
        error = str(exc)
    public = dict(cfg)
    public["peer_tokens"] = {k: mask_token(v) for k, v in (cfg.get("peer_tokens") or {}).items()}
    root_errors = {}
    for name, root in (cfg.get("peer_roots") or {}).items():
        try:
            validate_workspace(root)
        except ValueError as exc:
            root_errors[name] = str(exc)
    return {
        "settings": public,
        "peer_root_errors": root_errors,
        "mcp": mcp_health(),
        "resolved_workspace": resolved,
        "runtime_workspace": workspace_runtime,
        "workspace_error": error,
        # The running MCP server resolved its root at import time; a saved change
        # is inert until it restarts. Surfacing this prevents silent divergence.
        "restart_required": bool(resolved) and os.path.normcase(resolved) != os.path.normcase(workspace_runtime),
        "audit": audit_tail(),
        "audit_path": os.path.relpath(AUDIT_LOG, HERE).replace("\\", "/"),
        "env_override": os.environ.get("BUS_WORKSPACE", ""),
    }
