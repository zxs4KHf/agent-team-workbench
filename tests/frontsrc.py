"""测试用：读「某个前端模块 + 从它拆出去的文件」的源码（与 tests/_frontsrc.mjs 同一规则；文件名不能以下划线开头，.gitignore 忽略 _*.py，ADR 0015）。"""
import os
import re

JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "v2", "js")
SPLITS = {
    "app.js": lambda f: f == "main.js" or re.fullmatch(r"app-[\w-]+\.js", f) is not None,   # app.js 只是入口，本体在 main.js
    "views.js": lambda f: f in ("page-overview.js", "page-running.js", "page-decisions.js", "page-artifacts.js"),
    "views2.js": lambda f: f in ("page-compose.js", "page-agents.js", "page-settings.js"),
}


def front_src(name: str) -> str:
    extra = sorted(f for f in os.listdir(JS) if SPLITS.get(name, lambda _: False)(f))
    parts = []
    for f in [name, *extra]:
        with open(os.path.join(JS, f), encoding="utf-8") as fh:
            parts.append(fh.read())
    return "\n".join(parts)
