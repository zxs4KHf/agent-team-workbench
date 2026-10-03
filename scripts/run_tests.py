"""一键跑全部测试。

    py -3 scripts/run_tests.py            全部
    py -3 scripts/run_tests.py --fast     跳过 ui_* 浏览器集成测试（test_notify_* 仍会启动 Playwright）
    py -3 scripts/run_tests.py notify     只跑文件名含 notify 的
    py -3 scripts/run_tests.py -j 4       4 个并行（浏览器测试仍串行）
    py -3 scripts/run_tests.py --audit    测试之后再做界面体检（scripts/ui_audit.py：对比度 / 横向溢出 / 控制台报错；需服务在跑，只读）

每个测试文件在独立进程里跑，失败的打印末尾输出。退出码：全部通过 0，否则 1。
失败先在 main 上复跑；main 也失败的登记到 docs/FLAKY.md（见 docs/WORKFLOW.md §5）。

浏览器测试（2026-10-02 统一治理）：
- 文件里用到 Playwright / chromium 的都算浏览器测试（不只 ui_*），一律等其它测试全部跑完后再串行跑，不和别的并发。
- 浏览器测试失败会自动复跑一次，用来区分「负载偶发」和「稳定失败」，两次输出都留着。
- 复跑通过只有在该文件已登记在 docs/FLAKY.md 且状态是「待查」时才算通过（打 ⚠ 标记）；没登记的照样算失败——
  不加长等待、不删断言。每次偶发都追加到 log/flaky_runs.jsonl（运行数据，不入库），带首次失败的末尾输出，
  方便定位是哪个选择器超时。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")


def collect(pattern: str, fast: bool) -> list:
    out = []
    for f in sorted(os.listdir(TESTS)):
        if f.startswith("test_") and f.endswith(".py") or f.startswith("smoke_") and f.endswith(".mjs") \
                or f.startswith("ui_") and f.endswith(".py"):
            if fast and f.startswith("ui_"):
                continue
            if pattern and pattern not in f:
                continue
            out.append(f)
    return out


BROWSER_HINT = re.compile(r"playwright|chromium", re.I)


def is_browser(f: str) -> bool:
    if f.startswith("ui_"):
        return True
    try:
        with open(os.path.join(TESTS, f), encoding="utf-8", errors="replace") as fh:
            return bool(BROWSER_HINT.search(fh.read()))
    except OSError:
        return False


def known_flaky(path: str = "") -> set:
    """docs/FLAKY.md 表格里状态为「待查」的测试文件名。已修的不再容忍。"""
    path = path or os.path.join(ROOT, "docs", "FLAKY.md")
    out = set()
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return out
    for ln in lines:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 4 or not cells[-1].startswith("待查"):
            continue
        out.update(re.findall(r"tests/([\w.\-]+\.(?:py|mjs))", cells[0]))
    return out


def log_flaky(f: str, first: tuple, second: tuple) -> None:
    try:
        d = os.path.join(ROOT, "log")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "flaky_runs.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "file": f,
                                 "first": {"rc": str(first[1]), "sec": round(first[2], 1), "tail": first[3][-1500:]},
                                 "retry": {"rc": str(second[1]), "sec": round(second[2], 1)}}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def run_browser(f: str, timeout: int, flaky: set, retry: bool = True) -> tuple:
    """跑一个浏览器测试；失败复跑一次诊断。返回 (f, rc, 秒, 输出, 标记)。"""
    first = run(f, timeout)
    if first[1] == 0 or not retry:
        return (*first, "")
    second = run(f, timeout)
    log_flaky(f, first, second)
    out = f"----- 首次失败（{first[1]}，{first[2]:.0f}s）-----\n{first[3][-1500:]}\n----- 复跑（{second[1]}，{second[2]:.0f}s）-----\n{second[3][-800:]}"
    if second[1] != 0:
        return f, first[1], first[2] + second[2], out, "稳定失败"
    if f in flaky:
        return f, 0, first[2] + second[2], out, "偶发·已登记"
    return f, first[1], first[2] + second[2], out, "偶发·未登记"


def run(f: str, timeout: int) -> tuple:
    argv = ["node", f"tests/{f}"] if f.endswith(".mjs") else [sys.executable, "-X", "utf8", f"tests/{f}"]
    t0 = time.time()
    try:
        r = subprocess.run(argv, cwd=ROOT, capture_output=True, timeout=timeout,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": ROOT})
        rc, out = r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        rc, out = "超时", ""
    return f, rc, time.time() - t0, out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows 控制台默认 GBK
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="跑测试")
    ap.add_argument("pattern", nargs="?", default="")
    ap.add_argument("--fast", action="store_true", help="跳过浏览器测试")
    ap.add_argument("-j", type=int, default=1, help="并行数（浏览器测试总是串行）")
    ap.add_argument("--timeout", type=int, default=300, help="单个文件超时秒数")
    ap.add_argument("--audit", action="store_true", help="测试后跑 scripts/ui_audit.py 界面体检（改了 web/ 时用）")
    ap.add_argument("--no-retry", action="store_true", help="浏览器测试失败不复跑")
    a = ap.parse_args()
    files = collect(a.pattern, a.fast)
    if not files:
        print("没有匹配的测试")
        return 1
    ui = [f for f in files if is_browser(f)]
    flaky = known_flaky()
    rest = [f for f in files if f not in ui]
    print(f"共 {len(files)} 个测试文件（浏览器 {len(ui)} 个）\n")
    results = []

    warned = []

    def report(res):
        f, rc, sec, out = res[:4]
        tag = res[4] if len(res) > 4 else ""
        mark = ("⚠" if tag else "✓") if rc == 0 else "✗"
        print(f"  {mark} {f:<48} {sec:6.1f}s{'' if rc == 0 else f'  → {rc}'}{f'  [{tag}]' if tag else ''}", flush=True)
        results.append((f, rc, sec, out))
        if rc == 0 and tag:
            warned.append((f, out))

    with ThreadPoolExecutor(max_workers=max(1, a.j)) as pool:
        for res in pool.map(lambda f: run(f, a.timeout), rest):
            report(res)
    if ui:
        print(f"\n  —— 浏览器测试（串行，失败复跑一次诊断）——", flush=True)
    for f in ui:
        report(run_browser(f, a.timeout, flaky, retry=not a.no_retry))
    bad = [r for r in results if r[1] != 0]
    print(f"\n通过 {len(results) - len(bad)} / {len(results)}，用时 {sum(r[2] for r in results):.0f}s"
          + (f"（其中 {len(warned)} 个是已登记偶发、复跑通过，见 log/flaky_runs.jsonl）" if warned else ""))
    for f, out in warned:
        print(f"\n----- ⚠ {f}：已登记偶发（docs/FLAKY.md），复跑通过 -----\n{out[-1200:]}")
    for f, rc, _, out in bad:
        print(f"\n===== {f}（{rc}）=====\n{out[-1500:]}")
    if a.audit:  # 界面体检：tokens 守卫之上的第二道护栏，看渲染后的真实页面
        print("\n===== 界面体检 scripts/ui_audit.py =====", flush=True)
        au = subprocess.run([sys.executable, "-X", "utf8", os.path.join(ROOT, "scripts", "ui_audit.py"), "--web", os.path.join(ROOT, "web")], cwd=ROOT)
        if au.returncode == 2:  # 环境不满足：不算失败，但也绝不算通过
            print("（界面体检未运行：环境不满足。送审记录里要写「体检未运行」，不能当作通过）")
        elif au.returncode != 0:  # 1 = 查出问题；其它非零 = 崩溃 / 被杀，同样算失败，避免假绿（fable2 ca3a）
            bad.append(("ui_audit", au.returncode, 0, ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
