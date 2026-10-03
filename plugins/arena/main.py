"""Arena 插件入口（ADR 0014）。

- 控制面（server/arena_server.py）在本进程里跑：插件管理器停掉本进程，控制面一起停，不留孤儿；
- 可选拉起 ArenaX 登录器（config.arenax_root 留空就不管它），子进程挂在 Windows 作业对象上，本进程一退它也退；
- 数据放 WORKBENCH_PLUGIN_DATA（.runtime/plugins/arena/，不入库）。第一次启动时若那里是空的、
  而旧位置 <工作台>/arena/data 有数据，就**复制**过来（不删旧的，回滚时旧版本照样能读）；
- 工作台进程没了，本进程自己退出。
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server")
WB_ROOT = os.path.dirname(os.path.dirname(HERE))
LEGACY_DATA = os.environ.get("ARENA_LEGACY_DATA") or os.path.join(WB_ROOT, "arena", "data")  # 测试可指到临时目录


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def config() -> dict:
    try:
        return json.loads(os.environ.get("WORKBENCH_PLUGIN_CONFIG") or "{}")
    except ValueError:
        return {}


def migrate(data: str, legacy: str = LEGACY_DATA) -> list:
    """新数据目录里还没有任何 .json 时，从旧目录复制一份。返回复制了哪些文件。"""
    os.makedirs(data, exist_ok=True)
    if any(f.endswith(".json") for f in os.listdir(data)) or not os.path.isdir(legacy):
        return []
    copied = []
    for f in sorted(os.listdir(legacy)):
        src = os.path.join(legacy, f)
        if f.endswith(".json") and os.path.isfile(src):
            shutil.copy2(src, os.path.join(data, f))
            copied.append(f)
    return copied


def arenax_plan(cfg: dict):
    """要拉起 ArenaX 就返回 (argv, cwd, env, port)，否则 None。"""
    root = str(cfg.get("arenax_root") or "").strip()
    if not root or not os.path.isfile(os.path.join(root, "run_server.py")):
        return None
    u = urlparse(str(cfg.get("arenax_url") or "http://127.0.0.1:9090"))
    port = u.port or (443 if u.scheme == "https" else 80)
    vpy = os.path.join(root, ".venv", "Scripts", "python.exe")
    env = dict(os.environ)
    proxy = str(cfg.get("arenax_proxy") or "").strip()
    if proxy:
        env.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, ALL_PROXY=proxy)
    return [vpy if os.path.exists(vpy) else "python", os.path.join(root, "run_server.py")], root, env, port


def _job_handle():
    """Windows 作业对象：本进程退出（含被强杀）时，挂在上面的子进程一起结束。拿不到就返回 None。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = k32.CreateJobObjectW(None, None)

        class LIMIT(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class EXT(ctypes.Structure):
            _fields_ = [("Basic", LIMIT), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        info = EXT()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return None
        return k32, job
    except Exception:  # noqa: BLE001  拿不到作业对象：子进程可能在插件停掉后残留，下次启动前按端口清理
        return None


def _kill_port(port: int) -> None:
    if os.name != "nt":
        return
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, errors="replace").stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3] == "LISTENING" and parts[4].isdigit():
            subprocess.run(["taskkill", "/PID", parts[4], "/T", "/F"], capture_output=True)


def keep_arenax(plan, job) -> None:
    argv, cwd, env, port = plan
    while True:
        _kill_port(port)
        lf = open(os.path.join(os.environ.get("WORKBENCH_PLUGIN_DATA") or HERE, "arenax.log"), "a", encoding="utf-8")
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if job:
            import ctypes
            k32, h = job
            k32.AssignProcessToJobObject(h, ctypes.c_void_p(int(p._handle)))
        log(f"ArenaX 已启动 pid={p.pid}")
        rc = p.wait()
        lf.close()
        log(f"ArenaX 退出 rc={rc}，30 秒后重启")
        time.sleep(30)


def watch_parent() -> None:
    ppid = int(os.environ.get("WORKBENCH_PARENT_PID") or 0)
    if not ppid:
        return
    while True:
        time.sleep(5)
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {ppid}", "/NH"], capture_output=True, text=True, errors="replace").stdout
            alive = str(ppid) in out
        else:
            try:
                os.kill(ppid, 0)
                alive = True
            except OSError:
                alive = False
        if not alive:
            log("工作台进程已退出，插件随之退出")
            os._exit(0)


def main() -> None:
    cfg = config()
    data = os.environ.get("WORKBENCH_PLUGIN_DATA") or os.path.join(SERVER, "data")
    copied = migrate(data)
    if copied:
        log(f"首次启动：从 {LEGACY_DATA} 复制了 {len(copied)} 个数据文件（旧文件保留）")
    os.environ["ARENA_DATA"] = data
    os.environ.setdefault("WORKBENCH_ROOT", WB_ROOT)
    threading.Thread(target=watch_parent, daemon=True).start()
    plan = arenax_plan(cfg)
    if plan:
        threading.Thread(target=keep_arenax, args=(plan, _job_handle()), daemon=True).start()
    sys.path.insert(0, SERVER)
    os.chdir(SERVER)
    import arena_server  # noqa: E402  环境变量设好之后再导入：arena_store 在导入时读 ARENA_DATA
    port = int(cfg.get("port") or 9091)
    _kill_port(port)   # 迁移前由守护拉起的旧控制面还占着端口：先清掉，否则本进程绑不上、反复崩溃
    sys.argv = [sys.argv[0], "--port", str(port), "--host", "127.0.0.1"]
    arena_server.main()


if __name__ == "__main__":
    main()
