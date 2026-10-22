import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bus_core as B  # noqa: E402
import bus_projects as P  # noqa: E402

MAX_WAIT = 590


def main():
    action = sys.argv[1]
    raw = sys.stdin.read() or os.environ.get("SHUNCODE_TOOL_ARGS") or "{}"
    a = json.loads(raw)
    if action == "wait":
        timeout = max(5, min(int(a.get("timeout_sec", MAX_WAIT)), MAX_WAIT))
        print(B.wait_task(a["peer"], timeout, a.get("model", "")))
    elif action == "reply":
        print(B.reply(a["task_id"], a["peer"], a["answer"]))
    elif action == "dispatch":
        r = B.dispatch(a.get("from_peer", "main"), a["peers"], a.get("mode", "independent"), a["question"],
                       a.get("context_files", []), int(a.get("ttl_min", 30)), a.get("angles"), int(a.get("min_answers", 0)), a.get("repo_dir") or "",
                       max_peers=int(a.get("max_peers") or 0), parent_task=a.get("parent_task") or "", intro=a.get("intro") or "", project=a.get("project") or "")
        lines = [f"task_id: {r['task_id']}", f"peers: {', '.join(r['peers'])}", f"deadline: {r['deadline']}", f"min_answers: {r['min_answers']}（到齐这么多份即通知合并）"]
        auto = r.get("auto")
        if auto:
            # auto 的挑选依据原样回给调用方，Leader 才能复核「为什么是他不是别人」
            lines.append(f"auto: 选中 {', '.join(auto['picked'])}（空闲候选 {len(auto['idle_candidates'])} 个，上限 {auto['max_peers']}）")
            lines.append("auto 排除: " + "; ".join(f"{s['peer']}={s['reason']}" for s in auto["reasons"]))
        if r["offline"]:
            lines.append(f"OFFLINE（心跳超过 {B.HEARTBEAT_STALE_SEC}s 或从未上线，任务已入队但无人接）: {', '.join(r['offline'])}")
        print("\n".join(lines))
    elif action == "collect":
        timeout = max(5, min(int(a.get("timeout_sec", MAX_WAIT)), MAX_WAIT))
        r = B.collect(a["task_id"], timeout)
        out = [f"# collect {r['task_id']} · mode={r['mode']}", "", f"问题：{r['question'].strip()}", ""]
        for peer, text in r["answers"].items():
            out += [f"---", "", text.strip(), ""]
        sus = [k for k, v in r.get("similarity", {}).items() if v["suspect"]]
        out.append("similarity: " + json.dumps({k: v["jaccard"] for k, v in r.get("similarity", {}).items()}, ensure_ascii=False) + (f"  可疑雷同: {', '.join(sus)}" if sus else "  无雷同"))
        if r["pending"]:
            out.append("---")
            out.append("未回复：" + ", ".join(f"{p}（{'在线' if r['peer_states'][p]['online'] else '离线'}）" for p in r["pending"]))
        else:
            out.append(f"全部回复已到齐。合并后调用 bus_status 或直接 archive：{B.archive.__name__}('{r['task_id']}')")
        print("\n".join(out))
    elif action == "console":
        # 线上事故 2026-10-11：外部脚本把卡片 JSON 从 stdin 灌进来（无 action 字段），旧代码
        # 默认 sub="wait"，以 controller 身份阻塞 590 秒并经 pending_commands(claim=True) 把
        # 用户指令真的领走写 .taken，而调用方丢弃 stdout——指令每 10 分钟被退回一次。
        # 缺 action 一律大声失败，不猜调用意图（猜出来的写操作比失败更危险）。
        sub = a.get("action") or ""
        if sub not in ("wait", "ack", "post", "state"):
            raise ValueError("console 需要 action=wait|ack|post|state，缺省不再默认 wait")
        if sub == "wait":
            timeout = max(5, min(int(a.get("timeout_sec", MAX_WAIT)), MAX_WAIT))
            print(P.console_wait(timeout, a.get("peer") or "controller", session=a.get("session") or "", name=a.get("name") or ""))
        elif sub == "ack":
            # b342：task_id / project 是结构化通道，Leader 给了就不靠解析 result 措辞；老客户端不传，行为不变
            print(P.ack_command(a["command_id"], a.get("result", ""), bool(a.get("ok", True)),
                                task_id=a.get("task_id") or "", project=a.get("project") or "", session=a.get("session") or ""))
        elif sub == "post":
            print(P.post(a["project"], a.get("task_id", ""), a.get("status", ""), a.get("title", ""),
                         a.get("summary", ""), a.get("merge_path", ""), a.get("risks"), a.get("next"), a.get("note", ""), a.get("bridge_url", ""), a.get("artifacts"), a.get("peer") or "controller", a.get("landed"), a.get("decisions_needed"),
                         brief_draft=bool(a.get("brief_draft")),
                         intro=a.get("intro"), intro_source=a.get("intro_source") or "", brief=a.get("brief")))  # None=未传不动，""=清空（85c1 RETURN）
        elif sub == "state":
            print(json.dumps(P.full_state(), ensure_ascii=False, indent=1))
        else:
            raise ValueError("action 只能是 wait / ack / post / state")
    elif action == "build":
        import bus_build as BB
        sub = a.get("action", "summary")
        if sub == "summary":
            print(BB.summary(a["task_id"]))
        elif sub == "review":
            # 派审查任务：把某Agent的 diff 打包发给Reviewer
            r = BB.dispatch_review(a["task_id"], a["teammate"], int(a.get("ttl_min", 20)), a.get("reviewers"))
            print(f"review_task: {r['review_task']}  reviewers: {', '.join(r['reviewers'])}  deadline: {r['deadline']}")
        elif sub == "merge":
            r = BB.merge(a["task_id"], a["teammate"], "controller")
            print(json.dumps(r, ensure_ascii=False))
        elif sub == "close":
            print(json.dumps({"closed": bool(BB.close(a["task_id"]))}, ensure_ascii=False))
        else:
            raise ValueError("build action 只能是 summary / review / merge / close")
    elif action == "status":
        s = B.status()
        out = [f"time: {s['time']}", "peers:"]
        for p in s["peers"]:
            flag = "在线" if p["online"] else "离线"
            out.append(f"  - {p['peer']}  {flag}  status={p.get('status')}  model={p.get('model', '')}  last_seen={p.get('last_seen')}  age={p.get('age_sec', '')}s")
        out.append(f"open_tasks: {', '.join(s['open_tasks']) or '无'}")
        out.append(f"queued: {json.dumps(s['queued'], ensure_ascii=False)}")
        if s.get("expired"):
            out.append(f"expired: {json.dumps(s['expired'], ensure_ascii=False)}")
        if s.get("late_answers"):
            out.append(f"late_answers（已归档任务的迟到回答，可再次 archive）: {', '.join(s['late_answers'])}")
        if a.get("archive"):
            out.append(B.archive(a["archive"]))
        if a.get("requeue"):
            out.append(B.requeue(a["requeue"], a["requeue_peer"]))
        print("\n".join(out))
    else:
        print(f"unknown action {action}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # 错误文本回给模型，避免只有 exit code
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
