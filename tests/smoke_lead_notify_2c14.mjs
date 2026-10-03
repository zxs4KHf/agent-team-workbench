// 2c14 前端冒烟：不起服务，把 web/v2/js 复制到临时目录（pretext 打桩），最小 DOM 垫片验证
// 交接常驻通知的渲染/关闭/详情，以及 stateKey 对新维度的覆盖。跑法：node tests/smoke_lead_notify_2c14.mjs（仓库根目录）
import { mkdtempSync, mkdirSync, cpSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」

const repo = process.cwd();
const tmp = mkdtempSync(join(tmpdir(), 'bus2c14-ui-'));
cpSync(join(repo, 'web/v2/js'), join(tmp, 'web/v2/js'), { recursive: true });
mkdirSync(join(tmp, 'web/v2/vendor/pretext'), { recursive: true });
cpSync(join(repo, 'web/v2/vendor/idiomorph.esm.js'), join(tmp, 'web/v2/vendor/idiomorph.esm.js'));
writeFileSync(join(tmp, 'web/v2/vendor/pretext/layout.js'), 'export const prepare = () => ({}); export const layout = () => ({ lines: [] });');
process.on('exit', () => rmSync(tmp, { recursive: true, force: true }));
const mod = rel => import(pathToFileURL(join(tmp, rel)).href);

const store = new Map();
globalThis.localStorage = { getItem: k => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k) };
globalThis.location = { search: '', pathname: '/', hash: '', href: 'http://x/' };
globalThis.history = { state: null, pushState() {}, replaceState() {} };
globalThis.matchMedia = () => ({ matches: false });
globalThis.requestAnimationFrame = fn => fn();
globalThis.document = { querySelector: () => null, querySelectorAll: () => [], createElement: () => ({ style: {}, classList: { add() {}, remove() {} }, setAttribute() {}, querySelector: () => ({}) }), activeElement: null, body: { style: {} }, addEventListener() {}, hidden: false };
globalThis.window = globalThis;

let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };

const { store: st, S, displayName } = await mod('web/v2/js/model.js');
const src = frontSrc('app.js');

const iso = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
const now = new Date();
const ev = (o = {}) => ({ id: 'e1', at: iso(now), first_at: iso(now), from: 'controller', to: 'glm1', reason: 'takeover', epoch: 7, prompt: 'RELAY_TAKEOVER: 你现在是代理 Leader（epoch 7）。', count: 1, acked: false, unstable: false, recent_total: 1, ...o });

// ---------- 1. displayName 走 peers[].label ----------
st.set({
  peers: [{ peer: 'glm1', model: 'glm-5.3', status: 'idle', online: true, label: '小队长' }, { peer: 'controller', model: 'controller', status: 'idle', online: true, label: '' }],
  projects: [{ slug: 'p1', name: 'P1', peers: [], decisions: [] }], project: 'p1', view: 'overview',
  lease: { holder: 'glm1', epoch: 7 }, lead_events_open: [ev()], features: { lead_label: true, lead_events: true },
  commands: [], controller: { online: true }, priority: {}, relay_resolution: {},
});
ok(displayName('glm1') === '小队长', `displayName 取 peers[].label：${displayName('glm1')}`);
ok(displayName('controller') === 'controller', 'label 为空时回落真名');
ok(displayName('nosuch') === 'nosuch', '未知 peer 回落原字符串');

// ---------- 2. agent_meta 优先级高于 label（项目级显式别名不被覆盖） ----------
st.set({ projects: [{ slug: 'p1', name: 'P1', peers: [], decisions: [], agent_meta: { glm1: { display_name: '项目内别名' } } }] });
ok(displayName('glm1') === '项目内别名', 'agent_meta.display_name 优先于 peers[].label');
st.set({ projects: [{ slug: 'p1', name: 'P1', peers: [], decisions: [] }] });

// ---------- 3. renderLeadEvents：从源码抽出来单测 ----------
const rle = src.match(/function renderLeadEvents\(s\) \{[\s\S]*?\n\}/);
ok(!!rle, '从 app.js 抽到 renderLeadEvents');
const lr = src.match(/const LEAD_REASON = \{[^}]*\};/);
ok(!!lr, '从 app.js 抽到 LEAD_REASON');
const stub = `
${lr[0]}
const $ = () => box;
const morph = (el, html) => { el.innerHTML = html; captured.push(html); };
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const attr = esc;
const icon = () => '<svg></svg>';
const relTime = () => '刚刚';
const displayName = n => ({ glm1: '小队长', controller: 'controller' }[n] || n);
${rle[0]}
return { renderLeadEvents, box, captured };`;
function run(state) {
  const box = { hidden: false, innerHTML: '' };
  const cap = [];
  const f = new Function('box', 'captured', stub)(box, cap);
  f.renderLeadEvents(state);
  return box;
}

const base = { features: { lead_events: true }, lease: { holder: 'glm1', epoch: 7 } };
let box = run({ ...base, lead_events_open: [ev()] });
ok(box.hidden === false, '有未 ack 事件时 banner 显示');
ok(box.innerHTML.includes('Leader 从 <b>controller</b> 变为 <b>小队长</b>'), '文案用显示名且方向正确');
ok(!box.innerHTML.includes('已恢复') && !box.innerHTML.includes('resolved'), '租约 epoch 未前进时不算已恢复');
ok(box.innerHTML.includes('代理接管'), 'reason 文案已中文化');
ok(box.innerHTML.includes('epoch 7'), '带 epoch');
ok(box.innerHTML.includes('data-action="lead-ack"') && box.innerHTML.includes('data-id="e1"'), '有 × 关闭按钮并带 id');
ok(box.innerHTML.includes('data-action="lead-detail"'), '卡片本身是详情入口（点击才开）');
ok(!/setTimeout|autoclose|toast/i.test(box.innerHTML), '不是自动消失的 toast');

