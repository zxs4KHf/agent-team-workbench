"""Self-hosted authenticated workspace MCP.
Local origin: http://127.0.0.1:8791/mcp. A public hostname (optional, e.g. a tunnel) is taken from the
Bridge URL registered in the workbench (console/bridge.json) or BUS_PUBLIC_HOST; nothing is hard-coded.
Use --token-file (private local file) or BUS_MCP_TOKEN. Never put tokens in prompts.
Workspace execution is for trusted agents and is NOT an operating-system sandbox.
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import hmac
import json
import re
import os
import shlex
import subprocess
import sys
import time
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bus_core as B  # noqa: E402
import named_workspace_io as W
import runtime_exec as RX  # noqa: E402  quiet subprocess runner: no console window, job-object tree kill
from bus_web import safe_workspace_path  # noqa: E402  复用工作区路径判定

BUS_TOOL = os.path.join(HERE, "bus_tool.py")
MAX_WAIT = 590
READ_LIMIT = 200 * 1024
RUN_TAIL = 8 * 1024
_BUS_REL = os.path.relpath(HERE, B.WORKSPACE).replace(os.sep, "/").strip("./")
_BUS_PREFIX = (_BUS_REL + "/") if _BUS_REL and _BUS_REL != "." else ""
# Bus control state is written through bus tools only, wherever the workbench lives.
WRITE_DENY = (".git/", ".shuncode/", _BUS_PREFIX + "console/", _BUS_PREFIX + "peers/")
RUN_ALLOW = ("py -3 ", "python ", "node ", "npm ", "npx ", "pip ",
             "git status", "git diff", "git log", "git add", "git commit", "git restore", "git show",
             "ls", "dir", "where", "tasklist")
AUDIT_LOG = os.path.join(HERE, "console", "ws_run_audit.log")
STARTED_AT = time.time()
SERVICE_VERSION = "multi-leader-20261001"
import bus_settings as SET  # noqa: E402

# Identity of the caller for the current request. Set by BearerAuth from the token
# that authenticated the request: "" for the master token, or the peer name whose
# per-peer token matched. Read by ws_* tools to pick the peer's root and by the audit.
CURRENT_PEER: contextvars.ContextVar[str] = contextvars.ContextVar("bus_mcp_peer", default="")


def _live(key: str, fallback):
    """Read a setting per call, not at import, so toggling it in the workbench takes
    effect on the next tool call instead of requiring a server restart. Only the
    served workspace still needs a restart, because bus_core resolves it on import."""
    try:
        return SET.load().get(key, fallback)
    except Exception:
        return fallback


def _audit_run(cmd: str, peer: str = "", outcome: str = "") -> None:
    """Append every ws_run invocation. ws_run executes host-equivalent code, so this
    log is the only forensic trail of what an agent actually did.
    Line format: <time>\t<peer or ->\t<command>[\t<outcome>]  (older lines have 2 columns)."""
    import datetime
    if not _live("audit_enabled", True):
        return
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
        stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")  # ADR 0010：带偏移
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp}\t{peer or '-'}\t{cmd}" + (f"\t{outcome}" if outcome else "") + "\n")
    except OSError:
        pass  # auditing must never break the call


def _peer_root(peer: str) -> str:
    """Workspace root for ws_read/ws_write/ws_patch. A teammate inside a build task gets
    its own git worktree; a peer with an entry in settings.peer_roots gets that directory;
    everyone else gets the served workspace."""
    if not peer:
        return B.WORKSPACE
    try:
        B.touch_work(peer)   # ws_* 调用 = 正在干活的证据（心跳刷成「在做手上那一单」）
    except Exception:
        pass
    try:
        import bus_build as BB
        wt = BB.teammate_root(peer)
        if wt:
            return wt
    except Exception:
        pass
    roots = _live("peer_roots", {}) or {}
    root = roots.get(peer)
    if not root:
        return B.WORKSPACE
    try:
        return SET.validate_workspace(root)
    except ValueError:
        return B.WORKSPACE


def _effective_peer(explicit: str | None) -> str:
    """Peer identity for a ws_* call. A per-peer token fixes the identity; the master
    token may pass peer= explicitly to act within that peer's root (for the controller
    reviewing a peer's work)."""
    bound = CURRENT_PEER.get()
    if bound:
        return bound
    return (explicit or "").strip()


