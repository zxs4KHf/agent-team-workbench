import hashlib
import json
import os
import re
from urllib.parse import quote

import bus_core as B
import bus_projects as P
from bus_locks import file_mutex


SOURCE_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,40}\Z")
REQUEST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")


class JobError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _ids(source, request_id):
    if not isinstance(source, str) or not SOURCE_RE.fullmatch(source):
        raise JobError("source 格式不合法")
    if not isinstance(request_id, str) or not REQUEST_RE.fullmatch(request_id):
        raise JobError("request_id 格式不合法")


def _record_path(source, request_id):
    _ids(source, request_id)
    return os.path.join(B.ROOT, ".runtime", "plugin-jobs", source, request_id + ".json")


def _marker(source, request_id):
    return f"[plugin-job source={source} request_id={request_id}]\n"


def _public(record, deduplicated=False):
    out = {k: record.get(k) for k in ("source", "request_id", "state", "task_id", "peer")}
    if record.get("project"):
        out.update({k: record.get(k) for k in ("project", "team", "mode", "peers")})
        tid = record.get("task_id")
        out["task_url"] = f"/?p={quote(record['project'], safe='')}&view=running&t={quote(tid, safe='')}" if tid else None
    if record.get("error"):
        out["error"] = record["error"]
    if deduplicated:
        out["deduplicated"] = True
    return out


def agents():
    return {"agents": [{k: p.get(k) for k in ("peer", "label", "model", "online", "listening", "activity", "age_sec")}
                       for p in B.list_peers() if p.get("peer") != "controller"]}


def _find_task(source, request_id):
    prefix = _marker(source, request_id)
    matches = []
    roots = [B.OUTBOX, B.LOG]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for folder, _dirs, files in os.walk(root):
            for name in files:
                if not name.endswith(".meta.json"):
                    continue
                meta = B.read_json(os.path.join(folder, name), None)
                if isinstance(meta, dict) and meta.get("external_owner") == source and str(meta.get("question") or "").startswith(prefix):
                    if isinstance(meta.get("id"), str):
                        matches.append(meta["id"])
    return sorted(set(matches))


def _recover(record, path):
    if record.get("state") == "cancelled":
        return record
    if record.get("state") == "submitted" and not record.get("project"):
        return record
    if not record.get("task_id"):
        matches = _find_task(record["source"], record["request_id"])
        if len(matches) == 1:
            record["task_id"] = matches[0]
        elif len(matches) > 1:
            record.update(state="unknown", error="找到多个匹配任务，需要人工核实")
            B.write_json(path, record)
            return record
        elif record.get("state") == "dispatching":
            record.update(state="unknown", error="派发结果未确认，需要人工核实")
            B.write_json(path, record)
            return record
    if record.get("task_id"):
        try:
            if record.get("project"):
                _associate(record)
        except Exception:
            record.update(state="unknown", error="任务已派发，项目关联待恢复")
        else:
            record.update(state="submitted")
            record.pop("error", None)
        B.write_json(path, record)
    return record


def lookup(source, request_id):
    path = _record_path(source, request_id)
    with file_mutex(path + ".mutex", timeout=60):
        record = B.read_json(path, None)
        if not isinstance(record, dict):
            return {"source": source, "request_id": request_id, "state": "missing", "task_id": None, "peer": None}
        return _public(_recover(record, path))


