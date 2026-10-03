"""在 Agent 页直接移除一个 Agent（2026-10-02 用户要求：「还是不能直接把 Agent 删掉或移除」）。

以前只有「清理离线」：只删离线满 12 小时、手上没单的。现在可以点名移除任意一个（Leader 除外）：
  1. 先把它手上还开着的单挪走（普通单换人、审查单重派、施工单按同一基线给新人开 worktree）；
     挪不走的单列出来并停下，除非 force=True（留下的单会由守护 30 分钟规则再处理）；
  2. 登记文件归档到 log/<日期>/removed-peers/<peer>.json 后删除，连同总线心跳记录与「领了不做」记录；
  3. revoke=True 时同时吊销它的接入令牌：不吊销的话，它的会话还开着就会在下一次心跳时重新登记回来。
业务逻辑在这里，bus_web 只做转发。
"""
import datetime as dt
import os

import bus_core as B


def _move_one(tid: str, peer: str, mode: str) -> dict:
    import bus_reassign as RA
    import bus_review as RV
    if mode == "build":
        return RA._reassign_build(tid, peer, by="user")
    if mode == "review":
        r = RV.review_now(tid, by="user")
        if r["dispatched"]:
            return {"to": "、".join(r["dispatched"][0]["reviewers"])}
        raise ValueError((r["errors"] or r["skipped"] or [{"error": "没有在线 Reviewer"}])[0].get("error")
                         or (r["skipped"] or [{}])[0].get("reason", "审查没派出去"))
    meta = B.read_json(os.path.join(B.OUTBOX, f"{tid}.meta.json"), None) or {}
    ex = list(meta.get("peers") or []) + [peer, B.review_author(meta)]
    target = RA.pick_free_target(peer, ex) or B.pick_substitute(peer, exclude=ex, any_group=True)
    if not target:
        raise ValueError("没有其他在线 Agent 可以接")
    return {"to": B.reassign(tid, peer, to_peer=target, by="user")["to"]}


def remove_peer(peer: str, revoke: bool = True, force: bool = False, dry_run: bool = False) -> dict:
    import bus_reassign as RA
    B.valid_name(peer)
    if peer == "controller":
        raise ValueError("Leader（controller）不能在这里移除")
    if (B.lease() or {}).get("holder") == peer:
        raise ValueError(f"{peer} 正在代理 Leader（持有租约），先收回 Leader 再移除")
    held = RA._held_by(peer)
    if dry_run:
        return {"peer": peer, "tasks": [{"task_id": t, "kind": k, "mode": m} for t, k, m in held],
                "online": bool(B.peer_state(peer).get("online"))}
    moved, stuck = [], []
    for tid, kind, mode in held:
        try:
            moved.append({"task_id": tid, "mode": mode, **_move_one(tid, peer, mode)})
        except Exception as exc:  # noqa: BLE001  逐单报原因
            stuck.append({"task_id": tid, "mode": mode, "reason": str(exc)})
    if stuck and not force:
        return {"ok": False, "peer": peer, "moved": moved, "stuck": stuck,
                "note": "这些单挪不走，Agent 没有移除。可以等有人上线再试，或勾选「仍要移除」。"}
    path = os.path.join(B.PEERS, f"{peer}.json")
    with B.LN.file_mutex(B.LN.peer_mutex_path(B.PEERS, peer)):
        data = B.read_json(path, None)
        if data is not None:
            arch = os.path.join(B.LOG, dt.datetime.now().strftime("%Y-%m-%d"), "removed-peers")
            os.makedirs(arch, exist_ok=True)
            B.write_json(os.path.join(arch, f"{peer}.json"), {**data, "removed_at": B.now_iso()})
            os.remove(path)
    for p in (B._beat_path(peer),):
        try:
            os.remove(p)
        except OSError:
            pass
    B.clear_strikes(peer)
    revoked = False
    if revoke:
        try:
            import bus_settings as SET
            if SET.reveal_token(peer):
                SET.save({"peer_tokens": {peer: ""}})
                revoked = True
        except Exception:  # noqa: BLE001  令牌吊销失败不回滚移除，结果里如实说
            revoked = False
    try:
        import bus_events as E
        E.emit("peer.removed", {"peer": peer, "moved": len(moved), "left": len(stuck), "revoked": revoked})
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "peer": peer, "moved": moved, "stuck": stuck, "revoked": revoked}
