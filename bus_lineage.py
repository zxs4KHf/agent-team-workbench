"""决策谱系 / 人工交接 / 关联子单（任务 903f，ba28 P1）。

只做三件事，全部通过 bus_projects.upsert_decision 写决策记录，不碰 console/、inbox/、租约：
1. lineage：topic / root_task / parent_task / round / kind 五个可选字段。旧记录没有字段 → self-root、显示原标题，不猜不回填。
   与 implementation_task_id 无关：后者仍只指向真实 build 单。
2. handoff（等你一步）：必须同时有 user_step / reason / verify / evidence（Agent 已做完准备的证据），缺一不进入「等你一步」。
   builder 离线、审查 RETURN、无 builder 都不是人工项，默认继续留在「Agent 处理中」。
3. child（关联子单）：失败需用户决策时开一条 draft 决策挂在父单下；按原因签名去重、深度 / 数量限额；不动父单的 implementation / execution，不取消原施工。
"""
import datetime as dt
import hashlib
import re

import bus_projects as P

KINDS = {"discuss": "讨论", "independent": "对读", "redteam": "红队", "relay": "接力", "build": "施工",
         "review": "复审", "fix": "返修", "redecide": "再决策", "land": "落地"}
LINEAGE_FIELDS = ("topic", "root_task", "parent_task", "round", "kind")
TASK_ID_RE = re.compile(r"^(?:pending-)?[0-9A-Za-z][0-9A-Za-z_.-]{3,63}$")
MAX_CHILDREN = 3   # 每个父单最多 3 条自动 / 手动子单
MAX_DEPTH = 3      # 子单链最深 3 层（root → child → grandchild）
TOPIC_MAX = 24


def _now():
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()   # ADR 0010：带偏移


def _find(slug: str, task_id: str) -> dict:
    return next((x for x in P.decisions(slug) if x.get("task_id") == task_id), None)


# ---------- 1. lineage ----------
def normalize_lineage(fields: dict) -> dict:
    # 只接受五个字段；其它键一律丢弃（白名单入口），非法值抛 ValueError，不写半截
    out = {}
    if "topic" in fields:
        topic = re.sub(r"\s+", " ", str(fields.get("topic") or "")).strip()
        if len(topic) > TOPIC_MAX:
            raise ValueError(f"topic 不能超过 {TOPIC_MAX} 字")
        out["topic"] = topic
    for key in ("root_task", "parent_task"):
        if key in fields:
            v = str(fields.get(key) or "").strip()
            if v and not TASK_ID_RE.match(v):
                raise ValueError(f"{key} 不是合法 task_id")
            out[key] = v
    if "round" in fields:
        v = fields.get("round")
        if v in ("", None):
            out["round"] = 0
        else:
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError("round 必须是整数")
            if not 0 <= v <= 99:
                raise ValueError("round 只能在 0–99")
            out["round"] = v
    if "kind" in fields:
        v = str(fields.get("kind") or "").strip()
        if v and v not in KINDS:
            raise ValueError(f"kind 只能是 {', '.join(KINDS)}")
        out["kind"] = v
    return out


@P.serialized_project
def set_lineage(slug: str, task_id: str, by: str = "user", **fields) -> dict:
    rec = _find(slug, task_id)
    if not rec:
        raise ValueError(f"没有决策 {task_id}")
    clean = normalize_lineage(fields)
    if not clean:
        raise ValueError("没有可写的谱系字段")
    if clean.get("parent_task") == task_id:
        raise ValueError("parent_task 不能指向自己")
    if clean.get("parent_task"):
        parent = _find(slug, clean["parent_task"])
        if not parent:
            raise ValueError(f"父单 {clean['parent_task']} 不在本项目")
        if not clean.get("root_task"):
            clean["root_task"] = lineage_of(parent)["root_task"]
        if not clean.get("topic") and not rec.get("topic"):
            clean["topic"] = parent.get("topic") or ""
    clean["lineage_by"] = by
    return P.upsert_decision(slug, task_id, **clean)


