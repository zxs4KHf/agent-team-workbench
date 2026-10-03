import asyncio
import sys
from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8877/'


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={'width': 1440, 'height': 900})
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
        await pg.goto(BASE + '?view=arena-accounts', wait_until='networkidle')
        await pg.wait_for_timeout(2500)
        await pg.screenshot(path='shots/arena-accounts.png')
        await pg.click('[data-action="arena-account"]')
        await pg.wait_for_timeout(600)
        await pg.screenshot(path='shots/arena-account-drawer.png')
        await pg.keyboard.press('Escape')
        await pg.click('[data-action="arena-import"]')
        await pg.wait_for_timeout(400)
        await pg.fill('#importText', 'x@y.com pw1\nz@y.com, pw2')
        await pg.wait_for_timeout(600)
        await pg.screenshot(path='shots/arena-import.png')
        await pg.keyboard.press('Escape')
        for v in ['arena-sessions', 'arena-routes', 'arena-leaderboard']:
            await pg.click(f'[data-view="{v}"]')
            await pg.wait_for_timeout(2500)
            await pg.screenshot(path=f'shots/{v}.png')
        await pg.click('[data-view="arena-sessions"]')
        await pg.wait_for_timeout(1500)
        await pg.click('[data-action="arena-select-probes"]')
        await pg.wait_for_timeout(500)
        await pg.screenshot(path='shots/arena-sessions-sel.png')
        await pg.click('[data-view="arena-routes"]')
        await pg.wait_for_timeout(1500)
        await pg.click('[data-action="arena-new-key"]')
        await pg.fill('#keyName', 'test')
        await pg.click('#drawer button[type=submit]')
        await pg.wait_for_timeout(800)
        await pg.screenshot(path='shots/arena-key.png')
        await b.close()
    print('errors:', errs)

asyncio.run(main())
