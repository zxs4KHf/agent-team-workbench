"""桌面通知插件：增量读工作台事件（/api/event-log），把值得你知道的几类合并成 Windows 通知。

只读：不调任何写接口。纯函数（describe / summarize / in_quiet / RateLimiter）与副作用（HTTP、弹窗）分开，
tests/test_desktop_notify.py 只测纯函数，不会在你的桌面上真弹窗。
"""
import argparse
import importlib.util
from pathlib import Path
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request
from urllib.parse import quote, urlparse


_spec = importlib.util.spec_from_file_location('desktop_notify_ledger', Path(__file__).with_name('ledger.py'))
_ledger = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ledger)
Ledger, LedgerError = _ledger.Ledger, _ledger.LedgerError
CapacityError, ConflictError, LockedError = _ledger.CapacityError, _ledger.ConflictError, _ledger.LockedError

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
$ErrorActionPreference = 'Stop'
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
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class Worker:
    def __init__(self, ledger, config, transport=None):
        self.ledger = ledger
        self.types = list(config.get('types') or LABELS)
        self.batch_sec = max(0, int(config.get('batch_sec', 20)))
        self.per_hour = max(0, int(config.get('max_per_hour', 12)))
        self.quiet = str(config.get('quiet_hours') or '')
        self.transport = transport if transport is not None else toast

    def poll(self, base, fetch=None, now=None):
        free = self.ledger.remaining()
        if not free:
            return 'backpressure'
        state = self.ledger.inspect()
        read = get if fetch is None else fetch
        response = read(f"{base}/api/event-log?cursor={quote(state['cursor'])}&limit={min(200, free)}")
        if not isinstance(response, dict) or not isinstance(response.get('events'), list) or 'cursor' not in response:
            raise LedgerError('Invalid event-log response; cursor unchanged')
        accepted = [event for event in response['events'] if isinstance(event, dict) and describe(event) and event.get('type') in self.types]
        try:
            self.ledger.ingest(response['cursor'], accepted, time.time() if now is None else now)
        except CapacityError:
            return 'backpressure'
        return 'ingested'

    def deliver(self, now=None, hour=None):
        now = time.time() if now is None else now
        hour = dt.datetime.now().hour if hour is None else hour
        pending = self.ledger.pending()
        if not pending:
            return 'idle'
        if now - min(row['queued_at'] for row in pending) < self.batch_sec:
            return 'batching'
        if in_quiet(self.quiet, hour):
            return 'quiet'
        selected = pending[:64]
        events = [row['event'] for row in selected]
        # Configuration changes only affect new intake, never erase accepted work.
        accepted_types = list(dict.fromkeys(self.types + [event.get('type') for event in events]))
        message = summarize(events, accepted_types)
        if not message:
            raise LedgerError('Accepted notification cannot be rendered; manual recovery required')
        attempt = self.ledger.begin([row['id'] for row in selected], now, self.per_hour)
        if attempt is None:
            return 'rate_limited'
        try:
            handed_off = self.transport(*message) is True
        except Exception:
            handed_off = False  # Outcome is ambiguous, not safe to replay.
        self.ledger.finish(attempt, handed_off, now)
        return 'handed_off' if handed_off else 'unknown'


def main(argv=None):
    parser = argparse.ArgumentParser(description='Durable local notification delivery; inspect/resolve while plugin is stopped')
    parser.add_argument('action', nargs='?', choices=('serve', 'inspect', 'retry', 'resolve'), default='serve')
    parser.add_argument('event_id', nargs='?')
    parser.add_argument('--state-dir', default=os.environ.get('WORKBENCH_PLUGIN_DATA'))
    parser.add_argument('--expected-revision', type=int)
    parser.add_argument('--actor', default='')
    parser.add_argument('--reason', default='')
    args = parser.parse_args(argv)
    if not args.state_dir:
        print('desktop-notify: WORKBENCH_PLUGIN_DATA or --state-dir is required', file=sys.stderr)
        return 2
    try:
        if args.action != 'serve':
            with Ledger(args.state_dir) as ledger:
                if args.action != 'inspect':
                    ledger.decide(args.event_id, args.expected_revision, args.action, args.actor, args.reason, time.time())
                print(json.dumps(ledger.inspect(), ensure_ascii=False, indent=2))
            return 0
        base = os.environ.get('WORKBENCH_URL', '').rstrip('/')
        parsed = urlparse(base)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise LedgerError('WORKBENCH_URL must be a loopback HTTP origin')
        config = json.loads(os.environ.get('WORKBENCH_PLUGIN_CONFIG') or '{}')
        poll = max(2, int(config.get('poll_sec', 5)))
        def initialize():
            response = get(base + '/api/event-log?cursor=now&limit=1')
            if not isinstance(response, dict) or 'cursor' not in response:
                raise LedgerError('Cannot establish initial event cursor')
            return response['cursor']
        with Ledger(args.state_dir, initialize=initialize, max_pending=config.get('max_pending', 1000)) as ledger:
            worker = Worker(ledger, config)
            down_since = None
            while True:
                try:
                    worker.poll(base)
                    down_since = None
                except LedgerError:
                    raise  # Durable state failure must stop, never silently reset/retry.
                except Exception:
                    down_since = down_since or time.time()
                    if time.time() - down_since > 300:
                        print('[notify] Workbench unavailable; pending ledger preserved', file=sys.stderr)
                        return 0
                worker.deliver()
                time.sleep(poll)
    except LedgerError as exc:
        print('desktop-notify: ' + str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
