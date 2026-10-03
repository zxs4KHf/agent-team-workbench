import os
import re
import secrets
import threading
import time
import urllib.parse
import uuid

import arena_core as C
import arena_store as ST

# 只针对「一键接入 join」的持久作业队列（不动通用 _job/JOBS）：
# - 状态：queued → sending → waiting_peer → connected | unconfirmed | failed | cancelled
# - 幂等键 {group_id, session_id, request_id}；同 group/session 的在途作业去重（防多标签双发）
# - 落盘到 arena/data/join_jobs.json（走 arena_store 的临时文件替换写）；重启后 queued 继续，
#   sending/waiting_peer 一律标 unconfirmed（消息可能已发出，绝不自动重发）
# - job 文件与 API 只保留经 arena_core.sanitize_text 净化的 ≤200 字符摘要 + reason_code，不含接入提示词/token/完整上游响应

KIND = "join_jobs"
ST.FILES.setdefault(KIND, "join_jobs.json")
ST.DEFAULTS.setdefault(KIND, {"jobs": [], "config": {"max_concurrency": 1, "wait_rounds": 24, "wait_step_sec": 5}, "updated_at": ""})

ACTIVE = ("queued", "sending", "waiting_peer")
SENT = ("sending", "waiting_peer")
TERMINAL = ("connected", "unconfirmed", "failed", "cancelled")
LATE_CANCEL_NOTE = "取消请求已晚，消息已发送不可撤回"
CREDENTIAL_CODES = ("no_token",)          # 凭据/授权失效：中止同批次剩余未发送项
PREVIEW_TTL = 900
ONLINE_WINDOW = 180                       # 与 bus_status 一致：心跳 180 s 内且 idle/busy 视为在线
MAX_JOBS_KEPT = 300

DEFAULT_WORKBENCH = "http://127.0.0.1:8765"
# e5de/B1：对外（get_job / jobs_view / enqueue 回执 / confirm 明细）只出这份白名单，idem_key 与任何原始输入不外抛
PUBLIC_FIELDS = ("id", "kind", "status", "group", "session_id", "request_id", "batch", "peer_hint", "workbench",
                 "created_at", "started_at", "updated_at", "finished_at", "progress", "total", "message", "result",
                 "error", "reason_code", "retryable", "payload", "binding", "cancel_requested", "cancel_note", "source")
_PEER_HINT_RX = re.compile(r"^[A-Za-z0-9_.\-]{0,64}$")
_REQUEST_ID_RX = re.compile(r"^[A-Za-z0-9_.:@\-]{1,200}$")
_SOURCE_KEY_RX = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_SECRET_KEY_RX = re.compile(r"(?i)(token|secret|password|passwd|pwd|cookie|authorization|api_?key|key)$")
_WB_BAD = "workbench 非法：只接受 http(s)://主机[:端口]，不接受 path/query/#/用户名密码（疑似带 token 的地址一律拒绝，不回显输入）"

_LOCK = threading.RLock()
_WAKE = threading.Event()
_STOP = threading.Event()
_WORKER = None
_RUNNING = {}        # job id -> Thread（进程内领取记录，避免同一 job 被两个 worker 拿到）
_PREVIEWS = {}       # preview_token -> {"created", "selectable": {gid: set(session_id)}, "groups": [...]}
_RECOVERED = False


class JoinCancelled(Exception):
    pass


# ---------- 输入核验（e5de/B1）----------
def check_workbench(workbench) -> str:
    """工作台连接目标只接受 scheme://host[:port]。
    本项目的 Bridge/MCP 地址就是把 token 写在 path 里：这种输入既不能原样落盘，也不能打码后拿去连
    （打码后的 URL 连不上），更不能悄悄截掉 path 假装能连——所以直接拒绝。报错文案不回显输入。"""
    raw = str(workbench or "").strip() or DEFAULT_WORKBENCH
    if len(raw) > 256 or any(ch.isspace() or ord(ch) < 32 for ch in raw):
        raise ValueError(_WB_BAD)
    try:
        u = urllib.parse.urlsplit(raw)
        port = u.port
    except ValueError:
        raise ValueError(_WB_BAD)
    if u.scheme not in ("http", "https") or not u.hostname or u.username is not None or u.password is not None:
        raise ValueError(_WB_BAD)
    if u.query or u.fragment or u.path not in ("", "/"):
        raise ValueError(_WB_BAD)
    del port
    return f"{u.scheme}://{u.netloc}"


