import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bus_core as B  # noqa: E402

# Windows 控制台默认 GBK，任务包里的 U+2212 等字符会让 print 崩溃
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    # 本地 CLI Agent（Claude Code / Codex CLI 等）的适配器，与 Bridge 工具走同一目录协议
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    x = sp.add_parser("wait"); x.add_argument("--peer", required=True); x.add_argument("--model", default=""); x.add_argument("--timeout", type=int, default=590)
    x = sp.add_parser("reply"); x.add_argument("--peer", required=True); x.add_argument("--task", required=True); x.add_argument("--file", required=True, help="回答 Markdown 文件路径")
    x = sp.add_parser("dispatch"); x.add_argument("--peers", required=True); x.add_argument("--mode", default="independent"); x.add_argument("--question", required=True); x.add_argument("--context", nargs="*", default=[])
    x = sp.add_parser("collect"); x.add_argument("--task", required=True); x.add_argument("--timeout", type=int, default=590)
    x = sp.add_parser("status"); x.add_argument("--archive", default="")
    x = sp.add_parser("console_wait"); x.add_argument("--peer", required=True); x.add_argument("--timeout", type=int, default=30)
    a = ap.parse_args()
    if a.cmd == "wait":
        text = B.wait_task(a.peer, a.timeout, a.model)
        try:
            print(text)
        except Exception:
            # 打印失败时把刚领取的任务退回，让下一次 wait 能重新领取
            m = re.search(r"# 任务 (\S+)", text)
            if m:
                B.requeue(m.group(1), a.peer)
            raise
    elif a.cmd == "reply":
        with open(a.file, encoding="utf-8") as f:
            print(B.reply(a.task, a.peer, f.read()))
    elif a.cmd == "dispatch":
        print(json.dumps(B.dispatch("cli", a.peers.split(","), a.mode, a.question, a.context), ensure_ascii=False, indent=2))
    elif a.cmd == "collect":
        r = B.collect(a.task, a.timeout)
        for peer, text in r["answers"].items():
            print(text)
        if r["pending"]:
            print("pending:", ", ".join(r["pending"]))
    elif a.cmd == "status":
        print(json.dumps(B.status(), ensure_ascii=False, indent=2))
        if a.archive:
            print(B.archive(a.archive))
    elif a.cmd == "console_wait":
        import bus_projects as P
        print(P.console_wait(a.timeout, a.peer))


if __name__ == "__main__":
    main()
