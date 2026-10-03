import os
import sys
import json
import time
import subprocess
import urllib.request

# 拉起服务守护 bus_guard.py（无窗口、脱离作业对象、幂等）；--install 额外注册开机自启计划任务 AgentTeamWorkbenchGuard
HERE = os.path.dirname(os.path.abspath(__file__))
STATUS = os.path.join(HERE, "console", "guard_status.json")
PIDFILE = os.path.join(HERE, "console", "guard.pid")
TASK = "AgentTeamWorkbenchGuard"
FLAGS = 0x08000000 | 0x00000200 | 0x01000000


def alive() -> int:
    try:
        pid = int(open(PIDFILE).read().strip())
        # 不再要求 guard_status.json 的 pid 已对上：守护刚起、还没写第一份状态的那几秒里会被误判已死，
        # 于是「一键重启」又拉起第二个守护（2026-10-02 实测两个守护同时改派）。pid 文件 + 进程还活着 + 是 bus_guard 就算在。
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, timeout=15,
                             creationflags=0x08000000).stdout.decode("utf-8", "replace")
        return pid if f'"{pid}"' in out and "python" in out.lower() else 0
    except Exception:
        return 0


def start() -> int:
    import first_run   # 新克隆的仓库：建运行目录、生成主令牌、提示装依赖
    if first_run.ensure()["missing_deps"]:
        sys.exit(1)
    pid = alive()
    if pid:
        print("ALREADY_RUNNING", pid)
        return pid
    log = open(os.path.join(HERE, "console", "guard_stdout.log"), "ab", buffering=0)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([sys.executable, "-X", "utf8", os.path.join(HERE, "bus_guard.py")], cwd=HERE, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, env=env, creationflags=FLAGS, close_fds=True)
    print("STARTED", p.pid)
    return p.pid


def install():
    cmd = f'"{sys.executable}" -X utf8 "{os.path.join(HERE, "start_guard.py")}"'
    r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/RL", "LIMITED", "/TN", TASK, "/TR", cmd],
                       capture_output=True, timeout=30, creationflags=0x08000000)
    print("TASK", r.returncode, (r.stdout or r.stderr).decode("gbk", "replace").strip()[-200:])


if __name__ == "__main__":
    if "--install" in sys.argv:
        install()
    start()
