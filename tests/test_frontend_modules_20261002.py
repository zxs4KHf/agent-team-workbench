"""前端模块结构（ADR 0015 按页面拆模块，2026-10-02）。静态检查，不起服务。

- 入口 app.js 只做一件事：import './main.js'。其它模块一律不得 import './app.js'——index.html 用带版本号的地址加载入口，
  不带版本号再 import 一次就是另一个模块实例，整个应用会被加载、初始化两次（拆分当天踩到：
  `Cannot access 'PREF_ACTIONS' before initialization`，事件绑定和实时连接都是双份）。
- 每个 `import { a, b } from './x.js'` 里的名字，x.js 都得真有导出（含 `export { … } from` 转出）；
  少一个，浏览器会整页拒绝加载。
"""
import os
import re
import unittest

JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "v2", "js")
IMPORT_RX = re.compile(r"^import\s*\{([^}]*)\}\s*from\s*'\./([\w.-]+\.js)';", re.M)
EXPORT_DEF_RX = re.compile(r"^export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)", re.M)
EXPORT_LIST_RX = re.compile(r"^export\s*\{([^}]*)\}(?:\s*from\s*'\./[\w.-]+\.js')?;", re.M)


def src(f):
    with open(os.path.join(JS, f), encoding="utf-8") as fh:
        return fh.read()


def exports_of(f):
    t = src(f)
    names = set(EXPORT_DEF_RX.findall(t))
    for grp in EXPORT_LIST_RX.findall(t):
        for part in grp.split(","):
            part = part.strip()
            if part:
                names.add(part.split(" as ")[-1].strip())
    return names


def names_in(group):
    return [p.strip().split(" as ")[0].strip() for p in group.split(",") if p.strip()]


class FrontendModules(unittest.TestCase):
    def test_entry_is_thin_and_never_reimported(self):
        entry = src("app.js")
        code = [l for l in entry.splitlines() if l.strip() and not l.strip().startswith("//")]
        self.assertEqual(code, ["export * from './main.js';"])
        for f in os.listdir(JS):
            if f.endswith(".js") and f != "app.js":
                self.assertNotRegex(src(f), r"from\s*'\./app\.js'", f"{f} 不得 import './app.js'（会造成第二个应用实例）")

    def test_every_named_import_is_exported(self):
        missing = []
        for f in sorted(os.listdir(JS)):
            if not f.endswith(".js"):
                continue
            for group, target in IMPORT_RX.findall(src(f)):
                if not os.path.exists(os.path.join(JS, target)):
                    missing.append(f"{f} → ./{target}（文件不存在）")
                    continue
                have = exports_of(target)
                for n in names_in(group):
                    if n not in have:
                        missing.append(f"{f} → ./{target} 没有导出 {n}")
        self.assertEqual(missing, [])

    def test_pages_are_split_out(self):
        for f in ("page-overview.js", "page-running.js", "page-decisions.js", "page-artifacts.js",
                  "page-compose.js", "page-agents.js", "page-settings.js"):
            self.assertTrue(os.path.exists(os.path.join(JS, f)), f)
        self.assertLess(os.path.getsize(os.path.join(JS, "views.js")), 20_000, "views.js 只留共用的小件")


if __name__ == "__main__":
    unittest.main()
