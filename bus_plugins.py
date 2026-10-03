"""插件管理：发现、启停、守护。插件 = plugins/<id>/ 文件夹 + plugin.json 清单。

为什么这样设计（docs/adr/0002-plugins.md）：
- 每个插件是独立子进程，只通过工作台 HTTP 接口交互 → 插件崩溃不会拖垮工作台（同 VS Code Extension Host）；
- 启用状态和配置放在本机 plugins.local.json（不入库）→ 仓库里只有代码，没有你的私人路径；
- 开关即时生效，无需重启工作台（同 Home Assistant reload_config_entry）；
- 工作台不 import 插件代码，也不知道其它项目的路径 → 与其它项目零硬耦合。

plugin.json 字段：
  id           与文件夹同名，小写字母/数字/连字符
  name         显示名
  version      插件自己的版本
  api          固定 "workbench-plugin/1"
  kind         source（数据源，往收件箱送条目）| notifier（读事件发通知）| panel（只提供页面）
  description  一句话说明
  entry        可选，启动命令数组，如 ["python", "main.py"]；"python" 会替换为工作台所用解释器
  page         可选，插件自带页面（相对插件目录），通过 /plugins/<id>/<page> 访问；页面只能引用 page 所在目录及其子目录的文件
  proxy        可选，{"port": "<config 里的端口键>"}：插件启用时，工作台把 /api/<id>/* 转发到 127.0.0.1:<端口>/api/<id>/*
               （插件自己的 HTTP 服务；插件关闭时这些接口 404，ADR 0014）
  config       可选，默认配置（本机配置会覆盖同名键）
  config_help  可选，每个配置键的中文说明
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

import bus_core as B
import bus_events as E

PLUGINS = os.path.join(B.ROOT, "plugins")
LOCAL = os.path.join(B.ROOT, "plugins.local.json")
LOGS = os.path.join(B.ROOT, "log", "plugins")
DATA = os.path.join(B.ROOT, ".runtime", "plugins")
API = "workbench-plugin/1"
KINDS = ("source", "notifier", "panel")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
RESTART_LIMIT = 5          # 一小时内最多自动重启次数，超过就停下等人处理
_lock = threading.RLock()
_procs = {}                # id -> {"proc", "started", "restarts": [ts...], "error"}
_base_url = ""
_stale_checked = set()     # 每个插件只在本进程首次启动时清理一次上一代遗留进程


_WAKE = re.compile(r"^[a-z0-9][a-z0-9/-]{0,60}$")

def _pidfile(pid: str) -> str:
    return os.path.join(DATA, pid, "process.pid")


def _kill_stale(pid: str):
    """工作台被强杀时插件会成为孤儿。这里**不杀进程**，只清掉 pid 文件并记日志：
    Windows 很快复用 PID，旧号码可能已属于 MCP 或守护进程，按号杀会误伤（审查 6683 B-1）。
    孤儿插件按约定在工作台不可达 5 分钟内自行退出；双跑窗口里收件箱幂等，不会重复入箱。"""
    path = _pidfile(pid)
    try:
        old = open(path, encoding="utf-8").read().strip()
    except OSError:
        return
    try:
        os.remove(path)
    except OSError:
        pass
    try:
        os.makedirs(LOGS, exist_ok=True)
        with open(os.path.join(LOGS, pid + ".log"), "a", encoding="utf-8") as lf:
            lf.write(f"[{B.now_iso()}] 发现上一代遗留 pid {old}（工作台可能被强杀）；不强杀，遗留进程会自行退出\n")
    except OSError:
        pass
    E.emit("plugin.stale_pid", {"id": pid, "old_pid": old}, subject=pid)


# 传给插件的环境变量白名单：系统运行必需项 + 代理；不把工作台自己的令牌等变量带过去（审查 6683 可选项）
_ENV_KEEP = {"PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE",
             "HOME", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
             "PROGRAMFILES(X86)", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL",
             "PYTHONIOENCODING", "PYTHONUTF8", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY"}


def _base_env() -> dict:
    return {k: v for k, v in os.environ.items() if k.upper() in _ENV_KEEP}


def _local() -> dict:
    d = B.read_json(LOCAL, {})
    return d if isinstance(d, dict) else {}


def _save_local(d: dict):
    B.write_json(LOCAL, d)


def validate(manifest: dict, folder: str) -> list:
    errs = []
    if not isinstance(manifest, dict):
        return ["plugin.json 不是对象"]
    pid = manifest.get("id", "")
    if not _ID.match(str(pid)):
        errs.append("id 需为 2–41 位小写字母、数字、连字符")
    if pid != folder:
        errs.append(f"id「{pid}」必须与文件夹名「{folder}」一致")
    if manifest.get("api") != API:
        errs.append(f"api 必须是 {API}")
    if manifest.get("kind") not in KINDS:
        errs.append("kind 只能是 " + " / ".join(KINDS))
    entry = manifest.get("entry")
    if entry is not None and not (isinstance(entry, list) and entry and all(isinstance(x, str) for x in entry)):
        errs.append("entry 需为字符串数组")
    page = manifest.get("page")
    if page is not None and (not isinstance(page, str) or ".." in page.replace("\\", "/").split("/")):
        errs.append("page 路径不合法")
    if manifest.get("config") is not None and not isinstance(manifest.get("config"), dict):
        errs.append("config 需为对象")
    proxy = manifest.get("proxy")
    if proxy is not None:
        key = proxy.get("port") if isinstance(proxy, dict) else None
        port = (manifest.get("config") or {}).get(key) if isinstance(key, str) and isinstance(manifest.get("config"), dict) else None
        if not isinstance(port, int) or not 1024 <= port <= 65535:
            errs.append("proxy.port 需指向 config 里一个 1024–65535 的整数端口")
    wake = manifest.get("wake")
    if wake is not None and (not isinstance(wake, str) or not _WAKE.match(wake) or proxy is None):
        errs.append("wake 需为 /api/<id>/ 下的子路径（字母数字连字符斜杠），且插件要声明 proxy")
    return errs


def discover() -> list:
    """扫描 plugins/*/plugin.json，返回清单列表（含校验错误和运行状态）。"""
    out = []
    if not os.path.isdir(PLUGINS):
        return out
    local = _local()
    for folder in sorted(os.listdir(PLUGINS)):
        mf = os.path.join(PLUGINS, folder, "plugin.json")
        if not os.path.isfile(mf):
            continue
        try:
            with open(mf, encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, ValueError) as e:
            m = {"id": folder}
            errs = [f"plugin.json 读取失败：{e}"]
        else:
            errs = validate(m, folder)
        pid = m.get("id", folder)
        cfg = dict(m.get("config") or {}) if isinstance(m.get("config"), dict) else {}
        cfg.update((local.get("config") or {}).get(pid) or {})
        out.append({"id": pid, "folder": folder, "name": m.get("name") or pid, "version": m.get("version", ""),
                    "kind": m.get("kind", ""), "description": m.get("description", ""),
                    "entry": m.get("entry"), "page": m.get("page"), "proxy": m.get("proxy"), "wake": m.get("wake"), "config": cfg,
                    "config_help": m.get("config_help") or {}, "errors": errs,
                    "enabled": bool((local.get("enabled") or {}).get(pid)), **status(pid)})
    return out


def get(pid: str) -> dict:
    return next((p for p in discover() if p["id"] == pid), None)


def status(pid: str) -> dict:
    with _lock:
        st = _procs.get(pid)
        if not st:
            return {"running": False, "pid": None, "error": ""}
        alive = st["proc"] is not None and st["proc"].poll() is None
        return {"running": alive, "pid": st["proc"].pid if alive else None, "error": st.get("error", ""),
                "started": st.get("started", ""), "restarts": len(st.get("restarts", []))}


def log_tail(pid: str, n: int = 80) -> str:
    if not _ID.match(str(pid or "")):
        return ""
    path = os.path.join(LOGS, pid + ".log")
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        return "".join(f.readlines()[-n:])


_JOB = []   # [kernel32, 作业句柄]；懒创建，进程内只建一次


def _bind_to_workbench(proc) -> bool:
    """把插件子进程挂到工作台的 Windows 作业对象（KILL_ON_JOB_CLOSE）：工作台进程一退出（含被强杀、排水式重启），
    插件进程跟着退。以前靠插件自己看 WORKBENCH_PARENT_PID，多数插件没看，2026-10-02 查到 5 次重启攒了 10 个孤儿插件进程。
    拿不到作业对象（非 Windows / 权限）返回 False，退回旧行为：下次启动时按 pid 文件清理。"""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        if not _JOB:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE

            class LIMIT(ctypes.Structure):
                _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                            ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", wintypes.DWORD),
                            ("f", ctypes.c_size_t), ("g", wintypes.DWORD), ("h", wintypes.DWORD)]

            class EXT(ctypes.Structure):
                _fields_ = [("Basic", LIMIT), ("Io", ctypes.c_uint64 * 6), ("i", ctypes.c_size_t), ("j", ctypes.c_size_t),
                            ("k", ctypes.c_size_t), ("l", ctypes.c_size_t)]
            job = k32.CreateJobObjectW(None, None)
            info = EXT()
            info.Basic.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not job or not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                return False
            _JOB.extend([k32, job])
        k32, job = _JOB
        return bool(k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(int(proc._handle))))
    except Exception:  # noqa: BLE001
        return False


def _start(p: dict):
    if not p.get("entry") or p["errors"]:
        return
    with _lock:
        st = _procs.setdefault(p["id"], {"proc": None, "restarts": []})
        if st["proc"] is not None and st["proc"].poll() is None:
            return
        cwd = os.path.join(PLUGINS, p["folder"])
        argv = [sys.executable if (i == 0 and x in ("python", "python3", "py")) else x for i, x in enumerate(p["entry"])]
        data_dir = os.path.join(DATA, p["id"])
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(LOGS, exist_ok=True)
        env = dict(_base_env(), WORKBENCH_URL=_base_url, WORKBENCH_PARENT_PID=str(os.getpid()), WORKBENCH_PLUGIN_ID=p["id"],
                   WORKBENCH_PLUGIN_CONFIG=json.dumps(p["config"], ensure_ascii=False),
                   WORKBENCH_PLUGIN_DATA=data_dir, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        log = open(os.path.join(LOGS, p["id"] + ".log"), "a", encoding="utf-8")
        log.write(f"\n==== {B.now_iso()} start {' '.join(argv)}\n")
        log.flush()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        _kill_stale(p["id"])
        try:
            st["proc"] = subprocess.Popen(argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                          stdin=subprocess.DEVNULL, creationflags=flags)
            st["started"], st["error"] = B.now_iso(), ""
            st["bound"] = _bind_to_workbench(st["proc"])
            with open(_pidfile(p["id"]), "w", encoding="utf-8") as pf:
                pf.write(str(st["proc"].pid))
        except OSError as e:
            st["proc"], st["error"] = None, f"启动失败：{e}"
        finally:
            log.close()
    E.emit("plugin.started" if st["proc"] else "plugin.failed", {"id": p["id"], "error": st.get("error", "")}, subject=p["id"])


def _stop(pid: str, reason: str = "stop"):
    with _lock:
        st = _procs.get(pid)
        proc = st and st.get("proc")
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    try:
        os.remove(_pidfile(pid))
    except OSError:
        pass
    E.emit("plugin.stopped", {"id": pid, "reason": reason}, subject=pid)


def toggle(pid: str, enabled: bool) -> dict:
    p = get(pid)
    if not p:
        raise ValueError("没有这个插件")
    if enabled and p["errors"]:
        raise ValueError("插件清单有错误，不能启用：" + "；".join(p["errors"]))
    local = _local()
    local.setdefault("enabled", {})[pid] = bool(enabled)
    _save_local(local)
    if enabled:
        with _lock:
            _procs.pop(pid, None)              # 手动启用会清零重启计数
        _start(get(pid))
    else:
        _stop(pid, "disabled")
    return get(pid)


def set_config(pid: str, config: dict) -> dict:
    """保存本机配置并重启该插件使其生效。只接受插件声明过的键。"""
    p = get(pid)
    if not p:
        raise ValueError("没有这个插件")
    if not isinstance(config, dict):
        raise ValueError("config 需为对象")
    with open(os.path.join(PLUGINS, p["folder"], "plugin.json"), encoding="utf-8") as f:
        declared = set((json.load(f).get("config") or {}).keys())
    unknown = set(config) - declared
    if unknown:
        raise ValueError("未声明的配置项：" + "、".join(sorted(unknown)))
    local = _local()
    local.setdefault("config", {})[pid] = {**((local.get("config") or {}).get(pid) or {}), **config}
    _save_local(local)
    if p["enabled"]:
        _stop(pid, "reconfigure")
        _start(get(pid))
    return get(pid)


def supervise_once():
    """启动应运行但没在跑的插件；崩溃的按限额重启。"""
    now = time.time()
    for p in discover():
        if not p["enabled"] or not p.get("entry") or p["errors"]:
            continue
        with _lock:
            st = _procs.get(p["id"])
            if st and st["proc"] is not None and st["proc"].poll() is None:
                continue
            if st and st["proc"] is not None:                       # 曾经启动过，现在退出了 → 算一次崩溃
                st["restarts"] = [t for t in st.get("restarts", []) if now - t < 3600] + [now]
                st["error"] = f"进程退出（代码 {st['proc'].returncode}）"
                st["proc"] = None
                if len(st["restarts"]) > RESTART_LIMIT:
                    st["error"] += f"；一小时内已重启 {RESTART_LIMIT} 次，停止自动重启，请看日志"
                    E.emit("plugin.gave_up", {"id": p["id"]}, subject=p["id"])
                    continue
            elif st and len(st.get("restarts", [])) > RESTART_LIMIT:
                continue
        _start(p)


def start_supervisor(base_url: str, interval: float = 5.0):
    global _base_url
    _base_url = base_url

    def loop():
        while True:
            try:
                supervise_once()
            except Exception as e:      # 守护线程不能死
                sys.stderr.write(f"[plugins] supervise error: {e}\n")
            time.sleep(interval)

    threading.Thread(target=loop, name="plugin-supervisor", daemon=True).start()


def stop_all():
    for pid in list(_procs):
        _stop(pid, "shutdown")


def proxy_target(path: str) -> str:
    """/api/<id>/... 且该插件启用、声明了 proxy → 返回 http://127.0.0.1:<端口>；否则空串。
    只在核心路由都没命中之后才查（bus_web 的 404 兜底处），插件不可能遮住核心接口。"""
    parts = path.split("/")
    if len(parts) < 4 or parts[1] != "api" or not _ID.match(parts[2]):
        return ""
    p = get(parts[2])
    if not p or p["errors"] or not p["enabled"] or not isinstance(p.get("proxy"), dict):
        return ""
    port = p["config"].get(p["proxy"].get("port"))
    return f"http://127.0.0.1:{int(port)}" if isinstance(port, int) and 1024 <= port <= 65535 else ""


def wake_targets() -> list:
    """声明了 wake 的已启用插件：[(id, 完整地址)]。工作台的「唤醒」按钮按顺序问它们能不能把唤醒语发进会话（bus_wake）。"""
    out = []
    for p in discover():
        if p["errors"] or not p["enabled"] or not p.get("wake"):
            continue
        base = proxy_target(f"/api/{p['id']}/")
        if base:
            out.append((p["id"], f"{base}/api/{p['id']}/{p['wake']}"))
    return out


def page_path(pid: str, rel: str) -> str:
    """插件页面静态文件的安全路径；越界返回空串。
    只开放 page 所在目录（插件根目录下的 page → 整个插件目录，与以前一致；web/index.html → 只开放 web/），
    这样带服务端的插件（如 Arena）源码与数据不会被当静态文件读出去。"""
    p = get(pid)
    if not p or p["errors"]:
        return ""
    root = os.path.realpath(os.path.join(PLUGINS, p["folder"]))
    base = os.path.realpath(os.path.join(root, os.path.dirname(p.get("page") or "")))
    if base != root and not base.startswith(root + os.sep):
        return ""
    full = os.path.realpath(os.path.join(root, rel or p.get("page") or ""))
    if not full.startswith(base + os.sep) or not os.path.isfile(full):
        return ""
    return full