def _fingerprint(peer, question, ttl_min):
    body = json.dumps([peer, question, ttl_min], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


MODE_LABELS = {"race": "抢单", "discuss": "讨论", "independent": "独立作答", "redteam": "红队", "relay": "接力"}


def _plugin(source, plugins):
    item = next((p for p in plugins if p.get("id") == source and p.get("enabled") and not p.get("errors")), None)
    if item is None:
        raise JobError("source 插件不存在或未启用", 403)
    return item


def _dispatchable(project, all_agents):
    meta = project.get("agent_meta") or {}
    return [a["peer"] for a in all_agents if a.get("peer") != "controller" and a.get("online")
            and a.get("role") != "relay" and a.get("status") != "relay"
            and not B.get_profile(a["peer"]).get("paused")
            and not (meta.get(a["peer"]) or {}).get("paused_in_project")]


def _teams(project, all_agents):
    rows = [{"name": P.DYNAMIC_TEAM, "kind": "online", "peers": _dispatchable(project, all_agents),
             "note": "派发时取当前可派发的在线 Agent"},
            {"name": P.PROJECT_TEAM, "kind": "project", "peers": list(dict.fromkeys(project.get("peers") or [])),
             "note": "项目静态成员，离线成员留在收件箱等待领取"}]
    known = {r["name"] for r in rows}
    for team in project.get("teams") or []:
        if not isinstance(team, dict):
            continue
        name = team.get("name")
        if not isinstance(name, str) or not name.strip() or name in known:
            continue
        known.add(name)
        rows.append({"name": name, "kind": "custom", "peers": list(dict.fromkeys(team.get("peers") or [])),
                     "note": str(team.get("note") or "自定义固定团队")})
    return rows


def options(source, plugins):
    _ids(source, "options")
    plugin = _plugin(source, plugins)
    all_agents = B.list_peers()
    projects = [{"slug": p["slug"], "name": p.get("name") or p["slug"],
                 "default_team": p.get("default_team") or "", "teams": _teams(p, all_agents)}
                for p in P.list_projects()]
    cfg = plugin.get("config") or {}
    return {"source": source, "default_project": cfg.get("project") if isinstance(cfg.get("project"), str) else "",
            "default_mode": "race", "projects": projects,
            "modes": [{"value": mode, "label": label,
                       "description": ("成员抢单，首位领取者执行。" if mode == "race" else "")
                       + B.MODES["independent" if mode == "race" else mode]}
                      for mode, label in MODE_LABELS.items()], "agents": agents()["agents"]}


def _associate(record):
    task_id = record["task_id"]
    current = next((d for d in P.decisions(record["project"]) if d.get("task_id") == task_id), None)
    wanted = {"title": record["title"], "peers": record["peers"], "mode": record["mode"],
              "team": record["team"], "external_owner": record["source"],
              "external_request_id": record["request_id"]}
    if current is None:
        P.upsert_decision(record["project"], task_id, status="dispatched", **wanted)
    else:
        missing = {k: v for k, v in wanted.items() if not current.get(k)}
        if missing:
            P.upsert_decision(record["project"], task_id, **missing)


def _team_fingerprint(payload):
    fields = ("project", "team", "mode", "peers", "question", "title", "ttl_min")
    body = json.dumps({k: payload[k] for k in fields if k in payload}, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _submit_team(payload, plugins):
    source, request_id = payload.get("source"), payload.get("request_id")
    path = _record_path(source, request_id)
    _plugin(source, plugins)
    project = payload.get("project")
    if not isinstance(project, str) or not project:
        raise JobError("project 必须指定已有项目")
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip() or len(question) > 20000:
        raise JobError("question 必须为非空文本，最多 20000 字符")
    title = payload.get("title", "")
    if not isinstance(title, str) or len(title) > 180:
        raise JobError("title 最多 180 字符")
    mode = payload.get("mode", "race")
    if not isinstance(mode, str) or mode not in MODE_LABELS:
        raise JobError("mode 不支持此任务类型")
    ttl_min = payload.get("ttl_min", 30)
    if isinstance(ttl_min, bool) or not isinstance(ttl_min, int) or not 1 <= ttl_min <= 120:
        raise JobError("ttl_min 必须为 1 至 120 的整数")
    requested = payload.get("peers")
    if requested is not None and (not isinstance(requested, list) or not requested or
                                   any(not isinstance(p, str) for p in requested) or len(set(requested)) != len(requested)):
        raise JobError("peers 必须为无重复的非空成员数组")
    fingerprint = _team_fingerprint(payload)
    with file_mutex(path + ".mutex", timeout=60):
        record = B.read_json(path, None)
        if record is not None:
            if not isinstance(record, dict) or record.get("fingerprint") != fingerprint:
                raise JobError("request_id 已用于不同任务内容", 409)
            return _public(_recover(record, path), deduplicated=True)
        try:
            p = P.load_project(project)
        except ValueError as exc:
            raise JobError(str(exc)) from exc
        team = payload.get("team", p.get("default_team") or P.DYNAMIC_TEAM)
        if team == "":
            team = P.DYNAMIC_TEAM
        if not isinstance(team, str):
            raise JobError("team 必须为项目中的团队名称")
        chosen = next((t for t in _teams(p, B.list_peers()) if t["name"] == team), None)
        if chosen is None:
            raise JobError("项目中没有该团队")
        members = chosen["peers"]
        for member in members:
            try:
                B.valid_name(member)
            except ValueError as exc:
                raise JobError(str(exc)) from exc
        if any(member in ("controller", "auto") for member in members):
            raise JobError("团队包含不可派发的成员")
        if requested is not None:
            if any(member not in members for member in requested):
                raise JobError("peers 只能选择团队内成员")
            members = list(requested)
        if not members:
            raise JobError("所选团队没有可派发成员")
        task_id = B.new_task_id()
        record = {"source": source, "request_id": request_id, "project": project, "team": team,
                  "mode": mode, "peers": members, "peer": None, "title": title.strip() or question.strip()[:180],
                  "fingerprint": fingerprint, "state": "dispatching", "task_id": None}
        B.write_json(path, record)
        meta_path = os.path.join(B.OUTBOX, task_id + ".meta.json")
        try:
            with file_mutex(meta_path + ".race.mutex", timeout=60):
                result = B.dispatch("plugin:" + source, members, "independent" if mode == "race" else mode,
                                    _marker(source, request_id) + question, [], ttl_min=ttl_min,
                                    min_answers=len(members), race=mode == "race", substitute=False,
                                    external_owner=source, queue_until_claim=True, project=project, team=team,
                                    plugin_request_id=request_id, task_id_override=task_id)
        except Exception:
            record.update(state="unknown", error="派发结果未确认，需要人工核实")
            B.write_json(path, record)
            return _public(record)
        record["task_id"] = result["task_id"]
        return _public(_recover(record, path))


def cancel(payload, plugins):
    if not isinstance(payload, dict):
        raise JobError("请求体必须是对象")
    source, request_id = payload.get("source"), payload.get("request_id")
    path = _record_path(source, request_id)
    _plugin(source, plugins)
    with file_mutex(path + ".mutex", timeout=60):
        record = B.read_json(path, None)
        if not isinstance(record, dict):
            raise JobError("没有该任务", 404)
        if not record.get("project"):
            raise JobError("旧单人任务不支持取消", 409)
        if record.get("state") == "cancelled":
            return _public(record, deduplicated=True)
        record = _recover(record, path)
        task_id = record.get("task_id")
        if not task_id:
            raise JobError("派发结果未确认，暂不能取消", 409)
        meta_path = os.path.join(B.OUTBOX, task_id + ".meta.json")
        with file_mutex(meta_path + ".race.mutex", timeout=60):
            meta = B.read_json(meta_path, None)
            if not isinstance(meta, dict) or not meta.get("queue_until_claim"):
                raise JobError("任务状态不可取消", 409)
            if not meta.get("cancelled"):
                claimed = bool(meta.get("race_winner"))
                for member in record["peers"]:
                    inbox = os.path.join(B.INBOX, member)
                    claimed |= os.path.exists(os.path.join(inbox, task_id + ".taken"))
                    claimed |= os.path.exists(os.path.join(B.OUTBOX, f"{task_id}.{member}.md"))
                if claimed:
                    raise JobError("任务已被领取或回复，不能取消", 409)
                meta.update(cancelled=True, sealed=True, cancelled_at=B.now_iso())
                B.write_json(meta_path, meta)
                for member in record["peers"]:
                    src = os.path.join(B.INBOX, member, task_id + ".json")
                    if os.path.isfile(src):
                        os.replace(src, src + ".cancelled")
        current = next((d for d in P.decisions(record["project"]) if d.get("task_id") == task_id), None)
        if current is None or not P.is_terminal(current.get("status")):
            P.upsert_decision(record["project"], task_id, status="cancelled")
        record.update(state="cancelled")
        record.pop("error", None)
        B.write_json(path, record)
        return _public(record)


def submit(payload, plugins):
    if not isinstance(payload, dict):
        raise JobError("请求体必须是对象")
    if "project" in payload:
        return _submit_team(payload, plugins)
    source, request_id = payload.get("source"), payload.get("request_id")
    path = _record_path(source, request_id)
    if not any(p.get("id") == source and p.get("enabled") and not p.get("errors") for p in plugins):
        raise JobError("source 插件不存在或未启用", 403)
    peer, question = payload.get("peer"), payload.get("question")
    if not isinstance(peer, str) or peer in ("controller", "auto"):
        raise JobError("peer 必须明确指定一个普通 Agent")
    try:
        B.valid_name(peer)
    except ValueError as exc:
        raise JobError(str(exc)) from exc
    if not isinstance(question, str) or not question.strip() or len(question) > 20000:
        raise JobError("question 必须为非空文本，最多 20000 字符")
    ttl_min = payload.get("ttl_min", 30)
    if isinstance(ttl_min, bool) or not isinstance(ttl_min, int) or not 1 <= ttl_min <= 120:
        raise JobError("ttl_min 必须为 1 至 120 的整数")
    fingerprint = _fingerprint(peer, question, ttl_min)
    with file_mutex(path + ".mutex", timeout=60):
        record = B.read_json(path, None)
        if record is not None:
            if not isinstance(record, dict) or record.get("fingerprint") != fingerprint:
                raise JobError("request_id 已用于不同任务内容", 409)
            return _public(_recover(record, path), deduplicated=True)
        target = next((p for p in B.list_peers() if p.get("peer") == peer), None)
        if not target or not target.get("listening"):
            raise JobError("被点名的 Agent 当前未在监听", 409)
        record = {"source": source, "request_id": request_id, "peer": peer,
                  "fingerprint": fingerprint, "state": "dispatching", "task_id": None}
        B.write_json(path, record)
        try:
            result = B.dispatch("plugin:" + source, [peer], "independent", _marker(source, request_id) + question,
                                [], ttl_min=ttl_min, min_answers=1, substitute=False, external_owner=source)
        except ValueError as exc:
            if "未在监听" in str(exc):
                os.remove(path)
                raise JobError(str(exc), 409) from exc
            record.update(state="unknown", error="派发结果未确认，需要人工核实")
            B.write_json(path, record)
            return _public(record)
        except Exception:
            record.update(state="unknown", error="派发结果未确认，需要人工核实")
            B.write_json(path, record)
            return _public(record)
        record.update(state="submitted", task_id=result["task_id"])
        B.write_json(path, record)
        return _public(record)
