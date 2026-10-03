// 进行中页「只看卡住」：与概览行动条「卡住的施工」同口径（stallInfo.terminal）；行动条有卡住项时点击直达并带上筛选
import { mkdtempSync, mkdirSync, cpSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」
const repo = process.cwd();
const tmp = mkdtempSync(join(tmpdir(), 'bus-stuck-ui-'));
cpSync(join(repo, 'web/v2/js'), join(tmp, 'web/v2/js'), { recursive: true });
mkdirSync(join(tmp, 'web/v2/vendor/pretext'), { recursive: true });
cpSync(join(repo, 'web/v2/vendor/idiomorph.esm.js'), join(tmp, 'web/v2/vendor/idiomorph.esm.js'));
writeFileSync(join(tmp, 'web/v2/vendor/pretext/layout.js'), 'export const prepare = () => ({}); export const layout = () => ({ lines: [] });');
process.on('exit', () => rmSync(tmp, { recursive: true, force: true }));
const kv = new Map();
globalThis.localStorage = { getItem: k => kv.has(k) ? kv.get(k) : null, setItem: (k, v) => kv.set(k, String(v)), removeItem: k => kv.delete(k) };
globalThis.location = { search: '', pathname: '/', hash: '', href: 'http://x/' };
globalThis.history = { state: null, pushState() {}, replaceState() {} };
globalThis.matchMedia = () => ({ matches: false });
globalThis.requestAnimationFrame = fn => fn();
globalThis.document = { querySelector: () => null, querySelectorAll: () => [], createElement: () => ({ style: {}, classList: { add() {}, remove() {} }, setAttribute() {}, querySelector: () => ({}) }), activeElement: null, body: { style: {} }, addEventListener() {}, hidden: false };
globalThis.window = globalThis;
const mod = rel => import(pathToFileURL(join(tmp, rel)).href);
const M = await mod('web/v2/js/model.js');
const V = await mod('web/v2/js/views.js');
const now = '2026-09-24 00:50:00';
const mk = (id, state) => ({ task_id: id, status: 'collecting', title: id, created: now, updated: now, peers: ['f1'], progress: { expected: ['f1'], answered: [], states: { f1: state }, meta: { id, created: now, ttl_min: 999999 } } });
const base = { time: now, connected: true, lastRefresh: 0, history: [], peers: [{ peer: 'f1', model: 'm', status: 'idle', online: true, last_seen: now }],
  controller: { online: true, status: 'idle', age_sec: 3 }, projects: [{ slug: 'p', name: 'p', peers: ['f1'], decisions: [mk('t-ok1', 'working'), mk('t-bad', 'expired'), mk('t-ok2', 'working')] }],
  project: 'p', commands: [], nudges: {}, teams: [], priority: { version: 2, relay_order: [], task_order: [] }, lease: { holder: 'controller' } };
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
const keys = h => [...h.matchAll(/data-key="(t-[\w-]+)"/g)].map(m => m[1]);
M.store.set({ ...base }); const p = base.projects[0];
ok(M.F.filters.runningStuck === false, '默认不筛选');
let h = V.renderRunning(p);
ok(keys(h).length === 3, '默认显示全部 3 个任务');
ok(/data-filter-stuck="toggle" aria-pressed="false">只看卡住<span class="n" id="stuckN">1<\/span>/.test(h), '有卡住项时出现「只看卡住 1」筛选片（aria-pressed=false）');
M.F.filters.runningStuck = true; h = V.renderRunning(p);
ok(JSON.stringify(keys(h)) === '["t-bad"]', '打开后只剩卡住的 t-bad');
ok(h.includes('aria-pressed="true"') && h.includes('1 / 3'), '筛选片高亮，计数 1 / 3');
ok(JSON.stringify(keys(V.taskListHtml(p))) === '["t-bad"]', 'taskListHtml（局部重画）同样只出卡住项');
{ const m = V.runningMeta(p); ok(Array.isArray(m.rows) && m.rows.length === m.listN && !('list' in m) && JSON.stringify(keys(V.taskListHtml(p, m.rows))) === '["t-bad"]', 'runningMeta().rows 是当前行数组，交给 taskListHtml 结果与自算一致');
  ok(V.taskListHtml(p, []) === '', 'taskListHtml 用调用方传入的列表，不再自己重算（fable2 470d）'); }
M.F.filters.running = 'ok'; h = V.renderRunning(p);
ok(h.includes('没有匹配的任务') && h.includes('再点一次「只看卡住」取消筛选'), '关键词 + 卡住无交集：提示可取消筛选');
ok(/<span class="n" id="stuckN">0<\/span>/.test(h) && h.includes('data-tip="共 1 个卡住，符合关键词的 0 个"') && !/ title="共/.test(h), '关键词无交集：片上是 0（不再写 1 却列表空），悬停气泡（data-tip，随主题 / 字号）说明总数');
{ const m = V.runningMeta(p); ok(m.stuckN === 0 && m.stalled === 1 && m.count === '0 / 3' && m.showChip && m.tip === '共 1 个卡住，符合关键词的 0 个', `runningMeta 与页面同口径（${JSON.stringify(m)}）`); }
M.F.filters.running = '';
const clean = { ...p, decisions: [mk('t-ok1', 'working')] };
h = V.renderRunning(clean);
ok(h.includes('没有卡住的任务') && h.includes('data-filter-stuck="toggle"'), '卡住项清零但筛选仍开：显示「没有卡住的任务」并保留筛选片可关掉');
M.F.filters.runningStuck = false; h = V.renderRunning(clean);
ok(!h.includes('data-filter-stuck'), '没有卡住项且未筛选：不显示筛选片');
// 概览行动条
M.F.filters.runningStuck = false;
const ov = V.renderOverview(p);
ok(/data-view="running" data-filter-stuck="on"/.test(ov), '概览「卡住的施工」有卡住项时带 data-filter-stuck="on"');
M.store.set({ ...base, projects: [clean] });
ok(!V.renderOverview(clean).includes('data-filter-stuck'), '没有卡住项时行动条不带筛选');
// 开关行为（fable2 9a21：原来对 app.js 源码做长正则，换行就红；改为直接调用）
M.F.filters.runningStuck = false;
ok(V.toggleStuck('toggle') === true && V.toggleStuck('toggle') === false, 'toggle：来回切换');
ok(V.toggleStuck('on') === true && V.toggleStuck('on') === true, 'on（行动条直达）：总是打开，重复点不会关掉');
ok(V.toggleStuck('off') === false && M.F.filters.runningStuck === false, 'off：关');
// 局部重画：解卡后计数跟着变（runningMeta 就是 renderScoped 用来同步 #runningCount / #stuckN 的数据）
M.F.filters.running = ''; M.F.filters.runningStuck = false;
const healed = { ...p, decisions: [mk('t-ok1', 'working'), mk('t-bad', 'working'), mk('t-ok2', 'working')] };
{ const a = V.runningMeta(p), b2 = V.runningMeta(healed); ok(a.stuckN === 1 && b2.stuckN === 0 && !b2.showChip, `解卡后 stuckN 1→0、筛选片该隐藏（renderScoped 据此整页重画）`); }
// 接线：只做最小的结构检查——捕获阶段注册、调 toggleStuck、写 stuck 参数；启动与后退都读回
const app = frontSrc('app.js');
const line = app.split('\n').find(l => l.includes("closest('[data-filter-stuck]'); if (!b) return")) || '';
ok(line.includes('toggleStuck(mode)') && line.includes("url.set({ stuck:") && /\}, true\);\s*$/.test(line), 'app.js：捕获阶段调用 toggleStuck 并写 URL');
ok((app.match(/F\.filters\.runningStuck = url\.get\('stuck'\) === '1'/g) || []).length >= 2, 'app.js：启动与后退（popstate）都从 URL 读回');
ok(/runningMeta\(\)/.test(app.slice(app.indexOf('function renderScoped'), app.indexOf('function tickRelTime'))), 'renderScoped 局部重画时同步列表外计数');
ok(/dataset\.tip = m\.tip/.test(app.slice(app.indexOf('function renderScoped'), app.indexOf('function tickRelTime'))), 'renderScoped 同步筛选片的悬停说明');
ok(line.includes("if (mode === 'on') setTimeout(apply, 0)"), '行动条直达：切完视图再写 stuck（不挂在概览历史项上）');
{ M.F.filters.running = 'ok'; M.F.filters.runningStuck = false; const a = V.runningMeta(p); M.F.filters.running = ''; const b3 = V.runningMeta(p);
  ok(a.stuckN === 0 && b3.stuckN === 1, `关键词变了计数跟着变（${a.stuckN}→${b3.stuckN}）`); }
console.log(`\n${fails ? fails + ' 项失败' : '全部通过'}`); process.exit(fails ? 1 : 0);