def _bind_peer(peer: str) -> tuple[str, str]:
    """总线工具的 peer 参数必须与本连接的身份一致。返回 (生效 peer, 错误文本)。
    peer 令牌只能以本人身份调用：此前任一 peer token 传 peer="controller" 即可走完整
    Leader 通道，或传别人的名字领走别人的任务；主 token（身份为空）不受限。"""
    bound = CURRENT_PEER.get()
    peer = (peer or "").strip()
    if not bound:
        return peer, ""
    if peer and peer != bound:
        return "", f"ERROR: 本连接的身份是 peer {bound}，peer 参数只能是 {bound}"
    return bound, ""

# ---- SDK 兼容层：mcp 1.x = FastMCP，mcp 2.x = MCPServer ----------------------------------
try:  # mcp < 2
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore
    _SDK = 1
except ModuleNotFoundError:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server  # type: ignore
    _SDK = 2
try:
    from mcp.server.transport_security import TransportSecuritySettings  # 两版路径相同
except Exception:  # pragma: no cover
    TransportSecuritySettings = None  # type: ignore

INSTRUCTIONS = (
    "这是本机多模型对读总线（bus）的 MCP 入口。peer 会话循环调用 bus_wait_task → 作答 → bus_reply；"
    "Leader会话使用 bus_console / bus_dispatch / bus_collect / bus_status。"
    "工作区任务授权后可使用 ws_read/ws_write/ws_patch/ws_run；编辑已有文件须带 expected_version。"
    "本服务不是 ShunCode Bridge：不提供编辑器LSP/PTY等专属能力。Python/Node执行等同受信任宿主代码，不是OS沙箱。"
)
mcp = _Server("bus", instructions=INSTRUCTIONS)


# ---- 复用 bus_tool.py：子进程 + stdin JSON ------------------------------------------------
async def _tool(action: str, args: dict[str, Any], timeout: float) -> str:
    import json
    payload = json.dumps({k: v for k, v in args.items() if v is not None}, ensure_ascii=False)
    # bus_tool output is small (task packs, status text); 1 MB tail is effectively "all".
    res = await RX.run([sys.executable, BUS_TOOL, action], cwd=B.WORKSPACE, timeout=timeout + 30,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                       stdin_data=payload.encode("utf-8"), tail_bytes=1024 * 1024, merge_stderr=False)
    if res.exit_code == "timeout":
        raise RuntimeError(f"bus_tool {action} timed out; inspect task state before retrying")
    text = res.tail
    return text.strip() or f"(bus_tool {action} 无输出)"


def _clamp(v: int | None, default: int = MAX_WAIT) -> int:
    return max(5, min(int(v if v is not None else default), MAX_WAIT))


# ---- 六个总线工具（schema 与 manifest 一致） ---------------------------------------------
@mcp.tool(name="bus_wait_task", description=(
    "Peer 会话待命工具。阻塞轮询本会话的 inbox（每 2 秒），任务到达时立即返回完整任务包（问题、模式要求、上下文文件内容、回复方式）。"
    "无任务时最长阻塞 timeout_sec 秒后返回固定文本 'IDLE. Call bus_wait_task again.'——收到 IDLE 时不要输出任何文字，直接再次调用本工具。"
    "收到任务后按任务包作答并调用 bus_reply。"))
async def bus_wait_task(peer: str, model: str = "", timeout_sec: int = MAX_WAIT) -> str:
    """peer: 本会话的 peer 名称，小写字母数字下划线连字符；model: 可选，写入心跳；timeout_sec: 5–590，默认 590。"""
    peer, err = _bind_peer(peer)
    if err:
        return err
    t = _clamp(timeout_sec)
    return await _tool("wait", {"peer": peer, "model": model, "timeout_sec": t}, t)


