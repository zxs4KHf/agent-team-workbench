// cfa6 集成冒烟：903f + fedb + af57 合并后的阻断修复。纯函数用真实模块跑；其余为源码级守卫（保证修复没在合并里丢掉）。
// 跑法：node tests/smoke_integration_cfa6.mjs
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」
const repo = process.cwd();
const I = await import(pathToFileURL(join(repo, 'web/v2/js/interact.js')).href);
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// ---- A. 可见子集合回完整 task_order ----
const full = ['a', 'b', 'c', 'd', 'e', 'f'];
ok(eq(I.mergeVisibleOrder(full, ['e', 'b', 'a']), ['e', 'b', 'c', 'd', 'a', 'f']), '搜索只显示 a/b/e：拖成 e,b,a → 只在原槽位 0/1/4 内重排，c/d/f 原位不丢');
ok(eq(I.mergeVisibleOrder(full, ['a', 'b', 'e']), full), '可见顺序未变 → 整表不变');
ok(eq(I.mergeVisibleOrder(full, ['f', 'a']), ['f', 'b', 'c', 'd', 'e', 'a']), '首尾互换只动首尾');
ok(eq(I.mergeVisibleOrder(full, []), full), '空可见集 → 整表原样');
ok(eq(I.mergeVisibleOrder([], ['x', 'y']), ['x', 'y']), '空表 → 直接用可见顺序');
ok(eq(I.mergeVisibleOrder(full, ['b', 'NEW', 'a']), ['b', 'NEW', 'a', 'c', 'd', 'e', 'f']), '可见新 ID 紧跟其可见前驱 b 插入，a/b 在原槽位重排');
ok(eq(I.mergeVisibleOrder(full, ['NEW1', 'c']), ['a', 'b', 'NEW1', 'c', 'd', 'e', 'f']), '新 ID 无在表前驱 → 放到该可见组最前（c 之前）');
ok(eq(I.mergeVisibleOrder(['a', 'b', 'b', 'c'], ['c', 'c', 'a']), ['c', 'b', 'a']), '重复输入去重后再合并');
ok(eq(I.mergeVisibleOrder(full, ['zz']), ['zz', ...full]), '只有一个表外 ID → 放最前，不覆盖任何旧项');
ok(eq(I.mergeVisibleOrder(full, ['a', 'b', 'c', 'd', 'e', 'f'].reverse()), ['f', 'e', 'd', 'c', 'b', 'a']), '全部可见时等价于整表重排');
ok(eq(I.moveInList(['a', 'b', 'c'], 'c', -1), ['a', 'c', 'b']) && eq(I.moveInList(['a', 'b', 'c'], 'a', -1), ['a', 'b', 'c']) && eq(I.moveInList(['a', 'b', 'c'], 'a', 1), ['b', 'a', 'c']) && eq(I.moveInList(['a', 'b'], 'zz', 1), ['a', 'b']), 'moveInList 上移 / 下移 / 越界不动 / 未知 id 不动');

