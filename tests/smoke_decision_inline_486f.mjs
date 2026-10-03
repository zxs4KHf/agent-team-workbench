// 体验反馈 fb-486f8ac59c：「现在等我」里的待决项要能直接点选，且**不弹新窗口**。
// 不起服务：复制 web/v2/js 到临时目录（pretext 打桩）+ 最小 DOM 垫片，真实导入 views.js 渲染整页。
// 跑法：node tests/smoke_decision_inline_486f.mjs（仓库根目录）
import { mkdtempSync, mkdirSync, cpSync, writeFileSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { dirname } from 'node:path';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」
// 仓库根从本文件位置推，不用 process.cwd()：worktree 里跑时 cwd 可能是主仓
const repo = dirname(dirname(fileURLToPath(import.meta.url)));
const tmp = mkdtempSync(join(tmpdir(), 'bus486f-ui-'));
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

// fixture 用 Leader 今天真实挂着的两个待决事项
const pkg21 = {
  id: 'pkg21-preshrink', question: '包 21 预缩机：澄清要松绑死尺寸，怎么改原始设备清单？', allow_free_text: true,
  options: [{ key: 'a', label: '保留原尺寸并在其后追加新句', detail: '改动最小' }, { key: 'b', label: '原地改写掉死尺寸' }]
};
const dep = { id: '2d14-cycle', question: '2d14 循环依赖怎么解？', allow_free_text: true,
  options: [{ key: 'jia', label: '把 keep/2d14-r2 变基到当前 main' }, { key: 'yi', label: '在当前 main 上重新实现' }] };
const decided = { id: 'orphan-taken', question: '孤儿 .taken 存量 30 条怎么处理？', allow_free_text: false,
  options: [{ key: 'auto', label: '自动 requeue' }, { key: 'manual', label: '归档交人工' }],
  decided: { by: 'leader', choice: 'auto', reason: '全部超过 24 小时且原 peer 已离线，requeue 没有重复执行风险' } };
const mk = (task_id, title, dn) => ({ task_id, status: 'merged', title, created: iso(now), updated: iso(now), peers: [], decisions_needed: dn });
const d1 = mk('20260929-demo', '示例澄清改表', [pkg21]);
const d2 = mk('20260924-2d14', '2d14 循环依赖', [dep, decided]);
const p = { slug: 'p', name: 'p', peers: [], decisions: [d1, d2] };
const setState = proj => st.set({ connected: true, peers: [], controller: { online: true, status: 'idle' }, commands: [], nudges: {}, project: 'p', view: 'overview', history: [], projects: [proj] });
setState(p);

// 1 真实调用导出函数
const items = V.pendingDecisionItems(p);
ok(items.length === 3, `pendingDecisionItems 收齐三条（实际 ${items.length}）`);
ok(V.dnChoiceLabel({ options: pkg21.options, answer: { choice: 'a' } }) === 'a · 保留原尺寸并在其后追加新句', 'dnChoiceLabel 把 key 翻成人话');
ok(V.dnChoiceLabel({ options: [], answer: { choice: '' } }) === '没选项，只给了补充说明', '只补充不选项时有专门说法');

// 2 整页渲染：DOM 断言
const html = V.renderOverview(p);
const cut = (marker, end = '</section>') => { const i = html.indexOf(marker); return i < 0 ? '' : html.slice(i, html.indexOf(end, i)); };
const ask = cut('id="decideList"');
ok(!!ask, '概览渲染出「等你拍板」区（id=decideList）');
ok(/<form[^>]+data-form="decision-answer"/.test(ask), '待决项渲染为 form[data-form="decision-answer"]');
ok((ask.match(/type="radio"/g) || []).length === 4, `两个待决项共渲染 4 个 radio（实际 ${(ask.match(/type="radio"/g) || []).length}）`);
ok(ask.includes('保留原尺寸并在其后追加新句') && ask.includes('原地改写掉死尺寸'), '包 21 的 (a)/(b) 原样出现在页面上');
ok(ask.includes('把 keep/2d14-r2 变基到当前 main') && ask.includes('在当前 main 上重新实现'), '2d14 的甲/乙原样出现在页面上');
ok((ask.match(/<textarea/g) || []).length === 2, '每个待决项都有一个「补充说明」文本框');
ok(/data-project="p"/.test(ask) && /data-task="20260929-demo"/.test(ask) && /data-item="pkg21-preshrink"/.test(ask), 'form 带齐 project / task / item，提交时不用再猜');
ok(/<form[^>]+data-keep/.test(ask), 'form 带 data-keep：SSE 重绘不会抹掉用户打到一半的补充说明');
ok(ask.includes('就地提交，不会打开新窗口'), '页面上明说就地提交');
const dec = cut('id="decidedList"');
ok(!!dec && dec.includes('Leader 定的：auto · 自动 requeue'), '「Leader 已替你定」单独成区并写明所选项');
ok(dec.includes('理由：全部超过 24 小时且原 peer 已离线'), '已决项必须带一句理由');
// 931d：已决项默认只读，但可以推翻——表单收在默认折叠的 <details> 里
const noDetails = h => h.replace(/<details[\s\S]*?<\/details>/g, '');
ok(!/<form/.test(noDetails(dec)) && !/type="radio"/.test(noDetails(dec)), '已决项默认只读：折叠区外没有 form、radio');
ok(/<details class="dn-more" data-keep data-keep-id="dnr:[^"]+">/.test(dec) && !/<details[^>]* open/.test(dec) && dec.includes('不同意？写下你的意见'), '已决项可推翻：折叠的「不同意」入口，默认收起');

// 3 已回复：原地变成「已回复：<所选项> + <补充>」
const answered = { ...p, decisions: [mk('20260929-demo', '示例澄清改表', [{ ...pkg21, answer: { choice: 'a', text: '标黄，别动数量列', at: iso(now), by: 'user' } }])] };
setState(answered);
const ans = cut2(V.renderOverview(answered), 'id="decideList"');
function cut2(h, marker) { const i = h.indexOf(marker); return i < 0 ? '' : h.slice(i, h.indexOf('</section>', i)); }
ok(ans.includes('你的回答：a · 保留原尺寸并在其后追加新句'), '提交后原地变成「你的回答：<所选项>」');
ok(ans.includes('等 Leader 确认') && !ans.includes('Leader 已收到'), '未回执显示「等 Leader 确认」');
ok(ans.includes('补充：标黄，别动数量列'), '补充说明一并回显');
ok(!/<form[^>]+data-form="decision-answer"/.test(noDetails(ans)) && !/type="radio"/.test(noDetails(ans)), '已回复的项折叠区外不再显示可点表单');
ok(ans.includes('>改答</summary>'), '已回复的项可以改答（折叠入口）');
// 931d-2：回执后变「Leader 已收到」
const acked = { ...p, decisions: [mk('20260929-demo', '示例澄清改表', [{ ...pkg21, answer: { choice: 'a', text: '', at: iso(now), by: 'user', acked_at: iso(now), question: pkg21.question } }])] };
setState(acked);
const ack = cut2(V.renderOverview(acked), 'id="decideList"');
ok(ack.includes('Leader 已收到') && !ack.includes('等 Leader 确认'), '回执后显示「Leader 已收到」');
// 931d-4：Leader 定的与用户答的两行并存
const both = { ...p, decisions: [mk('t-ov', '推翻', [{ ...decided, answer: { choice: 'manual', text: '先别自动', at: iso(now), by: 'user' } }])] };
setState(both);
const bh = V.renderOverview(both);
ok(bh.includes('Leader 定的：auto · 自动 requeue') && bh.includes('你的回答：manual · 归档交人工'), 'Leader 的决定与用户的回答两行并存');
// 题面改过要提示；撤下的题回答存档、不再给改答入口
const moved = { ...p, decisions: [mk('t-mv', '改题', [{ ...pkg21, question: '新题面', answer: { choice: 'a', text: '', at: iso(now), by: 'user', question: '旧题面' } }, { ...pkg21, id: 'gone', retired: true, answer: { choice: 'b', text: '', at: iso(now), by: 'user' } }])] };
setState(moved);
const mh = V.renderOverview(moved);
ok(mh.includes('你回答时的题目是「旧题面」'), '题面改过时提示用户答的是旧题');
const rel = { ...p, decisions: [mk('t-rl', '改文案', [{ ...pkg21, answer: { choice: 'a', choice_label: '旧文案', text: '', at: iso(now), by: 'user', question: pkg21.question } }])] };
setState(rel);
const rh = V.renderOverview(rel);
ok(rh.includes('你的回答：a · 旧文案') && rh.includes('这个选项后来被 Leader 改成了「保留原尺寸并在其后追加新句」'), '选项文案改过：显示答题时的文案并提示');
const del = { ...p, decisions: [mk('t-dl', '删选项', [{ ...pkg21, options: [pkg21.options[1]], answer: { choice: 'a', choice_label: '保留原尺寸并在其后追加新句', text: '', at: iso(now), by: 'user', question: pkg21.question } }])] };
setState(del);
const dh = V.renderOverview(del);
ok(dh.includes('你的回答：a · 保留原尺寸并在其后追加新句') && dh.includes('你选的这个选项后来被 Leader 删除了'), '选项被删除：显示原文案并提示已删除');
const same = { ...p, decisions: [mk('t-sm', '没改', [{ ...pkg21, answer: { choice: 'a', choice_label: pkg21.options[0].label, text: '', at: iso(now), by: 'user', question: pkg21.question } }])] };
setState(same);
const sh = V.renderOverview(same);
ok(!sh.includes('后来被 Leader'), '选项没变：不出提示');
const gone = mh.slice(mh.indexOf('data-dn="t-mv/gone"'));
ok(gone.includes('Leader 已撤下此题，回答已存档') && !gone.slice(0, gone.indexOf('</div></div>') + 1).includes('改答'), '撤下的题：回答存档、不给改答入口');

// 4 回归：没有 decisions_needed 的旧决策，整页不受影响
const old = { slug: 'p', name: 'p', peers: [], decisions: [{ task_id: 'old-1', status: 'merged', title: '老决策', created: iso(now), updated: iso(now), peers: [] }] };
setState(old);
const oldHtml = V.renderOverview(old);
ok(!oldHtml.includes('decideList') && !oldHtml.includes('decidedList') && !oldHtml.includes('data-form="decision-answer"'), '旧决策一个待决区都不渲染');
ok(oldHtml.includes('id="focusList"') && oldHtml.includes('现在等我'), '旧决策的「现在等我」照常渲染，没被新区挤掉');
ok(V.pendingDecisionItems(old).length === 0, '旧决策 pendingDecisionItems 为空，不会崩');
const broken = { slug: 'p', name: 'p', peers: [], decisions: [{ task_id: 'b-1', status: 'merged', title: 'x', created: iso(now), peers: [], decisions_needed: 'not-an-array' }] };
setState(broken);
ok(V.pendingDecisionItems(broken).length === 0 && typeof V.renderOverview(broken) === 'string', '脏数据（decisions_needed 不是数组）不渲染也不抛');

// 5 源码断言：整条链路不得开新窗口/新抽屉
const appSrc = frontSrc('app.js');
const viewsSrc = frontSrc('views.js');
const h0 = appSrc.indexOf("'decision-answer':");
ok(h0 > 0, "app.js 注册了 FORMS['decision-answer']");
const handler = appSrc.slice(h0, appSrc.indexOf("\n  },", h0));
ok(!/openDrawer|openCmdk|confirmDialog|window\.open|location\.href\s*=/.test(handler), '提交处理器不开抽屉、不弹对话框、不跳页');
ok(handler.includes("api('/api/decision/answer'"), '提交打到 /api/decision/answer');
ok(/item_id: form\.dataset\.item/.test(handler) && /task_id: form\.dataset\.task/.test(handler), '提交带上 item_id / task_id（改名必红）');
ok(handler.includes('refresh(true)'), '提交后刷新状态，卡片原地更新');
const sec = viewsSrc.slice(viewsSrc.indexOf('export function decisionsNeededSection'), viewsSrc.indexOf('export function renderOverview'));
ok(!/openDrawer|data-action="open-/.test(sec), '待决区渲染不含任何打开抽屉的入口');
ok(!/summary|\.next\b/.test(sec), '待决项不去解析 summary / next 的自然语言');
ok(/decisionsNeededSection\(p[,)]/.test(viewsSrc.slice(viewsSrc.indexOf('export function renderOverview'))), 'renderOverview 真的挂上了这一区（2026-10-02 起放在概览标签页里：decisionsNeededSection(p, true)）');

console.log(fails ? `\n${fails} FAILED` : '\nALL PASS'); process.exit(fails ? 1 : 0);
