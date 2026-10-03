// 任务 5b61 前端冒烟：Leader 身份看板、任务卡 hover 手柄 + P0/P1/P2、权重下拉、/api/priority 提交语义、stateKey 新维度。
// 跑法：node tests/smoke_priority_ui_5b61.mjs（仓库根目录）
import { mkdtempSync, mkdirSync, cpSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」
const repo = process.cwd();
const tmp = mkdtempSync(join(tmpdir(), 'bus5b61-ui-'));
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
const { store: st } = await mod('web/v2/js/model.js');
const M = await mod('web/v2/js/model.js');
const V = await mod('web/v2/js/views.js');
const V2 = await mod('web/v2/js/views2.js');
const now = '2026-09-24 00:50:00';
const mk = (id, status = 'collecting') => ({ task_id: id, status, title: id, created: now, updated: now, peers: ['f1'], progress: { expected: ['f1'], answered: [], states: { f1: 'working' }, meta: { created: now, ttl_min: 30 } } });
const base = {
  time: now, connected: true, lastRefresh: 0, history: [],
  peers: [{ peer: 'f1', model: 'claude-fable-5', status: 'idle', online: true, last_seen: now }, { peer: 'f2', model: 'glm-5.3', status: 'idle', online: true, last_seen: now }],
  controller: { online: true, status: 'idle', age_sec: 3 },
  projects: [{ slug: 'p', name: 'p', peers: ['f1'], decisions: [mk('t-a'), mk('t-b'), mk('t-c'), mk('t-d')] }],
  project: 'p', commands: [], nudges: {}, teams: [],
  priority: { version: 2, relay_order: [], relay_order_meta: [], rev: 4, relay_policy: { strategy: 'recent' }, task_order: ['t-c', 't-a', 't-b'], agent_weight: { f1: '3' } },
  lease: { holder: 'controller', epoch: 12, expires: '2026-09-24 00:55:00' },
  relay_resolution: { items: [], pick: '', via_group: '', fallback: false, fallback_peers: [], refused: false }
};
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };

