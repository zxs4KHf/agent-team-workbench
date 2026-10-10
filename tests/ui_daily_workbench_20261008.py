"""Render the plugin with temporary fixtures; use the repository's UI audit checks."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ui_audit import CONTRAST_JS, FOCUS_JS, OVERFLOW_JS


class PanelAudit(unittest.TestCase):
    def test_offline_ready_and_retry_at_mobile_and_desktop_widths(self):
        with tempfile.TemporaryDirectory(prefix="daily-workbench-ui-") as directory:
            fixture = Path(directory)
            shutil.copytree(ROOT / "plugins" / "daily-workbench" / "web", fixture / "plugins/daily-workbench/web")
            for relative in ("js/boot.js", "styles/tokens.css", "styles/base.css", "styles/components.css"):
                target = fixture / "v2" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / "web/v2" / relative, target)
            ready = False
            requested = []

            def route(request):
                parsed = urlparse(request.request.url)
                requested.append(parsed.path)
                if parsed.port == 8790:
                    self.assertEqual(parsed.path, "/")
                    request.fulfill(content_type="text/html", body="<!doctype html><title>Fixture</title><main>每日主线 fixture</main>")
                elif parsed.path == "/api/daily-workbench/plugin-info":
                    request.fulfill(content_type="application/json", body=json.dumps({
                        "id": "daily-workbench", "ready": ready,
                        "url": "http://127.0.0.1:8790/?embedded=1", "team_url": "http://127.0.0.1:18765",
                    }))
                else:
                    target = fixture / parsed.path.lstrip("/")
                    self.assertTrue(target.is_relative_to(fixture))
                    self.assertTrue(target.is_file(), parsed.path)
                    content_type = {".js": "text/javascript", ".css": "text/css", ".html": "text/html"}[target.suffix]
                    request.fulfill(content_type=content_type, body=target.read_bytes())

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    for theme in ("light", "dark", "sand"):
                        for width in (360, 1440):
                            with self.subTest(theme=theme, width=width):
                                ready = False
                                page = browser.new_page(viewport={"width": width, "height": 900})
                                failures = []
                                page.on("pageerror", lambda error: failures.append(str(error)))
                                page.on("console", lambda message: failures.append(message.text) if message.type == "error" else None)
                                page.route("**/*", route)
                                page.add_init_script("localStorage.setItem('wb.prefs', JSON.stringify(" + json.dumps({"theme": {"mode": theme}}) + "))")
                                page.goto("http://127.0.0.1:18765/plugins/daily-workbench/web/index.html")
                                page.locator("#connection-error").wait_for(state="visible")
                                self.assertFalse(page.locator("#daily-workbench").is_visible())
                                self.assertEqual(page.evaluate(CONTRAST_JS), [])
                                self.assertEqual(page.evaluate(OVERFLOW_JS), [])
                                for _ in range(3):
                                    page.keyboard.press("Tab")
                                    focus = page.evaluate(FOCUS_JS)
                                    self.assertIsNotNone(focus)
                                    self.assertNotIn(focus["how"], ("none", "tiny", "shadow-soft"))
                                    self.assertGreaterEqual(focus.get("ratio", 0), 3)
                                    self.assertFalse(focus.get("clipped"))
                                ready = True
                                page.locator("#retry").click()
                                page.locator("#standalone").wait_for(state="visible")
                                self.assertTrue(page.locator("#daily-workbench").is_visible())
                                self.assertEqual(page.locator("#daily-workbench").get_attribute("src"), "http://127.0.0.1:8790/?embedded=1")
                                self.assertEqual(page.locator("#standalone").get_attribute("href"), "http://127.0.0.1:8790/")
                                self.assertEqual(page.evaluate(CONTRAST_JS), [])
                                self.assertEqual(page.evaluate(OVERFLOW_JS), [])
                                self.assertEqual(failures, [])
                                page.close()
                finally:
                    browser.close()
            self.assertEqual({path for path in requested if path.startswith("/api/")}, {"/api/daily-workbench/plugin-info"})


if __name__ == "__main__":
    unittest.main()
