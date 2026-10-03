import os, sys, time, json, datetime as dt
# Lead 本机保活：与 bus_projects.console_wait 相同的租约与心跳逻辑，但跑在宿主机上，不依赖隧道。
# 只维持在线与租约，不消费指令；真正的处理仍由远端 Lead 会话通过 bus_console(wait) 完成。
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bus_core as B
PIDFILE = os.path.join(HERE, "console", "lead_keepalive.pid")
INTERVAL = 20
STUCK_SEC = 240
COMMANDS = os.path.join(HERE, "console", "commands")
with open(PIDFILE, "w", encoding="utf-8") as fh:
    fh.write(str(os.getpid()))


def oldest_pending() -> float:
    # 网页指令排队最久的秒数；远端 Lead 会话若真的死了，队列会堆起来
    try:
        ages = [time.time() - os.path.getmtime(os.path.join(COMMANDS, f)) for f in os.listdir(COMMANDS) if f.endswith(".json")]
        return max(ages) if ages else 0.0
    except OSError:
        return 0.0


def consuming() -> bool:
    # 「队列有老指令」不等于「没人消费」：最近仍有领取说明 Leader 在消化积压，继续保活
    try:
        import bus_projects as P
        age = P.freshest_claim_age()
        return age is not None and age < STUCK_SEC
    except Exception:
        return False


while True:
    try:
        if oldest_pending() > STUCK_SEC and not consuming():
            # 指令没人消费：停止保活并交出租约，让顶替顺序里的第一个在线 Agent 接管；远端 Lead 回来后会重新拿到
            B.release_lease("controller")
            time.sleep(INTERVAL)
            continue
        l = B.acquire_lease("controller")
        if l.get("holder") == "controller":
            B.renew_lease("controller")
            c = B.read_json(os.path.join(B.PEERS, "controller.json"), {}) or {}
            # 远端 Lead 正在处理时它自己会写 busy 心跳；这里只在空闲或过期时补 idle 心跳。
            # session="keepalive"：与远端 Leader 会话区分开，多会话心跳互不覆盖
            if c.get("status") != "busy" or B.peer_state("controller").get("age_sec", 0) > 60:
                B.heartbeat("controller", "idle", "controller", session="keepalive")
    except Exception as exc:
        try:
            with open(os.path.join(HERE, "console", "lead_keepalive.log"), "a", encoding="utf-8") as fh:
                fh.write(f"{B.now_iso()} {exc}\n")
        except OSError:
            pass
    time.sleep(INTERVAL)
