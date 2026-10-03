"""Leader 显示名与交接事件：跨进程互斥的读-改-写，独立于 controller 租约。

与 bus_implementation_lock 的关系：同样是 OS 级字节锁，但锁文件不同、不嵌套、
不涉及任何 ownership/lease 变更。本模块不读也不写 console/controller.lock。
"""
import contextlib
import datetime as dt
import json
import os
import re
import threading
import time
import uuid

LABEL_MAX = 24
# 显示名是纯数据：禁止能闭合提示词引用边界或伪装标记/工具参数的字符（中文/英文引号、反引号、尖括号、花括号、反斜杠）
LABEL_FORBIDDEN = set("「」『』“”‘’\"'`<>{}\\")
EVENTS_CAP = 50
UNSTABLE_WINDOW_SEC = 600
UNSTABLE_COUNT = 3
REASONS = ("takeover", "handback", "user_handback", "lease_lost", "guard_forced")

# 落盘前必须命中 0 次的凭据形状。构造无凭据模板是第一道防线，这里是第二道。
SECRET_RE = re.compile(
    r"(?:Bearer\s+\S+)"                      # Authorization: Bearer xxx
    r"|(?:/mcp/[A-Za-z0-9_\-]{16,})"         # 带 token 的 MCP 直连 URL
    r"|(?:\b[A-Fa-f0-9]{32,}\b)"             # 裸 hex token
    r"|(?:sk-[A-Za-z0-9_\-]{16,})",
    re.I,
)
_REDACTED = "<redacted>"

from bus_locks import file_mutex, atomic_write_json


def _now():
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()   # ADR 0010：带偏移


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write(path, data):
    atomic_write_json(path, data)


# ---------------- 显示名（纯显示层，协议字段一律不动） ----------------

def peer_mutex_path(peers_dir, peer):
    # 每个 peer 一把锁：改名与心跳互斥，但不同 peer 之间不互相阻塞
    return os.path.join(peers_dir, f".{peer}.mutex")


def validate_label(label):
    """返回规范化后的 label。空串表示清除。禁控制字符，限长 LABEL_MAX。"""
    s = "" if label is None else str(label)
    s = s.strip()
    if not s:
        return ""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in s):
        raise ValueError("显示名不能包含控制字符或换行")
    bad = [ch for ch in s if ch in LABEL_FORBIDDEN]
    if bad:
        raise ValueError(f"显示名不能包含 {' '.join(sorted(set(bad)))} 这类引用/标记字符")
    if len(s) > LABEL_MAX:
        raise ValueError(f"显示名最长 {LABEL_MAX} 个字符，当前 {len(s)}")
    return s


class PeerMissing(LookupError):
    pass


def set_label(peers_dir, peer, label, create=False):
    """写 peers/<peer>.json 的 label；空串清除。绝不改写 peer 字段。
    peer 文件不存在时默认抛 PeerMissing（API 层转 404），不造幽灵登记；create=True 只给内部/测试用。"""
    label = validate_label(label)
    path = os.path.join(peers_dir, f"{peer}.json")
    with file_mutex(peer_mutex_path(peers_dir, peer)):
        data = _read(path, None)
        if data is None:
            if not create:
                raise PeerMissing(f"peer {peer} 不存在（未接入或已删除），改名未保存；首次连接前请用 pending 显示名")
            data = {"peer": peer}
        real = data.get("peer") or peer
        if label:
            data["label"] = label
            data["label_updated"] = _now()
        else:
            data.pop("label", None)
            data.pop("label_updated", None)
        data["peer"] = real          # 显式回写真名，任何路径都不得把 label 灌进 peer
        _write(path, data)
        return {"peer": real, "label": label}


def get_label(peers_dir, peer):
    return (_read(os.path.join(peers_dir, f"{peer}.json"), None) or {}).get("label") or ""


def display_name(peers_dir, peer):
    return get_label(peers_dir, peer) or peer


# ---------------- 首次连接前的显示名：明确的 pending 配置，不造心跳/在线记录 ----------------
def pending_path(root):
    return os.path.join(root, "console", "pending_labels.json")


def set_pending_label(root, peer, label):
    label = validate_label(label)
    with file_mutex(events_mutex_path(root)):
        cfg = _read(pending_path(root), None)
        cfg = cfg if isinstance(cfg, dict) else {}
        if label:
            cfg[peer] = {"label": label, "at": _now()}
        else:
            cfg.pop(peer, None)
        _write(pending_path(root), cfg)
    return {"peer": peer, "label": label, "pending": True}


def pending_label(root, peer):
    cfg = _read(pending_path(root), None)
    return ((cfg or {}).get(peer) or {}).get("label") or "" if isinstance(cfg, dict) else ""


def apply_pending_label(root, peers_dir, peer):
    """peer 首次落盘心跳时调用：把 pending 显示名搬进 peers/<peer>.json 并清掉 pending。失败静默（不影响心跳）。"""
    try:
        lb = pending_label(root, peer)
        if not lb:
            return ""
        set_label(peers_dir, peer, lb)
        set_pending_label(root, peer, "")
        return lb
    except Exception:
        return ""


# ---------------- 交接事件 ----------------

def events_path(root):
    return os.path.join(root, "console", "lead_events.json")


def events_mutex_path(root):
    return os.path.join(root, "console", "lead_events.mutex")


