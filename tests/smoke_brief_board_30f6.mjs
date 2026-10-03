// 决策 20260929-181437-30f6 · brief 落地第 5 步的前端部分：概览「简介体检」——统计口径、可配提醒线、看板渲染。
// 不起服务：复制 web/v2/js 到临时目录（pretext 打桩）+ 最小 DOM 垫片，真实导入 views.js 渲染概览。
// 后端提醒线（settings.json 的 brief_alert_ratio）见 tests/test_brief_board_30f6.py。
// 跑法：node tests/smoke_brief_board_30f6.mjs
import { mkdtempSync, mkdirSync, cpSync, writeFileSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
// 仓库根从本文件位置推，不用 process.cwd()：worktree 里跑时 cwd 可能是主仓
const repo = dirname(dirname(fileURLToPath(import.meta.url)));
const tmp = mkdtempSync(join(tmpdir(), 'bus30f6r-ui-'));
cpSync(join(repo, 'web/v2/js'), join(tmp, 'web/v2/js'), { recursive: true });
mkdirSync(join(tmp, 'web/v2/vendor/pretext'), { recursive: true });
cpSync(join(repo, 'web/v2/vendor/idiomorph.esm.js'), join(tmp, 'web/v2/vendor/idiomorph.esm.js'));
writeFileSync(join(tmp, 'web/v2/vendor/pretext/layout.js'), 'export const prepare = () => ({}); export const layout = () => ({ lines: [] });');
process.on('exit', () => rmSync(tmp, { recursive: true, force: true }));
const ls = new Map();
globalThis.localStorage = { getItem: k => ls.has(k) ? ls.get(k) : null, setItem: (k, v) => ls.set(k, String(v)), removeItem: k => ls.delete(k) };
globalThis.location = { search: '', pathname: '/', hash: '', href: 'http://x/' };
globalThis.history = { state: null, pushState() {}, replaceState() {} };
globalThis.matchMedia = () => ({ matches: false });
globalThis.requestAnimationFrame = fn => fn();
globalThis.document = { querySelector: () => null, querySelectorAll: () => [], createElement: () => ({ style: {}, classList: { add() {}, remove() {} }, setAttribute() {}, querySelector: () => ({}) }), activeElement: null, body: { style: {} }, addEventListener() {}, hidden: false };
globalThis.window = globalThis;
const mod = rel => import(pathToFileURL(join(tmp, rel)).href);
const { store: st } = await mod('web/v2/js/model.js');
const V = await mod('web/v2/js/views.js');
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
const now = new Date(); const iso = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
const BRIEF = '招标方发来澄清文件，设备清单要逐条改三十多处，已做工具自动改并自检。';
const base = { status: 'merged', created: iso(now), updated: iso(now), peers: [] };
const mk = (id, extra = {}) => ({ ...base, task_id: id, title: '标题 ' + id, brief: '', ...extra });
const setState = (proj, extra = {}) => st.set({ connected: true, peers: [], controller: { online: true, status: 'idle' }, commands: [], nudges: {}, project: 'p', view: 'overview', history: [], projects: [proj], brief_alert_ratio: undefined, ...extra });

// ---- 数据：6 张新卡（带 brief 槽位）+ 2 张老卡（没有 brief 键）+ 2 条还没成卡的新记录 ----
const A = mk('20261001-a', { brief: BRIEF });                                                  // 新卡 · 写了
const B2 = mk('20261001-b', { brief: BRIEF, status: 'adopted' });                              // 新卡 · 写了
const C = mk('20261001-c', { brief: '   ' });                                                  // 新卡 · 方案卡 · 只有空白 = 没写
const D = mk('20261001-d', { mode: 'review' });                                                // 新卡 · 审查卡 · 没写
const E = mk('20261001-e', { status: 'adopted', artifacts: [{ path: 'web/a.js', kind: 'code' }] }); // 新卡 · 交付物卡 · 没写
const F = mk('20261001-f', { status: 'adopted', artifacts: [{ path: 'docs/b.md', kind: 'doc' }] }); // 新卡 · 交付物卡 · 没写
const G = (({ brief, ...r }) => r)(mk('20260924-old-1')); // 老卡 · 没有 brief 键
const H = (({ brief, ...r }) => r)(mk('20260924-old-2', { mode: 'review' }));                  // 老卡 · 没有 brief 键
const I = mk('20261001-i', { status: 'collecting' });                                          // 还在收集：不是卡
const J = mk('20261001-j', { status: 'draft' });                                               // 派发占位：不是卡
const p = { slug: 'p', name: '示例', peers: [], decisions: [A, B2, C, D, E, F, G, H, I, J] };
setState(p);

// 2 统计口径：只算决策页上的卡里带 brief 槽位的新记录
const s = V.briefStats(p);
ok(s.total === 6 && s.written === 2 && s.missing === 4, `新卡 6 张，写了 2、缺 4（实际 ${s.total}/${s.written}/${s.missing}）`);
ok(s.fallback.plan === 1 && s.fallback.review === 1 && s.fallback.artifact === 2, `每种兜底句的出现次数：方案 1 / 审查 1 / 交付物 2（实际 ${JSON.stringify(s.fallback)}）`);
ok(Object.keys(s.fallback).join() === Object.keys(V.BRIEF_FALLBACK).join(), '统计按 BRIEF_FALLBACK 的类型逐个列，不另写一份类型表');
ok(s.legacy === 2, `老卡 2 张只走兜底句，不计入占比（实际 ${s.legacy}）`);
ok(Math.abs(s.share - 4 / 6) < 1e-9, '占比 = 缺失 / 新卡');
ok(s.threshold === 0.5 && s.alert === true, '默认提醒线 50%，4/6 超过 → 提醒');
ok(V.briefStats({ slug: 'q', decisions: [I, J] }).total === 0, '还没成卡的记录（收集中 / 派发占位）不算');
const empty = V.briefStats({ slug: 'q', decisions: [] });
ok(empty.total === 0 && empty.share === 0 && empty.alert === false, '没有新卡：占比 0，不提醒（不除零）');

// 3 阈值：严格「超过」才提醒；可配；非法回默认
const half = { slug: 'h', decisions: [mk('h-1', { brief: BRIEF }), mk('h-2')] };
ok(V.briefStats(half, 0.5).alert === false, '正好 50% 不算超过');
ok(V.briefStats(half, 0.49).alert === true, '超过 49% 就提醒');
ok(V.briefStats({ slug: 'h', decisions: [mk('h-3', { brief: BRIEF })] }, 0).alert === false, '全写了，线设 0 也不提醒');
ok(V.briefStats({ slug: 'h', decisions: [mk('h-4')] }, 0).alert === true, '线设 0：只要有一张兜底就提醒');
setState(p, { brief_alert_ratio: 0.9 });
ok(V.briefAlertRatio() === 0.9 && V.briefStats(p).threshold === 0.9 && V.briefStats(p).alert === false, '服务端下发 0.9：4/6 不再提醒');
setState(p, { brief_alert_ratio: 0.2 });
ok(V.briefStats(p).alert === true && V.briefStats(half).alert === true, '下发 0.2：更敏感');
for (const bad of [null, undefined, 'x', '0.3', NaN, -0.1, 1.1, true, [0.2]]) {
  setState(p, { brief_alert_ratio: bad });
  ok(V.briefAlertRatio() === V.BRIEF_ALERT_DEFAULT && V.BRIEF_ALERT_DEFAULT === 0.5, `非法提醒线 ${JSON.stringify(bad)} 回到默认 50%`);
}
setState(p);

// 4 概览看板：计数 / 提醒条 / 说明
let ov = V.renderOverview(p);
const board = h => { const i = h.indexOf('data-brief-health'); return i < 0 ? '' : h.slice(h.lastIndexOf('<section', i), h.indexOf('</section>', i) + 10); };
let b = board(ov);
ok(b.includes('<h2>简介体检</h2>'), '概览有「简介体检」');
ok(b.includes('新卡 6 张 · 缺简介 4（67%）'), '标题行写清新卡数、缺失数与占比');
for (const [kind, n] of [['plan', 1], ['review', 1], ['artifact', 2]]) {
  const m = b.match(new RegExp(`data-brief-kind="${kind}"><b class="num">(\\d+)</b><span[^>]*>次 · ([^：]+)：([^<]+)</span>`));
  ok(m && Number(m[1]) === n && m[2] === V.BRIEF_KIND_LABEL[kind] && m[3] === V.BRIEF_FALLBACK[kind], `每种兜底句各一行：${kind} 出现 ${n} 次，附兜底句原文`);
}
ok(b.includes('data-brief-alert') && b.includes('兜底句占 67%，超过提醒线 50%'), '超过提醒线：显示提醒条，写明占比与提醒线');
ok(b.includes('另有 2 张老卡只走兜底句，不计入占比') && !b.includes('等 Leader 重写') && b.includes('提醒线 50%'), '说明行：口径、老卡、提醒线（这一步还没有待重写）');
ok(!/style=/.test(b), '看板没有内联样式（设计棘轮只降不升）');
setState(p, { brief_alert_ratio: 0.9 });
b = board(V.renderOverview(p));
ok(!b.includes('data-brief-alert') && b.includes('提醒线 90%'), '提醒线调到 90%：4/6 不提醒，说明行跟着变');
setState({ slug: 'p', name: '示例', peers: [], decisions: [] });
b = board(V.renderOverview({ slug: 'p', name: '示例', peers: [], decisions: [] }));
ok(b.includes('还没有新卡') && !b.includes('data-brief-alert') && /data-brief-kind="plan"><b class="num">0<\/b>/.test(b), '没有新卡：三行都是 0，不提醒');
setState(p);
ov = V.renderOverview(p);
// 2026-10-02 概览一屏：看板是右栏独立标签「简介体检」（超提醒线默认打开），排在「最近定了」之后
ok(/data-group="side" data-tab="brief"/.test(ov) && ov.indexOf('data-brief-health') > ov.indexOf('最近定了什么'), '看板是右栏「简介体检」标签，排在「最近定了」之后');


console.log(fails ? `\n${fails} FAILED` : '\nALL PASS'); process.exit(fails ? 1 : 0);
