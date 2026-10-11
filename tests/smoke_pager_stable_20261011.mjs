// 翻页条不挪位 + 跳页（2026-10-11 用户：「这个选项会跑」「成果里那个也一样」「没有直接输入页码跳页」「同类按钮都改成这套」）
// 1) 页码格数只看总页数，不随当前页变（≤7 页全列，多了恒 7 格），翻页时中间不增不减；
// 2) 上一页 / 下一页分在两侧、首末页禁用；超过 7 页才有跳页框；
// 3) 全站翻页条都走 pagerNav（views.js 的列表分页、任务历史）；成果页分页条不在网格里；
// 4) CSS：三栏布局 + 分页条贴面板底（margin-top:auto），右侧让开「反馈」浮钮；跳页回车 / 离开即跳，越界夹到首末页。
import { frontSrc } from './_frontsrc.mjs';
const src = frontSrc('views.js');
let fails = 0; const ok = (c, m) => { console.log((c ? 'ok ' : 'FAIL ') + m); if (!c) fails++; };
const m = src.match(/export function pageSlots\([\s\S]*?\r?\n\}\r?\nexport function pagerNav\([\s\S]*?\r?\n\}/);
ok(!!m, '从 views.js 抽到 pageSlots + pagerNav');
const { pageSlots, pagerNav } = new Function(m[0].replace(/export /g, '') + '\nreturn { pageSlots, pagerNav };')();

for (const pages of [2, 5, 7, 8, 9, 20, 100]) {
  const lens = new Set(Array.from({ length: pages }, (_, pg) => pageSlots(pg, pages).length));
  ok(lens.size === 1 && [...lens][0] === Math.min(pages, 7), `${pages} 页：每一页的页码格数都是 ${Math.min(pages, 7)}`);
  const allIn = Array.from({ length: pages }, (_, pg) => pageSlots(pg, pages)).every((s, pg) => s.includes(pg) && s[0] === 0 && s[s.length - 1] === pages - 1);
  ok(allIn, `${pages} 页：当前页、首页、末页始终在格子里`);
}
ok(JSON.stringify(pageSlots(10, 20)) === JSON.stringify([0, -1, 9, 10, 11, -1, 19]), '中间页：首、…、前后、…、末');

const nav = o => pagerNav({ status: 's', act: 'page', idAttr: ' data-id="k"', jumpAttr: ' data-page-jump="k"', ...o });
let h = nav({ pg: 0, pages: 3 });
ok(/data-dir="-1" disabled/.test(h) && !/data-dir="1" disabled/.test(h), '第 1 页：上一页禁用、下一页可点');
ok(h.indexOf('data-dir="-1"') < h.indexOf('pg-nums') && h.indexOf('pg-nums') < h.indexOf('data-dir="1"'), '上一页在左、页码居中、下一页在右');
ok(h.includes('aria-current="page"') && !h.includes('pg-jump'), '当前页标 aria-current；不超过 7 页不出跳页框');
h = nav({ pg: 8, pages: 9 });
ok(/data-dir="1" disabled/.test(h) && h.includes('pg-jump') && h.includes('max="9"') && h.includes('data-page-jump="k"'), '末页下一页禁用；超过 7 页有跳页框（max=总页数）');
ok(h.includes('data-to="3"') || h.includes('data-to="4"'), '页码按钮带 data-to（直达目标页）');
ok(/data-dir="1" disabled/.test(nav({ pg: 1, pages: 9, atEnd: true })), 'atEnd 可覆盖（任务历史按服务端 complete 判断到底）');

const hist = frontSrc('history.js');
ok(hist.includes('pagerNav(') && !/<nav aria-label="分页" class="pager">/.test(hist), '任务历史也用 pagerNav，不再手写翻页条');
ok(/'hist-page'[\s\S]{0,400}dataset\.to/.test(hist), '任务历史翻页认 data-to（页码按钮 / 跳页框）');
ok(/pg\.knownPages/.test(hist), '任务历史取数期间页码格沿用上次页数，不缩成一格');
const art = frontSrc('page-artifacts.js');
ok(art.includes("pageParts('artifacts', list, 12)") && /<\/div>\$\{pg\.pager\}/.test(art), '成果页分页条放在卡片网格外面');
const app = frontSrc('app.js');
ok(/\[data-page-jump\],\[data-hist-jump\]/.test(app) && /Math\.min\(Number\(t\.max\)/.test(app), '跳页框：回车 / 离开即跳，越界夹到首末页');
ok(/page: t => \{[^\n]*dataset\.to/.test(app), '列表翻页动作认 data-to');

import { readFileSync } from 'node:fs';
const css = readFileSync(new URL('../web/v2/styles/components.css', import.meta.url), 'utf8');
ok(/\.pager\{display:grid;grid-template-columns:minmax\(0,1fr\) auto minmax\(0,1fr\)/.test(css), '分页条三栏：两侧等宽、页码居中');
ok(/\.fit-body \.pager\{margin-top:auto\}/.test(css) && /:has\(\.pager\):not\(\[hidden\]\)\{display:flex;flex-direction:column;flex:1 0 auto\}/.test(css), '分页条贴面板底：内容短也不上移');
ok(/\.pg-side\.end\{padding-right:92px\}/.test(css), '贴右缘的面板里下一页让开「反馈」浮钮');
process.exit(fails ? 1 : 0);
