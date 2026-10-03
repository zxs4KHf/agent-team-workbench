"""数据源插件：个人助理 → 工作台收件箱。

只读个人助理已经采集好的 JSON（默认 ~/.config/daily-assistant/data/collected/<日期>-<来源>.json），
按文件修改时间感知变化（改了就在 poll_sec 秒内送达），命中规则的新条目送进工作台「收件箱」，
由本人确认后才变成 Leader 指令。不改个人助理的任何文件，也不 import 它的代码。

输入契约（详见 README「输入契约」）：每个采集文件必须是对象且带布尔 ok。
  ok 为 true       → ok，读 items
  ok 为 false      → collect_failed（带 unsupported 标记时归为 unsupported）
  ok 缺失 / 纯列表 → 按配置 ok_missing 处理，默认 "fail" 视为 collect_failed，不隐式当成功
  文件损坏 / 非 JSON / 非对象 → collect_failed
所有有文件且非 unsupported 的来源都 collect_failed 时：--check 立即退出 3；常驻模式连续 fail_rounds 轮后退出 3。

环境变量（工作台启动插件时注入）：
  WORKBENCH_URL           工作台地址
  WORKBENCH_PLUGIN_ID     本插件 id
  WORKBENCH_PLUGIN_CONFIG 合并后的配置（JSON）
  WORKBENCH_PLUGIN_DATA   本插件的私有数据目录（状态文件放这里，不入库）
"""
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.request

DONE_WORDS = {"已完成", "完成", "已关闭", "关闭", "已取消", "取消", "done", "completed", "closed", "cancelled", "canceled"}
STATUS_KEYS = ("status", "状态", "state")

OK_MISSING_POLICIES = ("fail", "accept")
UNSUPPORTED_MARKS = ("unsupported", "not_supported", "not supported", "不支持", "不适用")
EXIT_CONFIG = 2        # 配置错误
EXIT_ALL_FAILED = 3    # 所有有文件且非 unsupported 的来源都 collect_failed
EXIT_NO_INPUT = 4      # 仅 --check：没有任何可采集的输入（文件都缺失，或全部 unsupported）
BASELINE_ALL = "__all__"