@mcp.tool(name="bus_reply", description=(
    "Peer 会话回复工具。把对任务 task_id 的回答写入 outbox 并归档任务，返回 'DONE. Archived. Call bus_wait_task again.'。"
    "answer 为 Markdown 正文，只写结论与理由，不要复述任务包。回复后立即再次调用 bus_wait_task 继续待命。"))
async def bus_reply(task_id: str, peer: str, answer: str) -> str:
    peer, err = _bind_peer(peer)
    if err:
        return err
    return await _tool("reply", {"task_id": task_id, "peer": peer, "answer": answer}, 30)


@mcp.tool(name="bus_dispatch", description=(
    "Leader工具。把同一个问题连同上下文文件内容打包成任务，投递给一个或多个 peer 的 inbox，返回 task_id。"
    "mode：independent 独立作答 / redteam 红队找失败理由 / relay 接力补细节。context_files 为工作区相对路径，内容会被完整嵌入任务包。"
    "返回中若列出 OFFLINE 的 peer，说明其心跳超过 180 秒，需要用户去对应窗口开启待命或改派。"
    "peers 里写 \"auto\" = 由总线自动挑在线且空闲的 Agent（默认最多补 2 个，可用 max_peers 调整），返回里的 auto 字段给出选中理由与每个排除对象的理由；"
    "同一列表里显式点名的 peer 仍然照发，auto 只补选空闲的；自动挑选不到任何人时直接报错、任务不创建。"))
async def bus_dispatch(peers: list[str], question: str, mode: str = "independent", context_files: list[str] | None = None,
                       from_peer: str = "main", ttl_min: int = 30, angles: bool | dict[str, str] | None = None,
                       min_answers: int = 0, repo_dir: str = "", max_peers: int = 0, parent_task: str = "", intro: str = "") -> str:
    """peers: Agent名列表；列表里含 "auto" 时由总线自动挑选在线+空闲+手上没有未回复任务的 Agent（离线、时钟错乱、忙、正在代理 Leader、有积压的都会被排除并给出理由）。mode: discuss（只讨论，无写权限）/ build（每个Agent一棵 git worktree，回复自动 commit）/
    review（审查 diff，回复 PASS 或 RETURN）/ independent / redteam / relay。
    angles: true = 按内置九个切入角度轮流分给每个Agent；或 {Agent: 角度文字}；省略 = 同题同角度。
    min_answers: 到齐多少份回答就通知Leader合并（默认在线Agent过半数，至少 2；relay 为 1）。
    repo_dir: build 模式的项目目录（默认工作区根），没有 git 仓库会自动初始化。
    max_peers: 配 auto 用，auto 最多补选几个 Agent（默认 2，至少 1；显式点名的人不占这个额度）。"""
    if mode not in ("discuss", "build", "review", "independent", "redteam", "relay"):
        return "ERROR: mode 只能是 discuss / build / review / independent / redteam / relay"
    return await _tool("dispatch", {"peers": peers, "question": question, "mode": mode, "context_files": context_files or [],
                                    "from_peer": from_peer, "ttl_min": max(1, min(int(ttl_min), 1440)), "angles": angles,
                                    "min_answers": int(min_answers or 0), "repo_dir": repo_dir or None,
                                    "max_peers": int(max_peers or 0), "parent_task": parent_task or None, "intro": intro or None}, 90)


@mcp.tool(name="bus_build", description=(
    "施工任务流转（Leader用）。action=summary 查看各Agent提交/门禁/审查/合并状态；"
    "action=review 把某Agent的 diff 派给Reviewer（reviewers 省略则按 roles.json 自动挑在线Reviewer）；"
    "action=merge 把审查通过的分支 --no-ff 合并进主线，冲突时返回冲突文件；action=close 清理 worktree。"))