def _workbench_public(value) -> str:
    """已通过 check_workbench 的值本身不含秘密，原样给前端；其余（历史落盘的原始输入）一律走 sanitize_text。"""
    try:
        return check_workbench(value)
    except ValueError:
        return C.sanitize_text(value, 80)


def check_peer_hint(peer) -> str:
    s = str(peer or "").strip()
    if not _PEER_HINT_RX.match(s):
        raise ValueError("peer 名只允许字母/数字/._-，长度 ≤ 64（不回显输入）")
    return s


def check_request_id(request_id) -> str:
    s = str(request_id or "").strip() or uuid.uuid4().hex
    if not _REQUEST_ID_RX.match(s):
        raise ValueError("request_id 只允许字母/数字/._:@-，长度 ≤ 200（不回显输入）")
    return s


def clean_source(source) -> dict:
    """来源标注：键名白名单形状 + 值逐个脱敏 + 最多 8 个键，不靠 dict 整包落盘。"""
    out = {}
    for k, v in (source or {}).items():
        k = str(k)
        if k in C.SECRET_FIELDS or _SECRET_KEY_RX.search(k) or not _SOURCE_KEY_RX.match(k):
            continue
        out[k] = C.sanitize_text(v, 80)
        if len(out) >= 8:
            break
    return out


# ---------- 存储 ----------
def _now() -> str:
    return ST.now_text()


def _load() -> dict:
    st = ST.load(KIND)
    st.setdefault("jobs", [])
    cfg = st.setdefault("config", {})
    for k, v in ST.DEFAULTS[KIND]["config"].items():
        cfg.setdefault(k, v)
    return st


def _save(st: dict) -> None:
    st["updated_at"] = _now()
    if len(st["jobs"]) > MAX_JOBS_KEPT:  # 只裁剪终态，在途作业永远保留
        done = [j for j in st["jobs"] if j["status"] in TERMINAL]
        drop = {j["id"] for j in done[: len(st["jobs"]) - MAX_JOBS_KEPT]}
        st["jobs"] = [j for j in st["jobs"] if j["id"] not in drop]
    ST.save(KIND, st)


def _update(jid: str, **fields):
    with _LOCK:
        st = _load()
        job = next((j for j in st["jobs"] if j["id"] == jid), None)
        if not job:
            return None
        payload = fields.pop("payload", None)
        if payload:
            job.setdefault("payload", {}).update(payload)
        job.update(fields)
        job["updated_at"] = _now()
        _save(st)
        return job


def get_job(jid: str):
    with _LOCK:
        j = next((j for j in _load()["jobs"] if j["id"] == jid), None)
        return _public(j) if j else None


def _public(j: dict) -> dict:
    # 对外形状与旧 _job 对齐（id/kind/status/started_at/progress/total/message/result/error/reason_code/retryable/payload）
    # e5de/B1：字段白名单，不再 dict(j) 全量外抛；workbench / peer_hint / source 再过一遍脱敏（双保险）
    out = {k: j.get(k) for k in PUBLIC_FIELDS if k in j}
    out.setdefault("kind", "join")
    out["started_at"] = j.get("started_at") or j.get("created_at") or ""
    out["workbench"] = _workbench_public(j.get("workbench") or "")
    out["peer_hint"] = C.sanitize_text(j.get("peer_hint") or "", 64)
    out["source"] = clean_source(j.get("source") or {})
    out["queue"] = "join"
    return out


# ---------- 配置 ----------
def get_config() -> dict:
    with _LOCK:
        return dict(_load()["config"])


