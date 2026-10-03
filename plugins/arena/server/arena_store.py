import json
import os
import threading
import time

# 控制面状态落盘：每类数据一个 JSON 文件，写入走临时文件后替换，保证进程被杀时文件不残缺
ROOT = os.path.dirname(os.path.abspath(__file__))
# 作为工作台插件运行时，main.py 把数据目录指到 .runtime/plugins/arena/（不入库，ADR 0014）；单独运行仍用 server/data
DATA = os.environ.get("ARENA_DATA") or os.path.join(ROOT, "data")
LOCK = threading.RLock()

FILES = {
    "accounts": "accounts.json",
    "sessions": "sessions.json",
    "routes": "routes.json",
    "keys": "keys.json",
    "probes": "probes.json",
    "leaderboard": "leaderboard.json",
    "stats": "stats.json",
    "settings": "settings.json",
}

DEFAULTS = {
    "accounts": {"accounts": [], "active_email": "", "synced_at": ""},
    "sessions": {"sessions": [], "synced_at": ""},
    "routes": {"routes": {}, "updated_at": ""},
    "keys": {"keys": []},
    "probes": {"records": []},
    "leaderboard": {"boards": {}, "fetched_at": ""},
    "stats": {"requests": [], "totals": {"count": 0, "ok": 0, "fail": 0, "input_tokens": 0, "output_tokens": 0}},
    "settings": {"upstream": "http://127.0.0.1:9090", "leaderboard_source": "https://raw.githubusercontent.com/oolong-tea-2026/arena-ai-leaderboards/main/data", "probe_prompt": "只回答数字 1，不要补充其他文字。", "probe_ttl_hours": 24, "cooldown_minutes": 30, "register_password": ""},
}


def path_of(kind: str) -> str:
    return os.path.join(DATA, FILES[kind])


def load(kind: str) -> dict:
    with LOCK:
        p = path_of(kind)
        if not os.path.isfile(p):
            return json.loads(json.dumps(DEFAULTS[kind]))
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return json.loads(json.dumps(DEFAULTS[kind]))
        merged = json.loads(json.dumps(DEFAULTS[kind]))
        merged.update(data if isinstance(data, dict) else {})
        return merged


def save(kind: str, data: dict) -> None:
    with LOCK:
        os.makedirs(DATA, exist_ok=True)
        p = path_of(kind)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)


def update(kind: str, fn):
    with LOCK:
        data = load(kind)
        result = fn(data)
        save(kind, data)
        return result


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
