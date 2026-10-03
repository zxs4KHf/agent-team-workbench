import asyncio, sys
from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8765/'

async def main():
    fails = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={'width': 1440, 'height': 900})
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
        await pg.goto(BASE, wait_until='networkidle'); await pg.wait_for_timeout(1500)
        # 1 七个视图可达且无脚本错误
        for v in ['running', 'decisions', 'artifacts', 'compose', 'peers', 'settings', 'overview']:
            await pg.click(f'[data-view="{v}"]'); await pg.wait_for_timeout(700)
            if not await pg.locator('.view').count(): fails.append(f'view {v} empty')
        # 2 URL 反映视图
        await pg.click('[data-view="decisions"]'); await pg.wait_for_timeout(500)
        if 'view=decisions' not in pg.url: fails.append('url view param')
        # 3 命令面板
        await pg.keyboard.press('Control+k'); await pg.wait_for_timeout(300)
        if not await pg.locator('.cmdk.show').count(): fails.append('cmdk open')
        await pg.keyboard.press('Escape'); await pg.wait_for_timeout(200)
        if await pg.locator('.cmdk.show').count(): fails.append('cmdk esc close')
        # 4 后台刷新不丢输入
        await pg.click('[data-view="compose"]'); await pg.wait_for_timeout(800)
        await pg.fill('#question', '焦点保护测试'); await pg.wait_for_timeout(5000)
        if await pg.input_value('#question') != '焦点保护测试': fails.append('composer text lost')
        if await pg.evaluate('document.activeElement && document.activeElement.id') != 'question': fails.append('composer focus lost')
        # 5 抽屉焦点回退与 Esc
        await pg.click('[data-view="peers"]'); await pg.wait_for_timeout(800)
        btn = pg.locator('[data-action="edit-agent"]').first
        if await btn.count():
            await btn.click(); await pg.wait_for_timeout(500)
            if not await pg.locator('#drawer.show').count(): fails.append('drawer open')
            await pg.keyboard.press('Escape'); await pg.wait_for_timeout(400)
            if await pg.locator('#drawer.show').count(): fails.append('drawer esc')
        # 6 破坏性操作需要确认
        await pg.click('[data-view="decisions"]'); await pg.wait_for_timeout(800)
        rej = pg.locator('[data-action="reject"]').first
        if await rej.count():
            await rej.click(); await pg.wait_for_timeout(400)
            if not await pg.locator('.dialog.show').count(): fails.append('reject without confirm')
            await pg.keyboard.press('Escape')
        # 7 减弱动效
        await pg.emulate_media(reduced_motion='reduce')
        ms = await pg.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--m-slow').trim()")
        if ms != '0ms': fails.append(f'reduced motion {ms}')
        # 8 无障碍：图标按钮 aria-label
        bad = await pg.evaluate("[...document.querySelectorAll('button.icon')].filter(b => !b.getAttribute('aria-label') && !b.textContent.trim()).length")
        if bad: fails.append(f'{bad} icon buttons without aria-label')
        await b.close()
    fails.extend(f'console: {e}' for e in errs)
    print('PASS' if not fails else 'FAIL'); [print(' -', f) for f in fails]
    sys.exit(1 if fails else 0)

asyncio.run(main())
