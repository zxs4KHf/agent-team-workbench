"""桌面通知插件：增量读工作台事件（/api/event-log），把值得你知道的几类合并成 Windows 通知。

只读：不调任何写接口。纯函数（describe / summarize / in_quiet / RateLimiter）与副作用（HTTP、弹窗）分开，
tests/test_desktop_notify.py 只测纯函数，不会在你的桌面上真弹窗。
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request
from urllib.parse import quote

LABELS = {"decision.merged": "待你拍板", "decision.landed": "施工已落地", "intake.added": "收件箱新增", "plugin.gave_up": "插件已停止"}


def describe(ev: dict):
    """事件 → (类别, 一行说明)；不关心的返回 None。"""
    t, d = ev.get("type", ""), ev.get("data") or {}
    title = str(d.get("title") or d.get("task_id") or "").strip()[:60]
    if t == "decision.merged":
        return t, f"{title or '一条决策'}（{d.get('project') or '—'}）"
    if t == "decision.landed":
        return t, title or "一项施工"
    if t == "intake.added":
        n = d.get("count")
        return t, f"{n if isinstance(n, int) and n > 0 else '若干'} 条，来自 {d.get('source') or '未知来源'}"
    if t == "plugin.gave_up":
        return t, f"{d.get('id') or '某个插件'} 反复崩溃，工作台已停止重启它"
    return None


def summarize(events: list, types: list):
    """一批事件 → 一条通知 (标题, 正文)；没有值得通知的返回 None。多类时标题列出各类计数。"""
    items = [x for x in (describe(e) for e in events) if x and x[0] in types]
    if not items:
        return None
    counts = {}
    for k, _ in items:
        counts[k] = counts.get(k, 0) + 1
    if len(items) == 1:
        k, line = items[0]
        return f"工作台 · {LABELS[k]}", line
    head = " · ".join(f"{LABELS[k]} {n}" for k, n in counts.items())
    body = "\n".join(f"{LABELS[k]}：{line}" for k, line in items[:4]) + (f"\n……共 {len(items)} 条" if len(items) > 4 else "")
    return f"工作台 · {head}", body


def in_quiet(spec: str, hour: int) -> bool:
    """quiet_hours 如 "23-8"（跨零点）或 "12-14"；格式不对视为不设。"""
    try:
        a, b = (int(x) for x in str(spec).split("-"))
    except (ValueError, TypeError):
        return False
    if not (0 <= a <= 23 and 0 <= b <= 23) or a == b:
        return False
    return a <= hour < b if a < b else (hour >= a or hour < b)


class RateLimiter:
    def __init__(self, per_hour: int):
        self.per_hour, self.sent = max(0, int(per_hour)), []

    def allow(self, now: float) -> bool:
        self.sent = [t for t in self.sent if now - t < 3600]
        if len(self.sent) >= self.per_hour:
            return False
        self.sent.append(now)
        return True


PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:WB_TOAST_TITLE)) | Out-Null
$x.Item(1).AppendChild($t.CreateTextNode($env:WB_TOAST_BODY)) | Out-Null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""


def toast(title: str, body: str) -> bool:
    """弹 Windows 通知。标题/正文走环境变量，不拼进命令行（防注入）。非 Windows 只记日志。"""
    print(f"[notify] {title} | {body.replace(chr(10), ' / ')}", flush=True)
    if os.name != "nt":
        return False
    env = {**os.environ, "WB_TOAST_TITLE": title, "WB_TOAST_BODY": body}
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS], env=env, capture_output=True, text=True, timeout=20, creationflags=0x08000000)
    if r.returncode:
        print("[notify] 弹窗失败：" + (r.stderr or r.stdout)[-300:], flush=True)
    return r.returncode == 0


def get(url: str, timeout: int = 10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    base = os.environ.get("WORKBENCH_URL", "").rstrip("/")
    if not base:  # 宿主总会设；手动运行时给句人话而不是 KeyError
        print("desktop-notify：缺少环境变量 WORKBENCH_URL（应由插件宿主设置，如 http://127.0.0.1:8765），退出", file=sys.stderr)
        return 2
    cfg = json.loads(os.environ.get("WORKBENCH_PLUGIN_CONFIG") or "{}")
    types = list(cfg.get("types") or LABELS)
    batch, poll = max(0, int(cfg.get("batch_sec", 20))), max(2, int(cfg.get("poll_sec", 5)))
    limiter, quiet = RateLimiter(cfg.get("max_per_hour", 12)), str(cfg.get("quiet_hours") or "")
    cursor, pending, first_at, down_since = "now", [], 0.0, None  # cursor=now：首次启动不补发历史
    while True:
        try:
            r = get(f"{base}/api/event-log?cursor={quote(cursor)}&limit=200")
            cursor, down_since = r.get("cursor") or cursor, None
            new = [e for e in r.get("events") or [] if describe(e) and e.get("type") in types]
            if new and not pending:
                first_at = time.time()
            pending += new
        except Exception as e:  # noqa: BLE001 - 工作台不可达：约定是长时间不可达就自行退出
            down_since = down_since or time.time()
            if time.time() - down_since > 300:
                print(f"[notify] 工作台 5 分钟不可达，退出：{e}", flush=True)
                return 0
        if pending and time.time() - first_at >= batch:
            msg = summarize(pending, types)
            pending = []
            if msg:
                if in_quiet(quiet, dt.datetime.now().hour):
                    print(f"[notify] 免打扰时段，只记日志：{msg[0]}", flush=True)
                elif not limiter.allow(time.time()):
                    print(f"[notify] 超过每小时上限，只记日志：{msg[0]}", flush=True)
                else:
                    toast(*msg)
        time.sleep(poll)


if __name__ == "__main__":
    sys.exit(main())
