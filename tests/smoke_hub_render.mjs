// hub 页重绘：插件页每 8s 轮询，原来每次 innerHTML 整页替换——入场动画反复重播（列表闪）、展开的「配置」被收起。
// 由 ui_audit 扩到 hub 时扫出（动画中途对比度 1:1）。这里在假 DOM 上真跑 render()，验证：数据没变不动 DOM；同分页刷新不重播动画；展开状态保留。
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const repo = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(repo, 'web/v2/js/hub.js'), 'utf8').replace(/\r\n/g, '\n'), css = readFileSync(join(repo, 'web/v2/styles/hub.css'), 'utf8');
let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
const start = src.indexOf('let lastRender'), end = src.indexOf('\n}\n', src.indexOf('function render()')) + 3;
ok(start > 0 && end > start, '找到 render() 与 lastRender 缓存');
// 最小假 DOM：只实现 render() 用到的 API
let writes = 0, html = '';
const cls = new Set(), opened = [];
const detailsFor = k => ({ setAttribute: (a) => { if (a === 'open') opened.push(k); } });
const main = {
  classList: { toggle: (c, on) => (on ? cls.add(c) : cls.delete(c)) },
  querySelectorAll: sel => sel.startsWith('details') ? [{ closest: () => ({ getAttribute: () => 'daily' }) }] : [],
  querySelector: sel => (sel.includes('daily') ? detailsFor('daily') : null),
  set innerHTML(v) { writes++; html = v; }, get innerHTML() { return html; }
};
const S = { tab: 'plugins', data: 'A' };
const env = { $: () => main, document: { activeElement: null, getElementById: () => null }, CSS: { escape: s => s }, S,
  renderInbox: () => '', renderFeedback: () => '', renderEvents: () => '', renderPlugins: () => `<p>${S.data}</p>` };
const render = new Function(...Object.keys(env), src.slice(start, end) + '\nreturn render;')(...Object.values(env));
render(); ok(writes === 1 && !cls.has('settled'), '首次进入分页：写 DOM，播放入场动画');
render(); render(); ok(writes === 1, '数据没变的轮询：不碰 DOM（不闪、不打断输入、不收起折叠）');
S.data = 'B'; render(); ok(writes === 2 && cls.has('settled'), '同分页数据变化：重绘但不重播入场动画');
ok(opened.includes('daily'), '重绘后恢复展开的「配置」');
S.tab = 'inbox'; render(); ok(writes === 3 && !cls.has('settled'), '切换分页：重新播放入场动画');
ok(/\.hub-main\.settled \.hub-item\{animation:none\}/.test(css), 'hub.css：settled 状态不播 hubIn');
console.log(`\n${fails ? fails + ' 项失败' : '全部通过'}`);
process.exit(fails ? 1 : 0);
