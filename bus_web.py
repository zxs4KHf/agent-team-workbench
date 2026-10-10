import argparse
import subprocess
import base64
import binascii
import datetime as dt
import mimetypes
import threading
import time
import secrets
import json
import re
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bus_core as B  # noqa: E402
import bus_projects as P  # noqa: E402
import bus_settings as SET  # noqa: E402
import named_workspace_io as W  # noqa: E402  shares the credential deny-list with the MCP file tools
import bus_events as E  # noqa: E402  events-v1 事件记录（docs/adr/0003）
import bus_feedback as FB  # noqa: E402  体验反馈
import bus_intake as IN  # noqa: E402  外部来源收件箱
import bus_review as RV  # noqa: E402  立即派审查（催审 / 全部催审）
import bus_reassign as RA  # noqa: E402  一键改派被占着不动的任务
import bus_history as HIST  # noqa: E402  任务历史只读分页（3847）
import bus_plugins as PL  # noqa: E402  插件管理（docs/PLUGINS.md）
import bus_plugin_jobs as PJ  # noqa: E402  插件任务派发
import bus_wake as WK  # noqa: E402  唤醒掉线的 Agent
from urllib.parse import unquote as _unquote  # noqa: E402

WEB = os.path.join(B.ROOT, "web")
V2 = os.path.join(WEB, "v2")
V2_INDEX = os.path.join(V2, "index.html")
V2_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json"}
SSE_PING_SEC = 15
WATCH_DIRS = ()  # 在 main 里按 B/P 常量填充

# 请求来源校验（2026-09-28 安全修复）。之前任何网页都能往 127.0.0.1:8765 发 POST——
# 你浏览的某个网站可以悄悄替你派任务、改设置（CSRF），或借 DNS 重绑定读取工作台数据。
# 现在：Host 必须是本机名字；带 Origin 的写请求必须同源。命令行工具和插件不带 Origin，不受影响。
LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}
ALLOWED_HOSTS = set(LOOPBACK_NAMES) | {h.strip().lower() for h in os.environ.get("WORKBENCH_ALLOWED_HOSTS", "").split(",") if h.strip()}


def _hostname(netloc: str) -> str:
    netloc = (netloc or "").strip().lower()
    if netloc.startswith("["):
        return netloc.split("]", 1)[0] + "]"
    return netloc.rsplit(":", 1)[0] if netloc.count(":") == 1 else netloc


def request_allowed(method: str, host: str, origin: str):
    """返回 (是否放行, 拒绝原因)。"""
    if host and _hostname(host) not in ALLOWED_HOSTS:
        return False, "Host 不在允许列表（局域网访问请设置环境变量 WORKBENCH_ALLOWED_HOSTS）"
    if method != "GET" and origin:
        if origin == "null" or urlparse(origin).netloc.lower() != (host or "").strip().lower():
            return False, "跨站请求被拒绝"
    return True, ""


def _watch_signature() -> tuple:
    # 目录 mtime 加文件数：peers 心跳、inbox/outbox 变化、commands 与 projects 改动都会体现
    sig = []
    dirs = list(WATCH_DIRS)
    projects_dir = os.path.join(B.ROOT, "projects")
    try:
        dirs.extend(os.path.join(projects_dir, n) for n in os.listdir(projects_dir))
    except OSError:
        pass
    for d in dirs:
        try:
            names = os.listdir(d)
            latest = max((os.path.getmtime(os.path.join(d, n)) for n in names), default=0)
            sig.append((len(names), round(latest, 2)))
        except OSError:
            sig.append(None)
    return tuple(sig)
TEXT_EXT = (".md", ".txt", ".json", ".toml", ".cs", ".py", ".yaml", ".yml", ".csv")


ATTACH_ROOT = os.path.join(B.ROOT, "attachments")
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_REQUEST_BYTES = 12 * 1024 * 1024
RAW_TYPES = {
    ".txt": "text/plain; charset=utf-8", ".json": "application/json; charset=utf-8", ".py": "text/plain; charset=utf-8",
    ".js": "text/plain; charset=utf-8", ".cs": "text/plain; charset=utf-8", ".yaml": "text/plain; charset=utf-8",
    ".yml": "text/plain; charset=utf-8", ".toml": "text/plain; charset=utf-8", ".log": "text/plain; charset=utf-8",
    ".pdf": "application/pdf", ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".md": "text/markdown; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".csv": "text/csv; charset=utf-8",
}
UPLOAD_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
# 浏览器 / 系统对 JPEG 的别名：查白名单前用这张显式表归一化，不做字符串裁剪；HEIC / AVIF / TIFF / SVG 不在任何表里，一律拒
UPLOAD_MIME_ALIAS = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg", "image/jfif": "image/jpeg"}
# 本地文件 file.type 可能为空：按文件名扩展名推定声明类型，再交给文件头校验
UPLOAD_EXT_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jfif": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
UPLOAD_REJECT_HINT = "仅 PNG/JPEG/GIF/WebP；HEIC 请先转 JPG"


