import argparse
import os
import sys
import time
import tomllib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))
import bus_core as B  # noqa: E402
from llm import Chat  # noqa: E402

SYSTEM = (
    "你是一位关卡设计师，正在参加多模型对读。只依据任务包内容回答，中文，术语保留英文，句子短，"
    "禁止复述任务包，禁止空话。按任务包的「模式要求」组织回答。"
)


def load_profiles() -> dict:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    p = os.path.join(root, "workbench", "config.toml")
    with open(p, "rb") as f:
        return tomllib.load(f)["models"]


def serve(peers: list, profiles: dict, cycle_sec: int, allow_paid: bool):
    # 每个 API peer 用短超时轮询，任务到达即调用模型回答；默认只用 Coding Plan profile
    chats = {peer: Chat(profiles[peer], allow_paid=allow_paid) for peer in peers}
    print("daemon up:", ", ".join(f"{p}={profiles[p]['model']}" for p in peers), flush=True)
    while True:
        for peer in peers:
            task_text = B.wait_task(peer, cycle_sec, profiles[peer]["model"])
            if task_text.startswith(B.IDLE_TEXT):
                continue
            task_id = task_text.split("\n", 1)[0].replace("# 任务", "").strip()
            print(f"[{B.now_iso()}] {peer} <- {task_id}", flush=True)
            try:
                answer = chats[peer].text(SYSTEM, task_text)
            except Exception as e:
                answer = f"（模型调用失败：{e}）"
            print(B.reply(task_id, peer, answer), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--peers", default="ark-deepseek,ark-glm", help="config.toml [models] 里的 profile 名，逗号分隔")
    ap.add_argument("--cycle", type=int, default=3, help="每个 peer 单次轮询秒数")
    ap.add_argument("--allow-paid", action="store_true", help="允许使用按量计费接口（如 DeepSeek 官方）")
    a = ap.parse_args()
    profiles = load_profiles()
    peers = [p.strip() for p in a.peers.split(",") if p.strip()]
    for p in peers:
        if p not in profiles:
            sys.exit(f"config.toml 里没有 profile {p}")
        B.valid_name(p)
    try:
        serve(peers, profiles, a.cycle, a.allow_paid)
    except KeyboardInterrupt:
        for p in peers:
            B.heartbeat(p, "offline")


if __name__ == "__main__":
    main()
