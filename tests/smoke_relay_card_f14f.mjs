// 任务 f14f 前端冒烟：不起服务，把 web/v2/js 复制到临时目录（pretext 用桩），最小 DOM 垫片导入 views2，喂 priority / relay_resolution 检查顶替顺序卡。
// 跑法：node tests/smoke_relay_card_f14f.mjs（仓库根目录）
import { mkdtempSync, mkdirSync, cpSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」
const repo = process.cwd();
const tmp = mkdtempSync(join(tmpdir(), 'busf14f-ui-'));
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
const V2 = await mod('web/v2/js/views2.js');
const { F } = await mod('web/v2/js/model.js');
const AGROUPS = [{ id: 'claude-fable-5', name: 'Claude Fable 5 系列', tier: 'strong', order: 1, match: ['claude-fable-5'], ready_count: 0, members: [] }, { id: 'glm-5.3', name: 'GLM 5.3 系列', tier: 'daily', order: 2, match: ['glm-5.3'], ready_count: 0, members: [] }];  // 2026-10-02：组名来自工作台自己的分组 S().agent_groups，不再看 Arena
const now = new Date().toISOString().slice(0, 19);
const base = { connected: true, agent_groups: AGROUPS, peers: [{ peer: 'f1', model: 'claude-fable-5', status: 'idle', online: true, last_seen: now }, { peer: 'f2', model: 'claude-fable-5', status: 'idle', online: true, last_seen: now }, { peer: 'opus1', model: 'claude-opus-5', status: 'idle', online: false, last_seen: now }], controller: { online: true, status: 'idle' }, projects: [{ slug: 'p', name: 'p', peers: [], decisions: [] }], project: 'p', commands: [], nudges: {} };
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
// 1 生产现状：五个 peer 名、无 meta → 常驻提示 + 出厂按钮；逐项无可用 → 兜底
st.set({ ...base, priority: { version: 2, relay_order: ['opus1', 'glm1'], relay_order_meta: null, rev: 0, relay_policy: { strategy: 'recent' } }, relay_resolution: { items: [{ item: 'opus1', kind: 'peer', pick: '', online_n: 0, total_n: 1, skipped_n: 0 }, { item: 'glm1', kind: 'unknown', pick: '', online_n: 0, total_n: 0, skipped_n: 0 }], pick: 'f1', via_group: '', fallback: true, fallback_peers: ['f1', 'f2'], refused: false, policy: 'recent', policy_requested: 'recent', policy_note: '' } });
// 0 折叠（2ff6）：默认折叠只留摘要行，不渲染 #relayList / #relayBody；展开后渲染且带 data-rev；aria-expanded 随状态切换
let h = V2.renderPeers();
ok(!h.includes('id="relayList"') && !h.includes('id="relayBody"') && h.includes('data-action="toggle-relay-card"') && h.includes('aria-expanded="false"') && h.includes('aria-controls="relayBody"') && h.includes('名单 2 项 · rev 0') && h.includes('名单无人可用，兜底 f1'), '默认折叠：不渲染 #relayList，只留摘要（人数 / rev / 兜底提示）+ 展开按钮（aria-expanded=false / aria-controls=relayBody）');
F.expanded.add('relay-card'); h = V2.renderPeers();
ok(h.includes('id="relayList"') && h.includes('data-rev="0"') && h.includes('id="relayBody"') && h.includes('aria-expanded="true"'), '展开后渲染 #relayList 且 data-rev 存在，aria-expanded=true');
F.expanded.delete('relay-card'); h = V2.renderPeers();
ok(h.includes('aria-expanded="false"') && !h.includes('id="relayList"') && !h.includes('class="relay-item card"'), '再次折叠：aria-expanded 切回 false，列表与 .relay-item 可拖拽项都不渲染（模型组区块的拖拽不受影响）');
// 既有断言以展开态为前提（默认折叠看不到列表），先置为展开
F.expanded.add('relay-card'); h = V2.renderPeers();
ok(h.includes('从未在界面编辑过') && h.includes('data-action="relay-factory-reset"'), 'relay_order_meta 为 null：常驻提示 + 恢复出厂 SOTA 序按钮');
ok((h.match(/class="relay-item card"/g) || []).length === 2 && h.includes('data-relay-item="opus1"') && h.includes('draggable="true"'), '两项都渲染成可拖拽 .relay-item');
ok(h.includes('名单无人可用，兜底 f1') && h.includes('兜底名单（按名字）：f1、f2'), 'fallback=true 时标出兜底与兜底名单');
ok(h.includes('无可用') && h.includes('未匹配到模型组或 Agent'), '逐项显示无可用 / 未匹配');
ok(h.includes('data-action="set-relay-order"') && h.includes('高级编辑'), '高级编辑入口保留');
ok(h.includes('id="relayAdd"') && h.includes('Claude Fable 5 系列（模型组）') && !h.includes('value="opus1">'), '加入下拉列出未在名单的模型组，已在名单的不重复');
// 2 组化解析：显示解析到谁、组内在线几人；有 meta → 无出厂提示
st.set({ priority: { version: 2, relay_order: ['claude-fable-5', 'glm-5.3'], relay_order_meta: [{ id: 'claude-fable-5', kind: 'group' }, { id: 'glm-5.3', kind: 'group' }], rev: 7, relay_policy: { strategy: 'rotate' } }, relay_resolution: { items: [{ item: 'claude-fable-5', kind: 'group', pick: 'f2', online_n: 2, total_n: 3, skipped_n: 1 }, { item: 'glm-5.3', kind: 'group', pick: '', online_n: 0, total_n: 0, skipped_n: 0 }], pick: 'f2', via_group: 'claude-fable-5', fallback: false, fallback_peers: [], refused: false, policy: 'recent', policy_requested: 'rotate', policy_note: '策略 rotate 未实现，按 recent 处理' } });
h = V2.renderPeers();
ok(!h.includes('从未在界面编辑过'), '有 meta 时不显示出厂提示');
ok(h.includes('模型组 · 在线 2/3 · 跳过离线有排队 1') && h.includes('将顶替 f2') && h.includes('当前顶替 f2 · 来自 Claude Fable 5 系列'), '逐项显示 pick / online_n / total_n / skipped_n 与当前顶替来源');
ok(h.includes('策略 rotate 未实现，按 recent 处理'), '非 recent 策略在界面标出');
ok(h.includes('data-rev="7"') && h.includes('rev 7'), '列表携带 rev');
// 3 空数组：明确关闭替补
st.set({ priority: { version: 2, relay_order: [], relay_order_meta: [], rev: 8, relay_policy: { strategy: 'recent' } }, relay_resolution: { items: [], pick: '', via_group: '', fallback: false, fallback_peers: [], refused: true, policy: 'recent', policy_requested: 'recent', policy_note: '' } });
h = V2.renderPeers();
ok(h.includes('已关闭替补') && h.includes('视为明确关闭替补') && !h.includes('relay-item card'), '空数组：显示已关闭替补，不兜底');
// 4 stateKey：rev / 解析结果变化触发重绘
const src = frontSrc('app.js');
const stateKey = new Function(src.match(/function stateKey\(s\) \{[\s\S]*?\n\}/)[0] + '; return stateKey;')();
const k = s => stateKey({ peers: [], projects: [], commands: [], controller: {}, ...s });
ok(k({ priority: { rev: 1 } }) !== k({ priority: { rev: 2 } }), 'rev 变化改 key');
ok(k({ relay_resolution: { items: [{ item: 'g', pick: 'a', online_n: 1 }] } }) !== k({ relay_resolution: { items: [{ item: 'g', pick: 'b', online_n: 1 }] } }), '解析 pick 变化改 key');
ok(k({}) === k({ priority: undefined, relay_resolution: undefined }), '字段缺失不抖动');
// 5 DRAG 分派：源码层面确认三条路径各自独立
ok(/closest\?\.\('\.group-section, \.relay-item/.test(src), 'dragstart 匹配 .relay-item（选择器可继续扩充）');
ok(/kind === '\.relay-item'\) return commitRelayOrder\(ids/.test(src) && /kind === '\.group-section'\) saveGroupOrder\(ids\)/.test(src) && !/api\/arena\//.test(src), 'dragend 两条提交路径：顶替序→/api/priority，模型组→localStorage；Arena 组卡拖拽随插件搬走（ADR 0014）');
ok(/#groupSections, #relayList/.test(src), 'dragover 放行 #relayList');
ok(/'toggle-relay-card': \(\) => \{ const id = 'relay-card'; F\.expanded\.has\(id\) \? F\.expanded\.delete\(id\) : F\.expanded\.add\(id\); localStorage\.setItem\('bus\.expanded'/.test(src), 'app.js 动作 toggle-relay-card：切换 F.expanded 并持久化到 bus.expanded 后重绘');
console.log(fails ? `\n${fails} FAILED` : '\nALL PASS'); process.exit(fails ? 1 : 0);
