import os
import sys
import json
import time
import socket
import subprocess
import datetime as dt
import urllib.request

# 服务守护：每 GUARD_INTERVAL 秒巡检 Workbench 栈，连续失败即拉起；网页「服务守护」卡片通过 requests 目录下发一键重启
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bus_core as B  # noqa: E402

PY = sys.executable
# Arena 控制面（9091）与 ArenaX 中继不再由守护拉起：它们属于可选插件 plugins/arena（ADR 0014），
# 插件启用时由工作台的插件管理器启动、崩溃重启；插件关闭就不运行。
STATUS = os.path.join(HERE, "console", "guard_status.json")
REQ_DIR = os.path.join(HERE, "console", "guard_requests")
LOG = os.path.join(HERE, "console", "guard.log")
PIDFILE = os.path.join(HERE, "console", "guard.pid")
GUARD_INTERVAL = 15
FAILS_BEFORE_RESTART = 2
RESTART_COOLDOWN_SEC = 60
NO_WINDOW = 0x08000000 | 0x00000200 | 0x01000000  # CREATE_NO_WINDOW | NEW_PROCESS_GROUP | BREAKAWAY_FROM_JOB


def log(msg: str):
    # 日志时间戳必须带偏移（ADR 0010）：naive strftime 写出的串跨时区不可比
    line = f"{dt.datetime.now().astimezone().isoformat(timespec='seconds')} {msg}\n"
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def http_ok(url: str, timeout: float = 4.0) -> tuple:
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bus_guard"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(2048)
            return True, int((time.time() - t0) * 1000), ""
    except urllib.error.HTTPError as exc:
        # 401/403 属鉴权拦截（MCP Bearer、ArenaX 未授权），服务本身活着；
        # 404 说明路由不存在（端口被别的服务占用）；5xx/530 是上游或隧道故障（Cloudflare 502/530）——都判不健康
        if exc.code == 404 or exc.code >= 500:
            return False, int((time.time() - t0) * 1000), f"http {exc.code}"
        return True, int((time.time() - t0) * 1000), f"http {exc.code}"
    except Exception as exc:
        return False, int((time.time() - t0) * 1000), str(exc)[:160]


def port_owners(port: int) -> list:
    try:
        raw = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, timeout=15,
                             creationflags=0x08000000).stdout or b""
    except Exception:
        return []
    pids = set()
    for line in raw.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3] == "LISTENING":
            try:
                pids.add(int(parts[4]))
            except ValueError:
                pass
    return sorted(p for p in pids if p != os.getpid())


# 守护只拉起 python 服务与 cloudflared；端口上若是别的程序，说明被第三方占用，只记日志不杀
KILLABLE_IMAGES = {"python.exe", "pythonw.exe", "cloudflared.exe"}


def image_name(pid: int) -> str:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, timeout=15,
                             creationflags=0x08000000).stdout.decode("utf-8", "replace").strip()
        return out.split(",")[0].strip('"').lower() if out.startswith('"') else ""
    except Exception:
        return ""


def kill_port(port: int):
    for pid in port_owners(port):
        img = image_name(pid)
        if img not in KILLABLE_IMAGES:
            log(f"port {port} is held by pid {pid} ({img or 'unknown'}); not ours, not killing")
            continue
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=20, creationflags=0x08000000)
        log(f"killed pid {pid} ({img}) on port {port}")
    for _ in range(20):
        if not port_owners(port):
            return
        time.sleep(0.3)


def spawn(argv: list, cwd: str, logname: str, env: dict | None = None) -> int:
    e = dict(os.environ)
    e.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    if env:
        e.update(env)
    out = open(os.path.join(HERE, "console", logname), "ab", buffering=0)
    try:
        p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=out, env=e,
                             creationflags=NO_WINDOW, close_fds=True)
    finally:
        # 子进程已继承句柄；父进程这份不关的话，守护长驻期间每拉起一次服务就漏一个
        out.close()
    return p.pid


def service_running(name: str) -> bool:
    try:
        out = subprocess.run(["sc", "query", name], capture_output=True, timeout=10, creationflags=0x08000000).stdout
        return b"RUNNING" in out
    except Exception:
        return False


# ---- 各服务的探测与拉起 ---------------------------------------------------------------
def start_mcp():
    kill_port(8791)
    r = subprocess.run([PY, "-X", "utf8", os.path.join(HERE, "start_named_mcp.py"), "--port", "8791"], cwd=HERE,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, creationflags=0x08000000)
    return (r.stdout or r.stderr).strip()[-200:]