def set_config(max_concurrency=None, wait_rounds=None, wait_step_sec=None) -> dict:
    with _LOCK:
        st = _load()
        cfg = st["config"]
        if max_concurrency is not None:
            cfg["max_concurrency"] = max(1, min(int(max_concurrency), 3))
        if wait_rounds is not None:
            cfg["wait_rounds"] = max(1, min(int(wait_rounds), 240))
        if wait_step_sec is not None:
            cfg["wait_step_sec"] = max(0, min(float(wait_step_sec), 30))
        _save(st)
        out = dict(cfg)
    _WAKE.set()
    return out


# ---------- 入队 / 查询 / 取消 ----------
def enqueue(group: str, session_id: str, request_id: str = "", peer: str = "", workbench: str = "", source: dict = None, batch: str = "") -> dict:
    group, session_id = str(group or "").strip(), str(session_id or "").strip()
    if not group or not session_id:
        raise ValueError("缺少 group 或 session_id")
    # e5de/B1：先核验再入队——非法输入在任何落盘/回显之前就被拒绝，错误文案不含输入本身
    request_id = check_request_id(request_id)
    peer = check_peer_hint(peer)
    workbench = check_workbench(workbench)
    key = f"{group}|{session_id}|{request_id}"
    with _LOCK:
        _recover_once()
        st = _load()
        same = next((j for j in st["jobs"] if j.get("idem_key") == key), None)
        if same:  # 同 request_id 重复提交：原 job 原样返回，不新发消息
            return {**_public(same), "dedup": "same_request"}
        active = next((j for j in st["jobs"] if j["group"] == group and j["session_id"] == session_id and j["status"] in ACTIVE), None)
        if active:  # 同 group/session 已有在途作业（多标签/连点）：复用，不再排第二条
            return {**_public(active), "dedup": "in_flight"}
        job = {
            "id": uuid.uuid4().hex[:10], "kind": "join", "status": "queued", "idem_key": key,
            "group": group, "session_id": session_id, "request_id": request_id, "batch": batch or request_id,
            "peer_hint": peer, "workbench": workbench,   # 均已核验：workbench 只含 scheme://host[:port]
            "created_at": _now(), "started_at": "", "updated_at": _now(), "finished_at": "",
            "progress": 0, "total": 3, "message": "排队中", "result": None, "error": "", "reason_code": "", "retryable": False,
            "payload": {"group": group, "session_id": session_id, "request_id": request_id},
            "binding": None, "cancel_requested": False, "cancel_note": "",
            "source": clean_source(source),
        }
        st["jobs"].append(job)
        _save(st)
    _ensure_worker()
    return {**_public(job), "dedup": ""}


def jobs_view(limit: int = 100) -> dict:
    _ensure_worker()  # 进程重启后第一次读取即恢复：sending/waiting_peer → unconfirmed，queued 由 worker 继续
    with _LOCK:
        st = _load()
        items = sorted(st["jobs"], key=lambda j: j.get("created_at") or "", reverse=True)[: max(1, int(limit))]
        return {"jobs": [_public(j) for j in items], "config": dict(st["config"])}


def cancel(jid: str) -> dict:
    with _LOCK:
        st = _load()
        job = next((j for j in st["jobs"] if j["id"] == jid), None)
        if not job:
            raise ValueError("作业不存在")
        if job["status"] == "queued":
            job.update({"status": "cancelled", "cancel_requested": True, "cancel_note": "已在投递前取消", "message": "已取消（未发送）",
                        "reason_code": "cancelled", "finished_at": _now(), "updated_at": _now()})
            _save(st)
            return {"ok": True, "cancelled": True, "status": "cancelled", "job": _public(job)}
        if job["status"] in SENT:
            job.update({"cancel_requested": True, "cancel_note": LATE_CANCEL_NOTE, "updated_at": _now()})
            _save(st)
            return {"ok": False, "cancelled": False, "status": job["status"], "note": LATE_CANCEL_NOTE, "job": _public(job)}
        raise ValueError(f"作业已是 {job['status']}，不可取消")


def _is_cancelled(jid: str) -> bool:
    with _LOCK:
        j = next((j for j in _load()["jobs"] if j["id"] == jid), None)
        return bool(j and j["status"] == "cancelled")


