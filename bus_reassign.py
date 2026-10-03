"""一键改派「被 Agent 占着不动」的任务（2026-10-01 用户要求）。

占着 = 收件箱里有这单（待领 .json 或已领未回 .taken）、任务还开着、它还没回复，并且它不会再动了：
  · Agent 离线；或
  · Agent 在线但空闲、心跳报告的 task_id 不是这单（会话断了留下的领取；它下次 wait 也会自己释放，这里是不等）。
怎么改派：
  · 普通任务（讨论 / 独立 / 接力 / redteam）→ bus_core.reassign：同组在线优先，没有就跨组；
  · 审查单 → bus_review.review_now：换在线 Reviewer 重派、旧单归档；
  · 施工单 → 换给在线 Agent，并按本单同一基线给它开新 worktree（bus_build.add_teammate）；原施工者的 worktree 保留。
业务逻辑在这里，bus_web 只做转发。
"""
import os
import time

import bus_core as B


def _held_by(peer: str) -> list:
    """peer 收件箱里还开着、它还没回的单 [(task_id, kind)]，kind = taken / queued。"""
    d = os.path.join(B.INBOX, peer)
    try:
        names = os.listdir(d)
    except OSError:
        return []
    out = []
    for f in sorted(names):
        if f.endswith(".taken"):
            tid, kind = f[:-6], "taken"
        elif f.endswith(".json"):
            tid, kind = f[:-5], "queued"
        else:
            continue
        meta = B.read_json(os.path.join(B.OUTBOX, f"{tid}.meta.json"), None)
        if not isinstance(meta, dict) or peer not in (meta.get("peers") or []):
            continue
        if os.path.exists(os.path.join(B.OUTBOX, f"{tid}.{peer}.md")):
            continue
        out.append((tid, kind, meta.get("mode") or ""))
    return out


def _stuck(peer_rec: dict, task_id: str, kind: str) -> bool:
    if not peer_rec.get("online"):
        return True
    # 在线：只有「已领、但它空闲着、手上报的不是这单」才算占着；待领的单它下一圈就会领
    return kind == "taken" and B.activity_of(peer_rec) == "idle" and peer_rec.get("task_id") != task_id


def held_items(peer: str = "") -> list:
    """被占着不动的单：[{task_id, peer, kind, mode, online}]。peer 为空 = 所有 Agent。"""
    if peer:
        names = [peer]
    else:
        # 登记过的 + 只有收件箱的（从没连上、或已被「清理离线」删了登记）：后者手上的单最容易被忘掉
        try:
            boxes = [n for n in os.listdir(B.INBOX) if os.path.isdir(os.path.join(B.INBOX, n))]
        except OSError:
            boxes = []
        names = sorted({p["peer"] for p in B.list_peers()} | set(boxes))
    peers = [B.peer_state(n) for n in names if n != "controller"]
    out = []
    for p in peers:
        for tid, kind, mode in _held_by(p["peer"]):
            if _stuck(p, tid, kind):
                out.append({"task_id": tid, "peer": p["peer"], "kind": kind, "mode": mode, "online": bool(p.get("online"))})
    return out


STALL_FREE_SEC = 1800   # 领了 30 分钟没有任何干活迹象（ws_* 调用 / 回复）= 领了不做


def _working_on(peer: str, task_id: str, within: float) -> bool:
    """总线心跳里最近 within 秒内报过「在做这一单」。ws_* 调用会刷新它（bus_core.touch_work）。"""
    beat = B.read_json(B._beat_path(peer), None) or {}
    age = B.age_sec(beat.get("at"))
    return beat.get("task_id") == task_id and age is not None and age <= within and beat.get("activity") in ("think", "build", "discuss")


