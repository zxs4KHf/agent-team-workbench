import json
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
import uuid

import arena_secrets as SEC
import arena_store as ST
import os
import arena_upstream as UP

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
# 工作台根目录：插件 main.py 通过 WORKBENCH_ROOT 传入；没有时按 plugins/arena/server 往上三级推
WB_ROOT_DEFAULT = os.environ.get("WORKBENCH_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(ROOT_DIR)))

# 控制面业务逻辑：账号池、会话池、健康判定、路由、API Key、探针去重、榜单、后台作业

SECRET_FIELDS = ("password", "auth_token", "cookies")
LEADERBOARD_BOARDS = ("text", "code", "vision", "search")
RESET_RE = re.compile(r"(\d{1,2}):(\d{2})")

# Cloudflare 按 User-Agent 拦截 urllib 默认签名（Python-urllib/3.x）并返回 403，
# 与前端 arena.js 走浏览器 fetch 的行为对齐，Python 侧所有出站请求都带浏览器头。
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/event-stream, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _urlopen(url: str, timeout: int = 30, headers: dict = None):
    req = urllib.request.Request(url, headers={**BROWSER_HEADERS, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


class JoinError(Exception):
    """接入失败并带可分辨的原因码，_job 会把 reason_code 落到 job 上供前端上色。"""

    def __init__(self, reason_code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.reason_code = reason_code
        self.retryable = retryable


# ---------- peer 名分配 ----------
JOIN_RESERVED = {}          # name -> 预留到期时间戳，防止并发 join 抢到同一个名字
JOIN_RESERVE_TTL = 600
JOIN_LOCK = threading.Lock()


def _wb_root() -> str:
    return ST.load("settings").get("workbench_root") or os.path.join(WB_ROOT_DEFAULT, "")


def _taken_peer_names() -> set:
    # 取名禁区 = peers/ 下全部文件名（含离线与 renamed 墓碑）∪ sessions.json 里已登记的 peer ∪ 未过期的预留名
    taken = set()
    peers_dir = os.path.join(_wb_root(), "peers")
    if os.path.isdir(peers_dir):
        taken |= {f[:-5] for f in os.listdir(peers_dir) if f.endswith(".json")}
    for s in ST.load("sessions")["sessions"]:
        if s.get("peer"):
            taken.add(str(s["peer"]))
    now = time.time()
    with JOIN_LOCK:
        for name, until in list(JOIN_RESERVED.items()):
            if until < now:
                JOIN_RESERVED.pop(name, None)
            else:
                taken.add(name)
    return taken


def _alloc_peer_name(canon: str, gid: str = "") -> tuple:
    # 服务端唯一取名：base 取自 canon（不再信任前端传来的名字），冲突时加数字后缀而不是覆盖已有 peer
    base = re.sub(r"[^a-z0-9]", "", str(canon or gid or "agent").lower())[:10] or "agent"
    taken = _taken_peer_names()
    if base not in taken:
        name, collided = base, False
    else:
        collided = True
        name = next(f"{base}{i}" for i in range(1, 1000) if f"{base}{i}" not in taken)
    with JOIN_LOCK:
        JOIN_RESERVED[name] = time.time() + JOIN_RESERVE_TTL
    return name, ("collision-renamed" if collided else "server-alloc")


def _release_peer_name(name: str) -> None:
    with JOIN_LOCK:
        JOIN_RESERVED.pop(name, None)


# ---------- 账号 ----------
def _seal_account(raw: dict) -> dict:
    acct = {k: v for k, v in raw.items() if k not in SECRET_FIELDS and k != "sessions"}
    acct["password_sealed"] = SEC.seal(str(raw.get("password") or ""))
    acct["auth_token_sealed"] = SEC.seal(str(raw.get("auth_token") or ""))
    cookies = raw.get("cookies") or {}
    acct["cookies_sealed"] = SEC.seal(json.dumps(cookies, ensure_ascii=False)) if cookies else ""
    acct["has_password"] = bool(raw.get("password"))
    acct["has_token"] = bool(raw.get("auth_token"))
    acct["cookie_count"] = len(cookies)
    acct["session_ids"] = [s.get("session_id") for s in (raw.get("sessions") or []) if isinstance(s, dict) and s.get("session_id")]
    return acct


def _reset_epoch(text: str, base: float) -> float:
    m = RESET_RE.search(text or "")
    if not m:
        return 0
    lt = time.localtime(base)
    target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, int(m.group(1)), int(m.group(2)), 0, 0, 0, -1))
    if target < base:
        target += 86400
    return target


def judge_health(acct: dict, now: float = None) -> dict:
    now = now or time.time()
    status = str(acct.get("status") or "")
    code = int(acct.get("status_code") or 0)
    cooldown = float(acct.get("cooldown_until") or 0)
    reset_text = str(acct.get("reset_time_text") or "")
    reset_at = _reset_epoch(reset_text, float(acct.get("last_checked_epoch") or now)) if reset_text else 0
    reason = str(acct.get("error_reason") or "")
    if status in ("banned", "disabled") or code in (401, 403):
        return {"health": "banned", "label": "已封禁或失效", "until": 0, "reason": reason or f"HTTP {code}"}
    if cooldown > now:
        return {"health": "cooldown", "label": "冷却中", "until": cooldown, "reason": reason}
    if reset_at > now:
        return {"health": "exhausted", "label": "额度用尽", "until": reset_at, "reason": reset_text}
    if status == "exhausted":
        return {"health": "exhausted", "label": "额度用尽", "until": 0, "reason": reason}
    if code and code != 200:
        return {"health": "degraded", "label": "异常", "until": 0, "reason": reason or f"HTTP {code}"}
    if not acct.get("has_token") and not acct.get("cookie_count"):
        return {"health": "unauthenticated", "label": "未登录", "until": 0, "reason": "缺少凭据"}
    return {"health": "ready", "label": "可用", "until": 0, "reason": ""}


def sync_accounts() -> dict:
    data = UP.list_accounts()
    incoming = data.get("accounts") or []
    now = time.time()

    def apply(store):
        old = {a.get("email"): a for a in store["accounts"]}
        merged = []
        for raw in incoming:
            acct = _seal_account(raw)
            prev = old.get(acct.get("email"), {})
            acct["note"] = prev.get("note", "")
            acct["tags"] = prev.get("tags", [])
            acct["probe_session_id"] = prev.get("probe_session_id", "")
            acct["last_checked_epoch"] = now
            acct.update(judge_health(acct, now))
            merged.append(acct)
        store["accounts"] = merged
        store["active_email"] = data.get("active_email") or ""
        store["synced_at"] = ST.now_text()
        return len(merged)

    n = ST.update("accounts", apply)
    return {"count": n}


def public_accounts() -> dict:
    store = ST.load("accounts")
    now = time.time()
    out = []
    sessions = ST.load("sessions")["sessions"]
    today = time.strftime("%Y-%m-%d")
    reqs = [r for r in ST.load("stats")["requests"] if str(r.get("at", "")).startswith(today)]
    sid_owner = {x["id"]: x.get("account") for x in sessions}
    used_today = {}
    for r in reqs:
        e = sid_owner.get(r.get("session_id") or "")
        if e:
            used_today[e] = used_today.get(e, 0) + 1
    by_acct = {}
    for x in sessions:
        c = session_canon(x)
        if c["source"] == "probe" or not c["canon"]:
            continue
        by_acct.setdefault(x.get("account"), {}).setdefault(c["canon"], 0)
        by_acct[x.get("account")][c["canon"]] += 1
    for a in store["accounts"]:
        item = {k: v for k, v in a.items() if not k.endswith("_sealed")}
        item.update(judge_health(a, now))
        models = by_acct.get(a.get("email"), {})
        item["models"] = dict(sorted(models.items(), key=lambda kv: -kv[1]))
        item["used_today"] = used_today.get(a.get("email"), 0)
        item["exhausted_at"] = a.get("exhausted_at") or ""
        item["reset_time_text"] = a.get("reset_time_text") or ""
        out.append(item)
    summary = {}
    for a in out:
        summary[a["health"]] = summary.get(a["health"], 0) + 1
    return {"accounts": out, "active_email": store["active_email"], "synced_at": store["synced_at"], "summary": summary}


def set_account_note(email: str, note: str, tags) -> None:
    def apply(store):
        for a in store["accounts"]:
            if a.get("email") == email:
                a["note"] = note
                a["tags"] = [str(t) for t in (tags or [])]
    ST.update("accounts", apply)


def parse_account_lines(raw_text: str) -> list:
    rows = []
    for line in str(raw_text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"[\s,;:|]+", line, maxsplit=1)
        if len(parts) == 2 and "@" in parts[0]:
            rows.append({"email": parts[0], "password": parts[1]})
        elif "@" in parts[0]:
            rows.append({"email": parts[0], "password": ""})
    return rows


# ---------- 会话 ----------
def sync_sessions() -> dict:
    data = UP.list_sessions(2000)
    entries = data.get("entries") or []
    probe_prompt = ST.load("settings").get("probe_prompt", "")

    def apply(store):
        old = {s.get("id"): s for s in store["sessions"]}
        rows = []
        for e in entries:
            sid = e.get("id") or e.get("session_id")
            if not sid:
                continue
            prev = old.get(sid, {})
            title = str(e.get("title") or "")
            row = {
                "id": sid, "title": title, "account": e.get("account") or "", "account_status": e.get("account_status") or "",
                "model": e.get("model") or "", "detected_model": e.get("detected_model") or "", "family": e.get("model_family") or "",
                "verified": bool(e.get("verified")), "verification_count": int(e.get("verification_count") or 0),
                "last_verified_at": e.get("last_verified_at") or "", "drift": bool(e.get("model_drift")), "prev_model": e.get("prev_model") or "",
                "created_at": e.get("createdAt") or "", "updated_at": e.get("updatedAt") or "", "healthy": bool(e.get("healthy", True)),
                "routes": e.get("routes") or [], "is_probe": bool(probe_prompt) and title.strip().startswith(probe_prompt[:8]),
                "pinned": prev.get("pinned", False), "note": prev.get("note", ""), "peer": prev.get("peer", ""), "group": prev.get("group", ""), "observed_model": prev.get("observed_model", ""), "observed_at": prev.get("observed_at", ""), "drift_from": prev.get("drift_from", ""),
            }
            rows.append(row)
        store["sessions"] = rows
        store["synced_at"] = ST.now_text()
        return len(rows)

    n = ST.update("sessions", apply)
    return {"count": n}


def sessions_view() -> dict:
    store = ST.load("sessions")
    rows = store["sessions"]
    families = {}
    for s in rows:
        key = s.get("detected_model") or "Unknown"
        if key in ("", "Unknown"):
            key = "未识别"
        families[key] = families.get(key, 0) + 1
    return {"sessions": rows, "synced_at": store["synced_at"], "families": families, "probe_count": sum(1 for s in rows if s.get("is_probe")), "drift_log": (ST.load("sessions").get("drift_log") or [])[-30:]}


def set_session_flags(session_id: str, pinned=None, note=None) -> None:
    def apply(store):
        for s in store["sessions"]:
            if s.get("id") == session_id:
                if pinned is not None:
                    s["pinned"] = bool(pinned)
                if note is not None:
                    s["note"] = str(note)
    ST.update("sessions", apply)


# ---------- 路由 ----------
def sync_routes() -> dict:
    cfg = UP.relay_config()

    def apply(store):
        store["routes"] = {k: {"session_id": v, "source": "upstream"} for k, v in (cfg.get("routes") or {}).items()}
        store["session_pools"] = cfg.get("session_pools") or {}
        store["default_model"] = cfg.get("default_model") or ""
        store["default_session_id"] = cfg.get("default_session_id") or ""
        store["updated_at"] = ST.now_text()
        return len(store["routes"])

    n = ST.update("routes", apply)
    return {"count": n}


def routes_view() -> dict:
    routes = ST.load("routes")
    sessions = {s["id"]: s for s in ST.load("sessions")["sessions"]}
    rows = []
    for model, info in (routes.get("routes") or {}).items():
        sid = info.get("session_id") if isinstance(info, dict) else info
        s = sessions.get(sid, {})
        rows.append({"model": model, "session_id": sid, "account": s.get("account", ""), "detected_model": s.get("detected_model", ""), "verified": s.get("verified", False), "title": s.get("title", ""), "pool_size": len((routes.get("session_pools") or {}).get(model, []))})
    rows.sort(key=lambda r: r["model"])
    return {"routes": rows, "default_model": routes.get("default_model", ""), "updated_at": routes.get("updated_at", ""), "models": sorted({r["model"] for r in rows})}


def set_route(model: str, session_id: str) -> dict:
    UP.set_route(model, session_id)
    return sync_routes()


def delete_route(model: str) -> dict:
    UP.delete_route(model)
    return sync_routes()


# ---------- API Key ----------
def new_key(name: str, allowed_models=None) -> dict:
    token = "ak-" + secrets.token_urlsafe(24)
    rec = {"id": uuid.uuid4().hex[:8], "name": name or "未命名", "prefix": token[:10], "hash": _hash(token), "created_at": ST.now_text(), "last_used_at": "", "requests": 0, "allowed_models": [str(m) for m in (allowed_models or [])], "revoked": False}
    ST.update("keys", lambda st: st["keys"].append(rec))
    return {"id": rec["id"], "key": token, "prefix": rec["prefix"]}


def _hash(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def keys_view() -> dict:
    ks = ST.load("keys")["keys"]
    return {"keys": [{k: v for k, v in r.items() if k != "hash"} for r in ks]}


def revoke_key(key_id: str) -> None:
    def apply(st):
        for r in st["keys"]:
            if r["id"] == key_id:
                r["revoked"] = True
    ST.update("keys", apply)


def verify_key(token: str, model: str = "") -> dict:
    if not token:
        return {"ok": False, "error": "缺少 API Key"}
    h = _hash(token)
    for r in ST.load("keys")["keys"]:
        if r["hash"] == h:
            if r.get("revoked"):
                return {"ok": False, "error": "API Key 已吊销"}
            if r.get("allowed_models") and model and model not in r["allowed_models"]:
                return {"ok": False, "error": f"此 Key 不允许模型 {model}"}
            return {"ok": True, "id": r["id"], "name": r["name"]}
    return {"ok": False, "error": "API Key 无效"}


def touch_key(key_id: str) -> None:
    def apply(st):
        for r in st["keys"]:
            if r["id"] == key_id:
                r["requests"] = int(r.get("requests") or 0) + 1
                r["last_used_at"] = ST.now_text()
    ST.update("keys", apply)


# ---------- 请求统计 ----------
def record_request(rec: dict) -> None:
    def apply(st):
        st["requests"] = (st["requests"] + [rec])[-500:]
        t = st["totals"]
        t["count"] += 1
        t["ok" if rec.get("ok") else "fail"] += 1
        t["input_tokens"] += int(rec.get("input_tokens") or 0)
        t["output_tokens"] += int(rec.get("output_tokens") or 0)
    ST.update("stats", apply)


def stats_view() -> dict:
    st = ST.load("stats")
    recent = st["requests"][-100:]
    by_model = {}
    for r in st["requests"]:
        m = by_model.setdefault(r.get("model") or "auto", {"count": 0, "ok": 0, "ms": 0})
        m["count"] += 1
        m["ok"] += 1 if r.get("ok") else 0
        m["ms"] += int(r.get("ms") or 0)
    for m in by_model.values():
        m["avg_ms"] = int(m["ms"] / m["count"]) if m["count"] else 0
        m["success_rate"] = round(m["ok"] / m["count"], 3) if m["count"] else 0
    return {"totals": st["totals"], "recent": recent, "by_model": by_model}


# ---------- 探针 ----------
def probe_account(email: str) -> dict:
    settings = ST.load("settings")
    ttl = float(settings.get("probe_ttl_hours") or 24) * 3600
    now = time.time()
    records = ST.load("probes")["records"]
    last = next((r for r in reversed(records) if r.get("email") == email), None)
    if last and now - float(last.get("epoch") or 0) < ttl and last.get("ok"):
        return {"skipped": True, "reason": "近期已探测", "last": last}
    started = time.time()
    try:
        status = UP.check_status(email)
        ok = int(status.get("status_code") or 0) == 200 and str(status.get("status") or "active") == "active"
        rec = {"email": email, "epoch": now, "at": ST.now_text(), "ok": ok, "ms": int((time.time() - started) * 1000), "status_code": status.get("status_code"), "category": status.get("status_category") or status.get("status"), "reason": status.get("error_reason") or status.get("reason") or ""}
    except UP.UpstreamError as e:
        rec = {"email": email, "epoch": now, "at": ST.now_text(), "ok": False, "ms": int((time.time() - started) * 1000), "status_code": e.status, "category": "探测失败", "reason": str(e)}
    def apply(st):
        st["records"] = (st["records"] + [rec])[-2000:]
    ST.update("probes", apply)
    return {"skipped": False, "result": rec}


def probes_view(limit=200) -> dict:
    return {"records": ST.load("probes")["records"][-limit:]}


# ---------- 榜单 ----------
def fetch_leaderboard() -> dict:
    src = ST.load("settings").get("leaderboard_source", "").rstrip("/")
    with _urlopen(src + "/latest.json", timeout=20) as r:
        latest = json.loads(r.read().decode("utf-8"))
    date = latest.get("path") or latest.get("date")
    boards = {}
    for name in LEADERBOARD_BOARDS:
        try:
            with _urlopen(f"{src}/{date}/{name}.json", timeout=20) as r:
                boards[name] = json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001  单个榜单失败不影响其他榜单
            boards[name] = {"meta": {"error": str(e)}, "models": []}
    ST.save("leaderboard", {"boards": boards, "fetched_at": ST.now_text(), "date": date})
    return {"date": date, "boards": list(boards)}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


LEADERBOARD_STALE_SEC = 12 * 3600   # 上游每天出一份；超过半天没拉就算旧


def leaderboard_age_sec(lb: dict):
    """本地榜单快照距上次拉取的秒数；从没拉过 / 时间坏了返回 None（按「需要拉」处理）。"""
    try:
        return max(0.0, time.time() - time.mktime(time.strptime(str(lb.get("fetched_at") or ""), "%Y-%m-%d %H:%M:%S")))
    except (TypeError, ValueError, OverflowError):
        return None


def ensure_fresh_leaderboard():
    """榜单只在有人点「刷新」时才拉，于是一直停在 09-21（2026-10-01 实测上游已到 10-01）。
    看榜单时快照超过 LEADERBOARD_STALE_SEC 就在后台起一次拉取；已有拉取在跑就不重复起。返回新作业或 None。"""
    age = leaderboard_age_sec(ST.load("leaderboard"))
    if age is not None and age < LEADERBOARD_STALE_SEC:
        return None
    with JOBS_LOCK:
        if any(j.get("kind") == "leaderboard" and j.get("status") == "running" for j in JOBS.values()):
            return None
    return start_leaderboard()


def leaderboard_view() -> dict:
    try:
        refreshing = ensure_fresh_leaderboard() is not None
    except Exception:  # noqa: BLE001  拉取失败不能挡住看旧数据
        refreshing = False
    with JOBS_LOCK:
        refreshing = refreshing or any(j.get("kind") == "leaderboard" and j.get("status") == "running" for j in JOBS.values())
    lb = ST.load("leaderboard")
    sessions = ST.load("sessions")["sessions"]
    stats = stats_view()["by_model"]
    routes = routes_view()["routes"]
    local = {}
    for s in sessions:
        key = _norm(s.get("detected_model") or s.get("model"))
        if key and key != "unknown":
            d = local.setdefault(key, {"sessions": 0, "verified": 0})
            d["sessions"] += 1
            d["verified"] += 1 if s.get("verified") else 0
    routed = {_norm(r["model"]): r for r in routes}
    boards = {}
    for name, board in (lb.get("boards") or {}).items():
        rows = []
        for m in board.get("models") or []:
            key = _norm(m.get("model"))
            fam = key[:12]
            match = local.get(key) or next((v for k, v in local.items() if k.startswith(fam) or fam.startswith(k[:12])), None)
            stat = stats.get(m.get("model")) or next((v for k, v in stats.items() if _norm(k)[:12] == fam), None)
            rows.append({**m, "local_sessions": (match or {}).get("sessions", 0), "local_verified": (match or {}).get("verified", 0), "success_rate": (stat or {}).get("success_rate"), "avg_ms": (stat or {}).get("avg_ms"), "routed": key in routed or any(k.startswith(fam) for k in routed)})
        boards[name] = {"meta": board.get("meta") or {}, "models": rows}
    age = leaderboard_age_sec(lb)
    return {"boards": boards, "fetched_at": lb.get("fetched_at", ""), "date": lb.get("date", ""),
            "age_hours": None if age is None else round(age / 3600, 1), "refreshing": refreshing,
            "stale": age is None or age >= LEADERBOARD_STALE_SEC,
            "source": ST.load("settings").get("leaderboard_source", "")}


# ---------- 后台作业 ----------
JOBS = {}
JOBS_LOCK = threading.Lock()

# 余额 / 限额 / 凭据类错误必须原样透传给用户，不能吞成一句「失败」
SPEND_RE = re.compile(r"spend limit|quota|insufficient|balance|rate.?limit|too many requests|额度|余额|限额", re.I)
TOKEN_RE = re.compile(r"publicAccessToken|no token|unauthor|invalid.{0,12}token|401|凭据", re.I)


def _classify_error(msg: str) -> str:
    m = msg or ""
    if "403" in m or "cloudflare" in m.lower() or "forbidden" in m.lower():
        return "cf_403"
    if SPEND_RE.search(m):
        return "spend_limit"
    if TOKEN_RE.search(m):
        return "no_token"
    if "timed out" in m.lower() or "timeout" in m.lower():
        return "timeout"
    return "error"


# 接入相关的自由文本（reply/first_text/relay_error/错误信息）统一脱敏后才允许落盘或返回给前端：
# 认证 scheme / URL / 键值对（显式 =： 或空白分隔）/ 中文标签 / 邮箱 / 前缀 key / 长随机串。
# 规则自上而下各跑一遍；每条「值」都要求不是 [ 或 < 开头，因此前面规则写进去的 [已脱敏]
# 不会被后面的规则再扫一次（0ff2 审查第 6 条）。测试用 sanitize(sanitize(x)) == sanitize(x) 钉住。
def _redact_url(m) -> str:
    try:
        u = urllib.parse.urlsplit(m.group(0))
    except ValueError:
        return "[URL已脱敏]"
    # 只去掉 userinfo（user:pass 常在这里），保留 host:port：端口不是秘密，但本机 8765/8791/9091
    # 全靠端口区分，丢了端口的错误信息没法排障（0ff2 审查第 4 条）。
    host = u.netloc.rsplit("@", 1)[-1]
    tail = "/[已脱敏]" if (u.path.strip("/") or u.query or u.fragment) else ""
    return f"{u.scheme}://{host}{tail}"


# 不依赖「键=值」形状的认证 scheme：authorization=Basic <base64> 里真正的凭据是 scheme 后面那一串，
# 28 字符的 base64 够不到 32 位兜底，只按关键词吃值会整串漏出（0ff2 审查必改 1）。
_NOT_REDACTED = r"(?![<\[])"
# 只排除「scheme 词 + 空白」：那是规则 1 的地盘（authorization=Basic <base64>）。
# 曾经写成 \b，于是 password=basic-hunter2 / secret=digest.abcdef99 / api_key=negotiate:ab12cd34 /
# token=Bearer-abc123xyz 里的 - . : 都算单词边界，规则 3 被整体跳过、规则 1 又要求空白，四行凭据原样漏出
# （sonnet1 RETURN @d4da3fe83a）。
_NOT_SCHEME = r"(?!(?:bearer|basic|digest|negotiate)\s)"
# 空白分隔时值必须「像一个凭据」：带数字或 _ - + / = @ %、「.字母」，或长度 ≥ 16
# （supersecretvalue 这类纯字母 header 值正好 16 位）。否则 "token expired"、"key not found"
# 里的普通词会被连带打码，错误信息就失去排障价值——这是精度取舍，写在注释里而不是靠 limit 兜。
_SECRETISH = (r"(?![<\[])(?:(?=[^\s,;&\"']{3,})(?=[^\s,;&\"']*(?:[0-9_\-+/=@%]|\.[A-Za-z]))[^\s,;&\"']+"
              r"|[^\s,;&\"']{16,})")
_KEY = (r"(?i)\b(publicAccessToken|access[_-]?token|refresh[_-]?token|api[_-]?key|x-api-key"
        r"|key|token|secret|password|passwd|pwd|authorization|cookie)\b")

REDACT_RULES = (
    (re.compile(r"(?i)\b(bearer|basic|digest|negotiate)\s+\S+"), r"\1 [已脱敏]"),
    (re.compile(r"https?://[^\s\"'<>）)]+"), _redact_url),
    (re.compile(_KEY + r"\s*[=:：]\s*\"?" + _NOT_REDACTED + _NOT_SCHEME + r"[^\s,;&\"']{3,}"), r"\1=[已脱敏]"),
    (re.compile(_KEY + r"\s+(?:" + _SECRETISH + r")"), r"\1=[已脱敏]"),
    (re.compile(r"(密码|口令|凭据|token|密钥)[:：]\s*" + _NOT_REDACTED + r"\S+"), r"\1:[已脱敏]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[邮箱已脱敏]"),
    # e5de/B2：本系统 new_key() 签发的 ak-… 以及 sk-/pk-/rk-/bt- 前缀 key：不要求含数字、允许较短（≥ 8 位随机段）。
    # 分隔符 [-_] 都认：sk_live_ / pk_test_ 是真实 key 的常见写法，只认连字符会整条漏出（0ff2 审查必改 2）。
    (re.compile(r"(?<![A-Za-z0-9])(?:ak|sk|pk|rk|bt)[-_][A-Za-z0-9_\-]{8,}"), "[已脱敏]"),
    # 兜底一（含字母的 ≥32 位串）：原先要求含数字会漏 token_urlsafe(24) 约 0.47% 的纯字母产物。
    (re.compile(r"\b(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{32,}\b"), "[已脱敏]"),
    # 兜底二（纯数字长串）：审查给的两档里取 ≥32 而不是 ≥40——34 位纯数字就是本轮要钉住的对抗输入，
    # 40 档当场漏出。代价是 32 位以上的纯数字（长单号、拼接时间戳）也会被打码；这类串出现在
    # 「接入相关的错误信息」里对排障没有价值，而凭据会落盘并回传前端，方向上宁可多打。
    (re.compile(r"\b[0-9]{32,}\b"), "[已脱敏]"),
)


def sanitize_text(text, limit: int = 200) -> str:
    # limit 只是显示宽度，不是安全边界：凭据必须由上面的规则打掉，不能指望「截断在后面」兜住。
    s = str(text or "").replace("\r", " ").replace("\n", " ")
    for pat, rep in REDACT_RULES:
        s = pat.sub(rep, s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max(0, int(limit))]


def _job(kind: str, fn, payload: dict):
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "kind": kind, "status": "running", "started_at": ST.now_text(), "progress": 0, "total": 0, "message": "", "result": None, "error": "", "reason_code": "", "retryable": False, "payload": {k: v for k, v in payload.items() if k not in SECRET_FIELDS}}
    with JOBS_LOCK:
        JOBS[jid] = job

    def run():
        try:
            job["result"] = fn(job)
            job["status"] = "done"
            job["reason_code"] = job.get("reason_code") or "ok"
        except JoinError as e:  # 已分辨原因的失败：原样透传给前端 toast，不吞成「失败」
            job["status"] = "failed"
            job["error"] = str(e)
            job["reason_code"] = e.reason_code
            job["retryable"] = e.retryable
        except Exception as e:  # noqa: BLE001  作业异常记录到 job 而非崩溃线程
            job["status"] = "failed"
            job["error"] = str(e)
            job["reason_code"] = job.get("reason_code") or _classify_error(str(e))
        job["finished_at"] = ST.now_text()

    threading.Thread(target=run, daemon=True).start()
    return job


def jobs_view() -> dict:
    with JOBS_LOCK:
        items = sorted(JOBS.values(), key=lambda j: j["started_at"], reverse=True)[:50]
    return {"jobs": items}


def start_check_all(emails=None) -> dict:
    def fn(job):
        pool = emails or [a["email"] for a in ST.load("accounts")["accounts"]]
        job["total"] = len(pool)
        results = []
        for i, email in enumerate(pool):
            job["message"] = f"检测 {email}"
            results.append(probe_account(email))
            job["progress"] = i + 1
        sync_accounts()
        return {"checked": len(results), "skipped": sum(1 for r in results if r.get("skipped"))}
    return _job("check_all", fn, {"emails": emails or []})


def start_import(raw_text: str) -> dict:
    rows = parse_account_lines(raw_text)

    def fn(job):
        job["total"] = len(rows)
        job["message"] = f"提交 {len(rows)} 个账号给登录器"
        res = UP.batch_add("\n".join(f"{r['email']} {r['password']}" for r in rows))
        job["progress"] = len(rows)
        sync_accounts()
        return {"submitted": len(rows), "upstream": {k: v for k, v in res.items() if k in ("success", "added", "count", "message", "detail")}}
    return _job("import", fn, {"count": len(rows)})


def start_register(count: int, password: str) -> dict:
    def fn(job):
        job["total"] = count
        job["message"] = "申请临时邮箱并注册"
        res = UP.auto_register(count, password)
        job["progress"] = count
        sync_accounts()
        return {"created": res.get("created") or (1 if res.get("success") else 0), "emails": [res["email"]] if res.get("email") else []}
    return _job("register", fn, {"count": count})


def start_full_sync() -> dict:
    def fn(job):
        job["total"] = 3
        job["message"] = "同步账号"
        a = sync_accounts()
        job["progress"] = 1
        job["message"] = "同步会话"
        s = sync_sessions()
        job["progress"] = 2
        job["message"] = "同步路由"
        r = sync_routes()
        job["progress"] = 3
        return {"accounts": a["count"], "sessions": s["count"], "routes": r["count"]}
    return _job("sync", fn, {})


def start_detect(session_ids) -> dict:
    def fn(job):
        job["total"] = len(session_ids)
        out = []
        for i, sid in enumerate(session_ids):
            job["message"] = f"识别 {sid[:8]}"
            try:
                out.append({"id": sid, "ok": True, "result": UP.detect_one(sid)})
            except UP.UpstreamError as e:
                out.append({"id": sid, "ok": False, "error": str(e)})
            job["progress"] = i + 1
        sync_sessions()
        return {"detected": sum(1 for o in out if o["ok"]), "failed": sum(1 for o in out if not o["ok"])}
    return _job("detect", fn, {"count": len(session_ids)})


def start_delete_sessions(session_ids) -> dict:
    def fn(job):
        job["total"] = len(session_ids)
        ok = 0
        for i, sid in enumerate(session_ids):
            try:
                UP.delete_session(sid)
                ok += 1
            except UP.UpstreamError as e:
                job["message"] = str(e)
            job["progress"] = i + 1
        sync_sessions()
        return {"deleted": ok}
    return _job("delete_sessions", fn, {"count": len(session_ids)})


def start_batch_create(emails, count, auto_detect) -> dict:
    prompt = ST.load("settings").get("probe_prompt", "")

    def fn(job):
        job["total"] = len(emails) * count
        job["message"] = "上游批量开会话"
        res = UP.batch_create(emails, count, auto_detect, prompt)
        job["progress"] = job["total"]
        sync_sessions()
        return {k: v for k, v in res.items() if k in ("success", "created", "count", "message")}
    return _job("batch_create", fn, {"emails": emails, "count": count})


def start_leaderboard() -> dict:
    def fn(job):
        job["total"] = 1
        job["message"] = "拉取榜单快照"
        res = fetch_leaderboard()
        job["progress"] = 1
        return res
    return _job("leaderboard", fn, {})


# ---------- 模型分组 ----------
# 官方模型名规范化：把标题、识别结果里的各种写法归到同一个 canonical 名
CANON_RULES = [
    (r"gpt[-_ ]?6[-_ ]?astra", "gpt-6-astra"), (r"gpt[-_ ]?5\.6[-_ ]?luna", "gpt-5.6-luna"), (r"gpt[-_ ]?5\.6[-_ ]?sol", "gpt-5.6-sol"), (r"gpt[-_ ]?5\.6[-_ ]?terra", "gpt-5.6-terra"), (r"gpt[-_ ]?5\.6", "gpt-5.6"),
    (r"claude[-_ ]?fable[-_ ]?5[-_.]1", "claude-fable-5.1"), (r"claude[-_ ]?fable[-_ ]?5", "claude-fable-5"), (r"claude[-_ ]?opus[-_ ]?5", "claude-opus-5"), (r"claude[-_ ]?opus[-_ ]?4[-_.]?6", "claude-opus-4.6"),
    (r"kimi[-_ ]?k3", "kimi-k3"), (r"glm[-_ ]?5[-_.]?3[-_ ]?flash", "glm-5.3-flash"), (r"glm[-_ ]?5[-_.]?3", "glm-5.3"), (r"glm[-_ ]?5[-_.]?2", "glm-5.2"),
    (r"grok[-_ ]?4[-_.]?6", "grok-4.6"), (r"grok[-_ ]?4[-_.]?5", "grok-4.5"),
    (r"deepseek[-_ ]?v4[-_.]?1?[-_ ]?flash|deepseek[-_ ]?flash", "deepseek-flash"), (r"deepseek[-_ ]?v4[-_ ]?pro", "deepseek-v4-pro"),
    (r"gemini[-_ ]?4[-_ ]?pro", "gemini-4-pro"), (r"gemini[-_ ]?3[-_.]?8[-_ ]?flash", "gemini-3.8-flash"), (r"gemini[-_ ]?3[-_.]?6[-_ ]?flash", "gemini-3.6-flash"),
    (r"qwen[-_ ]?3[-_.]?8[-_ ]?max", "qwen3.8-max"), (r"qwen[-_ ]?3[-_.]?8[-_ ]?flash", "qwen3.8-flash"), (r"qwen3p8", "qwen3.8-flash"), (r"qwen[-_ ]?latest[-_ ]?series", "qwen-latest-series"),
    (r"hy[-_ ]?4[-_ ]?preview|hy-dev|hunyuan", "hy4-preview"), (r"muse[-_ ]?spark[-_ ]?1[-_.]?3", "muse-spark-1.3"), (r"muse[-_ ]?spark[-_ ]?1[-_.]?2", "muse-spark-1.2"),
    (r"doubao[-_ ]?seed[-_ ]?2", "doubao-seed-2"), (r"mimo[-_ ]?v?2[-_.]?5", "mimo-v2.5"), (r"mistral[-_ ]?medium", "mistral-medium"), (r"super[-_ ]?nova", "super-nova"),
]
_CANON = [(re.compile(p, re.I), name) for p, name in CANON_RULES]
VARIANT_RE = re.compile(r"[-_ ](low|medium|high|xhigh|thinking|mini|nano|\d{4,})$", re.I)

# 预置分组：pattern 为前缀匹配（canonical 名以它开头即属于该组），order 越小越靠前
PRESET_GROUPS = [
    {"id": "gpt-6-astra", "name": "GPT-6 Astra", "match": ["gpt-6-astra"], "tier": "strong", "order": 1},
    {"id": "claude-fable-5", "name": "Claude Fable 5 系列", "match": ["claude-fable-5"], "tier": "strong", "order": 2},
    {"id": "kimi-k3", "name": "Kimi K3", "match": ["kimi-k3"], "tier": "strong", "order": 3},
    {"id": "claude-opus-5", "name": "Claude Opus 5", "match": ["claude-opus-5"], "tier": "strong", "order": 4},
    {"id": "glm-5.3", "name": "GLM 5.3 系列", "match": ["glm-5.3"], "tier": "daily", "order": 5},
    {"id": "deepseek-flash", "name": "DeepSeek Flash", "match": ["deepseek-flash"], "tier": "daily", "order": 6},
    {"id": "qwen3.8-flash", "name": "Qwen 3.8 Flash", "match": ["qwen3.8-flash"], "tier": "daily", "order": 7},
    {"id": "gemini-3.8-flash", "name": "Gemini 3.8 Flash", "match": ["gemini-3.8-flash"], "tier": "daily", "order": 8},
    {"id": "hy4-preview", "name": "混元 HY4 Preview", "match": ["hy4-preview"], "tier": "daily", "order": 9},
    {"id": "grok-4.6", "name": "Grok 4.6", "match": ["grok-4.6"], "tier": "strong", "order": 10},
    {"id": "qwen3.8-max", "name": "Qwen 3.8 Max", "match": ["qwen3.8-max"], "tier": "daily", "order": 11},
    {"id": "gemini-4-pro", "name": "Gemini 4 Pro（规则推断）", "match": ["gemini-4-pro"], "tier": "strong", "order": 12, "rule": "gemini-3.8-flash 会话若被识别为高于 flash 档的输出质量，归入此组；当前无自动判据，仅手工加入"},
    {"id": "gpt-5.6", "name": "GPT 5.6 系列", "match": ["gpt-5.6"], "tier": "strong", "order": 13},
]


def canonical_model(raw: str) -> str:
    text = str(raw or "").strip()
    if not text or text.lower() == "unknown":
        return ""
    text = text.split(" · ")[0]
    for rx, name in _CANON:
        if rx.search(text):
            return name
    base = text.split("/")[-1].lower()
    base = VARIANT_RE.sub("", base)
    return base


def session_canon(s: dict) -> dict:
    # 识别结果优先；未识别时用标题（探针方式开的会话，标题就是目标模型名），并标记来源
    det = canonical_model(s.get("detected_model"))
    if det:
        return {"canon": det, "source": "detected"}
    obs = canonical_model(s.get("observed_model"))
    if obs:
        return {"canon": obs, "source": "detected"}
    if s.get("is_probe"):
        return {"canon": "", "source": "probe"}
    title = canonical_model(s.get("title"))
    return {"canon": title, "source": "title" if title else "none"}


def _groups_store() -> dict:
    st = ST.load("routes")
    groups = st.get("groups")
    if not groups:
        groups = [dict(g, custom=False, manual_add=[], manual_remove=[], key_id="") for g in PRESET_GROUPS]
        st["groups"] = groups
        ST.save("routes", st)
    return st


def _group_members(g: dict, sessions: list) -> list:
    out = []
    removed = set(g.get("manual_remove") or [])
    added = set(g.get("manual_add") or [])
    for s in sessions:
        if s["id"] in removed:
            continue
        c = session_canon(s)
        hit = s["id"] in added or any(c["canon"] == m or c["canon"].startswith(m + "-") or c["canon"].startswith(m + ".") for m in g.get("match") or [])
        if hit and c["source"] != "probe":
            out.append({**s, "canon": c["canon"], "source": "manual" if s["id"] in added else c["source"]})
    healthy = {a["email"]: judge_health(a)["health"] for a in ST.load("accounts")["accounts"]}
    for m in out:
        m["account_health"] = healthy.get(m["account"], "unknown")
    # created_at 解析后比较（ADR 0010）：字符串序在无偏移/带偏移混存时会错排；延迟导入避免环
    def _created_ts(m):
        try:
            import bus_core as _B
            d = _B.parse_ts(m.get("created_at"))
            return d.timestamp() if d else 0.0
        except Exception:
            return 0.0
    out.sort(key=lambda m: (m["account_health"] != "ready", m["source"] != "detected", not m.get("verified"), _created_ts(m), str(m.get("created_at") or "")), reverse=False)
    return out


def groups_view() -> dict:
    st = _groups_store()
    sessions = ST.load("sessions")["sessions"]
    keys = {k["id"]: k for k in ST.load("keys")["keys"]}
    stats = stats_view()["by_model"]
    rows = []
    for g in st["groups"]:
        members = _group_members(g, sessions)
        ready = [m for m in members if m["account_health"] == "ready"]
        key = keys.get(g.get("key_id") or "")
        stat = stats.get(g["id"]) or {}
        rows.append({**{k: v for k, v in g.items() if k != "key_sealed"}, "common": bool(g.get("common")), "has_key_plain": bool(g.get("key_sealed")), "members": members, "member_count": len(members), "ready_count": len(ready), "detected_count": sum(1 for m in members if m["source"] == "detected"), "key_prefix": key["prefix"] if key and not key.get("revoked") else "", "key_requests": (key or {}).get("requests", 0), "success_rate": stat.get("success_rate"), "avg_ms": stat.get("avg_ms")})
    rows.sort(key=lambda r: (r.get("order") or 999, r["name"]))
    unmatched = {}
    for s in sessions:
        c = session_canon(s)
        if c["source"] == "probe" or not c["canon"]:
            continue
        if not any(any(c["canon"] == m or c["canon"].startswith(m + "-") or c["canon"].startswith(m + ".") for m in g.get("match") or []) for g in st["groups"]):
            unmatched[c["canon"]] = unmatched.get(c["canon"], 0) + 1
    return {"groups": rows, "unmatched": dict(sorted(unmatched.items(), key=lambda x: -x[1])), "base_url": ST.load("settings").get("public_base_url") or "http://127.0.0.1:9091"}


def save_group(data: dict) -> dict:
    gid = re.sub(r"[^a-z0-9.\-]", "-", str(data.get("id") or data.get("name") or "").lower()).strip("-")
    if not gid:
        raise ValueError("分组需要名称")
    st = _groups_store()
    cur = next((g for g in st["groups"] if g["id"] == gid), None)
    plain = ""
    if not cur:
        rec = new_key(f"组 {data.get('name') or gid}", [gid])
        plain = rec["key"]
        cur = {"id": gid, "custom": True, "manual_add": [], "manual_remove": [], "key_id": rec["id"], "order": max([g.get("order") or 0 for g in st["groups"]] + [0]) + 1, "tier": "custom", "match": [], "common": False}
        st["groups"].append(cur)
    for k in ("name", "match", "tier", "order", "rule"):
        if k in data:
            cur[k] = data[k]
    if "common" in data:  # 「常用组」只认用户显式设置，不从 strong/daily 等 tier 推导
        cur["common"] = bool(data["common"])
    cur["match"] = [canonical_model(m) or str(m).lower() for m in (cur.get("match") or []) if str(m).strip()]
    ST.save("routes", st)
    if plain:
        _seal_group_key(gid, plain)
    return {"id": gid, "key": plain}


def delete_group(gid: str) -> None:
    def apply(st):
        g = next((x for x in st.get("groups", []) if x["id"] == gid), None)
        if g and g.get("key_id"):
            revoke_key(g["key_id"])
        st["groups"] = [x for x in st.get("groups", []) if x["id"] != gid]
    ST.update("routes", apply)


def group_member_edit(gid: str, session_id: str, add: bool) -> None:
    def apply(st):
        for g in st.get("groups", []):
            if g["id"] != gid:
                continue
            g.setdefault("manual_add", [])
            g.setdefault("manual_remove", [])
            if add:
                g["manual_add"] = list({*g["manual_add"], session_id})
                g["manual_remove"] = [x for x in g["manual_remove"] if x != session_id]
            else:
                g["manual_remove"] = list({*g["manual_remove"], session_id})
                g["manual_add"] = [x for x in g["manual_add"] if x != session_id]
    ST.update("routes", apply)


def reorder_groups(ids: list) -> None:
    def apply(st):
        pos = {gid: i + 1 for i, gid in enumerate(ids)}
        for g in st.get("groups", []):
            if g["id"] in pos:
                g["order"] = pos[g["id"]]
    ST.update("routes", apply)


def set_common_groups(ids: list) -> dict:
    # 常用组白名单：列表里的组 common=True，其余一律 False；不认识的 id 原样报回，不静默丢弃
    want = [str(x) for x in (ids or []) if str(x).strip()]

    def apply(st):
        known = {g["id"] for g in st.get("groups", [])}
        for g in st.get("groups", []):
            g["common"] = g["id"] in want
        return {"common": [x for x in want if x in known], "unknown": [x for x in want if x not in known]}
    _groups_store()
    return ST.update("routes", apply)


def common_group_ids() -> list:
    return [g["id"] for g in _groups_store()["groups"] if g.get("common") is True]


def rotate_group_key(gid: str) -> dict:
    def apply(st):
        g = next((x for x in st.get("groups", []) if x["id"] == gid), None)
        if not g:
            raise ValueError("分组不存在")
        if g.get("key_id"):
            revoke_key(g["key_id"])
        rec = new_key(f"组 {g.get('name') or gid}", [gid])
        g["key_id"] = rec["id"]
        return rec
    rec = ST.update("routes", apply)
    _seal_group_key(gid, rec["key"])
    return rec


_RR = {}


def pick_group_session(gid: str) -> dict:
    # 组内轮转：只在账号可用的成员里选，优先已识别、已验证
    st = _groups_store()
    g = next((x for x in st["groups"] if x["id"] == gid), None)
    if not g:
        return {}
    members = [m for m in _group_members(g, ST.load("sessions")["sessions"]) if m["account_health"] == "ready"]
    if not members:
        return {"error": f"分组 {g.get('name') or gid} 没有可用会话（账号额度用尽或未识别）"}
    i = _RR.get(gid, 0) % len(members)
    _RR[gid] = i + 1
    return {"session": members[i], "group": g}


def resolve_model(model: str) -> dict:
    # 客户端 model 值：先看是不是分组 id，再看上游已有路由，最后原样透传
    st = _groups_store()
    if any(g["id"] == model for g in st["groups"]):
        return {"kind": "group", **pick_group_session(model)}
    return {"kind": "passthrough"}


def ensure_upstream_route(model_alias: str, session_id: str) -> None:
    # 组请求落到具体会话：给上游临时别名 <gid>@<sid前8位>，保证 ArenaX 按会话池命中
    routes = ST.load("routes").get("upstream_aliases") or {}
    if routes.get(model_alias) == session_id:
        return
    UP.set_route(model_alias, session_id)

    def apply(st):
        st.setdefault("upstream_aliases", {})[model_alias] = session_id
    ST.update("routes", apply)


JOIN_WAIT_ROUNDS = 24   # 心跳等待：默认 24 × 5 s = 120 s，可由 join 队列配置覆盖
JOIN_WAIT_STEP = 5


def start_join(gid: str, session_id: str, peer: str, workbench: str, job: dict = None, set_state=None, wait_rounds: int = None, wait_step: float = None):
    # 一键接入：从 Workbench 取带 token 的接入提示词，经中继发进该会话，等待 peer 心跳出现。
    # peer 参数只作为 base 前缀提示，实际名字一律由服务端 _alloc_peer_name 分配，避免前端用
    # /api/state 在线快照算名字而撞上离线 peer（旧行为会「秒判成功」并冒名）。
    # 两种调用方式：不传 job → 旧行为，起后台 _job 线程并返回内存 job（兼容保留）；
    # 传 job → 作为持久 join 队列 worker 的单项执行体同步运行，返回结果 dict：
    # registered=True 才算接入；registered=False 只是「未确认」，调用方绝不能当成功；JoinError 表示已分辨原因的失败。
    # set_state(status, message) 是阶段回调：precheck → sending（投递前最后一刻，可在此拒绝）→ waiting_peer。
    import urllib.parse
    wait_rounds = max(1, int(wait_rounds if wait_rounds is not None else JOIN_WAIT_ROUNDS))
    wait_step = max(0.0, float(wait_step if wait_step is not None else JOIN_WAIT_STEP))

    def phase(status, message):
        job["message"] = message
        if set_state:
            set_state(status, message)

    def run(job):
        job["total"] = 3
        job.setdefault("payload", {})
        sess = next((s for s in ST.load("sessions")["sessions"] if s["id"] == session_id), None)
        if not sess:
            raise JoinError("session_missing", "会话不存在")

        # ① 接入前校验：账号健康度。余额/限额/凭据问题直接原样透传，不浪费一次对话
        acct = next((a for a in ST.load("accounts")["accounts"] if a.get("email") == sess.get("account")), None)
        if acct:
            h = judge_health(acct)
            if h["health"] in ("exhausted", "cooldown"):
                raise JoinError("spend_limit", f"账号 {sess.get('account')} {h['label']}：{h['reason'] or '额度或冷却未恢复'}")
            if h["health"] in ("banned", "unauthenticated"):
                raise JoinError("no_token", f"账号 {sess.get('account')} {h['label']}：{h['reason'] or '缺少可用凭据'}")

        canon = session_canon(sess)["canon"] or gid
        name, name_source = _alloc_peer_name(peer or canon, gid)
        job["payload"]["peer_name"] = name
        job["payload"]["name_source"] = name_source
        if name_source == "collision-renamed":
            job["message"] = f"名字已被占用，改用 {name}"
            job["reason_code"] = "name_collision"

        try:
            phase("precheck", "生成接入提示词")
            q = urllib.parse.urlencode({"kind": "peer", "peer": name, "model": canon})
            try:
                with _urlopen(f"{workbench}/api/connect_link?{q}", timeout=30) as r:
                    prompt = (json.load(r) or {}).get("prompt", "")
            except Exception as e:  # noqa: BLE001
                raise JoinError(_classify_error(str(e)), f"取接入提示词失败：{sanitize_text(e)}", retryable=True) from e
            if not prompt or "Leader尚未登记" in prompt:
                raise JoinError("bridge_missing", "Bridge MCP 地址尚未登记，接入提示词里是占位符；请先在设置里登记 Bridge 地址")
            job["progress"] = 1

            # sending 回调放在任何副作用之前：队列在这里做最后一次取消检查，之后消息一旦发出就不可撤回
            phase("sending", "把提示词发进会话（等待模型连接 MCP）")
            alias = f"join@{session_id[:8]}"
            ensure_upstream_route(alias, session_id)
            sent_at = time.time()
            job["payload"]["sent_at"] = ST.now_text()

            # ② chat_probe 不再拦截接入：探测失败只记 reason_code，仍然进入心跳等待，
            #    因为模型进入 bus_wait_task 长轮询后本来就不会再吐文本（旧逻辑把「在干活」判成失败）。
            reply, probe_error = "", ""
            try:
                res = UP.chat_probe(alias, prompt, timeout=900)
                reply = ((res.get("choices") or [{}])[0].get("message") or {}).get("content", "") or ""
            except Exception as e:  # noqa: BLE001
                probe_error = str(e)
                code = _classify_error(probe_error)
                if code in ("spend_limit", "no_token"):
                    raise JoinError(code, f"中继返回：{sanitize_text(probe_error)}") from e
                job["reason_code"] = job.get("reason_code") or code
            # 模型有时会把接入提示词原样复述回来：先整段剔除，再过统一脱敏，落盘/返回只留净化摘要
            if prompt and prompt[:60] and prompt[:60] in reply:
                reply = reply.replace(prompt, "[接入提示词已省略]").replace(prompt[:60], "[接入提示词已省略]")
            reply = sanitize_text(reply, 200)
            probe_error = sanitize_text(probe_error, 200)
            job["payload"]["first_text"] = reply[:120]
            job["payload"]["relay_error"] = probe_error
            job["progress"] = 2

            # ③ 成功判定：心跳文件必须是本次接入之后写的、模型名对得上、状态正常。
            #    只看 os.path.exists 会被离线 peer 的旧文件骗成功。
            job["message"] = "等待 peer 心跳"
            phase("waiting_peer", f"等待 peer 心跳（最多 {int(wait_rounds * wait_step)} 秒）")
            peer_file = os.path.join(_wb_root(), "peers", name + ".json")
            last = {}
            for _ in range(wait_rounds):
                if os.path.exists(peer_file) and os.path.getmtime(peer_file) > sent_at:
                    try:
                        with open(peer_file, encoding="utf-8") as f:
                            last = json.load(f) or {}
                    except (OSError, ValueError):
                        last = {}
                    hb_model = canonical_model(str(last.get("model") or ""))
                    if last.get("status") not in ("idle", "busy"):
                        job["reason_code"] = "bad_status"
                    elif canon and hb_model and hb_model != canonical_model(canon):
                        job["reason_code"] = "model_mismatch"
                    else:
                        job["progress"] = 3
                        job["reason_code"] = "ok" if name_source != "collision-renamed" else "name_collision"
                        _remember_join(session_id, name, gid)
                        _release_peer_name(name)
                        return {"peer": name, "name_source": name_source, "registered": True,
                                "heartbeat_model": hb_model, "expected_canon": canon, "sent_at": job["payload"]["sent_at"],
                                "first_text": reply[:120], "reply": reply, "relay_error": probe_error}
                if wait_step:
                    time.sleep(wait_step)

            job["reason_code"] = job.get("reason_code") or ("no_text_but_running" if not reply else "no_heartbeat")
            return {"peer": name, "name_source": name_source, "registered": False,
                    "heartbeat_model": canonical_model(str(last.get("model") or "")), "expected_canon": canon, "sent_at": job["payload"]["sent_at"],
                    "first_text": reply[:120], "reply": reply, "relay_error": probe_error,
                    "hint": f"{int(wait_rounds * wait_step)} 秒内没有等到本次接入之后写入的心跳；看 first_text 判断模型是在跑还是报错，不要立刻重发（会再撞一个名字）"}
        except BaseException:
            _release_peer_name(name)
            raise
    if job is not None:
        return run(job)
    return _job("join", run, {"group": gid, "session_id": session_id})


def join_once(gid: str, session_id: str, peer: str, workbench: str, job: dict, set_state=None, wait_rounds: int = None, wait_step: float = None) -> dict:
    # 持久 join 队列（arena_join_queue）调用的单项执行入口：同步跑 start_join 的执行体
    return start_join(gid, session_id, peer, workbench, job=job, set_state=set_state, wait_rounds=wait_rounds, wait_step=wait_step)


def _remember_join(session_id: str, peer: str, gid: str) -> None:
    def apply(st):
        for s in st["sessions"]:
            if s["id"] == session_id:
                s["peer"] = peer
                s["group"] = gid
    ST.update("sessions", apply)


def ungrouped_session_ids(email: str = "") -> list:
    # 不属于任何模型组的会话（含探针会话、标题无法归一的会话）
    st = _groups_store()
    grouped = set()
    sessions = ST.load("sessions")["sessions"]
    for g in st["groups"]:
        grouped.update(m["id"] for m in _group_members(g, sessions))
    return [x["id"] for x in sessions if x["id"] not in grouped and (not email or x.get("account") == email)]


def group_session_ids(gid: str) -> list:
    st = _groups_store()
    g = next((x for x in st["groups"] if x["id"] == gid), None)
    return [m["id"] for m in _group_members(g, ST.load("sessions")["sessions"])] if g else []


def verify_group(gid: str) -> dict:
    # 一键检查：成员的识别结果与组不符就移出（手动加入的也一并复核）
    st = _groups_store()
    g = next((x for x in st["groups"] if x["id"] == gid), None)
    if not g:
        raise ValueError("分组不存在")
    kicked, kept, unknown = [], 0, 0
    for m in _group_members(g, ST.load("sessions")["sessions"]):
        det = canonical_model(m.get("detected_model"))
        if not det:
            unknown += 1
            continue
        ok = any(det == p or det.startswith(p + "-") or det.startswith(p + ".") for p in g.get("match") or [])
        if ok:
            kept += 1
        else:
            kicked.append({"id": m["id"], "title": m.get("title"), "detected": det})
            group_member_edit(gid, m["id"], False)
    return {"kicked": kicked, "kept": kept, "unknown": unknown}


def group_key_plain(gid: str) -> str:
    # 组 Key 明文：本机 DPAPI 封存在 routes 存储里，只对回环请求回显
    import arena_secrets as SEC
    st = _groups_store()
    g = next((x for x in st["groups"] if x["id"] == gid), None)
    sealed = (g or {}).get("key_sealed") or ""
    return SEC.unseal(sealed) if sealed else ""


def _seal_group_key(gid: str, plain: str) -> None:
    import arena_secrets as SEC
    def apply(st):
        for g in st.get("groups", []):
            if g["id"] == gid:
                g["key_sealed"] = SEC.seal(plain)
    ST.update("routes", apply)


def note_observed_model(session_id: str, observed: str, gid: str) -> None:
    # 漂移检测：上游回报的模型名归一后不属于该组 → 写入 session.observed_model 与 drift 标记，成员表下次重算即移出
    obs = canonical_model(observed)
    if not obs or obs == gid or "@" in observed:
        return
    st = _groups_store()
    g = next((x for x in st["groups"] if x["id"] == gid), None)
    fits = any(obs == p or obs.startswith(p + "-") or obs.startswith(p + ".") for p in (g or {}).get("match") or [])

    def apply(store):
        for s in store["sessions"]:
            if s["id"] == session_id:
                s["observed_model"] = obs
                s["observed_at"] = ST.now_text()
                if not fits:
                    s["detected_model"] = obs
                    s["verified"] = False
                    s["drift_from"] = gid
    ST.update("sessions", apply)
    if not fits:
        try:
            handle_drift(session_id, obs, gid)
        except Exception as exc:
            ST.update("sessions", lambda store: [s.__setitem__("drift_error", str(exc)[:200]) for s in store["sessions"] if s["id"] == session_id])


def _group_for_model(canon: str) -> dict:
    st = _groups_store()
    for g in sorted(st["groups"], key=lambda g: -max([len(x) for x in (g.get("match") or [])] + [0])):
        if any(canon == m or canon.startswith(m + "-") or canon.startswith(m + ".") for m in g.get("match") or []):
            return g
    return {}


def handle_drift(session_id: str, observed: str, from_gid: str) -> dict:
    # 漂移自动处置：会话已经不是原组的模型 → 1) 从原组移出（manual_remove）；2) 归入识别出的新组（没有匹配组就留在未分组）；
    # 3) 若该会话已接入 Workbench 作为 peer：改 peer 的 model 与 group、按新组重命名，并把新旧名的对应写进 peers/<old>.json 的 renamed_to；
    # 4) 记一条 drift_log 供页面展示。全程只动本地登记，不改 arena.ai 上游。
    st = _groups_store()
    new_g = _group_for_model(observed)
    new_gid = new_g.get("id", "")
    sess = next((x for x in ST.load("sessions")["sessions"] if x["id"] == session_id), None) or {}
    old_peer = sess.get("peer") or ""
    if from_gid and from_gid != new_gid:
        group_member_edit(from_gid, session_id, False)
    if new_gid and new_gid != from_gid:
        group_member_edit(new_gid, session_id, True)
    new_peer = old_peer
    wb_root = ST.load("settings").get("workbench_root") or os.path.join(WB_ROOT_DEFAULT, "")
    peers_dir = os.path.join(wb_root, "peers")
    if old_peer and os.path.isdir(peers_dir):
        base = re.sub(r"[^a-z0-9]", "", (new_gid or observed).lower().split("-")[0])[:10] or "agent"
        taken = {f[:-5] for f in os.listdir(peers_dir) if f.endswith(".json")}
        n = 1
        while f"{base}{n}" in taken:
            n += 1
        new_peer = f"{base}{n}" if new_gid else old_peer
        old_path = os.path.join(peers_dir, old_peer + ".json")
        try:
            data = json.load(open(old_path, encoding="utf-8")) if os.path.exists(old_path) else {}
        except ValueError:
            data = {}
        data.update({"model": observed, "group": new_gid, "drift_from": from_gid, "drift_at": ST.now_text()})
        if new_peer != old_peer:
            data.update({"peer": new_peer, "renamed_from": old_peer})
            json.dump(data, open(os.path.join(peers_dir, new_peer + ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            json.dump({"peer": old_peer, "status": "renamed", "renamed_to": new_peer, "last_seen": data.get("last_seen", ""), "model": observed, "group": new_gid},
                      open(old_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            # inbox 里排队中的任务包一起搬到新名字下，已领取的保留在旧名字下由 Lead 改派
            old_inbox, new_inbox = os.path.join(wb_root, "inbox", old_peer), os.path.join(wb_root, "inbox", new_peer)
            if os.path.isdir(old_inbox):
                os.makedirs(new_inbox, exist_ok=True)
                for f in os.listdir(old_inbox):
                    if f.endswith(".json"):
                        os.replace(os.path.join(old_inbox, f), os.path.join(new_inbox, f))
        else:
            json.dump(data, open(old_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    entry = {"at": ST.now_text(), "session_id": session_id, "title": sess.get("title", ""), "from_group": from_gid, "to_group": new_gid,
             "observed": observed, "old_peer": old_peer, "new_peer": new_peer}

    def apply(store):
        for x in store["sessions"]:
            if x["id"] == session_id:
                x["group"] = new_gid
                x["peer"] = new_peer
                x["drift_handled_at"] = entry["at"]
        store.setdefault("drift_log", []).append(entry)
        store["drift_log"] = store["drift_log"][-200:]
    ST.update("sessions", apply)
    return entry