def log(*a):
    print(dt.datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def load_config() -> dict:
    cfg = json.loads(os.environ.get("WORKBENCH_PLUGIN_CONFIG") or "{}")
    cfg.setdefault("collected_dir", "~/.config/daily-assistant/data/collected")
    cfg.setdefault("sources", ["xiaomi", "xiaomi-notes", "feishu", "quadrant"])
    cfg.setdefault("keywords", "")
    cfg.setdefault("poll_sec", 10)
    cfg.setdefault("lookback_days", 1)
    cfg.setdefault("ok_missing", "fail")
    cfg.setdefault("fail_rounds", 3)
    return cfg


def is_done(item: dict) -> bool:
    """已完成的条目不进收件箱。兼容布尔 done/completed 和四象限表格的「状态」列。"""
    if item.get("done") is True or item.get("completed") is True:
        return True
    for k in STATUS_KEYS:
        v = item.get(k)
        if isinstance(v, str) and v.strip().lower() in DONE_WORDS:
            return True
    fields = item.get("fields")
    if isinstance(fields, dict):
        return is_done(fields)
    return False


def text_of(item: dict) -> str:
    return ((item.get("title") or "") + "\n" + (item.get("text") or item.get("content") or "")).strip()


def fingerprint(source: str, item: dict) -> str:
    # 小米待办的 id 是页面顺序号，不稳定；用 来源 + 标题 + 正文前 200 字 做指纹（与旧 adapter 一致）
    body = (item.get("title") or "") + "\n" + (item.get("text") or item.get("content") or "")[:200]
    return hashlib.sha1((source + "\n" + body).encode("utf-8")).hexdigest()[:16]


def _unsupported(d: dict) -> bool:
    if d.get("unsupported") is True or d.get("skipped") is True:
        return True
    for k in ("reason", "error", "status", "message"):
        v = d.get(k)
        if isinstance(v, str) and any(m in v.lower() for m in UNSUPPORTED_MARKS):
            return True
    return False


def classify(d, ok_missing: str = "fail"):
    """把一份采集文件解析后的 JSON 归到 (kind, reason)。kind ∈ ok / unsupported / collect_failed。"""
    if isinstance(d, list):
        return ("ok", "list_legacy") if ok_missing == "accept" else ("collect_failed", "ok_missing")
    if not isinstance(d, dict):
        return "collect_failed", "not_object"
    ok = d.get("ok")
    if ok is True:
        return "ok", "ok_true"
    if _unsupported(d):
        return "unsupported", "unsupported"
    if ok is False:
        return "collect_failed", "ok_false"
    if "ok" not in d:
        return ("ok", "ok_missing") if ok_missing == "accept" else ("collect_failed", "ok_missing")
    return "collect_failed", "ok_not_bool"


def read_source(path: str, ok_missing: str = "fail"):
    """返回 (kind, reason, items)。kind 不是 ok 时 items 为空列表。兼容 {ok,items} / {ok,data:{items}} / 纯列表。"""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except OSError:
        return "collect_failed", "unreadable", []
    except ValueError:
        return "collect_failed", "bad_json", []
    kind, reason = classify(d, ok_missing)
    if kind != "ok":
        return kind, reason, []
    items = d if isinstance(d, list) else (d.get("items") or (d.get("data") or {}).get("items") or [])
    if not isinstance(items, list):
        items = []
    return "ok", reason, [x for x in items if isinstance(x, dict)]


def read_items(path: str, ok_missing: str = "fail"):
    """兼容旧签名：ok 返回条目列表，其他情况返回 None。"""
    kind, _, items = read_source(path, ok_missing)
    return items if kind == "ok" else None


def candidates(source: str, items: list, seen: dict, rule, today: str) -> list:
    out = []
    for it in items:
        if is_done(it):
            continue
        t = text_of(it)
        if not t:
            continue
        fp = fingerprint(source, it)
        if fp in seen:
            continue
        seen[fp] = today
        if rule is None or rule.search(t):
            title = (it.get("title") or t.split("\n", 1)[0])[:120]
            out.append({"fingerprint": fp, "title": title, "text": t[:2000], "origin": source,
                        "due": str(it.get("due") or it.get("deadline") or it.get("截止") or "")[:40],
                        "url": str(it.get("url") or "")[:500], "tags": [source]})
    return out


class State:
    """data_dir 为 None 时只在内存里，不落盘（--check 用）。"""

    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "state.json") if data_dir else None
        self.d = {}
        if self.path:
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.d = json.load(f)
            except (OSError, ValueError):
                self.d = {}
        self.first_run = "seen" not in self.d
        legacy = (not self.first_run) and "baselined" not in self.d   # 旧状态文件：整体已打过基线
        self.d.setdefault("seen", {})
        self.d.setdefault("mtimes", {})
        self.d.setdefault("baselined", BASELINE_ALL if legacy else [])

    def save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)


