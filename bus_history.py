"""任务历史（3847 重落地）：只读完整历史分页 / 游标接口。

数据来源只有两处现有事实：`console/done/*.json`（已完成指令）与
`projects/<slug>/decisions.json` 的终态决策。不新增事实表、不写任何业务状态、
不改 done/decisions 目录。

相对 0f52bed 旧实现，本文件按 20260926-142942-2c0a 的 RETURN 修正了六点：
1. `project` 不再走 `B.valid_name`（那是 peer 名规则，`协作工作台`/`重力回廊` 会被误拒），
   改成 `P.list_projects()` 白名单 + `project_dir` 的 commonpath 包含校验。
2. 终态口径取 `P.TERMINAL_STATUSES`（与提醒、决策过滤共用一份），不在本模块写死。
3. 游标帧取 981338a 的定长尾部 framing（raw + 16 字节 mac），不再用 `raw + "." + mac`
   的 `rsplit`——mac 里随机出现 `.` 时约 6% 概率切错位。
4. 快照缓存按 (project, filter) 复用，短 TTL + 条数上限 LRU；构建在锁内二次检查，
   同一键不会并发重复扫全量。
5. canonical id 不再掺 `basename(WORKSPACE)`：workspace 改指向不会让全部历史换 id。
   id = 固定 namespace + project + 源类型 + 源原生 id 的摘要，namespace/版本随响应下发。
6. 越界 offset 直接 410 `cursor_expired`，不返回「空末页」静默丢数据；
   `now` 可注入，过期分支能被真实测试打到。
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

import bus_core as B
import bus_projects as P

SNAPSHOT_TTL_SEC = 600          # 游标即凭据：10 分钟过期，且只在本进程内存活（重启全批 cursor_expired）
MAX_LIMIT = 100
DEFAULT_LIMIT = 20
MAX_SNAPS = 64                  # 快照条数上限：只按 TTL 清不够，翻页中途关掉的标签页会留孤儿快照
RESULT_CAP = 500
ID_NAMESPACE = "atwb-history-v1"
ID_VERSION = 1
FILTERS = ("all", "commands", "decisions")
_SECRET = secrets.token_bytes(32)
_SNAPS = {}
_LOCK = threading.Lock()


class CursorError(ValueError):
    def __init__(self, code, msg, status):
        super().__init__(msg)
        self.code, self.status = code, status


def canonical_id(project: str, source_type: str, native_id: str) -> str:
    """稳定 id：与 workspace 路径无关，换机器/换目录不改变已读态。"""
    raw = "|".join([ID_NAMESPACE, str(project), str(source_type), str(native_id)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def known_projects() -> list:
    try:
        return [p["slug"] for p in P.list_projects() if p.get("slug")]
    except Exception:  # noqa: BLE001  读不到项目列表时按「无项目」处理，不能让接口 500
        return []


def check_project(project: str) -> str:
    """只接受现存 slug（白名单），并要求其目录确实在 projects/ 之内。"""
    project = str(project or "").strip()
    if not project:
        return ""
    slugs = known_projects()
    if project not in slugs:
        raise CursorError("bad_project", f"项目不存在（可选：{'/'.join(slugs) or '（无）'}）", 400)
    root = os.path.realpath(P.PROJECTS)
    d = os.path.realpath(P.project_dir(project))
    if os.path.commonpath([root, d]) != root:
        raise CursorError("bad_project", "项目目录越界", 400)
    return project


def safe_merge_path(d: dict) -> str:
    """merge_path 只透出工作区相对路径：绝对路径、盘符、`..` 逃逸一律不给，并留计数。"""
    raw = str(d.get("merge_path") or "")
    if not raw:
        return ""
    cand = raw.replace("\\", "/")
    if os.path.isabs(cand) or (len(cand) > 1 and cand[1] == ":") or cand.startswith("/"):
        return ""
    norm = os.path.normpath(cand)
    if norm.startswith("..") or norm == ".":
        return ""
    return norm.replace("\\", "/")            # 线格式统一用正斜杠，别让 Windows 分隔符进 API


def _decision_time(d: dict) -> str:
    return str(d.get("updated") or d.get("created") or "")


def _iter_source(project: str, flt: str, skipped: list):
    """产生 (business_time, canonical_id, record)；只读现有文件，坏记录跳过并计数（不外泄正文）。"""
    slugs = [project] if project else known_projects()
    if flt in ("all", "commands"):
        # 指令按 done 文件本身归属，一条只出现一次；无 project 归属的全局指令在任何视图都可见
        for c, own in _done_commands(skipped):
            if project and own and own != project:
                continue
            t = str(c.get("done_at") or c.get("acked_at") or c.get("created") or "")
            cid = canonical_id(own, "command", c["id"])
            yield t, cid, {"kind": "command", "id": c["id"], "project": own, "cmd_kind": c.get("kind", ""),
                           "task_id": (c.get("payload") or {}).get("task_id") or "",
                           "status": "failed" if c.get("ok") is False else "done",
                           "result": str(c.get("result") or "")[:RESULT_CAP], "time": t}
    if flt in ("all", "decisions"):
        for slug in slugs:
            for d in _decisions(slug, skipped):
                t = _decision_time(d)
                cid = canonical_id(slug, "decision", d["task_id"])
                yield t, cid, {"kind": "decision", "id": d["task_id"], "project": slug, "revision": d.get("revision", 0),
                               "status": d.get("status"), "title": str(d.get("title") or "")[:200],
                               "merge_path": safe_merge_path(d), "time": t}


def _done_commands(skipped: list):
    if not os.path.isdir(P.DONE):
        return
    for f in sorted(os.listdir(P.DONE)):
        if not f.endswith(".json"):
            continue
        c = B.read_json(os.path.join(P.DONE, f), None)
        if not isinstance(c, dict) or not c.get("id"):
            skipped.append(f)               # 只记文件名：坏记录正文不得进错误信息
            continue
        yield c, str((c.get("payload") or {}).get("project") or "")


def _decisions(slug: str, skipped: list):
    try:
        rows = P.decisions(slug)
    except Exception:  # noqa: BLE001  decisions.json 损坏 → 跳过该源并计数
        skipped.append(f"{slug}/decisions.json")
        return
    for d in rows or []:
        if not isinstance(d, dict) or not d.get("task_id"):
            skipped.append(f"{slug}:bad_decision")
            continue
        if not P.is_terminal(d.get("status")):
            continue
        yield d


def _order_key(row):
    t = row[0][:19]
    valid = len(t) == 19 and t[4] == "-" and t[10] in "T "
    return (0 if valid else 1, "" if not valid else "".join(chr(0x10FFFF - ord(ch)) for ch in t), row[1])


def _build_snapshot(project: str, flt: str, now: float) -> dict:
    key = (project, flt)
    with _LOCK:                              # 锁内二次检查：同一 (project, filter) 只有一个线程真扫
        hit = _SNAPS.get(key)
        if hit and now - hit["created"] <= SNAPSHOT_TTL_SEC:
            return hit
        skipped = []
        rows, seen = [], set()
        for row in _iter_source(project, flt, skipped):
            if row[1] in seen:               # 同一 canonical id 只留一条，避免翻页重复
                skipped.append("dup:" + row[1][:8])
                continue
            seen.add(row[1])
            rows.append(row)
        rows.sort(key=_order_key)
        snap = {"id": secrets.token_hex(8), "project": project, "filter": flt, "created": now,
                "ids": [r[1] for r in rows], "records": {r[1]: r[2] for r in rows},
                "skipped": len(skipped)}
        _SNAPS[key] = snap
        while len(_SNAPS) > MAX_SNAPS:       # LRU：条数上限，按最早创建淘汰
            oldest = min(_SNAPS, key=lambda k: _SNAPS[k]["created"])
            _SNAPS.pop(oldest, None)
        return snap


def _expire(now: float):
    for k in [k for k, v in _SNAPS.items() if now - v["created"] > SNAPSHOT_TTL_SEC]:
        _SNAPS.pop(k, None)


def _sign(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    mac = hmac.new(_SECRET, raw, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(raw + mac).decode("ascii").rstrip("=")


def _verify(cursor: str) -> dict:
    try:
        blob = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        if len(blob) <= 16:
            raise ValueError("too short")
        raw, mac = blob[:-16], blob[-16:]        # 定长尾部，不吃 mac 里可能的 "."
    except Exception:
        raise CursorError("cursor_invalid", "游标格式无效", 400) from None
    if not hmac.compare_digest(hmac.new(_SECRET, raw, hashlib.sha256).digest()[:16], mac):
        raise CursorError("cursor_invalid", "游标签名不符（被篡改或来自别的服务实例）", 400)
    try:
        c = json.loads(raw.decode("utf-8"))
    except Exception:
        raise CursorError("cursor_invalid", "游标内容不可解析", 400) from None
    if not isinstance(c, dict):
        raise CursorError("cursor_invalid", "游标内容不是对象", 400)
    return c


def history_page(project: str = "", flt: str = "all", cursor: str = "", limit: int = DEFAULT_LIMIT, now: float = None) -> dict:
    """只读分页。返回 items/next_cursor/total/range/complete/snapshot/source/id_namespace。"""
    if now is None:
        now = time.time()
    project = check_project(project)
    flt = str(flt or "all")
    if flt not in FILTERS:
        raise CursorError("bad_filter", f"filter 只能是 {'/'.join(FILTERS)}", 400)
    try:
        limit = int(limit if limit not in (None, "") else DEFAULT_LIMIT)
    except (TypeError, ValueError):
        raise CursorError("bad_limit", "limit 必须是整数", 400)
    limit = max(1, min(limit, MAX_LIMIT))
    with _LOCK:
        _expire(now)
    if cursor:
        c = _verify(cursor)
        if str(c.get("project", "")) != project or c.get("filter") != flt:
            raise CursorError("cursor_invalid", "游标绑定的 project/filter 与本次请求不一致，请从头重取", 400)
        with _LOCK:
            snap = _SNAPS.get((project, flt))
        if not snap or snap["id"] != c.get("sid") or now - snap["created"] > SNAPSHOT_TTL_SEC:
            raise CursorError("cursor_expired", "游标已过期（快照超过 10 分钟或服务已重启），请从头重取", 410)
        offset = int(c.get("offset") or 0)
        if offset < 0 or offset > len(snap["ids"]):
            raise CursorError("cursor_expired", "游标位置已不在本次快照范围内，请从头重取", 410)
    else:
        snap = _build_snapshot(project, flt, now)
        offset = 0
    ids = snap["ids"]
    page_ids = ids[offset:offset + limit]
    nxt = offset + limit
    next_cursor = _sign({"sid": snap["id"], "offset": nxt, "project": project, "filter": flt}) if nxt < len(ids) else ""
    items = [dict(snap["records"][i], canonical_id=i) for i in page_ids]
    return {
        "items": items, "next_cursor": next_cursor, "total": len(ids), "offset": offset, "limit": limit,
        "range": {"start": offset, "end": offset + len(page_ids)}, "complete": not next_cursor,
        "snapshot": {"id": snap["id"], "created": dt.datetime.fromtimestamp(snap["created"]).strftime("%Y-%m-%dT%H:%M:%S"),
                     "ttl_sec": SNAPSHOT_TTL_SEC, "skipped": snap.get("skipped", 0)},
        "source": {"commands": "console/done/*.json（已完成指令）", "decisions": "projects/<slug>/decisions.json 的终态决策",
                   "terminal_statuses": list(P.TERMINAL_STATUSES)},
        "id_namespace": ID_NAMESPACE, "id_version": ID_VERSION,
        "project": project, "filter": flt,
        "note": "分页基于快照冻结顺序；快照有效期内新增/不会挤动本页，翻页到底或过期后从头重取即可看到",
    }


def reset_cache() -> None:
    """测试用：清进程内快照（服务重启等价）。"""
    with _LOCK:
        _SNAPS.clear()
