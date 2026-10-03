"""Agent 分组由工作台自己管（2026-10-02）。

以前「模型组」名单直接从 Arena 控制面（9091）拉：组名、「最强 / 常用」标签只能去 Arena 改，Arena 没有的模型
（例如 Sonnet）根本归不了组，进不了 Reviewer 组，也不会被自动分配。用户要求 Arena 以后做成可选插件，
所以分组必须是工作台自己的数据：
  · 存在 console/agent_groups.json：[{id, name, label, match, order}]；
  · 文件不存在时用内置的默认分组（按常见模型家族；id 与仓库模板 tools/roles.json 一致，Reviewer / 施工组与顶替顺序开箱可用）；
    不再向 Arena 要组表——2026-10-02 起 Arena 是可选插件，而且在 Windows 上连一个没人监听的端口要等约 2 秒，
    每次读分组都去连，新装机器（没有分组文件、没有 Arena）刷新一次状态要卡 10 秒；
  · 在 Agent 页可新建、改名、改标签、改「包含哪些模型」、合并、删除、排序，以及勾选「能审查 / 能施工」
    （直接写 console/roles.json 的 reviewer_groups / builder_groups）。
bus_core.model_groups() 读这里。
"""
import json
import os
import re
import time

import bus_core as B

TIER_LABEL = {"strong": "最强", "daily": "常用"}


def _path() -> str:
    return os.path.join(B.ROOT, "console", "agent_groups.json")


def _norm_id(s: str) -> str:
    return re.sub(r"[^a-z0-9.\-]", "-", str(s or "").strip().lower()).strip("-")


def _norm_match(m) -> list:
    items = m if isinstance(m, list) else re.split(r"[\s,，、]+", str(m or ""))
    out = []
    for x in items:
        x = str(x or "").strip().lower().replace("_", "-").replace(" ", "-")
        if x and x not in out:
            out.append(x)
    return out


# 内置默认分组：按模型家族前缀归组。用户在 Agent 页第一次编辑分组时才落盘到 console/agent_groups.json。
DEFAULT_GROUPS = [
    {"id": "claude-fable-5", "name": "Claude Fable 系列", "match": ["claude-fable"]},
    {"id": "claude-opus-5", "name": "Claude Opus 系列", "match": ["claude-opus"]},
    {"id": "claude-sonnet", "name": "Claude Sonnet 系列", "match": ["claude-sonnet"]},
    {"id": "gpt-6-astra", "name": "GPT-6 系列", "match": ["gpt-6"]},
    {"id": "gpt-5.6", "name": "GPT-5 系列", "match": ["gpt-5"]},
    {"id": "gemini-4-pro", "name": "Gemini 系列", "match": ["gemini"]},
    {"id": "grok-4.6", "name": "Grok 系列", "match": ["grok"]},
    {"id": "kimi-k3", "name": "Kimi 系列", "match": ["kimi"]},
    {"id": "glm-5.3", "name": "GLM 系列", "match": ["glm"]},
    {"id": "deepseek-flash", "name": "DeepSeek 系列", "match": ["deepseek"]},
    {"id": "qwen3.8-max", "name": "Qwen 系列", "match": ["qwen"]},
]


def _defaults() -> list:
    return [{**g, "label": "", "match": list(g["match"]), "order": i + 1} for i, g in enumerate(DEFAULT_GROUPS)]


_CACHE = {"mtime": None, "groups": []}


def _order(g) -> int:
    o = g.get("order")
    return int(o) if isinstance(o, (int, float)) else 999   # 0 是合法的「排第一」，不能用 `or 999`


def load() -> list:
    """读分组。文件还没有时用内置默认分组，**只放内存**（读路径不写盘，见 test_implementation_state_5054
    「读取路径不写任何文件」）；第一次在 Agent 页编辑分组时才落盘。不发任何网络请求。"""
    p = _path()
    if not os.path.exists(p):
        if _CACHE["mtime"] != "seed":
            _CACHE["groups"], _CACHE["mtime"] = _defaults(), "seed"
        return [dict(g, match=list(g["match"])) for g in _CACHE["groups"]]
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return []
    if _CACHE["mtime"] != mt:
        data = B.read_json(p, {}) or {}
        _CACHE["groups"] = sorted([g for g in data.get("groups") or [] if isinstance(g, dict) and g.get("id")],
                                  key=lambda g: (_order(g), g["id"]))
        _CACHE["mtime"] = mt
    return [dict(g) for g in _CACHE["groups"]]


