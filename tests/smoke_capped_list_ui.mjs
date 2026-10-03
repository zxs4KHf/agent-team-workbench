// 长列表分页（2026-10-02 起：用户要「分页切换」，替代原来的「显示其余 N 项」一次全展开）：
// 每页 LIST_CAP 条 + 「上一页 / 第 x / y 页 / 下一页」；页码记在 F.pages；不超过一页时不出翻页条
import { readFileSync } from 'node:fs';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」
const src = frontSrc('views.js');
let fails = 0; const ok = (c, m) => { console.log((c ? 'ok ' : 'FAIL ') + m); if (!c) fails++; };
// 2026-10-02 起 capped 拆成 pageParts（当前页 + 翻页条）、capped、pagerHtml 三个函数：从 LIST_CAP 一直取到 pagerHtml 结束
const m = src.match(/export const LIST_CAP = \d+;[\s\S]*?function pagerHtml\([\s\S]*?\r?\n\}/);
ok(!!m, '从 views.js 抽到 capped');
const CAP = Number((src.match(/export const LIST_CAP = (\d+);/) || [])[1]);
const F = { expanded: new Set() }; const attr = s => String(s);
const capped = new Function('F', 'attr', m[0].replace(/export /g, '') + '\nreturn capped;')(F, attr);
const items = Array.from({ length: CAP + 4 }, (_, i) => i);
let h = capped('focus', items, i => `<i>${i}</i>`);
ok((h.match(/<i>/g) || []).length === CAP && h.includes(`第 1 / 2 页`) && h.includes(`共 ${CAP + 4} 项`), `第 1 页 ${CAP} 条 + 页码`);
ok(/data-dir="-1" disabled/.test(h) && !/data-dir="1" disabled/.test(h), '第 1 页：上一页禁用、下一页可点');
F.pages = { focus: 1 }; h = capped('focus', items, i => `<i>${i}</i>`);
ok((h.match(/<i>/g) || []).length === 4 && h.includes('<i>' + CAP + '</i>') && h.includes('第 2 / 2 页'), '第 2 页是剩下的 4 条');
F.pages = { focus: 99 }; h = capped('focus', items, i => `<i>${i}</i>`);
ok(h.includes('第 2 / 2 页'), '页码越界时夹到最后一页（列表变短不出空页）');
h = capped('other', items.slice(0, CAP), i => `<i>${i}</i>`);
ok((h.match(/<i>/g) || []).length === CAP && !h.includes('class="pager"'), `恰好 ${CAP} 条不出翻页条`);
ok(capped('x', [], i => i) === '', '空列表输出空串');
const views = src;
ok(views.includes("capped('focus', focus, focusItem)") && views.includes('id="focusList"'), '现在等我用 capped 且保留 focusList（FLIP 依赖）');
ok(views.includes("capped('landing', landing, row)") && views.includes("capped('master', master, focusItem)"), '待落地 / 等 Leader 也分页');
ok(/const DECISION_CAP = 20;/.test(views) && views.includes("capped((q || st) ? 'decisions-filtered' : 'decisions', threadsOf(list, p), threadBlock, DECISION_CAP)"), '决策页每页 20 组；有搜索或状态筛选时也分页，页码与不筛选时分开记（ADR 0015）');
const app = frontSrc('app.js');
ok(/\bpage: t => \{/.test(app) && app.includes('F.pages[t.dataset.id]'), '翻页动作存在并写 F.pages');
process.exit(fails ? 1 : 0);