// ---- B. 键盘 / 脚本 click 不依赖陈旧 pointerdown（resolveAction 收到 null downEl 即可命中）----
class El { constructor(t, a = {}, c = []) { this.nodeType = 1; this.tag = t; this.attrs = a; this.children = []; this.parent = null; c.forEach(x => { x.parent = this; this.children.push(x); }); }
  get dataset() { const o = {}; for (const [k, v] of Object.entries(this.attrs)) if (k.startsWith('data-')) o[k.slice(5)] = v; return o; }
  matches(sel) { return sel.split(',').some(s => { s = s.trim(); const m = s.match(/^([a-z]+)?(\[([\w-]+)(?:="?([^"\]]*)"?)?\])?$/); if (!m) return false; if (m[1] && m[1] !== this.tag) return false; if (m[3]) return m[4] === undefined ? m[3] in this.attrs : this.attrs[m[3]] === m[4]; return true; }); }
  closest(sel) { let n = this; while (n) { if (n.matches(sel)) return n; n = n.parent; } return null; }
  contains(n) { while (n) { if (n === this) return true; n = n.parent; } return false; } }
const chev = new El('button', { type: 'button', 'data-action': 'toggle-task', 'data-id': 't1' });
const row = new El('div', { 'data-action': 'toggle-task', 'data-id': 't1', role: 'button' }, [chev]);
const elsewhere = new El('div', {});
ok(I.resolveAction(row, elsewhere) === null && I.resolveAction(row, null) === row, '鼠标在别处按下→Tab→Enter：detail=0 时传 null downEl 才能命中（app.js 已按 e.detail===0 传 null）');
ok(I.resolveAction(chev, null) === chev, '展开箭头有自己的 data-action，点击命中箭头而非被当嵌套控件拦截');

// ---- 源码级守卫 ----
const app = frontSrc('app.js'), views = frontSrc('views.js'), model = readFileSync(join(repo, 'web/v2/js/model.js'), 'utf8'), core = readFileSync(join(repo, 'web/v2/js/core.js'), 'utf8');
ok(app.includes("e.detail === 0 ? null : downEl"), 'click 委托：detail=0 不用陈旧 pointerdown');
ok(!app.includes("closest?.('[data-task-drag]')) e.stopPropagation(); }, true)") && app.includes("if (e.target.closest?.('[data-task-drag]')) return;"), '不在 document 捕获阶段吞 click；只把手柄点击排除在动作外');
ok(views.includes('data-action="toggle-task" data-id="${attr(d.task_id)}" aria-label="${open') , '展开箭头按钮有明确 toggle-task action');
ok(app.includes("if (view !== S().view) { const now = Date.now(); if (now - (switchView._last || 0) < 300) return; switchView._last = now; }"), 'switchView 只在真切换记节流戳');
ok(app.includes('没有「${view}」这个视图，仍停留在') && app.includes('链接里的视图「${v}」不存在，已回到当前页'), '未知视图：可读错误 + 留在当前页并修正 URL');
ok(app.includes("commitTaskOrder(mergeVisibleOrder(taskOrder(), ids))"), '拖动结束用 mergeVisibleOrder 合回完整表');
ok(app.includes("moveTaskByKeyboard(t, e.key === 'ArrowUp' ? -1 : 1)") && views.includes('data-task-drag role="button" tabindex="0" aria-keyshortcuts="ArrowUp ArrowDown"'), '手柄可聚焦 + ↑↓ 真实调序');
ok(app.includes("store.set({ priority: next.priority, lease: next.lease, connected: true, lastRefresh: Date.now(), time: data.time }); return; }"), 'minorOnly 成功刷新更新 connected / lastRefresh / time');
ok(app.includes("if (weight) w[peer] = weight; else delete w[peer];") && app.includes('权重暂不影响派发顺序'), '清空权重删键；权重未被调度消费如实标明');
ok(app.includes("await refresh();  // 不强制整页"), 'commitPriority 不再 force 整页 + 局部双重绘');
ok(app.includes("requestAnimationFrame(() => { if (k._flipRestore !== restore) return;"), 'FLIP rAF 回调校验 _flipRestore 令牌');
ok(model.includes("d.status !== h.status") || /hiddenNow[\s\S]{0,300}status/.test(model), '隐藏快照状态变化语义保留（未引回终态白名单）');
ok(app.includes("store.on(['time'], s => { renderShell(s); tickRelTime(); })") && views.includes('data-reltime=') && app.includes('function tickRelTime()'), '相对时间局部更新，不再整页 morph');
ok(app.includes("store.on(['priority', 'lease'], s => renderScoped(s))") && app.includes('function renderScoped'), 'af57 局部 morph 订阅保留');
ok(core.includes('keepNode(oldNode, document.activeElement, newNode)'), 'fedb morph 守卫保留');
ok(/landingSection\(buckets[,)]/.test(views) && views.includes('id="focusList"') && views.includes('threadBlock'), '903f 四桶 / 折叠 + FLIP focusList 共存（2026-10-02 起落地区放进概览标签页：landingSection(buckets, true)）');
ok(app.includes('function flipList') && app.includes('lineagePanel(d)') && app.includes("openEntityFromUrl()") && app.includes('function leaderBoard') === false, 'FLIP、903f 抽屉谱系、fedb 深链在 app.js 里同时存在');
ok(app.includes("'handoff-finish'") && app.includes("'spawn-child'"), 'handoff / 子单动作保留');
console.log(fails ? `\n${fails} FAILED` : '\nALL PASS'); process.exit(fails ? 1 : 0);