def _write(groups: list) -> None:
    data = B.read_json(_path(), {}) or {"seeded_at": B.now_iso()}
    data["version"] = 1
    data["groups"] = groups
    B.write_json(_path(), data)
    _CACHE["mtime"] = None   # 同一秒内连写两次时文件时间可能不变：写完直接作废缓存


# ---- 能审查 / 能施工：写 console/roles.json（实例层，不入库）----
def _roles_path() -> str:
    return os.path.join(B.ROOT, "console", "roles.json")


def roles_flags() -> dict:
    a = (B.read_roles().get("assignments") or {})
    return {"reviewer": list(a.get("reviewer_groups") or []), "builder": list(a.get("builder_groups") or [])}


def _set_roles(gid: str, reviewer=None, builder=None, rename_from: str = "") -> None:
    roles = B.read_roles()          # 实例不存在时读的是模板；写回一律写实例，不改入库模板
    a = roles.setdefault("assignments", {})
    for key, want in (("reviewer_groups", reviewer), ("builder_groups", builder)):
        lst = [x for x in (a.get(key) or [])]
        if rename_from:
            lst = [gid if x == rename_from else x for x in lst]
        if want is True and gid not in lst:
            lst.append(gid)
        if want is False:
            lst = [x for x in lst if x != gid]
        a[key] = list(dict.fromkeys(lst))
    B.write_json(_roles_path(), roles)


def view() -> list:
    flags = roles_flags()
    return [{**g, "reviewer": g["id"] in flags["reviewer"], "builder": g["id"] in flags["builder"]} for g in load()]


def save(data: dict) -> dict:
    groups = load()
    gid = _norm_id(data.get("id") or "")
    name = str(data.get("name") or "").strip()[:40]
    if not gid:
        # 新建：id 由名称推出；纯中文名称清洗后是空的（2026-10-02 用户报「填了名称还说需要名称」），
        # 退到第一条模型前缀，再退到 group-N。与已有分组撞 id 时加序号，绝不悄悄改掉别的组
        if not name:
            raise ValueError("分组需要名称")
        taken = {g["id"] for g in groups}
        base = _norm_id(name) or next(iter(_norm_match(data.get("match"))), "") or "group"
        gid, n = base, 2
        while gid in taken:
            gid, n = f"{base}-{n}", n + 1
    cur = next((g for g in groups if g["id"] == gid), None)
    if cur is None:
        if not name:
            raise ValueError("新分组需要名称")
        cur = {"id": gid, "name": name, "label": "", "match": [], "order": max([_order(g) for g in groups if _order(g) < 999] + [0]) + 1}
        groups.append(cur)
    if name:
        cur["name"] = name
    if "label" in data:
        cur["label"] = str(data.get("label") or "").strip()[:12]
    if "match" in data:
        cur["match"] = _norm_match(data.get("match"))
    _write(groups)
    if "reviewer" in data or "builder" in data:
        _set_roles(gid, reviewer=data.get("reviewer") if "reviewer" in data else None,
                   builder=data.get("builder") if "builder" in data else None)
    return next(g for g in view() if g["id"] == gid)


def delete(gid: str) -> dict:
    groups = load()
    if not any(g["id"] == gid for g in groups):
        raise ValueError(f"没有分组 {gid}")
    _write([g for g in groups if g["id"] != gid])
    _set_roles(gid, reviewer=False, builder=False)
    return {"ok": True, "id": gid}


def merge(src: str, dst: str) -> dict:
    """把 src 并进 dst：模型匹配并过去、手动归到 src 的 Agent 改归 dst、Reviewer / 施工名单里的 src 换成 dst，再删 src。"""
    if src == dst:
        raise ValueError("不能并进自己")
    groups = load()
    a = next((g for g in groups if g["id"] == src), None)
    b = next((g for g in groups if g["id"] == dst), None)
    if not a or not b:
        raise ValueError("要合并的两个分组都必须存在")
    b["match"] = _norm_match(list(b.get("match") or []) + list(a.get("match") or []))
    _write([g for g in groups if g["id"] != src])
    moved = []
    for p in B.list_peers():
        if (p.get("group_hint") or {}).get("group") == src or str(p.get("group") or "") == src:
            try:
                B.manual_group(p["peer"], dst)
                moved.append(p["peer"])
            except ValueError:
                pass
    _set_roles(dst, rename_from=src)
    return {"ok": True, "into": dst, "moved_agents": moved}


def reorder(ids: list) -> dict:
    groups = load()
    pos = {gid: i for i, gid in enumerate(ids or [])}
    for g in groups:
        g["order"] = pos.get(g["id"], 1000 + _order(g))
    _write(sorted(groups, key=lambda g: g["order"]))
    return {"ok": True}