def stalled_claims(min_age: float = STALL_FREE_SEC) -> list:
    """领了不做的单：.taken 已超过 min_age、任务还开着、没回复、领取者这段时间没有任何在做它的迹象。
    不管领取者显示在线与否——「假在线」（外部脚本续命、脚本代领后没人干）正是要抓的。"""
    out = []
    try:
        peers = [n for n in os.listdir(B.INBOX) if os.path.isdir(os.path.join(B.INBOX, n)) and n != "controller"]
    except OSError:
        return out
    for peer in sorted(peers):
        st = None
        for tid, kind, mode in _held_by(peer):
            if kind == "queued":
                # 排在收件箱里 30 分钟没人领、而它并没有在等任务（假在线 / 掉线）：一样收走
                st = st or B.peer_state(peer)
                if st.get("listening"):
                    continue
                # 按「进这个收件箱多久」算，不按任务创建时间：刚改派过来的单不能下一圈又被判超时（2026-10-02 实测来回弹）
                try:
                    age = max(0.0, time.time() - os.path.getmtime(os.path.join(B.INBOX, peer, f"{tid}.json")))
                except OSError:
                    age = None
                if age is None or age < min_age:
                    continue
                out.append({"task_id": tid, "peer": peer, "kind": kind, "mode": mode, "claimed_min": int(age // 60),
                            "online": bool(st.get("online"))})
                continue
            t = B.read_json(os.path.join(B.INBOX, peer, f"{tid}.taken"), {}) or {}
            age = B.age_sec(t.get("taken_at"))
            if age is None or age < min_age or _working_on(peer, tid, min_age):
                continue
            out.append({"task_id": tid, "peer": peer, "kind": kind, "mode": mode, "claimed_min": int(age // 60),
                        "online": bool(B.peer_state(peer).get("online"))})
    return out


def free_stalled(by: str = "guard", min_age: float = STALL_FREE_SEC) -> dict:
    """把领了不做的单收回并改派给别人（优先正在等任务的人），领取者记一次；没人可接就释放成过期，留给 Leader / 分配器。"""
    import bus_review as RV
    moved, released, errors = [], [], []
    for it in stalled_claims(min_age):
        tid, who, mode = it["task_id"], it["peer"], it["mode"]
        try:
            if it["kind"] == "taken":
                B.add_strike(who, tid)   # 只罚「领了不做」；没领的单是派错了人，不算它的
            if mode == "build":
                moved.append({**it, **_reassign_build(tid, who, by, listening_only=True)})
                continue
            if mode == "review":
                r = RV.review_now(tid, by=by)
                if r["dispatched"]:
                    moved.append({**it, "to": "、".join(r["dispatched"][0]["reviewers"])})
                    continue
            meta = B.read_json(os.path.join(B.OUTBOX, f"{tid}.meta.json"), None) or {}
            target = pick_free_target(who, exclude=list(meta.get("peers") or []) + [who, B.review_author(meta)])
            if target:
                moved.append({**it, "to": B.reassign(tid, who, to_peer=target, by=by)["to"]})
            elif it["kind"] == "queued":
                released.append(it)          # 没领的单没人可接：原样留在它收件箱里，等它真来 wait 或有人上线
            else:
                src = os.path.join(B.INBOX, who, f"{tid}.taken")
                t = B.read_json(src, {}) or {}
                t["reclaimed_at"] = B.now_iso()
                B.write_json(src, t)
                os.replace(src, os.path.join(B.INBOX, who, f"{tid}.json.expired"))
                released.append(it)
        except Exception as exc:  # noqa: BLE001  一单失败不挡其他单
            errors.append({**it, "reason": str(exc)})
    return {"moved": moved, "released": released, "errors": errors}


def clean_closed_copies() -> int:
    """任务已经关了（outbox 没有 meta），收件箱里还留着它的副本：挪进 _closed/，不再算作「已领未回 / 待领」。
    实测 2026-10-02 有 27 份分在 13 个 Agent 名下，让空闲的 Agent 看上去手上压着活。只挪不删。"""
    moved = 0
    try:
        peers = [n for n in os.listdir(B.INBOX) if os.path.isdir(os.path.join(B.INBOX, n))]
    except OSError:
        return 0
    for peer in peers:
        d = os.path.join(B.INBOX, peer)
        for f in os.listdir(d):
            if not f.endswith((".json", ".taken", ".json.expired")):
                continue
            tid = f.split(".")[0]
            if os.path.exists(os.path.join(B.OUTBOX, f"{tid}.meta.json")):
                continue
            os.makedirs(os.path.join(d, "_closed"), exist_ok=True)
            try:
                os.replace(os.path.join(d, f), os.path.join(d, "_closed", f))
                moved += 1
            except OSError:
                pass
    return moved


def builder_groups() -> list:
    """console/roles.json 里勾了「能施工」的分组；没配就是空表（= 不限）。"""
    try:
        return list(((B.read_roles() or {}).get("assignments") or {}).get("builder_groups") or [])
    except Exception:  # noqa: BLE001  roles.json 损坏：不限组，别让改派整个报错
        return []


def pick_free_target(peer: str, exclude: list, role: str = "") -> str:
    """收回来的单派给谁：只派给**正在 wait** 的人（listening）——派给假在线的人等于再压一次；
    没被罚下、不是原领取者；同组优先，其次按收件箱里已有的单数轮流，免得全压到一个人身上。没人就返回空。
    role="builder"：施工单只派给「能施工」的分组（2026-10-02：施工单被转给不干活的 flash 模型，转了 5 手没人交）。"""
    ex = set(exclude or []) | {peer}
    g = B.peer_group(peer)
    groups = builder_groups() if role == "builder" else []
    cands = [p for p in B.list_peers() if p.get("listening") and p["peer"] != "controller" and p["peer"] not in ex and not B.benched(p["peer"])
             and (not groups or B.peer_group(p["peer"]) in groups)]
    load = lambda p: (lambda c: c["queued"] + c["claimed"])(B.inbox_counts(p["peer"]))
    cands.sort(key=lambda p: (not (g and B.peer_group(p["peer"]) == g), load(p), p["peer"]))
    return cands[0]["peer"] if cands else ""


def _reassign_build(task_id: str, peer: str, by: str, listening_only: bool = False) -> dict:
    """施工单换人：新施工者按同一基线开自己的 worktree，任务包里的 worktree / branch 换成新的；原 worktree 保留。"""
    import bus_build as BB
    meta = B.read_json(os.path.join(B.OUTBOX, f"{task_id}.meta.json"), None) or {}
    exclude = list(meta.get("peers") or []) + list((BB.record(task_id).get("teammates") or {}).keys())
    # 施工单只交给正在等任务、且属于「能施工」分组的人（listening_only 参数保留兼容，施工单一律按它走）：
    # 以前退而求其次派给「在线但没在等」的人，结果单子在几个不干活的 Agent 之间转圈直到过期
    target = pick_free_target(peer, exclude, role="builder")
    if not target:
        groups = builder_groups()
        raise ValueError(f"现在没有正在等任务的施工 Agent 可以接 {task_id}"
                         + (f"（能施工的分组：{'、'.join(groups)}）" if groups else "")
                         + "。可以等施工 Agent 上线后再改派，或在 Agent 页给更多分组勾上「能施工」，或直接取消这张单")
    tm = BB.add_teammate(task_id, target)
    r = B.reassign(task_id, peer, to_peer=target, by=by)
    path = os.path.join(B.INBOX, target, f"{task_id}.json")
    task = B.read_json(path, None)
    if isinstance(task, dict):
        task.update(worktree=tm["path"], branch=tm["branch"])
        B.write_json(path, task)
    return {"to": r["to"], "worktree": tm["path"]}


def reassign_held(peer: str = "", by: str = "user", only: str = "") -> dict:
    """把 held_items 逐单改派。返回 {moved, skipped, errors}，每条都写清去了谁 / 为什么没动。"""
    import bus_review as RV
    moved, skipped, errors = [], [], []
    for it in held_items(peer):
        if only and it["task_id"] != only:      # 分配器一次只动它挑中的那一单
            continue
        tid, who, mode = it["task_id"], it["peer"], it["mode"]
        try:
            if mode == "build":
                moved.append({**it, **_reassign_build(tid, who, by)})
            elif mode == "review":
                r = RV.review_now(tid, by=by)
                if r["dispatched"]:
                    moved.append({**it, "to": "、".join(r["dispatched"][0]["reviewers"]), "new_task": r["dispatched"][0]["review_task"]})
                else:
                    (errors if r["errors"] else skipped).append({**it, "reason": (r["errors"] or r["skipped"] or [{}])[0].get("error") or (r["skipped"] or [{}])[0].get("reason", "")})
            else:
                r = B.reassign(tid, who, by=by)
                moved.append({**it, "to": r["to"]})
        except Exception as exc:  # noqa: BLE001  一单失败不挡其他单
            errors.append({**it, "reason": str(exc)})
    if moved:
        try:
            import bus_events as E
            E.emit("task.reassigned", {"by": by, "count": len(moved), "tasks": [m["task_id"] for m in moved]})
        except Exception:  # noqa: BLE001
            pass
    return {"moved": moved, "skipped": skipped, "errors": errors}