box = run({ ...base, lead_events_open: [] });
ok(box.hidden === true, '无事件时 banner 隐藏');

box = run({ ...base, features: { lead_events: false }, lead_events_open: [ev()] });
ok(box.hidden === true, 'features.lead_events=false 时整个 banner 降级不渲染');

box = run({ ...base, lead_events_open: [ev(), ev({ id: 'e2', epoch: 8 })] });
ok(box.innerHTML.includes('data-action="lead-ack-all"') && box.innerHTML.includes('全部关闭'), '多条时给「全部关闭」');
ok(box.innerHTML.includes('2 条 Leader 交接'), '多条时给计数抬头');

box = run({ ...base, lease: { holder: 'glm1', epoch: 9 }, lead_events_open: [ev()] });
ok(box.innerHTML.includes('lead-event resolved') && box.innerHTML.includes('已恢复，现在由 <b>小队长</b> 负责'), '租约已前进且有人持有：降为已恢复');
ok(box.innerHTML.includes('data-action="lead-ack"'), '已恢复的仍需手动关闭，不自动消失');
box = run({ ...base, lease: { holder: '', epoch: 9 }, lead_events_open: [ev()] });
ok(!box.innerHTML.includes('已恢复'), '租约无人持有时不算已恢复');

box = run({ ...base, lead_events_open: [ev({ count: 4, unstable: true, recent_total: 6 })] });
ok(box.innerHTML.includes('Leader 不稳定') && box.innerHTML.includes('6 次'), 'unstable 时切「Leader 不稳定」文案');
ok(box.innerHTML.includes('×4'), 'count > 1 时显示聚合次数');

box = run({ ...base, lead_events_open: [ev({ to: '' })] });
ok(box.innerHTML.includes('变为 <b>无人</b>'), 'to 为空（guard 强删）显示为「无人」');

// XSS：label 与 prompt 都必须转义，不能成为可执行标记
box = run({ ...base, lead_events_open: [ev({ from: '<img src=x onerror=alert(1)>' })] });
ok(!box.innerHTML.includes('<img src=x'), '事件字段经过转义，注入不成为标记');
ok(box.innerHTML.includes('&lt;img'), '转义后以文本呈现');

// ---------- 4. stateKey 覆盖新维度 ----------
const m = src.match(/function stateKey\(s\) \{[\s\S]*?\n\}/);
ok(!!m, '从 app.js 抽到 stateKey');
const stateKey = new Function(m[0] + '; return stateKey;')();
const b = { peers: [{ peer: 'a', label: '' }], projects: [{ slug: 'p', decisions: [{ task_id: 'a' }] }], commands: [], controller: { online: true }, lease: {}, priority: {} };
const k0 = stateKey(b);
ok(stateKey({ ...b, lead_events_open: [{ id: 'e1', count: 1, acked: false }] }) !== k0, '新事件改变 key');
ok(stateKey({ ...b, lead_events_open: [{ id: 'e1', count: 2, acked: false }] }) !== stateKey({ ...b, lead_events_open: [{ id: 'e1', count: 1, acked: false }] }), 'count 变化改变 key');
ok(stateKey({ ...b, peers: [{ peer: 'a', label: '新名' }] }) !== k0, '改名改变 key');
ok(stateKey({ ...b, lead_events_open: [] }) === k0, '空事件列表与缺失等价，不抖动');
ok(stateKey({ ...b, lead_events_open: undefined }) === k0, 'undefined 不抖动');

// ---------- 5. 详情抽屉：当前权威取 lease 实时值，不由事件推断 ----------
const od = src.match(/function openLeadDetail\(id\) \{[\s\S]*?\n\}/);
ok(!!od, '从 app.js 抽到 openLeadDetail');
ok(/S\(\)\.lease/.test(od[0]), '当前合并权威读 S().lease 实时真值');
ok(!/e\.to.*当前合并权威/.test(od[0]), '不从事件台账推断当前权威');
ok(od[0].includes('已脱敏，不含 token'), '详情里标注提示词已脱敏');
ok(od[0].includes('data-action="copy-text"'), '复制按钮复用既有 copy-text 动作');
ok(od[0].includes('真实 peer'), '详情同时展示真实 peer 名');

// ---------- 6. ACTIONS 挂载与订阅 ----------
ok(/'lead-ack':/.test(src) && /'lead-ack-all':/.test(src) && /'lead-detail':/.test(src), '三个 action 已注册');
ok(/'rename-peer':/.test(src), 'rename-peer action 已注册');
ok(/\/api\/lead_event\/ack/.test(src), 'ack 走服务端接口，不是 localStorage');
ok(/\/api\/peer\/label/.test(src), '改名走服务端接口');
ok(/store\.on\(\['lead_events_open', 'features', 'lease'\], s => renderLeadEvents\(s\)\)/.test(src), '通知单独订阅，不触发整页 renderView');
ok(/e\?\.stopPropagation\(\)/.test(src), '× 按钮阻止冒泡，避免同时打开详情');

console.log(fails ? `\n${fails} FAILED` : '\nALL PASS');
process.exit(fails ? 1 : 0);
