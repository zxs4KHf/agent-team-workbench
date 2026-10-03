"""工作台事件记录（events-v1）：只追加的 JSONL 文件，跨项目互通的唯一契约。

设计见 docs/adr/0003-events-outbox.md。要点：
- 一天一个文件 events/YYYY-MM-DD.jsonl，每行一条事件，只追加不改写；
- 字段取 CloudEvents 的子集：specversion / id / source / type / time / subject / data；
- 读方自己记游标（"YYYY-MM-DD:字节偏移"），工作台不需要知道谁在读；
- emit 永不抛异常：事件是旁路记录，写失败不能影响核心流程。
"""
import datetime as dt
import json
import os
import uuid

import bus_core as B
from bus_locks import file_mutex

EVENTS = os.path.join(B.ROOT, "events")
SPEC = "1.0"
SOURCE = "agent-workbench"
MAX_DATA_CHARS = 8000


def _day_path(day: str) -> str:
    return os.path.join(EVENTS, f"{day}.jsonl")


def emit(type_: str, data: dict = None, subject: str = "") -> dict:
    """追加一条事件；返回事件本身。失败时返回 {}，不抛异常。"""
    try:
        now = dt.datetime.now().astimezone()
        ev = {"specversion": SPEC, "id": uuid.uuid4().hex, "source": SOURCE, "type": str(type_)[:80],
              "time": now.isoformat(timespec="seconds"), "subject": str(subject or "")[:200],
              "data": data or {}}
        line = json.dumps(ev, ensure_ascii=False)
        if len(line) > MAX_DATA_CHARS:
            ev["data"] = {"truncated": True}
            line = json.dumps(ev, ensure_ascii=False)
        os.makedirs(EVENTS, exist_ok=True)
        path = _day_path(now.strftime("%Y-%m-%d"))
        with file_mutex(path + ".mutex"):
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
        return ev
    except Exception:
        return {}


def _parse_cursor(cursor: str):
    try:
        day, off = str(cursor).split(":", 1)
        dt.date.fromisoformat(day)
        return day, max(0, int(off))
    except (ValueError, TypeError):
        return None, 0


def read(cursor: str = "", limit: int = 200, days: int = 7) -> dict:
    """从游标之后读事件。没有游标时从最近 days 天的开头读。返回 {events, cursor}。"""
    limit = max(1, min(int(limit or 200), 1000))
    if not os.path.isdir(EVENTS):
        return {"events": [], "cursor": cursor or ""}
    files = sorted(f[:-6] for f in os.listdir(EVENTS) if f.endswith(".jsonl"))
    if cursor == "now":                 # 只要以后的事件（通知类插件首启用，避免补发 7 天历史）
        if not files:
            return {"events": [], "cursor": ""}
        return {"events": [], "cursor": f"{files[-1]}:{os.path.getsize(_day_path(files[-1]))}"}
    day0, off0 = _parse_cursor(cursor)
    if day0 is None:
        files = files[-max(1, days):]
        day0, off0 = (files[0], 0) if files else ("", 0)
    out, cur = [], cursor or ""
    for day in files:
        if day < day0:
            continue
        start = off0 if day == day0 else 0
        with open(_day_path(day), "rb") as f:
            f.seek(start)
            while len(out) < limit:
                raw = f.readline()
                if not raw or not raw.endswith(b"\n"):   # 半行（正在写）留到下次
                    break
                start += len(raw)
                try:
                    out.append(json.loads(raw.decode("utf-8")))
                except ValueError:
                    continue
        cur = f"{day}:{start}"
        if len(out) >= limit:
            break
    return {"events": out, "cursor": cur}


def recent(n: int = 50) -> list:
    """最近 n 条事件（新的在前），给界面用。"""
    if not os.path.isdir(EVENTS):
        return []
    out = []
    for name in sorted((f for f in os.listdir(EVENTS) if f.endswith(".jsonl")), reverse=True):
        with open(os.path.join(EVENTS, name), encoding="utf-8") as f:
            lines = f.readlines()
        for raw in reversed(lines):
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue
            if len(out) >= n:
                return out
    return out
