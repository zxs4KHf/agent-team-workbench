// 任务历史前端冒烟（任务 201a · 3847 按 2c0a 返工重落地）。
// 真 import web/v2/js/history.js 并执行它的 renderHistory / loadHistory / HISTORY_ACTIONS / historyKeydown，
// fetch 用假分页服务端（游标当不透明串）；不依赖真实工作台服务、不碰生产 done/projects。
// 跑法：node tests/smoke_history_view_201a.mjs（仓库根目录）
import { mkdtempSync, mkdirSync, cpSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」

const repo = process.cwd();
const tmp = mkdtempSync(join(tmpdir(), 'bus201a-ui-'));
cpSync(join(repo, 'web/v2/js'), join(tmp, 'web/v2/js'), { recursive: true });
mkdirSync(join(tmp, 'web/v2/vendor/pretext'), { recursive: true });
cpSync(join(repo, 'web/v2/vendor/idiomorph.esm.js'), join(tmp, 'web/v2/vendor/idiomorph.esm.js'));
writeFileSync(join(tmp, 'web/v2/vendor/pretext/layout.js'), 'export const prepare = () => ({}); export const layout = () => ({ lines: [] });');
process.on('exit', () => rmSync(tmp, { recursive: true, force: true }));

const lsStore = new Map();
let lsFail = false;
globalThis.localStorage = {
  getItem: k => (lsStore.has(k) ? lsStore.get(k) : null),
  setItem: (k, v) => { if (lsFail) throw new Error('QuotaExceededError'); lsStore.set(k, String(v)); },
  removeItem: k => lsStore.delete(k),
};
globalThis.location = { search: '', pathname: '/', hash: '', href: 'http://x/' };
const writeUrl = u => { const i = u.indexOf('?'); if (i >= 0) { const j = u.indexOf('#', i); globalThis.location.search = u.slice(i + 1, j < 0 ? undefined : j); } else globalThis.location.search = ''; };
const navStack = [];
globalThis.history = { state: null, pushState(_s, _t, u) { navStack.push(u); writeUrl(u); }, replaceState(_s, _t, u) { writeUrl(u); } };
globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.requestAnimationFrame = fn => fn();
globalThis.document = { querySelector: () => null, querySelectorAll: () => [], createElement: () => ({ style: {}, classList: { add() {}, remove() {} }, setAttribute() {}, querySelector: () => ({}) }), activeElement: null, body: { style: {}, classList: { add() {}, remove() {} } }, addEventListener() {}, hidden: false };
globalThis.window = globalThis;

const mod = rel => import(pathToFileURL(join(tmp, rel)).href);
const { store: st } = await mod('web/v2/js/model.js');
const H = await mod('web/v2/js/history.js');

let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
const iso = off => new Date(Date.UTC(2026, 8, 29, 1, 0, 0) + off * 60000).toISOString().slice(0, 19);

// ---------- 假服务端：25 条（15 决策 + 10 指令），游标 c<offset>，只按 (project,filter) 冻结 ----------
const PAGE = 20;
const mkRec = (i, kind, project) => kind === 'decision'
  ? { kind: 'decision', id: `t${i}`, project, revision: 1, status: i % 7 === 0 ? 'rejected' : 'adopted', title: `决策 ${i}`, time: iso(i), merge_path: `log/${i}.md`, canonical_id: `cid-d-${project}-${i}` }
  : { kind: 'command', id: `c${i}`, project, status: i % 5 === 0 ? 'failed' : 'done', cmd_kind: 'bus_collect', task_id: `t${i}`, result: '已收集', time: iso(i), canonical_id: `cid-c-${project || ''}-${i}` };
let DB = [];
for (let i = 31; i <= 35; i++) DB.push(mkRec(i, 'decision', 'q'));     // 项目 q：5 条，只在「全部项目」口径里出现
for (let i = 1; i <= 15; i++) DB.push(mkRec(i, 'decision', 'p'));      // 项目 p：15 决策 + 10 指令 = 25 条 → 2 页
for (let i = 16; i <= 25; i++) DB.push(mkRec(i, 'command', 'p'));
const mode = { expireOnce: false, failNext: false, drop: '' };
const served = [];
globalThis.fetch = async (path) => {
  served.push(String(path));
  const u = new URL(String(path), 'http://x');
  if (u.pathname !== '/api/inbox/history') return res(404, { error: 'not_found' });
  if (mode.failNext) { mode.failNext = false; return res(500, { error: 'boom' }); }
  const project = u.searchParams.get('project') || '';
  const filter = u.searchParams.get('filter') || 'all';
  const limit = Number(u.searchParams.get('limit') || PAGE);
  const cursor = u.searchParams.get('cursor') || '';
  if (cursor && mode.expireOnce) { mode.expireOnce = false; return res(410, { error: 'cursor_expired' }); }
  let list = DB.filter(r => r.canonical_id !== mode.drop && (!project || r.project === project) && (filter === 'all' || r.kind === (filter === 'commands' ? 'command' : 'decision')));
  const offset = cursor ? Number(cursor.slice(1)) : 0;
  if (!Number.isFinite(offset) || offset > list.length) return res(410, { error: 'cursor_expired' });
  const pageItems = list.slice(offset, offset + limit);
  const nxt = offset + limit < list.length ? `c${offset + limit}` : '';
  return res(200, { items: pageItems.map(r => ({ ...r })), next_cursor: nxt, total: list.length, offset, limit,
    range: { start: offset, end: offset + pageItems.length }, complete: !nxt,
    snapshot: { id: 'snap1', created: iso(0), ttl_sec: 600, skipped: 0 },
    source: { commands: '', decisions: '', terminal_statuses: ['merged', 'adopted', 'rejected', 'cancelled', 'superseded', 'archived'] },
    id_namespace: 'atwb-history-v1', id_version: 1, project, filter, note: '' });
};
function res(status, body) { return { ok: status < 400, status, text: async () => JSON.stringify(body) }; }

st.set({ connected: true, peers: [], controller: { online: true }, commands: [], nudges: {}, project: 'p', view: 'history',
  projects: [{ slug: 'p', name: 'p', peers: [], decisions: [], updated: iso(1) }, { slug: 'q', name: 'q', peers: [], decisions: [], updated: iso(2) }] });
let renders = 0;
H.HOOKS.rerender = () => { renders++; };

// ---------- 1 首屏未取数 / 取数后渲染 ----------
let html = H.renderHistory();
ok(html.includes('data-hist-view') && html.includes('任务历史'), 'renderHistory 真渲染出任务历史视图（未取数时不报错）');
ok(html.includes('aria-label="分页"') && html.includes('上一页</button>') && html.includes('disabled aria-disabled="true"'), '未取数：分页条存在且上一页禁用');
await H.loadHistory();
html = H.renderHistory();
ok(served.length === 1 && served[0].startsWith('/api/inbox/history?')  // 25 条，每页 20
  && served[0].includes('project=p') && served[0].includes('filter=all') && served[0].includes('limit=20'), '只请一次 /api/inbox/history，带 project / filter / limit');
ok((html.match(/class="card pad hist-item/g) || []).length === 20, '第一页渲染 20 条（页大小 20）');
ok(html.includes('已加载第 1–20 条 / 共 25 条，还不是完整历史'), '未到底：明确标出已加载范围且不说「完整」');
ok(html.includes('第 1 / 2 页 · 共 25 条'), '分页状态条：第 1 / 2 页 · 共 25 条');
ok(html.includes('data-hist-id="cid-d-p-1"') && !html.includes('AgentTeamWorkbench'), '列表项 id 用服务端 canonical_id，不含 workspace 目录名');
ok(html.includes('tabindex="0"') && html.includes('id="historyList" tabindex="-1"'), '列表与每项可聚焦（键盘可用）');

// ---------- 2 项目隔离 + 范围筛选 ----------
const keyP = H.dataKey();
ok(keyP === 'p|all', '数据键含当前项目：换项目即换页状态');
H.HISTORY_ACTIONS['hist-scope']({}, () => {});
await H.loadHistory();
html = H.renderHistory();
ok(served[served.length - 1].includes('limit=20') && !served[served.length - 1].includes('project=p'), '切到「全部项目」：请求不带 project（跨项目汇总）');
ok(html.includes('全部项目') && (html.match(/class="card pad hist-item/g) || []).length === 20, '全部项目口径照常渲染（30 条 → 首页 20）');
ok(html.includes('共 30 条'), '全部项目：总数从 25 变 30，口径独立');
ok(html.includes('class="chip ">q</span>') && html.includes('class="chip ">p</span>'), '全部项目视图逐条标出归属项目');
H.HISTORY_ACTIONS['hist-scope']({}, () => {});
await H.loadHistory();
const pgP = H.pageOf(H.histState(), H.dataKey());
ok(pgP.idx === 0 && H.histState().pages.size >= 2, '切回当前项目：页码回到第 1 页，两个口径各自持有自己的页状态');
ok(H.renderHistory().includes('第 1 / 2 页 · 共 25 条'), '切回当前项目：总数回到本项目口径的 25 条');

// ---------- 3 类型筛选 ----------
H.HISTORY_ACTIONS['hist-kind']({ value: 'commands' }, () => {});
await H.loadHistory();
html = H.renderHistory();
ok(served[served.length - 1].includes('filter=commands'), '类型筛选透传给服务端 filter=commands');
ok((html.match(/class="card pad hist-item/g) || []).length === 10 && html.includes('bus_collect · 关联任务 t16'), '本项目只剩 10 条已完成指令');
ok(html.includes('已到底：本口径共 10 条'), '单页口径：直接到底');
ok(html.includes('已完成') && !html.includes('决策 1'), '指令标题不混入决策');
H.HISTORY_ACTIONS['hist-kind']({ value: 'all' }, () => {});
await H.loadHistory();

// ---------- 4 翻页 / 游标不透明 / URL 前进后退 ----------
H.HISTORY_ACTIONS['hist-page']({ dataset: { dir: '1' } }, () => {});
await H.loadHistory();
html = H.renderHistory();
ok(served[served.length - 1].includes('cursor=c20'), '下一页带上一次下发的游标（客户端不解析它）');
ok((html.match(/class="card pad hist-item/g) || []).length === 5, '第二页 5 条');
ok(html.includes('已到底：本口径共 25 条，已全部载入') && !html.includes('还不是完整历史'), 'complete=true 才说「已全部载入」');
ok(navStack.length >= 1 && navStack[navStack.length - 1].includes('hpage=1') && globalThis.location.search.includes('hpage=1'), '翻页写 URL 且用 pushState（可后退）');
H.HISTORY_ACTIONS['hist-page']({ dataset: { dir: '-1' } }, () => {});
await H.loadHistory();
ok(H.pageOf(H.histState(), H.dataKey()).idx === 0 && !globalThis.location.search.includes('hpage'), '上一页回到第 1 页，URL 清掉 hpage');

// ---------- 5 410 过期：不静默显示空页，从头重取 ----------
const pgh = H.pageOf(H.histState(), H.dataKey());
pgh.resp = {};                            // 丢掉缓存，逼它真的带游标去取第 2 页
const before410 = served.length;
mode.expireOnce = true;
H.HISTORY_ACTIONS['hist-page']({ dataset: { dir: '1' } }, () => {});
await H.loadHistory();
const pg = H.pageOf(H.histState(), H.dataKey());
ok(served.length === before410 + 3, '410 之后确实重走了游标链（第 1 页 + 第 2 页各一次）');
ok(pg.idx === 1 && pg.resp[0] && pg.resp[1] && !pg.error, '游标过期：从头重走到原来那一页，不静默停在空页');
ok(H.histState().expired.includes('已按最新快照重取到第 2 页'), '过期重取在界面上说明原因');
const h2 = H.renderHistory();
ok(h2.includes('第 2 / 2 页') && (h2.match(/class="card pad hist-item/g) || []).length === 5, '过期重取后第 2 页照常渲染 5 条');

// ---------- 6 冻结页新增 / 移除，只有用户主动应用才换页 ----------
H.HISTORY_ACTIONS['hist-page']({ dataset: { dir: '-1' } }, () => {});
await H.loadHistory();
html = H.renderHistory();
ok(html.includes('data-hist-id="cid-d-p-1"'), '回到第 1 页：先建立冻结页');
const qi = DB.findIndex(r => r.project === 'p');
DB.splice(qi, 0, mkRec(99, 'decision', 'p'));             // 服务端在第 1 页位置多一条
mode.drop = 'cid-d-p-1';                                  // 并且原本第一条被「删掉」
await H.loadHistory(true);
html = H.renderHistory();
ok(html.includes('有 1 条新内容，1 条已移除 · 应用'), '冻结页差异提示：新增 1 + 移除 1');
ok(html.includes('data-hist-id="cid-d-p-1"') && html.includes('已从最新结果移除'), '未点应用前不漂页：删除项仍在列表里并标明');
ok(!html.includes('data-hist-id="cid-d-p-99"'), '新内容不自动挤进正在读的这页');
const rmv = H.computeView().view.removed;
const removedItem = rmv[0];
ok(removedItem && removedItem.title === '决策 1' && removedItem.time === iso(1) && removedItem.bucket === 'done' && removedItem.removed === true, 'reconcile 用 itemsById：删除项保留 title / time / bucket 快照，不只是断言 removed=true');
H.HISTORY_ACTIONS['hist-apply-new']({}, () => {});
html = H.renderHistory();
ok(html.includes('data-hist-id="cid-d-p-99"'), '用户主动应用后新内容进入列表');
ok(!html.includes('已从最新结果移除'), '应用后不再挂旧的删除项');
mode.drop = ''; DB = DB.filter(r => r.id !== 't99');

// ---------- 7 已读 / 未读 / 语义修订 ----------
H.HISTORY_ACTIONS['hist-unread']({}, () => {});
html = H.renderHistory();
ok(html.includes('只看未读 (20)') && (html.match(/class="card pad hist-item/g) || []).length === 20, '只看未读：全部未读时照常 20 条');
H.HISTORY_ACTIONS['hist-mark-page-read']({}, () => {});
html = H.renderHistory();
ok(html.includes('本页没有未读项'), '本页标为已读后未读视图为空');
ok(JSON.parse(localStorage.getItem('bus.histread') || '{}')['cid-d-p-2'] !== undefined, '已读写入 bus.histread（只在本机）');
H.HISTORY_ACTIONS['hist-unread']({}, () => {});
html = H.renderHistory();
ok(!html.includes('本页没有未读项') && (html.match(/hist-item unread/g) || []).length === 0, '取消只看未读：列表回来且都标为已读');
// 语义修订：同一条 status 变了 → revision 变 → 重新算未读
const read0 = H.histState().read;
H.histState().filters.unread = false;
const before = read0.isUnread({ id: 'cid-d-p-2', revision: 'r1' });
const di = DB.findIndex(r => r.canonical_id === 'cid-d-p-2');
DB[di] = { ...DB[di], revision: 2 };
await H.loadHistory(true);
const row = H.computeView().view.items.find(x => x.id === 'cid-d-p-2');
ok(before === false && row && row.changed === 1 && row.revision === 'r1', '冻结页里这条只标「已更新」，不偷偷把用户在读的内容换掉');
H.HISTORY_ACTIONS['hist-apply-new']({}, () => {});
const it2 = H.computeView().view.items.find(x => x.id === 'cid-d-p-2');
ok(it2.revision === 'r2' && read0.isUnread(it2) === true, '用户应用最新结果后按新 revision 重算：该条重新变未读');
H.histState().filters.unread = true;
DB[di] = { ...DB[di], revision: 1 };

// ---------- 8 本地存储失败要明说 ----------
lsFail = true;                       // 已有一条已读存在前一步的内存里，这里造写入失败
const r2 = H.readStore();
ok(r2.markPage([{ id: 'x', revision: 'r1' }]) === false && r2.status().degraded === true, 'readStore：localStorage 写失败返回 false 且 degraded=true');
H.histState().read = r2;
html = H.renderHistory();
ok(html.includes('本地存储写入失败（配额或隐私模式），已读状态只在本次会话有效，刷新后会恢复未读'), '本地存储失败：界面上明确警示');
H.histState().read = H.readStore();
lsFail = false;

// ---------- 9 失联 / 读失败：保留上次快照 ----------
const snapshotBefore = (H.computeView().resp || {}).total;
mode.failNext = true;
await H.loadHistory(true);
html = H.renderHistory();
ok(H.computeView().resp && H.computeView().resp.total === snapshotBefore, '读失败：保留上次成功快照（不清空、不换空页）');
ok(html.includes('本口径读取失败：boom（保留上次成功页，未清空列表）') && html.includes('data-action="hist-reload"'), '读失败：横幅 + 重试按钮');
st.set({ connected: false });
html = H.renderHistory();
ok(html.includes('工作台失联：下面是上次成功取到的快照，内容可能过期'), '失联横幅');
st.set({ connected: true });

// ---------- 10 键盘：上下键移焦点，Enter 打开详情 ----------
const opened = [];
H.HOOKS.openDecision = id => opened.push(['decision', id]);
H.HOOKS.openCommand = id => opened.push(['command', id]);
H.HOOKS.setProject = slug => { st.set({ project: slug }); };
const btn = { dataset: { item: 'cid-d-p-5', kind: 'decision', id: 't5', project: 'p' } };
const li = { classList: { add() {}, remove() {} }, closest: sel => (sel === '.hist-item' ? li : null), querySelector: () => ({ click() {} }), parentElement: { querySelectorAll: () => [li, 'x'] } };
let moved = '';
const next = { focus: () => { moved = 'next'; } };
li.parentElement = { querySelectorAll: () => [li, next] };
let prevented = false;
ok(H.historyKeydown({ target: li, key: 'ArrowDown', preventDefault: () => { prevented = true; } }) === true && moved === 'next' && prevented, 'ArrowDown 把焦点移到下一条并吃掉默认滚动');
ok(H.historyKeydown({ target: li, key: 'ArrowUp', preventDefault() {} }) === false, '第一条按 ArrowUp 不越界');
li.focus = () => {};
globalThis.document.activeElement = li;
let clicked = 0;
li.querySelector = () => ({ click: () => { clicked++; } });
ok(H.historyKeydown({ target: li, key: 'Enter', preventDefault() {} }) === true && clicked === 1, 'Enter 在聚焦的列表项上打开详情');
globalThis.document.activeElement = null;
ok(H.historyKeydown({ target: li, key: 'Enter', preventDefault() {} }) === false, '焦点不在列表项上时 Enter 不抢浏览器默认行为');
ok(H.historyKeydown({ target: { closest: () => null }, key: 'ArrowDown', preventDefault() {} }) === false, '列表外的按键不拦截');

// ---------- 11 动作卡：跨项目先切项目，命令行走 command 抽屉，并标已读 ----------
const read = H.histState().read;
read.clear();
const before5 = read.isUnread({ id: 'cid-d-p-5', revision: 'r1' });
H.HISTORY_ACTIONS['hist-open']({ dataset: { item: 'cid-d-p-5', kind: 'decision', id: 't5', project: 'q', rev: 'r1' } }, () => {});
ok(S_proj() === 'q' && opened[0] && opened[0][0] === 'decision' && opened[0][1] === 't5', '跨项目历史项：先切项目再开决策（否则只会「找不到这条决策」）');
H.HISTORY_ACTIONS['hist-open']({ dataset: { item: 'cid-c-p-16', kind: 'command', id: 'c16', project: '' } }, () => {});
ok(opened[1] && opened[1][0] === 'command', '指令行走指令抽屉');
ok(before5 === true && read.isUnread({ id: 'cid-d-p-5', revision: 'r1' }) === false, '打开详情即标已读（写的是本机状态，不动任务）');
function S_proj() { return st.get().project; }

// ---------- 12 不覆盖 hub 外部来源收件箱 ----------
const hub = readFileSync(join(repo, 'web/v2/js/hub.js'), 'utf8');
const viewsInbox = (() => { try { return readFileSync(join(repo, 'web/v2/js/views_inbox.js'), 'utf8'); } catch (_) { return ''; } })();
ok(!viewsInbox, '旧 views_inbox.js 整页没有被复活');
ok(hub.includes('renderInbox') && hub.includes('intake'), 'hub#inbox（外部来源收件箱）仍在原位');
ok(!hub.includes('hist-') && !hub.includes('/api/inbox/history'), 'hub.js 未被任务历史改动污染');
ok(!html.includes('data-inbox-view') && !H.HIST_URL.includes('intake'), '任务历史用自己的 data-hist-view，不抢收件箱容器');
const appjs = frontSrc('app.js');
ok(/history: renderHistory/.test(appjs) && /histState, readUrlFilters \} from '\.\/history\.js'/.test(appjs), 'app.js 只登记视图 + 合并动作（逻辑都在 history.js，不往 app.js 堆功能）');
const busweb = readFileSync(join(repo, 'bus_web.py'), 'utf8');
ok(/if u\.path == "\/api\/inbox\/history"[\s\S]{0,900}127\.0\.0\.1/.test(busweb) && busweb.includes('history_local_only'), '路由边界：/api/inbox/history 只放行本机回环，否则 403');
ok(/IN\.items\(q\.get\("status"/.test(busweb), '外部来源收件箱的 /api/intake 分支没被动过');

// ---------- 13 筛选进 URL / 从 URL 还原（刷新与后退）----------
H.histState().filters.unread = false;
H.HISTORY_ACTIONS['hist-kind']({ value: 'decisions' }, () => {});
// ---------- 14 终态口径只有一份：服务端下发 terminal_statuses，前端跟着变 ----------
const M = await mod('web/v2/js/model.js');
st.set({ ...st.get(), projects: [{ slug: 'p', name: 'p', peers: [], updated: iso(9), decisions: [
  { task_id: 'x1', status: 'retired', title: '新终态', created: iso(9), updated: iso(9), history: [] },
  { task_id: 'x2', status: 'collecting', title: '在办', created: iso(9), updated: iso(9), history: [] }] }], project: 'p' });
ok(M.terminalStatuses().join(',') === M.TERMINAL_FALLBACK.join(','), '服务端没下发时用兜底列表');
st.set({ ...st.get(), terminal_statuses: ['merged', 'adopted', 'retired'] });
ok(M.terminalStatuses().includes('retired'), '服务端下发什么口径界面就用什么');
ok(M.doneDecisions().map(d => d.task_id).join(',') === 'x1', 'doneDecisions 走下发口径：retired 算终态、collecting 不算');
st.set({ ...st.get(), terminal_statuses: [] });
ok(!M.doneDecisions().some(d => d.task_id === 'x1'), '空下发回落兜底列表，不会把已完成清空');
ok(H.histBucket({ kind: 'decision', status: 'collecting' }) === 'other' && H.histBucket({ kind: 'decision', status: 'merged' }) === 'done' && H.histBucket({ kind: 'decision', status: 'rejected' }) === 'closed' && H.histBucket({ kind: 'command', status: 'failed' }) === 'failed', '分桶：done / closed / failed / other 各自对上');
st.set({ ...st.get(), terminal_statuses: ['retired'] });
ok(H.histBucket({ kind: 'decision', status: 'retired' }) === 'done' && H.histBucket({ kind: 'decision', status: 'merged' }) === 'other', '分桶跟着下发口径走：新终态进「已完成」，未列出的不再算完成');
st.set({ ...st.get(), terminal_statuses: M.TERMINAL_FALLBACK, project: 'p' });

H.HISTORY_ACTIONS['hist-unread']({}, () => {});
ok(globalThis.location.search.includes('hkind=decisions') && globalThis.location.search.includes('hunread=1'), '筛选写进 URL');
H.histState().filters.kind = 'all'; H.histState().filters.unread = false;
H.readUrlFilters(H.histState());
ok(H.histState().filters.kind === 'decisions' && H.histState().filters.unread === true, '从 URL 还原筛选（刷新 / 后退不丢口径）');
H.histState().filters.kind = 'all'; H.histState().filters.unread = false;

console.log(fails ? `\n${fails} FAILED` : '\nALL PASS');
process.exit(fails ? 1 : 0);
