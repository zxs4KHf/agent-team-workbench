import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arena_core as C  # noqa: E402
import arena_join_queue as JQ  # noqa: E402
import arena_store as ST  # noqa: E402
import arena_upstream as UP  # noqa: E402
import arena_wake as WAKE  # noqa: E402

# arena 控制面 + 中继入口（9091）：/api/arena/* 给 Workbench 页面用，/v1/* 给外部 Agent Harness 用
RELAY_PATHS = ("/v1/chat/completions", "/chat/completions", "/v1/messages", "/messages", "/v1/messages/count_tokens", "/v1/responses", "/responses", "/v1/completions", "/completions", "/v1/embeddings", "/embeddings")
HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization", "proxy-authenticate", "host", "content-length", "authorization", "x-api-key"}


class Handler(BaseHTTPRequestHandler):
    server_version = "ArenaControl/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    # ---------- 基础 ----------
    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def read_json(self) -> dict:
        raw = self.read_body()
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return {}

    def bearer(self) -> str:
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return (self.headers.get("x-api-key") or "").strip()

    # ---------- 路由 ----------
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/health", "/api/arena/health"):
                return self.send_json({"ok": True, "upstream": UP.health(), "time": ST.now_text()})
            if u.path in ("/v1/models", "/models"):
                return self.relay_models()
            if u.path.startswith("/api/arena/"):
                return self.control_get(u.path[len("/api/arena/"):], q)
            return self.send_json({"error": "未知路径"}, 404)
        except UP.UpstreamError as e:
            return self.send_json({"error": str(e)}, e.status if e.status >= 400 else 502)
        except Exception as e:  # noqa: BLE001  接口层兜底
            return self.send_json({"error": str(e)}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            if u.path in RELAY_PATHS:
                return self.relay(u.path)
            if u.path.startswith("/api/arena/"):
                return self.control_post(u.path[len("/api/arena/"):], self.read_json())
            return self.send_json({"error": "未知路径"}, 404)
        except UP.UpstreamError as e:
            return self.send_json({"error": str(e)}, e.status if e.status >= 400 else 502)
        except Exception as e:  # noqa: BLE001
            return self.send_json({"error": str(e)}, 500)

    # ---------- 控制面 GET ----------
    def control_get(self, name, q):
        if name == "wake":  # 哪些 Agent 能自动唤醒（有绑定会话）+ 最近的发送状态
            return self.send_json({"bound": WAKE.bound_peers(), "status": WAKE.status()})
        if name == "overview":
            acc = C.public_accounts()
            sess = C.sessions_view()
            return self.send_json({"accounts": acc["summary"], "account_total": len(acc["accounts"]), "active_email": acc["active_email"], "sessions_total": len(sess["sessions"]), "families": sess["families"], "probe_count": sess["probe_count"], "routes": C.routes_view()["routes"], "keys": len([k for k in C.keys_view()["keys"] if not k.get("revoked")]), "stats": C.stats_view()["totals"], "jobs": [j for j in C.jobs_view()["jobs"] if j["status"] == "running"], "upstream": UP.health(), "synced_at": acc["synced_at"], "leaderboard_date": ST.load("leaderboard").get("date", "")})
        if name == "accounts":
            return self.send_json(C.public_accounts())
        if name == "sessions":
            return self.send_json(C.sessions_view())
        if name == "routes":
            return self.send_json(C.routes_view())
        if name == "keys":
            return self.send_json(C.keys_view())
        if name == "stats":
            return self.send_json(C.stats_view())
        if name == "probes":
            return self.send_json(C.probes_view(int(q.get("limit") or 200)))
        if name == "leaderboard":
            return self.send_json(C.leaderboard_view())
        if name == "jobs":  # 旧 generic JOBS（内存）+ 持久 join 队列作业合并返回，刷新/多标签读到同一份 join 状态
            legacy = C.jobs_view()["jobs"]
            return self.send_json({"jobs": legacy + JQ.jobs_view()["jobs"], "join_config": JQ.get_config()})
        if name == "join/jobs":
            return self.send_json(JQ.jobs_view(int(q.get("limit") or 100)))
        if name == "join/job":
            job = JQ.get_job(q.get("id", ""))
            return self.send_json(job or {"error": "作业不存在"}, 200 if job else 404)
        if name == "join/config":
            return self.send_json(JQ.get_config())
        if name == "groups":
            return self.send_json(C.groups_view())
        if name == "groups/key":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self.send_json({"error": "只允许本机查看"}, 403)
            return self.send_json({"key": C.group_key_plain(q.get("id", ""))})
        if name == "settings":
            s = ST.load("settings")
            s["register_password"] = "******" if s.get("register_password") else ""
            return self.send_json(s)
        return self.send_json({"error": "未知接口"}, 404)

    # ---------- 控制面 POST ----------
    def control_post(self, name, body):
        if name == "wake":  # 工作台「唤醒」：把唤醒语发进这个 Agent 绑定的会话；没绑定返回 bound=False（前端改为复制）
            try:
                return self.send_json(WAKE.wake(str(body.get("peer") or ""), str(body.get("text") or "")))
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
        if name == "sync":
            return self.send_json(C.start_full_sync())
        if name == "accounts/check":
            return self.send_json(C.start_check_all(body.get("emails") or None))
        if name == "accounts/import":
            rows = C.parse_account_lines(body.get("raw_text") or "")
            if not rows:
                return self.send_json({"error": "没有解析到账号，每行格式：邮箱 密码"}, 400)
            return self.send_json(C.start_import(body.get("raw_text") or ""))
        if name == "accounts/preview":
            return self.send_json({"rows": [{"email": r["email"], "has_password": bool(r["password"])} for r in C.parse_account_lines(body.get("raw_text") or "")]})
        if name == "accounts/register":
            count = max(1, min(int(body.get("count") or 1), 20))
            pwd = str(body.get("password") or ST.load("settings").get("register_password") or "")
            if not pwd:
                return self.send_json({"error": "需要统一注册密码，可在设置中保存"}, 400)
            return self.send_json(C.start_register(count, pwd))
        if name == "accounts/note":
            C.set_account_note(body.get("email") or "", str(body.get("note") or ""), body.get("tags") or [])
            return self.send_json({"ok": True})
        if name == "accounts/delete":
            UP.delete_account(body.get("email") or "")
            C.sync_accounts()
            return self.send_json({"ok": True})
        if name == "accounts/reset_cooldown":
            UP.reset_cooldown(body.get("email") or "")
            C.sync_accounts()
            return self.send_json({"ok": True})
        if name == "accounts/switch":
            UP.switch_account(body.get("email") or "")
            C.sync_accounts()
            return self.send_json({"ok": True})
        if name == "sessions/detect":
            ids = [str(x) for x in (body.get("session_ids") or []) if x]
            if not ids:
                return self.send_json({"error": "没有选择会话"}, 400)
            return self.send_json(C.start_detect(ids))
        if name == "sessions/delete":
            ids = [str(x) for x in (body.get("session_ids") or []) if x]
            if not ids:
                return self.send_json({"error": "没有选择会话"}, 400)
            return self.send_json(C.start_delete_sessions(ids))
        if name == "sessions/create":
            emails = [str(x) for x in (body.get("emails") or []) if x]
            if not emails:
                return self.send_json({"error": "没有选择账号"}, 400)
            return self.send_json(C.start_batch_create(emails, max(1, min(int(body.get("count") or 1), 10)), bool(body.get("auto_detect"))))
        if name == "sessions/flag":
            C.set_session_flags(body.get("session_id") or "", body.get("pinned"), body.get("note"))
            return self.send_json({"ok": True})
        if name == "routes/set":
            return self.send_json(C.set_route(str(body.get("model") or "").strip(), str(body.get("session_id") or "").strip()))
        if name == "routes/delete":
            return self.send_json(C.delete_route(str(body.get("model") or "").strip()))
        if name == "keys/new":
            return self.send_json(C.new_key(str(body.get("name") or ""), body.get("allowed_models") or []))
        if name == "keys/revoke":
            C.revoke_key(str(body.get("id") or ""))
            return self.send_json({"ok": True})
        if name == "groups/save":
            return self.send_json(C.save_group(body))
        if name == "groups/delete":
            C.delete_group(str(body.get("id") or ""))
            return self.send_json({"ok": True})
        if name == "groups/member":
            C.group_member_edit(str(body.get("id") or ""), str(body.get("session_id") or ""), bool(body.get("add", True)))
            return self.send_json({"ok": True})
        if name == "groups/reorder":
            C.reorder_groups([str(x) for x in (body.get("ids") or [])])
            return self.send_json({"ok": True})
        if name == "groups/rotate_key":
            return self.send_json(C.rotate_group_key(str(body.get("id") or "")))
        if name == "groups/verify":
            return self.send_json(C.verify_group(str(body.get("id") or "")))
        if name == "groups/purge":
            ids = C.group_session_ids(str(body.get("id") or ""))
            if not ids:
                return self.send_json({"error": "该组没有会话"}, 400)
            return self.send_json(C.start_delete_sessions(ids))
        if name == "sessions/purge_ungrouped":
            ids = C.ungrouped_session_ids(str(body.get("email") or ""))
            if not ids:
                return self.send_json({"error": "没有可清理的会话"}, 400)
            return self.send_json(C.start_delete_sessions(ids))
        if name == "sessions/purge_account":
            ids = [x["id"] for x in C.sessions_view()["sessions"] if x.get("account") == str(body.get("email") or "")]
            if not ids:
                return self.send_json({"error": "该账号没有会话"}, 400)
            return self.send_json(C.start_delete_sessions(ids))
        if name in ("groups/join", "join/enqueue"):
            # 旧路径保持参数/返回键兼容，但一律走持久队列：幂等键 {group, session_id, request_id}，同 group/session 在途去重
            try:
                return self.send_json(JQ.enqueue(str(body.get("id") or body.get("group") or ""), str(body.get("session_id") or ""), str(body.get("request_id") or ""),
                                                 peer=str(body.get("peer") or ""), workbench=str(body.get("workbench") or "http://127.0.0.1:8765"),
                                                 source={"origin": "single", "client": str(body.get("client") or "")}))
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
        if name == "join/cancel":
            try:
                return self.send_json(JQ.cancel(str(body.get("id") or "")))
            except ValueError as e:
                return self.send_json({"error": str(e)}, 409)
        if name == "join/config":
            return self.send_json(JQ.set_config(body.get("max_concurrency"), body.get("wait_rounds"), body.get("wait_step_sec")))
        if name == "join/preview":  # 只读：不发消息、不写 store
            ids = body.get("group_ids") or body.get("ids") or C.common_group_ids()
            try:
                return self.send_json(JQ.preview(ids, body.get("extra") or {}, str(body.get("workbench") or "http://127.0.0.1:8765")))
            except ValueError as e:  # e5de/B1：workbench 等输入非法 → 400，文案不含输入
                return self.send_json({"error": str(e)}, 400)
        if name == "join/confirm":
            try:
                return self.send_json(JQ.confirm(str(body.get("preview_token") or ""), str(body.get("request_id") or ""), body.get("selections"),
                                                 workbench=str(body.get("workbench") or "http://127.0.0.1:8765"), source={"client": str(body.get("client") or "")}))
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
        if name == "groups/common":  # 常用组白名单：只认用户显式列表，不从 tier 推导
            if isinstance(body.get("ids"), list):
                return self.send_json(C.set_common_groups(body.get("ids")))
            if body.get("id"):
                cur = set(C.common_group_ids())
                (cur.add if body.get("common") else cur.discard)(str(body["id"]))
                return self.send_json(C.set_common_groups(sorted(cur)))
            return self.send_json({"error": "需要 ids 列表或 id+common"}, 400)
        if name == "leaderboard/refresh":
            return self.send_json(C.start_leaderboard())
        if name == "settings":
            def apply(s):
                for k in ("upstream", "leaderboard_source", "probe_prompt", "probe_ttl_hours", "cooldown_minutes", "register_password"):
                    if k in body and body[k] != "******":
                        s[k] = body[k]
            ST.update("settings", apply)
            return self.control_get("settings", {})
        return self.send_json({"error": "未知接口"}, 404)

    # ---------- 中继 ----------
    def relay_models(self):
        token = self.bearer()
        auth = C.verify_key(token)
        if not auth["ok"] and self.client_address[0] not in ("127.0.0.1", "::1"):
            return self.send_json({"error": {"message": auth["error"], "type": "authentication_error"}}, 401)
        data = UP.call("GET", "/v1/models")
        return self.send_json(data)

    def relay(self, path):
        raw = self.read_body()
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            payload = {}
        model = str(payload.get("model") or "")
        auth = C.verify_key(self.bearer(), model)
        if not auth["ok"]:
            return self.send_json({"error": {"message": auth["error"] + "。在工作台「路由」页生成 API Key 后填入客户端。", "type": "authentication_error"}}, 401)
        started = time.time()
        route = C.resolve_model(model)
        if route.get("kind") == "group":
            if route.get("error"):
                return self.send_json({"error": {"message": route["error"], "type": "invalid_request_error"}}, 503)
            sess = route["session"]
            alias = f"{model}@{sess['id'][:8]}"
            try:
                C.ensure_upstream_route(alias, sess["id"])
            except UP.UpstreamError as e:
                return self.send_json({"error": {"message": f"上游路由失败：{e}", "type": "api_error"}}, 502)
            payload["model"] = alias
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
        headers["Content-Type"] = self.headers.get("Content-Type") or "application/json"
        resp = UP.open_stream("POST", path, raw, headers)
        status = getattr(resp, "status", None) or getattr(resp, "code", 502)
        ctype = resp.headers.get("Content-Type") or "application/json"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        streaming = "text/event-stream" in ctype
        out_tokens = 0
        in_tokens = 0
        ok = 200 <= int(status) < 300
        if streaming:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                while True:
                    chunk = resp.read1(8192) if hasattr(resp, "read1") else resp.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(f"{len(chunk):x}\r\n".encode("ascii") + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                ok = False
        else:
            body = resp.read()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            try:
                data = json.loads(body.decode("utf-8")) or {}
                usage = data.get("usage") or {}
                in_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                out_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
                if route.get("kind") == "group":
                    # 每次任务后复核：上游若在响应里报出真实模型名，与组不符就记漂移并触发重新归组
                    C.note_observed_model(route["session"]["id"], str(data.get("model") or ""), model)
            except (ValueError, AttributeError):
                pass
        C.touch_key(auth["id"])
        C.record_request({"at": ST.now_text(), "key": auth["name"], "path": path, "model": model or "auto", "session_id": (route.get("session") or {}).get("id", ""), "status": int(status), "ok": ok, "ms": int((time.time() - started) * 1000), "stream": streaming, "input_tokens": in_tokens, "output_tokens": out_tokens})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9091)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    os.makedirs(ST.DATA, exist_ok=True)
    JQ.jobs_view()  # 启动即恢复持久 join 队列：sending/waiting_peer 标 unconfirmed（不重发），queued 继续
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"arena 控制面 http://{a.host}:{a.port}  upstream={UP.base()}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