def scrub(text):
    """落盘前抹掉凭据形状。返回 (清洗后文本, 命中次数)。"""
    if not text:
        return "", 0
    out, n = SECRET_RE.subn(_REDACTED, str(text))
    return out, n


def has_secret(text):
    return bool(SECRET_RE.search(str(text or "")))


def _load(root):
    data = _read(events_path(root), None)
    if not isinstance(data, dict):
        data = {}
    evs = data.get("events")
    return [e for e in evs if isinstance(e, dict)] if isinstance(evs, list) else []


def record_lead_event(root, frm, to, epoch, reason, prompt="", now=None):
    """记录一次主控交接。同 (from,to,reason,epoch) 只 count++ 并刷新 at。

    prompt 应由调用方以「无凭据模板」构造（token 传空）；本函数再 scrub 一次兜底。
    """
    if reason not in REASONS:
        raise ValueError(f"未知 reason：{reason!r}")
    try:
        epoch = int(epoch or 0)
    except (TypeError, ValueError):
        epoch = 0
    frm, to = str(frm or ""), str(to or "")
    text, hits = scrub(prompt)
    at = now or _now()
    with file_mutex(events_mutex_path(root)):
        evs = _load(root)
        key = (frm, to, reason, epoch)
        for e in evs:
            same_key = (e.get("from"), e.get("to"), e.get("reason"), int(e.get("epoch") or 0)) == key
            # lease_lost（真 Leader 回归、等代理交回）与同 epoch 的 takeover 是同一次权威变化：只刷新时间，不另开一条
            folded = reason == "lease_lost" and e.get("reason") == "takeover" and int(e.get("epoch") or 0) == epoch and epoch
            if same_key or folded:
                # 幂等：同一真实事件被 wait/console_wait 轮询重复上报，只刷新 at 与 seen，count 永远表示真实次数
                e["seen"] = int(e.get("seen") or 1) + 1
                e["at"] = at
                if folded:
                    e["pending_handback"] = True
                if text and not e.get("prompt"):
                    e["prompt"] = text
                _write(events_path(root), {"events": _prune(evs)})
                return e
        ev = {"id": dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4],
              "at": at, "first_at": at, "from": frm, "to": to, "reason": reason,
              "epoch": epoch, "prompt": text, "count": 1, "seen": 1, "acked": False, "acked_at": "",
              "redacted": hits}
        evs.append(ev)
        _write(events_path(root), {"events": _prune(evs)})
        return ev


def _prune(evs):
    """保留上限 EVENTS_CAP，但未 ack 的事件永不因上限被静默丢弃。"""
    if len(evs) <= EVENTS_CAP:
        return evs
    open_evs = [e for e in evs if not e.get("acked")]
    done = [e for e in evs if e.get("acked")]
    room = max(0, EVENTS_CAP - len(open_evs))
    keep_done = sorted(done, key=lambda e: str(e.get("at") or ""))[-room:] if room else []
    keep = set(id(e) for e in open_evs) | set(id(e) for e in keep_done)
    return [e for e in evs if id(e) in keep]


def lead_events_open(root):
    """未 ack 的事件，最新在前；带 unstable 标记供 banner 切文案。"""
    evs = [e for e in _load(root) if not e.get("acked")]
    import bus_core as B                                   # ADR 0010：排序按解析后的时间，不按字符串
    evs.sort(key=lambda e: (B.parse_ts(e.get("at")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)), reverse=True)
    if not evs:
        return []
    # ADR 0010：事件时间戳带偏移后字符串比较不再成立，改传 epoch 秒
    cutoff = dt.datetime.now().astimezone().timestamp() - UNSTABLE_WINDOW_SEC
    recent = recent_changes(root, cutoff)
    unstable = recent > UNSTABLE_COUNT
    for e in evs:
        e["unstable"] = unstable
        e["recent_total"] = recent
    return evs


def recent_changes(root, cutoff):
    """窗口内真实的权威变化次数：按 (epoch, reason) 去重、不数 lease_lost（那是等待态）、含已 ack 的。轮询重复上报不会抬高它。"""
    import bus_core as B
    cut = cutoff if isinstance(cutoff, (int, float)) else (B.parse_ts(cutoff).timestamp() if B.parse_ts(cutoff) else 0.0)
    seen = set()
    for e in _load(root):
        at = B.parse_ts(e.get("first_at") or e.get("at"))
        if at is None or at.timestamp() < cut or e.get("reason") == "lease_lost":
            continue
        seen.add((int(e.get("epoch") or 0), e.get("reason")))
    return len(seen)


def ack_lead_event(root, event_id="", all_events=False):
    """幂等：已 ack 的再 ack 不报错、不改 acked_at。返回本次真正变更的条数。"""
    at = _now()
    with file_mutex(events_mutex_path(root)):
        evs = _load(root)
        changed = 0
        for e in evs:
            if e.get("acked"):
                continue
            if all_events or e.get("id") == event_id:
                e["acked"], e["acked_at"] = True, at
                changed += 1
        if changed:
            _write(events_path(root), {"events": _prune(evs)})
        return changed


# ---------------- 功能开关（回滚保证） ----------------

FEATURE_DEFAULTS = {"lead_label": True, "lead_events": True, "module_assign": False}


def features(root):
    cfg = _read(os.path.join(root, "console", "features.json"), None)
    out = dict(FEATURE_DEFAULTS)
    if isinstance(cfg, dict):
        for k, v in cfg.items():
            if k in out:
                out[k] = bool(v)
    return out
