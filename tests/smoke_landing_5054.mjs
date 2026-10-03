// 任务 5054 前端冒烟（验收 C）：不起服务，复制 web/v2/js 到临时目录（pretext 打桩），最小 DOM 垫片渲染概览，检查 builder 零在线时的文案与假 building 显示。跑法：node tests/smoke_landing_5054.mjs
import { mkdtempSync, mkdirSync, cpSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const repo = process.cwd();
const tmp = mkdtempSync(join(tmpdir(), 'bus5054-ui-'));
cpSync(join(repo, 'web/v2/js'), join(tmp, 'web/v2/js'), { recursive: true });
mkdirSync(join(tmp, 'web/v2/vendor/pretext'), { recursive: true });
cpSync(join(repo, 'web/v2/vendor/idiomorph.esm.js'), join(tmp, 'web/v2/vendor/idiomorph.esm.js'));
writeFileSync(join(tmp, 'web/v2/vendor/pretext/layout.js'), 'export const prepare = () => ({}); export const layout = () => ({ lines: [] });');
process.on('exit', () => rmSync(tmp, { recursive: true, force: true }));
const store = new Map();
globalThis.localStorage = { getItem: k => store.has(k) ? store.get(k) : null, setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k) };
globalThis.location = { search: '', pathname: '/', hash: '', href: 'http://x/' };
globalThis.history = { state: null, pushState() {}, replaceState() {} };
globalThis.matchMedia = () => ({ matches: false });
globalThis.requestAnimationFrame = fn => fn();
globalThis.document = { querySelector: () => null, querySelectorAll: () => [], createElement: () => ({ style: {}, classList: { add() {}, remove() {} }, setAttribute() {}, querySelector: () => ({}) }), activeElement: null, body: { style: {} }, addEventListener() {}, hidden: false };
globalThis.window = globalThis;
const mod = rel => import(pathToFileURL(join(tmp, rel)).href);
const { store: st } = await mod('web/v2/js/model.js');
const V = await mod('web/v2/js/views.js');
const now = new Date(); const iso = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
const dec = (id, im) => ({ task_id: id, status: 'adopted', execution: 'pending', title: '任务' + id, created: iso(now), history: [], implementation: im });
const state = { connected: true, peers: [], controller: { online: true, status: 'idle' }, commands: [], nudges: {}, project: 'p', view: 'overview',
  builders: { groups: ['claude-fable-5'], members: [{ peer: 'f1', online: false, last_seen: iso(new Date(now - 3600000)) }], online_n: 0 },
  projects: [{ slug: 'p', name: 'p', peers: [], decisions: [
    dec('a', { state: 'unassigned', label: '未派发 · 等 Leader 排单', blocked: true, wait_sec: 0, queue_pos: 0 }),
    dec('b', { state: 'queued', label: '排队中 · 第 1 位 · 已等 12 分钟', blocked: true, wait_sec: 720, queue_pos: 1 }),
    dec('c', { state: 'dispatched', label: '已建单，等待 builder 领取', blocked: true, wait_sec: 1800, queue_pos: 0 }),
    dec('d', { state: 'review', label: '待审 · f1 · 0123456', blocked: false, peer: 'f1', commit: '0123456' }),
    dec('e', { state: 'merged', label: '已合并 · f1 · fedcba9', blocked: false, peer: 'f1', merge_commit: 'fedcba9' }),
  ] }] };
st.set(state);
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
const h = V.renderOverview(state.projects[0]);
ok(h.includes('已采纳，等待落地'), '区块存在');
ok(h.includes('4 项 · 待派 2 · 等 builder 1（最长 30 分钟） · 在建 0 · 待审 1'), '计数句：' + (h.match(/\d 项 · 待派[^<]*/) || [''])[0]);
ok(!h.includes('正在派 build') && !h.includes('施工中'), 'builder 零在线：无「正在派」「施工中」');
ok(h.includes('已建单，等待 builder 领取'), '假 building 显示为已建单等待领取');
ok(h.includes('无在线 builder，任务已入队') && h.includes('f1 离线'), '受阻横幅列出 builder 组成员与 last_seen');
ok(h.includes('data-action="implement-nudge"') && h.includes('已在队列 · 第 1 位') && h.includes('派落地给 builder'), '派落地按钮态随状态变化');
ok(h.includes('data-action="export-task-pack"'), '导出任务包按钮');
ok(h.includes('已合并进主分支、待 Leader 回写 landed：任务e（fedcba9）'), 'merged 项移出待落地并列出');
ok(!h.includes('可以手动切换') && h.includes('不会触发施工'), '误导文案已删，改为说明标记不触发施工');
ok(!h.includes('data-action="set-execution"'), '待落地区块无 set-execution 控件');
const cards = V.renderDecisions(state.projects[0]);
ok(!cards.includes('data-action="set-execution"'), '决策卡头部不再有 set-execution 按钮（移到详情抽屉）');
console.log(fails ? `\n${fails} FAILED` : '\nALL PASS'); process.exit(fails ? 1 : 0);
