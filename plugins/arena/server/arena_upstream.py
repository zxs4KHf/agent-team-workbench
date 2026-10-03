import json
import urllib.error
import urllib.request

import arena_store as ST

# 对 ArenaX 9090 的调用全部收口在这里；以后换成自研 driver 只改这个文件


class UpstreamError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def base() -> str:
    return ST.load("settings").get("upstream", "http://127.0.0.1:9090").rstrip("/")


def call(method: str, path: str, body=None, timeout=30, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base() + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(raw)
            msg = detail.get("detail") or detail.get("error") or raw
        except ValueError:
            msg = raw
        raise UpstreamError(f"上游 {e.code}: {str(msg)[:300]}", e.code)
    except (urllib.error.URLError, OSError) as e:
        raise UpstreamError(f"上游不可达: {e}", 503)
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {"raw": raw}


def open_stream(method: str, path: str, body: bytes, headers: dict, timeout=600):
    req = urllib.request.Request(base() + path, data=body, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return e
    except (urllib.error.URLError, OSError) as e:
        raise UpstreamError(f"上游不可达: {e}", 503)


def health() -> dict:
    try:
        call("GET", "/health", timeout=5)
        return {"ok": True, "url": base()}
    except UpstreamError as e:
        return {"ok": False, "url": base(), "error": str(e)}


def list_accounts() -> dict:
    return call("GET", "/v1/accounts/list")


def list_sessions(limit=1000) -> dict:
    return call("GET", f"/v1/agent/sessions?limit={int(limit)}", timeout=60)


def relay_config() -> dict:
    return call("GET", "/v1/relay/config")


def relay_stats() -> dict:
    return call("GET", "/v1/relay/stats")


def check_status(email: str) -> dict:
    return call("POST", "/v1/accounts/check_status", {"email": email}, timeout=120)


def check_all() -> dict:
    return call("POST", "/v1/accounts/check_all", {"probe_unresolved": False}, timeout=600)


def batch_add(raw_text: str) -> dict:
    return call("POST", "/v1/accounts/batch_add", {"raw_text": raw_text}, timeout=600)


def delete_account(email: str) -> dict:
    return call("POST", "/v1/accounts/delete", {"email": email})


def reset_cooldown(email: str) -> dict:
    return call("POST", "/v1/accounts/reset_cooldown", {"email": email})


def switch_account(email: str) -> dict:
    return call("POST", "/v1/accounts/switch", {"email": email}, timeout=120)


def sync_routes(email: str) -> dict:
    return call("POST", "/v1/accounts/sync_routes", {"email": email}, timeout=120)


def batch_create(emails, count, auto_detect, prompt) -> dict:
    return call("POST", "/v1/sessions/batch_create", {"emails": emails, "count_per_account": count, "auto_detect": auto_detect, "probe_prompt": prompt}, timeout=900)


def detect_one(session_id: str) -> dict:
    return call("POST", "/v1/sessions/detect_one", {"session_id": session_id}, timeout=300)


def delete_session(session_id: str) -> dict:
    return call("POST", "/v1/agent/sessions/delete", {"session_id": session_id}, timeout=60)


def set_route(model_id: str, session_id: str) -> dict:
    # ArenaX 只认会话池：先把会话的模型改成目标名（进池），再写显式路由
    call("POST", "/v1/agent/sessions/update-model", {"session_id": session_id, "model": model_id}, timeout=120)
    return call("POST", "/v1/relay/route/set", {"model_id": model_id, "session_id": session_id}, timeout=120)


def delete_route(model_id: str) -> dict:
    return call("POST", "/v1/relay/route/delete", {"model_id": model_id})


def auto_register(count: int, password: str) -> dict:
    if count > 1:
        return call("POST", "/v1/accounts/batch_auto_register", {"count": count, "password": password}, timeout=1800)
    return call("POST", "/v1/accounts/auto_register", {"password": password}, timeout=900)


def chat_probe(session_model: str, prompt: str, timeout=120) -> dict:
    return call("POST", "/v1/chat/completions", {"model": session_model, "messages": [{"role": "user", "content": prompt}], "stream": False}, timeout=timeout)