def display_title(rec: dict) -> str:
    # 「主题 · R轮 · 动作」；没有 topic 的旧记录显示原标题，ID 只是次级信息
    topic = (rec.get("topic") or "").strip()
    if not topic:
        return rec.get("title") or rec.get("task_id") or "未命名决策"
    parts = [topic]
    rnd = rec.get("round") or 0
    if rnd:
        parts.append(f"R{rnd}")
    kind = rec.get("kind") or ""
    if kind in KINDS:
        parts.append(KINDS[kind])
    return " · ".join(parts)


def lineage_of(rec: dict) -> dict:
    # 派生视图（不落盘）：root_task 缺省为自己（self-root），has_lineage 表示是否显式写过字段
    tid = rec.get("task_id") or ""
    root = rec.get("root_task") or ""
    parent = rec.get("parent_task") or ""
    if not root:
        root = tid  # Missing explicit root is self-root; never infer historical lineage.
    return {"topic": rec.get("topic") or "", "root_task": root, "parent_task": parent, "round": int(rec.get("round") or 0),
            "kind": rec.get("kind") or "", "kind_label": KINDS.get(rec.get("kind") or "", ""), "display_title": display_title(rec),
            "has_lineage": any(rec.get(k) for k in LINEAGE_FIELDS), "self_root": root == tid, "children": list(rec.get("children") or [])}


def _root_via_parent(rec: dict) -> str:
    return rec.get("parent_task") or rec.get("task_id") or ""


def depth_of(slug: str, task_id: str) -> int:
    # 沿 parent_task 上溯的层数（root 为 0）；断链 / 环 最多走 MAX_DEPTH+1 步
    seen, depth, cur = set(), 0, _find(slug, task_id)
    while cur and cur.get("parent_task") and cur["task_id"] not in seen and depth <= MAX_DEPTH:
        seen.add(cur["task_id"])
        cur = _find(slug, cur["parent_task"])
        depth += 1
    return depth


# ---------- 2. handoff（等你一步） ----------
HANDOFF_TEXT = ("user_step", "reason", "verify")
HANDOFF_MAX = 500


def handoff_ready(rec: dict) -> bool:
    h = rec.get("handoff") or {}
    if h.get("state") != "ready":
        return False
    if not all(str(h.get(k) or "").strip() for k in HANDOFF_TEXT):
        return False
    return bool(h.get("prepared_by")) and isinstance(h.get("evidence"), list) and any(str(e).strip() for e in h["evidence"])


@P.serialized_project
def set_handoff(slug: str, task_id: str, user_step: str, reason: str, verify: str, evidence, by: str = "") -> dict:
    # 进入「等你一步」的唯一入口：四项齐全才写 state=ready；任何一项缺失直接拒绝，而不是降级写入
    rec = _find(slug, task_id)
    if not rec:
        raise ValueError(f"没有决策 {task_id}")
    if rec.get("status") != "adopted":
        raise ValueError("只有已采纳的决策才有人工交接项")
    if (rec.get("execution") or "pending") == "released":
        raise ValueError("该决策已落地，无需交接")
    vals = {}
    for key, val in (("user_step", user_step), ("reason", reason), ("verify", verify)):
        v = re.sub(r"\s+", " ", str(val or "")).strip()
        if not v:
            raise ValueError(f"handoff 缺少 {key}：必须写清唯一必要动作、原因与验证方法")
        if len(v) > HANDOFF_MAX:
            raise ValueError(f"handoff.{key} 不能超过 {HANDOFF_MAX} 字")
        vals[key] = v
    if isinstance(evidence, str):
        evidence = [evidence]
    ev = [str(e).strip() for e in (evidence or []) if str(e).strip()][:10]
    if not ev:
        raise ValueError("handoff 缺少 evidence：Agent 准备完成的证据（commit / 文件 / 测试输出）至少一条")
    by = str(by or "").strip()
    if not by:
        raise ValueError("handoff 缺少 prepared_by")
    h = {**vals, "evidence": ev, "prepared_by": by, "prepared_at": _now(), "state": "ready"}
    return P.upsert_decision(slug, task_id, handoff=h)