async def bus_build(action: str, task_id: str, teammate: str = "", reviewers: list[str] | None = None, ttl_min: int = 20) -> str:
    if action not in ("summary", "review", "merge", "close"):
        return "ERROR: action 只能是 summary / review / merge / close"
    if action in ("review", "merge") and not teammate:
        return "ERROR: review / merge 需要 teammate"
    return await _tool("build", {"action": action, "task_id": task_id, "teammate": teammate or None, "reviewers": reviewers, "ttl_min": int(ttl_min)}, 120)


@mcp.tool(name="bus_collect", description=(
    "Leader工具。阻塞等待 task_id 的全部 peer 回答到齐（每 2 秒检查），最长 timeout_sec 秒；返回所有已到的回答全文与未回复 peer 的在线状态。"
    "回答到齐后由Leader按 共识/分歧/最终方案/验证依据 四段合并，合并稿写入 workbench/bus/log/<日期>/<task_id>.merge.md，然后调用 bus_status 并传 archive=task_id 归档。"))
async def bus_collect(task_id: str, timeout_sec: int = MAX_WAIT) -> str:
    t = _clamp(timeout_sec)
    return await _tool("collect", {"task_id": task_id, "timeout_sec": t}, t)


@mcp.tool(name="bus_status", description=(
    "查看总线状态：所有 peer 的在线情况（心跳 180 秒内且状态为 idle/busy 视为在线）、模型名、最后活跃时间；未完成的 task_id；各 inbox 排队数。"
    "传 archive=task_id 时把该任务的回答与元数据从 outbox 移入 log/<日期>/。发 dispatch 之前先调用本工具确认目标 peer 在线。"
    "requeue+requeue_peer：把已领取/已过期任务退回待领取。输出含 expired 计数与 late_answers。"))
async def bus_status(archive: str = "", requeue: str = "", requeue_peer: str = "") -> str:
    if requeue and not requeue_peer:
        return "ERROR: requeue 需同时给 requeue_peer"
    return await _tool("status", {"archive": archive or None, "requeue": requeue or None, "requeue_peer": requeue_peer or None}, 30)


@mcp.tool(name="bus_console", description=(
    "Leader与浏览器工作台之间的双向通道。action=wait：阻塞等待，返回用户在网页上发出的指令或某个任务回答已到齐的通知；没有事件时返回 IDLE，需再次调用。"
    "action=ack：标记一条指令已处理（command_id, result, ok）；回执 dispatch 时请同时带上 task_id=<刚派出的任务号> 与 project=<项目 slug>，这两个结构化参数比解析 result 措辞可靠，缺了也不会把面板上的派发记录误判成已取消，只会挂一条 warning。"
    "action=post：把Leader的处理结果回写给工作台（project 必填；task_id+status/title/summary/brief/merge_path/risks 更新一条决策；"
    "brief 是卡面上唯一给用户看的一句话：〔起因或对象〕+〔做了什么〕+〔结果或要不要你动手〕，建议 45 字、硬上限 60 字，"
    "不含编号 / 提交号 / 站内黑话，且不能与 title 相同，不过关会被拒收并回显模板；summary 语义不变，仍是给 Leader 的流水账，收在详情抽屉里；"
    "没写 brief 且拼得出草稿时，post 回执末尾会附一行机器拼的草稿（未发布，卡面仍显示兜底句）：满意就再 post 一次带 brief_draft=true 一键采用，不满意自己写 brief（二选一）；"
    "用户在卡片上点「看不懂」会以 kind=brief_rewrite 指令出现在 wait 里（带 task_id 与卡面原文）：按 brief 模板重写后用 post 的 brief 回写，再 ack，卡片上的「待重写」随之消失；"
    "next 覆盖下一步计划列表；note 追加备注；bridge_url 登记当前 Bridge 地址；artifacts 登记交付物 [{path,label,kind}]；"
    "decisions_needed 登记「要用户拍板的问题」[{id,question,options:[{key,label,detail}],allow_free_text,decided:{choice,reason}}]——"
    "工作台会把它渲染成可点选的内联表单，用户回答以 kind=decision_answer 指令出现在 wait 里，处理后照常 ack；回答归系统保管，post 带 answer 会被丢弃，重发同 id 不会覆盖回答；能自己定的请直接填 decided 并写明理由，别让用户打字）。"
    "action=state：返回工作台完整状态 JSON。"
    "peer：代理 Leader时填自己的 peer 名（收到 RELAY_TAKEOVER 后）；真Leader省略。"
    "session：多 Leader 并行接入时的会话标识。首次 wait 返回体里的 SESSION=<id> 之后每次调用原样带回。"
    "name：action=wait 时报上你自己的显示名（例如 opus-5.5 Leader A），工作台按会话分别列出每位在线 Leader；不传则显示为会话号。"))