def _abort_batch_siblings(batch: str, keep_id: str, why: str) -> list:
    # 同批次里还没发出去的（queued）一律取消；已发送/已连接的保持原状态
    out = []
    with _LOCK:
        st = _load()
        for j in st["jobs"]:
            if j.get("batch") == batch and j["id"] != keep_id and j["status"] == "queued":
                j.update({"status": "cancelled", "cancel_requested": True, "cancel_note": why, "message": why, "reason_code": "aborted_batch",
                          "finished_at": _now(), "updated_at": _now()})
                out.append(j["id"])
        if out:
            _save(st)
    return out


# ---------- 重启恢复 / worker ----------
def _recover_once() -> None:
    global _RECOVERED
    if _RECOVERED:
        return
    _RECOVERED = True
    st = _load()
    changed = False
    for j in st["jobs"]:
        wb = j.get("workbench") or ""
        if wb and _workbench_public(wb) != wb:   # e5de/B1：修复前落盘的原始输入，首次加载即脱敏改写
            j["workbench"] = _workbench_public(wb)
            changed = True
        if j["status"] in SENT and j["id"] not in _RUNNING:
            j.update({"status": "unconfirmed", "reason_code": "restarted", "finished_at": _now(), "updated_at": _now(),
                      "message": "进程重启：接入消息可能已发出，不自动重发；请看会话或 peer 心跳确认"})
            changed = True
    if changed:
        _save(st)


def _ensure_worker() -> None:
    global _WORKER
    with _LOCK:
        _recover_once()
        if _WORKER and _WORKER.is_alive():
            _WAKE.set()
            return
        _STOP.clear()
        _WORKER = threading.Thread(target=_loop, name="arena-join-queue", daemon=True)
        _WORKER.start()


def _loop() -> None:
    while not _STOP.is_set():
        try:
            _tick()
        except Exception:  # noqa: BLE001  worker 循环本身不能因单个作业异常退出
            pass
        _WAKE.wait(0.5)
        _WAKE.clear()


def _tick() -> None:
    with _LOCK:
        st = _load()
        alive = {k: t for k, t in _RUNNING.items() if t.is_alive()}
        _RUNNING.clear()
        _RUNNING.update(alive)
        slots = int(st["config"].get("max_concurrency") or 1) - len(_RUNNING)
        if slots <= 0:
            return
        queued = sorted((j for j in st["jobs"] if j["status"] == "queued" and j["id"] not in _RUNNING), key=lambda j: j.get("created_at") or "")
        picked = queued[:slots]
        for j in picked:
            j["started_at"] = j.get("started_at") or _now()
            j["message"] = "开始预检（会话/账号/取名）"
        if picked:
            _save(st)
        for j in picked:
            t = threading.Thread(target=_run, args=(j["id"],), name=f"join-{j['id']}", daemon=True)
            _RUNNING[j["id"]] = t
            t.start()


def _run(jid: str) -> None:
    job = get_job(jid)
    if not job or job["status"] != "queued":
        _RUNNING.pop(jid, None)
        return
    cfg = get_config()
    shadow = {"payload": {}, "message": "", "progress": 0, "total": 3, "reason_code": ""}

    def set_state(status, message):
        if status == "sending" and _is_cancelled(jid):  # 投递前最后一次取消检查；之后发出的消息不可撤回
            raise JoinCancelled()
        persisted = status if status in ("sending", "waiting_peer") else None
        fields = {"message": message, "progress": shadow.get("progress", 0), "payload": _clean_payload(shadow.get("payload") or {})}
        if persisted:
            fields["status"] = persisted
        if shadow.get("reason_code"):
            fields["reason_code"] = shadow["reason_code"]
        _update(jid, **fields)

    try:
        res = C.join_once(job["group"], job["session_id"], job.get("peer_hint") or "", job.get("workbench") or "", shadow,
                          set_state=set_state, wait_rounds=cfg.get("wait_rounds"), wait_step=cfg.get("wait_step_sec"))
        _finish(jid, job, shadow, res)
    except JoinCancelled:
        _update(jid, message="已在投递前取消", payload=_clean_payload(shadow.get("payload") or {}))
    except C.JoinError as e:
        _update(jid, status="failed", error=C.sanitize_text(str(e), 200), reason_code=e.reason_code, retryable=bool(e.retryable),
                finished_at=_now(), message="接入失败", progress=shadow.get("progress", 0), payload=_clean_payload(shadow.get("payload") or {}))
        if e.reason_code in CREDENTIAL_CODES:
            _abort_batch_siblings(job.get("batch") or "", jid, "同批次凭据失效，剩余未发送项已中止")
    except Exception as e:  # noqa: BLE001
        _update(jid, status="failed", error=C.sanitize_text(str(e), 200), reason_code=shadow.get("reason_code") or C._classify_error(str(e)),
                finished_at=_now(), message="接入失败", progress=shadow.get("progress", 0), payload=_clean_payload(shadow.get("payload") or {}))
    finally:
        _RUNNING.pop(jid, None)
        _WAKE.set()


