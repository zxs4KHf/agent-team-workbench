"""首次启动准备（v0.1 发布）：新克隆的仓库没有 console/ 目录、也没有 MCP 主令牌，守护和 MCP 都会直接退出。
启动脚本（start_guard.py / start_named_mcp.py）先调 ensure()：
- 建好运行目录；
- 没有主令牌就生成一把（48 位随机，只写进本机 console/mcp_token.txt，已在 .gitignore，不打印）；
- 检查 MCP 依赖装没装，没装给出一行安装命令。
可重复调用，已有的东西一律不动。"""
import importlib.util
import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.path.join(HERE, "console", "mcp_token.txt")
RUNTIME_DIRS = ("console", "log", "inbox", "outbox", "peers", "projects", "events")
DEPS = ("mcp", "uvicorn", "starlette")


def missing_deps() -> list:
    return [m for m in DEPS if importlib.util.find_spec(m) is None]


def ensure(quiet: bool = False) -> dict:
    for d in RUNTIME_DIRS:
        os.makedirs(os.path.join(HERE, d), exist_ok=True)
    created = False
    try:
        with open(TOKEN, encoding="utf-8-sig") as f:
            ok = len(f.read().strip()) >= 32
    except OSError:
        ok = False
    if not ok:
        tmp = TOKEN + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(secrets.token_urlsafe(36))
        os.replace(tmp, TOKEN)
        created = True
    deps = missing_deps()
    if not quiet:
        if created:
            print("[first_run] 已生成 MCP 主令牌：console/mcp_token.txt（只在本机，不要发给别人；Leader 接入链接里会带上它）")
        if deps:
            print(f"[first_run] 缺少依赖 {', '.join(deps)}：请先运行  {os.path.basename(sys.executable)} -m pip install -r requirements.txt")
    return {"token_created": created, "missing_deps": deps}


if __name__ == "__main__":
    r = ensure()
    sys.exit(1 if r["missing_deps"] else 0)