@P.serialized_project
def finish_handoff(slug: str, task_id: str, result: str, by: str = "user", note: str = "") -> dict:
    # done：用户做完那一步 → 落地（execution=released）；failed：退回 Agent 处理中，保留记录，不自动开单
    rec = _find(slug, task_id)
    if not rec or not (rec.get("handoff") or {}).get("state") == "ready":
        raise ValueError("该决策没有待处理的人工交接项")
    if result not in ("done", "failed"):
        raise ValueError("result 只能是 done / failed")
    h = {**rec["handoff"], "state": result, "finished_by": by, "finished_at": _now(), "note": str(note or "")[:500]}
    fields = {"handoff": h}
    if result == "done":
        fields.update(execution="released", execution_by=by, execution_at=_now())
    return P.upsert_decision(slug, task_id, **fields)


# ---------- 分页归类 ----------
def landing_bucket(rec: dict) -> str:
    # agent：Agent 处理中（默认，含 builder 离线 / 审查 RETURN / 排队）；user：等你一步；decide：等待决策；done：已完成；'' 不是采纳项
    if rec.get("status") != "adopted":
        return ""
    if rec.get("execution") in ("released", "n-a") or (rec.get("implementation") or {}).get("state") == "merged":
        return "done"
    if handoff_ready(rec):
        return "user"
    if rec.get("needs_decision"):
        return "decide"
    return "agent"


# ---------- 3. 关联子单 ----------
def reason_signature(reason: str) -> str:
    norm = re.sub(r"\s+", " ", str(reason or "").lower()).strip()
    norm = re.sub(r"\d{8}-\d{6}-[0-9a-f]{4}", "<id>", norm)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


@P.serialized_project
def spawn_child(slug: str, parent_task: str, reason: str, kind: str = "redecide", by: str = "system", title: str = "") -> dict:
    # 失败且需要用户决策时开子单：draft 决策，不派发、不取消父单施工、不改父单 implementation。
    # 幂等：同父单 + 同原因签名且子单仍开放 → 返回既有；限额：每父单 MAX_CHILDREN，链深 MAX_DEPTH
    parent = _find(slug, parent_task)
    if not parent:
        raise ValueError(f"没有决策 {parent_task}")
    if parent.get("status") in ("cancelled", "superseded", "rejected"):
        raise ValueError("已撤销 / 已替代 / 已驳回的事项不再开子单")
    if kind not in ("redecide", "fix"):
        raise ValueError("子单 kind 只能是 redecide / fix")
    reason = re.sub(r"\s+", " ", str(reason or "")).strip()
    if len(reason) < 4:
        raise ValueError("reason 太短，写清失败原因与需要用户决定的事")
    sig = reason_signature(reason)
    items = P.decisions(slug)
    kids = [x for x in items if x.get("parent_task") == parent_task]
    same = next((x for x in kids if x.get("reason_sig") == sig and x.get("status") not in ("cancelled", "superseded", "rejected", "archived", "merged", "adopted")), None)
    if same:
        return {"ok": True, "dedup": True, "task_id": same["task_id"], "note": "同一原因的子单已存在"}
    if len(kids) >= MAX_CHILDREN:
        raise ValueError(f"父单已有 {len(kids)} 条子单，达到上限 {MAX_CHILDREN}；请先处理既有子单")
    if depth_of(slug, parent_task) + 1 > MAX_DEPTH:
        raise ValueError(f"子单链已达最深 {MAX_DEPTH} 层，不再自动加深")
    plin = lineage_of(parent)
    child_id = f"pending-child-{parent_task[-4:]}-{sig[:6]}"
    if _find(slug, child_id):
        child_id = f"pending-child-{parent_task[-4:]}-{sig[:6]}-{len(kids) + 1}"
    child = P.upsert_decision(slug, child_id, status="draft", title=title or f"{display_title(parent)} 失败 · 需决策",
                              summary=reason[:2000], mode=kind, topic=plin["topic"], root_task=plin["root_task"], parent_task=parent_task,
                              round=(plin["round"] or 1) + 1, kind=kind, reason_sig=sig, created_by=by)
    children = list(parent.get("children") or [])
    children.append(child_id)
    fields = {"children": children}
    if kind == "redecide":
        fields["needs_decision"] = True
    P.upsert_decision(slug, parent_task, **fields)
    return {"ok": True, "dedup": False, "task_id": child_id, "round": child["round"], "root_task": child["root_task"]}