def _clean_payload(p: dict) -> dict:
    out = {}
    for k in ("peer_name", "name_source", "sent_at"):
        if p.get(k):
            out[k] = C.sanitize_text(p[k], 80)
    for k in ("first_text", "relay_error"):
        if p.get(k):
            out[k] = C.sanitize_text(p[k], 200)
    return out


def _finish(jid: str, job: dict, shadow: dict, res: dict) -> None:
    res = res or {}
    registered = res.get("registered") is True
    result = {
        "peer": C.sanitize_text(res.get("peer"), 80), "name_source": res.get("name_source") or "", "registered": registered,
        "heartbeat_model": C.sanitize_text(res.get("heartbeat_model"), 80), "expected_canon": C.sanitize_text(res.get("expected_canon"), 80),
        "first_text": C.sanitize_text(res.get("first_text"), 200), "relay_error": C.sanitize_text(res.get("relay_error"), 200),
    }
    if res.get("hint"):
        result["hint"] = C.sanitize_text(res["hint"], 200)
    fields = {"result": result, "finished_at": _now(), "payload": _clean_payload(shadow.get("payload") or {})}
    if registered:
        binding = {"group": job["group"], "session_id": job["session_id"], "request_id": job["request_id"], "peer": result["peer"],
                   "sent_at": res.get("sent_at") or (shadow.get("payload") or {}).get("sent_at") or "", "heartbeat_model": result["heartbeat_model"]}
        fields.update({"status": "connected", "progress": 3, "message": f"已接入：{result['peer']}", "binding": binding,
                       "reason_code": shadow.get("reason_code") or "ok"})
        _bind_session(job["session_id"], binding, jid)
    else:
        fields.update({"status": "unconfirmed", "progress": max(2, int(shadow.get("progress") or 0)),
                       "message": "消息已发送，但没等到本次接入之后的有效心跳（未确认，不自动重发）",
                       "reason_code": shadow.get("reason_code") or "no_heartbeat"})
    _update(jid, **fields)


def _bind_session(session_id: str, binding: dict, jid: str) -> None:
    # 来源绑定只写元数据（job/request/peer/sent_at），不写任何凭据
    def apply(st):
        for s in st.get("sessions", []):
            if s.get("id") == session_id:
                s["join_meta"] = {"job_id": jid, "request_id": binding["request_id"], "peer": binding["peer"], "sent_at": binding["sent_at"], "group": binding["group"]}
    try:
        ST.update("sessions", apply)
    except Exception:  # noqa: BLE001  绑定元数据写失败不影响 connected 判定
        pass


# ---------- 在线 Agent / 预览 / 确认 ----------
def online_peers(now: float = None) -> dict:
    now = now or time.time()
    out = {}
    peers_dir = os.path.join(C._wb_root(), "peers")
    if not os.path.isdir(peers_dir):
        return out
    for fn in os.listdir(peers_dir):
        if not fn.endswith(".json"):
            continue
        p = os.path.join(peers_dir, fn)
        try:
            age = now - os.path.getmtime(p)
            if age > ONLINE_WINDOW:
                continue
            import json
            with open(p, encoding="utf-8") as f:
                d = json.load(f) or {}
        except (OSError, ValueError):
            continue
        if d.get("status") in ("idle", "busy"):
            out[fn[:-5]] = {"model": C.canonical_model(str(d.get("model") or "")), "status": d.get("status"), "age": int(age)}
    return out


