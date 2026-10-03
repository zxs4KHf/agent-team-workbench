"""把一个大前端模块按「入口函数」拆成多个文件（ADR 0015，一次性重构工具，留着给以后再拆用）。

    py -3 scripts/split_module.py web/v2/js/views.js page-running.js=renderRunning,runningMeta page-decisions.js=renderDecisions ...

规则：
- 按顶层语句切块（行首的 function / async function / const / let / export ... / 注释 / 其它语句）；注释块归给紧跟其后的定义。
- 命令行指定每个新文件的入口符号；只被某一个新文件用到的辅助块跟着搬过去（可传递），被多处用到的留在原文件。
- 原文件对搬走的、原本 export 的符号加一行 `export { … } from './新文件.js'`，所以所有引用原文件的地方（app.js、测试）路径不变。
- 新文件头部复制原文件的全部 import（浏览器里没用到的 import 无副作用），再补上从原文件 / 兄弟新文件引的符号。
- 函数体一个字不改。只给需要被别处引用的定义加 export。
- 有「顶层立即执行、又用到别的模块符号」的块会打印警告（循环引用下可能读到未初始化的值），需要人工确认。
"""
import os
import re
import sys

START_RX = re.compile(r"^(?:export\s|import\s|async\s+function\b|function\b|const\s|let\s|var\s|class\s|Object\.assign\(|window\.|document\.|globalThis\.|if\s*\(|for\s*\(|try\s*\{|\((?:async\s*)?(?:function|\()|[A-Za-z_$][\w$]*(?:\.[\w$]+)*\s*\()")
DEF_RX = re.compile(r"^(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)|^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)")


def split_chunks(text: str) -> list:
    """顶层块：[(name|None, text)]。块从「行首非空白、且不是 } ) ] 的行」开始；前置注释并入后面的块。"""
    lines = text.split("\n")
    chunks, cur, pending_comments = [], [], []
    def flush():
        if cur:
            chunks.append("\n".join(cur))
    for ln in lines:
        starts = bool(START_RX.match(ln))
        if ln.startswith("//") or ln.startswith("/*"):
            if cur and (cur[-1].startswith("//") or cur[-1] == ""):
                pass
            pending_comments.append(ln)
            continue
        if starts:
            flush()
            cur = pending_comments + [ln]
            pending_comments = []
        else:
            if pending_comments:
                cur.extend(pending_comments)
                pending_comments = []
            cur.append(ln)
    if pending_comments:
        cur.extend(pending_comments)
    flush()
    out = []
    for c in chunks:
        first = next((l for l in c.split("\n") if l and not l.startswith("//") and not l.startswith("/*")), "")
        m = DEF_RX.match(first)
        out.append({"name": (m.group(1) or m.group(2)) if m else None, "text": c, "first": first})
    return out


def idents(text: str) -> set:
    # 去掉字符串里的内容太复杂；按词边界粗匹配，宁多勿少（多导入一个名字无害）
    return set(re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)", text))