@P.serialized_project
def resolve_decision(slug: str, parent_task: str, by: str = "user") -> dict:
    # 用户处理完子单（采纳 / 驳回 / 取消）后清掉父单的 needs_decision，使其回到「Agent 处理中」
    parent = _find(slug, parent_task)
    if not parent:
        raise ValueError(f"没有决策 {parent_task}")
    open_kids = [x for x in P.decisions(slug) if x.get("parent_task") == parent_task and x.get("kind") == "redecide"
                 and x.get("status") in ("draft", "dispatched", "collecting", "ready_for_merge")]
    if open_kids:
        return {"ok": False, "open": [x["task_id"] for x in open_kids], "note": "仍有未定夺的子单"}
    P.upsert_decision(slug, parent_task, needs_decision=False, decision_resolved_by=by)
    return {"ok": True, "open": []}


def threads(items: list) -> list:
    # 按 root_task 分组，组内按 round / created 排序；旧记录各自成组（self-root），不做猜测合并
    import bus_core as B  # 延迟导入：bus_projects 反向依赖本模块，模块级导入会成环
    groups = {}
    for rec in items:
        lin = lineage_of(rec)
        groups.setdefault(lin["root_task"], []).append(rec)
    out = []
    for root, recs in groups.items():
        # 排序解析后比较（ADR 0010）：无偏移旧串与带偏移新串混存时字符串序会挑错「最新一轮 / latest」
        recs.sort(key=lambda r: (int(r.get("round") or 0), B.rec_ts(r, "created"), str(r.get("created") or "")))
        latest = max(recs, key=lambda r: (B.rec_ts(r, "updated", "created"), str(r.get("updated") or r.get("created") or "")))
        out.append({"root_task": root, "topic": next((r.get("topic") for r in recs if r.get("topic")), ""), "count": len(recs),
                    "task_ids": [r["task_id"] for r in recs], "latest": latest["task_id"], "latest_status": latest.get("status", "")})
    out.sort(key=lambda g: max((B.rec_ts(r, "updated", "created"), str(r.get("updated") or r.get("created") or ""))
                               for r in groups[g["root_task"]]), reverse=True)
    return out


DERIVED_KEYS = ("lineage", "bucket", "progress", "implementation")  # 只给浏览器投影的派生键；root_task / parent_task / round / topic / kind / handoff / children 是真实持久字段，不在此列


def decorate(rec: dict) -> dict:
    # _full_state 用：只加派生键 lineage / bucket，不改既有字段；字段缺省时值稳定（'' / 0 / []），前端 stateKey 不抖
    rec["lineage"] = lineage_of(rec)
    rec["bucket"] = landing_bucket(rec)
    return rec


def persistable_all(items: list) -> list:
    # 落盘前的副本：逐条剥掉 decorate 的派生键，磁盘上历史泄漏的派生键顺带规范化；传入的列表与元素本身不受影响
    return [{k: v for k, v in rec.items() if k not in DERIVED_KEYS} for rec in items]