def post_intake(url: str, source: str, items: list):
    body = json.dumps({"source": source, "items": items}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url.rstrip("/") + "/api/intake", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


_BAD_DAY_WARNED = set()


def parse_day(v):
    """把 YYYY-MM-DD 解析成 date；不合契约返回 None。

    只认 YYYY-MM-DD：`date.fromisoformat` 在 3.11+ 还收 `20260929`、`2026-W01-1` 等写法，
    放进来会让「格式错就回退系统日期」这条契约变得模糊。
    """
    s = str(v or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def as_of_from_argv(argv=None):
    """从命令行取 --as-of YYYY-MM-DD / --as-of=YYYY-MM-DD。没传返回 None；传了但格式不对抛 ValueError。

    为什么新增命令行参数而不是只靠环境变量：环境变量会**无差别继承给所有子进程**，
    测试里一旦 setUp 设了它，后面任何 subprocess 都被悄悄钉在那一天，谁在钉、钉到哪天都看不见；
    命令行参数是显式的、只作用于这一次调用、在进程列表里一眼可见，误伤正式运行的风险也更小。
    环境变量保留下来兼容既有用法，优先级：**--as-of > DAILY_ASSISTANT_TODAY > 系统日期**。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    for i, a in enumerate(argv):
        raw = None
        if a == "--as-of":
            raw = argv[i + 1] if i + 1 < len(argv) else ""
        elif a.startswith("--as-of="):
            raw = a.split("=", 1)[1]
        if raw is None:
            continue
        d = parse_day(raw)
        if d is None:
            raise ValueError(f"--as-of 需要 YYYY-MM-DD，收到 {raw!r}")
        return d
    return None


def business_day(as_of=None) -> dt.date:
    """业务日。优先级：显式参数 as_of > 环境变量 DAILY_ASSISTANT_TODAY > 系统日期。

    as_of 是本轮新增的显式注入点（`--as-of` 或直接传参），`--check` 子进程与常驻 main() 共用同一个值，
    彻底消除「各自取系统时钟、跨午夜错开一天」的竞态——这个竞态真实发生过：写死日期的测试在次日
    变成「找不到文件 → 连 127.0.0.1:9 重试 5 分钟」的全量超时。
    环境变量仍然有效（测试专用；它对正式运行同样生效，会把常驻 / --once 钉在那一天，正式运行请勿设置）。
    格式错只告警一次（常驻每轮都会读）。
    """
    if as_of is not None:
        return as_of if isinstance(as_of, dt.date) else (parse_day(as_of) or dt.date.today())
    v = (os.environ.get("DAILY_ASSISTANT_TODAY") or "").strip()
    if v:
        d = parse_day(v)
        if d is not None:
            return d
        if v not in _BAD_DAY_WARNED:
            _BAD_DAY_WARNED.add(v)
            log(f"DAILY_ASSISTANT_TODAY={v!r} 不是 YYYY-MM-DD，改用系统日期")
    return dt.date.today()


def scan_report(cfg: dict, st: State, now: dt.date = None, baseline: bool = True) -> dict:
    """扫一轮：只处理修改时间变了的文件。

    返回 {candidates, files:{"<日期>-<来源>": (kind, reason, n)}, ok, collect_failed, unsupported, missing, baselined_now}。
    来源级别 kind：任一日期文件 collect_failed → collect_failed；否则读过成功 → ok；否则 unsupported；否则 missing。
    基线（baseline=True，常驻模式与 --check 用）：某来源尚未打基线时，本轮读到的全部条目只记「已见」不入队；
    该来源本轮没有失败文件才算基线完成。baseline=False 时不做基线处理，读到什么就按规则返回什么（scan() 旧语义）。
    """
    now = now or business_day()
    root = os.path.expanduser(cfg["collected_dir"])
    rule = re.compile(cfg["keywords"], re.I) if cfg.get("keywords") else None
    policy = cfg.get("ok_missing", "fail")
    today = now.isoformat()
    days = [(now - dt.timedelta(days=i)).isoformat() for i in range(int(cfg["lookback_days"]), -1, -1)]
    if st.d.get("baselined") == BASELINE_ALL:
        st.d["baselined"] = list(cfg["sources"])
    pending = [s for s in cfg["sources"] if s not in set(st.d["baselined"])] if baseline else []
    # 已见记录只保留 60 天（按首次见到的日期）；先清理再扫，过期后仍未完成的条目在文件下次变化时会再次入队
    cutoff = (now - dt.timedelta(days=60)).isoformat()
    st.d["seen"] = {k: v for k, v in st.d["seen"].items() if v >= cutoff}
    rep = {"candidates": [], "files": {}, "ok": [], "collect_failed": [], "unsupported": [], "missing": [], "baselined_now": []}
    per_source = {s: set() for s in cfg["sources"]}
    for day in days:
        for source in cfg["sources"]:
            key = f"{day}-{source}"
            path = os.path.join(root, f"{key}.json")
            try:
                mt = os.path.getmtime(path)
            except OSError:
                rep["files"][key] = ("missing", "no_file", 0)
                per_source[source].add("missing")
                continue
            if st.d["mtimes"].get(key) == mt:         # 只有读成功过的文件才会记 mtime
                rep["files"][key] = ("ok", "unchanged", 0)
                per_source[source].add("ok")
                continue
            kind, reason, items = read_source(path, policy)
            per_source[source].add(kind)
            if kind != "ok":                          # 失败/不适用：不记 mtime，下轮重读
                rep["files"][key] = (kind, reason, 0)
                continue
            st.d["mtimes"][key] = mt
            if source in pending:
                n = len(candidates(source, items, st.d["seen"], None, today))   # 基线：只记已见
                rep["files"][key] = ("ok", reason + ":baseline", n)
            else:
                rep["candidates"] += candidates(source, items, st.d["seen"], rule, today)
                rep["files"][key] = ("ok", reason, len(items))
    for s, kinds in per_source.items():
        level = "collect_failed" if "collect_failed" in kinds else "ok" if "ok" in kinds else "unsupported" if "unsupported" in kinds else "missing"
        rep[level].append(s)
        if s in pending and level == "ok":
            st.d["baselined"].append(s)
            rep["baselined_now"].append(s)
    # mtimes 只保留最近几天，防止状态文件无限增长
    keep = set(f"{d}-{s}" for d in days for s in cfg["sources"])
    st.d["mtimes"] = {k: v for k, v in st.d["mtimes"].items() if k in keep}
    return rep


def scan(cfg: dict, st: State, now: dt.date = None) -> list:
    """旧语义：不做基线处理，只返回待送的候选（未发送）。--once 与既有测试使用；常驻模式走 scan_report。"""
    return scan_report(cfg, st, now, baseline=False)["candidates"]


def all_failed(rep: dict) -> bool:
    """所有有文件且非 unsupported 的来源都 collect_failed。缺文件、unsupported 不算失败也不算成功。"""
    return bool(rep["collect_failed"]) and not rep["ok"]


def describe(rep: dict) -> str:
    """只给来源状态与计数，不含条目正文。"""
    parts = []
    for key, (kind, reason, n) in sorted(rep["files"].items()):
        parts.append(f"{key}: {kind}" + (f"({reason})" if reason and reason not in ("no_file",) else "") + (f" {n} 条" if n else ""))
    return "; ".join(parts) or "没有任何来源文件"


def check(cfg: dict, now: dt.date = None) -> int:
    """只读契约检查：不读写状态、不入队、不打印正文。返回退出码 0 / 3 / 4。

    now 就是注入点：`--check` 从 `--as-of` 拿到它，和常驻 main() 用的是同一个值。
    """
    now = now or business_day()
    rep = scan_report(cfg, State(None), now)
    for key, (kind, reason, n) in sorted(rep["files"].items()):
        print(f"{key}: {kind} {reason}" + (f" {n} 条" if n else ""))
    print(f"ok={rep['ok']} collect_failed={rep['collect_failed']} unsupported={rep['unsupported']} missing={rep['missing']}")
    if all_failed(rep):
        print(f"所有有文件的来源都 collect_failed，退出码 {EXIT_ALL_FAILED}")
        return EXIT_ALL_FAILED
    if not rep["ok"]:
        print(f"没有可采集的输入（缺文件或全部 unsupported），退出码 {EXIT_NO_INPUT}")
        return EXIT_NO_INPUT
    if rep["collect_failed"]:
        print(f"部分来源 collect_failed：{rep['collect_failed']}（其余来源正常，退出码 0）")
    return 0


def validate_config(cfg: dict):
    """配置错误直接退出 2，与 keywords 非法正则同一口径。"""
    if cfg.get("ok_missing") not in OK_MISSING_POLICIES:
        log(f"配置错误：ok_missing 只能是 {OK_MISSING_POLICIES}，当前 {cfg.get('ok_missing')!r}")
        sys.exit(EXIT_CONFIG)
    try:
        if int(cfg.get("fail_rounds", 3)) < 1:
            raise ValueError
    except (TypeError, ValueError):
        log(f"配置错误：fail_rounds 必须是 ≥1 的整数，当前 {cfg.get('fail_rounds')!r}")
        sys.exit(EXIT_CONFIG)
    if cfg.get("keywords"):
        try:
            re.compile(cfg["keywords"])
        except re.error as e:
            log(f"配置错误：keywords 不是合法的正则表达式（{e}）。请在「扩展与来源 → 插件 → 配置」里改正后重新启用")
            sys.exit(EXIT_CONFIG)


def main(as_of: dt.date = None):
    """常驻模式。as_of 非空时**所有轮次钉在这一天**（测试与复现用），否则每轮取当天业务日
    （正式运行要跨午夜自然滚动到新的一天，所以不能在启动时取一次就固定）。"""
    url = os.environ.get("WORKBENCH_URL") or "http://127.0.0.1:8765"
    pid = os.environ.get("WORKBENCH_PLUGIN_ID") or "daily-assistant"
    data_dir = os.environ.get("WORKBENCH_PLUGIN_DATA") or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".data")
    os.makedirs(data_dir, exist_ok=True)
    cfg = load_config()
    validate_config(cfg)
    st = State(data_dir)
    log(f"启动：读取 {os.path.expanduser(cfg['collected_dir'])}，来源 {cfg['sources']}，每 {cfg['poll_sec']} 秒检查，"
        f"ok 缺失={cfg['ok_missing']}，全源失败 {cfg['fail_rounds']} 轮后退出 {EXIT_ALL_FAILED}")
    pending = []
    unreachable_since = None
    bad_rounds = 0
    last_failed = None
    while True:
        try:
            rep = scan_report(cfg, st, as_of)      # as_of=None → 每轮取业务日；非空 → 全程钉死
            if rep["baselined_now"]:
                log(f"首次读取来源 {rep['baselined_now']}：已把现有条目记为基线，不导入（之后只送新增条目）")
            failed = sorted(rep["collect_failed"])
            if failed != last_failed:
                if failed:
                    log(f"采集失败的来源 {failed}（不入队、不记已见，下轮重试）：{describe(rep)}")
                elif last_failed:
                    log("采集恢复正常")
                last_failed = failed
            if all_failed(rep):
                bad_rounds += 1
                if bad_rounds >= int(cfg["fail_rounds"]):
                    log(f"所有有文件的来源连续 {bad_rounds} 轮 collect_failed，插件退出（退出码 {EXIT_ALL_FAILED}）：{describe(rep)}")
                    sys.exit(EXIT_ALL_FAILED)
            else:
                bad_rounds = 0
            pending += rep["candidates"]
            if pending:
                r = post_intake(url, pid, pending[:200])
                log(f"送入收件箱 {r.get('added')} 条（跳过 {r.get('skipped')}）")
                pending = pending[200:]
            else:                        # 没东西要送时也探一下，工作台没了就能及时退出
                urllib.request.urlopen(url.rstrip("/") + "/api/ping", timeout=5).close()
            if not pending:              # 全部送完才记"已见过"；中途崩溃则下次重扫，收件箱按指纹去重
                st.save()
            unreachable_since = None
        except Exception as e:          # 工作台暂时不可达：条目留在 pending，下轮重试
            log("本轮失败，稍后重试：", e)
            unreachable_since = unreachable_since or time.time()
            if time.time() - unreachable_since > 300:
                log("工作台 5 分钟不可达，插件退出（工作台重启后会重新拉起）")
                return
        time.sleep(max(3, int(cfg["poll_sec"])))


if __name__ == "__main__":
    try:
        AS_OF = as_of_from_argv()          # --check 与常驻走同一个值，三方不会各自取系统时钟
    except ValueError as exc:
        print(exc, file=sys.stderr)
        sys.exit(EXIT_CONFIG)
    if "--check" in sys.argv:
        c = load_config()
        validate_config(c)
        sys.exit(check(c, AS_OF))
    elif "--once" in sys.argv:
        c = load_config()
        s = State(os.environ.get("WORKBENCH_PLUGIN_DATA") or ".")
        print(json.dumps(scan(c, s, AS_OF), ensure_ascii=False, indent=1))
    else:
        main(AS_OF)