def start_workbench():
    kill_port(8765)
    return spawn([PY, "-X", "utf8", os.path.join(HERE, "bus_web.py"), "--port", "8765", "--host", "127.0.0.1"], HERE, "workbench_8765.log")


def start_cloudflared():
    r = subprocess.run(["sc", "start", CF_SERVICE], capture_output=True, timeout=20, creationflags=0x08000000)
    return r.stdout.decode("utf-8", "replace")[-200:]


# 公网隧道是可选的个人部署（多数用户只在本机用，不需要）：配置放 console/guard.local.json（不入库），没有就不守护隧道。
# {"tunnel": {"public_url": "https://你的域名", "service": "cloudflared",
#             "cloudflared_exe": "C:/Program Files (x86)/cloudflared/cloudflared.exe", "token_file": "C:/ProgramData/cloudflared/token"}}
LOCAL_CFG = os.path.join(HERE, "console", "guard.local.json")


def tunnel_config(path: str = "") -> dict:
    try:
        with open(path or LOCAL_CFG, encoding="utf-8") as fh:
            cfg = (json.load(fh) or {}).get("tunnel") or {}
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) and str(cfg.get("public_url") or "").startswith("https://") else {}


TUNNEL = tunnel_config()
CF_SERVICE = str(TUNNEL.get("service") or "cloudflared")
CF_EXE = str(TUNNEL.get("cloudflared_exe") or "")
CF_TOKEN = str(TUNNEL.get("token_file") or "")
CF_PID = os.path.join(HERE, "console", "cloudflared_replica.pid")


def token_readable() -> bool:
    try:
        with open(CF_TOKEN, "rb") as fh:
            fh.read(1)
        return True
    except OSError:
        return False


def replica_alive() -> tuple:
    if not token_readable():
        return None, 0, "等待授权：请以管理员运行 tools/grant_admin.bat"
    # 用户态第二个 connector：与系统服务共用同一隧道 token，Cloudflare 侧自动在两个 connector 间分流；
    # 系统服务挂了（守护无管理员权限拉不起来）时，公网入口仍由本副本承担
    try:
        pid = int(open(CF_PID).read().strip())
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, timeout=15, creationflags=0x08000000).stdout.decode("utf-8", "replace")
        ok = f'"{pid}"' in out and "cloudflared" in out.lower()
        return ok, 0, "" if ok else "replica not running"
    except Exception as exc:
        return False, 0, str(exc)[:120]


def start_replica():
    try:
        with open(CF_TOKEN, "rb") as fh:
            fh.read(1)
    except OSError as exc:
        # 未授权前不反复拉起：在服务卡片上给出明确指引
        return f"token 不可读（需运行一次 tools/grant_admin.bat 授权）: {str(exc)[:60]}"
    if not os.path.exists(CF_EXE):
        return "cloudflared exe missing"
    pid = spawn([CF_EXE, "tunnel", "--no-autoupdate", "run", "--token-file", CF_TOKEN], HERE, "cloudflared_replica.log")
    with open(CF_PID, "w", encoding="utf-8") as fh:
        fh.write(str(pid))
    return pid


def keepalive_alive() -> tuple:
    pf = os.path.join(HERE, "console", "lead_keepalive.pid")
    try:
        pid = int(open(pf).read().strip())
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, timeout=15, creationflags=0x08000000).stdout.decode("utf-8", "replace")
        ok = f'"{pid}"' in out and "python" in out.lower()
        return ok, 0, "" if ok else "lead keepalive process not running"
    except Exception as exc:
        return False, 0, str(exc)[:120]


def start_keepalive():
    return spawn([PY, "-X", "utf8", os.path.join(HERE, "lead_keepalive.py")], HERE, "lead_keepalive.log")


SERVICES = {
    "lead_keepalive": {"label": "Leader 本机保活", "check": keepalive_alive, "start": start_keepalive},
    "mcp": {"label": "MCP 服务 8791", "check": lambda: http_ok("http://127.0.0.1:8791/health"), "start": start_mcp},
    "workbench": {"label": "工作台 8765", "check": lambda: http_ok("http://127.0.0.1:8765/api/mcp/health"), "start": start_workbench},
}
if TUNNEL:   # 只有本机配置了公网隧道才守护这三项（见 tunnel_config）
    _host = TUNNEL["public_url"].rstrip("/")
    SERVICES.update({
        "cloudflared": {"label": "Cloudflare 隧道（系统服务）", "check": lambda: (service_running(CF_SERVICE), 0, ""), "start": start_cloudflared},
        "cf_replica": {"label": "Cloudflare 隧道（用户态副本）", "check": replica_alive, "start": start_replica},
        "tunnel": {"label": f"公网入口 {_host.split('//', 1)[-1]}", "check": lambda: http_ok(_host + "/health", 8), "start": start_replica},
    })


