import datetime as dt
import json
import os
import re
import threading
import time
import uuid

import bus_lead_notify as LN

ROOT = os.path.dirname(os.path.abspath(__file__))


def _resolve_workspace() -> str:
    """Workspace served to agents. Precedence: env > workspace.json > project root.

    Historically this was os.path.dirname(os.path.dirname(ROOT)), i.e. the parent
    project that contained workbench/bus. The workbench is now a standalone project,
    so the default is ROOT itself and anything else must be stated explicitly.
    """
    env = os.environ.get("BUS_WORKSPACE")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    cfg = os.path.join(ROOT, "workspace.json")
    if os.path.isfile(cfg):
        try:
            with open(cfg, encoding="utf-8-sig") as fh:
                target = (json.load(fh) or {}).get("workspace") or ""
            if target:
                if not os.path.isabs(target):
                    target = os.path.join(ROOT, target)
                return os.path.abspath(target)
        except (OSError, ValueError):
            pass
    return ROOT


WORKSPACE = _resolve_workspace()
PEERS = os.path.join(ROOT, "peers")
INBOX = os.path.join(ROOT, "inbox")
OUTBOX = os.path.join(ROOT, "outbox")
LOG = os.path.join(ROOT, "log")
POLL_SEC = 2
HEARTBEAT_STALE_SEC = 180
# 心跳时间比「现在」晚超过这么多秒 = 时钟/时区错乱，该心跳不可信（见 heartbeat_age）
CLOCK_SKEW_SEC = 300
IDLE_TEXT = "IDLE. Call bus_wait_task again."

# ---- Agent 状态词表：唯一真源（合并稿 20260929-125009-e839 第 1、2 节）----
# 两个正交字段：online（能否联系上，只看 age）与 activity（在干什么）；role 另存，不是第 8 态。
ACTIVITIES = ("idle", "think", "build", "discuss")
LEGACY_ACTIVITY = {"busy": "think", "relay": "think"}          # 旧心跳值 → 新 activity
LEGACY_STATUS = {"idle": "idle", "think": "busy", "build": "busy", "discuss": "busy"}  # 双写：新 activity → 旧 status
AGENT_STATES = ("build", "discuss", "think", "idle", "stale_with_queue", "offline", "unknown")  # 兼排序优先级
AGENT_STATE_LABEL = {"offline": "离线", "unknown": "未接入", "stale_with_queue": "离线·有排队",
                     "idle": "待命", "think": "思考中", "build": "施工中", "discuss": "讨论中"}
AGENT_STATE_TONE = {"offline": "muted", "unknown": "muted", "stale_with_queue": "danger",
                    "idle": "ok", "think": "info", "build": "violet", "discuss": "warn"}
ROLES = ("relay",)
ROLE_LABEL = {"relay": "代理 Leader"}


def normalize_activity(value) -> str:
    """把任意历史写法归一到 ACTIVITIES；认不出的一律当 idle，绝不返回未知值。"""
    v = str(value or "").strip()
    v = LEGACY_ACTIVITY.get(v, v)
    return v if v in ACTIVITIES else "idle"


def activity_of(p: dict) -> str:
    """从 peer 记录取 activity：优先新字段，回落旧 status。给排序/挑人用，双写期间与旧口径等价。"""
    return normalize_activity((p or {}).get("activity") or (p or {}).get("status"))


def legacy_status(activity: str) -> str:
    return LEGACY_STATUS.get(normalize_activity(activity), "idle")


def agent_state(p: dict) -> str:
    """合成单一 state key。unknown 只在 peers/<peer>.json 缺失时出现（peer_state 已标记）。"""
    p = p or {}
    if p.get("status") == "unknown" and p.get("last_seen") is None:
        return "unknown"
    if not p.get("online"):
        # queued 拆分后只剩「待领」，离线且只有 .taken 的 peer 同样算「活没干完」，
        # 这里按总量判定，否则 stale_with_queue 会静默退化成 offline（4e6b 验收 4）
        return "stale_with_queue" if (p.get("queued") or p.get("claimed")) else "offline"
    return activity_of(p)


def agent_vocab() -> dict:
    """下发给前端的词表，前端不得二次硬编码（合并稿第 2 节）。"""
    return {"activities": list(ACTIVITIES), "states": list(AGENT_STATES),
            "label": dict(AGENT_STATE_LABEL), "tone": dict(AGENT_STATE_TONE),
            "roles": list(ROLES), "role_label": dict(ROLE_LABEL)}
MODES = {
    "discuss": "方案讨论。只输出 Markdown 结论：方案、取舍、风险、建议采纳项。本模式没有写权限，不要调用 ws_write / ws_patch。",
    "build": "施工。你有一棵独立的 git worktree（ws_read / ws_write / ws_patch / ws_run 的根目录已经指向它），直接修改代码并自测。回复时用 answer 写清改了什么、为什么、怎么验证的；系统会自动 commit 你的改动并送Reviewer审查，Leader合并后才进主线。",
    "review": "审查。任务包里是一位Agent的 diff 与门禁结果。只审不改：逐项核对正确性、边界、回归风险；回复第一行必须是 PASS 或 RETURN，后面写理由与必须修改的点。",
    "independent": "独立作答。只依据任务包内容回答，给出你的结论、理由、与结论相反的最强反驳各一段。",
    "redteam": "红队。任务包里有一份方案。找出它会失败的三个最具体的理由，每条给出触发条件与后果，最后给一条修改建议。",
    "relay": "接力。任务包里有一份骨架。在骨架上补充细节，所有改动用「[新增]」「[修改]」标出，保留原有编号。",
}


def _local_tz():
    """本机当前时区偏移。每次现取：常驻进程里系统时区改了也能跟上。"""
    return dt.datetime.now().astimezone().tzinfo


def now_dt() -> dt.datetime:
    """当前时间，带本地偏移（aware）。所有时间差计算的唯一起点。"""
    return dt.datetime.now().astimezone()


def now_iso() -> str:
    # ADR 0010 第 2 步：时间戳一律带偏移。进程缓存的时区新旧、机器时区怎么改，比较结果都正确。
    return now_dt().replace(microsecond=0).isoformat()


def iso_after(seconds: float) -> str:
    """now + seconds 的带偏移时间戳（租约 expires、deadline 等用）。"""
    return (now_dt() + dt.timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def parse_ts(value):
    """把时间戳解析成 aware datetime；解析不了返回 None。

    接受三种写法：
      · 带偏移（新）  2026-10-01T12:32:14+08:00
      · 末尾 Z        2026-10-01T04:32:14Z
      · 无偏移（旧）  2026-10-01T12:32:14  —— legacy 策略：按**本机当前本地时区**补偏移。
    legacy 这条是有损的：那串字符本来就没记录是哪个时区，机器时区改过之后旧值必然偏，
    这正是 ADR 0010 要消灭的来源。它只保证「不崩、按最可能的解释走」，新写入一律带偏移，
    旧值会随着心跳/租约刷新在一个 TTL 内自然换掉。
    """
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_local_tz())
    s = str(value or "").strip()
    if not s:
        return None
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        d = dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        try:                                   # 容忍 "...T12:32:14.123456" 之外的尾巴（旧日志里出现过）
            d = dt.datetime.fromisoformat(s[:19])   # parse_ts 内部兜底：尾巴坏掉时至少取到秒
        except (TypeError, ValueError):
            return None
    # 用 replace 而不是 astimezone()：后者对 9999 这类越界年份在 Windows 上直接抛 OSError(22)
    return d if d.tzinfo else d.replace(tzinfo=_local_tz())


def age_sec(value, default=None):
    """now - value 的秒数；解析不了返回 default（默认 None，由调用方决定方向）。"""
    d = parse_ts(value)
    return default if d is None else (now_dt() - d).total_seconds()


def rec_ts(rec, *keys, default=0.0):
    """记录里多个时间键依次解析成 epoch 秒；全部解析不了返回 default。

    排序/比较统一走它（ADR 0010）：不能拿时间戳字符串直接比——无偏移旧串与带偏移新串
    的字典序随时区漂移，会把「最新一轮 / latest」挑错。
    """
    for k in keys:
        d = parse_ts((rec or {}).get(k))
        if d:
            return d.timestamp()
    return default


def deadline_after(minutes) -> tuple:
    """(本地时间字符串, UTC 时间戳)。字符串给人看、兼容旧读者；判断过期只认时间戳（ADR 0010）。"""
    return (iso_after(float(minutes) * 60),
            round(time.time() + float(minutes) * 60, 3))


def deadline_passed(rec) -> bool:
    """任务 / meta 是否已过截止。
    本地时间字符串跨进程不可比：Windows 上 Python 进程沿用启动时的时区，用户切过时区后，
    各 Agent 的 MCP 进程、网页服务各自认定的「现在」可以差 12 小时——实况是任务一派发就被判过期（.json.expired）。
    有 deadline_ts（epoch 秒，与时区无关）就只看它；没有的旧任务退回原来的字符串比较。"""
    rec = rec if isinstance(rec, dict) else {}
    ts = rec.get("deadline_ts")
    if rec.get("queue_until_claim") and ts == 0 and not rec.get("taken_at"):
        return False  # 外部队列尚无人领取，执行期限从领取时开始
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return time.time() > ts
    d = parse_ts(rec.get("deadline"))
    if d is None:                 # 没有 deadline / 解析不了：按「未过期」处理，避免把任务当场判死
        return False
    return now_dt() > d


def ensure_dirs():
    for d in (PEERS, INBOX, OUTBOX, LOG):
        os.makedirs(d, exist_ok=True)


def _open_shared(path: str):
    """读文件时允许别人同时替换它。Windows 上 Python 的 open 不带 FILE_SHARE_DELETE：只要有人在读，另一个进程的
    os.replace（atomic_write_json）就会被拒（2026-10-03 实测：工作台每秒读决策表，接手脚本写同一张表 1 秒内 6 次重试全失败）。
    带上 FILE_SHARE_DELETE 后替换照常成功，读的一方拿到的是打开那一刻的完整旧内容。其它系统没有这个问题，直接 open。"""
    if os.name != "nt":
        return open(path, encoding="utf-8")
    import ctypes
    import msvcrt
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    GENERIC_READ, SHARE_ALL, OPEN_EXISTING, NORMAL = 0x80000000, 0x1 | 0x2 | 0x4, 3, 0x80
    h = k32.CreateFileW(os.fspath(path), GENERIC_READ, SHARE_ALL, None, OPEN_EXISTING, NORMAL, None)
    if h in (None, wintypes.HANDLE(-1).value):
        err = ctypes.get_last_error()
        if err in (2, 3):
            raise FileNotFoundError(2, "No such file", path)
        raise PermissionError(13, ctypes.FormatError(err).strip(), path) if err in (5, 32, 33) else OSError(err, ctypes.FormatError(err).strip(), path)
    try:
        fd = msvcrt.open_osfhandle(h, os.O_RDONLY)
    except Exception:
        k32.CloseHandle(h)
        raise
    return open(fd, encoding="utf-8", closefd=True)


def read_json(path: str, default=None):
    # Windows 上读文件恰好撞上另一进程的原子替换（心跳、决策表每秒都在写）会 PermissionError，接口因此 500（41db8cf / 6c31）。
    # 短暂重试；仍不行就抛出——绝不能当成「文件不存在」返回默认值：先读后写的路径（upsert_decision 等）会拿空表覆盖整个文件
    for attempt in range(6):
        try:
            with _open_shared(path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.02 * (attempt + 1))


def write_json(path: str, data):
    from bus_locks import atomic_write_json
    atomic_write_json(path, data)


def write_text(path: str, text: str):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def valid_name(name: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name or ""):
        raise ValueError(f"peer 名称不合法：{name!r}，只允许小写字母、数字、下划线、连字符")
    return name


def heartbeat(peer: str, status: str = "idle", model: str = "", task_id: str = "", wait_started: str = "", wait_timeout: int = 0, role: str = "", session: str = "", label: str = "", temp=None):
    # 第二参语义已升格为 activity（idle/think/build/discuss），旧值 busy/relay 由 normalize_activity 归一。
    # role 与 activity 正交（relay = 代理 Leader），默认空表示没有附加角色。
    # session：多 Leader 并行接入时的会话标识（仅 controller 使用），逐会话记录，互不清除。
    # temp：过路调用者标记（fb-1b5ec6d4cf），None=保持原值。心跳写失败只跳过这一拍，不能中断 wait
    try:
        _heartbeat(peer, status, model, task_id, wait_started, wait_timeout, role, session, label, temp)
    except OSError:
        pass


SESSION_TTL_SEC = 600  # 会话心跳超过此时长未更新即从 peers/<peer>.json 的 sessions 里清除
TEMP_SESSION_TTL_SEC = 120  # fb-1b5ec6d4cf：过路调用者的临时会话（无显示名、未领指令）用更短的 TTL 清除


def _merge_session(data: dict, session: str, activity: str, model: str, task_id: str, wait_started: str, wait_timeout: int, role: str, label: str = "", temp=None):
    """把一次会话心跳并入 data["sessions"]。多 Leader 并行接入时顶层字段只反映最近一拍，
    各会话的独立状态记录在 sessions 里（在线判定不变：只看顶层 last_seen）。
    temp：过路调用者标记（fb-1b5ec6d4cf）。None=保持原值；True=本次标为临时；False=转正（报了显示名）。
    临时会话用 TEMP_SESSION_TTL_SEC 清除，不占 600 秒全量 TTL——sessions 不能因过路调用无限堆积。"""
    sessions = data.get("sessions") if isinstance(data.get("sessions"), dict) else {}
    prev = sessions.get(session) or {}
    temp_flag = prev.get("temp", False) if temp is None else bool(temp)
    if label:
        temp_flag = False   # 报了显示名的就是真 Leader，临时标记即刻清除
    sessions[session] = {"session": session, "activity": activity, "last_seen": now_iso(), "task_id": task_id,
                         "model": model or prev.get("model", ""), "role": role if role in ROLES else "",
                         "wait_started": wait_started or prev.get("wait_started", ""),
                         "wait_timeout": wait_timeout or prev.get("wait_timeout", 0),
                         # 每位 Leader 自己的显示名（多活）：只写本会话，不再改顶层 label——顶层只有一份，新接入者会把别人的名字顶掉
                         "label": str(label or prev.get("label", ""))[:40],
                         "joined": prev.get("joined") or now_iso(),
                         "temp": temp_flag}
    live = {}
    for sid, s in sessions.items():
        age, skewed = heartbeat_age(s.get("last_seen"))
        if skewed or age > SESSION_TTL_SEC:
            continue
        if s.get("temp") and age > TEMP_SESSION_TTL_SEC:
            continue   # 临时会话短 TTL：过路调用者不留长痕
        live[sid] = s
    data["sessions"] = live


