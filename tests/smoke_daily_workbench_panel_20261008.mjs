import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../plugins/daily-workbench/web/panel.js', import.meta.url), 'utf8');
const good = { id: 'daily-workbench', ready: true,
  url: 'http://127.0.0.1:8790/?embedded=1', team_url: 'http://127.0.0.1:8765' };
const response = (data = good, ok = true) => ({ ok, json: async () => data });
const elements = new Map(['daily-workbench', 'connection', 'connection-error', 'error-detail', 'standalone', 'retry'].map(id => [id, {
  hidden: true, disabled: false, textContent: '',
  removeAttribute(key) { delete this[key]; },
  addEventListener(event, callback) { this[event] = callback; },
}]));
const requests = [];
let result = response();
const context = vm.createContext({
  URL, Error, TypeError,
  document: { getElementById: id => elements.get(id) },
  AbortSignal: { timeout: ms => ({ timeout: ms }) },
  fetch: async (url, options) => { requests.push({ url, options }); return await result; },
});
vm.runInContext(source, context);
const flush = () => new Promise(resolve => setImmediate(resolve));
await flush();
const frame = elements.get('daily-workbench');
const standalone = elements.get('standalone');
const error = elements.get('connection-error');
const retry = elements.get('retry');
assert.equal(requests[0].url, '/api/daily-workbench/plugin-info');
assert.equal(requests[0].options.signal.timeout, 10000);
assert.equal(requests[0].options.cache, 'no-store');
assert.equal(requests[0].options.redirect, 'error');
assert.equal(frame.src, good.url);
assert.equal(frame.hidden, false);
assert.equal(standalone.href, 'http://127.0.0.1:8790/');
assert.equal(error.hidden, true);

for (const host of ['127.0.0.1', 'localhost', '[::1]']) {
  const info = context.connectionInfo({ ...good, url: `http://${host}:18890/?embedded=1`, team_url: `http://${host}:18765/` });
  assert.equal(info.embedded, `http://${host}:18890/?embedded=1`);
  assert.equal(info.standalone, `http://${host}:18890/`);
}
for (const invalid of [null, {}, { ...good, id: 'level-design' }, { ...good, ready: false },
  { ...good, ready: 1 }, { ...good, ready: 'true' }, { ...good, team_url: undefined }]) {
  assert.throws(() => context.connectionInfo(invalid));
}
for (const url of [
  'https://127.0.0.1:8790/?embedded=1', 'javascript:alert(1)', '//127.0.0.1:8790/?embedded=1',
  'http://example.com:8790/?embedded=1', 'http://127.0.0.1.example.com:8790/?embedded=1',
  'http://127.0.0.1:8790@evil.test/?embedded=1', 'http://user@127.0.0.1:8790/?embedded=1',
  'http://user:password@127.0.0.1:8790/?embedded=1', 'http://0.0.0.0:8790/?embedded=1',
  'http://127.1:8790/?embedded=1', 'http://2130706433:8790/?embedded=1',
  'http://0x7f000001:8790/?embedded=1', 'http://%31%32%37.0.0.1:8790/?embedded=1',
  'http://localhost.:8790/?embedded=1', 'http://[::ffff:127.0.0.1]:8790/?embedded=1',
  ' http://127.0.0.1:8790/?embedded=1', 'http://127.0.0.1:8790/\n?embedded=1',
  'http:\\127.0.0.1:8790/?embedded=1', 'http://127.0.0.1:8790/other?embedded=1',
  'http://127.0.0.1:8790/?embedded=1#fragment', 'http://127.0.0.1:8790/',
  'http://127.0.0.1:8790/?embedded=0', 'http://127.0.0.1:8790/?embedded=1&next=https://evil.test',
  'http://127.0.0.1:8790/?embedded=1&embedded=0', 'http://127.0.0.1/?embedded=1',
  'http://127.0.0.1:80/?embedded=1', 'http://127.0.0.1:1023/?embedded=1',
  'http://127.0.0.1:65536/?embedded=1', 8790,
]) assert.throws(() => context.connectionInfo({ ...good, url }), String(url));
for (const team_url of ['https://127.0.0.1:8765', 'http://evil.test:8765',
  'http://127.0.0.1:8765/?token=secret', 'http://127.0.0.1:8765/#fragment',
  'http://127.0.0.1:8765/team']) {
  assert.throws(() => context.connectionInfo({ ...good, team_url }));
}

// An offline retry clears the old iframe and target, then allows a clean recovery.
result = response({}, false);
await retry.click();
assert.equal(frame.hidden, true);
assert.equal(frame.src, undefined);
assert.equal(standalone.hidden, true);
assert.equal(standalone.href, undefined);
assert.equal(error.hidden, false);
assert.equal(retry.disabled, false);

result = response({ ...good, url: 'http://evil.test:8790/?embedded=1' });
await retry.click();
assert.equal(frame.src, undefined);
assert.equal(standalone.href, undefined);
assert.equal(error.hidden, false);

let finish;
result = new Promise(resolve => { finish = resolve; });
const first = retry.click();
const count = requests.length;
assert.equal(retry.disabled, true);
await retry.click();
assert.equal(requests.length, count, 'repeated retry must not race the active discovery');
finish(response());
await first;
assert.equal(frame.src, good.url);
assert.equal(error.hidden, true);
assert.equal(retry.disabled, false);

let fail;
result = new Promise((resolve, reject) => { fail = reject; });
const pending = retry.click();
fail(Object.assign(new Error('timeout'), { name: 'TimeoutError' }));
await pending;
assert.match(elements.get('error-detail').textContent, /连接超时/);
assert.equal(error.hidden, false);
assert.equal(retry.disabled, false);
assert.equal(frame.src, undefined);

console.log('daily workbench panel URL validation, offline recovery and retry PASS');