async def bus_console(action: str, timeout_sec: int | None = None, command_id: str = "", result: str = "", ok: bool = True,
                      project: str = "", task_id: str = "", status: str = "", title: str = "", summary: str = "",
                      merge_path: str = "", risks: list[str] | None = None, next: list[str] | None = None, note: str = "",
                      bridge_url: str = "", artifacts: list[dict[str, Any]] | None = None, peer: str = "", landed: bool | None = None,
                      decisions_needed: list[dict[str, Any]] | None = None, session: str = "", brief: str | None = None, name: str = "", brief_draft: bool = False) -> str:
    if action not in ("wait", "ack", "post", "state"):
        return "ERROR: action 只能是 wait / ack / post / state"
    if status and status not in ("draft", "dispatched", "collecting", "ready_for_merge", "merged", "adopted", "archived", "cancelled", "rejected", "superseded"):
        return "ERROR: status 取值非法"
    peer, err = _bind_peer(peer)
    if err:
        return err
    t = _clamp(timeout_sec) if action == "wait" else 30
    args = {"action": action, "timeout_sec": t if action == "wait" else None, "command_id": command_id or None, "result": result or None,
            "ok": ok, "project": project or None, "task_id": task_id or None, "status": status or None, "title": title or None,
            # brief：None=未传、""=清空，两者必须分开传到底（astra2 85c1 RETURN）。
            # 注释必须独占一行：写在行尾会把同一行后面的 merge_path / risks / next / note 一起注释掉，这四个字段就被静默丢了。
            "summary": summary or None, "brief": brief, "brief_draft": brief_draft or None,
            "merge_path": merge_path or None, "risks": risks, "next": next, "note": note or None,
            "bridge_url": bridge_url or None, "artifacts": artifacts, "peer": peer or None, "landed": landed,
            "decisions_needed": decisions_needed, "session": session or None, "name": name or None}
    return await _tool("console", args, t)


# ---- 三个受限工作区工具 ------------------------------------------------------------------
def _rel(path: str) -> str:
    return os.path.relpath(os.path.normpath(os.path.join(B.WORKSPACE, path)), B.WORKSPACE).replace("\\", "/")


@mcp.tool(name="ws_read", description="Read UTF-8 workspace files with SHA256 versions and line ranges. Credential/private paths denied; not an image reader.")
def ws_read(paths: list[str], start_line: int = 1, end_line: int | None = None, peer: str | None = None) -> str:
    """peer: optional; only honoured for the master token (per-peer tokens are bound to their own root)."""
    return W.read(_peer_root(_effective_peer(peer)), paths, start_line, end_line)


def _explain(exc: Exception) -> str:
    """Surface why a write was refused. Without this the MCP framework swallows the
    exception and every failure looks identical ("Error executing tool ws_write"),
    so a stale version, a missing anchor and a protected path are indistinguishable."""
    return f"ERROR: {type(exc).__name__}: {exc}"


@mcp.tool(name="ws_write", description="Atomically create/replace a workspace text file. Existing files REQUIRE expected_version from ws_read; use missing for create-only. Coordination still required with external writers.")
def ws_write(path: str, content: str, expected_version: str | None = None, peer: str | None = None) -> str:
    try:
        return W.write(_peer_root(_effective_peer(peer)), path, content, expected_version)
    except (PermissionError, ValueError, OSError) as exc:
        return _explain(exc)