def main(argv):
    dry = "--dry" in argv
    argv = [a for a in argv if a != "--dry"]
    src_path = argv[0]
    targets = {}
    for spec in argv[1:]:
        fname, roots = spec.split("=", 1)
        targets[fname] = set(roots.split(","))
    raw = open(src_path, "rb").read().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")
    chunks = split_chunks(text)
    imports = [c for c in chunks if c["first"].startswith("import ")]
    body = [c for c in chunks if not c["first"].startswith("import ")]
    names = {c["name"]: i for i, c in enumerate(body) if c["name"]}
    owner = {}
    for f, roots in targets.items():
        for r in roots:
            if r not in names:
                raise SystemExit(f"找不到入口 {r}")
            owner[names[r]] = f
    uses = [idents(c["text"]) for c in body]
    # 迭代：未分配的命名块，如果「引用它的块」都属于同一个新文件（且原文件里没有未分配块引用它），就搬过去
    changed = True
    while changed:
        changed = False
        for i, c in enumerate(body):
            if i in owner or not c["name"]:
                continue
            users = {owner.get(j, "__orig__") for j, u in enumerate(uses) if j != i and c["name"] in u}
            if len(users) == 1 and "__orig__" not in users:
                owner[i] = users.pop()
                changed = True
    files = {f: [i for i in range(len(body)) if owner.get(i) == f] for f in targets}
    orig_idx = [i for i in range(len(body)) if i not in owner]
    where = {c["name"]: owner.get(i, "__orig__") for i, c in enumerate(body) if c["name"]}
    base = os.path.basename(src_path)
    d = os.path.dirname(src_path)
    exported_in_orig = {c["name"] for c in body if c["name"] and c["first"].startswith("export")}

    def need_export(i):
        n = body[i]["name"]
        if not n:
            return False
        my = owner.get(i, "__orig__")
        return any(c["name"] != n and owner.get(j, "__orig__") != my and n in uses[j] for j, c in enumerate(body)) or n in exported_in_orig

    def with_export(i):
        c = body[i]
        if c["first"].startswith("export") or not need_export(i):
            return c["text"]
        return c["text"].replace(c["first"], "export " + c["first"], 1)

    def imports_for(module, idxs):
        used = set().union(*(uses[i] for i in idxs)) if idxs else set()
        by_file = {}
        for n in sorted(used):
            w = where.get(n)
            if w and w != module:
                by_file.setdefault(base if w == "__orig__" else w, []).append(n)
        return [f"import {{ {', '.join(v)} }} from './{f}';" for f, v in sorted(by_file.items())]

    warnings = []
    if dry:
        for f, idxs in files.items():
            print(f, [body[i]["name"] or body[i]["first"][:30] for i in idxs])
        print("留在原文件的命名块:", [body[i]["name"] for i in orig_idx if body[i]["name"]])
        return
    for f, idxs in files.items():
        for i in idxs:
            c = body[i]
            if not c["first"].startswith(("function", "async function", "export function", "export async function")):
                ext = {n for n in uses[i] if where.get(n) not in (None, f) and n != c["name"]}
                if ext:
                    warnings.append(f"{f}: 顶层块「{c['first'][:60]}」用到别处的 {sorted(ext)[:6]}")
        hdr = f"// {f}：从 {base} 拆出（ADR 0015，按页面拆模块；scripts/split_module.py 生成，函数体未改）\n"
        out = hdr + "\n".join(c["text"] for c in imports) + "\n" + "\n".join(imports_for(f, idxs)) + "\n\n" + "\n".join(with_export(i) for i in idxs) + "\n"
        open(os.path.join(d, f), "wb").write(out.replace("\n", nl).encode("utf-8"))
    moved_exports = {}
    for f, idxs in files.items():
        names_out = [body[i]["name"] for i in idxs if body[i]["name"] and (body[i]["name"] in exported_in_orig or any(body[i]["name"] in uses[j] for j in orig_idx))]
        if names_out:
            moved_exports[f] = names_out
    orig_imports = imports_for("__orig__", orig_idx)
    re_exports = [f"export {{ {', '.join(v)} }} from './{f}';" for f, v in sorted(moved_exports.items())]
    # 原文件里还要用到的搬走符号：上面的 imports_for 已经从新文件导入；对外再导出（re_exports）
    new_orig = "\n".join(c["text"] for c in imports) + "\n" + "\n".join(orig_imports) + "\n" + "\n".join(re_exports) + "\n\n" + "\n".join(with_export(i) for i in orig_idx) + "\n"
    open(src_path, "wb").write(new_orig.replace("\n", nl).encode("utf-8"))
    for f, idxs in files.items():
        print(f"{f}: {len(idxs)} 块 {sum(len(body[i]['text']) for i in idxs) // 1024}KB")
    print(f"{base} 剩 {len(orig_idx)} 块 {sum(len(body[i]['text']) for i in orig_idx) // 1024}KB")
    for w in warnings:
        print("警告", w)


if __name__ == "__main__":
    main(sys.argv[1:])