def _sniff(raw: bytes) -> str:
    # 按文件头识别图片格式，返回落盘扩展名；识别不出返回空串
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if raw[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return ""


# 合并稿「已交付」的明示写法：只在行首（可带列表符 / 加粗 / 「结论：」前缀）匹配，且只看「## 验证依据」之前的正文；
# 叙述性的「……已落地……」出现在句中不算（22:50 c695 采纳时被这种句子误判成 released）
IMPLEMENTED_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?(?:结论[:：]\s*)?(?:本任务|本轮|本方案|本次)?(?:已实施并上线|已上线|已落地并合并|已合并进主线|无需实施|不需要改动|本任务只定方案[^\n]*已排)(?P<rest>[^\n]*)$", re.M)
# 079b：一句话后面还带着疑问、转折、「仍需 / 还要 / 待」时，说明还有活没做，不能据此把决策标成已落地
_NOT_DONE_TAIL = re.compile(r"[?？]|但|然而|仍|还需|还要|尚未|待|除了|以外|之外")


def declares_implemented(txt: str) -> bool:
    """缺「最终方案」段时的兜底：全文有一行完整、明确地声明整件事已实施 / 不需要改动，才算。
    不认：引用块（> 开头）和代码块里的句子、疑问句、带转折或「仍需」的半句（「不需要改动前端，但仍需要实施后端修复」）。"""
    head = (txt or "").split("## 验证依据", 1)[0]
    head = re.sub(r"^```.*?^```", "", head, flags=re.S | re.M)   # 代码块里的示例句不算
    for m in IMPLEMENTED_LINE_RE.finditer(head):
        rest = m.group("rest").strip().strip("*").strip()
        # 关键词后面要么结束，要么紧跟标点（「不需要改动前端」只说了一部分，不算）；剩下的不能有疑问、转折、「仍需」
        if (not rest or rest[0] in "。.，,；;！!（(:：、") and not _NOT_DONE_TAIL.search(rest):
            return True
    return False


# 热重载白名单：业务模块按依赖序重载，监听 socket 与已建立连接（SSE）不受影响。
# 不在名单里的：锁注册表（bus_locks / bus_implementation_lock / bus_project_store，重载会换掉进程内
# 锁对象、破坏互斥）、并发信号量（runtime_exec）、插件守护状态（bus_plugins）、进程本体（bus_web / bus_mcp）。
# 这些文件的变更走排水式重启（/api/admin/restart 或守护拉起）。
HOT_RELOAD_MODULES = ("bus_lead_notify", "bus_core", "bus_settings", "bus_events",
                      "bus_intake", "bus_feedback", "bus_lineage", "bus_projects")


def hot_reload() -> dict:
    import importlib
    reloaded, errors = [], []
    for name in HOT_RELOAD_MODULES:
        mod = sys.modules.get(name)
        try:
            if mod is None:
                mod = importlib.import_module(name)  # 懒加载模块先装载，再统一按新代码重建
            importlib.reload(mod)  # reload 原地重建模块对象，本进程所有旧引用立即看到新代码
            reloaded.append(name)
        except Exception as exc:  # noqa: BLE001  单模块失败不拖垮其余，但必须如实上报
            errors.append({"module": name, "error": f"{type(exc).__name__}: {exc}"})
    return {"ok": not errors, "reloaded": reloaded, "errors": errors,
            "note": "锁/并发底座与 web/mcp 进程本体不在热重载范围；这些文件的变更需排水式重启"}


def _delayed_restart(host: str, port: int, no_plugins: bool):
    # 先让响应发出去，再拉新进程（新进程启动时端口还被本进程占着，靠 bus_web 启动时的绑定重试等我们退出）
    time.sleep(0.5)
    argv = [sys.executable, "-X", "utf8", os.path.abspath(__file__), "--port", str(port), "--host", host]
    if no_plugins:
        argv.append("--no-plugins")
    log_path = os.path.join(B.ROOT, "console", "workbench_%d.log" % port)
    out = open(log_path, "ab", buffering=0)
    try:
        flags = (0x08000000 | 0x00000200 | 0x01000000) if os.name == "nt" else 0  # NO_WINDOW|NEW_GROUP|BREAKAWAY
        subprocess.Popen(argv, cwd=B.ROOT, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                         creationflags=flags, close_fds=True)
    finally:
        out.close()
    os._exit(0)


class PathError(ValueError):
    """路径层的可区分错误：带 code 与 HTTP 状态，避免被 do_GET 末尾的兜底 except 统一吞成 400。"""

    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(message)


def _extra_roots() -> list:
    # 实现已收到 bus_projects.extra_roots()（全仓只留一套），这里只保留旧名转发
    return P.extra_roots()


def safe_workspace_path(rel: str) -> str:
    """HTTP 层入口：解析统一走 P.resolve_workspace_path（全仓唯一实现），
    这里只负责拒绝名单与错误分级。字面优先由解析器保证，兼容分支命中会打 legacy_prefix 日志。"""
    raw = str(rel or "").replace("\\", "/").lstrip("/")
    try:
        full, source = P.resolve_workspace_path(raw)
    except ValueError as exc:
        raise PathError("path_out_of_scope", str(exc), 403)
    # Same deny-list as ws_read: tokens, settings.json, .env, keys are never served over HTTP.
    # 字面写法与剥前缀写法各查一次——兼容分支不能成为绕过拒绝名单的通道。
    for probe in dict.fromkeys([raw, P.normalize_merge_path(raw)]):
        if not probe:
            continue
        try:
            W.resolve(B.WORKSPACE, probe)
        except PermissionError as exc:
            raise PathError("path_denied", str(exc), 403)
        except Exception:
            pass  # non-credential errors (missing file, directory) are handled by the caller
    if source == "legacy_prefix":
        # 兼容层必须有声音：字面路径没命中、靠剥前缀/换根才读到，属降级，应回填规范路径
        print("[path] 降级命中：rel=%r -> %s（path_source=legacy_prefix，建议回填规范路径）"
              % (raw, full), file=sys.stderr)
    return full


def list_workspace_files(prefix: str) -> list:
    # 供上下文文件选择器使用，只列文本文件，跳过依赖与缓存目录
    base = safe_workspace_path(prefix or ".")
    skip = {"node_modules", ".git", "Library", "Temp", "obj", "bin", "__pycache__", "peers", "inbox", "outbox"}
    out = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        for f in files:
            if f.endswith(TEXT_EXT):
                out.append(os.path.relpath(os.path.join(root, f), B.WORKSPACE).replace("\\", "/"))
        if len(out) > 3000:
            break
    return sorted(out)


def app_info() -> dict:
    """app.json：{"name", "version", "feedback": {"github_repo": "owner/repo", "labels": "..."}}。缺失或损坏给默认值，不挡页面。"""
    info = {"name": "Agent 协作台", "version": "0.0.0", "feedback": {"github_repo": "", "labels": ""}}
    try:
        with open(os.path.join(B.ROOT, "app.json"), encoding="utf-8-sig") as fh:
            data = json.load(fh) or {}
        info.update({k: data[k] for k in ("name", "version") if isinstance(data.get(k), str)})
        fb = data.get("feedback") or {}
        repo = str(fb.get("github_repo") or "").strip()
        info["feedback"] = {"github_repo": repo if re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", repo) else "",
                            "labels": str(fb.get("labels") or "")}
    except (OSError, ValueError):
        pass
    return info


class Handler(BaseHTTPRequestHandler):
    server_version = "BusWeb/1"

    def log_message(self, fmt, *args):
        return

    def guard_request(self, method: str) -> bool:
        ok, why = request_allowed(method, self.headers.get("Host", ""), self.headers.get("Origin", ""))
        if not ok:
            # 先把请求体读掉再拒绝：Windows 对带未读数据的 socket 关闭时会发 RST，
            # 客户端拿到的就是"连接中断"而不是可读的 403（审查 d939）
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 1 << 20)
                if n:
                    self.rfile.read(n)
            except (ValueError, OSError):
                pass
            self.close_connection = True
            self.send_json({"error": why}, 403)
        return ok

    def guard_state(self) -> dict:
        st = B.read_json(os.path.join(B.ROOT, "console", "guard_status.json"), None) or {}
        # 判活统一走 heartbeat_age（ADR 0010）：解析失败按 1e9=失联，未来戳超过 CLOCK_SKEW_SEC 标 skewed 不判活
        age, skewed = B.heartbeat_age(st.get("updated"))
        st["alive"] = (not skewed) and age < 3 * int(st.get("interval_sec", 15)) + 10
        return st
    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, ctype="text/plain; charset=utf-8", code=200):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def send_bytes(self, body, ctype="application/octet-stream", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def handle_upload(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_UPLOAD_REQUEST_BYTES:
            return self.send_json({"error": "上传请求超过限制", "mime": ""}, 413)  # 还没读请求体，收不到 MIME
        received = ""
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            # 前端 v2 发 type、旧客户端发 mime，两个字段名都接受；received 原样保留用于回显
            received = str(payload.get("mime") or payload.get("type") or "").lower().split(";", 1)[0].strip()
            mime = UPLOAD_MIME_ALIAS.get(received, received)
            if mime in ("", "application/octet-stream"):
                # 空 MIME 与 octet-stream（系统不认识的本地文件）一样：按文件名扩展名推定，再交给文件头校验
                mime = UPLOAD_EXT_MIME.get(os.path.splitext(str(payload.get("name") or ""))[1].lower(), "")
            if mime not in UPLOAD_TYPES:
                raise ValueError(f"不支持的图片类型 {received or '空'}（{UPLOAD_REJECT_HINT}）")
            try:
                raw = base64.b64decode(payload.get("data") or "", validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("base64 数据无效（请确认已剥离 data:...;base64, 前缀）")
            if not raw or len(raw) > MAX_UPLOAD_BYTES:
                return self.send_json({"error": "单张图片大小必须在 8 MB 以内", "mime": received}, 413)
            ext = _sniff(raw)  # 以文件头定扩展名，不信任客户端自报的 MIME
            if not ext:
                raise ValueError(f"文件内容与声明的 {received or '空'} 不符，文件头不是 PNG/JPEG/GIF/WebP（{UPLOAD_REJECT_HINT}）")
        except (ValueError, TypeError, binascii.Error, json.JSONDecodeError) as error:
            # 具体原因直接回给用户，附上服务端实际收到的 MIME，外层不再套无信息的包装文案
            return self.send_json({"error": str(error), "mime": received}, 400)
        day = dt.datetime.now().strftime("%Y%m%d")
        folder = os.path.join(ATTACH_ROOT, day)
        os.makedirs(folder, exist_ok=True)
        name = secrets.token_hex(12) + ext
        full = os.path.join(folder, name)
        with open(full, "xb") as stream:
            stream.write(raw)
        rel = os.path.relpath(full, B.WORKSPACE).replace(os.sep, "/")
        return self.send_json({"path": rel, "mime": mime, "received": received, "bytes": len(raw)})  # mime = 归一化后的白名单类型，received = 客户端原话

    def send_raw(self, rel, download=False):
        # 工作区内任意文件：已知类型按其 MIME 内联，未知类型一律以附件下载；download=1 强制下载
        full = safe_workspace_path(rel)
        if not os.path.isfile(full):
            raise FileNotFoundError(f"文件不存在：{rel}（解析为 {full}）")
        ext = os.path.splitext(full)[1].lower()
        ctype = RAW_TYPES.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(os.path.getsize(full)))
        self.send_header("Cache-Control", "no-store")
        if download or ext not in RAW_TYPES:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(os.path.basename(full)))
        self.end_headers()
        with open(full, "rb") as stream:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def stamp_assets(self, html: str) -> str:
        # 静态资源加版本参数（目录内最新 mtime），热更新后普通刷新即可生效
        try:
            latest = 0
            for d, _, fs in os.walk(V2):
                for name in fs:
                    latest = max(latest, int(os.path.getmtime(os.path.join(d, name))))
            return re.sub(r'((?:src|href)="/v2/[^"?]+)"', lambda m: f'{m.group(1)}?v={latest}"', html)
        except OSError:
            return html
    def send_v2_static(self, rel):
        full = os.path.normpath(os.path.join(V2, rel))
        if not full.startswith(os.path.normpath(V2)) or not os.path.isfile(full):
            return self.send_text("not found", code=404)
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as stream:
            body = stream.read()
        self.send_response(200)
        self.send_header("Content-Type", V2_TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_events(self):
        # SSE：目录签名变化即推一条 change，空闲时每 15 秒 ping；前端收到 change 后拉 /api/state
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last = _watch_signature()
        last_ping = time.time()
        try:
            self.wfile.write(b"event: ping\ndata: {}\n\n")
            self.wfile.flush()
            while True:
                time.sleep(1)
                sig = _watch_signature()
                if sig != last:
                    last = sig
                    self.wfile.write(b"data: {\"kind\":\"change\"}\n\n")
                    self.wfile.flush()
                    last_ping = time.time()
                elif time.time() - last_ping >= SSE_PING_SEC:
                    self.wfile.write(b"event: ping\ndata: {}\n\n")
                    self.wfile.flush()
                    last_ping = time.time()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return

    def open_local(self, rel, reveal=False):
        # 用本机默认程序打开（浏览器与 bus_web 同机时才有意义）；reveal 在资源管理器中定位
        full = safe_workspace_path(rel)
        if not os.path.exists(full):
            raise ValueError("文件不存在")
        if reveal:
            subprocess.Popen(["explorer", "/select,", full])
        else:
            os.startfile(full)
        return {"opened": full, "reveal": reveal}

    def file_info(self, rel):
        # 前端据此决定预览方式：markdown / image / html / csv / pdf / video / audio / text 可内嵌，其余给下载与本机打开
        full = safe_workspace_path(rel)
        if not os.path.exists(full):
            raise FileNotFoundError(f"文件不存在：{rel}（解析为 {full}）")
        ext = os.path.splitext(full)[1].lower()
        text_like = TEXT_EXT + (".log", ".js", ".ts", ".css", ".xml", ".ini", ".bat", ".ps1", ".sh", ".jsonl")
        kind = ("markdown" if ext == ".md" else "image" if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg") else
                "html" if ext in (".html", ".htm") else "csv" if ext == ".csv" else "pdf" if ext == ".pdf" else
                "video" if ext in (".mp4", ".webm") else "audio" if ext in (".mp3", ".wav") else
                "office" if ext in (".docx", ".xlsx", ".pptx") else "text" if ext in text_like else "binary")
        return {"path": rel, "abs": full, "ext": ext, "kind": kind, "size": os.path.getsize(full),
                "mtime": dt.datetime.fromtimestamp(os.path.getmtime(full)).astimezone().replace(microsecond=0).isoformat(),
                "preview": kind in ("markdown", "image", "html", "csv", "pdf", "video", "audio", "text"),
                "same_machine": self.client_address[0] in ("127.0.0.1", "::1")}

    def proxy_plugin(self, base, method, path, body):
        """插件声明的 /api/<id>/* 转发到插件自己的服务（bus_plugins.proxy_target，ADR 0014）。"""
        import urllib.error
        import urllib.request
        req = urllib.request.Request(base + path, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return self.send_bytes(r.read(), r.headers.get("Content-Type") or "application/json; charset=utf-8", r.status)
        except urllib.error.HTTPError as e:
            return self.send_bytes(e.read(), e.headers.get("Content-Type") or "application/json; charset=utf-8", e.code)
        except (urllib.error.URLError, OSError):
            pid = path.split("/")[2]
            return self.send_json({"error": f"插件 {pid} 的服务没有响应（{base}）。到「扩展与来源 → 插件」看它的状态和日志。",
                                   "plugin_down": True}, 503)

    @staticmethod
    def _peer_token(peer: str) -> str:
        if not SET.reveal_token(peer):
            SET.save({"peer_tokens": {peer: "__rotate__"}})
        return SET.reveal_token(peer)

    def do_GET(self):
        u = urlparse(self.path)
        if not self.guard_request("GET"):
            return
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/" or u.path == "/index.html":
                with open(V2_INDEX, encoding="utf-8") as f:
                    return self.send_text(self.stamp_assets(f.read()), "text/html; charset=utf-8")
            if u.path.startswith("/v2/"):
                return self.send_v2_static(u.path[4:])
            if u.path in ("/hub", "/hub.html"):
                with open(os.path.join(V2, "hub.html"), encoding="utf-8") as f:
                    return self.send_text(self.stamp_assets(f.read()), "text/html; charset=utf-8")
            if u.path.startswith("/plugins/"):
                parts = _unquote(u.path).split("/", 3)
                full = PL.page_path(parts[2], parts[3] if len(parts) > 3 else "") if len(parts) > 2 else ""
                if not full:
                    return self.send_text("not found", code=404)
                with open(full, "rb") as fh:
                    return self.send_bytes(fh.read(), V2_TYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream"))
            if u.path == "/api/app":
                # 名称、版本、反馈仓库（app.json，入库）：前端页脚显示版本，反馈面板据此决定是否给「发到 GitHub」
                return self.send_json(app_info())
            if u.path == "/api/ping":
                return self.send_json({"ok": True})
            if u.path == "/api/ext/summary":
                return self.send_json({"intake": IN.counts(), "feedback": FB.counts(),
                                       "plugins": {"running": sum(1 for p in PL.discover() if p["running"])}})
            if u.path == "/api/plugins":
                return self.send_json({"plugins": PL.discover()})
            if u.path == "/api/plugin-jobs/agents":
                return self.send_json(PJ.agents())
            if u.path == "/api/plugin-jobs/options":
                try:
                    return self.send_json(PJ.options(q.get("source"), PL.discover()))
                except PJ.JobError as exc:
                    return self.send_json({"error": str(exc)}, exc.status)
            if u.path == "/api/plugin-jobs":
                try:
                    return self.send_json(PJ.lookup(q.get("source"), q.get("request_id")))
                except PJ.JobError as exc:
                    return self.send_json({"error": str(exc)}, exc.status)
            if u.path == "/api/plugins/log":
                return self.send_json({"log": PL.log_tail(q.get("id", ""))})
            if u.path == "/api/intake":
                return self.send_json({"items": IN.items(q.get("status", "")), "counts": IN.counts()})
            if u.path == "/api/feedback":
                return self.send_json({"items": FB.items(q.get("status", "")), "counts": FB.counts()})
            if u.path == "/api/event-log":
                return self.send_json(E.read(q.get("cursor", ""), int(q.get("limit") or 200)))
            if u.path == "/api/event-log/recent":
                return self.send_json({"events": E.recent(int(q.get("n") or 50))})
            if u.path == "/api/inbox/history":
                # 任务历史（3847 重落地）。命名保留 /api/inbox/history 以兼容旧设计，但它不属于外部来源收件箱
                # （那是 bus_intake / hub#inbox），这里只读 console/done 与 decisions 的终态。
                # 边界：本机只读工具。Host 允许列表可被 WORKBENCH_ALLOWED_HOSTS 放宽，因此这里额外要求来源是本机回环，
                # 否则一旦被反代暴露，它就是可翻页导出全部历史（含 result 摘要、决策标题、merge_path）的只读面。
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self.send_json({"error": "history_local_only", "message": "任务历史只允许本机访问"}, 403)
                try:
                    return self.send_json(HIST.history_page(q.get("project", ""), q.get("filter", "all"),
                                                            q.get("cursor", ""), q.get("limit", HIST.DEFAULT_LIMIT)))
                except HIST.CursorError as e:
                    return self.send_json({"error": e.code, "message": str(e)}, e.status)
            if u.path == "/api/events":
                return self.send_events()
            if u.path == "/api/state":
                return self.send_json(P.full_state())
            if u.path == "/api/priority":
                return self.send_json(B.priority())
            if u.path == "/api/settings":
                out = SET.describe(B.WORKSPACE)
                out["guard"] = self.guard_state()
                return self.send_json(out)
            if u.path == "/api/mcp/health":
                return self.send_json(SET.mcp_health())
            if u.path == "/api/guard":
                return self.send_json(self.guard_state())
            if u.path == "/api/settings/peer_token":
                # Reveal one peer token in full so the user can paste it into that agent's window.
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self.send_json({"error": "只允许本机查看"}, 403)
                return self.send_json({"peer": q.get("peer", ""), "token": SET.reveal_token(q.get("peer", ""))})
            if u.path == "/api/raw":
                return self.send_raw(q.get("path", ""), q.get("download") == "1")
            if u.path == "/api/fileinfo":
                return self.send_json(self.file_info(q.get("path", "")))
            if u.path == "/api/file":
                rel = q.get("path", "")
                full = safe_workspace_path(rel)
                if not full.endswith(TEXT_EXT):
                    raise ValueError("只允许读取文本文件")
                if not os.path.isfile(full):
                    # 合并稿走按文件名兜底（落在错误日期目录 / workbench/bus/log / 项目名前缀下的旧稿），找到就读
                    alt, _src = P.resolve_merge_path(rel)
                    if alt:
                        with open(alt, encoding="utf-8") as f:
                            return self.send_text(f.read())
                    # 不要裸 FileNotFoundError：告诉调用方找的是哪条相对路径、解析到了哪里
                    return self.send_json({"error": f"文件不存在：{rel}（解析为 {full}）",
                                           "code": "file_not_found", "path": rel}, 404)
                with open(full, encoding="utf-8") as f:
                    return self.send_text(f.read())
            if u.path == "/api/connect_link":
                # 链接直连：URL 里带 token，复制到宿主的 MCP 配置即可；只对本机回环返回
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self.send_json({"error": "只允许本机获取"}, 403)
                kind, peer = q.get("kind", "peer"), (q.get("peer") or "").strip()
                base = P.mcp_base()
                if kind == "controller":
                    tok_path = os.path.join(B.ROOT, "console", "mcp_token.txt")
                    with open(tok_path, encoding="utf-8") as fh:
                        tok = fh.read().strip()
                else:
                    if not peer:
                        return self.send_json({"error": "缺少 peer"}, 400)
                    tok = self._peer_token(peer)
                url = f"{P.bridge_base(base)}/mcp/{tok}"   # 只拼一段 /mcp/<令牌>（Bridge 地址只留协议 + 主机）
                lb = B.label_of(peer or ("controller" if kind == "controller" else "")) if (peer or kind == "controller") else ""
                return self.send_json({"url": url, "kind": kind, "peer": peer, "label": lb,
                                       "prompt": P.prompt_text(kind, peer or "<PEER>", q.get("model") or "<MODEL>", "", url, lb)})
            if u.path == "/api/wake":
                # 唤醒语（bus_wake）：带该 Agent 的接入地址（含令牌），所以和接入链接一样只对本机回环返回
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self.send_json({"error": "只允许本机获取"}, 403)
                peer = (q.get("peer") or "").strip()
                if not peer or peer == "controller":
                    return self.send_json({"error": "缺少 peer"}, 400)
                st = B.peer_state(peer) or {}
                link = f"{P.bridge_base(P.mcp_base())}/mcp/{self._peer_token(peer)}"
                model = q.get("model") or st.get("model") or ""
                return self.send_json({"peer": peer, "model": model, "last_seen": st.get("last_seen") or "",
                                       "text": WK.wake_text(peer, model, link, B.label_of(peer))})
            if u.path == "/api/wake/auto":
                # 哪些 Agent 能由插件直接发进会话（插件清单声明 wake 的；核心不认识具体插件，ADR 0014）
                return self.send_json({"peers": WK.auto_peers(PL.wake_targets())})
            if u.path == "/api/prompt":
                kind, peer = q.get("kind", "peer"), (q.get("peer") or "").strip()
                token = ""
                if kind == "controller":
                    token = "<Leader token：本机 console\\mcp_token.txt 的内容，自行粘贴，工作台不会显示>"
                elif peer and self.client_address[0] in ("127.0.0.1", "::1"):
                    # 自建 MCP 需要 Bearer：接入提示词生成时给该 peer 发一把专属 token（已有则复用）
                    if not SET.reveal_token(peer):
                        SET.save({"peer_tokens": {peer: "__rotate__"}})
                    token = SET.reveal_token(peer)
                # label 优先取 query（派发抽屉里改了还没落盘时的预览），否则读 peers/*.json
                lb = q.get("label") if q.get("label") is not None else (B.label_of(peer or ("controller" if kind == "controller" else "")) if (peer or kind == "controller") else "")
                return self.send_text(P.prompt_text(kind, peer or "<PEER>", q.get("model") or "<MODEL>", token, "", lb))
            if u.path == "/api/files":
                return self.send_json(list_workspace_files(q.get("prefix", "")))
            if u.path == "/api/task":
                tid = q.get("id", "")
                pr = P.task_progress(tid)
                answers = {}
                if pr:
                    for peer in pr["answered"]:
                        with open(os.path.join(B.OUTBOX, f"{tid}.{peer}.md"), encoding="utf-8") as f:
                            answers[peer] = f.read()
                else:
                    # 已归档任务从 log 目录取
                    for day in sorted(os.listdir(B.LOG), reverse=True):
                        d = os.path.join(B.LOG, day)
                        hit = [f for f in os.listdir(d) if f.startswith(tid + ".") and f.endswith(".md") and not f.endswith(".task.md") and not f.endswith(".merge.md")]
                        if hit:
                            for f in hit:
                                with open(os.path.join(d, f), encoding="utf-8") as fh:
                                    answers[f[len(tid) + 1:-3]] = fh.read()
                            pr = {"task_id": tid, "meta": B.read_json(os.path.join(d, f"{tid}.meta.json"), {}), "archived": True}
                            break
                return self.send_json({"progress": pr, "answers": answers})
            target = PL.proxy_target(u.path)
            if target:
                return self.proxy_plugin(target, "GET", u.path + ("?" + u.query if u.query else ""), None)
            return self.send_text("not found", code=404)
        except PathError as e:
            # 必须排在 Exception 之前：越界/拒绝名单各有状态码与 code，不再一律 400（980a 必改 3）
            return self.send_json({"error": str(e), "code": e.code}, e.status)
        except FileNotFoundError as e:
            return self.send_json({"error": str(e), "code": "file_not_found"}, 404)
        except Exception as e:
            return self.send_json({"error": str(e)}, 400)

    def handle_decision_ext(self, action: str, body: dict):
        import bus_lineage as L
        slug, tid = str(body.get("project") or ""), str(body.get("task_id") or "")
        if not slug or not tid:
            raise ValueError("project 与 task_id 必填")
        by = str(body.get("by") or "user")[:40]
        if action == "lineage":
            fields = {k: body[k] for k in L.LINEAGE_FIELDS if k in body}
            rec = L.set_lineage(slug, tid, by=by, **fields)
            return self.send_json({"ok": True, "task_id": tid, "lineage": L.lineage_of(rec)})
        if action == "handoff":
            rec = L.set_handoff(slug, tid, body.get("user_step", ""), body.get("reason", ""), body.get("verify", ""), body.get("evidence"), by=by)
            return self.send_json({"ok": True, "task_id": tid, "handoff": rec.get("handoff"), "bucket": L.landing_bucket(rec)})
        if action == "handoff-finish":
            rec = L.finish_handoff(slug, tid, str(body.get("result") or ""), by=by, note=str(body.get("note") or ""))
            return self.send_json({"ok": True, "task_id": tid, "handoff": rec.get("handoff"), "execution": rec.get("execution", "pending")})
        if action == "child":
            return self.send_json(L.spawn_child(slug, tid, body.get("reason", ""), kind=str(body.get("kind") or "redecide"), by=by, title=str(body.get("title") or "")))
        if action == "answer":
            # fb-486f8ac59c：概览内联表单提交。畸形入参由 answer_decision_needed 抛 ValueError → do_POST 统一转 400
            rec = P.answer_decision_needed(slug, tid, str(body.get("item_id") or ""),
                                           str(body.get("choice") or ""), str(body.get("text") or ""), by=by)
            P.invalidate_state()
            return self.send_json({"ok": True, **rec})
        if action == "resolve":
            return self.send_json(L.resolve_decision(slug, tid, by=by))
        if action == "brief-rewrite":
            # 第 6 步「看不懂」：排一条 brief_rewrite 指令给 Leader（带 task_id 与卡面原文）并标「待重写」；幂等，只排队不改 brief
            r = P.request_brief_rewrite(slug, tid, shown=str(body.get("shown") or ""), by=by)
            P.invalidate_state()
            return self.send_json(r)
        raise ValueError(f"未知 decision 操作 {action}")

    def do_POST(self):
        u = urlparse(self.path)
        if not self.guard_request("POST"):
            return
        try:
            if u.path == "/api/upload":
                return self.handle_upload()
            body = self.read_body()
            # ---- 扩展层（收件箱 / 体验反馈 / 插件），见 docs/PLUGINS.md ----
            if u.path == "/api/plugin-jobs":
                try:
                    return self.send_json(PJ.submit(body, PL.discover()))
                except PJ.JobError as exc:
                    return self.send_json({"error": str(exc)}, exc.status)
            if u.path == "/api/plugin-jobs/cancel":
                try:
                    return self.send_json(PJ.cancel(body, PL.discover()))
                except PJ.JobError as exc:
                    return self.send_json({"error": str(exc)}, exc.status)
            if u.path == "/api/wake/send":
                # 唤醒：先问声明了 wake 的插件能不能直接发进会话；都不能就把唤醒语还给前端复制
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self.send_json({"error": "只允许本机操作"}, 403)
                peer = str(body.get("peer") or "").strip()
                if not peer or peer == "controller":
                    return self.send_json({"error": "缺少 peer"}, 400)
                st = B.peer_state(peer) or {}
                link = f"{P.bridge_base(P.mcp_base())}/mcp/{self._peer_token(peer)}"
                text = WK.wake_text(peer, st.get("model") or "", link, B.label_of(peer))
                r = WK.send_via_plugins(peer, text, PL.wake_targets())
                return self.send_json({**r, "peer": peer, **({} if r["sent"] else {"text": text})})
            if u.path == "/api/intake":
                return self.send_json(IN.add(str(body.get("source") or ""), body.get("items") or []))
            if u.path == "/api/intake/decide":
                return self.send_json(IN.decide(str(body.get("id") or ""), str(body.get("action") or ""),
                                                str(body.get("project") or ""), str(body.get("note") or "")))
            if u.path.startswith("/api/peer_group/"):
                # 9e1e：group_hint 人工归类后端 API（suggest/confirm/manual/reject），前端二期再接
                action = u.path.rsplit("/", 1)[-1]
                peer_name, group = str(body.get("peer") or ""), str(body.get("group") or "")
                try:
                    if action == "suggest":
                        return self.send_json(B.suggest_group(peer_name))
                    if action == "confirm":
                        return self.send_json(B.confirm_group(peer_name, group))
                    if action == "manual":
                        return self.send_json(B.manual_group(peer_name, group))
                    if action == "reject":
                        return self.send_json(B.reject_group(peer_name, group))
                    return self.send_json({"error": f"未知操作 {action}（可选 suggest/confirm/manual/reject）"}, 404)
                except ValueError as exc:
                    return self.send_json({"error": str(exc)}, 400)
            if u.path == "/api/feedback":
                return self.send_json(FB.create(body.get("text", ""), str(body.get("kind") or "ux"), body.get("page", ""),
                                                body.get("view", ""), body.get("target", ""), body.get("viewport", ""),
                                                body.get("project", "")))
            if u.path == "/api/feedback/update":
                return self.send_json(FB.update(str(body.get("id") or ""), str(body.get("status") or ""), str(body.get("note") or "")))
            if u.path == "/api/plugins/toggle":
                return self.send_json(PL.toggle(str(body.get("id") or ""), bool(body.get("enabled"))))
            if u.path == "/api/plugins/config":
                return self.send_json(PL.set_config(str(body.get("id") or ""), body.get("config")))
            if u.path == "/api/settings":
                # Settings are applied immediately instead of queueing through the
                # controller: they are user configuration, not agent work, and a
                # queued config change would silently do nothing while offline.
                try:
                    saved = SET.save(body or {})
                    # Return the same shape as GET so the page can rerender from one payload.
                    out = SET.describe(B.WORKSPACE)
                    if saved.get("_notice"):
                        out["_notice"] = saved["_notice"]
                    return self.send_json(out)
                except ValueError as exc:
                    return self.send_json({"error": str(exc)}, 400)
            if u.path == "/api/mcp/restart":
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self.send_json({"error": "只允许本机操作"}, 403)
                return self.send_json(SET.mcp_restart())
            if u.path == "/api/admin/hot_reload":
                # 热重载业务模块：监听 socket、SSE 长连接与在途请求全部保留，新代码立即生效
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self.send_json({"error": "只允许本机操作"}, 403)
                result = hot_reload()
                P.invalidate_state()
                return self.send_json(result, 200 if result["ok"] else 500)
            if u.path == "/api/admin/restart":
                # 排水式自重启（bus_web.py 本体变更时用）：拉新进程后本进程退出。
                # 浏览器 EventSource 会自动重连；期间守护巡检最多记一次失败，低于拉起阈值
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self.send_json({"error": "只允许本机操作"}, 403)
                host, port, no_plugins = getattr(self.server, "restart_argv", ("127.0.0.1", 8765, False))
                threading.Thread(target=_delayed_restart, args=(host, port, no_plugins), daemon=True).start()
                return self.send_json({"ok": True, "note": "新进程启动中，本进程即将退出；页面会自动重连"})
            if u.path == "/api/implement/direct":
                # 已采纳的决策直接派给正在等任务的施工 Agent（不经 Leader）；没人可派时退回 Leader 队列
                return self.send_json(P.dispatch_implement(str(body.get("project") or ""), str(body.get("task_id") or ""),
                                                           by="user", comment=str(body.get("comment") or "")))
            if u.path == "/api/handback":
                # 收回 Leader：代理持有租约时写交回请求，代理下一次等待指令时交回
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self.send_json({"error": "只允许本机操作"}, 403)
                try:
                    return self.send_json(P.request_handback(by="user"))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 409)
            if u.path == "/api/guard/restart":
                # 一键拉起：写请求文件，由 bus_guard 在下一个巡检周期执行；守护本身没起来时先拉守护
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self.send_json({"error": "只允许本机操作"}, 403)
                service = str((body or {}).get("service") or "all")
                if not self.guard_state().get("alive"):
                    subprocess.run([sys.executable, "-X", "utf8", os.path.join(B.ROOT, "start_guard.py")], cwd=B.ROOT,
                                   capture_output=True, timeout=60, creationflags=0x08000000 if os.name == "nt" else 0)
                req_dir = os.path.join(B.ROOT, "console", "guard_requests")
                os.makedirs(req_dir, exist_ok=True)
                with open(os.path.join(req_dir, f"{int(time.time() * 1000)}.json"), "w", encoding="utf-8") as fh:
                    json.dump({"service": service, "at": B.now_iso()}, fh)
                return self.send_json({"ok": True, "service": service, "note": "守护将在 15 秒内执行拉起"})
            if u.path == "/api/command/requeue":
                # F4：人工把卡在「Leader 处理中」的指令退回队列（Leader 中途断线时用）
                try:
                    return self.send_json(P.requeue_command(str((body or {}).get("id") or ""), by="user", force=bool((body or {}).get("force"))))
                except ValueError as exc:
                    return self.send_json({"error": str(exc)}, 400)
            if u.path == "/api/command":
                # 网页上的所有操作都进指令队列，由Leader Agent 通过 bus_console 领取并执行
                kind = body.get("kind", "")
                payload = body.get("payload", {})
                if kind not in ("dispatch", "merge_now", "adopt", "reject", "cancel", "followup", "plan", "custom", "external", "reassign", "redispatch", "supersede", "execution", "seal", "implement", "nudge", "implement_nudge"):
                    raise ValueError(f"未知指令 {kind}")
                # 采纳 / 驳回 / 取消 / 被取代 是用户的最终决定，服务端立即落状态，页面即时刷新；Leader 只收通知
                if kind in ("adopt", "reject", "cancel", "supersede") and payload.get("project") and payload.get("task_id"):
                    with P.implementation_mutex(P.COMMANDS):
                        status = {"adopt": "adopted", "reject": "rejected", "cancel": "cancelled", "supersede": "superseded"}[kind]
                        fields = {"status": status, "decided_by": "user"}
                        if payload.get("comment"):
                            fields["user_comment"] = str(payload["comment"])[:2000]
                        rec = P.upsert_decision(payload["project"], payload["task_id"], **fields)
                        if kind == "adopt" and not payload.get("skip_execution"):
                            # 采纳只是定方案；方案要变成改动必须再派一轮 build。这里自动排 implement 指令给 Leader，决策记录 execution=pending，
                            # 概览「已采纳待落地」持续显示，直到 Leader 用 landed 回写或用户手动标记。
                            # 三态（e1cf）：读到且声明已实施 → released；读到未声明 → pending；
                            # 读不到 → 记录原因 + 告警，不能把「不知道」当成「没有」直接去排 implement
                            implemented, mp = False, rec.get("merge_path") or ""
                            read_state, read_detail = "ok", ""
                            try:
                                txt, _plan, used, source = P.read_merge_plan(payload["task_id"], mp)
                                implemented = declares_implemented(txt)
                                if source != "canonical":
                                    read_detail = f"兼容层命中 {used}（path_source={source}）"
                            except P.MergePathError as exc:
                                # 缺「## 最终方案」但全文明示已实施 / 不需要改动 → 仍按 released（9a96 引入的回归，5054 用例 notneeded）
                                if exc.code == P.MERGE_NO_FINAL_PLAN_SECTION and declares_implemented(getattr(exc, "text", "")):
                                    implemented = True
                                else:
                                    read_state, read_detail = exc.code, str(exc)
                            except Exception as exc:  # noqa: BLE001 - 编码 / 权限之类的意外，同样不静默
                                read_state, read_detail = "merge_read_error", repr(exc)
                            if read_state != "ok":
                                print(f"[adopt] 合并稿读取失败：{read_state} {read_detail}", file=sys.stderr)
                                P.upsert_decision(payload["project"], payload["task_id"],
                                                  merge_read_state=read_state, merge_read_detail=read_detail[:500])
                                payload["merge_read_state"] = read_state
                            if read_state != "ok":
                                # 读不到合并稿：既不敢判 released，也不该当「没交付」硬排 implement，交给用户看告警
                                pass
                            elif not implemented and rec.get("execution") not in ("released", "n-a"):
                                # Attachments are not deployment evidence. Use the same idempotent path as nudge.
                                P.request_implement(payload["project"], payload["task_id"], by="adopt", comment=fields.get("user_comment", ""))
                            else:
                                P.upsert_decision(payload["project"], payload["task_id"], execution="released")
                        if kind in ("cancel", "supersede"):
                            payload["cleanup"] = B.cancel_task(payload["task_id"], reason=status)
                # 改派与重派直接在服务端完成，不经 Leader 排队
                if kind == "reassign":
                    meta = B.read_json(os.path.join(B.OUTBOX, f"{payload['task_id']}.meta.json"), None) or {}
                    if meta.get("mode") == "build" and not payload.get("to"):
                        # 施工单走施工专用改派：新施工人开自己的 worktree，只派给正在等任务的施工 Agent
                        r = {"task_id": payload["task_id"], "from": payload["peer"], "listening": True,
                             **RA._reassign_build(payload["task_id"], payload["peer"], by="user", listening_only=True)}
                    else:
                        to = payload.get("to", "") or RA.pick_free_target(payload["peer"], list(meta.get("peers") or []) + [B.review_author(meta)])
                        r = B.reassign(payload["task_id"], payload["peer"], to, by="user")
                    P.push_command("external", {"note": f"reassign {r['task_id']} {r['from']} -> {r['to']}"})
                    return self.send_json({"ok": True, **r})
                if kind == "redispatch":
                    slug = payload.get("project", "")
                    old = next((x for x in P.decisions(slug) if x["task_id"] == payload["task_id"]), None) if slug else None
                    r = B.redispatch(payload["task_id"], peers=payload.get("peers") or (old or {}).get("peers") or None,
                                     question=payload.get("question", "") or "", ttl_min=int(payload.get("ttl_min") or 30),
                                     mode=payload.get("mode") or "", only_pending=bool(payload.get("only_pending")),
                                     min_answers=int(payload.get("min_answers") or 0), carry_answers=bool(payload.get("carry_answers")))
                    if slug:
                        P.upsert_decision(slug, payload["task_id"], status="superseded", superseded_by=r["task_id"])
                        P.upsert_decision(slug, r["task_id"], status="dispatched", title=(old or {}).get("title", "") or r.get("question", "")[:60],
                                          peers=r["peers"], mode=r.get("mode") or (old or {}).get("mode", ""), redispatched_from=payload["task_id"],
                                          tier=P.tier_of(r["peers"]), tier_source="auto", online_n=P.online_agents_n())
                    return self.send_json({"ok": True, **r})
                if kind == "implement_nudge" and payload.get("project") and payload.get("task_id"):
                    # 「派落地给 builder」：按 (implement, task_id) 幂等，已有未消费 implement 或未 merged 的 builds 记录时返回既有 id；否则直接落指令文件
                    return self.send_json(P.request_implement(payload["project"], payload["task_id"], by="user"))
                if kind == "nudge" and payload.get("task_id"):
                    # 催 Leader 合并：写 nudges.json（幂等 count++），Leader 下次 wait 立即收到 MERGE_REQUESTED；不进指令队列
                    n = P.add_nudge(payload["task_id"], by="user", note=str(payload.get("note") or ""))
                    return self.send_json({"ok": True, "task_id": payload["task_id"], **n})
                if kind in ("adopt", "cancel", "supersede") or (kind == "reject" and not payload.get("comment")):
                    # 状态已在服务端落定，不需要 Leader 再做什么：直接记为已完成，不进待领取队列（否则代理 Leader 不领用户定夺类指令，会永远排队）
                    cmd = P.push_command(kind, payload)
                    P.ack_command(cmd["id"], "服务端已落状态", True)
                    cmd["status"] = "done"
                    return self.send_json(cmd)
                if kind == "execution" and payload.get("project") and payload.get("task_id"):
                    # 落地状态是用户手动切换的标签，服务端直接落，不排队
                    st = payload.get("state") or payload.get("execution") or "pending"
                    if st not in ("pending", "released", "partial", "n-a"):
                        raise ValueError("execution 取值非法")
                    P.upsert_decision(payload["project"], payload["task_id"], execution=st, execution_by="user", execution_at=B.now_iso())
                    return self.send_json({"ok": True, "execution": st})
                if kind == "seal" and payload.get("task_id"):
                    r = B.seal_task(payload["task_id"], by="user")
                    if payload.get("project"):
                        P.upsert_decision(payload["project"], payload["task_id"], status="ready_for_merge")
                    return self.send_json(r)
                if kind == "merge_now" and payload.get("project") and payload.get("task_id"):
                    # 现在合并：先封口（法定已到即可），状态立即进入待合并，从进行中消失；Leader 收到指令后生成合并稿
                    try:
                        B.seal_task(payload["task_id"], by="user")
                    except ValueError:
                        pass
                    P.upsert_decision(payload["project"], payload["task_id"], status="ready_for_merge")
                if kind == "dispatch" and payload.get("mode") == "race":
                    # 抢单（ADR 0013）：要的就是快，不经 Leader 排队，工作台直接派；谁先领谁做
                    return self.send_json(P.dispatch_race(payload))
                cmd = P.push_command(kind, payload)
                if kind == "dispatch" and payload.get("project"):
                    P.upsert_decision(payload["project"], "pending-" + cmd["id"], status="draft",
                                      title=payload.get("question", "")[:60], peers=payload.get("peers", []), mode=payload.get("mode", ""),
                                      tier=P.tier_of(payload.get("peers")), tier_source="auto", online_n=P.online_agents_n())
                return self.send_json(cmd)
            if u.path == "/api/open":
                return self.send_json(self.open_local(body.get("path", ""), bool(body.get("reveal"))))
            if u.path == "/api/task-pack":
                # 导出落地任务包（Markdown）：只追加一条 project note，不改 execution，不写 console/
                try:
                    return self.send_json(P.export_task_pack(body["project"], body["task_id"]))
                except P.MergePathError as exc:
                    # 三态各自可被前端区分；任何一态都不得返回 200 + 占位文案
                    return self.send_json({"error": str(exc), "code": exc.code, "task_id": exc.task_id,
                                           "merge_path": exc.merge_path, "detail": exc.detail}, 422)
            if u.path.startswith("/api/decision/"):
                # 903f：谱系 / 人工交接 / 子单。白名单字段、服务端校验，只写 projects/<slug>/decisions.json，不进指令队列、不碰 console/
                return self.handle_decision_ext(u.path[len("/api/decision/"):], body or {})
            if u.path == "/api/profile":
                # Agent 个人设置（507e）：body {peer, ...patch}；白名单校验失败就地报错，不走指令队列
                peer = str((body or {}).get("peer") or "")
                patch = {k: v for k, v in (body or {}).items() if k != "peer"}
                try:
                    result = B.set_profile(peer, patch)
                    P.invalidate_state()
                    return self.send_json(result)
                except ValueError as exc:
                    return self.send_json({"error": str(exc)}, 400)
            if u.path == "/api/priority":
                # 用户对调度的设置直接落文件，不经Leader指令队列。整表提交带 rev：不等于当前值就 409 且不写，前端只回滚 UI。
                # 「读 rev → 校验 → 改写」全程持互斥：两个请求并发时旧实现会同读一个 rev、各自 +1 后写，
                # 静默丢一次更新且不返回 409
                from bus_locks import file_mutex
                with file_mutex(B.PRIORITY + ".mutex"):
                    cur = B.priority()
                    if body.get("rev") not in (None, "") and int(body["rev"]) != int(cur.get("rev") or 0):
                        return self.send_json({"error": f"conflict: 顶替顺序已被别处改到 rev {cur.get('rev')}，已放弃本次提交，请刷新后重试", "conflict": True, "priority": cur}, 409)
                    if body.get("factory_reset"):
                        # 恢复出厂 SOTA 序：整表改写为本地常量，并写 relay_order_meta（source=factory）
                        cur["relay_order"] = list(B.RELAY_FACTORY_ORDER)
                        cur["relay_order_meta"] = B.relay_meta_for(cur["relay_order"], None, "factory", "group")
                    else:
                        for k in ("relay_order", "agent_weight", "task_order"):
                            if k in body:
                                cur[k] = body[k]
                        if "relay_order" in body:
                            if not isinstance(body["relay_order"], list) or not all(isinstance(x, str) for x in body["relay_order"]):
                                raise ValueError("relay_order 必须是字符串数组")
                            meta = body.get("relay_order_meta") if isinstance(body.get("relay_order_meta"), list) else cur.get("relay_order_meta")
                            cur["relay_order_meta"] = B.relay_meta_for(cur["relay_order"], meta, "ui")
                        if isinstance(body.get("relay_policy"), dict):
                            cur["relay_policy"] = {**cur["relay_policy"], **body["relay_policy"]}
                    cur["rev"] = int(cur.get("rev") or 0) + 1
                    B.write_json(B.PRIORITY, cur)
                    return self.send_json(cur)
            if u.path.startswith("/api/agent_groups/"):
                # Agent 页分组（工作台自己管，不依赖 Arena）：save / delete / merge / reorder
                import bus_agent_groups as AG
                act, body = u.path.rsplit("/", 1)[-1], body or {}
                try:
                    if act == "save":
                        return self.send_json(AG.save(body))
                    if act == "delete":
                        return self.send_json(AG.delete(str(body.get("id") or "")))
                    if act == "merge":
                        return self.send_json(AG.merge(str(body.get("src") or ""), str(body.get("dst") or "")))
                    if act == "reorder":
                        return self.send_json(AG.reorder([str(x) for x in body.get("ids") or []]))
                except ValueError as exc:
                    return self.send_json({"error": str(exc)}, 400)
                return self.send_json({"error": f"未知操作 {act}"}, 404)
            if u.path == "/api/peers/remove":
                # 点名移除一个 Agent：先挪走手上的单，再归档登记、可选吊销令牌；dry_run 只列出会挪哪些单
                body = body or {}
                import bus_peer_admin as PA
                return self.send_json(PA.remove_peer(str(body.get("peer") or "").strip(), revoke=bool(body.get("revoke", True)),
                                                     force=bool(body.get("force")), dry_run=bool(body.get("dry_run"))))
            if u.path == "/api/allocator/mode":
                # ADR 0012：off / preview / on；切换后守护下一圈按新模式跑
                import bus_allocate as AL
                return self.send_json(AL.set_mode(str((body or {}).get("mode") or "")))
            if u.path == "/api/reassign/held":
                # 一键改派：peer 为空 = 所有 Agent 手上被占着不动的单；dry_run 只列出不动
                body = body or {}
                peer = str(body.get("peer") or "").strip()
                if body.get("dry_run"):
                    return self.send_json({"items": RA.held_items(peer)})
                return self.send_json(RA.reassign_held(peer, by="user"))
            if u.path == "/api/review/now":
                # 催审：直接派给在线 Reviewer，不再只给 Leader 留言。task_id 为空 = 全部待审
                body = body or {}
                return self.send_json(RV.review_now(str(body.get("task_id") or "").strip(), by="user",
                                                    ttl_min=int(body.get("ttl_min") or 15)))
            if u.path == "/api/peers/prune":
                body = body or {}
                max_age_sec = body.get("max_age_sec", 43200)
                dry_run = bool(body.get("dry_run", False))
                try:
                    pruned = B.prune_peers(max_age_sec=max_age_sec, dry_run=dry_run)
                    return self.send_json({"ok": True, "pruned": pruned, "count": len(pruned), "dry_run": dry_run})
                except Exception:
                    return self.send_json({"error": "清理失效 Agent 失败"}, 500)
            if u.path == "/api/peer/label":
                # 纯显示层改名。peer 必须是已登记的真名（否则 404，不造幽灵登记）；pending=true 只写首次连接前的显示名配置
                peer = str(body.get("peer") or "").strip()
                if not peer:
                    raise ValueError("缺少 peer")
                session = str(body.get("session") or "").strip()
                if session:
                    # 多活：只给这一位 Leader 会话改名，别的 Leader 的名字不受影响
                    try:
                        return self.send_json(B.set_session_label(peer, session, body.get("label", "")))
                    except KeyError as exc:
                        return self.send_json({"error": str(exc).strip("\"'"), "missing": True}, 404)
                    except PermissionError as exc:
                        return self.send_json({"error": str(exc)}, 403)
                try:
                    r = B.set_label(peer, body.get("label", ""), pending=bool(body.get("pending")))
                except B.LN.PeerMissing as exc:
                    return self.send_json({"error": str(exc), "missing": True}, 404)
                except PermissionError as exc:
                    return self.send_json({"error": str(exc)}, 403)
                except OSError as exc:
                    return self.send_json({"error": f"保存失败：{exc}"}, 503)
                return self.send_json(r)
            if u.path == "/api/lead_event/ack":
                # 幂等：已 ack 的再 ack 返回 changed=0，不报错
                try:
                    n = B.LN.ack_lead_event(B.ROOT, str(body.get("id") or ""), bool(body.get("all")))
                except OSError as exc:
                    return self.send_json({"error": f"保存失败：{exc}"}, 503)
                return self.send_json({"ok": True, "changed": n, "open": B.lead_events_open()})
            if u.path == "/api/bridge":
                P.set_bridge_url(body["url"])
                return self.send_json({"url": P.bridge_url()})
            if u.path == "/api/project":
                p = P.create_project(body["name"], body.get("peers", []), body.get("mode", "independent"),
                                     body.get("context_files", []), body.get("description", ""),
                                     body.get("default_team") if isinstance(body.get("default_team"), str) else "")
                return self.send_json(p)
            if u.path == "/api/land/bulk":
                # 待落地页批量处理一类：rework 重新派施工 / drop 不再落地 / rereview 重新审查（bus_land）
                import bus_land as LD
                return self.send_json(LD.bulk(str(body.get("cat") or ""), str(body.get("action") or ""), by="user"))
            if u.path == "/api/project/update":
                p = P.update_project(body["slug"], body)
                return self.send_json(p)
            target = PL.proxy_target(u.path)
            if target:
                return self.proxy_plugin(target, "POST", u.path, json.dumps(body, ensure_ascii=False).encode("utf-8"))
            return self.send_text("not found", code=404)
        except Exception as e:
            return self.send_json({"error": str(e)}, 400)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-plugins", action="store_true", help="不启动插件（测试或排障用）")
    a = ap.parse_args()
    P.ensure()
    global WATCH_DIRS
    WATCH_DIRS = tuple(d for d in (B.PEERS, B.INBOX, B.OUTBOX, getattr(P, "COMMANDS", ""), getattr(P, "DONE", "")) if d)
    for d in (E.EVENTS, IN.INTAKE, FB.FEEDBACK):
        os.makedirs(d, exist_ok=True)
    WATCH_DIRS = WATCH_DIRS + (IN.INTAKE, FB.FEEDBACK)  # 收件箱/反馈变化也推 SSE，页面实时刷新
    if a.host not in ("0.0.0.0", "::", ""):
        if a.host.count(":") > 1:       # 裸 IPv6：浏览器发的 Host 是 [addr]:port，两种写法都放行
            ALLOWED_HOSTS.update({a.host.lower().strip("[]"), "[" + a.host.lower().strip("[]") + "]"})
        else:
            ALLOWED_HOSTS.add(_hostname(a.host))
    # 绑定重试：排水式自重启时新进程先于旧进程退出而启动，等旧进程释放端口（最多 90 秒）
    srv = None
    deadline = time.time() + 90
    while True:
        try:
            srv = ThreadingHTTPServer((a.host, a.port), Handler)
            break
        except OSError:
            if time.time() >= deadline:
                raise
            print(f"端口 {a.port} 仍被占用，等待旧进程释放……", file=sys.stderr)
            time.sleep(0.5)
    srv.daemon_threads = True
    srv.restart_argv = (a.host, a.port, a.no_plugins)
    if not a.no_plugins:
        import atexit
        PL.start_supervisor(f"http://127.0.0.1:{a.port}")
        atexit.register(PL.stop_all)
    print(f"工作台 http://{a.host}:{a.port}  workspace={B.WORKSPACE}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
