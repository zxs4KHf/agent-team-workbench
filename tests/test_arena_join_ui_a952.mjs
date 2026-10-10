// 前端最小模块测试（20260929-152248-a952）：不启动浏览器，不请求网络。
// 把 arena.js 的三个依赖模块换成桩，用假 DOM 跑：预览→确认、刷新恢复、状态文案、unconfirmed 非成功、重复点击同 request。
// 运行：node tests/test_arena_join_ui_a952.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const SRC = path.join(ROOT, 'plugins', 'arena', 'web', 'arena.js');

// ---- 桩：记录 api 调用与 toast/drawer ----
const calls = [];
const toasts = [];
const drawers = [];
globalThis.__stub = {
  api: async (url, opts) => {
    const body = opts && opts.body ? JSON.parse(opts.body) : null;
    calls.push({ url, method: (opts && opts.method) || 'GET', body });
    const h = globalThis.__stub.routes[url.replace(/\?.*$/, '')];
    if (!h) return {};
    const r = typeof h === 'function' ? h(body) : h;
    if (r && r.__error) { const e = new Error(r.__error); e.reason_code = r.reason_code; throw e; }
    return r;
  },
  routes: {},
  toast: (msg, tone, o) => toasts.push({ msg, tone }),
  openDrawer: d => drawers.push(d),
};

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'a952-ui-'));
fs.writeFileSync(path.join(tmp, 'core.js'), `
export const esc = s => String(s ?? '');
export const attr = s => String(s ?? '');
export const icon = () => '';
export const api = (...a) => globalThis.__stub.api(...a);
export const fmtDate = s => String(s || '');
export const relTime = s => String(s || '');
export const fmtNum = n => String(n);
export const $ = (sel, root) => (root || globalThis.document).querySelector(sel);
export const $$ = (sel, root) => Array.from((root || globalThis.document).querySelectorAll(sel));
`);
fs.writeFileSync(path.join(tmp, 'components.js'), `
export const toast = (...a) => globalThis.__stub.toast(...a);
export const openDrawer = d => globalThis.__stub.openDrawer(d);
export const closeDrawer = () => {};
export const confirmDialog = async () => true;
`);
fs.writeFileSync(path.join(tmp, 'views.js'), `
export const head = (a, b, c, extra = '') => '<h1>' + b + '</h1>' + extra;
export const empty = () => '';
`);
// 插件页面里 arena.js 按绝对路径引工作台模块（/v2/js/…），这里换回同目录桩
fs.writeFileSync(path.join(tmp, 'arena.js'), fs.readFileSync(SRC, 'utf-8').replaceAll("'/v2/js/", "'./"));

// ---- 假 DOM：只实现本测试用到的 querySelector/querySelectorAll ----
class FakeForm {
  constructor(checked, unchecked = []) { this.checked = checked; this.unchecked = unchecked; this.submit = { disabled: false }; this.result = { innerHTML: '' }; }
  querySelector(sel) { if (sel === 'button[type="submit"]') return this.submit; if (sel === '#commonJoinResult') return this.result; return null; }
  querySelectorAll(sel) { return sel.includes(':checked') ? this.checked.map(v => ({ value: v })) : [...this.checked, ...this.unchecked].map(v => ({ value: v })); }
}
globalThis.document = { hidden: false, querySelector: () => null, querySelectorAll: () => [], dispatchEvent: () => {} };
globalThis.location = { search: '?view=arena-groups' };
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};

const mod = await import(pathToFileURL(path.join(tmp, 'arena.js')).href);
const { A, ARENA_ACTIONS, ARENA_FORMS, renderArenaGroups, loadArena } = mod;

const GROUPS = { groups: [
  { id: 'claude', name: 'Claude 组', tier: 'strong', custom: false, common: true, members: [], member_count: 2, ready_count: 2, detected_count: 2, match: ['claude-opus-4'] },
  { id: 'gpt', name: 'GPT 组', tier: 'daily', custom: false, common: false, members: [], member_count: 1, ready_count: 1, detected_count: 1, match: ['gpt-5'] },
], unmatched: {}, base_url: 'http://127.0.0.1:9091' };

