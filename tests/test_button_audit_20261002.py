"""按钮与接口体检（2026-10-02 用户反馈「很多按钮点了没用，一键改派报 404」）。

静态核对两件事，任何一条不通过就列出具体是哪个按钮 / 哪个接口：
1. 页面里每个字面写死的 data-action="x"，在某个 JS 文件里都有处理函数（'x': … => 或按 data-action="x" 选择元素的代码）；
2. 页面里每个字面写死的 api('/api/…') 调用，bus_web 的 do_GET / do_POST 里都有对应路由，而且**方法对得上**
   （带 method:'POST' 的必须在 do_POST，不带的必须在 do_GET；方法错了服务器同样回 404）。
/api/arena/<name> 再到 arena_server 里核对 name。
只读源码，不起服务。跑法：py -3 tests/test_button_audit_20261002.py
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web", "v2")


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def _js_sources():
    out = {}
    for dp, dn, fn in os.walk(WEB):
        dn[:] = [d for d in dn if d != "vendor"]
        for f in fn:
            if f.endswith((".js", ".html")):
                with open(os.path.join(dp, f), encoding="utf-8") as fh:
                    out[os.path.relpath(os.path.join(dp, f), WEB).replace("\\", "/")] = fh.read()
    return out


def _calls(src: str, fn: str):
    """找出 fn( … ) 的完整参数文本（按括号配对），只要第一个参数是字面字符串的。"""
    for m in re.finditer(r"\b%s\(\s*([`'\"])(/api/[^`'\"]*)\1" % fn, src):
        depth, i = 0, m.start() + len(fn)
        while i < len(src):
            c = src[i]
            depth += c == "("
            depth -= c == ")"
            if depth == 0:
                break
            i += 1
        yield m.group(2), src[m.start():i + 1]


def _routes(section: str) -> tuple:
    exact = set(re.findall(r'u\.path == "(/api/[^"]+)"', section))
    prefixes = set(re.findall(r'u\.path\.startswith\("(/api/[^"]+)"\)', section))
    return exact, prefixes


class ButtonAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _js_sources()
        web = _read("bus_web.py")
        g, p = web.index("    def do_GET(self):"), web.index("    def do_POST(self):")
        cls.get_routes = _routes(web[g:p])
        cls.post_routes = _routes(web[p:])
        arena = _read("plugins", "arena", "server", "arena_server.py")
        cls.arena_names = set(re.findall(r'name == "([^"]+)"', arena)) | set(re.findall(r'name\.startswith\("([^"]+)"\)', arena))

    def _ok(self, path, routes):
        exact, prefixes = routes
        return path in exact or any(path.startswith(px) for px in prefixes)

    def test_every_button_has_a_handler(self):
        everything = "\n".join(self.js.values())
        actions = set()
        for src in self.js.values():
            actions |= set(re.findall(r'data-action=\\?"([a-z0-9][a-z0-9-]*)\\?"', src))
            actions |= set(re.findall(r"data-action='([a-z0-9][a-z0-9-]*)'", src))
        missing = []
        for a in sorted(actions):
            key = r"""(?:['"]%s['"]|(?<![\w.-])%s)""" % (re.escape(a), re.escape(a))   # 带引号或不带引号的键（refresh: () => …）
            handled = re.search(key + r"""\s*:\s*(async\s+)?(\([^)]*\)|\w+)\s*=>""", everything) \
                or re.search(r"""['"]%s['"]\s*:\s*\w+\s*[,}]""" % re.escape(a), everything) \
                or re.search(r"""data-action=\\?["']%s\\?["']\]""" % re.escape(a), everything) \
                or re.search(r"""action\s*===?\s*['"]%s['"]""" % re.escape(a), everything)
            if not handled:
                missing.append(a)
        self.assertEqual(missing, [], "这些按钮点了没有任何处理函数")

    def test_every_api_call_has_a_route_with_the_right_method(self):
        problems = []
        for name, src in self.js.items():
            for fn in ("api", "textApi", "fetch"):
                for path, text in _calls(src, fn):
                    path = path.split("?")[0].split("${")[0]
                    post = bool(re.search(r"method\s*:\s*['\"](POST|PUT|DELETE)['\"]", text))
                    if fn == "api" and re.search(r"function api\(path, body\)", src):
                        # hub.js 自带 api(path, body)：传了 body 就是 POST
                        post = bool(re.match(r"api\(\s*[`'\"][^`'\"]*[`'\"]\s*,", text))
                    if path.startswith("/api/arena/"):
                        name_part = path[len("/api/arena/"):]
                        if name_part and not any(name_part == n or name_part.startswith(n) or n.startswith(name_part) for n in self.arena_names):
                            problems.append(f"{name}: {path}（arena_server 没有 {name_part}）")
                        continue
                    routes = self.post_routes if post else self.get_routes
                    if not self._ok(path, routes):
                        other = self.get_routes if post else self.post_routes
                        why = f"方法不对：服务器只在 {'GET' if post else 'POST'} 里有它" if self._ok(path, other) else "服务器没有这个接口"
                        problems.append(f"{name}: {'POST' if post else 'GET'} {path}（{why}）")
        self.assertEqual(sorted(set(problems)), [], "这些接口调用会 404")


if __name__ == "__main__":
    unittest.main()
