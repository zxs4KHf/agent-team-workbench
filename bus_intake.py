"""外部来源收件箱：数据源插件送来的候选条目，必须经本人确认才会变成 Leader 指令。

为什么要确认这一步：2026-09 的外部源 adapter（B9）首轮运行可能一次导入约 85 条历史项。
数据源只负责「召回」，是否值得做由人判断——这是 docs/adr/0002-plugins.md 的硬约束。
"""
import hashlib
import json
import os
import re

import bus_core as B
import bus_events as E
from bus_locks import file_mutex

INTAKE = os.path.join(B.ROOT, "intake")
STATUSES = ("pending", "accepted", "dismissed")
MAX_BATCH = 200
_ID = re.compile(r"^[a-f0-9]{16}$")


def _path(iid: str) -> str:
    return os.path.join(INTAKE, iid + ".json")


def _clean(s, n):
    return str(s or "").replace("\x00", "")[:n]


def item_id(source: str, fingerprint: str) -> str:
    return hashlib.sha1(f"{source}\n{fingerprint}".encode("utf-8")).hexdigest()[:16]


def add(source: str, items: list) -> dict:
    """批量加入候选。同一 source+fingerprint 只收一次（幂等），已处理的不会被重新打开。"""
    source = _clean(source, 60)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", source):
        raise ValueError("source 需为插件 id（小写字母、数字、连字符）")
    if not isinstance(items, list) or len(items) > MAX_BATCH:
        raise ValueError(f"items 需为列表且不超过 {MAX_BATCH} 条")
    os.makedirs(INTAKE, exist_ok=True)
    added, skipped = [], 0
    with file_mutex(os.path.join(INTAKE, ".intake.mutex")):
        for it in items:
            if not isinstance(it, dict):
                skipped += 1
                continue
            fp = _clean(it.get("fingerprint"), 200)
            title = _clean(it.get("title"), 200).strip()
            if not fp or not title:
                skipped += 1
                continue
            iid = item_id(source, fp)
            if os.path.exists(_path(iid)):
                skipped += 1
                continue
            rec = {"id": iid, "source": source, "fingerprint": fp, "title": title,
                   "text": _clean(it.get("text"), 4000), "url": _clean(it.get("url"), 500),
                   "due": _clean(it.get("due"), 40), "tags": [_clean(t, 40) for t in (it.get("tags") or [])][:10],
                   "origin": _clean(it.get("origin"), 60), "status": "pending", "created": B.now_iso()}
            B.write_json(_path(iid), rec)
            added.append(iid)
    if added:
        E.emit("intake.added", {"source": source, "count": len(added)})
    return {"ok": True, "added": len(added), "skipped": skipped, "ids": added}


def items(status: str = "", limit: int = 300) -> list:
    if not os.path.isdir(INTAKE):
        return []
    out = []
    for f in os.listdir(INTAKE):
        if f.endswith(".json"):
            rec = B.read_json(os.path.join(INTAKE, f), None)
            if isinstance(rec, dict) and (not status or rec.get("status") == status):
                out.append(rec)
    # 解析后比较（ADR 0010）：created 可能是无偏移旧串，字符串序随时区漂移
    out.sort(key=lambda r: (B.rec_ts(r, "created"), str(r.get("created") or "")), reverse=True)
    return out[:limit]


def counts() -> dict:
    c = {s: 0 for s in STATUSES}
    for r in items(limit=100000):
        c[r.get("status", "pending")] = c.get(r.get("status", "pending"), 0) + 1
    return c


def decide(iid: str, action: str, project: str = "", note: str = "") -> dict:
    """accept：转成给 Leader 的 external 指令；dismiss：忽略。重复决定会被拒绝。"""
    import bus_projects as P
    if not _ID.match(str(iid or "")):
        raise ValueError("条目 id 不合法")
    if action not in ("accept", "dismiss"):
        raise ValueError("action 只能是 accept 或 dismiss")
    with file_mutex(os.path.join(INTAKE, ".intake.mutex")):
        rec = B.read_json(_path(iid), None)
        if not isinstance(rec, dict):
            raise ValueError("没有找到该条目")
        if rec.get("status") != "pending":
            raise ValueError(f"该条目已处理（{rec.get('status')}）")
        rec["status"] = "accepted" if action == "accept" else "dismissed"
        rec["decided"] = B.now_iso()
        rec["note"] = _clean(note, 1000)
        B.write_json(_path(iid), rec)   # 先落盘再入队：写失败就不会留下"已入队但仍 pending"导致再点一次重复下指令
        if action == "accept":
            try:
                cmd = P.push_command("external", {
                    "note": f"外部来源「{rec['source']}」条目，本人已确认值得处理",
                    "project": _clean(project, 80), "intake_id": iid, "title": rec["title"],
                    "text": rec["text"], "url": rec["url"], "due": rec["due"], "comment": rec["note"]})
            except Exception:
                rec.update(status="pending", decided="")   # 入队失败：退回待定，可再点一次
                B.write_json(_path(iid), rec)
                raise
            rec["command_id"] = (cmd or {}).get("id", "")
            B.write_json(_path(iid), rec)
    E.emit("intake." + rec["status"], {"id": iid, "source": rec["source"], "title": rec["title"]}, subject=iid)
    return {"ok": True, "item": rec}
