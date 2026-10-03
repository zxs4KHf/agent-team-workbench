// 体验反馈 fb-b35df17376：概览「等你决定」行动卡不能再用内部流程词。
// 不起服务：复制 web/v2/js 到临时目录（pretext 打桩）+ 最小 DOM 垫片，真实导入 views.js 并调用生成该卡的函数与 renderOverview。
// 跑法：node tests/smoke_wait_copy_b35d.mjs（仓库根目录）
import { mkdtempSync, mkdirSync, cpSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」
const repo = process.cwd();
const tmp = mkdtempSync(join(tmpdir(), 'busb35d-ui-'));
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
const wait = { task_id: '20260929-wait-1', status: 'merged', title: '示例 dry-run 收尾待复审', created: iso(now), updated: iso(now), peers: [] };
const p = { slug: 'p', name: 'p', peers: [], decisions: [wait] };
st.set({ connected: true, peers: [], controller: { online: true, status: 'idle' }, commands: [], nudges: {}, project: 'p', view: 'overview', history: [], projects: [p] });

// 1 生成该卡的函数（真实调用导出函数，不是读源码字符串）
const card = V.waitForYouCard(wait);
// 2026-10-02 用户：每条都是同一句「Agent 已把方案整理好了…」没有意义。第二行改成：有简介显示简介，没有就写事实（多久前整理好、几份回答）
ok(card.body !== V.WAIT_FOR_YOU_COPY.body && card.body.includes('整理好'), '没写简介：body 是这张卡自己的事实，不是人人一样的固定句');
const briefed = V.waitForYouCard({ ...wait, brief: '招标方发来澄清文件，设备清单要逐条改，已做工具自动改并自检。' });
ok(briefed.body === '招标方发来澄清文件，设备清单要逐条改，已做工具自动改并自检。', '写了简介：body 就是简介');
ok(card.label === '打开看看', '按钮文案是「打开看看」');
ok(card.action === 'open-decision' && card.id === '20260929-wait-1', 'action 仍是 open-decision、task id 原样带过去（点击行为不变）');
ok(card.title === '示例 dry-run 收尾待复审', '标题继续用用户的业务标题，没被文案改动波及');
const BANNED = ['合并稿', '核验', '退回', '查看决策'];
ok(!BANNED.some(w => (card.body + card.label).includes(w)), `卡内不含内部流程词 ${BANNED.join(' / ')}`);

// 2 整页渲染：「现在等我」的行动卡列表里确实是这张卡，且该列表通体没有那四个词
const html = V.renderOverview(p);
const ls0 = html.indexOf('id="focusList"');
const sec = html.slice(ls0, html.indexOf('</section>', ls0));
ok(sec.includes('打开看看') && !sec.includes(V.WAIT_FOR_YOU_COPY.body) && sec.includes('整理好'), '概览「现在等我」渲染出这张卡自己的第二行，不再是固定句');
ok(!BANNED.some(w => sec.includes(w)), '这张卡的渲染结果（含 body / 按钮 / data 属性）不含内部流程词');
ok(/data-action="open-decision" data-id="20260929-wait-1"/.test(sec), '渲染出的按钮仍带 open-decision + 原 task id');
ok(sec.includes('focus-item violet'), '仍是 violet 行动卡样式');
// 空态提示同属这一块，也不能带流程词
st.set({ connected: true, peers: [], controller: { online: true, status: 'idle' }, commands: [], nudges: {}, project: 'p', view: 'overview', history: [], projects: [{ ...p, decisions: [] }] });
const emptySec = V.renderOverview({ ...p, decisions: [] });
ok(emptySec.includes('Agent 把方案整理好后会出现在这里'), '空态提示改成直白说法');
ok(!/合并稿核验完成后会出现在这里/.test(emptySec), '旧的「合并稿核验完成后会出现在这里」不再出现');

// 3 该卡文案只有一个出处，避免以后又各处写回术语
const src = frontSrc('views.js');
const ov = src.slice(src.indexOf('export function renderOverview'), src.indexOf('export function renderRunning'));
ok(/userWait\.forEach\(d => focus\.push\(waitForYouCard\(d\)\)\)/.test(ov), 'renderOverview 只通过 waitForYouCard 生成该卡');
const copyLines = src.split(/\r?\n/).filter(l => l.includes('打开看看'));
ok(copyLines.length === 1 && copyLines[0].includes('Agent 已把方案整理好了') && /^export const WAIT_FOR_YOU_COPY =/.test(copyLines[0].trim()), '新文案只写在 WAIT_FOR_YOU_COPY 一处');
ok(!/等待你采纳或退回继续讨论/.test(src), '旧的「等待你采纳或退回继续讨论」已从源码消失');

console.log(fails ? `\n${fails} FAILED` : '\nALL PASS'); process.exit(fails ? 1 : 0);
