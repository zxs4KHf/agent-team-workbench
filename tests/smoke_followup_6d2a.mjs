// 6d2a 追问面板冒烟：不起服务，复制 web/v2/js 到临时目录（pretext 桩），导入真实 core/components/model，
// 从 app.js 源码抽 followupTeams / followupPanel 用真实依赖跑，字符串级断言（仿 smoke_relay_card_f14f.mjs）。
// 每条断言同时在「旧实现」（git show a923f2d:web/v2/js/app.js，主线基线）上跑一遍，确认旧版会 FAIL，拦不住的断言等于没写。
// 跑法：node tests/smoke_followup_6d2a.mjs（仓库根目录）
import { mkdtempSync, mkdirSync, cpSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」
const repo = process.cwd();
const tmp = mkdtempSync(join(tmpdir(), 'bus6d2a-ui-'));
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
const core = await mod('web/v2/js/core.js');
const comps = await mod('web/v2/js/components.js');
const M = await mod('web/v2/js/model.js');
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
const now = new Date(); const ago = new Date(now.getTime() - 7 * 60000).toISOString().slice(0, 19);
M.store.set({ connected: true, peers: [{ peer: 'f1', model: 'claude-fable-5', status: 'idle', online: true, last_seen: now.toISOString().slice(0, 19) }, { peer: 'f2', model: 'claude-fable-5', status: 'idle', online: true, last_seen: now.toISOString().slice(0, 19) }, { peer: 'opus1', model: 'claude-opus-5', status: 'idle', online: false, last_seen: ago }, { peer: 'g1', model: 'glm-5.3', status: 'idle', online: false, last_seen: ago }],
  controller: { online: true, status: 'idle' }, projects: [{ slug: 'p', name: 'p', peers: ['f1', 'f2'], decisions: [], teams: [{ name: '夜班', peers: ['g1', 'opus1'], mode: 'redteam', note: '' }, { name: '白班', peers: ['f1', 'g1'], mode: 'discuss', note: '' }] }], project: 'p', commands: [], nudges: {} });
// 上一轮参与者：f1 在线、opus1 离线、kimi9 已移除
const d = { task_id: 't1', title: '决策一', peers: ['f1', 'opus1', 'kimi9'], mode: 'independent', progress: { meta: { peers: ['f1', 'opus1', 'kimi9'] } } };
const deps = { agentList: M.agentList, agentByName: M.agentByName, onlineAgents: M.onlineAgents, effectiveTeams: M.effectiveTeams, DYNAMIC_TEAM: M.DYNAMIC_TEAM, PROJECT_TEAM: M.PROJECT_TEAM, displayName: M.displayName, titleOf: M.titleOf, F: M.F, relTime: core.relTime, esc: core.esc, attr: core.attr, icon: core.icon, teamChips: comps.teamChips, releaseAttachments: () => {} };
function build(src, label) {
  const p = src.match(/function followupPanel\(d, show\) \{[\s\S]*?\n\}/); const t = src.match(/function followupTeams\(d\) \{[\s\S]*?\n\}/);
  if (!p) return null;
  const body = (t ? t[0] : 'function followupTeams() { return []; }') + '\n' + p[0] + '\n; return { followupPanel, followupTeams };';
  return new Function(...Object.keys(deps), body)(...Object.values(deps));
}
const src = frontSrc('app.js');
let oldSrc = ''; try { oldSrc = execSync('git show a923f2d:web/v2/js/app.js', { encoding: 'utf8' }); } catch { console.log('SKIP  无法 git show a923f2d（主线基线），跳过旧版 FAIL 确认'); }
const cur = build(src, 'cur'); ok(!!cur, '当前 app.js 抽到 followupPanel + followupTeams');
const old = oldSrc ? build(oldSrc, 'old') : null; if (oldSrc) ok(!!old, '旧版 a923f2d 抽到 followupPanel');
const label = (h, peer) => (h.match(new RegExp(`<label class="fchip[^"]*"[^>]*><input type="checkbox" name="fpeer" value="${peer}"[^>]*>[^<]*(<small[^>]*>[^<]*</small>)?</label>`)) || [''])[0];
const checks = [
  ['① 离线 opus1：.dim + 「离线 7 分钟前」且未 checked', h => { const l = label(h, 'opus1'); return /class="fchip [^"]*dim/.test(l) && /离线 /.test(l) && !/checked/.test(l) && !/disabled/.test(l); }],
  ['② 已移除 kimi9：显示「已移除」且 disabled', h => { const l = label(h, 'kimi9'); return /已移除/.test(l) && /disabled/.test(l); }],
  ['③ chips 行三类齐全，「原 Team」count 为 1/3 且 tip 列出缺席', h => h.includes('data-action="followup-team" data-team="原 Team"') && /data-team="原 Team" data-tip="缺席：opus1、kimi9"[^>]*>原 Team<span class="n">1\/3<\/span>/.test(h) && h.includes('data-team="动态在线"') && h.includes('data-team="夜班"') && h.includes('data-team="白班"')],
  ['⑤ 全员离线 Team「夜班」：chip 带 .dim、可点（无 disabled）、tip 全员离线', h => { const c = (h.match(/<button[^>]*data-team="夜班"[^>]*>/) || [''])[0]; return /class="fchip [^"]*dim/.test(c) && !/\sdisabled(\s|>|=)/.test(c) && /全员离线/.test(c); }],
  ['⑥ 在线 f1 默认 checked，在线 f2 不在原 Team 不勾（回归保护，旧版同样成立）', h => /value="f1" checked/.test(label(h, 'f1')) && !/checked/.test(label(h, 'f2')), false],
];
const hNew = cur.followupPanel(d, true);
for (const [name, fn, mustFailOld = true] of checks) { ok(fn(hNew), name); if (old && mustFailOld) { const hOld = old.followupPanel(d, true); ok(!fn(hOld), `   旧版 a923f2d 上同一断言确认 FAIL：${name.slice(0, 2)}`); } }
ok(!hNew.includes('data-action="edit-teams"'), '抽屉 chips 不渲染「管理团队」按钮（避免抽屉内跳转）');
// ④ 源码级：followup-team 动作存在且先全部置 false 再勾；不触发 renderView/morph；FORMS.followup 现存过滤
const act = (src.match(/'followup-team': t => \{[^\n]*\n/) || [''])[0];
ok(act.length > 0, '④ app.js 存在 followup-team 动作');
ok(/\$\$\('input\[name="fpeer"\]', form\)\.forEach\(cb => \{ cb\.checked = false;/.test(act) && act.indexOf('cb.checked = false') < act.indexOf('cb.checked = true'), '④ followup-team 先把全部 fpeer 置 false 再勾（重置语义）');
ok(!/renderView|morph\(/.test(act) && /toast\('该 Team 0 人在线'/.test(act) && /F\.followup\.teamName = t\.dataset\.team/.test(act) && /if \(team\?\.mode\) F\.followup\.mode = team\.mode/.test(act), '④ followup-team 只改 DOM，不 renderView/morph；空结果 toast；写 teamName/mode');
ok(/followup: async form => \{[^\n]*\.filter\(n => agentList\(\)\.some\(a => a\.peer === n\)\)/.test(src), 'FORMS.followup 提交时按现存 Agent 再过滤一道');
if (old) { ok(!/'followup-team'/.test(oldSrc) && !/followupTeams/.test(oldSrc), '   旧版 a923f2d 无 followup-team / followupTeams（④ 在旧版 FAIL）'); }
console.log(fails ? `\n${fails} FAILED` : '\nALL PASS'); process.exit(fails ? 1 : 0);