def controller_guard(state: dict):
    # Leader活性以指令队列的消费为准：pending 指令超过 STUCK_SEC 没被领取视为Leader失联，
    # 释放Leader租约并把Leader心跳标为过期，让顶替顺序里的第一个在线 peer 在下一个检查周期接管。
    # 但「队列有老指令」不等于「没人消费」：退回队列的老指令会让 pending 年龄长期大于阈值，
    # 只要最近仍有领取发生，Leader 就是在消化积压，不能踢租约（否则代理会在慢处理窗口接管、重复处理）
    c = B.peer_state("controller")
    age = B.heartbeat_age(c.get("last_seen"))[0] if c.get("last_seen") else 1e9
    l = B.lease()
    stuck = oldest_pending_age()
    claim_age = freshest_claim_age()
    consuming = claim_age is not None and claim_age < STUCK_SEC
    state["controller"] = {"online": bool(c.get("online")), "age_sec": int(min(age, 10**9)), "lease": l, "oldest_pending_sec": stuck,
                           "freshest_claim_sec": int(claim_age) if claim_age is not None else None}
    if stuck >= STUCK_SEC and not consuming and l and l.get("holder") == "controller":
        # 强释放必须复查（force_release_lease 在互斥锁内比对 holder+epoch）：
        # 读到旧租约后直接删，读与删之间新持有者可能已抢到租约，会被误删
        if B.force_release_lease("controller", l.get("epoch")):
            log(f"controller stuck: pending command {stuck}s unclaimed, released lease epoch {l.get('epoch')}")
            B.note_lead_event(l.get("holder") or "controller", "", l.get("epoch"), "guard_forced",
                              f"守护释放租约：pending 指令 {stuck} 秒无人领取。")
        return
    if age >= B.RELAY_AFTER_SEC and l and l.get("holder") == "controller":
        if B.force_release_lease("controller", l.get("epoch")):
            log(f"released stale controller lease epoch {l.get('epoch')} (controller age {int(age)}s)")
            B.note_lead_event(l.get("holder") or "controller", "", l.get("epoch"), "guard_forced",
                              f"守护释放过期租约：Leader 心跳已停 {int(age)} 秒。")


STUCK_SEC = 240


def freshest_claim_age():
    try:
        import bus_projects as P
        return P.freshest_claim_age()
    except Exception:
        return None


def oldest_pending_age() -> int:
    # 网页指令队列中最早一条 pending 的等待秒数；目录不存在或为空返回 0
    try:
        import bus_projects as P
        d = getattr(P, "COMMANDS", "")
        if not d or not os.path.isdir(d):
            return 0
        ages = []
        for f in os.listdir(d):
            if not f.endswith(".json"):
                continue
            cmd = B.read_json(os.path.join(d, f), None) or {}
            if cmd.get("status", "pending") != "pending":
                continue
            ages.append(time.time() - os.path.getmtime(os.path.join(d, f)))
        return int(max(ages)) if ages else 0
    except Exception:
        return 0


