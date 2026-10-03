// 任务 903f 前端冒烟：不起服务，复制 web/v2/js 到临时目录（pretext 打桩），最小 DOM 垫片渲染概览与决策页。
// 检查：四分页（Agent 处理中 / 等你一步 / 等待决策 / 已完成）、人工项只在准备齐全时出现、显示标题「主题 · R轮 · 动作」、按 root_task 分组折叠、旧记录 self-root 显示原标题。
// 跑法：node tests/smoke_lineage_903f.mjs
import { mkdtempSync, mkdirSync, cpSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const repo = process.cwd();
const tmp = mkdtempSync(join(tmpdir(), 'bus903f-ui-'));
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
const M = await mod('web/v2/js/model.js');
const V = await mod('web/v2/js/views.js');
const now = new Date(); const iso = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
const lin = (topic, round, kind, root, parent = '') => ({ topic, round, kind, kind_label: { discuss: '讨论', build: '施工', redecide: '再决策' }[kind] || '', root_task: root, parent_task: parent, display_title: `${topic} · R${round} · ${{ discuss: '讨论', build: '施工', redecide: '再决策' }[kind]}`, has_lineage: true, self_root: root === '', children: [] });
const base = (id, extra) => ({ task_id: id, status: 'adopted', execution: 'pending', title: '旧标题 ' + id, created: iso(now), updated: iso(now), history: [], ...extra });
const decs = [
  base('20260923-000001-aaaa', { bucket: 'agent', implementation: { state: 'dispatched', label: '已建单，等待 builder 领取', blocked: false, wait_sec: 60 }, lineage: lin('Antigravity代理', 1, 'discuss', '20260923-000001-aaaa') }),
  base('20260923-000002-bbbb', { bucket: 'agent', implementation: { state: 'review', label: '待审 · f1 · 0123456', blocked: false, peer: 'f1', commit: '0123456' }, lineage: lin('Antigravity代理', 2, 'build', '20260923-000001-aaaa', '20260923-000001-aaaa') }),
  base('20260923-000003-cccc', { bucket: 'user', handoff: { user_step: '双击 tools\\run_8791.bat', reason: '需要管理员权限改 hosts', verify: '浏览器打开 8791 返回 200', evidence: ['commit 1234567', 'tests/x.py 3 OK'], prepared_by: 'f1', prepared_at: iso(now), state: 'ready' } }),
  base('20260923-000004-dddd', { bucket: 'decide', needs_decision: true, children: ['pending-child-dddd-abc123'], lineage: { ...lin('上传MIME', 1, 'build', '20260923-000004-dddd'), children: ['pending-child-dddd-abc123'] } }),
  { task_id: 'pending-child-dddd-abc123', status: 'draft', title: '上传MIME 失败 · 需决策', summary: 'merge 冲突需要选 A 还是 B', created: iso(now), updated: iso(now), history: [], bucket: '', lineage: lin('上传MIME', 2, 'redecide', '20260923-000004-dddd', '20260923-000004-dddd') },
  base('20260923-000005-eeee', { bucket: 'done', implementation: { state: 'merged', label: '已合并 · f1 · fedcba9', merge_commit: 'fedcba9' } }),
  base('20260923-000006-ffff', { bucket: 'done', execution: 'released' }),
  base('20260923-000007-gggg', { bucket: 'agent', handoff: { user_step: '重启服务', state: 'ready' } }),  // 半成品交接：服务端不会给 user；即使前端兜底也不能进「等你一步」
  { task_id: '20260923-000008-hhhh', status: 'archived', title: '旧单 无谱系', created: iso(now), updated: iso(new Date(now - 60000)), history: [], bucket: '' },
];
const state = { connected: true, peers: [], controller: { online: true, status: 'idle' }, commands: [], nudges: {}, project: 'p', view: 'overview', builders: { groups: ['g'], members: [{ peer: 'f1', online: true }], online_n: 1 },
  projects: [{ slug: 'p', name: 'p', peers: [], decisions: decs }] };
M.store.set(state);
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
const p = state.projects[0];
const h = V.renderOverview(p);
ok(h.includes('已采纳，等待落地') && h.includes('data-landing'), '区块标题仍为「已采纳，等待落地」');
for (const k of ['agent', 'user', 'decide', 'done']) ok(h.includes(`data-bucket="${k}"`), `分页 ${k} 存在`);
ok(h.includes('Agent 处理中') && h.includes('等你一步') && h.includes('等待决策') && h.includes('已完成'), '四个分页标题');
ok((h.match(/data-bucket-item="agent"/g) || []).length === 3, 'Agent 处理中 3 项（含半成品交接项 gggg）');
ok((h.match(/data-bucket-item="user"/g) || []).length === 1 && h.includes('双击 tools\\run_8791.bat') && h.includes('需要管理员权限改 hosts') && h.includes('浏览器打开 8791 返回 200') && h.includes('commit 1234567'), '等你一步：只有准备齐全的一项，含动作 / 原因 / 验证 / 证据');
ok(!h.includes('重启服务'), '半成品 handoff 不进入「等你一步」');
ok(h.includes('data-action="handoff-finish"') && h.includes('data-result="done"') && h.includes('data-result="failed"'), '已完成 / 失败 按钮');
ok(h.includes('data-bucket-item="decide"') && h.includes('施工未取消') && h.includes('上传MIME · R2 · 再决策'), '等待决策：列出子单并说明施工未取消');
ok(h.includes('已合并进主分支、待 Leader 回写 landed：旧标题 20260923-000005-eeee（fedcba9）') && h.includes('已落地：') && h.includes('旧标题 20260923-000006-ffff'), '已完成：merged 与手动落地分列');
ok(h.includes('3 项 · 待派 0 · 等 builder 1'), '计数句仍只数 Agent 处理中：' + (h.match(/\d 项 · 待派[^<]*/) || [''])[0]);
ok(h.includes('Antigravity代理 · R1 · 讨论') && h.includes('Antigravity代理 · R2 · 施工'), '显示标题为「主题 · R轮 · 动作」');
ok(!h.includes('data-action="set-execution"'), '待落地区块无 set-execution 控件');
// 决策页：按 root_task 分组折叠
const d2 = V.renderDecisions(p);
ok(d2.includes('data-thread="20260923-000001-aaaa"') && d2.includes('〔Antigravity代理〕') && d2.includes('共 2 轮'), '同 root 的两轮折叠成一组');
ok(!d2.includes('data-thread="20260923-000004-dddd"') && d2.includes('上传MIME · R1 · 施工') && !d2.includes('pending-child-dddd-abc123'), '决策页只列已定结果：draft 子单不出现，父单单条平铺（子单在概览「等待决策」里）');
ok(!d2.includes('data-thread="20260923-000008-hhhh"') && d2.includes('旧单 无谱系'), '无谱系旧单 self-root，不套折叠、显示原标题');
ok(!d2.includes('data-action="set-execution"'), '决策卡头部无 set-execution 按钮');
// 模型层兜底
ok(M.displayTitle({ task_id: 'x', title: 'T' }) === 'T' && M.displayTitle({ task_id: 'x' }) === 'x', 'displayTitle 兜底原标题 / id');
ok(M.bucketOf({ status: 'adopted' }) === 'agent' && M.bucketOf({ status: 'adopted', execution: 'released' }) === 'done' && M.bucketOf({ status: 'merged' }) === '', 'bucketOf 兜底：默认 agent，不会凭空给 user / decide');
ok(M.threadsOf(decs, p).length === 7 && M.threadsOf(decs, p).find(t => t.root === '20260923-000004-dddd').items.length === 2, 'threadsOf 分组数 = 7（两组多轮 + 五个 self-root），父子同组');
console.log(fails ? `\n${fails} FAILED` : '\nALL PASS'); process.exit(fails ? 1 : 0);
