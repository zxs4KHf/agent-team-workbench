"""scripts/ui_audit.py 的自测：一个"永远报 0"的检查器和正常工作的看起来一模一样，所以用固定页面证明它真能抓到问题、也不乱报。
不需要工作台服务；没装 playwright 时跳过（退出 0 并说明）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP 未安装 playwright")
    sys.exit(0)
import ui_audit  # noqa: E402

PAGE = """<!doctype html><html><body style="margin:0;background:#fff;font:14px sans-serif">
<p id="ok" style="color:#222">正常文字</p>
<p style="color:#bbb">低对比文字</p>
<p style="color:color-mix(in srgb, #222 30%, #fff)">混色低对比</p>
<p style="color:color-mix(in srgb, #222 90%, #fff)">混色正常</p>
<div style="opacity:.4"><span style="color:#222">半透明压淡</span></div>
<div style="background:#1b1f23"><span style="color:#eee">深底浅字</span></div>
<button disabled style="color:#ccc">禁用按钮不算</button>
<div style="width:2000px;height:10px">撑出页面</div>
<div style="overflow-x:auto;width:300px"><div style="width:900px">滚动容器里不算</div></div>
<aside style="position:fixed;top:0;left:100%;width:500px"><h2 style="color:#222">关闭的抽屉不算</h2></aside>
</body></html>"""

FOCUS_PAGE = """<!doctype html><html><body style="background:#fff">
<style>#g:focus-visible{outline:2px solid #28664b}#w:focus-visible{outline:2px solid rgba(40,102,75,.3)}#n:focus-visible{outline:none}#s:focus-visible{outline:none;box-shadow:0 0 0 3px #28664b}
#sw:focus-visible{outline:none;box-shadow:0 0 0 3px rgba(40,102,75,.25)}#sf:focus-visible{outline:none;box-shadow:0 1px 3px rgba(0,0,0,.3)}
.clip{overflow:hidden;width:90px}.clip button{display:block;width:90px}.clip button:focus-visible{outline:2px solid #28664b;outline-offset:2px}.clip .in:focus-visible{outline-offset:-2px}
.t{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}.lab{display:inline-block;padding:4px}.lab.ok:has(input:focus-visible){outline:2px solid #28664b}</style>
<button id="g">好</button><button id="w">淡</button><button id="n">无</button><button id="s">阴影</button>
<button id="sw">淡阴影</button><button id="sf">柔阴影</button>
<div class="clip"><button>裁</button></div><div class="clip"><button class="in">内收</button></div><a class="t" href="#x">极小</a><label class="lab ok">有环片<input type="checkbox" class="t"></label><label class="lab">无环片<input type="checkbox" class="t"></label><div class="clip"><button style="position:fixed;left:300px;top:300px">固定</button></div></body></html>"""

fails = 0
def ok(cond, msg):
    global fails
    print(("PASS  " if cond else "FAIL  ") + msg)
    fails += not cond

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1024, "height": 700})
    pg.set_content(PAGE)
    c = pg.evaluate(ui_audit.CONTRAST_JS)
    o = pg.evaluate(ui_audit.OVERFLOW_JS)
    # 入口页必须来自被审的 web 目录（fable2 ca3a）：临时目录放一个带唯一标记的 index.html，用不存在的域名证明没走线上
    import tempfile
    tmp = tempfile.mkdtemp(); os.makedirs(os.path.join(tmp, "v2"))
    with open(os.path.join(tmp, "v2", "index.html"), "w", encoding="utf-8") as fh:
        fh.write('<!doctype html><body><p id="mark">AUDIT-MARK-7f3</p><script src="/v2/x.js"></script></body>')
    with open(os.path.join(tmp, "v2", "x.js"), "w", encoding="utf-8") as fh:
        fh.write("document.body.dataset.js = 'local';")
    blocked = []
    pg2 = b.new_page(); pg2.route("**/*", ui_audit.make_router(tmp, "audit.invalid", blocked))
    pg2.goto("http://audit.invalid/?view=overview"); pg2.wait_for_timeout(200)
    mark = pg2.inner_text("#mark"); js = pg2.evaluate("document.body.dataset.js")
    pg2.evaluate("fetch('/api/decisions', { method: 'POST', body: '{}' }).catch(() => 0)"); pg2.wait_for_timeout(300)
    miss = pg2.goto("http://audit.invalid/hub").status
    # 键盘焦点：好的实色描边不报；淡描边、无指示要报；box-shadow 焦点环算"有"
    pg3 = b.new_page(); pg3.set_content(FOCUS_PAGE); focus = ui_audit.scan_focus(pg3, 12)
    # 真实前端 + 低对比自定义强调色（fable2 a7ad）：按 Tab 时焦点环仍须 ≥3:1。无服务：用 audit.invalid，只有壳层可聚焦，足够验证焦点
    web = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
    pg4 = b.new_page(); pg4.route("**/*", ui_audit.make_router(web, "audit.invalid", []))
    pg4.add_init_script("localStorage.setItem('wb.prefs', JSON.stringify({theme:{mode:'light',accent:'#ffdd00'}}))")
    pg4.goto("http://audit.invalid/?view=settings"); pg4.wait_for_timeout(1500)
    seen = []; weak = ui_audit.scan_focus(pg4, 12, seen)
    pg5 = b.new_page(); pg5.route("**/*", ui_audit.make_router(web, "audit.invalid", []))
    pg5.add_init_script("localStorage.setItem('wb.prefs', JSON.stringify({theme:{mode:'light',accent:'#ffdd00'}}))")
    pg5.goto("http://audit.invalid/hub#plugins"); pg5.wait_for_timeout(1500)
    seen_hub = []; weak_hub = ui_audit.scan_focus(pg5, 8, seen_hub)
    # fable2 13b7：#8c8c8c 对 bg 3.07 / surface 3.36 但在 hub 筛选栏的 surface-2 上 2.95
    pg6 = b.new_page(); pg6.route("**/*", ui_audit.make_router(web, "audit.invalid", []))
    pg6.add_init_script("localStorage.setItem('wb.prefs', JSON.stringify({theme:{mode:'light',accent:'#8c8c8c'}}))")
    pg6.goto("http://audit.invalid/hub#inbox"); pg6.wait_for_timeout(1500)
    seen_grey = []; weak_grey = ui_audit.scan_focus(pg6, 30, seen_grey)
    b.close()

ok(len([x for x in seen if x["how"] == "outline"]) >= 3 and not weak, f"自定义 #ffdd00 下真实前端按 Tab：焦点环都 ≥3:1（看到 {len(seen)} 个焦点，最低 {min([x['ratio'] for x in seen if x['how'] == 'outline'] or [0])}；不达标 {weak[:2]}）")
ok(len([x for x in seen_hub if x["how"] == "outline"]) >= 3 and not weak_hub, f"自定义 #ffdd00 下 hub 页按 Tab：焦点环都 ≥3:1（hub 也走 prefs.apply；看到 {len(seen_hub)} 个，不达标 {weak_hub[:2]}）")
ok(len(seen_grey) >= 5 and not weak_grey, f"自定义 #8c8c8c 下 hub#inbox 按 30 次 Tab：焦点环都 ≥3:1（含 surface-2 上的筛选按钮；看到 {len(seen_grey)} 个，不达标 {weak_grey[:2]}）")
ok(len(ui_audit.VIEWS) == 11 and sum(v.startswith("hub#") for v in ui_audit.VIEWS) == 4, f"默认体检 11 个视图（7 个核心页 + hub 四个分页；Arena 五个视图随插件搬走，ADR 0014），现在 {len(ui_audit.VIEWS)}")
_r = []; _rt = ui_audit.make_router(web, "x.invalid", _r)
ok('"/hub.html": "v2/hub.html"' in open(ui_audit.__file__, encoding="utf-8").read() and 'f"{a.base}/hub#{view[4:]}"' in open(ui_audit.__file__, encoding="utf-8").read(), "hub 路由（/hub、/hub.html）与 hub#x 导航都在")
ftexts = {x["text"]: x for x in focus}
ok("淡" in ftexts and ftexts["淡"]["how"] == "outline" and ftexts["淡"]["ratio"] < 3, f"抓到太淡的焦点描边（{ftexts.get('淡')}）")
ok("无" in ftexts and ftexts["无"]["how"] == "none", "抓到完全没有焦点指示的按钮")
ok("好" not in ftexts and "阴影" not in ftexts, "实色描边与 box-shadow 焦点环不误报")
ok(ftexts.get("淡阴影", {}).get("how") == "shadow" and ftexts["淡阴影"]["ratio"] < 3, f"box-shadow 焦点环也算对比度，太淡要报（{ftexts.get('淡阴影')}）")
ok(ftexts.get("柔阴影", {}).get("how") == "shadow-soft", "只有无扩展半径的柔和阴影不算焦点环")
ok(ftexts.get("裁", {}).get("how") == "clipped", f"焦点环被 overflow:hidden 祖先整边裁掉要报（{ftexts.get('裁')}）")
ok("内收" not in ftexts, "环内收（负 outline-offset）后不再误报被裁")
ok("固定" not in ftexts, "position:fixed 的元素不受祖先 overflow 裁剪，不误报")
_lab = {x["text"][:3]: x for x in focus if x["text"].startswith(("有环片", "无环片"))}
ok("有环片" not in _lab and _lab.get("无环片", {}).get("how") == "none", f"隐藏复选框看外层 label：label 有环不报、没环报「无」（{_lab}）")
ok(ftexts.get("极小", {}).get("how") == "tiny", "获得焦点却只有 1px 的元素要报（「跳到主内容」原来就是这样）")
ok(not [x for x in seen if x["how"] == "tiny"] and not [x for x in seen_hub if x["how"] == "tiny"], "真实前端两页的「跳到主内容」聚焦时看得见")
ok(mark == "AUDIT-MARK-7f3" and js == "local", "入口页与脚本都来自被审的 web 目录（不是线上 main）")
ok(any(x.startswith("POST /api/decisions") for x in blocked), f"写请求被拦截（{blocked}）")
ok(miss == 404, "被审目录缺文件时 404，不回落线上版本")

texts = {x.get("text") for x in c if x["kind"] == "contrast"}
ok("低对比文字" in texts, "抓到普通低对比文字（#bbb on #fff）")
ok("混色低对比" in texts, "抓到 color-mix() 产生的 color(srgb …) 低对比色（不能因解析不了而漏掉或误报 1:1）")
ok("半透明压淡" in texts, "抓到被祖先 opacity 压淡的文字（ADR 0007 的 1.6:1 就是这一类）")
ok(not texts & {"正常文字", "混色正常", "深底浅字"}, f"正常文字不误报（误报：{texts & {'正常文字', '混色正常', '深底浅字'}}）")
ok("禁用按钮不算" not in texts, "禁用控件豁免（WCAG 不要求）")
ok(not [x for x in c if x["kind"] == "unparsed"], "测试页里没有解析不了的颜色")
els = [x["el"] for x in o]
ok("document" in els, "抓到页面级横向溢出")
ok(not any("h2" in e for e in els), "关闭的固定定位抽屉不算溢出")
ok(len(o) >= 1 and not any(x.get("px", 0) < 0 for x in o), "溢出像素为正数")
print(f"\n{'全部通过' if not fails else f'{fails} 项失败'}")

# 悬停说明：贴顶 / 贴右 / 在 overflow:hidden 卡片里都不报；缺 tip.js 必须报「没显示」，说明层被推出屏幕必须报「超出」
TIP_PAGE = """<!doctype html><html><head><style>%s</style></head><body style="margin:0;background:#fff;font:14px sans-serif">
<button id="top" data-tip="顶边上的按钮，说明应翻到下方" style="position:absolute;left:2px;top:2px">顶</button>
<button id="right" data-tip="贴右边的一段比较长的说明文字，应该收回到视口里面来显示" style="position:absolute;right:2px;top:200px">右</button>
<div style="overflow:hidden;width:60px;height:40px;position:absolute;left:300px;top:300px"><button id="card" data-tip="卡片设了超出隐藏也不会裁掉">卡</button></div>
<button id="empty" data-tip="" style="position:absolute;left:500px;top:300px">空说明不算</button>
</body></html>"""
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 带上 tokens.css：否则 --text / --bg / --m-fast 都未定义，animation 整条作废，测的只是几何、不是真实渲染（opus1 fba7）
TIP_CSS = "".join(open(os.path.join(REPO, "web", "v2", "styles", f), encoding="utf-8").read() for f in ("tokens.css", "components.css"))
TIP_JS_SRC = open(os.path.join(REPO, "web", "v2", "js", "tip.js"), encoding="utf-8").read()
with sync_playwright() as p:
    b = p.chromium.launch()

    def tips(extra_css="", with_js=True):
        pg = b.new_page(viewport={"width": 800, "height": 600})
        pg.set_content(TIP_PAGE % (TIP_CSS + extra_css))
        if with_js:
            pg.add_script_tag(content=TIP_JS_SRC)
        r = ui_audit.scan_tips(pg, 10)
        side = pg.evaluate("() => document.getElementById('wbTip') && document.getElementById('wbTip').dataset.side") if with_js else None
        pg.close()
        return r, side
    ui_audit.TIP_STATS.update(total=0, checked=0, skipped=0)
    good, _ = tips()
    ok(good == [], f"悬停说明：贴顶 / 贴右 / 卡片内都完整显示在视口里（实际 {good}）")
    ok(ui_audit.TIP_STATS == {"total": 3, "checked": 3, "skipped": 0}, f"悬停检查计数：3 个都真的检查了（实际 {ui_audit.TIP_STATS}）")
    pg = b.new_page(viewport={"width": 800, "height": 600}); pg.set_content((TIP_PAGE % TIP_CSS).replace("</body>", '<div style="position:fixed;inset:0;z-index:99999"></div></body>'))
    pg.add_script_tag(content=TIP_JS_SRC); covered = ui_audit.scan_tips(pg, 10); pg.close()
    ok([x["how"] for x in covered] == ["none-checked"], f"全被遮住、一个都没悬停上时报出来，而不是 0 项（实际 {covered}）")
    pg = b.new_page(viewport={"width": 800, "height": 600}); pg.set_content(TIP_PAGE % TIP_CSS); pg.add_script_tag(content=TIP_JS_SRC)
    pg.hover("#top"); ok(pg.evaluate("() => document.getElementById('wbTip').dataset.side") == "below", "顶边按钮的说明翻到下方"); pg.close()
    pg = b.new_page(viewport={"width": 800, "height": 600})
    pg.set_content((TIP_PAGE % TIP_CSS).replace("</body>", '<div style="height:2000px"></div><button id="low" data-tip="滑到光标下的按钮" style="position:absolute;left:300px;top:900px">低</button></body>'))
    pg.add_script_tag(content=TIP_JS_SRC)
    pg.hover("#card"); pg.evaluate("() => scrollTo(0, 600)"); pg.wait_for_timeout(100)
    box = pg.locator("#low").bounding_box(); pg.mouse.move(box["x"] + 3, box["y"] + 3); pg.mouse.move(box["x"] + 5, box["y"] + 5)
    ok(pg.evaluate("() => { const t = document.getElementById('wbTip'); return t.classList.contains('on') && t.textContent }") == "滑到光标下的按钮", "滚动后光标下换了按钮，鼠标一动说明就补上")
    pg.close()
    def replaced(src):
        pg = b.new_page(viewport={"width": 800, "height": 600}); pg.set_content(TIP_PAGE % TIP_CSS); pg.add_script_tag(content=src)
        pg.hover("#card"); pg.evaluate("() => { const o = document.getElementById('card'); o.outerHTML = o.outerHTML; }"); pg.wait_for_timeout(120)
        r = pg.evaluate("() => { const t = document.getElementById('wbTip'); return !!t && t.classList.contains('on') && t.textContent === '卡片设了超出隐藏也不会裁掉' }")
        pg.keyboard.press("Escape"); pg.evaluate("() => { const o = document.getElementById('card'); o.outerHTML = o.outerHTML; }"); pg.wait_for_timeout(120)
        esc = pg.evaluate("() => !document.getElementById('wbTip').classList.contains('on')")
        pg.close(); return r, esc
    shown, esc_ok = replaced(TIP_JS_SRC)
    ok(shown, "光标下的元素被重绘替换（定时刷新），说明仍然显示")
    ok(esc_ok, "按 Esc 收起后，重绘不会把同一条说明又弹出来")
    # 注：这条是防退步的守卫，不是区分新旧的证据——Chrome 在元素被替换后会自己补发移入事件，第一版在这里也能过。
    # 区分证据来自真实页面：artifacts 页 1440px 悬停全部带说明的元素两轮，第一版丢 9 / 8 次，现版 0 / 0（见 CHANGELOG）。
    # 能区分新旧：屏蔽浏览器的移入 / 移动事件后替换光标下的元素，只有「按光标位置校正」能把说明补回来。
    # 反向对照两份：上一版（6d74230）和把 MutationObserver 关掉的现版——都必须在这里失败，证明这条测的就是校正路径本身。
    def blocked_replace(src):
        pg = b.new_page(viewport={"width": 800, "height": 600}); pg.set_content(TIP_PAGE % TIP_CSS); pg.add_script_tag(content=src)
        pg.hover("#card"); pg.wait_for_timeout(50)
        pg.evaluate("""() => { const stop = e => e.stopImmediatePropagation();
          ['pointerover', 'pointerenter', 'pointermove', 'mouseover', 'mousemove'].forEach(t => window.addEventListener(t, stop, true));
          // 像定时刷新那样换成一个新节点：说明文字变了、不带旧节点上的 aria-describedby（否则什么都不做也能「通过」）
          const o = document.getElementById('card'), n = o.cloneNode(true); n.removeAttribute('aria-describedby'); n.setAttribute('data-tip', '刷新后的新说明'); o.replaceWith(n); }""")
        pg.wait_for_timeout(200)
        r = pg.evaluate("() => { const t = document.getElementById('wbTip'); return !!t && t.classList.contains('on') && t.textContent === '刷新后的新说明' && document.getElementById('card').getAttribute('aria-describedby') === 'wbTip' }")
        pg.close(); return r
    ok(blocked_replace(TIP_JS_SRC), "屏蔽移入 / 移动事件后，光标下的元素被刷新成新说明：按光标位置校正显示新说明并挂到新节点上")
    no_mo = TIP_JS_SRC.replace("if (document.body) watch();", "if (false) watch();")
    ok(no_mo != TIP_JS_SRC and not blocked_replace(no_mo), "反向对照：关掉 MutationObserver 的版本在这里失败")
    if os.environ.get("TIP_PREV"):
        print("INFO  上一版在屏蔽事件时：", "补回来了" if blocked_replace(open(os.environ["TIP_PREV"], encoding="utf-8").read()) else "丢了")
    # Esc 静音只管同一个节点：相邻同名按钮照常弹（opus1 fba7 阻塞 3）
    pg = b.new_page(viewport={"width": 800, "height": 600})
    pg.set_content((TIP_PAGE % TIP_CSS).replace("</body>", '<button id="c1" data-tip="复制" style="position:absolute;left:100px;top:450px">一</button><button id="c2" data-tip="复制" style="position:absolute;left:200px;top:450px">二</button></body>'))
    pg.add_script_tag(content=TIP_JS_SRC)
    pg.hover("#c1"); pg.keyboard.press("Escape"); pg.wait_for_timeout(50)
    first_hidden = pg.evaluate("() => !document.getElementById('wbTip').classList.contains('on')")
    pg.hover("#c2"); pg.wait_for_timeout(50)
    ok(first_hidden and pg.evaluate("() => { const t = document.getElementById('wbTip'); return t.classList.contains('on') && document.getElementById('c2').getAttribute('aria-describedby') === 'wbTip' }"), "Esc 收起第一个「复制」后，悬停相邻的同名按钮照常显示")
    pg.close()
    # 滚动停下后按光标位置校正（像原生 :hover）：鼠标不动、移入 / 移动事件全屏蔽，只靠滚动后的校正。
    # 覆盖两种来源：用户滚动把另一个按钮带到光标下；刷新引起的滚动锚定，光标下还是同一个按钮。
    # 反向对照：上一版（滚动时清掉光标位置）两条都必须失败
    def scroll_settle(src):
        pg = b.new_page(viewport={"width": 800, "height": 600})
        pg.set_content((TIP_PAGE % TIP_CSS).replace("</body>", '<div style="height:2400px"></div><button id="s1" data-tip="滚动前光标下" style="position:absolute;left:300px;top:400px">一</button><button id="s2" data-tip="滚动后光标下" style="position:absolute;left:300px;top:700px">二</button></body>'))
        pg.add_script_tag(content=src); pg.hover("#s1"); pg.wait_for_timeout(50)
        pg.evaluate("""() => { const stop = e => e.stopImmediatePropagation();
          ['pointerover', 'pointerenter', 'pointermove', 'mouseover', 'mousemove'].forEach(t => window.addEventListener(t, stop, true)); }""")
        state = "() => { const t = document.getElementById('wbTip'); return t.classList.contains('on') ? t.textContent : '' }"
        pg.evaluate("() => scrollBy(0, 1)"); pg.wait_for_timeout(350); same = pg.evaluate(state)       # 像滚动锚定：动 1px，光标下还是 s1
        pg.evaluate("() => scrollBy(0, 299)"); pg.wait_for_timeout(350); other = pg.evaluate(state)    # 用户滚动：s2 到了光标下
        pg.close(); return same, other
    ok(scroll_settle(TIP_JS_SRC) == ("滚动前光标下", "滚动后光标下"), "滚动停下后按光标位置校正：滚动锚定时同一个说明回来，滚到别的按钮时换成它的说明（鼠标不动）")
    # 反向对照每次都跑：直接去掉「停下后校正」，两条都必须丢说明（opus1 1c4f：环境变量门住的对照退化了不会有人发现）
    NO_SETTLE = TIP_JS_SRC.replace("settle = setTimeout(later, 150);", "settle = 0;")
    ok(NO_SETTLE != TIP_JS_SRC, "反向对照的替换生效（找不到这句说明 tip.js 改了写法，要同步改这里）")
    ok(scroll_settle(NO_SETTLE) == ("", ""), "反向对照：去掉滚动停下后的校正，两条都丢说明")
    # 纯键盘：Tab 聚焦显示说明，滚动收起后停下时按焦点重新显示（没有光标位置）
    def kb_scroll(src, dy=1):
        pg = b.new_page(viewport={"width": 800, "height": 600})
        pg.set_content((TIP_PAGE % TIP_CSS).replace("</body>", '<div style="height:2400px"></div></body>'))
        pg.add_script_tag(content=src); pg.keyboard.press("Tab")
        state = "() => { const t = document.getElementById('wbTip'); return t && t.classList.contains('on') ? t.textContent : '' }"
        before = pg.evaluate(state)
        pg.evaluate(f"() => scrollBy(0, {dy})"); pg.wait_for_timeout(350); after = pg.evaluate(state)
        pg.close(); return before, after
    kb = kb_scroll(TIP_JS_SRC)
    ok(kb == ("顶边上的按钮，说明应翻到下方", "顶边上的按钮，说明应翻到下方"), f"键盘聚焦的说明：滚动停下后按焦点重新显示（实际 {kb}）")
    ok(kb_scroll(NO_SETTLE) == ("顶边上的按钮，说明应翻到下方", ""), "反向对照：去掉校正时，键盘聚焦的说明滚动后丢掉")
    # 和上面配对：「该回来」之外还有「不该回来」——聚焦的元素滚出视口上方，说明不能被夹进视口钉在顶边（opus1 5631）
    off = kb_scroll(TIP_JS_SRC, 400)
    ok(off == ("顶边上的按钮，说明应翻到下方", ""), f"聚焦的元素滚出视口后，说明不回来（实际 {off}）")
    # 鼠标点进输入框（:focus-visible 为真）→ 移开 → 离开窗口 → 页面刷新：说明不能自己弹回来（最后一次显示不是来自聚焦）
    pg = b.new_page(viewport={"width": 800, "height": 600})
    pg.set_content((TIP_PAGE % TIP_CSS).replace("</body>", '<input id="in" data-tip="输入框的说明" style="position:absolute;left:300px;top:100px"></body>'))
    pg.add_script_tag(content=TIP_JS_SRC); pg.click("#in"); pg.mouse.move(700, 550); pg.wait_for_timeout(50)
    pg.evaluate("() => document.body.dispatchEvent(new PointerEvent('pointerout', { bubbles: true, pointerType: 'mouse', relatedTarget: null }))")
    pg.evaluate("() => document.body.appendChild(document.createElement('div'))"); pg.wait_for_timeout(120)
    back = pg.evaluate("() => { const t = document.getElementById('wbTip'); return t && t.classList.contains('on') ? t.textContent : '' }"); pg.close()
    ok(back == "", f"鼠标点进输入框、移开、离开窗口后，刷新不把说明弹回来（实际 {back!r}）")
    faint, _ = tips(".wb-tip.on{animation:none;opacity:.2}")
    ok(len(faint) == 3 and all(x["how"] == "faint" for x in faint), f"说明层显示了但看不清（不透明度 0.2）时报「看不清」（实际 {len(faint)} 条）")
    # page_items：console 报错必须进结果（以前被行尾注释吞掉，体检永远 0 条 console）
    import types
    pg = b.new_page(viewport={"width": 800, "height": 600}); pg.set_content("<p style='color:#222'>正常</p>")
    items = ui_audit.page_items(pg, types.SimpleNamespace(tabs=0, tips=0, themes="light"), "light", ["boom: 控制台报错"])
    ok(any(x["kind"] == "console" and "boom" in x["el"] for x in items), "页面的控制台报错会进体检结果（page_items）")
    pg.close()
    missing, _ = tips(with_js=False)
    ok(len(missing) == 3 and all(x["how"] == "none" for x in missing), f"缺 tip.js 时每个都报「没显示」（实际 {len(missing)} 条）")
    broken, _ = tips(".wb-tip{left:-400px!important}")
    ok(any(x["how"] == "out" and x["px"] > 0 for x in broken), f"说明层被推出屏幕时报「超出」（实际 {broken}）")
# 对读屏隐藏的区域里还能聚焦：要报；加了 inert、disabled、tabindex=-1、display:none 的不报
HIDDEN_PAGE = """<!doctype html><html><body>
<aside aria-hidden="true" style="position:fixed;left:100%"><button data-action="close-x" aria-label="藏起来的关闭">x</button><a href="#">藏起来的链接</a><div aria-hidden="true"><button data-action="nested">嵌套只报一次</button></div></aside>
<button aria-hidden="true" data-action="self">自身隐藏的按钮</button>
<aside aria-hidden="true" inert style="position:fixed;left:100%"><button>inert 不算</button></aside>
<div aria-hidden="true"><button disabled>禁用不算</button><span tabindex="-1">-1 不算</span><button style="display:none">不显示不算</button><svg></svg></div>
</body></html>"""
WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(); pg.set_content(HIDDEN_PAGE); hid = pg.evaluate(ui_audit.HIDDEN_FOCUS_JS); pg.close()
    ok(sorted(x["el"] for x in hid) == ["a", "button[close-x]", "button[nested]", "button[self]"], f"对读屏隐藏的区域里可聚焦的要报（含自身被隐藏、嵌套只报一次），inert / 禁用 / -1 / 不显示的不报（实际 {hid}）")
    # 真实前端：关着的抽屉不能被 Tab 走进去（2026-09-29 修）
    def real_hidden(web):
        pg = b.new_page(viewport={"width": 1440, "height": 900}); pg.route("**/*", ui_audit.make_router(web, "audit.invalid", []))
        pg.goto("http://audit.invalid/?view=overview"); pg.wait_for_timeout(1500)
        r = pg.evaluate(ui_audit.HIDDEN_FOCUS_JS); pg.close(); return r
    real = real_hidden(WEB)
    ok(real == [], f"真实前端：关着的抽屉里没有能被 Tab 到的元素（实际 {real}）")
    # 关抽屉的最终行为：焦点回到触发按钮、不经过 body，抽屉 inert。只守结果，不证明顺序——
    # Chrome 里 inert 不当场挪焦点，旧顺序（先 inert）在这里同样通过（2026-09-29 对照过）
    def close_order(web):
        pg = b.new_page(viewport={"width": 1440, "height": 900}); pg.route("**/*", ui_audit.make_router(web, "audit.invalid", []))
        pg.goto("http://audit.invalid/?view=overview"); pg.wait_for_timeout(1500)
        drop = pg.evaluate("""async () => {
          const m = await import('/v2/js/components.js');
          const trigger = document.createElement('button'); trigger.textContent = 'trigger'; document.body.appendChild(trigger); trigger.focus();
          m.openDrawer({ title: 't', body: '<button id="inside">inside</button>' });
          await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
          document.getElementById('inside').focus();
          let toBody = 0; const f = e => { if (!e.relatedTarget) toBody++; }; document.addEventListener('focusout', f, true);
          m.closeDrawer(true); document.removeEventListener('focusout', f, true);
          return { toBody, back: document.activeElement === trigger, inert: document.getElementById('drawer').inert };
        }""")
        pg.close()
        return drop
    drop = close_order(WEB)
    ok(drop == {"toBody": 0, "back": True, "inert": True}, f"关抽屉：焦点直接回到触发按钮、不先掉到 body，之后抽屉 inert（实际 {drop}）")
    # 反向对照缺省不跑（需要一份修之前的前端）：日常靠上面固定页面里「移到屏幕外 + aria-hidden + 无 inert」那个 aside 守住同一种写法
    if os.environ.get("OLD_WEB"):   # 反向对照：修之前的前端必须报出抽屉的「关闭」
        old = real_hidden(os.environ["OLD_WEB"])
        ok(any("close-drawer" in x["el"] for x in old), f"反向对照：修之前的前端报出抽屉的「关闭」（实际 {old}）")
    b.close()

# 「加载资源失败」的控制台报错带上出错网址：两次体检各冒出一次 400，只有文字没法查是哪个接口
class _Msg:
    text = "Failed to load resource: the server responded with a status of 400 (Bad Request)"
    location = {"url": "http://127.0.0.1:8765/api/x?y=1"}
ok(ui_audit.console_text(_Msg()) == "加载资源失败 400 /api/x?y=1", f"加载失败的报错写成「状态码 + 网址」放最前面（实际 {ui_audit.console_text(_Msg())}）")
_Msg.location = {"url": "http://127.0.0.1:8765"}
ok(ui_audit.console_text(_Msg()) == "加载资源失败 400 /", f"网址没有路径时写 /，不把主机名当路径（实际 {ui_audit.console_text(_Msg())}）")
_Msg.text = "TypeError: boom"
ok(ui_audit.console_text(_Msg()) == "TypeError: boom", "其它报错原样")

sys.exit(1 if fails else 0)
