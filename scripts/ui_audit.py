"""界面体检：用真实浏览器打开工作台各视图，检查 文字对比度 / 键盘焦点 / 横向溢出 / 控制台报错。

为什么要有它：静态测试只能看 tokens.css 里的颜色对不对，看不到"半透明把文字压淡""表格把页面撑出横向滚动"
这类只有渲染后才出现的问题（ADR 0007 里 opacity 状态的 1.6:1 就是这样发现的）。改完界面跑一次，问题直接列出来。

用法（在仓库根或任一 worktree 根目录）：
    python scripts/ui_audit.py                       # 默认 3 主题 × 全部视图 × 1440 宽
    python scripts/ui_audit.py --themes light --views overview,compose --widths 1024,1440
    python scripts/ui_audit.py --json > audit.json   # 给程序读
退出码：0 = 全部通过；1 = 有问题；2 = 环境不满足（没装 playwright / 服务没起）。

只读：体检拦截一切非 GET 请求，不会改动正在使用的 console 数据。
原理：页面仍从运行中的服务（默认 http://127.0.0.1:8765）加载数据接口（/api/，只放行 GET），但入口页（/、/hub）
和 /v2/ 下的前端文件一律换成"当前目录下 web/"里的版本（缺文件直接 404，不回落线上）——所以在 worktree 里跑，审的就是这个分支的改动，不用重启服务。
范围：对比度只看直接含文字的元素；输入框 placeholder、图标、背景图上的文字不在扫描范围内。
依赖：pip install playwright && python -m playwright install chromium（可选依赖，不装不影响其他测试）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

VIEWS = ["overview", "running", "decisions", "artifacts", "compose", "peers", "settings",
         # Arena 五个视图已搬到插件页面 /plugins/arena/web/index.html（ADR 0014），这里只体检核心页面
         "hub#inbox", "hub#feedback", "hub#plugins", "hub#events"]  # hub#x = 扩展与来源页的各分页
THEMES = ["light", "dark", "sand"]
TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}

# 在页面里执行：逐个"直接含文字"的元素，取计算后前景色，向上叠加背景直到不透明，再乘上祖先 opacity。
# color-mix() 算出的颜色是 color(srgb r g b)（0–1 小数），必须单独换算，否则会误报 1:1。
CONTRAST_JS = r"""() => {
  const parse = c => { const m = c.match(/[\d.]+/g); if (!m) return null; let [r, g, b, a = 1] = m.map(Number);
    if (/^color\(srgb/.test(c)) { r *= 255; g *= 255; b *= 255; } return { r, g, b, a }; };
  const lum = ({ r, g, b }) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const blend = (fg, bg) => ({ r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1 });
  const bgOf = el => { const st = []; for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor);
      if (c && c.a > 0) { st.push(c); if (c.a >= 1) break; } }
    let bg = { r: 255, g: 255, b: 255, a: 1 }; for (let i = st.length - 1; i >= 0; i--) bg = blend(st[i], bg); return bg; };
  const label = el => el.tagName.toLowerCase() + [...el.classList].slice(0, 3).map(c => '.' + c).join('') +
    (el.parentElement ? ' < ' + el.parentElement.tagName.toLowerCase() + [...el.parentElement.classList].slice(0, 2).map(c => '.' + c).join('') : '');
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
    const rect = el.getBoundingClientRect(); if (!rect.width || !rect.height) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || +cs.opacity === 0) continue;
    if (el.closest('button:disabled,[aria-disabled=true],.sr-only')) continue;
    if (!/^(rgb|color\(srgb)/.test(cs.color)) { out.push({ kind: 'unparsed', el: label(el), color: cs.color.slice(0, 40) }); continue; }
    const bg = bgOf(el); let fg = blend(parse(cs.color), bg);
    let op = 1; for (let e = el; e; e = e.parentElement) op *= +getComputedStyle(e).opacity; if (op < 1) fg = blend({ ...fg, a: op }, bg);
    const x = lum(fg), y = lum(bg), ratio = (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
    const fs = parseFloat(cs.fontSize), large = fs >= 24 || (+cs.fontWeight >= 700 && fs >= 18.66), need = large ? 3 : 4.5;
    if (ratio < need) out.push({ kind: 'contrast', el: label(el), ratio: +ratio.toFixed(2), need, text: el.textContent.trim().slice(0, 20) });
  }
  return out; }"""

# 横向溢出：页面整体出现横向滚动，或某个非滚动容器里的子元素伸出了视口右缘。
OVERFLOW_JS = r"""() => {
  const out = [], vw = document.documentElement.clientWidth;
  if (document.documentElement.scrollWidth > vw + 1) out.push({ kind: 'overflow', el: 'document', px: document.documentElement.scrollWidth - vw });
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect(); if (!r.width || r.right <= vw + 1) continue;
    let clipped = false; for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const ps = getComputedStyle(p); if (ps.overflowX !== 'visible' || ps.position === 'fixed') { clipped = true; break; } }
    // 固定定位的层（如关闭时移到屏外的抽屉）不产生页面滚动，不算溢出
    if (clipped || getComputedStyle(el).position === 'fixed') continue;
    out.push({ kind: 'overflow', el: el.tagName.toLowerCase() + [...el.classList].slice(0, 2).map(c => '.' + c).join(''), px: Math.round(r.right - vw) });
  }
  return out.slice(0, 20); }"""


# 键盘焦点：真按 Tab，看当前焦点元素有没有可见指示（WCAG 2.4.7），描边颜色对外侧底色 ≥ 3:1（1.4.11 非文字对比度）。
# 用 box-shadow 做焦点环的只记"有"，不计算对比度。
FOCUS_JS = r"""() => {
  let el = document.activeElement; if (!el || el === document.body) return null;
  { const r0 = el.getBoundingClientRect(), lab = el.closest('label'); if ((r0.width <= 2 || r0.height <= 2) && lab) el = lab; }  // .sr-only 输入框：焦点指示画在外层 label 上（选择片、文件按钮）
  const parse = c => { const m = c.match(/[\d.]+/g); if (!m) return null; let [r, g, b, a = 1] = m.map(Number); if (/^color\(srgb/.test(c)) { r *= 255; g *= 255; b *= 255; } return { r, g, b, a }; };
  const lum = ({ r, g, b }) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const blend = (fg, bg) => ({ r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1 });
  const bgOf = e0 => { const st = []; for (let e = e0; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { st.push(c); if (c.a >= 1) break; } } let bg = { r: 255, g: 255, b: 255, a: 1 }; for (let i = st.length - 1; i >= 0; i--) bg = blend(st[i], bg); return bg; };
  const ratio = (x, y) => { const a = lum(x), b = lum(y); return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); };
  const cs = getComputedStyle(el), label = el.tagName.toLowerCase() + [...el.classList].slice(0, 2).map(c => '.' + c).join('') + (el.dataset.action ? `[${el.dataset.action}]` : '');
  const text = (el.getAttribute('aria-label') || el.textContent || el.value || '').trim().slice(0, 16);
  const outer = bgOf(el.parentElement || el);
  // 环的外沿：描边 = 宽度 + 偏移；阴影 = 扩展半径。再看有没有被 overflow 非 visible 的祖先整条边裁掉（盲区 13b7）
  const clippedBy = w => { if (w <= 0 || cs.position === 'fixed') return '';  /* fixed 元素的包含块是视口，不受祖先 overflow 裁剪（fable2 a180） */ const r = el.getBoundingClientRect(); const ring = { l: r.left - w, t: r.top - w, r: r.right + w, b: r.bottom + w };
    for (let a = el.parentElement; a && a !== document.documentElement; a = a.parentElement) { const s = getComputedStyle(a);
      if (s.overflowX === 'visible' && s.overflowY === 'visible') continue; const q = a.getBoundingClientRect(); const bl = parseFloat(s.borderLeftWidth) || 0, bt = parseFloat(s.borderTopWidth) || 0;
      const box = { l: q.left + bl, t: q.top + bt, r: q.left + bl + a.clientWidth, b: q.top + bt + a.clientHeight };
      const cut = []; if (s.overflowX !== 'visible') { if (ring.l < box.l - 0.5 && r.left - box.l < 0.5) cut.push('左'); if (ring.r > box.r + 0.5 && box.r - r.right < 0.5) cut.push('右'); }
      if (s.overflowY !== 'visible') { if (ring.t < box.t - 0.5 && r.top - box.t < 0.5) cut.push('上'); if (ring.b > box.b + 0.5 && box.b - r.bottom < 0.5) cut.push('下'); }
      if (cut.length) return cut.join('') + ' @ ' + a.tagName.toLowerCase() + [...a.classList].slice(0, 2).map(c => '.' + c).join(''); }
    return ''; };
  { const rr = el.getBoundingClientRect(); if (rr.width <= 2 || rr.height <= 2) return { label, text, how: 'tiny' }; }  // 有焦点却只有 1px（如 .sr-only 没写聚焦显示）
  if (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 1) { const oc = parse(cs.outlineColor); const r = oc ? ratio(blend(oc, outer), outer) : 0;
    const w = parseFloat(cs.outlineWidth) + (parseFloat(cs.outlineOffset) || 0);  /* 负偏移=环内收，伸出量 ≤0 就不会被裁 */ return { label, text, how: 'outline', ratio: +r.toFixed(2), clipped: clippedBy(w) }; }
  if (cs.boxShadow && cs.boxShadow !== 'none') {
    // 计算样式形如 "rgb(40, 102, 75) 0px 0px 0px 3px, rgba(0,0,0,.1) 0px 1px 2px 0px"：取扩展半径 > 0 的环形阴影里对比最好的那段
    const rings = [...cs.boxShadow.matchAll(/((?:rgba?|color)\([^)]*\))\s+(-?[\d.]+)px\s+(-?[\d.]+)px\s+([\d.]+)px\s+(-?[\d.]+)px(\s+inset)?/g)]
      .filter(m => parseFloat(m[5]) > 0 && !m[6]).map(m => ({ c: parse(m[1]), spread: parseFloat(m[5]) })).filter(x => x.c && x.c.a > 0);
    if (!rings.length) return { label, text, how: 'shadow-soft', shadow: cs.boxShadow.slice(0, 60) };
    const best = rings.map(x => ({ ...x, ratio: ratio(blend(x.c, outer), outer) })).sort((a, b) => b.ratio - a.ratio)[0];
    return { label, text, how: 'shadow', ratio: +best.ratio.toFixed(2), clipped: clippedBy(best.spread) }; }
  return { label, text, how: 'none' };
}"""


TIP_OK_JS = r"""el => { const r = el.getBoundingClientRect(), vw = document.documentElement.clientWidth;
  const vis = el.checkVisibility ? el.checkVisibility({ visibilityProperty: true, opacityProperty: true }) : true;
  return !!((el.getAttribute('data-tip') || '').trim() && r.width && r.height && r.right > 0 && r.left < vw && vis); }"""
TIP_JS = r"""el => {
  const t = document.getElementById('wbTip'), vw = document.documentElement.clientWidth, vh = innerHeight, text = el.getAttribute('data-tip') || '';
  const name = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + ' 「' + text.slice(0, 24) + '」';
  if (!t || !t.classList.contains('on') || t.textContent !== text || getComputedStyle(t).display === 'none') return { kind: 'tip', el: name, how: 'none' };
  // 淡入动画直接播到终点再量不透明度：动画被改坏（终点仍透明）时能报出来，又不用每次等 350ms
  try { t.getAnimations().forEach(x => x.finish()); } catch (e) {}
  const op = +getComputedStyle(t).opacity;
  if (op < 0.9) return { kind: 'tip', el: name, how: 'faint', px: Math.round(op * 100) / 100 };
  const r = t.getBoundingClientRect(), off = Math.max(-r.left, -r.top, r.right - vw, r.bottom - vh);
  return off > 0.5 ? { kind: 'tip', el: name, how: 'out', px: Math.round(off) } : { ok: true };
}"""


TIP_STATS = {"total": 0, "checked": 0, "skipped": 0}   # 全程累计，结尾打印：「检查 12 个」和「40 个全过」不能长得一样（opus1 4e22）


def scan_tips(pg, limit: int = 40) -> list:
    """逐个真实悬停带 data-tip 的元素：说明必须显示出来、淡入后看得清、整块落在视口内。
    直接拿元素句柄，不按下标：页面每 2 秒左右自刷新，下标在两次查找之间可能换了人（opus1 fba7）。
    被遮住、悬停不上、或者期间被重绘换掉的跳过，不算问题。"""
    out = []
    try:
        pg.evaluate("() => document.activeElement && document.activeElement.blur && document.activeElement.blur()")
        handles = [h for h in pg.query_selector_all("[data-tip]") if h.evaluate(TIP_OK_JS)][:limit]
    except Exception:  # noqa: BLE001
        return out
    checked = 0
    for h in handles:
        try:
            pg.mouse.move(0, 0)       # 每次从元素外移进来，和真人一样
            # 滚到视口中间后等两帧再悬停：滚动事件在下一帧的 rAF 之前派发完，等两帧就确定落地了。
            # 以前固定等 80ms，页面忙时滚动事件晚到，会把刚显示的说明收掉（说明层遇到滚动会收起）
            # behavior:'instant'：不受页面 CSS scroll-behavior 影响（平滑滚动时悬停期间页面还在动）；300ms 上界：页面被节流、rAF 不回调时不挂住
            h.evaluate("el => Promise.race([new Promise(r => { el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' }); requestAnimationFrame(() => requestAnimationFrame(r)); }), new Promise(r => setTimeout(r, 300))])")
            h.hover(timeout=1500)
            r = h.evaluate(TIP_JS)
        except Exception:  # noqa: BLE001 - 被遮住 / 已被重绘换掉
            continue
        checked += 1
        if r and not r.get("ok"):
            out.append(r)
    TIP_STATS["total"] += len(handles); TIP_STATS["checked"] += checked; TIP_STATS["skipped"] += len(handles) - checked
    if handles and not checked:   # 一个都没悬停上：不能当成「全过」
        out.append({"kind": "tip", "el": f"（本页 {len(handles)} 个全部跳过）", "how": "none-checked"})
    return out


def console_text(m) -> str:
    """控制台报错的文字。「加载资源失败」只说状态码不说是谁：从报错位置取出网址路径，
    写成「加载资源失败 400 /api/x」——状态码和网址放最前面，报告按长度截断时也看得到。"""
    t = m.text[:200]
    try:
        u = (m.location or {}).get("url") or ""
    except Exception:  # noqa: BLE001
        u = ""
    if t.startswith("Failed to load resource") and u:
        code = re.search(r"status of (\d+)", t)
        s = urllib.parse.urlsplit(u)
        where = (s.path or "/") + ("?" + s.query if s.query else "") if s.scheme in ("http", "https") else u[:60]
        t = f"加载资源失败 {code.group(1) if code else '?'} {where[:160]}"
    return t


def page_items(pg, a, theme: str, errs: list) -> list:
    """一页的全部检查结果。每项一行、注释单独成行：以前写在一行里，行尾注释把 console 报错那一项吞掉了（opus1 fba7）。"""
    items = pg.evaluate(CONTRAST_JS)
    items += pg.evaluate(OVERFLOW_JS)
    items += pg.evaluate(HIDDEN_FOCUS_JS)
    if a.tabs:
        items += scan_focus(pg, a.tabs)
    # 说明层位置与主题无关：只在第一个主题上悬停，省掉 2/3 时间
    if a.tips and theme == a.themes.split(",")[0]:
        items += scan_tips(pg, a.tips)
    items += [{"kind": "console", "el": e} for e in errs]
    return items


# 对读屏隐藏（aria-hidden="true"）的区域里，还能被键盘聚焦的元素：Tab 会走到一个读屏不念、往往也看不见的地方。
# 静态检查，不靠按 Tab 碰运气——关着的抽屉在页面最后，数据多时 30 次 Tab 到不了，数据少时才报（2026-09-29 抽屉 bug 就是这样时有时无）。
HIDDEN_FOCUS_JS = r"""() => {
  const sel = 'a[href], button, input, select, textarea, summary, [tabindex], [contenteditable=""], [contenteditable="true"]';
  const out = [], seen = new Set();
  for (const host of document.querySelectorAll('[aria-hidden="true"]')) {
    if (host.closest('[inert]')) continue;
    // host 自己可聚焦也算（<button aria-hidden="true">）；嵌套的 aria-hidden 用 seen 去重
    for (const el of [...(host.matches(sel) ? [host] : []), ...host.querySelectorAll(sel)]) {
      if (seen.has(el)) continue; seen.add(el);
      if (el.disabled || el.tabIndex < 0 || el.closest('[inert]')) continue;
      // display:none / visibility:hidden 本来就聚焦不到；浏览器不支持 checkVisibility 时跳过——宁漏不误报（opus2 09e8）
      if (!el.checkVisibility || !el.checkVisibility({ visibilityProperty: true })) continue;
      const name = el.tagName.toLowerCase() + (el.dataset.action ? '[' + el.dataset.action + ']' : '') + (el.id ? '#' + el.id : '');
      out.push({ kind: 'hidden-focus', el: name, text: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 30) });
    }
  }
  return out;
}"""


def scan_focus(pg, presses: int = 30, seen: list | None = None) -> list:
    out = []
    for _ in range(presses):
        pg.keyboard.press("Tab")
        r = pg.evaluate(FOCUS_JS)
        if r and seen is not None:
            seen.append(r)
        # none：没有可见焦点；shadow-soft：只有无扩展半径的柔和阴影（算不上环）；outline/shadow 对比 < 3；环被 overflow 祖先整边裁掉
        if r and (r["how"] in ("none", "shadow-soft", "tiny") or (r["how"] in ("outline", "shadow") and r["ratio"] < 3)):
            out.append({"kind": "focus", "el": r["label"], "how": r["how"], "ratio": r.get("ratio"), "need": 3, "text": r["text"]})
        elif r and r.get("clipped"):
            out.append({"kind": "focus", "el": r["label"], "how": "clipped", "ratio": r.get("ratio"), "need": 3, "text": r["text"], "clipped": r["clipped"]})
    return out


def make_router(web: str, host: str, blocked: list):
    """Playwright 路由：前端一律取 web 目录（入口页也是，否则审的是线上 main 的 index.html——fable2 ca3a），
    其余请求（/api/ 数据接口等）交给线上服务，且只放行 GET。"""
    def serve(route):
        req = route.request
        path = req.url.split(host, 1)[-1].split("?")[0].split("#")[0] or "/"
        if req.method != "GET":  # 只读保证：写数据、标记已读等一律拦掉
            blocked.append(req.method + " " + path[:60])
            return route.abort()
        rel = {"/": "v2/index.html", "/index.html": "v2/index.html", "/hub": "v2/hub.html", "/hub.html": "v2/hub.html"}.get(path)
        if rel is None and path.startswith("/v2/"):
            rel = path.lstrip("/")
        if rel is None:
            return route.continue_()
        f = os.path.join(web, rel.replace("/", os.sep))
        if not os.path.isfile(f):
            return route.fulfill(status=404, body=f"ui_audit: {rel} 不在 {web}")  # 不回落到线上版本，避免审错对象
        with open(f, "rb") as fh:
            route.fulfill(status=200, body=fh.read(), headers={"content-type": TYPES.get(os.path.splitext(f)[1], "application/octet-stream"), "cache-control": "no-store"})
    return serve


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="界面体检：对比度 / 键盘焦点 / 隐藏区域可聚焦 / 横向溢出 / 悬停说明 / 控制台报错")
    ap.add_argument("--base", default=os.environ.get("WB_URL", "http://127.0.0.1:8765"), help="运行中的工作台服务地址")
    ap.add_argument("--web", default="web", help="用这个目录下的前端文件替换页面资源（默认当前目录的 web/）")
    ap.add_argument("--themes", default=",".join(THEMES))
    ap.add_argument("--views", default=",".join(VIEWS))
    ap.add_argument("--widths", default="1440", help="视口宽度，逗号分隔，如 1024,1440")
    ap.add_argument("--wait", type=int, default=4000, help="每页等待毫秒（等入场动画结束）")
    ap.add_argument("--tabs", type=int, default=30, help="每页按几次 Tab 检查键盘焦点（0 = 不查）")
    ap.add_argument("--project", default=None, help="检查哪个项目的数据（默认：决策最多的项目——全新浏览器自己挑的默认项目可能是空的，覆盖面会悄悄缩水）")
    ap.add_argument("--tips", type=int, default=40, help="每页真实悬停几个带 data-tip 的元素，检查说明是否显示、是否超出视口（0 = 不查）")
    ap.add_argument("--accent", default="", help="模拟用户自定义强调色（如 #ffdd00），检查低对比自定义色下焦点是否仍可见")
    ap.add_argument("--font-scale", type=float, default=1, help="模拟「设置 → 字号」（0.85–1.3，界面档位 0.9 / 1 / 1.15），检查放大字号后是否撑出横向溢出")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("未安装 playwright：pip install playwright && python -m playwright install chromium", file=sys.stderr)
        return 2
    try:
        urllib.request.urlopen(a.base + "/", timeout=5).read(1)
    except Exception as e:  # noqa: BLE001 - 只为给出清楚的提示
        print(f"连不上工作台服务 {a.base}：{e}（先启动服务，或用 --base 指定）", file=sys.stderr)
        return 2
    if a.project is None:
        try:
            st = json.load(urllib.request.urlopen(a.base + "/api/state", timeout=10))
            a.project = max(st.get("projects") or [], key=lambda p: len(p.get("decisions") or []), default={}).get("slug", "")
        except Exception as e:  # noqa: BLE001
            a.project = ""
            print(f"取项目列表失败，退回页面自己的默认项目（覆盖面可能缩水）：{e}", file=sys.stderr)
    if a.project:
        print(f"检查项目：{a.project}", file=sys.stderr)
    web = os.path.abspath(a.web)
    host = a.base.split("://", 1)[-1]
    blocked: list[str] = []

    serve = make_router(web, host, blocked)

    # 聚合：同一主题下同一元素类型只报一次，记最差比值、出现次数和所在视图。
    found: dict[tuple, dict] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for theme in a.themes.split(","):
            for width in (int(w) for w in a.widths.split(",")):
                for view in a.views.split(","):
                    pg = browser.new_page(viewport={"width": width, "height": 900})
                    errs: list[str] = []
                    pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
                    pg.on("console", lambda m: errs.append(console_text(m)) if m.type == "error" else None)
                    pg.route("**/*", serve)
                    pg.add_init_script(f"localStorage.setItem('wb.prefs', JSON.stringify({{theme:{{mode:'{theme}',accent:'{a.accent}',fontScale:{a.font_scale:g}}}}}))")
                    pg.goto(f"{a.base}/hub#{view[4:]}" if view.startswith("hub#") else (f"{a.base}/?view={view}" + (f"&p={urllib.parse.quote(a.project)}" if a.project else "")))
                    pg.wait_for_timeout(a.wait)
                    items = page_items(pg, a, theme, errs)
                    # 项目名写错时页面会悄悄换回默认项目：回读实际生效的项目，对不上就报，别让「检查项目：X」骗人
                    if a.project and not view.startswith("hub#"):
                        actual = pg.evaluate("() => (document.getElementById('projectSelect') || {}).value || ''")
                        if actual != a.project:
                            items.append({"kind": "project", "el": f"要求检查 {a.project}，页面实际是 {actual or '（空）'}"})
                    for it in items:
                        key = (theme, width, it["kind"], it["el"])
                        agg = found.setdefault(key, {**it, "theme": theme, "width": width, "n": 0, "views": []})
                        agg["n"] += 1
                        if view not in agg["views"]:
                            agg["views"].append(view)
                        if it.get("ratio", 99) < agg.get("ratio", 99):
                            agg["ratio"], agg["text"] = it["ratio"], it.get("text", "")
                    pg.close()
        browser.close()

    if a.tips and not TIP_STATS["total"]:
        print("悬停说明：没有找到任何带 data-tip 的元素（选择器变了或页面没加载出来？）", file=sys.stderr)
    elif a.tips:
        print(f"悬停说明：检查 {TIP_STATS['checked']} / 共 {TIP_STATS['total']} 个，跳过 {TIP_STATS['skipped']}（被遮住或已被重绘替换）", file=sys.stderr)
    rows = sorted(found.values(), key=lambda r: (r["theme"], r["width"], r["kind"], r.get("ratio", 0)))
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        for theme in a.themes.split(","):
            for width in a.widths.split(","):
                sub = [r for r in rows if r["theme"] == theme and r["width"] == int(width)]
                print(f"== {theme} @ {width}px：{len(sub)} 项")
                for r in sub:
                    detail = {"contrast": f"{r.get('ratio')}:1 < {r.get('need')}  「{r.get('text', '')}」",
                              "overflow": f"超出 {r.get('px')}px", "unparsed": f"颜色无法解析 {r.get('color')}",
                              "focus": ({"none": "焦点不可见", "tiny": "焦点元素只有 1px", "shadow-soft": "只有柔和阴影、不成环", "clipped": f"焦点环被裁（{r.get('clipped')}）"}.get(r.get("how")) or f"焦点环 {r.get('ratio')}:1 < 3") + f"  「{r.get('text', '')}」",
                              "tip": {"none": "悬停后说明没有显示", "none-checked": "悬停检查一个都没做成（全被遮住或被重绘替换）", "faint": f"悬停说明淡入后仍看不清（不透明度 {r.get('px')}）"}.get(r.get("how")) or f"悬停说明超出视口 {r.get('px')}px",
                              "project": "项目没切过去，这一页查的不是你要的项目",
                              "hidden-focus": f"对读屏隐藏的区域里仍能被键盘聚焦  「{r.get('text', '')}」",
                              "console": ""}[r["kind"]]
                    print(f"  [{r['kind']}] {r['el'][:70]} {detail} ×{r['n']} {r['views'][:3]}")
        if blocked:
            print(f"（已拦截 {len(blocked)} 个写请求，保证只读：{sorted(set(blocked))[:3]}）")
        print("通过" if not rows else f"共 {len(rows)} 项需要处理")
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
