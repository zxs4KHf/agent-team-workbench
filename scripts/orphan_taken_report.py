# -*- coding: utf-8 -*-
"""孤儿 .taken 盘点（默认 dry-run，只打印，绝不改动任何文件）。

背景 fb-8a7c79854b：inbox 里的 `<task>.taken` 是「任务包已被某个会话领走、但那个会话
在回复前就结束了」。它不会再被任何 Agent 领走（领取端看到 .taken 就跳过），于是永久
滞留。过去 queued 把 .json 与 .taken 合并计数，界面就显示成「idle 却排队 7」。

本脚本只做盘点，输出每条孤儿的：peer / task_id / 领取时刻 / 滞留时长 / 该 task 的当前状态
（outbox 里还有没有 meta、这个 peer 到底回没回、任务有没有过 deadline）。

用法：
    py -3 scripts/orphan_taken_report.py                  # 全部 .taken
    py -3 scripts/orphan_taken_report.py --min-hours 24   # 只看滞留超过 24 小时的
    py -3 scripts/orphan_taken_report.py --json           # 机器可读

为什么不自动 requeue：任务可能早已过 ttl、也可能已被 Leader 以别的方式处理（改派、
人工合并、直接取消）。自动改回 .json 会让别人重新做一遍已经做完的事，代价远高于
「多留一个僵尸文件」。所以默认策略是**盘点 + 人工定夺**，处置动作不在本脚本内。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import bus_core as B  # noqa: E402


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def scan(min_hours: float = 0.0, root: str = "") -> list:
    inbox_root = os.path.join(root, "inbox") if root else B.INBOX
    outbox_root = os.path.join(root, "outbox") if root else B.OUTBOX
    now = dt.datetime.now().timestamp()
    rows = []
    if not os.path.isdir(inbox_root):
        return rows
    for peer in sorted(os.listdir(inbox_root)):
        d = os.path.join(inbox_root, peer)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".taken"):
                continue
            path = os.path.join(d, fn)
            task_id = fn[:-len(".taken")]
            try:
                taken_at = os.path.getmtime(path)
            except OSError:
                continue
            hours = (now - taken_at) / 3600.0
            if hours < min_hours:
                continue
            task = B.read_json(path, None) or {}
            meta_path = os.path.join(outbox_root, f"{task_id}.meta.json")
            answer_path = os.path.join(outbox_root, f"{task_id}.{peer}.md")
            deadline = str(task.get("deadline") or "")
            overdue = ""
            if deadline:
                try:
                    overdue = "是" if dt.datetime.fromisoformat(deadline) < dt.datetime.now() else "否"
                except ValueError:
                    overdue = "?"
            rows.append({
                "peer": peer, "task_id": task_id,
                "taken_at": _iso(taken_at), "stale_hours": round(hours, 1),
                "mode": str(task.get("mode") or ""),
                "deadline": deadline, "past_deadline": overdue,
                "task_open": os.path.exists(meta_path),      # outbox 里还有 meta = 该任务还没归档
                "this_peer_answered": os.path.exists(answer_path),
            })
    rows.sort(key=lambda r: -r["stale_hours"])
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="盘点 inbox 里的孤儿 .taken（只读）")
    ap.add_argument("--min-hours", type=float, default=0.0, help="只列出滞留超过 N 小时的")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--root", default="", help="workspace 根（测试用；默认用 bus_core 的 INBOX/OUTBOX）")
    a = ap.parse_args(argv)

    rows = scan(a.min_hours, a.root)
    if a.json:
        print(json.dumps({"dry_run": True, "count": len(rows), "items": rows}, ensure_ascii=False, indent=2))
        return 0

    print("孤儿 .taken 盘点（DRY-RUN：本脚本不会修改或删除任何文件）")
    print("=" * 96)
    if not rows:
        print("没有 .taken 滞留项。")
        return 0
    head = "%-14s %-26s %-19s %8s %-6s %-6s %-6s" % (
        "peer", "task_id", "领取时刻", "滞留(h)", "已过期", "任务开", "已回答")
    print(head)
    print("-" * 96)
    per_peer = {}
    for r in rows:
        per_peer[r["peer"]] = per_peer.get(r["peer"], 0) + 1
        print("%-14s %-26s %-19s %8.1f %-6s %-6s %-6s" % (
            r["peer"], r["task_id"], r["taken_at"], r["stale_hours"],
            r["past_deadline"] or "-", "是" if r["task_open"] else "否",
            "是" if r["this_peer_answered"] else "否"))
    print("-" * 96)
    print("合计 %d 条，按 peer：%s" % (
        len(rows), "、".join(f"{k} {v}" for k, v in sorted(per_peer.items(), key=lambda x: -x[1]))))
    print()
    print("处置建议（需人工确认，本脚本不执行）：")
    print("  · 滞留很短（几分钟~1 小时）的多半是**正在做**的活，不是孤儿；判孤儿请配 --min-hours，")
    print("    建议取 LEASE_TTL_SEC(300s) 的若干倍，别用 HEARTBEAT_STALE_SEC(180s) 那种短阈值")
    print("  · 任务开=否   → 任务已归档，.taken 纯属残留，可直接移入 log/<日期>/ 留档")
    print("  · 已回答=是   → 该 peer 其实回过了，.taken 是清理漏网，同上")
    print("  · 已过期=是   → 重新派发意义不大，建议归档而不是 requeue")
    print("  · 其余（任务开=是 且 未回答 且 未过期）→ 才值得考虑 requeue，且要先确认 Leader 没用别的方式处理过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