// 服务端 finished_at 是本地时间「YYYY-MM-DD HH:MM:SS」，浏览器也按本地时间解析；测试同样用本地时间
const nowText = () => { const d = new Date(); const p = n => String(n).padStart(2, '0'); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`; };
function job(over) { return { id: 'j1', kind: 'join', status: 'queued', started_at: '2026-09-29 15:00:00', updated_at: '2026-09-29 15:00:00', progress: 0, total: 3, message: '排队中', result: null, error: '', reason_code: '', retryable: false, payload: { group: 'claude', session_id: 's1' }, ...over }; }
const reset = () => { calls.length = 0; toasts.length = 0; drawers.length = 0; A.lastJobs = {}; };

test('刷新恢复：jobs 接口返回的持久 join 作业直接进入 A.jobs 并按服务端状态渲染', async () => {
  reset();
  globalThis.__stub.routes = {
    '/api/arena/overview': {}, '/api/arena/accounts': { accounts: [], summary: [] }, '/api/arena/groups': GROUPS,
    '/api/arena/jobs': { jobs: [job({ status: 'waiting_peer', message: '等待 peer 心跳（最多 120 秒）', progress: 2 })], join_config: { max_concurrency: 1 } },
  };
  await loadArena('arena-groups');
  assert.equal(A.jobs.length, 1);
  assert.equal(A.joinConfig.max_concurrency, 1);
  const html = renderArenaGroups();
  assert.match(html, /等待 Agent 心跳/);
  // 4582（用户 2026-10-02 决定）：等心跳时消息已发进会话，撤不回——取消按钮置灰并说明，不再可点
  assert.doesNotMatch(html, /arena-join-cancel/);
  assert.match(html, /disabled title="消息已经发进会话，撤不回/);
  A.jobs = [job({ status: 'queued' })];
  assert.match(renderArenaGroups(), /arena-join-cancel/);   // 只有排队中的可取消
  assert.match(html, /接入常用组 \(1\)/);           // 常用组只数 common:true 的
  assert.match(html, /★ 常用/);
  assert.match(html, /☆ 设为常用/);
  assert.doesNotMatch(html, /setTimeout\(.*120/);
});

test('状态文案：unconfirmed 不显示成功、不提供取消；connected 显示已接入', () => {
  reset();
  A.groups = GROUPS;
  A.jobs = [job({ id: 'u', status: 'unconfirmed', message: '消息已发送，但没等到本次接入之后的有效心跳（未确认，不自动重发）', finished_at: nowText(), reason_code: 'no_heartbeat', result: { registered: false, peer: 'claude' } })];
  let html = renderArenaGroups();
  assert.match(html, /未确认/);
  assert.doesNotMatch(html, /已接入/);
  assert.doesNotMatch(html, /arena-join-cancel/);
  A.jobs = [job({ id: 'c', status: 'connected', message: '已接入：claude', finished_at: nowText(), reason_code: 'ok', result: { registered: true, peer: 'claude' } })];
  html = renderArenaGroups();
  assert.match(html, /已接入/);
  assert.doesNotMatch(html, /arena-join-cancel/);
});

test('announceJobs：waiting_peer → unconfirmed 弹 warn，不弹完成；→ connected 弹 ok', async () => {
  reset();
  const seq = [job({ status: 'waiting_peer' }), job({ status: 'unconfirmed', reason_code: 'no_heartbeat' })];
  let i = 0;
  globalThis.__stub.routes = { '/api/arena/overview': {}, '/api/arena/groups': GROUPS, '/api/arena/accounts': {}, '/api/arena/jobs': () => ({ jobs: [seq[Math.min(i++, seq.length - 1)]] }) };
  await loadArena('arena-groups');
  await loadArena('arena-groups');
  const last = toasts.at(-1);
  assert.ok(last, 'should toast on state change');
  assert.equal(last.tone, 'warn');
  assert.match(last.msg, /未确认/);
  assert.doesNotMatch(last.msg, /完成/);
  reset();
  const seq2 = [job({ status: 'sending' }), job({ status: 'connected', payload: { peer_name: 'claude' } })];
  i = 0;
  globalThis.__stub.routes['/api/arena/jobs'] = () => ({ jobs: [seq2[Math.min(i++, 1)]] });
  await loadArena('arena-groups');
  await loadArena('arena-groups');
  assert.equal(toasts.at(-1).tone, 'ok');
  assert.match(toasts.at(-1).msg, /已接入 · Agent claude/);
});

test('单会话接入：每次点击都带 request_id，走 groups/join；dedup 时提示沿用', async () => {
  reset();
  globalThis.__stub.routes = { '/api/arena/groups/join': b => ({ id: 'jx', status: 'queued', dedup: b.request_id === 'fixed' ? 'in_flight' : '' }), '/api/arena/overview': {}, '/api/arena/groups': GROUPS, '/api/arena/accounts': {}, '/api/arena/jobs': { jobs: [] } };
  await ARENA_ACTIONS['arena-group-join']({ dataset: { id: 'claude', session: 's1' } });
  const c = calls.find(x => x.url.endsWith('groups/join'));
  assert.ok(c.body.request_id && c.body.request_id.length >= 8);
  assert.equal(c.body.id, 'claude');
  assert.match(toasts[0].msg, /已排队/);
});

test('预览→确认：preview 只带 common 组；确认带 preview_token+request_id+明确选择；重复提交同一 request_id', async () => {
  reset();
  A.groups = GROUPS;
  globalThis.__stub.routes = {
    '/api/arena/join/preview': b => ({ preview_token: 'tok1', groups: b.group_ids.map(g => ({ group: g, name: g, source: 'preset', online_agents: [], in_flight: [], skip_reason: '', will_send: 1, candidates: [
      { session_id: 's1', title: 'Claude A', canon: 'claude-opus-4', account: 'a@x', eligible: true, selected: true, skip_reason: '' },
      { session_id: 's2', title: 'Claude B', canon: 'claude-opus-4', account: 'a@x', eligible: true, selected: false, skip_reason: '' },
      { session_id: 's3', title: 'Claude C', canon: 'claude-opus-4', account: 'd@x', eligible: false, selected: false, skip_reason: 'account_unauthenticated' }] })), will_send_total: 1, side_effects: 'none' }),
    '/api/arena/join/confirm': b => ({ request_id: b.request_id, queued: Object.values(b.selections).flat().length, aborted: false, items: Object.entries(b.selections).flatMap(([g, ss]) => ss.map(s => ({ group: g, session_id: s, job: 'j-' + s, status: 'queued', skip: '', error: '' }))) }),
    '/api/arena/overview': {}, '/api/arena/groups': GROUPS, '/api/arena/accounts': {}, '/api/arena/jobs': { jobs: [] },
  };
  await ARENA_ACTIONS['arena-common-join']();
  const pv = calls.find(x => x.url.endsWith('join/preview'));
  assert.deepEqual(pv.body.group_ids, ['claude']);              // gpt 不是 common，不进预览
  assert.equal(drawers.length, 1);
  assert.match(drawers[0].body, /默认选中/);
  assert.match(drawers[0].body, /可加选/);                         // 同组第二个允许明确加选
  assert.match(drawers[0].body, /账号未登录/);
  assert.match(drawers[0].body, /将发送 <span id="commonJoinCount">1<\/span> 条接入消息/);
  assert.ok(!calls.some(x => x.url.endsWith('join/confirm')), '预览阶段不得调用 confirm');
  const form = new FakeForm(['claude|s1', 'claude|s2'], ['claude|s3']);
  await ARENA_FORMS['arena-common-confirm'](form);
  const cf = calls.find(x => x.url.endsWith('join/confirm'));
  assert.equal(cf.body.preview_token, 'tok1');
  assert.ok(cf.body.request_id);
  assert.deepEqual(cf.body.selections, { claude: ['s1', 's2'] });
  assert.match(form.result.innerHTML, /已入队 2 项/);
  assert.match(form.result.innerHTML, /排队中/);
  const first = cf.body.request_id;
  await ARENA_FORMS['arena-common-confirm'](new FakeForm(['claude|s1']));  // 连点/重复提交
  const again = calls.filter(x => x.url.endsWith('join/confirm')).at(-1);
  assert.equal(again.body.request_id, first);                     // 同一 request_id：服务端幂等
  await ARENA_ACTIONS['arena-common-join']();                     // 重新预览才换 request_id
  await ARENA_FORMS['arena-common-confirm'](new FakeForm(['claude|s1']));
  assert.notEqual(calls.filter(x => x.url.endsWith('join/confirm')).at(-1).body.request_id, first);
});

test('没有常用组时不请求预览，只提示', async () => {
  reset();
  A.groups = { ...GROUPS, groups: GROUPS.groups.map(g => ({ ...g, common: false })) };
  await ARENA_ACTIONS['arena-common-join']();
  assert.equal(calls.length, 0);
  assert.match(toasts[0].msg, /还没有常用组/);
});
