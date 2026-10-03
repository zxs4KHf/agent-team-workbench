# -*- coding: utf-8 -*-
"""b342 只读报告：盘点被旧 ack 逻辑误降级的派发占位决策。

旧 `_ack_command` 在 ok=True 但 task_id 解析不出来时，把 `pending-<command_id>` 占位写成 cancelled，
用户的派发于是在所有面板上消失。本脚本只读地把这些记录挑出来并给出分类建议：

  real_task    console/done/<cid>.json 的 result 里能解析出 task_id，且该 task_id 在 outbox/log/builds 里能验证 → 可回填
  redispatch   找不到可验证的 task_id，但 outbox/log 里有与该占位同期、标题吻合的任务 → 需人工确认后重派
  真取消        done 记录 ok=false，或 payload 明确是用户取消 → 保持 cancelled
  unknown      没有 done 记录，或证据不足 → 人工判断

默认只打印分类计数与 task_id 列表，**不写 decisions.json**。加 --json <路径> 只输出一份报告文件。
本脚本不提供 apply：复活历史记录必须由用户提供显式映射文件并逐条 CAS，见 AGENTS.md。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bus_core as B  # noqa: E402
import bus_projects as P  # noqa: E402

KINDS = ("real_task", "redispatch", "真取消", "unknown")


def _done_record(cid: str) -> dict:
    return B.read_json(os.path.join(P.DONE, f"{cid}.json"), None) or {}


def classify(slug: str, rec: dict) -> dict:
    tid = rec.get("task_id", "")
    cid = tid[len("pending-"):] if tid.startswith("pending-") else ""
    out = {"project": slug, "record": tid, "command_id": cid, "status": rec.get("status", ""),
           "title": rec.get("title", ""), "kind": "unknown", "candidate": "", "why": ""}
    if not cid:
        out["why"] = "不是 pending-<command_id> 占位"
        return out
    done = _done_record(cid)
    if not done:
        out["why"] = f"console/done/{cid}.json 不存在，无法判断当初 ack 了什么"
        return out
    if done.get("status") == "failed" or done.get("ok") is False:
        out["kind"] = "真取消"
        out["why"] = "回执本身就是 ok=false"
        return out
    result = done.get("result", "") or ""
    tid2, src, warn = P.resolve_ack_task_id(cid, result, slug, done.get("task_id", "") or "")
    if tid2:
        out.update(kind="real_task", candidate=tid2, why=f"回执里解析到可验证的 task_id（{src}）")
        return out
    out.update(kind="redispatch", why=(warn or {}).get("message", "回执里没有可验证的 task_id"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="只读盘点被误降级的 pending 占位决策（b342）")
    ap.add_argument("--json", default="", help="把完整报告写成 JSON（唯一会落盘的东西，不碰 decisions.json）")
    ap.add_argument("--all-status", action="store_true", help="不只看 cancelled，列出所有 pending-* 占位")
    a = ap.parse_args()

    rows = []
    for slug in sorted(os.listdir(P.PROJECTS)):
        if slug.startswith(".") or not os.path.isdir(os.path.join(P.PROJECTS, slug)):
            continue
        for rec in P.decisions(slug):
            if not str(rec.get("task_id", "")).startswith("pending-"):
                continue
            if not a.all_status and rec.get("status") != "cancelled":
                continue
            rows.append(classify(slug, rec))

    counts = {k: 0 for k in KINDS}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    print(f"共 {len(rows)} 条占位记录；分类：" + json.dumps(counts, ensure_ascii=False))
    for kind in KINDS:
        sel = [r for r in rows if r["kind"] == kind]
        if not sel:
            continue
        print(f"\n── {kind}（{len(sel)}）")
        for r in sel:
            print(f"  {r['project']:<12} {r['record']:<36} -> {r['candidate'] or '—':<22} {r['why']}")
    print("\n本脚本没有修改任何决策文件。回填必须由用户提供显式映射并逐条 CAS。")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"counts": counts, "rows": rows}, fh, ensure_ascii=False, indent=1)
        print(f"报告已写入 {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
