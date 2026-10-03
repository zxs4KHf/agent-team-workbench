// 决策 20260929-181437-30f6 · brief 落地第 6 步的前端部分：卡片「看不懂」按钮与「待重写」标记。
// 不起服务：复制 web/v2/js 到临时目录（pretext 打桩）+ 最小 DOM 垫片，真实导入 views.js 渲染决策页 / 成果页 / 概览。
// 点击处理在 app.js（要真 DOM，导入不了），按源码断言；后端的指令 / 标记 / 幂等见 tests/test_brief_feedback_30f6.py。
// 跑法：node tests/smoke_brief_feedback_30f6.mjs
import { mkdtempSync, mkdirSync, cpSync, writeFileSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」
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
const E = mk('20261001-e', { status: 'adopted', artifacts: [{ path: 'web/a.js', kind: 'code' }], brief_rewrite: { command_id: 'x1', original: '兜底' } }); // 新卡 · 交付物卡 · 没写 · 待重写
const F = mk('20261001-f', { status: 'adopted', artifacts: [{ path: 'docs/b.md', kind: 'doc' }] }); // 新卡 · 交付物卡 · 没写
const G = (({ brief, ...r }) => r)(mk('20260924-old-1', { brief_rewrite: { command_id: 'x2', original: '兜底' } })); // 老卡 · 没有 brief 键 · 待重写
const H = (({ brief, ...r }) => r)(mk('20260924-old-2', { mode: 'review' }));                  // 老卡 · 没有 brief 键
const I = mk('20261001-i', { status: 'collecting' });                                          // 还在收集：不是卡
const J = mk('20261001-j', { status: 'draft' });                                               // 派发占位：不是卡
const p = { slug: 'p', name: '示例', peers: [], decisions: [A, B2, C, D, E, F, G, H, I, J] };
setState(p);

// 看板：待重写的卡数（含老卡）与说明行
ok(V.briefStats(p).rewrite === 2, `待重写的卡数含老卡（实际 ${V.briefStats(p).rewrite}）`);
{ const hb = V.renderOverview(p); const i = hb.indexOf('data-brief-health'); const bd = hb.slice(i, hb.indexOf('</section>', i)); ok(bd.includes('2 张在等 Leader 重写'), '看板说明行写出有几张卡在等 Leader 重写'); }

// 5 决策卡：「看不懂」按钮与「待重写」标记
const html = V.renderDecisions(p);
const cardOf = id => { const i = html.indexOf(`id="decision-${id}"`); return i < 0 ? '' : html.slice(i, html.indexOf('</article>', i)); };
const cC = cardOf('20261001-c'), cA = cardOf('20261001-a'), cE = cardOf('20261001-e'), cG = cardOf('20260924-old-1'), cH = cardOf('20260924-old-2');
ok(/data-action="brief-confusing"[^>]*data-id="20261001-c"/.test(cC) && cC.includes('data-project="p"') && cC.includes('>看不懂</button>'), '没写 brief 的方案卡：有「看不懂」，带任务号与项目');
ok(cC.includes(`data-shown="${V.BRIEF_FALLBACK.plan}"`), '按钮带上的原文就是卡面上的兜底句');
ok(cA.includes(`data-shown="${BRIEF}"`) && cA.includes('data-action="brief-confusing"'), '写了 brief 的卡：原文是 brief，同样能点');
ok(cC.includes(`<p class="summary">${V.BRIEF_FALLBACK.plan}</p>`) && cA.includes(`<p class="summary">${BRIEF}</p>`), '卡面那句话原样不动（按钮在它下面，不改 brief 本身）');
ok(cC.indexOf('<p class="summary">') < cC.indexOf('data-brief-feedback'), '按钮排在简介下面');
ok(cE.includes('data-brief-rewrite') && cE.includes('待重写') && !cE.includes('data-action="brief-confusing"'), '已排上重写的卡：标「待重写」，不再给按钮（防重复点）');
ok(cG.includes('待重写') && cH.includes('data-action="brief-confusing"'), '老卡（没有 brief 键）照样能反馈 / 显示待重写');
ok(!html.includes('20261001-i') && !html.includes('20261001-j'), '还没成卡的记录不进决策页');
ok(!/undefined|\[object/.test(html), '整页没有 undefined / [object]');

// 6 成果页的交付物卡：原文是交付物兜底句；迷你卡不放按钮
const aF = V.artifactCard(F, p);
ok(aF.includes('data-action="brief-confusing"') && aF.includes(`data-shown="${V.BRIEF_FALLBACK.artifact}"`) && aF.includes('data-project="p"'), '成果页交付物卡：按钮带交付物兜底句');
const revArt = mk('20261001-r', { mode: 'review', status: 'adopted', artifacts: [{ path: 'docs/r.md', kind: 'doc' }] });
const aR = V.artifactCard(revArt, p);
ok(V.briefOf(revArt) === V.BRIEF_FALLBACK.review && aR.includes(`<p class="summary">${V.BRIEF_FALLBACK.artifact}</p>`) && aR.includes(`data-shown="${V.BRIEF_FALLBACK.artifact}"`), '审查单登记了交付物：成果页显示什么、按钮就带什么（强制交付物类型）');
ok(!V.artifactCard(F, p, true).includes('brief-confusing'), '概览里的迷你交付物卡不放按钮');
ok(V.artifactCard(E, p).includes('待重写') && !V.artifactCard(E, p).includes('brief-confusing'), '交付物卡也认「待重写」标记');

// 7 app.js 接线（要真 DOM，按源码断言）
const app = frontSrc('app.js'), views = frontSrc('views.js');
ok(/import \{[^}]*\bbriefFeedback\b[^}]*\} from '\.\/views\.js'/.test(app), 'app.js 导入 briefFeedback');
const h = app.slice(app.indexOf("'brief-confusing': async t =>"), app.indexOf("'export-task-pack'"));
ok(h.length > 50 && h.includes("'/api/decision/brief-rewrite'") && h.includes("method: 'POST'"), "点击 → POST /api/decision/brief-rewrite");
ok(/project: t\.dataset\.project \|\| S\(\)\.project/.test(h) && /task_id: t\.dataset\.id/.test(h) && /shown: t\.dataset\.shown \|\| ''/.test(h), '带上项目（缺省取当前项目）、任务号、卡面原文');
ok(/r\.dedup/.test(h) && /await refresh\(true\)/.test(h) && /notify\.error\(e\)/.test(h) && /classList\.remove\('busy'\)/.test(h), '重复点给提示、成功后刷新、失败走统一报错、按钮忙状态必还原');
ok(/F\.drawer\.kind === 'decision' && F\.drawer\.key === t\.dataset\.id\) openDecision\(t\.dataset\.id\)/.test(h), '在详情抽屉里点：重开抽屉，让「待重写」当场出现');
const drawer = app.slice(app.indexOf('function openDecision'), app.indexOf('function openDecision') + 4000);
ok(drawer.includes('briefFeedback(d, (project() || {}).slug)'), '详情抽屉的「简介」旁也有「看不懂」');
// 新增代码的设计护栏：不用内联样式（tests/design_ratchet.json 的上限只降不升）
const mine = views.slice(views.indexOf('export const BRIEF_KIND_LABEL'), views.indexOf('// fb-486f8ac59c：Leader 能定的自己定'));
ok(mine.length > 500 && !/style=/.test(mine), '新增的看板 / 反馈代码不含内联样式');
const BRIEF_TEXT_LINES = [V.BRIEF_FALLBACK.review, V.BRIEF_FALLBACK.artifact].map(t => views.split(/\r?\n/).filter(l => l.includes(t)).length);
ok(BRIEF_TEXT_LINES.every(n => n === 1), '兜底句文案仍只写在 BRIEF_FALLBACK 一处（看板读它，不复制它）');


console.log(fails ? `\n${fails} FAILED` : '\nALL PASS'); process.exit(fails ? 1 : 0);
