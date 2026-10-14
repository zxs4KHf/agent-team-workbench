"""体验反馈：本人在任何页面随手记下的问题或想法，是工作台「自我迭代」的入口。

流程：记录→转任务→待验证或暂缓；有明确验证依据才解决，明确决定才不做。
反馈只存在本机 feedback/ 目录，不入库。设计见 docs/adr/0004-feedback-loop.md。
"""
import os
import re
import uuid

import bus_core as B
import bus_events as E
from bus_locks import file_mutex

FEEDBACK = os.path.join(B.ROOT, "feedback")
STATUSES = ("open", "converted", "awaiting_verification", "deferred", "done", "dismissed")
ACTIONABLE = ("open", "awaiting_verification", "deferred")
KINDS = ("bug", "ux", "idea")
_ID = re.compile(r"^fb-[a-f0-9]{10}$")


def _path(fid: str) -> str:
    return os.path.join(FEEDBACK, fid + ".json")


def _clean(s, n):
    return str(s or "").replace("\x00", "")[:n]


def create(text: str, kind: str = "ux", page: str = "", view: str = "", target: str = "",
           viewport: str = "", project: str = "") -> dict:
    text = _clean(text, 4000).strip()
    if len(text) < 2:
        raise ValueError("请写下遇到的问题或想法")
    if kind not in KINDS:
        kind = "ux"
    fid = "fb-" + uuid.uuid4().hex[:10]
    rec = {"id": fid, "kind": kind, "text": text, "page": _clean(page, 300), "view": _clean(view, 80),
           "target": _clean(target, 300), "viewport": _clean(viewport, 40), "project": _clean(project, 80),
           "status": "open", "created": B.now_iso(), "history": []}
    os.makedirs(FEEDBACK, exist_ok=True)
    B.write_json(_path(fid), rec)
    E.emit("feedback.created", {"id": fid, "kind": kind, "text": text[:200], "view": rec["view"]}, subject=fid)
    return rec


def items(status: str = "", limit: int = 300) -> list:
    if not os.path.isdir(FEEDBACK):
        return []
    out = []
    for f in os.listdir(FEEDBACK):
        if f.endswith(".json"):
            rec = B.read_json(os.path.join(FEEDBACK, f), None)
            if isinstance(rec, dict) and (not status or rec.get("status") == status):
                out.append(rec)
    # 解析后比较（ADR 0010）：created 可能是无偏移旧串，字符串序随时区漂移
    out.sort(key=lambda r: (B.rec_ts(r, "created"), str(r.get("created") or "")), reverse=True)
    return out[:limit]


def counts() -> dict:
    c = {s: 0 for s in STATUSES}
    for r in items(limit=100000):
        c[r.get("status", "open")] = c.get(r.get("status", "open"), 0) + 1
    c["actionable"] = sum(c[state] for state in ACTIONABLE)
    return c


def update(fid: str, status: str, note: str = "", expect: str = "", expect_command_id: str = "") -> dict:
    """改状态。status=converted 时自动生成一条给 Leader 的 custom 指令。
    expect 非空时，只有当前状态等于 expect 才改（在锁内判断）；否则原样返回、不写盘、不发事件。
    expect_command_id 同理：只有反馈正在等的就是这条指令才改——反馈可以重开再转任务，旧指令的迟到回执不能关掉新一轮（fable2 6b1b）。
    注意锁序：本函数持 feedback 锁调用 P.push_command；调用方不得持 COMMANDS 锁再调本函数（见 bus_projects.ack_command）。"""
    import bus_projects as P
    if not _ID.match(str(fid or "")):
        raise ValueError("反馈 id 不合法")
    if status not in STATUSES:
        raise ValueError("未知状态")
    os.makedirs(FEEDBACK, exist_ok=True)
    with file_mutex(os.path.join(FEEDBACK, ".feedback.mutex")):
        rec = B.read_json(_path(fid), None)
        if not isinstance(rec, dict):
            raise ValueError("没有找到该反馈")
        if (expect and rec.get("status") != expect) or (expect_command_id and rec.get("command_id") != expect_command_id):
            return rec
        if rec.get("status") == status and status in ("done", "dismissed"):
            return rec  # Repeated terminal confirmations do not duplicate evidence/history.
        cleaned_note = _clean(note, 1000).strip()
        if status in ("done", "dismissed") and len(cleaned_note) < 2:
            raise ValueError("请填写验证依据或不处理的明确理由；指令回执不等于问题解决")
        if status == "converted":
            if rec.get("status") not in ACTIONABLE:
                raise ValueError("只有未处理、待验证或暂缓的反馈可以继续派工")
            where = " / ".join(x for x in (rec.get("view"), rec.get("target")) if x)
            cmd = P.push_command("custom", {
                "text": (f"【体验反馈 {fid}·{rec['kind']}】{rec['text']}\n位置：{where or '未记录'}\n"
                         f"要求：先复现并判断是否值得改；值得改就按 docs/WORKFLOW.md 走分支施工，完成后回执本指令 ok=true、result 写明提交编号"
                         f"（反馈只进入待验证，不会自动解决）；本轮受阻或暂缓就回执 ok=false、result 写理由（只进入暂缓）。明确验证后再操作反馈结案并记录依据。{('补充：' + _clean(note, 1000)) if note else ''}"),
                "feedback_id": fid, "project": rec.get("project", "")})
            rec["command_id"] = (cmd or {}).get("id", "")
        rec["history"] = (rec.get("history") or [])[-30:] + [{"at": B.now_iso(), "from": rec.get("status"), "to": status,
                                                              "note": _clean(note, 1000)}]
        rec["status"] = status
        if status in ("done", "dismissed"):
            rec["resolution"] = {"status": status, "note": cleaned_note, "at": B.now_iso(),
                                 "command_id": rec.get("command_id", ""), "kind": "explicit-confirmation"}
        elif status in ("open", "converted"):
            rec.pop("resolution", None)
        B.write_json(_path(fid), rec)
    E.emit("feedback." + status, {"id": fid, "text": rec["text"][:200]}, subject=fid)
    return rec