def take_requests() -> list:
    os.makedirs(REQ_DIR, exist_ok=True)
    out = []
    for f in sorted(os.listdir(REQ_DIR)):
        p = os.path.join(REQ_DIR, f)
        try:
            with open(p, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except Exception:
            pass
        try:
            os.remove(p)
        except OSError:
            pass
    return out


def restart(name: str, reason: str, state: dict):
    svc = SERVICES[name]
    log(f"restart {name}: {reason}")
    try:
        detail = svc["start"]()
    except Exception as exc:
        detail = f"start failed: {exc}"
    log(f"restart {name} -> {detail}")
    state["services"][name]["last_restart"] = B.now_iso()
    state["services"][name]["last_restart_detail"] = str(detail)
    state["services"][name]["restarts"] = state["services"][name].get("restarts", 0) + 1


def main():
    os.makedirs(os.path.dirname(STATUS), exist_ok=True)
    with open(PIDFILE, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    fails = {k: 0 for k in SERVICES}
    last_restart = {k: 0.0 for k in SERVICES}
    state = {"pid": os.getpid(), "started": B.now_iso(), "interval_sec": GUARD_INTERVAL, "services": {k: {"label": v["label"]} for k, v in SERVICES.items()}}
    log(f"guard started pid {os.getpid()}")
    while True:
        cycle_start = time.time()
        wanted = {r.get("service"): r for r in take_requests() if r.get("service") in SERVICES or r.get("service") == "all"}
        if "all" in wanted:
            wanted = {k: wanted["all"] for k in SERVICES if k not in ("cloudflared", "tunnel", "cf_replica")}
        for name, svc in SERVICES.items():
            ok, ms, err = svc["check"]()
            st = state["services"][name]
            st.update({"ok": ok, "latency_ms": ms, "error": err if not ok else "", "checked": B.now_iso()})
            if ok is None:
                # 待授权：不计失败、不重启，只在卡片上显示指引
                st.update({"ok": True, "pending": True, "error": err})
                fails[name] = 0
                continue
            fails[name] = 0 if ok else fails[name] + 1
            st["fails"] = fails[name]
            manual = name in wanted
            if manual or (fails[name] >= FAILS_BEFORE_RESTART and time.time() - last_restart[name] >= RESTART_COOLDOWN_SEC):
                if name == "tunnel" and not manual and state["services"]["mcp"].get("ok") is False:
                    continue  # 本地 MCP 都没起来时公网入口失败属正常，先修 MCP
                restart(name, "manual" if manual else f"{fails[name]} consecutive failures: {err}", state)
                last_restart[name] = time.time()
                fails[name] = 0
        try:
            controller_guard(state)
        except Exception as exc:
            state["controller"] = {"error": str(exc)[:200]}
        try:
            acted = B.auto_reassign()
            for r in B.auto_seal():
                log(f"auto-seal {r['task_id']} kept={len(r['kept'])} dropped={r['dropped']}")
            for a in acted:
                if a.get("reason") == "stale_closed":
                    log(f"auto-close stale {a['task_id']} (pending: {a['from']})")
                elif a.get("error"):
                    log(f"auto-reassign {a['task_id']} {a['from']}: {a['reason']} -> failed: {a['error']}")
                else:
                    log(f"auto-reassign {a['task_id']} {a['from']} -> {a['to']} ({a['reason']})")
            if acted:
                state["last_reassign"] = {"time": B.now_iso(), "items": acted[-10:]}
            # 领了不做（30 分钟没有任何干活迹象）的单收回改派、已关任务的残留副本挪走——不受分配器模式影响，每圈都做
            import bus_reassign as RA
            gone = RA.clean_closed_copies()
            if gone:
                log(f"moved {gone} leftover inbox copies of closed tasks to _closed/")
            fr = RA.free_stalled()
            for m in fr["moved"]:
                log(f"free stalled {m['task_id']} {m['peer']} ({m['claimed_min']} min, no work) -> {m.get('to', '')}")
            for m in fr["released"]:
                log(f"free stalled {m['task_id']} {m['peer']} -> released (nobody to take it)")
            # 落地推进器：审查通过（48 小时内）的自动合并；合并受阻的记原因，交给用户在待落地页批量处理
            import bus_land as LD
            for m in (LD.tick().get("merged") or []):
                log(f"land merge {m['build_id']} {m['peer']}: {'ok ' + str(m['result'].get('merge_commit', ''))[:7] if m['result'].get('ok') else 'blocked: ' + str(m['result'].get('reason', ''))[:120]}")
            # ADR 0012：按积压自动分配。默认只预演（写 console/allocator.json），用户在 Agent 页切到「真派」才动手
            import bus_allocate as AL
            al = AL.tick()
            if al.get("mode") == "on":
                for a in al.get("actions") or []:
                    log(f"allocate {a['stage']} {a['task_id']} -> {a.get('to', '')}: {'ok' if (a.get('result') or {}).get('ok') else (a.get('result') or {}).get('error', 'not moved')}")
        except Exception as exc:
            state["reassign_error"] = str(exc)[:200]
        state["updated"] = B.now_iso()
        state["cycle_ms"] = int((time.time() - cycle_start) * 1000)
        B.write_json(STATUS, state)
        time.sleep(max(3, GUARD_INTERVAL - (time.time() - cycle_start)))


if __name__ == "__main__":
    main()