def preview(group_ids: list, extra: dict = None, workbench: str = "") -> dict:
    # 只读预览：不发消息、不写任何 store；只在内存登记 preview_token 供 confirm 校验
    workbench = check_workbench(workbench)   # e5de/B1：进内存 _PREVIEWS 之前就核验
    _recover_once()
    extra = {str(k): [str(x) for x in (v or [])] for k, v in (extra or {}).items()}
    gstore = C._groups_store()
    sessions = ST.load("sessions")["sessions"]
    online = online_peers()
    jobs = _load()["jobs"]
    items, selectable = [], {}
    for gid in [str(x) for x in (group_ids or []) if str(x).strip()]:
        g = next((x for x in gstore["groups"] if x["id"] == gid), None)
        if not g:
            items.append({"group": gid, "name": gid, "error": "分组不存在", "skip_reason": "group_missing", "candidates": [], "will_send": 0})
            continue
        members = C._group_members(g, sessions)
        inflight = [j for j in jobs if j["group"] == gid and j["status"] in ACTIVE]
        inflight_sessions = {j["session_id"] for j in inflight}
        online_agents = [{"peer": m["peer"], "session_id": m["id"], "model": online[m["peer"]]["model"]} for m in members if m.get("peer") and m["peer"] in online]
        group_skip = "already_online" if online_agents else ("in_flight" if inflight else "")
        cands, picked_default, ok_ids = [], False, set()
        for m in members:
            c = {"session_id": m["id"], "title": m.get("title") or "", "canon": m.get("canon") or "", "source": m.get("source") or "", "account": m.get("account") or "",
                 "account_health": m.get("account_health"), "peer": m.get("peer") or "", "selected": False, "eligible": False, "skip_reason": ""}
            if m["id"] in inflight_sessions:
                c["skip_reason"] = "in_flight"
            elif m.get("peer") and m["peer"] in online:
                c["skip_reason"] = "already_online"
            elif m.get("account_health") != "ready":
                c["skip_reason"] = "account_" + str(m.get("account_health") or "unknown")
            else:
                c["eligible"] = True
                ok_ids.add(m["id"])
                if not group_skip and not picked_default:  # 默认每组只选一个 ready 会话
                    c["selected"], picked_default = True, True
                elif group_skip:
                    c["skip_reason"] = group_skip
            if m["id"] in extra.get(gid, []) and c["eligible"]:  # 用户明确加选（允许同组第二个）
                c["selected"], c["skip_reason"], c["explicit"] = True, "", True
            cands.append(c)
        n = sum(1 for c in cands if c["selected"])
        selectable[gid] = ok_ids
        items.append({"group": gid, "name": g.get("name") or gid, "source": "custom" if g.get("custom") else "preset", "common": bool(g.get("common")),
                      "tier": g.get("tier") or "", "online_agents": online_agents, "in_flight": [{"job": j["id"], "session_id": j["session_id"], "status": j["status"]} for j in inflight],
                      "ready_count": len(ok_ids), "candidates": cands, "skip_reason": group_skip if not n else "", "will_send": n,
                      "estimated_cost": {"messages": n, "note": "每个选中会话经中继发送 1 条接入提示词，各消耗该账号 1 次对话；未选中的不发"}})
    token = secrets.token_urlsafe(18)
    with _LOCK:
        now = time.time()
        for k in [k for k, v in _PREVIEWS.items() if now - v["created"] > PREVIEW_TTL]:
            _PREVIEWS.pop(k, None)
        _PREVIEWS[token] = {"created": now, "selectable": selectable, "groups": [i["group"] for i in items], "workbench": workbench}
    return {"preview_token": token, "expires_in": PREVIEW_TTL, "groups": items, "will_send_total": sum(i.get("will_send", 0) for i in items),
            "side_effects": "none", "note": "预览不发送任何消息；确认后才入队，按队列并发（默认 1）逐个发送"}


