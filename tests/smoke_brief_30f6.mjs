// 决策 20260929-181437-30f6 · 卡面简介 brief：前端只显示 brief，缺失按类型显示兜底句，summary 收进默认折叠的详情。
// 不起服务：复制 web/v2/js 到临时目录（pretext 打桩）+ 最小 DOM 垫片，真实导入 views.js 调用导出函数与 renderDecisions。
// 跑法：node tests/smoke_brief_30f6.mjs（仓库根目录）
import { mkdtempSync, mkdirSync, cpSync, writeFileSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」
const repo = process.cwd();
const tmp = mkdtempSync(join(tmpdir(), 'bus30f6-ui-'));
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
const SUMMARY = 'R2 合并稿已核验，主线 commit a1b2c3d4 聚合完毕';
const base = { status: 'merged', created: iso(now), updated: iso(now), peers: [] };
const plan = { ...base, task_id: '20261001-plan-1', title: '示例改表 · 澄清文件落单', summary: SUMMARY };
const planBrief = { ...base, task_id: '20261001-plan-2', title: '示例改表 · 对照说明', summary: SUMMARY, brief: BRIEF };
const review = { ...base, task_id: '20261001-rev-1', title: '一键接入改动审查', mode: 'review', summary: SUMMARY };
const artifact = { ...base, task_id: '20261001-art-1', status: 'adopted', title: '任务链归组模块', summary: SUMMARY, artifacts: [{ path: 'web/v2/js/lineage.js', kind: 'code' }] };
const blankBrief = { ...base, task_id: '20261001-plan-3', title: '空白简介', brief: '   ' };
const p = { slug: 'p', name: '示例', peers: [], decisions: [plan, planBrief, review, artifact, blankBrief] };
st.set({ connected: true, peers: [], controller: { online: true, status: 'idle' }, commands: [], nudges: {}, project: 'p', view: 'decisions', history: [], projects: [p] });

// 1 三类兜底句：类型只认结构化字段，各一个用例
ok(V.briefKindOf(plan) === 'plan' && V.briefKindOf(review) === 'review' && V.briefKindOf(artifact) === 'artifact', '卡片类型按 mode / artifacts 判定，不猜标题');
ok(V.briefOf(plan) === V.WAIT_FOR_YOU_COPY.body, '方案卡兜底句与 917c 同口径（复用 WAIT_FOR_YOU_COPY.body）');
ok(V.briefOf(review) === '机器已经检查完，打开看结论。', '审查卡兜底句');
ok(V.briefOf(artifact) === '东西已经做好并通过检查，等接进界面。', '交付物卡兜底句');
ok(V.briefOf(blankBrief) === V.BRIEF_FALLBACK.plan, '只有空白的 brief 等于没写，照样走兜底句，绝不留白');
ok(V.briefOf(planBrief) === BRIEF, '写了 brief 就原样显示，不被兜底句顶掉');
ok(Object.values(V.BRIEF_FALLBACK).every(s => s && s.trim().length > 0), '三条兜底句都不是空串');

// 2 兜底句本身不许带状态词、编号、提交号
const STATUS_WORDS = ['已合并', '待合并', '已采纳', '已归档', '收集中', '已派发', 'merged', 'adopted', 'ready_for_merge'];
for (const [kind, text] of Object.entries(V.BRIEF_FALLBACK)) {
  ok(!STATUS_WORDS.some(w => text.includes(w)), `${kind} 兜底句不含状态词`);
  ok(!/\d{8}-\d{6}|[0-9a-f]{7,}|\d/.test(text), `${kind} 兜底句不含编号 / 提交号 / 数字`);
}

// 3 决策页：卡面显示 brief，summary 不在卡面正文
const html = V.renderDecisions(p);
const cardOf = id => { const i = html.indexOf(`id="decision-${id}"`); return i < 0 ? '' : html.slice(i, html.indexOf('</article>', i)); };
const c1 = cardOf('20261001-plan-1'), c2 = cardOf('20261001-plan-2'), c3 = cardOf('20261001-rev-1'), c4 = cardOf('20261001-art-1');
ok(c1.includes(`<p class="summary">${V.BRIEF_FALLBACK.plan}</p>`), '没写 brief 的方案卡，卡面正文是方案兜底句');
ok(c3.includes(`<p class="summary">${V.BRIEF_FALLBACK.review}</p>`), '审查卡卡面正文是审查兜底句');
ok(c4.includes(`<p class="summary">${V.BRIEF_FALLBACK.artifact}</p>`), '登记了交付物的卡，卡面正文是交付物兜底句');
ok(c2.includes(`<p class="summary">${BRIEF}</p>`), '写了 brief 的卡，卡面正文就是这句话');
ok(!c1.includes('Leader 尚未回写最终方案'), '旧的「尚未回写最终方案」占位句已消失');
ok(!/<p class="summary">[^<]*合并稿[^<]*<\/p>/.test(html), 'summary 的流水账不再出现在卡面正文里');

// 4 summary 零回归：仍在卡上的详情里，默认折叠
const det = c1.slice(c1.indexOf('<details class="dsum"'), c1.indexOf('</details>', c1.indexOf('<details class="dsum"')) + 10);
ok(det.startsWith('<details class="dsum"') && det.includes(SUMMARY), 'summary 收进 details，内容照旧可见');
ok(!/<details class="dsum"[^>]*\sopen/.test(det), '该 details 默认折叠（没有 open）');
ok(!cardOf('20261001-plan-3').includes('<details class="dsum"'), '没有 summary 的记录不渲染空抽屉');

// 5 交付物卡（成果页）也有一句人话
const art = V.artifactCard(artifact, p);
ok(art.includes(`<p class="summary">${V.BRIEF_FALLBACK.artifact}</p>`), '交付物卡缺 brief 时显示交付物兜底句');
ok(V.artifactCard({ ...artifact, brief: BRIEF }, p).includes(`<p class="summary">${BRIEF}</p>`), '交付物卡写了 brief 就显示 brief');

// 6 历史记录（没有 brief 字段）不报错、正常走兜底
const legacy = { task_id: '20260924-old-1', status: 'adopted', title: '老决策', created: iso(now), updated: iso(now), peers: [] };
ok(V.briefOf(legacy) === V.BRIEF_FALLBACK.plan, '老记录没有 brief 字段，直接走兜底句');
const legacyHtml = V.renderDecisions({ ...p, decisions: [legacy] });
ok(legacyHtml.includes(V.BRIEF_FALLBACK.plan) && !legacyHtml.includes('undefined'), '整页渲染老记录不出 undefined');

// 7 文案只有一个出处，免得以后各处写回术语
const src = frontSrc('views.js');
for (const text of [V.BRIEF_FALLBACK.review, V.BRIEF_FALLBACK.artifact]) {
  ok(src.split(/\r?\n/).filter(l => l.includes(text)).length === 1, `兜底句「${text}」只写在 BRIEF_FALLBACK 一处`);
}
ok(/plan: WAIT_FOR_YOU_COPY\.body/.test(src), '方案卡兜底句直接引用 917c 的文案常量，改一处两处同步');

console.log(fails ? `\n${fails} FAILED` : '\nALL PASS'); process.exit(fails ? 1 : 0);
