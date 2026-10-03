"""按积压自动分配 Agent（ADR 0012）：闲着的 Agent 派到最缺人的那一段，先预演。

守护每圈调一次 tick()：
  · 三段积压：待审（已提交没人审）、施工被离线者占着、其他任务被离线 / 空闲者占着；
  · 每段压力 = 积压单数 ÷ 能接这段的空闲人数（空闲 = 在线、idle、收件箱里没有待领 / 已领的单）；
  · 按压力从高到低、同段内最老的单先，挑出最多 MAX_PER_CYCLE 个动作；每个空闲 Agent 一圈最多被派 1 单；
    同一个名额被换人超过 MAX_MOVES_PER_SLOT 次就不再动，留给人。
模式（console/allocator.json 的 mode）：off 不做；preview 只算出「会怎么派」写进 last_plan（默认）；on 真派。
真派复用现有路径：被占着的单走 bus_reassign（施工换人开新 worktree），待审走 bus_review.review_now。
预演里的「派给谁」用同一套挑人函数算，真派时以那一刻的在线情况为准，可能略有不同。
"""
import os

import bus_core as B

MODES = ("off", "preview", "on")
MAX_PER_CYCLE = 5
MAX_MOVES_PER_SLOT = 2
STAGE_LABEL = {"review": "待审", "build": "施工没人做", "answer": "回答没人做"}
def _cfg_path() -> str:
    return os.path.join(B.ROOT, "console", "allocator.json")   # 每次现算：测试 / 工作副本各用各的数据目录


def config() -> dict:
    c = B.read_json(_cfg_path(), None) or {}
    c["mode"] = c.get("mode") if c.get("mode") in MODES else "preview"
    return c


def set_mode(mode: str) -> dict:
    if mode not in MODES:
        raise ValueError(f"mode 只能是 {' / '.join(MODES)}")
    c = config()
    c["mode"] = mode
    B.write_json(_cfg_path(), c)
    return c


def idle_agents() -> list:
    out = []
    for p in B.list_peers():
        # 只算真正在 wait 的人（listening），且没有因「领了不做」被罚下：在线却不等任务的派过去也没人领
        if p["peer"] == "controller" or not p.get("listening") or B.benched(p["peer"]):
            continue
        c = B.inbox_counts(p["peer"])
        if c["queued"] or c["claimed"]:
            continue
        out.append(p["peer"])
    return out


def _moves(task_id: str) -> int:
    meta = B.read_json(os.path.join(B.OUTBOX, f"{task_id}.meta.json"), None) or {}
    return sum(1 for r in meta.get("reassign_log") or [] if not r.get("error"))


def backlog() -> dict:
    """三段积压，每条 {stage, task_id, peer?, teammate?, mode, created}。"""
    import bus_reassign as RA
    import bus_review as RV
    import bus_build as BB
    stages = {"review": [], "build": [], "answer": []}
    reviews = RV.open_reviews()
    seen = set()
    for tid, mate in RV.pending_targets():
        if tid in seen or RV._live_review_for(tid, mate, reviews):
            continue
        seen.add(tid)
        stages["review"].append({"stage": "review", "task_id": tid, "teammate": mate, "mode": "build",
                                 "created": BB.record(tid).get("created", "")})
    for it in RA.held_items():
        if _moves(it["task_id"]) >= MAX_MOVES_PER_SLOT:
            continue
        meta = B.read_json(os.path.join(B.OUTBOX, f"{it['task_id']}.meta.json"), None) or {}
        stage = "build" if it["mode"] == "build" else "review" if it["mode"] == "review" else "answer"
        stages[stage].append({**it, "stage": stage, "created": meta.get("created", "")})
    for items in stages.values():
        items.sort(key=lambda x: B.rec_ts(x, "created"))
    return stages


def _target_for(item: dict, idle: list) -> str:
    """预演用：真派时挑人的同一套规则，限定在本圈还空着的人里。"""
    if item["stage"] == "review":
        picks = [p for p in B.pick_reviewers(exclude=[item.get("teammate") or item.get("peer") or ""], n=len(idle) or 1) if p in idle]
        return picks[0] if picks else ""
    same = [p for p in idle if B.peer_group(p) and B.peer_group(p) == B.peer_group(item.get("peer", ""))]
    return (same or idle or [""])[0]


def plan() -> dict:
    idle = idle_agents()
    stages = backlog()
    pressure = {s: (len(items) / max(1, len(idle))) for s, items in stages.items()}
    order = sorted(stages, key=lambda s: -pressure[s])
    free, actions = list(idle), []
    cursor = {s: 0 for s in stages}
    while free and len(actions) < MAX_PER_CYCLE:
        progressed = False
        for s in order:   # 按压力轮流取，避免最高压力那段把人全吃光
            if not free or len(actions) >= MAX_PER_CYCLE:
                break
            while cursor[s] < len(stages[s]):
                item = stages[s][cursor[s]]
                cursor[s] += 1
                who = _target_for(item, free)
                if who:
                    free.remove(who)
                    actions.append({**item, "to": who})
                    progressed = True
                    break
        if not progressed:
            break
    return {"time": B.now_iso(), "idle": idle, "backlog": {s: len(v) for s, v in stages.items()},
            "pressure": {s: round(v, 2) for s, v in pressure.items()}, "actions": actions}


def _execute(action: dict) -> dict:
    import bus_reassign as RA
    import bus_review as RV
    if action["stage"] == "review" and action.get("teammate"):
        r = RV.review_now(action["task_id"], by="allocator")
        return {"ok": bool(r["dispatched"]), "detail": r}
    r = RA.reassign_held(action["peer"], by="allocator", only=action["task_id"])
    return {"ok": bool(r["moved"]), "detail": r}


def tick() -> dict:
    """守护每圈调用。off 什么都不做；preview 只记录计划；on 执行并记录结果。"""
    c = config()
    if c["mode"] == "off":
        return {"mode": "off"}
    p = plan()
    if c["mode"] == "on":
        for a in p["actions"]:
            try:
                a["result"] = _execute(a)
            except Exception as exc:  # noqa: BLE001  一单失败不挡其他单
                a["result"] = {"ok": False, "error": str(exc)}
    c["last_plan"] = p
    B.write_json(_cfg_path(), c)
    if c["mode"] == "on" and p["actions"]:
        try:
            import bus_events as E
            E.emit("allocator.dispatched", {"count": len(p["actions"]), "tasks": [a["task_id"] for a in p["actions"]]})
        except Exception:  # noqa: BLE001
            pass
    return {"mode": c["mode"], **p}


def view() -> dict:
    """给界面：模式 + 上一圈的计划（没有就现算一份预演，不落盘）。"""
    # 只读落盘的上一圈计划，不现算：/api/state 每几秒刷一次，现算要扫全部收件箱与施工记录（守护一圈就会写一份）
    c = config()
    return {"mode": c["mode"], "last_plan": c.get("last_plan"), "labels": STAGE_LABEL,
            "limits": {"per_cycle": MAX_PER_CYCLE, "moves_per_slot": MAX_MOVES_PER_SLOT, "per_agent": 1}}