def confirm(preview_token: str, request_id: str, selections, workbench: str = "", source: dict = None) -> dict:
    request_id = str(request_id or "").strip()
    if not request_id:
        raise ValueError("缺少 request_id")
    request_id = check_request_id(request_id)
    workbench = check_workbench(workbench) if str(workbench or "").strip() else ""   # 空则沿用预览时核验过的值
    with _LOCK:
        pv = _PREVIEWS.get(str(preview_token or ""))
        if not pv or time.time() - pv["created"] > PREVIEW_TTL:
            raise ValueError("预览已过期或不存在，请重新预览后再确认")
    pairs = []
    if isinstance(selections, dict):
        for gid, sids in selections.items():
            for sid in (sids or []):
                pairs.append((str(gid), str(sid)))
    else:
        for it in (selections or []):
            if isinstance(it, dict) and it.get("group") and it.get("session_id"):
                pairs.append((str(it["group"]), str(it["session_id"])))
    if not pairs:
        raise ValueError("没有明确选择任何会话")
    gstore = C._groups_store()
    sessions = {s["id"]: s for s in ST.load("sessions")["sessions"]}
    accounts = {a.get("email"): a for a in ST.load("accounts")["accounts"]}
    online = online_peers()
    items, aborted, seen = [], "", set()
    for gid, sid in pairs:
        row = {"group": gid, "session_id": sid, "job": "", "status": "", "skip": "", "error": ""}
        items.append(row)
        if (gid, sid) in seen:
            row["skip"] = "duplicate_selection"
            continue
        seen.add((gid, sid))
        if aborted:
            row["skip"], row["error"] = "aborted", aborted
            continue
        g = next((x for x in gstore["groups"] if x["id"] == gid), None)
        if not g:
            row["error"] = "分组不存在"
            continue
        if sid not in pv["selectable"].get(gid, set()):
            row["error"] = "该会话不在预览候选里（不可用或已在途），请重新预览"
            continue
        sess = sessions.get(sid)
        if not sess:
            row["error"] = "会话不存在"
            continue
        acct = accounts.get(sess.get("account"))
        health = C.judge_health(acct)["health"] if acct else "unknown"
        if health in ("banned", "unauthenticated"):
            row["error"], row["status"] = f"账号凭据失效（{health}）", "failed"
            row["reason_code"] = "no_token"
            aborted = "前一项账号凭据失效，剩余未发送项已中止"
            continue
        if health in ("exhausted", "cooldown"):
            row["error"], row["status"], row["reason_code"] = f"账号额度或冷却未恢复（{health}）", "failed", "spend_limit"
            continue
        if sess.get("peer") and sess["peer"] in online:
            row["skip"] = "already_online"
            continue
        job = enqueue(gid, sid, request_id=f"{request_id}:{gid}:{sid}", workbench=workbench or pv.get("workbench") or "", batch=request_id,
                      source={"origin": "common_join", "preview": str(preview_token)[:8], **(source or {})})
        row.update({"job": job["id"], "status": job["status"], "skip": "in_flight" if job.get("dedup") == "in_flight" else "", "dedup": job.get("dedup") or ""})
    return {"request_id": request_id, "items": items, "queued": sum(1 for r in items if r["job"] and not r["dedup"]),
            "aborted": bool(aborted), "abort_reason": aborted}


# ---------- 测试钩子 ----------
def _reset_for_tests(wait_running: bool = True) -> None:
    # 模拟进程重启：停 worker、清进程内领取记录与预览缓存；存储文件保持不动
    # wait_running=False 模拟「进程被杀」：正在执行的作业线程不等待、不认领（由 _recover_once 标 unconfirmed）
    global _WORKER, _RECOVERED
    _STOP.set()
    _WAKE.set()
    w = _WORKER
    if w and w.is_alive():
        w.join(timeout=5)
    if wait_running:
        for t in list(_RUNNING.values()):
            t.join(timeout=5)
    _RUNNING.clear()
    _PREVIEWS.clear()
    _WORKER = None
    _RECOVERED = False
    _STOP.clear()
    _WAKE.clear()