def _heartbeat(peer: str, status: str, model: str, task_id: str, wait_started: str, wait_timeout: int, role: str = "", session: str = "", label: str = "", temp=None):
    ensure_dirs()
    path = os.path.join(PEERS, f"{peer}.json")
    # 读-改-写必须与 set_label 互斥：否则改名与心跳并发时，后写的一方会用自己的旧快照覆盖对方
    with LN.file_mutex(LN.peer_mutex_path(PEERS, peer)):
        data = read_json(path, {}) or {}
        first = not data
        activity = normalize_activity(status)
        # 双写：新 activity + 旧 status（idle/busy），旧消费方按原样继续工作
        data.update({"peer": peer, "status": legacy_status(activity), "activity": activity,
                     "role": role if role in ROLES else "", "last_seen": now_iso(), "task_id": task_id})
        if session:
            _merge_session(data, session, activity, model, task_id, wait_started, wait_timeout, role, label, temp)
        if first:
            # 首次落盘：把首次连接前配置的 pending 显示名带上（纯显示字段，协议字段不变）
            lb = LN.pending_label(ROOT, peer)
            if lb:
                data["label"], data["label_updated"] = lb, now_iso()
        if model:
            data["model"] = model
        if wait_started:
            # 记录本次阻塞 wait 的起点与时长，工作台据此推算该 peer 何时能领取新任务
            data["wait_started"] = wait_started
            data["wait_timeout"] = wait_timeout
        write_json(path, data)
    # 只有总线代码会写这份「总线心跳」：外部脚本直接改 peers/<peer>.json 续命（tmp/glm3_keepalive.py 那种）不算在线
    try:
        write_json(_beat_path(peer), {"at": data["last_seen"], "activity": activity, "task_id": task_id})
    except OSError:
        pass
    if first and data.get("label"):
        try:
            LN.set_pending_label(ROOT, peer, "")
        except Exception:
            pass


def set_label(peer: str, label: str, pending: bool = False) -> dict:
    # 显示层改名。peer / status / 租约 holder 等协议字段一律不动。
    # peer 文件不存在 → LN.PeerMissing（API 404），不造幽灵登记；pending=True 写 console/pending_labels.json，首次心跳时生效
    valid_name(peer)
    ensure_dirs()
    if not LN.features(ROOT).get("lead_label", True):
        raise PermissionError("显示名功能已关闭（console/features.json lead_label=false）")
    if pending:
        return LN.set_pending_label(ROOT, peer, label)
    return LN.set_label(PEERS, peer, label)


def set_session_label(peer: str, session: str, label: str) -> dict:
    """给某一位 Leader 会话改显示名（多活）。只改 sessions[session].label，顶层 label 与协议字段不动。
    会话不在（已断开 / 会话号写错）抛 KeyError（API 转 404）。空串清除。"""
    valid_name(peer)
    if not LN.features(ROOT).get("lead_label", True):
        raise PermissionError("显示名功能已关闭（console/features.json lead_label=false）")
    label = LN.validate_label(label)
    path = os.path.join(PEERS, f"{peer}.json")
    with LN.file_mutex(LN.peer_mutex_path(PEERS, peer)):
        data = read_json(path, {}) or {}
        sessions = data.get("sessions") if isinstance(data.get("sessions"), dict) else {}
        if session not in sessions:
            raise KeyError(f"会话 {session} 不在线（已断开或会话号不对），改名未保存")
        sessions[session]["label"] = label
        data["sessions"] = sessions
        write_json(path, data)
    return {"peer": peer, "session": session, "label": label}


def label_of(peer: str) -> str:
    if not LN.features(ROOT).get("lead_label", True):
        return ""
    return LN.get_label(PEERS, peer) or LN.pending_label(ROOT, peer)


def display_name(peer: str) -> str:
    return LN.display_name(PEERS, peer)


def heartbeat_age(last_seen) -> tuple:
    """距上次心跳的秒数，返回 (age_sec, skewed)。全仓判在线 / Leader 租约都走这里。

    时间戳是无时区的本地时间（now_iso）。系统时区一变（2026-10-01 远端被自动改到美东 EDT），
    旧心跳就落在「未来」，负 age 恒满足 age <= 180 → 离线者显示在线、Leader 租约永不过期。
    小于 CLOCK_SKEW_SEC 的超前按正常机器误差容忍（记 0）；更大的超前判不可信、按失联算并标 skewed。"""
    age = age_sec(last_seen)
    if age is None:
        # 解析失败 = 按失联。方向是有意选的（ADR 0010）：反方向「按在线」会让一个时间戳坏掉的
        # Agent 被当成活的一直派单、任务石沉大海；按失联最多是多一次顶替，代价小得多。
        return 1e9, False
    if age < -CLOCK_SKEW_SEC:
        return 1e9, True
    return max(age, 0.0), False


def _beat_path(peer: str) -> str:
    # 跟着 PEERS 走（测试把 PEERS 指到临时目录时一起走）：<根>/console/beats/<peer>.json
    return os.path.join(os.path.dirname(PEERS), "console", "beats", f"{peer}.json")


LISTEN_SLACK_SEC = 90
STRIKE_WINDOW_SEC = 24 * 3600
STRIKE_BENCH = 2   # 24 小时内「领了不做被收回」满 2 次：不再给它派新单，直到它交出一次回复


def _strikes_path() -> str:
    return os.path.join(os.path.dirname(PEERS), "console", "strikes.json")


def add_strike(peer: str, task_id: str) -> int:
    s = read_json(_strikes_path(), {}) or {}
    s.setdefault(peer, []).append({"at": now_iso(), "task_id": task_id})
    s[peer] = s[peer][-20:]
    write_json(_strikes_path(), s)
    return strikes(peer)


def strikes(peer: str) -> int:
    rows = (read_json(_strikes_path(), {}) or {}).get(peer) or []
    return sum(1 for r in rows if (age_sec(r.get("at")) or 1e9) <= STRIKE_WINDOW_SEC)


def clear_strikes(peer: str) -> None:
    s = read_json(_strikes_path(), None)
    if isinstance(s, dict) and s.pop(peer, None) is not None:
        write_json(_strikes_path(), s)


def benched(peer: str) -> bool:
    return strikes(peer) >= STRIKE_BENCH


def touch_work(peer: str) -> None:
    """Agent 调 ws_* 工具 = 正在干活的证据：把心跳刷成「在做手上那一单」。
    以前干活期间不心跳，3 分钟后真干活的人显示离线；而外部脚本续命的人显示在线。"""
    if not peer or peer == "controller":
        return
    d = os.path.join(INBOX, peer)
    try:
        taken = [f for f in os.listdir(d) if f.endswith(".taken")]
    except OSError:
        return
    if not taken:
        return
    f = max(taken, key=lambda n: os.path.getmtime(os.path.join(d, n)))
    task = read_json(os.path.join(d, f), {}) or {}
    heartbeat(peer, "build" if task.get("mode") == "build" else "think", "", f[:-6])


def peer_state(peer: str) -> dict:
    data = read_json(os.path.join(PEERS, f"{peer}.json"), None)
    if not isinstance(data, dict) or not data:
        # 文件根不是 dict（残件/外部工具写坏）与文件缺失同待遇：读取出口兜底，list_peers/relay_resolution 不崩
        return {"peer": peer, "online": False, "status": "unknown", "last_seen": None, "label": ""}
    # 文件存在但残缺（手工编辑/半截写入/外部工具）也要有 peer 键：relay_resolution/pick_reviewers
    # 按 p["peer"] 直接下标，缺了就 KeyError（68e6 审查 ②：兜底放在读出口，任何来源的残件都不该打死顶替链）
    data.setdefault("peer", peer)
    beat = read_json(_beat_path(peer), None)
    if isinstance(beat, dict) and beat.get("at"):
        # 总线记过心跳：只认它。peers 文件里的 last_seen 可能被外部脚本刷新过
        age, skewed = heartbeat_age(beat["at"])
        data["beat_source"] = "bus"
    else:
        # 总线从没记过（升级前 / 测试夹具 / 只有外部脚本在写）：退回看 peers 文件。只靠脚本续命的人
        # 不会在 wait 里（listening=False），派给它的单 30 分钟没人领会被 free_stalled 收走
        age, skewed = heartbeat_age(data.get("last_seen"))
        data["beat_source"] = "file"
    if skewed:
        data["clock_skew"] = True          # 前端/排障可见：不是掉线，是时间戳不可信
    data["age_sec"] = int(age)
    # online 只看 age：状态枚举一变 online 就跟着错（代理 Leader 期间 status=relay 被判离线）。两者解耦。
    data["online"] = age <= HEARTBEAT_STALE_SEC
    raw_status = str(data.get("status") or "")
    data["activity"] = activity_of(data)
    role = data.get("role") if data.get("role") in ROLES else ""
    if not role and raw_status == "relay":
        role = "relay"   # 兼容改造前写下的心跳：旧 status=relay 里带着角色信息，别丢
    data["role"] = role
    data["status"] = legacy_status(data["activity"])  # 双写：旧消费方与前端零改动
    # 正在等任务：在线、空闲、而且确实处在一次 wait 里（wait 起点 + 超时 + 余量之内）。在线但不在等 = 派给它也没人领
    wa = age_sec(data.get("wait_started")) if data.get("wait_started") else None
    data["listening"] = bool(data["online"] and data["activity"] == "idle" and wa is not None
                             and wa <= float(data.get("wait_timeout") or 0) + LISTEN_SLACK_SEC)
    data["label"] = data.get("label") or ""
    # group_hint 暴露（9e1e ⑦）：净化白名单字段 + effective_group + classification_state；旧字段全部保留
    hint = data.get("group_hint") if isinstance(data.get("group_hint"), dict) else {}
    data["effective_group"] = str(data.get("group") or "") or _hint_effective_group(data) or group_of_model_cached(data.get("model", ""))
    data["classification_state"] = str(hint.get("state") or "unclassified")
    data["group_hint"] = {k: hint[k] for k in HINT_PUBLIC_KEYS if k in hint}  # 内部簿记（probe 次数等）不外露
    return data


def list_peers() -> list:
    ensure_dirs()
    return [peer_state(f[:-5]) for f in sorted(os.listdir(PEERS)) if f.endswith(".json")]


def prune_peers(max_age_sec: int = 43200, dry_run: bool = False) -> list:
    """清理离线超过 max_age_sec 且收件箱无未完成任务的失效 Agent 登记。

    保护条件（严格排除，绝不误删）：
    1. peer == 'controller' 或为当前 Leader 租约持有者；
    2. online 为 True 的 Agent；
    3. 时钟异常（st.get("clock_skew")）或时间戳不可信/解析失败（age_sec >= 1e8 哨兵值）；
    4. 离线时长未超过 max_age_sec（默认 12 小时 = 43200 秒）；
    5. inbox/ 下仍有待领 (.json) 或已领未回 (.taken) 任务包（inbox_queued(peer) > 0）；
    6. 进锁复查：持锁重读文件，确认 last_seen 未被并发心跳刷新，消除 TOCTOU 竞态。

    安全归档：被清理的 peer 文件先归档至 log/<日期>/pruned-peers/<peer>.json 留证，
    避免用户自定义别名或归类数据永久丢失。
    """
    ensure_dirs()
    cur_lease = lease()
    lease_holder = cur_lease.get("holder") or ""
    pruned = []
    try:
        max_age = float(max_age_sec)
    except (TypeError, ValueError):
        max_age = 43200.0

    today = dt.datetime.now().strftime("%Y-%m-%d")
    archive_dir = os.path.join(LOG, today, "pruned-peers")

    for f in sorted(os.listdir(PEERS)):
        if not f.endswith(".json"):
            continue
        peer = f[:-5]
        if peer == "controller" or peer == lease_holder:
            continue
        st = peer_state(peer)
        if st.get("online"):
            continue
        if st.get("clock_skew"):
            continue
        age_sec = st.get("age_sec", 0)
        # 1e8 拦截 heartbeat_age 在时间戳损坏或不可信时返回的 1e9 哨兵门限
        if age_sec >= 1e8 or age_sec < max_age:
            continue
        if inbox_queued(peer) > 0:
            continue

        if dry_run:
            pruned.append(peer)
            continue

        peer_file = os.path.join(PEERS, f)
        with LN.file_mutex(LN.peer_mutex_path(PEERS, peer)):
            try:
                # 进锁复查：避免 TOCTOU
                cur_data = read_json(peer_file, None)
                if not cur_data:
                    continue
                cur_age, cur_skewed = heartbeat_age(cur_data.get("last_seen"))
                if cur_skewed or cur_age < max_age or cur_age >= 1e8:
                    continue
                if inbox_queued(peer) > 0:
                    continue
                # 归档留档
                os.makedirs(archive_dir, exist_ok=True)
                archive_file = os.path.join(archive_dir, f)
                write_json(archive_file, cur_data)
                # 安全删除
                if os.path.isfile(peer_file):
                    os.remove(peer_file)
                    pruned.append(peer)
            except OSError:
                pass
    return pruned


def new_task_id() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


def resolve_context(paths: list) -> list:
    """Read only bounded, selected and credential-checked references (ADR 0018)."""
    from bus_context import collect_context
    return collect_context(WORKSPACE, paths)


def render_task(task: dict) -> str:
    lines = [
        f"# 任务 {task['id']}",
        "",
        f"- 发起者：{task['from']}",
        f"- 模式：{task['mode']}",
        f"- 截止：{task['deadline']}",
        "",
        "## 模式要求",
        "",
        MODES.get(task["mode"], task["mode"]),
        "",
        "## 问题",
        "",
        task["question"].strip(),
        "",
    ]
    if task.get("role_line"):
        lines += [task["role_line"], ""]
    if task.get("angle"):
        lines += ["## 你的切入角度", "", task["angle"], "", "只从这个角度作答，其他角度由别的Agent负责；同一个点别人会说的就不要写。", ""]
    if task.get("worktree"):
        lines += ["## 你的施工目录", "", f"`{task['worktree']}`（分支 `{task.get('branch', '')}`）。你的 ws_* 工具已经以这个目录为根，路径直接写相对路径。不要碰 `.wt/` 之外的其他Agent目录。", ""]
    from bus_context import reference_lines
    for c in task.get("context", []):
        lines += reference_lines(c)
    lines += [
        "## 回复方式",
        "",
        f"用 bus_reply(task_id=\"{task['id']}\", peer=\"{task['to']}\", answer=...) 回复（参数名 peer 就填你的Agent名）。answer 为 Markdown 正文，不要复述任务包。回复后立即再次调用 bus_wait_task。",
        "",
    ]
    return "\n".join(lines)


ANGLES = [
    "站在最终用户角度：只谈体验与结果，不谈实现",
    "站在维护者角度：只谈最简实现与最少改动",
    "站在反对者角度：先找这个需求本身的问题，再给替代方案",
    "站在运维角度：故障、超时、回滚、可观测性",
    "站在成本角度：token、时间、人的注意力",
    "站在安全角度：越权、注入、泄露",
    "站在数据角度：先列出需要核实的数字，再下结论",
    "站在设计师角度：信息架构与交互，不谈后端",
    "站在新手角度：哪里看不懂、哪里会误操作",
]


AUTO_TOKEN = "auto"
AUTO_MAX_PEERS = 2


