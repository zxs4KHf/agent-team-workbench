"""把唤醒语直接发进 Agent 的会话（2026-10-02，工作台「唤醒」按钮的自动通道）。

只对经 Arena 中继管理、且接入时已绑定到这个 Agent 名字的会话有效（sessions[].peer，接入成功时由 _remember_join 写入）；
其它宿主工作台够不着，返回 bound=False，由前端改为复制唤醒语。
发送走后台线程：中继的一次对话可能阻塞很久（模型进入 bus_wait_task 长轮询后不再吐文字），接口立刻返回。
是否真的醒了不看这里，看工作台的心跳（前端发完会提示「等心跳」）。同一个 Agent 在途时不重复发。"""
import threading
import time

import arena_core as C
import arena_store as ST
import arena_upstream as UP

_LOCK = threading.Lock()
_STATE = {}        # peer -> {"status": sending|sent|failed, "session_id", "at", "error"}
RESEND_GAP = 120   # 秒：同一个 Agent 两次发送的最小间隔，防连点和多标签重复发
FAIL_FAST_SEC = 30  # 秒：这之内报错才算没发出去


def bound_session(peer: str):
    """这个 Agent 最近一次接入绑定的会话；没有返回 None。"""
    hits = [s for s in ST.load("sessions").get("sessions", []) if s.get("peer") == peer]
    return hits[-1] if hits else None


def bound_peers() -> list:
    return sorted({s["peer"] for s in ST.load("sessions").get("sessions", []) if s.get("peer")})


def status(peer: str = "") -> dict:
    with _LOCK:
        return dict(_STATE.get(peer) or {}) if peer else {k: dict(v) for k, v in _STATE.items()}


def wake(peer: str, text: str, send=None) -> dict:
    peer, text = str(peer or "").strip(), str(text or "").strip()
    if not peer or not text:
        raise ValueError("缺少 peer 或唤醒语")
    sess = bound_session(peer)
    if not sess:
        return {"peer": peer, "bound": False}
    with _LOCK:
        cur = _STATE.get(peer) or {}
        if cur.get("status") == "sending" or (cur.get("status") == "sent" and time.time() - cur.get("ts", 0) < RESEND_GAP):
            return {"peer": peer, "bound": True, "session_id": sess["id"], "status": cur["status"], "duplicate": True}
        _STATE[peer] = {"status": "sending", "session_id": sess["id"], "at": ST.now_text(), "ts": time.time(), "error": ""}
    threading.Thread(target=_send, args=(peer, sess["id"], text, send), daemon=True, name=f"wake-{peer}").start()
    return {"peer": peer, "bound": True, "session_id": sess["id"], "status": "sending"}


def _send(peer: str, session_id: str, text: str, send=None) -> None:
    # 中继这次对话往往要等很久才返回（模型醒来后进入 bus_wait_task 长轮询，不再吐文字），甚至超时；
    # 所以只有很快就报错（连不上中继、会话不存在、额度用完）才算发送失败，慢的超时按「已发出」处理，绝不重发
    t0 = time.time()
    try:
        if send:
            send(session_id, text)
        else:
            alias = f"wake@{session_id[:8]}"
            C.ensure_upstream_route(alias, session_id)
            UP.chat_probe(alias, text, timeout=900)
        result = {"status": "sent", "error": ""}
    except Exception as e:  # noqa: BLE001
        quick = time.time() - t0 < FAIL_FAST_SEC
        result = {"status": "failed" if quick else "sent", "error": C.sanitize_text(str(e), 200)}
    with _LOCK:
        _STATE.setdefault(peer, {}).update(result, ts=time.time())
