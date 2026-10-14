# -*- coding: utf-8 -*-
"""无头启动器（任务 20261011-214338-8b25 → 返修 20261011-225953-67af，方案稿 fb-e879da0e14 → 260a）。

以 CREATE_NO_WINDOW 启动工作台(8765)与 Arena 控制面(9091)，stdout/stderr 重定向到
console/<name>.log（console/ 在 .gitignore 内，运行日志不入库），不新建可见控制台窗口。

- 端口已被占用的服务视为已在运行，跳过并说明，不产生重复实例。
- 日志超过 MAX_LOG_BYTES 自动按时间戳轮转（<name>-<YYYYmmdd-HHMMSS>.log），轮转后
  在原路径新建空文件（任务书语义：改名**并新建**）；历史档按 --keep 保留最近 N 代，
  更老的自动清理，避免「不弹窗换成慢慢写满磁盘」。
- --rotate-only：只做一次轮转检查后退出，便于单独验证轮转逻辑。
- 退出码有语义：全部服务「已启动」或「已在运行而跳过」→ 0；有服务失败 → 1。
- stdin 一律 DEVNULL（与 bus_guard.spawn 对齐）：无头服务不该继承交互式 stdin。

Arena 服务路径说明：ADR 0014 后 arena 服务在 plugins/arena/server/arena_server.py
（旧文档里的 arena/arena_server.py 已迁移），本文件按新路径配置。
workbench 日志名与 bus_guard.py:132 对齐（workbench_8765.log），同一服务只有一份日志。
"""
import argparse
import glob
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 与 bus_guard.py:25 同一套成熟组合：不新建窗口 + 新进程组 + 脱离调用方作业对象
# （没有 BREAKAWAY 时，子进程会被拉起它的作业对象整体回收——实测：工具会话结束子进程即死）。
CREATE_NO_WINDOW = 0x08000000 | 0x00000200 | 0x01000000   # NO_WINDOW | NEW_PROCESS_GROUP | BREAKAWAY_FROM_JOB
LOG_DIR = os.path.join(ROOT, "console")
MAX_LOG_BYTES = 5 * 1024 * 1024        # 5 MB 触发轮转
KEEP_GENERATIONS = 3                   # 轮转历史保留代数（--keep 可调）

SERVICES = [
    {"name": "workbench_8765", "script": os.path.join("bus_web.py"),
     "args": ["--port", "8765"], "port": 8765},
    {"name": "arena_9091",
     "script": os.path.join("plugins", "arena", "server", "arena_server.py"),
     "args": ["--port", "9091"], "port": 9091},
]


def rotate_file(path, max_bytes=MAX_LOG_BYTES):
    """超过阈值把日志改名为 <base>-<时间戳><ext>，并在原路径新建空文件；返回是否轮转。

    文件被占用（os.replace 失败）视为本轮不轮转，不影响调用方。"""
    try:
        if not os.path.exists(path) or os.path.getsize(path) < max_bytes:
            return False
    except OSError:
        return False
    base, ext = os.path.splitext(path)
    dst = "%s-%s%s" % (base, time.strftime("%Y%m%d-%H%M%S"), ext)
    try:
        os.replace(path, dst)
        open(path, "ab").close()   # 任务书语义：改名**并新建**；--rotate-only 跑完也留下服务日志路径
    except OSError:
        return False
    return True


def prune_history(path, keep=KEEP_GENERATIONS):
    """只保留最近 keep 代 <base>-<时间戳> 历史，更老的删除；返回删除清单。"""
    base, ext = os.path.splitext(path)
    hist = sorted(glob.glob("%s-*%s" % (base, ext)))
    removed = []
    for old in (hist[:-keep] if keep > 0 else hist):
        try:
            os.remove(old)
            removed.append(old)
        except OSError:
            pass
    return removed


def rotate_logs(log_dir=LOG_DIR, max_bytes=MAX_LOG_BYTES, services=SERVICES, keep=KEEP_GENERATIONS):
    """对每个服务的日志做一次轮转检查 + 历史清理，返回 {name: 是否轮转}。"""
    out = {}
    for svc in services:
        path = os.path.join(log_dir, svc["name"] + ".log")
        out[svc["name"]] = rotate_file(path, max_bytes)
        prune_history(path, keep)
    return out


def port_busy(port, probe=None):
    """端口探测：probe 可注入（测试用）；默认 TCP connect_ex 127.0.0.1:port。"""
    if probe is not None:
        return bool(probe(port))
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def launch(svc, popen=None, probe=None, log_dir=LOG_DIR, max_bytes=MAX_LOG_BYTES, keep=KEEP_GENERATIONS):
    """启动单个服务；返回 (是否启动, 说明)。端口占用时明确跳过，不重复起实例。"""
    popen = popen or subprocess.Popen
    log_path = os.path.join(log_dir, svc["name"] + ".log")
    if port_busy(svc["port"], probe=probe):
        return False, "%s: 端口 %d 已被占用，视为已在运行，不重复启动" % (svc["name"], svc["port"])
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError:
        pass
    rotate_file(log_path, max_bytes)
    prune_history(log_path, keep)
    args = [sys.executable, "-X", "utf8", os.path.join(ROOT, svc["script"])] + list(svc.get("args", []))
    with open(log_path, "ab") as out:
        proc = popen(args, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                     creationflags=CREATE_NO_WINDOW, close_fds=True)
    return True, "%s: pid=%d 日志=%s" % (svc["name"], proc.pid, log_path)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="无头启动工作台(8765)与 Arena 控制面(9091)；不新建控制台窗口")
    ap.add_argument("--rotate-only", action="store_true",
                    help="只做一次日志轮转检查后退出，不启动服务")
    ap.add_argument("--log-dir", default=LOG_DIR, help="日志目录（默认 console/）")
    ap.add_argument("--max-bytes", type=int, default=MAX_LOG_BYTES,
                    help="轮转阈值字节数（默认 5MB；调小便于单独验证轮转逻辑）")
    ap.add_argument("--keep", type=int, default=KEEP_GENERATIONS,
                    help="轮转历史保留代数（默认 3；0=只留当前不留历史）")
    ns = ap.parse_args(argv)
    if ns.rotate_only:
        for name, done in rotate_logs(ns.log_dir, ns.max_bytes, keep=ns.keep).items():
            print("%s: %s" % (name, "已轮转" if done else "未达阈值，保持原样"))
        return 0
    failures = 0
    for svc in SERVICES:
        started, detail = launch(svc, log_dir=ns.log_dir, max_bytes=ns.max_bytes, keep=ns.keep)
        print(detail)
        # 已在运行而跳过不算失败；既没启动也不是运行中跳过才是失败（退出码可被脚本判断）
        if not started and "已被占用" not in detail:
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