def auto_pick(exclude=(), max_peers: int = AUTO_MAX_PEERS) -> dict:
    """自动挑「在线 + 空闲 + 手上没有未回复任务 + 心跳时间可信」的 Agent，并逐个说明取舍理由。

    理由必须回给调用方（派单 20261001-142456-ed28）：Leader 要能复核「为什么是他不是别人」，
    否则 auto 只是把「派给离线的人」换成「派给一个说不清的人」。排序用（心跳新→旧, peer 名）全序，
    同一份 peers/*.json 多次调用结果一致。不做关键词猜测，mode 仍由调用方决定。
    """
    ex = {str(e) for e in exclude if e}
    ex.add("controller")                      # Leader 自己不是可派对象（与 relay_resolution 同口径）
    picked, skipped = [], []
    for p in list_peers():
        peer = p["peer"]
        if peer in ex:
            skipped.append({"peer": peer, "reason": "调用方排除（Leader/发起人）"})
            continue
        if not p.get("online"):
            reason = "时钟错乱（clock_skew），心跳不可信" if p.get("clock_skew") else (
                "心跳时间戳解析失败，按失联处理" if int(p.get("age_sec") or 0) >= 10 ** 8 else "离线")
            skipped.append({"peer": peer, "reason": reason})
            continue
        if p.get("role") == "relay":
            skipped.append({"peer": peer, "reason": "正在代理 Leader，不接派单"})
            continue
        activity = activity_of(p)
        if activity != "idle":
            skipped.append({"peer": peer, "reason": f"忙（activity={activity}）"})
            continue
        c = inbox_counts(peer)
        if c["queued"] + c["claimed"]:
            skipped.append({"peer": peer, "reason": f"手上还有未回复的任务（待领 {c['queued']} / 已领 {c['claimed']}）"})
            continue
        picked.append(p)
    picked.sort(key=lambda x: (-rec_ts(x, "last_seen"), x["peer"]))
    n = max(1, int(max_peers or AUTO_MAX_PEERS))
    for p in picked[n:]:
        skipped.append({"peer": p["peer"], "reason": f"已够 {n} 个，按心跳新鲜度排在后面"})
    return {"picked": [p["peer"] for p in picked[:n]], "idle_candidates": [p["peer"] for p in picked],
            "max_peers": n, "reasons": skipped}


def _infer_project_for_peer(peer: str, parent_task: str = "") -> str:
    """尝试判定 from_peer 的归属项目；判不出来返回空串。不瞎猜，不默认塞项目（体验反馈 fb-28bb2a1d9a）。"""
    if parent_task:
        try:
            import bus_projects
            p = bus_projects.project_for_task(parent_task)
            if p:
                return str(p).strip()
        except Exception:
            pass
    p = str(peer or "").strip()
    if not p:
        return ""
    rec = read_json(os.path.join(PEERS, f"{p}.json"), None)
    if isinstance(rec, dict):
        if rec.get("project"):
            return str(rec["project"]).strip()
        cur_tid = rec.get("task_id")
        if cur_tid:
            try:
                import bus_projects
                pr = bus_projects.project_for_task(cur_tid)
                if pr:
                    return str(pr).strip()
            except Exception:
                pass
    return ""