@mcp.tool(name="ws_patch", description="Version-checked exact text patch. old_text must match once. Use expected_version from ws_read. No console/peer/credential writes.")
def ws_patch(path: str, old_text: str, new_text: str, expected_version: str, peer: str | None = None) -> str:
    try:
        return W.patch(_peer_root(_effective_peer(peer)), path, old_text, new_text, expected_version)
    except (PermissionError, ValueError, OSError) as exc:
        return _explain(exc)


@mcp.tool(name="ws_run", description=(
    "在工作区根目录执行白名单命令并返回 stdout+stderr 尾部 8 KB。允许前缀见 RUN_ALLOW："
    "py -3 / python / node / npm / npx / pip、git status|diff|log|add|commit|restore|show、ls / dir / where / tasklist。"
    "timeout_sec ≤ 120。每次调用都会记入 console/ws_run_audit.log。"
    "注意：执行的是宿主等权代码，不是沙箱；路径限制只约束 ws_read/ws_write，不约束本工具。"))
async def ws_run(command: str, timeout_sec: int = 60) -> str:
    cmd = command.strip()
    if not _live("run_enabled", True):
        return "ERROR: ws_run 已在工作台设置中关闭。"
    allow = tuple(_live("run_allow", list(RUN_ALLOW))) or RUN_ALLOW
    if not cmd or not any(cmd == p.strip() or cmd.startswith(p) for p in allow):
        return f"ERROR: 命令不在白名单内。允许前缀：{', '.join(repr(p) for p in allow)}"
    # Shell metacharacters are irrelevant here because the command is never handed to a
    # shell (create_subprocess_exec, no shell=True). Only reject what could still chain
    # commands through a shell that a child process might spawn itself. ";" is NOT in this
    # list: it is required by every Python/Node one-liner passed via -c / -e.
    if any(tok in cmd for tok in ("&&", "||", "|", ">", "<", "`", "$(")):
        return "ERROR: 不允许管道、重定向或命令串联"
    t = max(1, min(int(timeout_sec), int(_live("run_timeout_max", 120))))
    peer = CURRENT_PEER.get()
    try:
        B.touch_work(peer)   # 同 ws_read / ws_write：干活期间心跳不断
    except Exception:
        pass
    # posix=True on every platform: with posix=False shlex keeps the surrounding quotes,
    # so `py -3 -c "print(1)"` reaches Python as the literal string '"print(1)"', which
    # evaluates to a discarded string expression -> exit 0 with no output (silent failure).
    argv = shlex.split(cmd, posix=True)
    if os.name == "nt" and argv[0] in ("dir", "ls"):  # cmd 内建命令
        argv = ["cmd", "/c"] + argv
    # Working directory: the peer's own root when one is configured, otherwise the
    # served workspace. This is a convenience, not a boundary: code can still cd anywhere.
    cwd = _peer_root(peer)
    try:
        res = await RX.run(argv, cwd=cwd, timeout=t, tail_bytes=RUN_TAIL, lane="run",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    except FileNotFoundError as e:
        _audit_run(cmd, peer, "not_found")
        return f"ERROR: 找不到可执行文件 {argv[0]}（{e}）"
    _audit_run(cmd, peer, f"exit={res.exit_code} via={res.mechanism}")
    if res.truncated:
        res.notes.append("output truncated to tail")
    return res.render(RUN_TAIL // 1024)


# ---- Bearer 鉴权中间件 + 启动 ---------------------------------------------------------------
class BearerAuth:
    """纯 ASGI 中间件：除 /health 外所有 HTTP 请求都必须带 Authorization: Bearer <token>。
    接受主 token（身份为空 = Leader/管理员）或 settings.peer_tokens 里的任一 peer token（身份 = 该 peer 名）。
    peer token 每次请求从设置读取，工作台里改完立即生效，无需重启。"""

    def __init__(self, app, token: str):
        self.app, self.token = app, token.encode()

    def _identify(self, presented: bytes) -> str | None:
        if hmac.compare_digest(presented, self.token):
            return ""
        peers = _live("peer_tokens", {}) or {}
        for name, tok in peers.items():
            tok = (tok or "").strip()
            if len(tok) >= 32 and hmac.compare_digest(presented, tok.encode()):
                return str(name)
        return None

    def _known(self):
        yield self.token.decode(), ""
        for name, tok in (_live("peer_tokens", {}) or {}).items():
            tok = (tok or "").strip()
            if len(tok) >= 32:
                yield tok, str(name)

    def _from_path(self, scope) -> tuple[str | None, str]:
        # 链接直连：/mcp/<token> 把 token 放在路径里，剥掉后交给 MCP 应用；identity 与 Bearer 相同规则。
        # 只认**最后一个** /mcp/ 之后的那一段（手动切，不用正则回溯）：2026-10-02 前 Bridge 地址里带了主令牌，
        # 发出去的链接是 /mcp/<主令牌>/mcp/<Agent 令牌>[，黏上的字]。认最后一段 = 这个 Agent 自己；
        # 用正则时，后面黏了字会回溯成「第一段是令牌、其余是子路径」，把 Agent 认成 Leader——测试钉住了这一条。
        path = scope.get("path") or ""
        if not path.startswith("/mcp/"):
            return None, path
        rest = path.rsplit("/mcp/", 1)[1]
        m = re.match(r"^([A-Za-z0-9_\-]{16,})(/[A-Za-z0-9_\-/]*)?$", rest)
        if m:
            who = self._identify(m.group(1).encode())
            if who is not None:
                return who, "/mcp" + (m.group(2) or "")
        # 粘贴时链接后面黏上了字（「…<token>，然后」「…<token>and」）：按已知 token 前缀认，尾巴丢掉；只认已知 token
        for tok, who in self._known():
            if len(rest) > len(tok) and hmac.compare_digest(rest[:len(tok)].encode(), tok.encode()):
                tail = rest[len(tok):]
                sub = tail if re.match(r"^/[A-Za-z0-9_\-/]*$", tail) else ""
                return who, "/mcp" + sub
        return None, path
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == "/health":
            return await self.app(scope, receive, send)
        auth = dict(scope.get("headers") or {}).get(b"authorization", b"")
        who = self._identify(auth[7:].strip()) if auth.startswith(b"Bearer ") else None
        if who is None:
            who, new_path = self._from_path(scope)
            if who is not None:
                scope = dict(scope)
                scope["path"] = new_path
                scope["raw_path"] = new_path.encode()
        if who is None and (_live("auth_mode", "link") == "open"):
            # 开放模式：不要 token，身份取 ?peer=，没有就是Leader
            qs = (scope.get("query_string") or b"").decode("utf-8", "ignore")
            who = ""
            for part in qs.split("&"):
                if part.startswith("peer="):
                    who = re.sub(r"[^a-z0-9_\-]", "", part[5:].lower())[:32]
        if who is None:
            body = b'{"error":"unauthorized"}'
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        token_ctx = CURRENT_PEER.set(who)
        try:
            return await self.app(scope, receive, send)
        finally:
            CURRENT_PEER.reset(token_ctx)


def public_hosts() -> list:
    """对外主机名：环境变量 BUS_PUBLIC_HOST（逗号分隔）+ 工作台里登记的 Bridge 地址的主机名。都没有 = 只允许本机访问。"""
    hosts = [h.strip().lower() for h in os.environ.get("BUS_PUBLIC_HOST", "").split(",") if h.strip()]
    try:
        with open(os.path.join(HERE, "console", "bridge.json"), encoding="utf-8") as fh:
            url = str((json.load(fh) or {}).get("url") or "")
        m = re.match(r"^\s*https?://([^/:\s?#]+)", url)
        if m:
            hosts.append(m.group(1).lower())
    except (OSError, ValueError):
        pass
    return sorted(set(h for h in hosts if h not in ("127.0.0.1", "localhost")))


def stateless() -> bool:
    """无状态模式（默认开，2026-10-02 查 Agent 掉线）：有状态模式下服务端发会话号，闲置 30 分钟（SDK 默认
    session_idle_timeout）或 MCP 重启后会话号失效，Agent 下一次调用收到 404「Session not found」，很多宿主就此断开。
    我们的工具每次调用都独立（bus_tool 子进程，Leader 的多会话用工具参数 session 区分），不依赖 MCP 会话；
    无状态下不发会话号，宿主手里旧的会话号也直接被接受——重启、闲置都不再掉线。BUS_MCP_STATEFUL=1 切回旧行为。"""
    return os.environ.get("BUS_MCP_STATEFUL", "") not in ("1", "true", "yes")


def build_app(token: str):
    kw: dict[str, Any] = {}
    if TransportSecuritySettings is not None:  # Named hostname and loopback only; Bearer authentication remains mandatory
        public = public_hosts()
        kw["transport_security"] = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=["127.0.0.1:*", "localhost:*", *public],
                                                             allowed_origins=[*(f"https://{h}" for h in public), "http://127.0.0.1:*", "http://localhost:*"])
    if _SDK == 1:
        mcp.settings.streamable_http_path = "/mcp"
        mcp.settings.stateless_http = stateless()
        for k, v in kw.items():
            setattr(mcp.settings, k, v)
        app = mcp.streamable_http_app()
    else:
        app = mcp.streamable_http_app(streamable_http_path="/mcp", host="0.0.0.0", stateless_http=stateless(), **kw)

    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def health(_):
        return JSONResponse({"ok": True, "service": "bus_mcp", "sdk": _SDK, "version": SERVICE_VERSION, "stateless": stateless(),
                             "pid": os.getpid(), "uptime_sec": int(time.time() - STARTED_AT),
                             "workspace": B.WORKSPACE, "runtime": RX.self_check()})

    async def admin_shutdown(_):
        # 优雅关停（重启用）：仅主 token；停止接收新连接，在途请求最多再跑 timeout_graceful_shutdown 秒，
        # 被取消的 bus_tool 子进程由 runtime_exec 整树终止，不会残留。比 taskkill 直接杀树温和且边界明确。
        if CURRENT_PEER.get() != "":
            return JSONResponse({"error": "只有主 token 可以关停服务"}, status_code=403)
        import threading
        threading.Timer(0.5, _request_exit).start()
        return JSONResponse({"ok": True, "stopping": True})

    app.router.routes.insert(0, Route("/health", health))
    app.router.routes.insert(0, Route("/admin/shutdown", admin_shutdown, methods=["POST"]))
    return BearerAuth(app, token)


_SERVER = None


def _request_exit():
    if _SERVER is not None:
        _SERVER.should_exit = True


def main():
    ap = argparse.ArgumentParser(description="bus 自建 MCP 服务（Streamable HTTP）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--token", default="", help="Deprecated: prefer --token-file; never pass credentials on the command line")
    ap.add_argument("--token-file", default=os.path.join(HERE, "console", "mcp_token.txt"))
    a = ap.parse_args()
    token = a.token
    if not token and os.path.isfile(a.token_file):
        with open(a.token_file, encoding="utf-8-sig") as stream:
            token = stream.read().strip()
    token = token or os.environ.get("BUS_MCP_TOKEN", "")
    if not token or len(token) < 32:
        sys.exit("Refusing startup: configure a private token file or BUS_MCP_TOKEN (at least 32 characters)")
    if not os.path.isfile(BUS_TOOL):
        sys.exit(f"找不到 {BUS_TOOL}")
    import uvicorn
    print(f"[bus_mcp] mcp sdk v{_SDK}  workspace={B.WORKSPACE}")
    print(f"[bus_mcp] listening http://{a.host}:{a.port}/mcp   health: /health   auth: Bearer <token>")
    global _SERVER
    config = uvicorn.Config(build_app(token), host=a.host, port=a.port, log_level="info",
                            timeout_keep_alive=75, timeout_graceful_shutdown=5)
    _SERVER = uvicorn.Server(config)
    _SERVER.run()


if __name__ == "__main__":
    main()
