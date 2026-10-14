"""Actual hub UI, fake API, isolated browser. Never dispatches a real task."""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import unittest
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]

class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

class FeedbackBrowser(unittest.TestCase):
    def test_resolution_and_retry_at_desktop_and_phone_widths(self):
        from playwright.sync_api import sync_playwright
        server = ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Quiet,directory=str(ROOT/'web')))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base = 'http://127.0.0.1:' + str(server.server_address[1])
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for width in (1440,375):
                    with self.subTest(width=width):
                        context = browser.new_context(viewport={'width':width,'height':900})
                        page = context.new_page(); errors=[]; posts=[]
                        records=[{'id':'fb-aaaaaaaaaa','text':'sample awaiting check','kind':'bug','status':'awaiting_verification','command_id':'old-a'},
                                 {'id':'fb-bbbbbbbbbb','text':'sample deferred','kind':'ux','status':'deferred','command_id':'old-b'}]
                        page.on('pageerror',lambda error:errors.append(str(error)))
                        def api(route):
                            path=urlparse(route.request.url).path
                            if path=='/api/events':
                                return route.fulfill(status=200,content_type='text/event-stream',body='')
                            if path=='/api/feedback/update':
                                body=route.request.post_data_json;posts.append(body)
                                row=next(row for row in records if row['id']==body['id']);row['status']=body['status'];data=row
                            elif path=='/api/feedback':data={'items':records}
                            elif path=='/api/plugins':data={'plugins':[]}
                            elif path=='/api/state':data={'projects':[]}
                            elif path.startswith('/api/event-log'):data={'events':[]}
                            else:data={'items':[]}
                            route.fulfill(status=200,content_type='application/json',body=json.dumps(data))
                        page.route('**/api/**',api)
                        page.goto(base+'/v2/hub.html#feedback')
                        page.locator('[data-item="fb-aaaaaaaaaa"]').wait_for()
                        self.assertEqual(page.locator('[data-count="feedback"]').inner_text(),'2')
                        page.locator('[data-item="fb-aaaaaaaaaa"] [data-s="done"]').click()
                        page.wait_for_function("document.querySelector('#hubToast').textContent.includes('依据')")
                        self.assertEqual(posts,[])
                        page.locator('#fn-fb-aaaaaaaaaa').fill('checked on isolated fixture')
                        page.locator('[data-item="fb-aaaaaaaaaa"] [data-s="done"]').click()
                        page.locator('[data-item="fb-aaaaaaaaaa"]').wait_for(state='detached')
                        self.assertEqual(posts[-1]['expect'],'awaiting_verification')
                        self.assertEqual(posts[-1]['expect_command_id'],'old-a')
                        page.locator('[data-item="fb-bbbbbbbbbb"] [data-s="converted"]').click()
                        page.locator('[data-item="fb-bbbbbbbbbb"]').wait_for(state='detached')
                        self.assertEqual(posts[-1]['expect'],'deferred')
                        self.assertEqual(posts[-1]['expect_command_id'],'old-b')
                        self.assertEqual(errors,[])
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                        context.close()
            finally:
                browser.close()

if __name__=='__main__':
    unittest.main(verbosity=2)
