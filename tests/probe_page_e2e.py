"""Read-only real-page probe. All /api requests are synthetic fixture responses."""
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_notify_page_60e8 import PageTest, STATE


def _artifact(name):
    # 运行产物写到 tmp/（已被 .gitignore 忽略），不再落进 docs/ 被误提交（sota1 32b8）
    d = Path(__file__).resolve().parents[1] / 'tmp' / 'probe_page_e2e'
    d.mkdir(parents=True, exist_ok=True)
    return d / name



def main():
    PageTest.setUpClass()
    ctx = PageTest.browser.new_context()
    page = ctx.new_page()
    errors = []
    cdp = ctx.new_cdp_session(page)
    cdp.on("Debugger.scriptFailedToParse", lambda data: errors.append({"parse": data}))
    cdp.send("Debugger.enable")
    print("BROWSER", PageTest.browser.version, flush=True)
    page.on('pageerror', lambda err: errors.append({"message": str(err), "stack": err.stack}))
    page.route('**/api/**', lambda route: route.fulfill(
        status=200, content_type='application/json',
        body=json.dumps(STATE) if '/api/state' in route.request.url else '{}'))
    try:
        page.goto('http://127.0.0.1:' + str(PageTest.port) + '/v2/index.html')
        page.wait_for_timeout(1200)
        print(json.dumps({'errors': errors, 'visible': page.locator('#content').inner_text()[:1000]}, ensure_ascii=False), flush=True)
        for item in errors:
            if "parse" in item:
                source = cdp.send("Debugger.getScriptSource", {"scriptId": item["parse"]["scriptId"]})["scriptSource"]
                _artifact("served-app.js").write_text(source, encoding="utf-8")
                print("SERVED_SIZE", len(source), flush=True)
        target = _artifact('stage2-page.png')
        page.screenshot(path=str(target), full_page=True)
        return 1 if errors else 0
    finally:
        ctx.close()
        PageTest.tearDownClass()


if __name__ == '__main__':
    raise SystemExit(main())
