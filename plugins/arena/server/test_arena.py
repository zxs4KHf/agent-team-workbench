import json
import sys
import urllib.request

# 控制面冒烟测试：只读接口 + 鉴权拒绝，不消耗上游额度
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9091"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, body, headers=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


fails = []
h = get("/health")
if not h.get("ok"):
    fails.append("health")
if not h.get("upstream", {}).get("ok"):
    fails.append("upstream 9090 不可达")
for name in ("overview", "accounts", "sessions", "routes", "keys", "stats", "leaderboard", "jobs"):
    try:
        get("/api/arena/" + name)
    except Exception as e:  # noqa: BLE001
        fails.append(f"{name}: {e}")
code, _ = post("/v1/chat/completions", {"model": "auto", "messages": []})
if code != 401:
    fails.append(f"无 Key 应 401，实际 {code}")
code, body = post("/api/arena/accounts/preview", {"raw_text": "a@b.com pw\nbad line"})
if code != 200 or len(body.get("rows", [])) != 1:
    fails.append("账号解析预览")
acc = get("/api/arena/accounts")
if acc["accounts"] and any(k in acc["accounts"][0] for k in ("password", "cookies", "auth_token", "password_sealed")):
    fails.append("账号接口回显了凭据字段")
print("PASS" if not fails else "FAIL")
for f in fails:
    print(" -", f)
sys.exit(1 if fails else 0)