def dispatch(from_peer: str, peers: list, mode: str, question: str, context_files: list, ttl_min: int = 30, angles=None, min_answers: int = 0, repo_dir: str = "", review_of=None, parent_task: str = "", intro: str = "", *, max_peers: int = 0, race: bool = False, substitute: bool = True, external_owner: str = "", queue_until_claim: bool = False, project: str = "", team: str = "", plugin_request_id: str = "", task_id_override: str = "") -> dict:
    from bus_context import check_text
    check_text(question)
    from bus_dispatch_scope import resolve as resolve_scope
    normalized_parent = str(parent_task or '').strip()
    if normalized_parent and not re.fullmatch(r'\d{8}-\d{6}-[0-9a-zA-Z]{2,12}', normalized_parent):
        normalized_parent = ''
    project, repo_dir, project_source = resolve_scope(mode, project, normalized_parent, repo_dir,
                                                     lambda: _infer_project_for_peer(from_peer, normalized_parent))
    ensure_dirs()
    if mode not in MODES:
        raise ValueError(f"mode 只能是 {', '.join(MODES)}")
    if not question or not question.strip():
        raise ValueError("question 不能为空")
    if external_owner and not queue_until_claim and (mode != "independent" or substitute or len(peers or []) != 1):
        raise ValueError("外部任务只能点名一个 Agent，且不能自动替换")
    if queue_until_claim and (not external_owner or substitute or not peers or mode in ("build", "review")):
        raise ValueError("外部队列参数不合法")
    # 兼容式校验：不是 task_id 形状就当没传，不报错——老调用方不传、网页可能传别的串，
    # 派发不该因为一个纯展示用的字段整单失败。
    parent_task = str(parent_task or "").strip()
    if parent_task and not re.fullmatch(r"\d{8}-\d{6}-[0-9a-zA-Z]{2,12}", parent_task):
        parent_task = ""
    intro = str(intro or "").strip()
    # peers 里出现 "auto" = 由总线挑人（大小写不敏感）。放在最前面：挑不出人要在建任务/建 worktree 之前
    # 立即失败，否则又会造出一个「必然静默过期」的任务（fb-2c3e8c868c 要消灭的那类事故）。
    # 同一列表里显式点名的 peer 仍然照发（不因来了 auto 就消失），auto 只补选空闲的。
    auto = None
    peers = list(peers or [])
    if external_owner and (peers[0] in ("controller", "auto") or (not queue_until_claim and not peer_state(peers[0]).get("listening"))):
        raise ValueError("被点名的 Agent 当前未在监听")
    if any(str(p).strip().lower() == AUTO_TOKEN for p in peers):
        named = [p for p in peers if str(p).strip().lower() != AUTO_TOKEN]
        auto = auto_pick(exclude=[from_peer] + named, max_peers=max_peers)
        if not auto["picked"]:
            raise ValueError("auto: 没有在线空闲的 Agent，任务未创建。排除理由："
                             + json.dumps(auto["reasons"], ensure_ascii=False))
        peers = list(dict.fromkeys(named + auto["picked"]))
    context = resolve_context(context_files)
    task_id = task_id_override or new_task_id()
    build_rec = None
    if mode == "build":
        import bus_build as BB
        swaps_pre = substitute_offline(list(peers)) if substitute else {}
        builders = [swaps_pre.get(p, p) for p in peers]
        build_rec = BB.open_worktrees(task_id, builders, repo_dir or WORKSPACE)
    deadline, deadline_ts = ("", 0) if queue_until_claim else deadline_after(ttl_min)
    offline = []
    # angles：dict 指定每个 peer 的切入角度；True 则按 ANGLES 轮流分配；None 不分配
    if angles is True:
        angles = {p: ANGLES[i % len(ANGLES)] for i, p in enumerate(peers)}
    angles = angles or {}
    author = (review_of or {}).get("teammate") if isinstance(review_of, dict) else None
    if author and author in peers:
        raise ValueError(f"{author} 是被审提交的作者，不能审自己的代码；换一位 Reviewer")
    swaps = substitute_offline(list(peers), exclude={author} if author else None) if substitute else {}
    if swaps:
        peers = [swaps.get(p, p) for p in peers]
        angles = {swaps.get(k, k): v for k, v in angles.items()}
    for peer in peers:
        valid_name(peer)
        st = peer_state(peer)
        if external_owner and not queue_until_claim and not st.get("listening"):
            raise ValueError("被点名的 Agent 当前未在监听")
        if not st["online"]:
            offline.append(peer)
        task = {"id": task_id, "from": from_peer, "to": peer, "mode": mode, "question": question, "angle": angles.get(peer, ""),
                "context": context, "created": now_iso(), "deadline": deadline, "deadline_ts": deadline_ts}
        if project:
            task.update(project=project, project_source=project_source)
        if external_owner:
            task["external_owner"] = external_owner
        if queue_until_claim:
            task.update(queue_until_claim=True, queue_ttl_min=ttl_min, project=project, team=team,
                        plugin_request_id=plugin_request_id)
        if race:
            task["race"] = True   # 抢单：谁先领谁做，领到的瞬间撤掉其他人的副本（_race_claim）
        if parent_task:
            task["parent_task"] = parent_task
        if intro:
            task["intro"] = intro
        if review_of is not None:
            if mode != "review" or not isinstance(review_of, dict):
                raise ValueError("Review binding is only valid for review tasks")
            task["review_of"] = dict(review_of)
        role_line = role_line_for(peer)
        if role_line:
            task["role_line"] = role_line
        if build_rec and peer in build_rec["teammates"]:
            task["worktree"] = build_rec["teammates"][peer]["path"]
            task["branch"] = build_rec["teammates"][peer]["branch"]
        d = os.path.join(INBOX, peer)
        os.makedirs(d, exist_ok=True)
        write_json(os.path.join(d, f"{task_id}.json"), task)
    # 法定份数：到齐这么多份就通知Leader合并，其余算迟到；0 = 默认「在线 peer 的过半数，至少 2，relay 为 1」
    expected = [p for p in peers if p not in offline] or list(peers)
    if not min_answers:
        min_answers = 1 if mode == "relay" else max(2, (len(expected) + 1) // 2)
    min_answers = min(min_answers, len(expected))
    meta = {"id": task_id, "from": from_peer, "peers": list(peers), "offline_at_dispatch": offline, "mode": mode, "question": question, "angles": angles,
            "min_answers": min_answers, "context_files": list(context_files or []), "created": now_iso(), "deadline": deadline, "deadline_ts": deadline_ts, "substituted": swaps}
    if external_owner:
        meta["external_owner"] = external_owner
    if queue_until_claim:
        meta.update(queue_until_claim=True, queue_ttl_min=ttl_min, project=project, team=team,
                    plugin_request_id=plugin_request_id)
        meta["min_answers"] = len(peers)
    if race:
        meta["race"] = True
        meta["min_answers"] = 1
    # 简介 / 父任务只在有值时落 meta：老调用方的 meta 形状不变（test_dispatch_auto_20261001 钉着字段集合）
    if parent_task:
        meta["parent_task"] = parent_task
    if intro:
        meta["intro"] = intro
    # 归属项目判定（体验反馈 fb-28bb2a1d9a）：优先取调用方显式传入的 project；
    # 取不到再尝试判定 from_peer 的归属；两者都判不出来留空。不瞎猜，不默认塞项目。
    if project:
        meta["project"] = project
        meta["project_source"] = project_source
    if review_of is not None:
        meta["review_of"] = dict(review_of)   # 审查单绑定的被审对象也落 meta：「催审」据此判断是否已有人在审
    if auto:
        meta["auto"] = auto
    if context:
        meta['context_manifest'] = [{k: v for k, v in item.items() if k != 'content'} for item in context]
    write_json(os.path.join(OUTBOX, f"{task_id}.meta.json"), meta)
    result = {"task_id": task_id, "peers": list(peers), "offline": offline, "deadline": deadline, "min_answers": min_answers, "substituted": swaps}
    if auto:
        result["auto"] = auto          # 只在用了 auto 时出现：显式派发的返回结构与旧行为逐字节一致
    return result


RELAY_CHECK_SEC = 30


REDELIVER_MAX = 1
REDELIVER_NOTE = "> 重新下发：这是你之前领取、但还没回复的任务（会话中断后领取没有释放）。请接着完成并 bus_reply；做不了就直接回复说明原因。\n\n"


def reclaim_claims(peer: str) -> tuple:
    """Agent 回到 wait_task = 它手上没有在做的活了。此时它名下「已领未回」的 .taken 是孤儿：
    不会再被任何 Agent 领走，又让这个空闲 Agent 看上去积压一堆（fb-8a7c79854b / fb-aded5a13c6 的根因之一）。

    返回 (要重新下发的 (path, task) 或 None, 释放掉的 task_id 列表)：
      · 任务仍开着、未过截止、还没重发过 → 原样还给它（同一人续做，worktree 不变）；
      · 已过截止或已重发过 REDELIVER_MAX 次 → 改名 .json.expired 释放（打 reclaimed_at，迟到回复仍收），
        普通任务随后由 auto_reassign 按 expired 改派，build/review 在进度里如实显示过期；
      · 任务已归档 / 已改派给别人 / 已回复 → 不动，留给既有清理路径。"""
    inbox = os.path.join(INBOX, peer)
    redeliver, released = None, []
    try:
        names = sorted(f for f in os.listdir(inbox) if f.endswith(".taken"))
    except OSError:
        return None, released
    for f in names:
        path = os.path.join(inbox, f)
        task = read_json(path, None)
        if not isinstance(task, dict) or not task.get("id"):
            continue
        task_id = f[:-6]
        meta = read_json(os.path.join(OUTBOX, f"{task_id}.meta.json"), None)
        if not isinstance(meta, dict) or peer not in (meta.get("peers") or []) \
                or os.path.exists(os.path.join(OUTBOX, f"{task_id}.{peer}.md")):
            continue
        if task.get("queue_until_claim"):
            if not deadline_passed(task):
                continue
        spent = bool(task.get("queue_until_claim")) or int(task.get("redelivered") or 0) >= REDELIVER_MAX
        if not spent and not deadline_passed(task):
            if redeliver is None:
                redeliver = (path, task)
            continue
        task["reclaimed_at"] = now_iso()
        try:
            write_json(path, task)
            os.replace(path, os.path.join(inbox, f"{task_id}.json.expired"))
            released.append(task_id)
        except OSError:
            pass  # 同时被 requeue/reassign 挪走：让对方赢
    return redeliver, released


RACE_LOST = ".taken_by_other"   # 抢单没抢到的副本：inbox/<peer>/<task>.json.taken_by_other，留痕不再领取


def _race_claim(task_id: str, peer: str) -> bool:
    """抢单（ADR 0013）：同一把锁里定胜负。第一个领到的记为 race_winner，并撤掉其他人还没领的副本、名单只剩自己；
    之后来的人看到已有胜者就返回 False。胜者自己再次领取（重连重领）返回 True。"""
    from bus_locks import file_mutex
    meta_path = os.path.join(OUTBOX, f"{task_id}.meta.json")
    with file_mutex(meta_path + ".race.mutex"):
        meta = read_json(meta_path, None) or {}
        if not meta.get("race"):
            return True
        winner = meta.get("race_winner")
        if winner:
            return winner == peer
        others = [p for p in meta.get("peers") or [] if p != peer]
        for p in others:
            src = os.path.join(INBOX, p, f"{task_id}.json")
            try:
                os.replace(src, src + RACE_LOST)
            except OSError:
                pass
        meta.update(race_winner=peer, race_at=now_iso(), race_withdrawn=others, peers=[peer], min_answers=1)
        _save_meta(meta)
    return True


def _external_queue_claim(task_id: str, peer: str) -> dict:
    """在任务级锁内核对、确定抢单胜者并完成领取。"""
    from bus_locks import file_mutex
    meta_path = os.path.join(OUTBOX, f"{task_id}.meta.json")
    src = os.path.join(INBOX, peer, f"{task_id}.json")
    with file_mutex(meta_path + ".race.mutex"):
        meta = read_json(meta_path, None)
        if not isinstance(meta, dict) or not meta.get("queue_until_claim") or meta.get("cancelled") or meta.get("sealed"):
            return {}
        if peer not in meta.get("peers", []) or not os.path.isfile(src):
            return {}
        winner = meta.get("race_winner")
        if meta.get("race") and winner and winner != peer:
            os.replace(src, src + RACE_LOST)
            return {}
        task = read_json(src, None)
        if not isinstance(task, dict) or task.get("id") != task_id:
            return {}
        if meta.get("race") and not winner:
            others = [p for p in meta["peers"] if p != peer]
            meta.update(race_winner=peer, race_at=now_iso(), race_withdrawn=others, peers=[peer], min_answers=1)
            _save_meta(meta)
            for other in others:
                other_src = os.path.join(INBOX, other, f"{task_id}.json")
                if os.path.isfile(other_src):
                    os.replace(other_src, other_src + RACE_LOST)
        task["deadline"], task["deadline_ts"] = deadline_after(task.get("queue_ttl_min") or meta.get("queue_ttl_min") or 30)
        task["taken_at"] = now_iso()
        write_json(src, task)
        os.replace(src, os.path.join(INBOX, peer, f"{task_id}.taken"))
        if not meta.get("deadline_ts") or task["deadline_ts"] > meta["deadline_ts"]:
            meta["deadline"], meta["deadline_ts"] = task["deadline"], task["deadline_ts"]
            _save_meta(meta)
        return task


def wait_task(peer: str, timeout_sec: int, model: str = "") -> str:
    # 阻塞轮询，任务到达 2 秒内返回；超时返回固定 IDLE 文本
    valid_name(peer)
    ensure_dirs()
    inbox = os.path.join(INBOX, peer)
    os.makedirs(inbox, exist_ok=True)
    end = time.time() + timeout_sec
    started = now_iso()
    if peer != "controller":  # Leader 多会话并行领取（多活），不能把别的会话手上的单当孤儿
        pending, _released = reclaim_claims(peer)
        if pending:
            path, task = pending
            task["taken_at"] = now_iso()
            task["redelivered"] = int(task.get("redelivered") or 0) + 1
            write_json(path, task)
            heartbeat(peer, "busy", model, task["id"])
            return REDELIVER_NOTE + render_task(task)
    next_relay_check = time.time() + RELAY_CHECK_SEC
    while True:
        heartbeat(peer, "idle", model, "", started, timeout_sec)
        files = sorted(f for f in os.listdir(inbox) if f.endswith(".json"))
        # 用户设置的全局任务顺序（priority.json task_order，任务 id 列表）优先；未被全局序覆盖的任务之间才按该 peer 的 task_prefs 破平；其余按时间
        files = inbox_pick_order(peer, inbox, files)
        for f in files:
            task = read_json(os.path.join(inbox, f), None)
            if not task:
                continue
            if task.get("queue_until_claim"):
                claimed = _external_queue_claim(task["id"], peer)
                if claimed:
                    heartbeat(peer, "busy", model, claimed["id"])
                    return render_task(claimed)
                continue
            if deadline_passed(task):
                os.replace(os.path.join(inbox, f), os.path.join(inbox, f + ".expired"))
                continue
            if task.get("race") and not _race_claim(task["id"], peer):
                # 抢单已被别人领走：本人副本标记后跳过（文件可能已被胜者挪走）
                try:
                    os.replace(os.path.join(inbox, f), os.path.join(inbox, f + RACE_LOST))
                except OSError:
                    pass
                continue
            # 领取即改名，避免同一任务被重复返回；记录 taken_at 供延迟统计与 stalled 判定
            task["taken_at"] = now_iso()
            write_json(os.path.join(inbox, f[:-5] + ".taken"), task)
            os.remove(os.path.join(inbox, f))
            heartbeat(peer, "busy", model, task["id"])
            return render_task(task)
        # 顶替检查每 RELAY_CHECK_SEC 做一次，不再只在超时那一刻做：Leader失联后最迟一个检查周期内就有人接管
        if peer != "controller" and time.time() >= next_relay_check:
            next_relay_check = time.time() + RELAY_CHECK_SEC
            if relay_candidate(peer):
                l = acquire_lease(peer)
                if l.get("holder") == peer:
                    c = peer_state("controller")
                    age = int(heartbeat_age(c["last_seen"])[0]) if c.get("last_seen") else 0
                    text = RELAY_TEXT.format(age=age, peer=peer, epoch=l["epoch"])
                    # RELAY_TEXT 本身不含任何 token，落盘的就是这份无凭据文本
                    note_lead_event("controller", peer, l.get("epoch"), "takeover", text)
                    return text
        if time.time() >= end:
            return IDLE_TEXT + f"\nNEXT_TIMEOUT={suggest_timeout()}"
        time.sleep(POLL_SEC)


LOCK = os.path.join(ROOT, "console", "controller.lock")
PRIORITY = os.path.join(ROOT, "console", "priority.json")
LEASE_TTL_SEC = 300
RELAY_AFTER_SEC = 180


PRIORITY_VERSION = 2
RELAY_POLICIES = ("recent",)  # 已实现的组内选择策略；rotate / allow_busy / relay_chain 只存字段，不走代码分支
RELAY_POLICY_DEFAULT = {"strategy": "recent", "skip_stale_queue": True, "rotate": False, "allow_busy": False, "relay_chain": []}
# 出厂 SOTA 序：本地显式白名单（Arena 模型组 id，按 2026-09-23 控制面 strong 档的 order 抄录），不读 model_groups() 的 tier，行为可预测
RELAY_FACTORY_ORDER = ("gpt-6-astra", "claude-fable-5", "kimi-k3", "claude-opus-5", "grok-4.6", "gemini-4-pro", "gpt-5.6")

# ── Agent 个人设置（507e 第一切片：数据层）──
PROFILES = os.path.join(ROOT, "profiles")
PROFILE_PREFS = ("P0 阻塞", "设计问题", "代码实现", "调研", "评审")
ANSWER_STYLES = ("redteam", "实现", "设计")
_PROFILE_FIELDS = {"display_name", "scope_paths", "strengths", "task_prefs", "paused", "answer_style"}


def _profile_path(peer: str) -> str:
    valid_name(peer)
    return os.path.join(PROFILES, f"{peer}.json")


def default_profile() -> dict:
    return {"display_name": "", "scope_paths": [], "strengths": {"tags": [], "note": ""},
            "task_prefs": [], "paused": False, "answer_style": ""}


def _str_list(v) -> list:
    # 只保留非空字符串项；整体不是 list 就退默认空表。用于 scope_paths / task_prefs / strengths.tags
    if not isinstance(v, list):
        return []
    return [x.strip() for x in v if isinstance(x, str) and x.strip()]


def _norm_profile_field(key: str, v, fallback):
    """读侧逐字段规范化：形状不对就退回该字段默认值，绝不把坏形状原样交给下游。

    这是 9216 审查提出的第一个阻断：get_profile 原样搬运嵌套结构时，
    磁盘上一个 strengths: "x" / {} / null 会让 role_line_for 抛 KeyError/TypeError，
    而 role_line_for 在 dispatch 里对每个 peer 调用一次 —— 一个坏文件会让整次派发对所有 Agent 失败。
    注意范围：只对 profiles/ 这类「描述性配置」宽松。租约、权限、凭据类读取仍然 fail-closed，本函数不碰它们。
    """
    if key == "display_name":
        return v if isinstance(v, str) else fallback
    if key in ("scope_paths", "task_prefs"):
        return _str_list(v)
    if key == "strengths":
        if not isinstance(v, dict):
            return {"tags": [], "note": ""}
        note = v.get("note", "")
        return {"tags": _str_list(v.get("tags", [])), "note": note if isinstance(note, str) else ""}
    if key == "paused":
        return v if isinstance(v, bool) else False
    if key == "answer_style":
        return v if isinstance(v, str) and (v == "" or v in ANSWER_STYLES) else ""
    return fallback


def get_profile(peer: str) -> dict:
    # 读侧宽松：缺文件返回默认对象，peer 名不合法也按默认处理，不抛错；
    # 每个字段都过 _norm_profile_field，旧版本文件 / 手改坏的文件都降级为默认值而不是把坏形状漏给下游。
    try:
        path = _profile_path(peer)
    except ValueError:
        return default_profile()
    try:
        data = read_json(path, None)
    except (OSError, UnicodeError):
        return default_profile()  # Descriptive profiles cannot break dispatch.
    if not isinstance(data, dict):
        return default_profile()
    out = default_profile()
    for k in _PROFILE_FIELDS & data.keys():
        out[k] = _norm_profile_field(k, data[k], out[k])
    return out


def _profile_mutex_path(peer: str) -> str:
    # 专用锁文件，不复用 peers/.<peer>.mutex（那把锁保护心跳的读改写）：
    # profile 与心跳是两份不相干的数据，共用一把锁只会让改名和派发互相阻塞，且引入不必要的锁顺序约束。
    return os.path.join(PROFILES, f".{peer}.mutex")


def set_profile(peer: str, patch: dict) -> dict:
    """写侧严格：字段白名单，未知字段拒绝；task_prefs 去重保序（允许自定义项，PROFILE_PREFS 只是预设不是白名单）。

    锁边界（真实范围，不夸大）：整个「读 → 改 → 写」持 LN.file_mutex —— 那是 3e32 已上线的 OS 级字节锁
    （Windows msvcrt.locking / POSIX fcntl.flock），**跨线程与跨进程都互斥**，进程崩溃由内核释放。
    单靠 write_json 的 os.replace 只保证单次写原子，保证不了读改写这段窗口，不能当跨进程事务用。
    锁文件独立于 controller 租约，也不与 peers 心跳锁嵌套：本函数持锁期间不调用任何会再取锁的路径。
    """
    if not isinstance(patch, dict):
        raise ValueError("profile 更新必须是对象")
    unknown = set(patch) - _PROFILE_FIELDS
    if unknown:
        raise ValueError(f"未知字段：{', '.join(sorted(unknown))}")
    valid_name(peer)                      # 先校验再建目录，别为非法名字造出锁文件
    os.makedirs(PROFILES, exist_ok=True)
    with LN.file_mutex(_profile_mutex_path(peer)):
        return _set_profile_locked(peer, patch)


def _set_profile_locked(peer: str, patch: dict) -> dict:
    cur = get_profile(peer)
    for k, v in patch.items():
        if k == "display_name":
            if not isinstance(v, str):
                raise ValueError("display_name 必须是字符串")
            cur[k] = v
        elif k == "scope_paths":
            if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
                raise ValueError("scope_paths 必须是非空字符串数组")
            cur[k] = [x.strip() for x in v]
        elif k == "strengths":
            if not isinstance(v, dict) or not isinstance(v.get("tags", []), list) \
                    or not all(isinstance(t, str) for t in v.get("tags", [])) or not isinstance(v.get("note", ""), str):
                raise ValueError("strengths 必须是 {tags: 字符串数组, note: 字符串}")
            cur[k] = {"tags": [t.strip() for t in v.get("tags", []) if t.strip()], "note": v.get("note", "")}
        elif k == "task_prefs":
            if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
                raise ValueError("task_prefs 必须是非空字符串数组")
            seen, ordered = set(), []
            for x in v:
                item = x.strip()
                if item not in seen:
                    seen.add(item)
                    ordered.append(item)
            cur[k] = ordered
        elif k == "paused":
            if not isinstance(v, bool):
                raise ValueError("paused 必须是布尔")
            cur[k] = v
        elif k == "answer_style":
            if not isinstance(v, str) or (v and v not in ANSWER_STYLES):
                raise ValueError(f"answer_style 只能是 {', '.join(ANSWER_STYLES)} 或空")
            cur[k] = v
    os.makedirs(PROFILES, exist_ok=True)
    write_json(_profile_path(peer), cur)
    return cur


def profiles_all() -> dict:
    """全量读取，_full_state 一次聚合下发用；目录或文件缺失不报错。

    只收「名字合法 + 确实是普通文件」的条目：
    - `.<peer>.mutex` 锁文件不是 profile；
    - 名字不合 valid_name 的（手工放进来的、别的工具落的）直接跳过，不让它变成一个幽灵 peer；
    - 同名目录 `sub.json/` 会让 open() 抛 PermissionError/IsADirectoryError —— 在这里跳过，
      而不是去放宽全局 read_json 的异常吞咽（租约、权限、凭据类读取必须继续 fail-closed）。
    """
    out = {}
    if not os.path.isdir(PROFILES):
        return out
    for f in sorted(os.listdir(PROFILES)):
        if not f.endswith(".json") or not os.path.isfile(os.path.join(PROFILES, f)):
            continue
        peer = f[:-5]
        try:
            valid_name(peer)
        except ValueError:
            continue
        out[peer] = get_profile(peer)
    return out


def migrate_team_tags(peer: str, teams: list) -> dict:
    # team.tags 兼容读取：profile 未设 strengths.tags 时回填旧 team 定义里的 tags；只读，不改历史文件
    prof = get_profile(peer)
    if prof["strengths"]["tags"]:
        return prof
    for t in teams or []:
        if peer in (t.get("peers") or []) and t.get("tags"):
            prof["strengths"]["tags"] = [str(x) for x in t["tags"]]
            break
    return prof


# 任务类型：mode → 预设 task_prefs 项。只是「推荐 Agent」与「个人偏好破平」用的软信号，不是权限、不做过滤；task 显式带 task_type 时以它为准
MODE_TASK_TYPE = {"build": "代码实现", "review": "评审", "relay": "调研", "independent": "设计问题", "discuss": "设计问题", "redteam": ""}


def task_type_of(task: dict) -> str:
    return str((task or {}).get("task_type") or MODE_TASK_TYPE.get(str((task or {}).get("mode") or ""), "") or "")


def inbox_pick_order(peer: str, inbox: str, files: list) -> list:
    """507e 约定「全局序优先，同级按个人偏好破平；个人偏好不推翻全局序」的真实消费层——wait_task 的收件顺序。
    全局序 = priority.json 的 task_order（任务 id 列表，用户设置，排的是「任务先后」）；个人偏好 = profile.task_prefs（任务类型序）。
    键：(是否不在全局序, 全局位次, 个人类型位次, 文件名=时间)。在全局序里的任务永远排在不在的前面；只有都不在全局序的任务之间，才按 task_prefs 破平，再按时间。
    与 bus_projects.suggest_peers（给一道题推荐 Agent）是两件事：这里排任务，那里排人；task_prefs 在两边都只是软信号。"""
    order = {t: i for i, t in enumerate(priority()["task_order"])}
    prefs = get_profile(peer)["task_prefs"]

    def key(f):
        tid = f[:-5]
        if tid in order:
            return (0, order[tid], 0, f)
        rank = 0
        if prefs:
            tt = task_type_of(read_json(os.path.join(inbox, f), None))
            rank = prefs.index(tt) if tt in prefs else len(prefs)
        return (1, 0, rank, f)
    return sorted(files, key=key)


_ROLE_WS = re.compile(r"[\r\n\t\v\f\u2028\u2029]+")


def role_line_for(peer: str) -> str:
    """任务包头部注入的严格一行：先规范化后截断（200 字符）。

    严格单行有两层保证：get_profile 已经把形状洗干净（不会抛错），这里再把任何残留的
    换行 / 制表 / U+2028 折成空格 —— 否则 scope_paths 里一个换行就能在任务包里凭空多出几行，
    把后面的「## 你的切入角度」挤走或伪造出一段不存在的指令。
    """
    prof = get_profile(peer)
    parts = []
    if prof["scope_paths"]:
        parts.append("负责 " + "、".join(prof["scope_paths"][:4]))
    if prof["strengths"]["tags"]:
        parts.append("擅长 " + "、".join(prof["strengths"]["tags"][:6]))
    if prof["answer_style"]:
        parts.append(f"作答风格 {prof['answer_style']}")
    if not parts:
        return ""
    line = _ROLE_WS.sub(" ", "你的角色：" + "，".join(parts)).strip()
    return line[:200]


def priority() -> dict:
    # schema v2 五键：version / relay_order / relay_order_meta / rev / relay_policy。只补缺失键，不改写已有值；
    # relay_order_meta 为 None 表示这份序列从未在界面编辑过（出厂预置提示据此判定）。task_order 仍有消费方，agent_weight 原样保留
    p = read_json(PRIORITY, None) or {}
    p.setdefault("version", PRIORITY_VERSION)
    p.setdefault("relay_order", [])
    p.setdefault("relay_order_meta", None)
    p.setdefault("rev", 0)
    pol = p.get("relay_policy") if isinstance(p.get("relay_policy"), dict) else {}
    p["relay_policy"] = {**RELAY_POLICY_DEFAULT, **pol}
    p.setdefault("agent_weight", {})
    p.setdefault("task_order", [])
    return p


def lease_left(l) -> float:
    """租约剩余秒数；<= 0 表示无效（无租约 / 已过期 / 时间戳不可信）。

    合法租约的 expires 只会被设成「当时 now + LEASE_TTL_SEC」（发放与续约都是），所以剩余时间不可能超过
    LEASE_TTL_SEC。超出 TTL + CLOCK_SKEW_SEC 的只能是时钟 / 时区错乱时写下的（ADR 0010），按过期处理——
    否则要等墙钟追上那个时刻才放行，热备一直接不了班（sota2 审查 7078 实测 +12h 复现）。
    只看租约本身，不看心跳：有效租约不会因为持有者心跳被标 skewed 就被抢走。"""
    if not l:
        return 0.0
    exp = parse_ts(l.get("expires"))
    if exp is None:
        # 解析失败 = 按过期。这里和 heartbeat_age 方向一致，但代价相反：可能被热备抢走租约、
        # 短时间出现双 Leader（抢锁是 O_EXCL 原子的，最终只有一个）。相比之下「按在线」会让
        # 一把坏租约永远锁死、热备永远接不了班，没有自愈路径，所以仍然选按过期。
        return 0.0
    left = (exp - now_dt()).total_seconds()
    if left > LEASE_TTL_SEC + CLOCK_SKEW_SEC:
        return 0.0
    return left


def _lease_mutex():
    """租约的跨进程互斥：检查-续期-发放-强释放全在这把锁内，消除旧实现的竞态窗口。
    锁文件是常驻文件（.mutex 后缀），任何代码不得删除它（见 bus_locks 模块头约定）。"""
    from bus_locks import file_mutex
    return file_mutex(LOCK + ".mutex")


def _read_lease_file() -> tuple:
    """(lease_dict, corrupt)。文件不存在 → ({}, False)；存在但解析失败 → ({}, True)。
    租约是权限类数据：损坏必须可被识别，不能与「没有租约」混为一谈。"""
    try:
        with open(LOCK, encoding="utf-8") as f:
            data = json.load(f)
        return (data if isinstance(data, dict) else {}), False
    except FileNotFoundError:
        return {}, False
    except (json.JSONDecodeError, OSError):
        return {}, True


def lease() -> dict:
    # 返回当前有效租约；过期、时间戳不可信、文件损坏视为无（发放路径会把损坏文件隔离留证）
    l, corrupt = _read_lease_file()
    if corrupt or not l:
        return {}
    return l if lease_left(l) > 0 else {}


def _lease_expires() -> str:
    # 租约到期串必须带偏移（ADR 0010）：naive strftime 在跨时区进程间无法正确比较
    return iso_after(LEASE_TTL_SEC)


def acquire_lease(holder: str) -> dict:
    # Leader 与代理 Leader 抢同一把锁：全程持租约互斥，过期锁先清再放，持有者本人直接续约。
    # 旧实现用 O_EXCL 创建后再写内容，中间的空文件窗口会让并发方读到空 JSON 后删锁重建，
    # POSIX 上可造成双 holder；Windows 上则退化成 holder="?" 的混乱态。
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    with _lease_mutex():
        cur = lease()
        if cur and cur.get("holder") == holder:
            return _renew_lease_locked(holder)
        if cur:
            return cur
        _, corrupt = _read_lease_file()
        if corrupt:
            # 损坏的租约不等于无租约：移走留证再发放，不当作空白直接覆盖
            try:
                os.replace(LOCK, LOCK + ".corrupt-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
            except OSError:
                return lease() or {"holder": "?", "epoch": 0}
        elif os.path.exists(LOCK):
            try:
                os.remove(LOCK)
            except OSError:
                return lease() or {"holder": "?", "epoch": 0}
        old = read_json(LOCK + ".last", {}) or {}
        l = {"holder": holder, "epoch": int(old.get("epoch", 0)) + 1, "since": now_iso(), "expires": _lease_expires()}
        write_json(LOCK, l)
        write_json(LOCK + ".last", l)
        return l


def _renew_lease_locked(holder: str) -> dict:
    # 调用方必须已持有 _lease_mutex
    l = lease()
    if not l or l.get("holder") != holder:
        return l
    l["expires"] = _lease_expires()
    write_json(LOCK, l)
    return l


def renew_lease(holder: str) -> dict:
    with _lease_mutex():
        return _renew_lease_locked(holder)


def release_lease(holder: str) -> bool:
    with _lease_mutex():
        l = lease()
        if l and l.get("holder") == holder:
            try:
                os.remove(LOCK)
            except OSError:
                pass
            return True
        return False


def force_release_lease(expected_holder: str, expected_epoch) -> bool:
    """守护强释放：互斥锁内复查磁盘上的租约仍是同一份（holder 与 epoch 都一致）才删。
    旧实现读到旧租约后直接 os.remove，读与删之间新持有者可能已抢到租约，会被误删。"""
    with _lease_mutex():
        raw, corrupt = _read_lease_file()
        if corrupt or not raw:
            return False
        if raw.get("holder") != expected_holder or raw.get("epoch") != expected_epoch:
            return False
        try:
            os.remove(LOCK)
        except OSError:
            return False
        return True


_GROUPS_CACHE = {"at": 0.0, "groups": []}
_GROUPS_RETRY_SEC = 15  # 拉取失败时的重试间隔；失败不得按「空结果」缓存 60 秒，否则 relay 分组静默退化


def model_groups() -> list:
    # 2026-10-02：分组名单由工作台自己管（bus_agent_groups → console/agent_groups.json），不再每次问 Arena。
    # 文件不存在时那边会从 Arena 抄一次；之后 Arena 在不在（以后做成插件）都不影响分组。读本地文件，便宜，每次都刷新缓存
    try:
        import bus_agent_groups as AG
        _GROUPS_CACHE["groups"] = [{"id": g["id"], "match": g.get("match") or [], "order": g.get("order") or 999} for g in AG.load()]
        _GROUPS_CACHE["at"] = time.time()
    except Exception:  # noqa: BLE001  分组文件坏了：沿用上次结果
        pass
    return _GROUPS_CACHE["groups"]


def _group_of_model_in(groups: list, model: str) -> str:
    m = str(model or "").lower().split(" · ")[0].split("/")[-1].replace("_", "-").replace(" ", "-")
    for g in sorted(groups, key=lambda g: -max([len(x) for x in g["match"]] + [0])):
        if any(m == x or m.startswith(x + "-") or m.startswith(x + ".") for x in g["match"]):
            return g["id"]
    return ""


def group_of_model(model: str) -> str:
    return _group_of_model_in(model_groups(), model)


def group_of_model_cached(model: str) -> str:
    """只读本地缓存的 group_of_model：读取路径（peer_state/list_peers）专用，绝不触发 Arena 请求
    （7755 审查 P2-3）。缓存为空时返回空串——读取不阻塞在网络拉取上，组表由写入路径或派发路径补拉。"""
    return _group_of_model_in(list(_GROUPS_CACHE["groups"]), model)


# ---- group_hint：未知模型「先接入、后归类」后端一期（20261001-090838-9e1e）----
# peer 文件里的 "group_hint" 字段（读写走 peer 锁 + 原子写，未知字段保留，不触碰心跳/模型/状态字段）：
#   {"group": "glm", "source": "normalize|probe|manual", "confidence": 0.9,
#    "state": "auto|confirmed|rejected", "at": "2026-10-01T12:00:00", "rejected": ["a", "b"]}
# 归类不阻断接入：hint 缺失或未生效时 peer_group 回退到 group_of_model，再回退到空串（未分类仍可派发）。

GROUP_HINT_SUFFIXES = ("flash", "preview", "latest", "thinking")  # 逐层去尾缀的候选尾缀
GROUP_HINT_AUTO_CONF = 0.9        # 前缀命中（确定性归类）写 auto hint 的置信度
GROUP_HINT_FAMILY_CONF = 0.4      # 仅家族词命中：只提示不落盘
GROUP_HINT_EFFECTIVE_CONF = 0.8   # auto hint 生效下限（normalize/probe 落盘值都 ≥ 此值）
HINT_PUBLIC_KEYS = ("group", "source", "confidence", "state", "at", "rejected")  # 对外暴露的净化白名单


def _normalize_model_key(model) -> str:
    """模型名 → 归一化键：小写、取「 · 」前段与路径最后一段（去厂商前缀）、空格/下划线转连字符、版本号 v 前缀去掉（v4.1→4.1）。"""
    m = str(model or "").strip().lower()
    m = m.split(" · ")[0].split("/")[-1]
    m = m.replace("_", "-").replace(" ", "-")
    m = re.sub(r"(?<![a-z0-9])v(?=[0-9])", "", m)   # 只去独立成词的 v 前缀；不动 glm-4v 这类
    return m


def _model_key_layers(model) -> list:
    """归一化键 + 逐层去尾缀（flash/preview/latest/thinking）得到候选键列表，由具体到宽泛。
    例：glm-5.3-flash → ["glm-5.3-flash", "glm-5.3", "glm"]。逐层剥而非一次剥光，
    每层都做边界匹配，避免 deepseek-flash 这类被剥到错误粒度或撞上子串。"""
    key = _normalize_model_key(model)
    if not key:
        return []
    layers = [key]
    cur = key
    while True:
        nxt = cur
        for s in sorted(GROUP_HINT_SUFFIXES, key=len, reverse=True):
            if nxt.endswith("-" + s):
                nxt = nxt[: -(len(s) + 1)]
                break
        if not nxt or nxt == cur:
            break
        layers.append(nxt)
        cur = nxt
    return layers


def _match_group_layers(layers: list) -> str:
    """逐层最长前缀匹配：层越深（越具体）越优先，同层取更长的 match 词；未命中返回空串。
    边界规则与 group_of_model 同源（相等 / 后随 - / 后随 .），防止子串误命中。"""
    best, best_score = "", (-1, -1)
    for g in model_groups():
        for x in (g.get("match") or []):
            x = str(x)
            if not x:
                continue
            for layer in layers:
                if layer == x or layer.startswith(x + "-") or layer.startswith(x + "."):
                    score = (len(layer), len(x))
                    if score > best_score:
                        best, best_score = str(g.get("id") or ""), score
    return best


def _family_group(key: str) -> str:
    """家族词兜底：键以某组 match 词开头（qwen3-max → qwen），或任一连字符分段等于/以 match 词开头
    （ark-deepseek → deepseek，7755 审查 P2-5）时返回该组 id。只作 0.4 置信的建议，不落盘、不生效。"""
    if not key:
        return ""
    segs = [s for s in key.split("-") if s]
    for g in sorted(model_groups(), key=lambda g: -max([len(str(x)) for x in (g.get("match") or [])] + [0])):
        for x in (g.get("match") or []):
            x = str(x)
            if x and key != x and (key.startswith(x) or any(seg == x or seg.startswith(x) for seg in segs)):
                return str(g.get("id") or "")
    return ""


def _hint_mutex(peer: str):
    from bus_lead_notify import peer_mutex_path
    from bus_locks import file_mutex
    return file_mutex(peer_mutex_path(PEERS, peer))


def _mutate_group_hint(peer: str, fn) -> dict:
    """持 peer 锁读改写 group_hint（与心跳、改名同一把锁），只动 group_hint 子键，其余字段原样保留。

    归类是给「已接入过的 peer」打标签：peer 文件必须已存在，否则抛 ValueError（bus_web 回 400）。
    不得为不存在的 peer 凭空建档——68e6 审查 ①：残缺 peers/<name>.json 会让 relay_resolution KeyError。"""
    ensure_dirs()
    valid_name(peer)
    path = os.path.join(PEERS, f"{peer}.json")
    if not os.path.isfile(path):   # 锁外只做存在性预检（不 open，免得撞上心跳的原子替换）；没接入过的不进锁，不在 peers/ 下留 .mutex
        raise ValueError(f"peer {peer} 尚未接入（没有 peers/{peer}.json），不能只靠归类建档")
    with _hint_mutex(peer):
        data = read_json(path, None)
        if not isinstance(data, dict):
            raise ValueError(f"peer {peer} 尚未接入（没有 peers/{peer}.json），不能只靠归类建档")
        hint = data.get("group_hint") if isinstance(data.get("group_hint"), dict) else {}
        data["group_hint"] = fn(hint)
        write_json(path, data)
    return dict(data["group_hint"])


def get_group_hint(peer: str) -> dict:
    data = read_json(os.path.join(PEERS, f"{peer}.json"), None)
    hint = (data or {}).get("group_hint")
    return dict(hint) if isinstance(hint, dict) else {}


def set_group_hint(peer: str, patch: dict) -> dict:
    """合并写 group_hint：patch 覆盖同名字段，其余（含未知字段）保留。"""
    if not isinstance(patch, dict) or not patch:
        raise ValueError("group_hint patch 必须是非空 dict")
    return _mutate_group_hint(peer, lambda hint: {**hint, **patch})


def _hint_effective_group(data: dict) -> str:
    """从已读的 peer 数据里取「生效中」的 hint 组：confirmed，或 conf≥0.8 且 state≠rejected，且组不在 rejected 名单。
    normalize/probe 的自动 hint 绑定模型指纹（model_key）：模型变了即失效（7755 审查 P2-4）；
    指纹缺失视为一期旧数据按原样兼容，manual confirmed 不做指纹校验。"""
    hint = data.get("group_hint") if isinstance(data.get("group_hint"), dict) else {}
    g = str(hint.get("group") or "")
    rejected = hint.get("rejected") if isinstance(hint.get("rejected"), list) else []
    if not g or g in rejected:
        return ""
    src = str(hint.get("source") or "")
    if src in ("normalize", "probe"):
        mk = str(hint.get("model_key") or "")
        if mk and mk != _normalize_model_key(data.get("model", "")):
            return ""
    conf = hint.get("confidence")
    conf = conf if isinstance(conf, (int, float)) and not isinstance(conf, bool) else 0.0
    if str(hint.get("state") or "") == "confirmed" or (conf >= GROUP_HINT_EFFECTIVE_CONF and hint.get("state") != "rejected"):
        return g
    return ""


def peer_group(peer: str) -> str:
    """四级优先级：显式 group > 生效中的 group_hint（manual confirmed 可 conf=1）> group_of_model > 空串。"""
    data = read_json(os.path.join(PEERS, f"{peer}.json"), None) or {}
    return str(data.get("group") or "") or _hint_effective_group(data) or group_of_model(data.get("model", ""))


def suggest_group(peer: str) -> dict:
    """按模型名做确定性归类：前缀命中 → conf 0.9 的 auto hint；仅家族词命中 → conf 0.4 只提示不落盘。
    候选资格检查（rejected / 人工确认在效）与落盘在同一次 peer 锁内完成（7755 审查 P1-2），
    人工确认（manual confirmed）在效时不落自动 hint。返回 {candidate, confidence, source, reason}。"""
    data = read_json(os.path.join(PEERS, f"{peer}.json"), None) or {}
    key = _normalize_model_key(data.get("model", ""))
    cand = _match_group_layers(_model_key_layers(key))
    if cand:
        sug = {"candidate": cand, "confidence": GROUP_HINT_AUTO_CONF, "source": "normalize",
               "reason": f"模型键逐层最长前缀命中（{key}）"}
    else:
        fam = _family_group(key)
        if fam:
            sug = {"candidate": fam, "confidence": GROUP_HINT_FAMILY_CONF, "source": "normalize",
                   "reason": f"仅家族词命中（{key}），置信不足不落盘"}
        else:
            return {"candidate": "", "confidence": 0.0, "source": "normalize",
                    "reason": f"无法归类（{key or '空模型名'}）"}
    verdict = {"applied": False}

    def fn(hint):
        hint = dict(hint)
        if str(hint.get("state") or "") == "confirmed" and str(hint.get("source") or "") == "manual":
            sug["reason"] += "；人工确认在效，未落自动建议"
            return hint
        if sug["candidate"] in (hint.get("rejected") or []):
            sug.update({"candidate": "", "confidence": 0.0})
            sug["reason"] = f"候选已被拒绝，不再自动归类（{sug['source']}）"
            return hint
        if sug["confidence"] >= GROUP_HINT_EFFECTIVE_CONF:
            hint.update({"group": sug["candidate"], "source": "normalize", "confidence": sug["confidence"],
                         "state": "auto", "at": now_iso(), "model_key": key})
            verdict["applied"] = True
            sug["reason"] += "；已落盘 auto hint"
        return hint

    _mutate_group_hint(peer, fn)
    return sug


def _require_known_group(group) -> str:
    g = str(group or "").strip()
    if g not in {str(x.get("id") or "") for x in model_groups()}:
        raise ValueError(f"未知模型组：{g!r}（组 id 必须来自 model_groups；未知组只能出现在建议里）")
    return g


def confirm_group(peer: str, group: str) -> dict:
    """人工确认归类：state=confirmed、conf=1.0（生效优先级最高）；候选曾被拒绝的，人工确认即解禁。"""
    g = _require_known_group(group)

    def fn(hint):
        hint = dict(hint)
        hint.update({"group": g, "source": "manual", "confidence": 1.0, "state": "confirmed", "at": now_iso(),
                     "rejected": [x for x in (hint.get("rejected") or []) if x != g]})
        return hint

    return _mutate_group_hint(peer, fn)


def manual_group(peer: str, group: str) -> dict:
    """人工指定归类（与 confirm 同效：manual + confirmed + conf 1.0，可覆盖 rejected）。"""
    return confirm_group(peer, group)


def reject_group(peer: str, group: str) -> dict:
    """拒绝候选：追加 rejected（去重）；当前 hint 就是该组时置 state=rejected 使其失效。
    此后 normalize/probe 不得再自动给该候选，人工确认可解禁。"""
    g = _require_known_group(group)

    def fn(hint):
        hint = dict(hint)
        rej = list(hint.get("rejected") or [])
        if g not in rej:
            rej.append(g)
        hint["rejected"] = rej
        if str(hint.get("group") or "") == g:
            hint["state"] = "rejected"   # 只在拒的是当前 hint 组时降 state，别误伤其它组的有效 hint
        hint["at"] = now_iso()
        return hint

    return _mutate_group_hint(peer, fn)


def probe_group(peer: str, session_id: str = "", adapter=None) -> dict:
    """会话探针归类：adapter(peer, session_id) → 组 id 字符串，最多尝试 2 次。
    失败只记异常类型名（不带消息与堆栈，防泄密），簿记写在 hint.probe（对外净化时剥掉）。
    候选资格检查（rejected / 人工确认在效）与落盘在同一次 peer 锁内完成（7755 审查 P1-2）；
    人工确认（manual confirmed）在效时 probe 不覆盖。落盘的 auto hint 绑定当前模型指纹。
    adapter 为空 = Arena 真实探针本期未接线（明确不做），如实返回未接线状态。"""
    if adapter is None:
        err = "Arena 真实探针未接线（本期仅支持注入 adapter）"
        _mutate_group_hint(peer, lambda hint: {**hint, "probe": {"attempts": 1, "error": err, "at": now_iso()}})
        return {"ok": False, "candidate": "", "attempts": 1, "error": err}
    last_err, attempts = "", 0
    for attempt in (1, 2):
        attempts = attempt
        try:
            cand = str(adapter(peer, session_id) or "").strip()
            if not cand:
                last_err = "probe 无结果"
                continue
            if cand not in {str(x.get("id") or "") for x in model_groups()}:
                last_err = "probe 返回未知组"
                continue
            cur_key = _normalize_model_key((read_json(os.path.join(PEERS, f"{peer}.json"), None) or {}).get("model", ""))
            verdict = {"applied": False, "confirmed": False, "rejected": False}

            def fn(hint, cand=cand, cur_key=cur_key):
                hint = dict(hint)
                if str(hint.get("state") or "") == "confirmed" and str(hint.get("source") or "") == "manual":
                    verdict["confirmed"] = True
                    return hint
                if cand in (hint.get("rejected") or []):
                    verdict["rejected"] = True
                    return hint
                hint.update({"group": cand, "source": "probe", "confidence": GROUP_HINT_AUTO_CONF,
                             "state": "auto", "at": now_iso(), "model_key": cur_key})
                verdict["applied"] = True
                return hint

            _mutate_group_hint(peer, fn)
            if verdict["applied"]:
                return {"ok": True, "candidate": cand, "attempts": attempt, "error": ""}
            if verdict["rejected"]:
                return {"ok": False, "candidate": "", "attempts": attempt, "error": f"候选 {cand} 已被拒绝"}
            if verdict["confirmed"]:
                return {"ok": False, "candidate": cand, "attempts": attempt, "error": "人工确认在效，probe 不覆盖"}
            last_err = "probe 未落盘"
        except Exception as e:   # 不泄密：只记异常类型名
            last_err = type(e).__name__
    err_out = last_err or "probe 失败"
    _mutate_group_hint(peer, lambda hint: {**hint, "probe": {"attempts": attempts, "error": err_out, "at": now_iso()}})
    return {"ok": False, "candidate": "", "attempts": attempts, "error": err_out}


def inbox_counts(peer: str) -> dict:
    """inbox 三分计数：待领 .json / 已领未回 .taken / 已过期 .json.expired。

    合并计数是 fb-8a7c79854b 的根因：`.taken` 是「已被某个会话领走、但会话结束前没回复」的孤儿，
    不会再被任何 Agent 领走，却被当成积压显示，于是界面出现「idle 却排队 7」这种自相矛盾。
    展示层必须分开；判定「离线但活没干完」时仍要看总量（见 inbox_queued）。
    注意 .json.expired 只命中 .expired，不会被算进 queued。"""
    d = os.path.join(INBOX, peer)
    try:
        files = os.listdir(d)
    except OSError:
        return {"queued": 0, "claimed": 0, "expired": 0}
    return {"queued": sum(1 for f in files if f.endswith(".json")),
            "claimed": sum(1 for f in files if f.endswith(".taken")),
            "expired": sum(1 for f in files if f.endswith(".expired"))}


def inbox_queued(peer: str) -> int:
    # 历史口径 = 待领 + 已领未回。relay 的 skip_stale_queue 与「离线·有排队」都要用总量：
    # 对一个离线 peer 来说，.taken 同样意味着「这份活没干完」，拆字段不能把这个信号弄丢。
    c = inbox_counts(peer)
    return c["queued"] + c["claimed"]


def relay_resolution(exclude=None) -> dict:
    # 按 relay_order 逐项解析：项等于某个 peer 名 → 只看它；否则视为模型组 id → 组内可用者按 (last_seen 新→旧, peer 名) 全序取第一个（recent）。
    # 可用 = 在线、不在 exclude、且（skip_stale_queue 时）不是「离线但 inbox 有积压」。
    # relay_order 为空数组 = 用户明确拒绝替补，不兜底；非空但逐项都解析不到 → 保留旧的无条件兜底（全部可用者按名字），fallback=True。
    pr = priority()
    pol = pr["relay_policy"]
    ex = set(exclude or [])
    skip_stale = bool(pol.get("skip_stale_queue", True))
    requested = str(pol.get("strategy") or "recent")
    peers = [p for p in list_peers() if p["peer"] != "controller"]
    for p in peers:
        # 9e1e：改调 peer_group，group_hint（confirmed / auto conf≥0.8）自然生效，不复制判定逻辑
        p["_group"] = peer_group(p["peer"])
        p["_stale_queue"] = (not p.get("online")) and inbox_queued(p["peer"]) > 0

    def usable(p):
        return bool(p.get("online")) and p["peer"] not in ex and not (skip_stale and p["_stale_queue"])

    items, pick, via = [], "", ""
    for raw in pr["relay_order"]:
        it = str(raw)
        direct = [p for p in peers if p["peer"] == it]
        members = direct or [p for p in peers if p["_group"] == it]
        cands = sorted((p for p in members if usable(p)), key=lambda c: c["peer"])
        cands.sort(key=lambda c: c.get("last_seen") or "", reverse=True)
        chosen = cands[0]["peer"] if cands else ""
        items.append({"item": it, "kind": "peer" if direct else ("group" if members else "unknown"), "pick": chosen,
                      "online_n": len(cands), "total_n": len(members), "skipped_n": sum(1 for p in members if p["_stale_queue"])})
        if chosen and not pick:
            pick, via = chosen, ("" if direct else it)
    fallback_peers = []
    if pr["relay_order"] and not pick:
        fallback_peers = sorted(p["peer"] for p in peers if usable(p))
        pick = fallback_peers[0] if fallback_peers else ""
    return {"items": items, "pick": pick, "via_group": via, "fallback": bool(fallback_peers), "fallback_peers": fallback_peers,
            "refused": not pr["relay_order"], "policy": "recent", "policy_requested": requested,
            "policy_note": "" if requested in RELAY_POLICIES else f"策略 {requested} 未实现，按 recent 处理", "skip_stale_queue": skip_stale}


def relay_pick(exclude=None) -> tuple:
    # 供后端与界面调用：(peer, via_group)；解析不到时 ("", "")
    r = relay_resolution(exclude)
    return r["pick"], r["via_group"]


def relay_order_peers() -> list:
    # 兼容旧调用：解析后的有效顺序（逐项 pick 去重，再接兜底名单），首位就是候选
    r = relay_resolution()
    out = []
    for p in [it["pick"] for it in r["items"]] + r["fallback_peers"]:
        if p and p not in out:
            out.append(p)
    return out


def relay_candidate(peer: str) -> bool:
    # 旧签名保留：Leader心跳超过 RELAY_AFTER_SEC，且解析结果就是我自己
    c = peer_state("controller")
    age = heartbeat_age(c["last_seen"])[0] if c.get("last_seen") else 1e9
    if age < RELAY_AFTER_SEC:
        return False
    return relay_pick()[0] == peer


def relay_meta_for(order: list, old=None, source: str = "ui", kind: str = "") -> list:
    # relay_order_meta 与 relay_order 平行：旧条目沿用（保留 added_at），新条目按「是否模型组 id」判 kind
    prev = {m.get("id"): m for m in (old or []) if isinstance(m, dict)}
    gids = {g["id"] for g in model_groups()}
    return [prev.get(i) or {"id": i, "kind": kind or ("group" if i in gids else "peer"), "source": source, "added_at": now_iso()} for i in order]


ROLES_TEMPLATE = os.path.join(ROOT, "tools", "roles.json")


def read_roles(path: str = "") -> dict:
    """角色配置：优先本机实例 console/roles.json（不入库）；缺失时回退到仓库模板 tools/roles.json。
    否则新装机器会表现为"没有在线的 Reviewer"，而不是"配置缺失"。"""
    inst = path or os.path.join(ROOT, "console", "roles.json")
    if os.path.exists(inst):            # 存在就以它为准；损坏必须报错，不能静默换成模板（审查 95fe：fail-closed）
        data = read_json(inst, None)
        if data is None:
            raise ValueError(f"roles.json 解析失败，未回退模板，请修复：{inst}")
        return data if isinstance(data, dict) else {}
    data = read_json(ROLES_TEMPLATE, None)
    return data if isinstance(data, dict) else {}


def roles_source(path: str = "") -> tuple:
    """角色配置实际来自哪里：("instance", console/roles.json) / ("template", tools/roles.json) / ("missing", 期望路径)。
    调用方据此把「配置缺失」和「人不在线」分开报，不要让新装机器只看到「无人可审」。"""
    inst = path or os.path.join(ROOT, "console", "roles.json")
    if os.path.exists(inst):
        return ("instance", inst)
    if os.path.exists(ROLES_TEMPLATE):
        return ("template", ROLES_TEMPLATE)
    return ("missing", inst)


def reviewer_config_error(path: str = "") -> str:
    """派审前的配置自检：配置缺失 / 没配 reviewer_groups 时返回一句可直接抛的说明，配置正常返回空串。

    没有它，新装机器上 pick_reviewers 拿到空 groups 返回空列表，现场只会看到「没有在线的Reviewer」，
    把「配置缺失」误导成「人不在线」，极难排查（审查 6683 分歧点 2）。

    两处文案按审查 fe87 的 M1 / M2 修正：
      * M1 missing 分支的前提**就是模板也不存在**，不能再让用户「把 tools/roles.json 复制过去」——
        那是一个按定义不存在的文件，照做只会得到「找不到文件」，比原来的「无人在线」更迷惑。
      * M2 template 分支要指向**实例** console/roles.json：角色配置是实例层、不入库（ADR 0005、WORKFLOW §6），
        教用户去改仓库模板会把个人配置带进 git，或者被下一次 git checkout 覆盖丢掉。
    """
    src, p = roles_source(path)
    inst = path or os.path.join(ROOT, "console", "roles.json")
    if src == "missing":
        return (f"配置缺失：既没有本机实例 {p}，也没有仓库模板 {ROLES_TEMPLATE}。"
                f"请从仓库恢复 tools/roles.json，或直接新建 {inst} 并填 assignments.reviewer_groups"
                "——这不是「无人在线」。")
    groups = (read_roles(path).get("assignments") or {}).get("reviewer_groups") or []
    if not groups:
        if src == "template":
            return (f"配置缺失：当前用的是仓库模板 {p}，其 assignments.reviewer_groups 为空。"
                    f"请在本机实例 {inst} 里补 assignments.reviewer_groups（不要改仓库模板 {ROLES_TEMPLATE}，"
                    "它入 git、会被 checkout 覆盖）——这不是「无人在线」。")
        return (f"配置缺失：{p} 的 assignments.reviewer_groups 为空，没有任何模型组被指定为Reviewer。"
                "请补齐该字段再派审——这不是「无人在线」。")
    return ""


def pick_reviewers(exclude: list = None, n: int = 1, avoid_group: str = "") -> list:
    # Reviewer：roles.json assignments.reviewer_groups 里的模型组中在线的Agent，排除施工者本人（按 peer 名）。
    # avoid_group=施工者的模型组：policy.review_prefer_other_group（默认开）时先挑别的组，没有才回退同组——
    # 同一模型的两个窗口上下文独立，但盲点相同；硬禁会在只有一个组在线时无人可审，所以是软规则（docs/WORKFLOW.md 规则 4）
    roles = read_roles()
    groups = (roles.get("assignments") or {}).get("reviewer_groups") or []
    prefer_other = bool(avoid_group) and (roles.get("policy") or {}).get("review_prefer_other_group", True)
    ex = set(exclude or [])
    cands = [p for p in list_peers() if p.get("online") and p["peer"] != "controller" and p["peer"] not in ex and peer_group(p["peer"]) in groups and not benched(p["peer"])]
    if not cands and (roles.get("policy") or {}).get("review_fallback_any", True):
        # Reviewer 组一个在线的都没有时（2026-10-02 实测：在线的只有 glm / qwen / sonnet，「催审」一单都派不出去），
        # 退到「正在等任务、不是作者」的任何人。审查包里会标明是临时 Reviewer（bus_build.dispatch_review）
        cands = [p for p in list_peers() if p.get("listening") and p["peer"] != "controller" and p["peer"] not in ex and not benched(p["peer"])]
    # 收件箱里待领 + 已领未回的单数也参与排序：一次派很多审查（全部催审）时，Agent 要等领了下一单才不算 idle，
    # 只按 idle 排会把所有审查都派给名字最靠前的那一位
    load = lambda p: (lambda c: c["queued"] + c["claimed"])(inbox_counts(p["peer"]))
    cands.sort(key=lambda p: (prefer_other and peer_group(p["peer"]) == avoid_group, load(p), activity_of(p) != "idle", p["peer"]))
    return [p["peer"] for p in cands[:n]]


def substitute_offline(peers: list, exclude=None) -> dict:
    # 派发时离线的 peer 换成同组在线 peer；返回 {原名: 替补名}
    # exclude：不能顶上来的人。审查单传被审提交的作者——作者和点名的 Reviewer 常在同一组，
    # 不排除就会「Reviewer 离线 → 作者顶上 → 自己审自己」（d5de 事故：读完包才被 set_review 拒收，白跑一轮）
    ex = set(exclude or ())
    online = {p["peer"] for p in list_peers() if p.get("online") and p["peer"] != "controller" and p["peer"] not in ex}
    peers = list(dict.fromkeys(peers))  # 去重：同名出现两次会让替补撞名、判定多算
    used = set(p for p in peers if p in online)
    swaps = {}
    for peer in peers:
        if peer in online:
            continue
        g = peer_group(peer)
        if not g:
            continue
        for cand in sorted(online):
            if cand not in used and peer_group(cand) == g:
                swaps[peer] = cand
                used.add(cand)
                break
    return swaps


RELAY_TEXT = """RELAY_TAKEOVER: Leader已离线超过 {age} 秒，你（{peer}）现在是代理 Leader（epoch {epoch}）。

循环：调用 bus_console(action="wait", peer="{peer}")，按返回的用户指令处理：dispatch → bus_dispatch；ANSWERS_COMPLETE → bus_collect、写合并稿、bus_console(action="post", peer="{peer}", status="ready_for_merge")；每条指令处理后 bus_console(action="ack", peer="{peer}")。
禁止：adopt / reject（用户动作，留在队列不要 ack）、archive、修改 projects 配置与 priority。你写的合并稿会标 relay_draft，真Leader回来复核。
当 wait 返回 RELAY_HANDBACK 时，停止Leader循环，回到 bus_wait_task。"""


def note_lead_event(frm: str, to: str, epoch, reason: str, prompt: str = ""):
    # 交接通知是旁路：任何失败都不能打断租约流转或 wait 循环；失败留一行不含提示词/凭据的诊断
    try:
        if not LN.features(ROOT).get("lead_events", True):
            return None
        return LN.record_lead_event(ROOT, frm, to, epoch, reason, prompt)
    except Exception as exc:
        try:
            with open(os.path.join(ROOT, "console", "lead_notify.err.log"), "a", encoding="utf-8") as f:
                f.write(f"{now_iso()} note_lead_event reason={reason} epoch={epoch} from={frm} to={to} err={type(exc).__name__}: {str(exc)[:200]}\n")
        except Exception:
            pass
        return None


def lead_events_open() -> list:
    try:
        if not LN.features(ROOT).get("lead_events", True):
            return []
        return LN.lead_events_open(ROOT)
    except Exception:
        return []


def suggest_timeout() -> int:
    # 普通协作任务沿用活跃期短等待；插件任务保持其它 Agent 的空闲等待周期。
    # 字符串比较会在时区变化后错几小时（ADR 0010）：一律解析后比
    for f in os.listdir(OUTBOX):
        if f.endswith(".meta.json"):
            m = read_json(os.path.join(OUTBOX, f), {})
            if m.get("external_owner"):
                continue
            a = age_sec(m.get("created"))
            if a is not None and a <= 600:
                return 30
    return 300


def reply(task_id: str, peer: str, answer: str) -> str:
    valid_name(peer)
    ensure_dirs()
    src = os.path.join(INBOX, peer, f"{task_id}.taken")
    task = read_json(src, None)
    if not task:
        # reclaim_claims 释放的领取仍收迟到回复（只认带 reclaimed_at 的，普通过期不放开）
        alt = os.path.join(INBOX, peer, f"{task_id}.json.expired")
        task = read_json(alt, None)
        if isinstance(task, dict) and task.get("reclaimed_at"):
            src = alt
        else:
            task = None
    if not task:
        raise ValueError(f"inbox 里没有任务 {task_id}（peer={peer}），可能已过期或已回复")
    if not answer or not answer.strip():
        raise ValueError("answer 不能为空")
    header = f"# 回答 {task_id} · {peer}\n\n- 模型：{peer_state(peer).get('model', '')}\n- 时间：{now_iso()}\n- 领取：{task.get('taken_at', '')}\n- 模式：{task['mode']}\n\n"
    if task.get("mode") == "build":
        import bus_build as BB
        try:
            t = BB.commit_work(task_id, peer, answer.strip().splitlines()[0] if answer.strip() else "build")
            g = t.get("gates", {})
            header += f"- 提交：{t.get('commit', '')[:10]}\n- 改动文件：{', '.join(t.get('changed_files', [])) or '无'}\n- 门禁：{'通过' if g.get('ok', True) else '失败'}\n\n"
        except Exception as exc:
            header += f"- 提交失败：{exc}\n\n"
    if task.get("mode") == "review":
        first = (answer.strip().splitlines() or [""])[0].strip().upper()
        verdict = "PASS" if first.startswith("PASS") else "RETURN" if first.startswith("RETURN") else ""
        if not verdict:
            raise ValueError("审查回复第一行必须是 PASS 或 RETURN")
        import bus_build as BB
        if task.get("review_of"):
            target = task["review_of"]
            try:
                BB.set_review(target["task_id"], target["teammate"], verdict, peer, answer, expected_commit=target.get("commit", ""))
            except ValueError as exc:
                header += f"\nREVIEW_NOT_APPLIED: {exc}\n\n"  # Preserve the answer; never apply an unbound/stale PASS.
    write_text(os.path.join(OUTBOX, f"{task_id}.{peer}.md"), header + answer.strip() + "\n")
    clear_strikes(peer)   # 交出了回复：之前「领了不做」的记录清零，恢复派单
    day = dt.datetime.now().strftime("%Y-%m-%d")
    os.makedirs(os.path.join(LOG, day), exist_ok=True)
    write_text(os.path.join(LOG, day, f"{task_id}.{peer}.task.md"), render_task(task))
    os.remove(src)
    heartbeat(peer, "idle")
    return "DONE. Archived. Call bus_wait_task again."


def collect(task_id: str, timeout_sec: int) -> dict:
    ensure_dirs()
    meta, where = find_meta(task_id)
    if not meta:
        raise ValueError(f"没有任务 {task_id}")
    end = time.time() + timeout_sec
    while True:
        answers = {}
        for peer in meta["peers"]:
            p = os.path.join(OUTBOX, f"{task_id}.{peer}.md")
            if not os.path.exists(p) and where != OUTBOX:
                p = os.path.join(where, f"{task_id}.{peer}.md")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    answers[peer] = f.read()
        pending = [p for p in meta["peers"] if p not in answers]
        if not pending or time.time() >= end:
            return {"task_id": task_id, "question": meta["question"], "mode": meta["mode"],
                    "answers": answers, "pending": pending, "peer_states": {p: peer_state(p) for p in pending},
                    "similarity": similarity(answers)}
        time.sleep(POLL_SEC)


def similarity(answers: dict) -> dict:
    # 两两词汇 Jaccard 与最长公共片段，用来判断回答是否互相抄读；0.5 以上或公共片段超过 80 字视为可疑
    import difflib
    import itertools
    def toks(t):
        return set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z_]{3,}", t))
    out = {}
    for a, b in itertools.combinations(sorted(answers), 2):
        ta, tb = toks(answers[a]), toks(answers[b])
        j = round(len(ta & tb) / max(1, len(ta | tb)), 2)
        sm = difflib.SequenceMatcher(None, answers[a][:6000], answers[b][:6000], autojunk=False)
        mt = sm.find_longest_match(0, min(6000, len(answers[a])), 0, min(6000, len(answers[b])))
        out[f"{a}~{b}"] = {"jaccard": j, "longest_common": mt.size, "suspect": j >= 0.5 or mt.size >= 80}
    return out


def find_meta(task_id: str):
    # outbox 优先，归档后回退到 log，保证迟到回答仍能 collect
    meta = read_json(os.path.join(OUTBOX, f"{task_id}.meta.json"), None)
    if meta:
        return meta, OUTBOX
    if os.path.isdir(LOG):
        for day in sorted(os.listdir(LOG), reverse=True):
            p = os.path.join(LOG, day, f"{task_id}.meta.json")
            meta = read_json(p, None)
            if meta:
                return meta, os.path.join(LOG, day)
    return None, None


def archive(task_id: str) -> str:
    # 合并完成后把 outbox 里的回答与 meta 移入 log；迟到回答下次 archive 时补入同一目录
    ensure_dirs()
    meta, where = find_meta(task_id)
    day = (meta or {}).get("created", now_iso())[:10]
    dst = os.path.join(LOG, day)
    os.makedirs(dst, exist_ok=True)
    moved = 0
    for f in os.listdir(OUTBOX):
        if f.startswith(task_id + "."):
            os.replace(os.path.join(OUTBOX, f), os.path.join(dst, f))
            moved += 1
    return f"archived {moved} files to log/{day}/"


def requeue(task_id: str, peer: str, extend_min: int = 30) -> str:
    # 把已领取或已过期的任务退回待领取，并续期
    inbox = os.path.join(INBOX, peer)
    for name in (f"{task_id}.taken", f"{task_id}.json.expired", f"{task_id}.json"):
        p = os.path.join(inbox, name)
        task = read_json(p, None)
        if task:
            task["deadline"], task["deadline_ts"] = deadline_after(extend_min)
            write_json(os.path.join(inbox, f"{task_id}.json"), task)
            # meta 是任务级聚合截止，必须跟着续期，否则 close_stale 会把刚续期的副本当过期任务关掉（sota3 d56d）
            meta = read_json(os.path.join(OUTBOX, f"{task_id}.meta.json"), None)
            if meta:
                _extend_meta_deadline(meta, task["deadline"], task["deadline_ts"])
                _save_meta(meta)
            if name != f"{task_id}.json":
                os.remove(p)
            return f"requeued {task_id} for {peer}, deadline {task['deadline']}"
    return f"nothing to requeue for {peer}/{task_id}"


AUTO_REASSIGN_MAX = 2
STALL_SEC = 900


def mark_expired() -> list:
    # 把所有 inbox 里过了 deadline 且还没被领取的任务标成 expired（不依赖该 Agent 自己来轮询）
    ensure_dirs()
    out = []
    now = now_iso()
    for peer in os.listdir(INBOX):
        d = os.path.join(INBOX, peer)
        for f in os.listdir(d):
            if f.endswith(".json"):
                task = read_json(os.path.join(d, f), None)
                if task and deadline_passed(task):
                    os.replace(os.path.join(d, f), os.path.join(d, f + ".expired"))
                    out.append((peer, f[:-5]))
    return out


def _extend_meta_deadline(meta: dict, deadline: str, deadline_ts: float):
    """任务级截止只延不缩。meta 已有 deadline_ts 才按 epoch 取大；升级前的旧 meta 没有它，
    不能把「缺失」当 0 再写入新值——那会让聚合截止早于其他收件人仍有效的旧副本（sota3 d56d）。旧 meta 保持字符串语义。"""
    meta["deadline"] = max(str(meta.get("deadline", "")), deadline)
    ts = meta.get("deadline_ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        meta["deadline_ts"] = max(float(ts), float(deadline_ts))


def _copy_alive(task_id: str, peer: str) -> bool:
    """该收件人手里还有未到期的副本（待领 / 已领）。"""
    for name in (f"{task_id}.json", f"{task_id}.taken"):
        t = read_json(os.path.join(INBOX, peer, name), None)
        if t and not deadline_passed(t):
            return True
    return False


def _drop_inbox(task_id: str, peer: str) -> bool:
    d = os.path.join(INBOX, peer)
    hit = False
    for name in (f"{task_id}.json", f"{task_id}.taken", f"{task_id}.json.expired"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            os.remove(p)
            hit = True
    return hit


def _save_meta(meta: dict):
    write_json(os.path.join(OUTBOX, f"{meta['id']}.meta.json"), meta)


def pick_substitute(peer: str, exclude: list, any_group: bool = False) -> str:
    # 顶替顺序：同组在线空闲 > 同组在线忙 > （any_group 时）任意在线空闲 > 任意在线；都没有返回空
    g = peer_group(peer)
    online = [p for p in list_peers() if p.get("online") and p["peer"] != "controller" and p["peer"] not in exclude and not benched(p["peer"])]
    same = [p for p in online if g and peer_group(p["peer"]) == g]
    # 正在 wait 的优先（派过去马上有人领）；领了不做被罚下的不派
    same.sort(key=lambda p: (not p.get("listening"), activity_of(p) != "idle", p["peer"]))
    if same:
        return same[0]["peer"]
    if any_group and online:
        online.sort(key=lambda p: (not p.get("listening"), activity_of(p) != "idle", p["peer"]))
        return online[0]["peer"]
    return ""


def _ensure_build_seat(task_id: str, meta: dict, target: str) -> dict:
    """施工单换人：任何改派路径都要给新接手人开 worktree 席位（3089）。

    以前只有 bus_reassign._reassign_build 一条路开席位，而 auto_reassign（守护）、bus_web 的手动改派、
    free_stalled 里 mode 没识别成 build 时的通用兜底都直接调 reassign——新接手人收件箱里有单、却没有
    工作副本，自然零交付（实证：施工单 20261001-085345-5724 两次改派后两人分支都停在基线、0 提交）。
    bus_build.add_teammate 幂等（已有席位直接返回），所以 _reassign_build 的既有调用不会重复开 worktree，
    原施工者的 worktree 也照旧保留。没有施工记录的任务按普通单处理，不改变原有行为。
    """
    if (meta or {}).get("mode") != "build":
        return {}
    try:
        import bus_build as BB
    except Exception:  # noqa: BLE001  施工模块不可用时不挡改派
        return {}
    if not BB.record(task_id):
        return {}
    return BB.add_teammate(task_id, target) or {}


def review_author(meta: dict) -> str:
    """审查单被审提交的作者（没有返回空串）。改派 / 顶替 / 重派挑人时都要把他排除。"""
    r = (meta or {}).get("review_of")
    return str(r.get("teammate") or "") if isinstance(r, dict) else ""


def reassign(task_id: str, peer: str, to_peer: str = "", extend_min: int = 60, by: str = "user") -> dict:
    # 把某个 Agent 名下的任务改派：指定 to_peer，或自动挑同组在线 Agent；该 Agent 自己在线且没人可换时退回给它自己并续期
    meta = read_json(os.path.join(OUTBOX, f"{task_id}.meta.json"), None)
    if not meta:
        raise ValueError(f"任务 {task_id} 已归档或不存在，请直接取消或重派")
    if peer not in meta["peers"]:
        raise ValueError(f"{peer} 不在任务 {task_id} 的名单里")
    if os.path.exists(os.path.join(OUTBOX, f"{task_id}.{peer}.md")):
        raise ValueError(f"{peer} 已经回答过 {task_id}")
    author = review_author(meta)
    if to_peer and to_peer == author:
        raise ValueError(f"{author} 是被审提交的作者，不能把审查改派给他本人")
    ex = list(meta["peers"]) + ([author] if author else [])
    target = to_peer or pick_substitute(peer, exclude=ex)
    if not target and peer_state(peer)["online"]:
        target = peer
    if not target and by == "user":
        # 用户手动改派：同组没人时跨组顶替，只要有在线 Agent 就不让任务卡死
        target = pick_substitute(peer, exclude=ex, any_group=True)
    if not target:
        raise ValueError(f"{peer} 离线，且当前没有任何在线 Agent 可以顶替；请取消任务或等 Agent 上线")
    valid_name(target)
    old = read_json(os.path.join(INBOX, peer, f"{task_id}.taken"), None) or read_json(os.path.join(INBOX, peer, f"{task_id}.json"), None) \
        or read_json(os.path.join(INBOX, peer, f"{task_id}.json.expired"), None)
    _drop_inbox(task_id, peer)
    deadline, deadline_ts = deadline_after(extend_min)
    task = old or {"id": task_id, "from": meta.get("from", "controller"), "mode": meta["mode"], "question": meta["question"],
                   "angle": (meta.get("angles") or {}).get(peer, ""), "context": resolve_context(meta.get("context_files") or []), "created": now_iso()}
    task.pop("taken_at", None)
    task.update({"to": target, "deadline": deadline, "deadline_ts": deadline_ts, "reassigned_from": peer if target != peer else task.get("reassigned_from", "")})
    if target != peer:
        seat = _ensure_build_seat(task_id, meta, target)
        if seat:
            task.update(worktree=seat.get("path", ""), branch=seat.get("branch", ""))
    d = os.path.join(INBOX, target)
    os.makedirs(d, exist_ok=True)
    write_json(os.path.join(d, f"{task_id}.json"), task)
    if target != peer:
        meta["peers"] = [target if p == peer else p for p in meta["peers"]]
        if peer in (meta.get("angles") or {}):
            meta["angles"][target] = meta["angles"].pop(peer)
        meta["offline_at_dispatch"] = [p for p in meta.get("offline_at_dispatch", []) if p != peer]
        meta.setdefault("substituted", {})[peer] = target
    _extend_meta_deadline(meta, deadline, deadline_ts)
    meta.setdefault("reassign_log", []).append({"from": peer, "to": target, "by": by, "time": now_iso()})
    _save_meta(meta)
    # listening：接手的人此刻是否真在等任务。不在等的话，页面要明说「可能要等它上线」，而不是默默排队到过期
    return {"task_id": task_id, "from": peer, "to": target, "deadline": deadline, "listening": bool(peer_state(target).get("listening"))}


def cancel_task(task_id: str, reason: str = "cancelled") -> dict:
    # 取消：清掉所有 Agent inbox 里的副本，meta 标注后归档到 log，不再出现在开放任务里
    ensure_dirs()
    meta = read_json(os.path.join(OUTBOX, f"{task_id}.meta.json"), None)
    dropped = []
    for peer in (meta or {}).get("peers", []) if meta else os.listdir(INBOX):
        if _drop_inbox(task_id, peer):
            dropped.append(peer)
    if meta:
        meta["closed"] = {"reason": reason, "time": now_iso()}
        _save_meta(meta)
        archive(task_id)
    return {"task_id": task_id, "dropped": dropped, "archived": bool(meta)}


def seal_task(task_id: str, by: str = "user") -> dict:
    # 封口：法定份数已到时，把还没回答的名额从任务里摘掉（清 inbox、写 meta.sealed），任务立即视为回答齐全，可合并。
    # 迟到的回答仍会被 outbox 收下（不再计入名单，但 collect 会显示为附加回答）。
    ensure_dirs()
    meta = read_json(os.path.join(OUTBOX, f"{task_id}.meta.json"), None)
    if not meta:
        raise ValueError(f"任务 {task_id} 不在开放任务里")
    answered = [p for p in meta["peers"] if os.path.exists(os.path.join(OUTBOX, f"{task_id}.{p}.md"))]
    pending = [p for p in meta["peers"] if p not in answered]
    quorum = int(meta.get("min_answers") or 0) or len(meta["peers"])
    if len(answered) < min(quorum, 1):
        raise ValueError("还没有任何回答，不能封口；请取消或改派")
    for p in pending:
        _drop_inbox(task_id, p)
    meta["peers"] = answered
    meta["min_answers"] = len(answered)
    meta["sealed"] = {"by": by, "time": now_iso(), "dropped": pending}
    _save_meta(meta)
    return {"task_id": task_id, "kept": answered, "dropped": pending}


def auto_seal(grace_sec: int = 600) -> list:
    # 守护：法定份数已到、且到齐后超过 grace_sec 仍有名额未回 → 自动封口，避免任务在队列里长期显示「有 Agent 未回」
    ensure_dirs()
    acted = []
    for f in os.listdir(OUTBOX):
        if not f.endswith(".meta.json"):
            continue
        meta = read_json(os.path.join(OUTBOX, f), None)
        if not meta or meta.get("external_owner") or meta.get("sealed") or meta.get("mode") in ("build", "review"):
            continue
        tid = meta["id"]
        answered = [p for p in meta["peers"] if os.path.exists(os.path.join(OUTBOX, f"{tid}.{p}.md"))]
        quorum = int(meta.get("min_answers") or 0) or len(meta["peers"])
        if len(answered) < quorum or len(answered) == len(meta["peers"]):
            continue
        # 第 quorum 份回答的落盘时间
        times = sorted(os.path.getmtime(os.path.join(OUTBOX, f"{tid}.{p}.md")) for p in answered)
        if time.time() - times[quorum - 1] < grace_sec:
            continue
        try:
            acted.append(seal_task(tid, by="guard"))
        except ValueError:
            pass
    return acted


def redispatch(task_id: str, from_peer: str = "controller", peers: list = None, question: str = "", ttl_min: int = 30, mode: str = "",
               only_pending: bool = False, min_answers: int = 0, carry_answers: bool = False) -> dict:
    # 重派：以原任务包（或页面给的标题）开一个新任务，原任务标记被取代并归档
    # only_pending：只重派尚未回答的名额（过滤必须在 substitute_offline 之前，否则已答的离线者会被换成同组在线者再派一遍）
    # min_answers：钳到 min(min_answers or len(new_peers), len(new_peers))，避免缩到 1 人而法定仍是 2
    # carry_answers：把原任务已到的回答 .md 作为 context_files 带进新任务
    meta = read_json(os.path.join(OUTBOX, f"{task_id}.meta.json"), None)
    if not meta:
        meta, _ = find_meta(task_id)
    # 审查单重派后变成普通任务、不再带 review_of，作者在这一步就得排除，否则可能被派去「审」自己的提交
    author = ((meta or {}).get("review_of") or {}).get("teammate") or ""
    src_peers = [p for p in dict.fromkeys(peers or (meta or {}).get("peers") or []) if p != author]
    q = question or (meta or {}).get("question", "")
    if not q or not src_peers:
        raise ValueError(f"任务 {task_id} 没有可用的原始任务包，请从「发起」页重新派发")
    answered = [p for p in ((meta or {}).get("peers") or []) if os.path.exists(os.path.join(OUTBOX, f"{task_id}.{p}.md"))]
    if only_pending:
        src_peers = [p for p in src_peers if p not in answered]
        if not src_peers:
            raise ValueError("所有名额都已作答，无需重派；可直接「现在合并」")
    # 原名单里离线的 Agent 先换成同组在线 Agent；一个都不在线就报错，不生成空任务
    online = {p["peer"] for p in list_peers() if p.get("online") and p["peer"] != "controller"}
    swaps = substitute_offline(list(src_peers), exclude={author} if author else None)
    new_peers = []
    for p in src_peers:
        t = swaps.get(p, p)
        if t in online and t not in new_peers:
            new_peers.append(t)
    if not new_peers:
        # 原名单全离线且同组无人：退到当前全部在线 Agent（最多 5 个，空闲优先）
        allp = [p for p in list_peers() if p.get("online") and p["peer"] not in ("controller", author)]
        allp.sort(key=lambda p: (activity_of(p) != "idle", p["peer"]))
        new_peers = [p["peer"] for p in allp[:5]]
    if not new_peers:
        raise ValueError("当前没有任何在线 Agent，无法重派；请先到 Agent 页接入")
    new_peers = list(dict.fromkeys(new_peers))
    m = mode or (meta or {}).get("mode") or "independent"
    if m in ("build", "review"):
        m = "independent"
    quorum = min(int(min_answers or len(new_peers)), len(new_peers))
    ctx = list((meta or {}).get("context_files") or [])
    if carry_answers:
        for p in answered:
            rel = os.path.relpath(os.path.join(OUTBOX, f"{task_id}.{p}.md"), WORKSPACE).replace(os.sep, "/")
            if rel not in ctx and not rel.startswith(".."):
                ctx.append(rel)
    res = dispatch(from_peer, new_peers, m, q, ctx, ttl_min=ttl_min, min_answers=quorum, project=str(meta.get("project") or ""))
    new_meta = read_json(os.path.join(OUTBOX, f"{res['task_id']}.meta.json"), {}) or {}
    new_meta["redispatched_from"] = task_id
    new_meta["redispatch_opts"] = {"only_pending": bool(only_pending), "min_answers": quorum, "carry_answers": bool(carry_answers)}
    _save_meta(new_meta) if new_meta else None
    if meta and os.path.exists(os.path.join(OUTBOX, f"{task_id}.meta.json")):
        meta["superseded_by"] = res["task_id"]
        _save_meta(meta)
        cancel_task(task_id, reason=f"superseded_by {res['task_id']}")
    res["superseded"] = task_id
    res["mode"] = m
    return res


def auto_reassign(max_per_peer: int = AUTO_REASSIGN_MAX) -> list:
    # 守护每个周期调用：过期 / 领取过久未回 / 排队但 Agent 离线 的名额自动改派给同组在线 Agent，每个名额最多改派 max_per_peer 次
    ensure_dirs()
    if not priority().get("auto_reassign", True):
        return []
    mark_expired()
    acted = []
    for f in os.listdir(OUTBOX):
        if not f.endswith(".meta.json"):
            continue
        meta = read_json(os.path.join(OUTBOX, f), None)
        if not meta or meta.get("external_owner") or meta.get("mode") in ("build", "review"):
            continue
        task_id = meta["id"]
        for peer in list(meta["peers"]):
            if os.path.exists(os.path.join(OUTBOX, f"{task_id}.{peer}.md")):
                continue
            inbox = os.path.join(INBOX, peer)
            expired = os.path.exists(os.path.join(inbox, f"{task_id}.json.expired"))
            queued = os.path.exists(os.path.join(inbox, f"{task_id}.json"))
            taken = read_json(os.path.join(inbox, f"{task_id}.taken"), None)
            st = peer_state(peer)
            stalled = False
            if taken and taken.get("taken_at"):
                a = age_sec(taken["taken_at"])
                stalled = a is not None and a > STALL_SEC
            reason = "expired" if expired else "stalled" if stalled and not st["online"] else "offline_queued" if queued and not st["online"] else ""
            if not reason:
                continue
            log_ = meta.get("reassign_log", [])
            chain = [r for r in log_ if r.get("by") == "guard" and not r.get("error")]
            if len(chain) >= max_per_peer * len(meta["peers"]):
                continue
            # 同一个名额失败后一小时内不再重试，避免刷日志
            last_fail = [r for r in log_ if r.get("from") == peer and r.get("error")]
            lf = age_sec(last_fail[-1]["time"]) if last_fail else None
            if lf is not None and lf < 3600:
                continue
            try:
                r = reassign(task_id, peer, by="guard")
                acted.append({"task_id": task_id, **r, "reason": reason})
            except ValueError as exc:
                meta = read_json(os.path.join(OUTBOX, f), None) or meta
                meta.setdefault("reassign_log", []).append({"from": peer, "to": "", "by": "guard", "time": now_iso(), "error": str(exc)})
                _save_meta(meta)
                acted.append({"task_id": task_id, "from": peer, "to": "", "reason": reason, "error": str(exc)})
    acted.extend(close_stale(int(priority().get("stale_close_hours", 24))))
    return acted


def close_stale(hours: int) -> list:
    # 过了 deadline 超过 hours 小时、仍没到齐、且剩余名额全部离线且无人可顶替的任务：自动关闭归档，不再占着队列
    out = []
    for f in os.listdir(OUTBOX):
        if not f.endswith(".meta.json"):
            continue
        meta = read_json(os.path.join(OUTBOX, f), None)
        if not meta or meta.get("external_owner") or meta.get("mode") in ("build", "review"):
            continue
        created_age = age_sec(meta.get("created"))
        if not deadline_passed(meta) or created_age is None or created_age < hours * 3600:
            continue
        task_id = meta["id"]
        pending = [p for p in meta["peers"] if not os.path.exists(os.path.join(OUTBOX, f"{task_id}.{p}.md"))]
        answered = len(meta["peers"]) - len(pending)
        if answered >= (meta.get("min_answers") or 1):
            continue  # 到齐的交给 Leader 合并，不在这里关
        if any(_copy_alive(task_id, p) for p in pending):
            continue  # meta 只是聚合：还有未到期的副本（续期 / 改派 / 旧格式混用）就不关（sota3 d56d）
        if any(peer_state(p)["online"] for p in pending):
            continue
        if any(pick_substitute(p, exclude=list(meta["peers"])) for p in pending):
            continue
        cancel_task(task_id, reason=f"stale: deadline passed {hours}h, remaining agents offline")
        out.append({"task_id": task_id, "from": ",".join(pending), "to": "", "reason": "stale_closed"})
    return out


def status() -> dict:
    ensure_dirs()
    open_tasks = sorted(f[:-10] for f in os.listdir(OUTBOX) if f.endswith(".meta.json"))
    queued, claimed, expired = {}, {}, {}
    for peer in os.listdir(INBOX):
        c = inbox_counts(peer)   # queued 只数 .json；.taken 单列为 claimed（已领未回）
        if c["queued"]:
            queued[peer] = c["queued"]
        if c["claimed"]:
            claimed[peer] = c["claimed"]
        if c["expired"]:
            expired[peer] = c["expired"]
    # 已归档任务的迟到回答：outbox 里有回答却没有 meta
    late = sorted({f.split(".")[0] for f in os.listdir(OUTBOX) if f.endswith(".md")} - set(open_tasks))
    peers = list_peers()
    # ADR 0010：时钟/时区不可信的机器要在状态输出里看得见，不能只在内部用
    skewed = {p["peer"]: {"last_seen": p.get("last_seen"), "age_sec": p.get("age_sec")}
              for p in peers if p.get("clock_skew")}
    return {"peers": peers, "open_tasks": open_tasks, "queued": queued, "claimed": claimed,
            "queued_total": {p: queued.get(p, 0) + claimed.get(p, 0) for p in set(queued) | set(claimed)},
            "expired": expired, "late_answers": late, "time": now_iso(),
            "clock_skew": skewed, "clock_skew_n": len(skewed)}