// ---------- 1 任务卡：hover 手柄 + P0/P1/P2 ----------
st.set({ ...base });
const run = V.renderRunning(base.projects[0]);
ok(/class="drag-handle task-handle" draggable="true" data-task-drag/.test(run), '每张任务卡带 hover 拖拽手柄（draggable + data-task-drag）');
ok(run.includes('data-tier="P0"') && run.includes('data-tier="P1"') && run.includes('data-tier="P2"'), 'task_order 前三位渲染 P0 / P1 / P2');
ok((run.match(/data-tier="/g) || []).length === 3, '未列入 task_order 的第四张没有等级标记');
const orderInDom = [...run.matchAll(/data-key="(t-[a-d])"[^>]*>([\s\S]*?)data-tier="(P\d)"/g)].map(m => [m[1], m[3]]);
ok(JSON.stringify(orderInDom) === JSON.stringify([['t-c', 'P0'], ['t-a', 'P1'], ['t-b', 'P2']]), '列表按 task_order 排序且等级与位置一致');
const css = readFileSync(join(repo, 'web/v2/styles/components.css'), 'utf8');
ok(/\.task \.task-handle\{[^}]*opacity:0/.test(css), 'CSS：手柄默认 opacity:0');
ok(/\.task:hover \.task-handle,\.task:focus-within \.task-handle\{opacity:1\}/.test(css), 'CSS：hover 或聚焦才显出手柄');
// 拖动落点：.task 走 key，且只放行 #taskList
const src = frontSrc('app.js');
ok(/closest\?\.\('\.group-section, \.relay-item, \.task'\)/.test(src), 'dragstart 认领 .task');
ok(/'\.task': 'key'/.test(src) && /sec\.matches\('\.relay-item'\) \? '\.relay-item' : '\.task'/.test(src), 'DRAG.attr 把 .task 映射到 data-key');
ok(/kind === '\.task'\) \{ DRAG\.lastEnd = Date\.now\(\); return commitTaskOrder\(mergeVisibleOrder\(taskOrder\(\), ids\)\)/.test(src), 'dragend 把可见顺序合回完整表后交给 commitTaskOrder（cfa6：不再整表用 DOM 子集覆盖）');
ok(/#groupSections, #relayList, #taskList/.test(src), 'dragover 放行 #taskList');
ok(/data-action="toggle-task"/.test(src) || run.includes('data-action="toggle-task"'), '手柄所在行仍可展开');
ok(!/\[data-task-drag\]'\)\) e\.stopPropagation\(\)/.test(src) && src.includes("if (e.target.closest?.('[data-task-drag]')) return;"), '不再在 document 捕获阶段吞 click；手柄点击只在委托里被排除（cfa6）');

// ---------- 2 权重下拉在团队抽屉里 ----------
ok(/function weightRows\(\)/.test(src) && src.includes('data-agent-weight'), 'app.js 渲染权重下拉出口 data-agent-weight');
ok(/body: `<form data-form="teams"[\s\S]*?\$\{weightRows\(\)\}`/.test(src), '权重下拉随编辑 Team 抽屉一起打开');
ok(src.includes('WEIGHT_OPTIONS') && M.WEIGHT_OPTIONS.length === 4 && M.WEIGHT_OPTIONS[0][0] === '', '权重选项：默认 / 低 / 中 / 高');
ok(/t\.dataset\.agentWeight\) \{ saveAgentWeight\(t\.dataset\.agentWeight, t\.value\)/.test(src), 'change 委托把权重下拉接到 saveAgentWeight');
ok(M.agentWeight('f1', base.projects.length ? st.get() : {}) === '3' || M.agentWeight('f1') === '3', 'agentWeight 读出服务端值');

// ---------- 3 leaderBoard：归属与倒计时 ----------
st.set({ ...base, lease: { holder: 'controller', epoch: 12, expires: '2026-09-24 00:55:00' } });
let board = V2.leaderBoard();
ok(board.includes('Leader 在任') && !board.includes('接管中'), 'holder 是 controller：显示在任');
ok(/class="chip ok"/.test(board) && board.includes('epoch 12') && board.includes('data-countdown='), '在任态：绿色 chip + epoch + 倒计时锚点');
st.set({ ...base, lease: { holder: 'f2', epoch: 13, expires: '2026-09-24 00:52:00' } });
board = V2.leaderBoard();
ok(board.includes('接管中：f2') && /chip warn/.test(board) && /class="card pad leader-board warn"/.test(board), 'holder 非 controller：转琥珀并显示接管中：X');
ok(/data-action="lead-handback" (?!disabled)/.test(board), '代理持有租约时「收回 Leader」可点（f4f5：/api/handback 已接上）');
ok(!/leaseCard/.test(board), '不使用 2d14 的 leaseCard 符号');
st.set({ ...base, lease: { holder: 'controller', epoch: 14, expires: '2026-09-24 00:52:00' } });
ok(/data-action="lead-handback" disabled/.test(V2.leaderBoard()), '真 Leader 持有租约时「收回 Leader」禁用');
st.set({ ...base, lease: {} });
board = V2.leaderBoard();
ok(board.includes('无有效租约') && board.includes('无人接管'), '无租约：明确显示无人接管');
// 多活（2026-10-01）：每位 Leader 一行、按会话号做 key；新接入者是多出一行，不是把名字换掉
const two = [{ session: 'aaaa1111', label: 'opus · A', activity: 'idle', age_sec: 3, joined: '2026-10-01T10:00:00-04:00', claimed: [] },
             { session: 'bbbb2222', label: 'fable · B', activity: 'idle', age_sec: 1, joined: '2026-10-01T10:05:00-04:00', claimed: ['c1', 'c2'] }];
st.set({ ...base, lease: { holder: 'controller', epoch: 12, expires: '2026-09-24 00:55:00' }, leaders: two, leader_queue: { pending: 3, claimed: 2, orphaned: 4 } });
board = V2.leaderBoard();
ok(board.includes('2 位 Leader 在线') && (board.match(/class="leader-row"/g) || []).length === 2, '两位 Leader：标题写人数、各占一行');
ok(board.includes('data-key="aaaa1111"') && board.includes('data-key="bbbb2222"') && board.includes('opus · A') && board.includes('fable · B'), '每行带会话 key 与各自显示名');
ok(/data-key="bbbb2222" data-state="busy"/.test(board) && board.includes('正在处理 2 条指令'), '手上有指令的 Leader 标为忙');
ok(board.includes('4 条被已断开的会话领走') && board.includes('排队 <b>3</b>'), '孤儿指令与排队数可见');
st.set({ leaders: [...two, { session: 'cccc3333', label: 'gpt · C', age_sec: 0, joined: '2026-10-01T10:09:00-04:00', claimed: [] }] });
const three = V2.leaderBoard();
ok(three.includes('3 位 Leader 在线') && three.includes('opus · A') && three.includes('gpt · C'), '第三位接入：多一行，原来的两位还在');
// 倒计时换算
ok(M.fmtLeft(M.secondsLeft('2026-09-24 00:55:00', now)) === '5 分 0 秒', 'secondsLeft + fmtLeft：分钟格式');
ok(M.secondsLeft('2026-09-24T00:55:00', '2026-09-24T00:50:00') === 300, '后端 now_iso 的 T 分隔格式同样可解析');
ok(M.fmtLeft(M.secondsLeft('2026-09-24 00:49:00', now)) === '已到期' && M.fmtLeft(null) === '—', '到期与缺字段各有显示');

// ---------- 4 stateKey：新维度进 key，base 保持稳定 ----------
const stateKeyFn = new Function(src.match(/function stateKey\(s\) \{[\s\S]*?\n\}/)[0] + '; return stateKey;')();
const stateGroupsFn = new Function(src.match(/function stateKey\(s\) \{[\s\S]*?\n\}/)[0].replace('function stateKey', 'function stateGroupsForTest').replace(/return `\$\{base\}[^\n]*;/, 'return { base, order, leader };') + '; return stateGroupsForTest;')();
const k = s => stateKeyFn({ peers: [], projects: [], commands: [], controller: {}, ...s });
ok(k({ priority: { rev: 4, task_order: ['a'] } }) !== k({ priority: { rev: 4, task_order: ['a', 'b'] } }), 'task_order 变化改 key');
ok(k({ priority: { rev: 4, agent_weight: {} } }) !== k({ priority: { rev: 4, agent_weight: { f1: '3' } } }), 'agent_weight 变化改 key');
ok(k({ lease: { holder: 'controller', epoch: 1, expires: 'x' } }) !== k({ lease: { holder: 'f2', epoch: 1, expires: 'x' } }), '租约 holder 变化改 key');
ok(k({ priority: { rev: 4, task_order: ['a'], agent_weight: { f1: '3' } }, lease: { holder: 'controller', epoch: 1, expires: 'x' }, settings: { a: 1 }, time: 't1' }) === k({ priority: { rev: 4, task_order: ['a'], agent_weight: { f1: '3' } }, lease: { holder: 'controller', epoch: 1, expires: 'x' }, settings: { a: 2 }, time: 't2' }), '与调度无关的字段变化不改 key');
const g = s => stateGroupsFn({ peers: [], projects: [], commands: [], controller: {}, ...s });
const plain = { peers: base.peers, projects: base.projects, commands: [], controller: {}, priority: { rev: 1 }, lease: {} };
ok(g({ ...plain, priority: { rev: 2, task_order: ['t-a'] } }).base === g(plain).base, '只有 order 维度变：base 指纹不变（可走局部 morph）');
ok(g({ ...plain, lease: { holder: 'f2' } }).base === g(plain).base && g({ ...plain, lease: { holder: 'f2' } }).leader !== g(plain).leader, '只有 leader 维度变：base 不变、leader 变');
ok(/lastGroups\.base === g\.base/.test(src) && /minorOnly/.test(src), 'refresh 里按 base 是否变化决定整页重绘还是局部 morph');
ok(/store\.on\(\['priority', 'lease'\], s => renderScoped\(s\)\)/.test(src), 'priority / lease 单独订阅到 renderScoped');
ok(/renderScoped/.test(src) && /#taskList/.test(src) && /#leaderBoard/.test(src), 'renderScoped 只 morph 看板与任务列表');
ok(/setInterval\(tickCountdown, 1000\)/.test(src), '倒计时每秒自走一格');

// ---------- 5 /api/priority 提交语义 ----------
const mkCommit = (responses) => {
  const env = { calls: [], toasts: [], sets: [], refreshed: 0 };
  const apiStub = async (url, opt) => { env.calls.push({ url, body: JSON.parse(opt.body) }); const r = responses.shift(); if (r instanceof Error) throw r; return r; };
  const fn = new Function('S', 'api', 'toast', 'store', 'refresh', 'displayName', 'agentWeights', 'env',
    src.match(/async function commitPriority\(patch[\s\S]*?\n\}/)[0] + '\n' +
    src.match(/const commitTaskOrder = [^\n]*\n/)[0] + src.match(/const saveAgentWeight = [^\n]*\n/)[0] +
    '; env.commitTaskOrder = commitTaskOrder; env.saveAgentWeight = saveAgentWeight; return env;');
  return fn(() => ({ priority: { rev: 4, agent_weight: { f1: '3' } } }), apiStub,
    (msg, tone) => env.toasts.push([msg, tone]), { set: p => env.sets.push(p) }, async () => { env.refreshed++; },
    n => '别名-' + n, () => ({ f1: '3' }), env);
};
// 5.1 拖拽提交 task_order：整表带 rev
let env = mkCommit([{ rev: 5, task_order: ['t-b', 't-a', 't-c'] }]);
await env.commitTaskOrder(['t-b', 't-a', 't-c']);
ok(env.calls[0].url === '/api/priority' && JSON.stringify(env.calls[0].body.task_order) === JSON.stringify(['t-b', 't-a', 't-c']) && env.calls[0].body.rev === 4, '拖拽后整表提交 task_order 并带 rev');
ok(env.sets[0]?.priority?.task_order?.length === 3, '提交成功后把服务端返回写回 store');
ok(env.toasts.some(([m, t]) => t === 'ok' && m.includes('任务顺序已保存')), '一致时提示保存成功');
ok(!env.toasts.some(([, t]) => t === 'warn'), '提交值与返回值一致时不误报警告');
// 5.2 服务端去重导致返回≠提交：可见提示
// 5.2 服务端去重或裁剪导致返回≠提交：可见提示（提交里两条重复，服务端只回一条）
env = mkCommit([{ rev: 5, task_order: ['t-b', 't-a'] }]);
await env.commitTaskOrder(['t-b', 't-a', 't-b']);
ok(env.toasts.some(([m, t]) => t === 'warn' && m.includes('服务端调整了') && m.includes('task_order')), '提交值与返回值不一致时给可见提示，不静默接受');
// 5.3 权重下拉提交 agent_weight
env = mkCommit([{ rev: 5, agent_weight: { f1: '2' } }]);
await env.saveAgentWeight('f1', '2');
ok(JSON.stringify(env.calls[0].body.agent_weight) === JSON.stringify({ f1: '2' }) && env.calls[0].body.rev === 4, '权重下拉整表提交 agent_weight 并带 rev');
ok(env.toasts.some(([m]) => m.includes('别名-f1 权重已保存')), '提示用 displayName 出口');
env = mkCommit([{ rev: 5, agent_weight: {} }]);
await env.saveAgentWeight('f1', '');
ok(env.toasts.some(([m]) => m.includes('权重已清除')) && !('f1' in (env.calls[0].body.agent_weight || {})), '选回默认时删键并提示已清除（cfa6：不再写空串占位）');
// 5.4 409：提示刷新、不覆盖
env = mkCommit([Object.assign(new Error('conflict: rev 3 != 4'), { status: 409 })]);
const r = await env.commitTaskOrder(['t-a']);
ok(r === false, '409 返回 false');
ok(env.sets.length === 0, '409 时不写 store，即不覆盖别处的调度表');
ok(env.toasts.some(([m, t]) => t === 'warn' && m.includes('未提交') && m.includes('conflict')), '409 提示冲突而不是保存成功');
ok(env.calls[0].body.rev === 4, '冲突请求仍然带本地 rev 交给服务端判定');
// 5.5 老链路 commitRelayOrder 不受影响
ok(/async function commitRelayOrder/.test(src) && /relay_order_meta/.test(src), '顶替序提交路径保持原样');

console.log(fails ? `\n${fails} FAILED` : '\nALL PASS');
process.exit(fails ? 1 : 0);
