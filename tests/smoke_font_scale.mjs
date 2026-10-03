// 字号跟随「设置 → 字号」：web/v2 下的 JS/CSS 不许再写死 px 字号（tokens.css 定义 --fs-* 除外），最小 11px；迷你趋势图不足两点画虚线空态
import { readFileSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const repo = join(dirname(fileURLToPath(import.meta.url)), '..');
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
const files = [];
for (const d of ['web/v2/js', 'web/v2/styles']) for (const f of readdirSync(join(repo, d))) if (/\.(js|css)$/.test(f) && f !== 'tokens.css') files.push(`${d}/${f}`);
for (const f of ['web/v2/index.html', 'web/v2/hub.html']) { try { readFileSync(join(repo, f)); files.push(f); } catch {} }
// 插件自带页面（ADR 0014，如 plugins/arena/web）同样用工作台 tokens，一并守住
for (const id of readdirSync(join(repo, 'plugins'))) { let fs = []; try { fs = readdirSync(join(repo, 'plugins', id, 'web')); } catch { continue; } for (const f of fs) if (/\.(js|css)$/.test(f)) files.push(`plugins/${id}/web/${f}`); }
ok(files.length >= 10, `扫描 ${files.length} 个前端文件`);
// 所有前端文件与 tokens.css 里定义过的自定义属性。取舍：不分作用域，--x 在任何文件定义过就放过 var(--x, 12px)——启发式，宁漏勿误报
const DEFINED = new Set();
for (const f of [...files, 'web/v2/styles/tokens.css']) for (const m of readFileSync(join(repo, f), 'utf8').matchAll(/(--[\w-]+)\s*:/g)) DEFINED.add(m[1]);
// 守卫与自证共用这一个函数（fable2 a180：原来自证抄了一份正则，改生产正则忘了改副本，自证照样绿）
// 剥 var() → calc() → 「/行高」后看还有没有裸 px；var(--x, 12px) 且 --x 哪里都没定义时，生效的永远是备用 px，单独查
// （var(--fs-1,13px) 这类备用值不算：--fs-1 已定义且随缩放，13px 只是组件脱离 tokens 独立嵌入时的兜底）；JS 的 .style.font / .style.fontSize 赋值同样处理
function hardPx(s, defined = DEFINED) {
  const out = [], bare = d => /\b\d+(?:\.\d+)?px/.test(d.replace(/var\([^()]*\)/g, 'V').replace(/calc\([^()]*\)/g, 'C').replace(/\/\s*\d+(?:\.\d+)?(?:px)?/g, ''));
  const fallbackPx = d => [...d.matchAll(/var\((--[\w-]+)\s*,[^()]*\d+(?:\.\d+)?px/g)].some(m => !defined.has(m[1]));
  for (const m of s.matchAll(/font(?:-size)?\s*:\s*([^;}"'`\n]+)/g)) if (bare(m[1]) || fallbackPx(m[1])) out.push(`font:${m[1].trim().slice(0, 60)}`);
  for (const m of s.matchAll(/\.style\.font(?:Size)?\s*=\s*[`'"]([^`'"]*)/g)) if (bare(m[1]) || fallbackPx(m[1])) out.push(m[0].slice(0, 60));
  return out;
}
const raw = [], tiny = [];
let scaled = 0;
for (const f of files) {
  const s = readFileSync(join(repo, f), 'utf8');
  // fable2 9a21：原先 font: 简写要求带「/行高」，font:11px var(--mono) 这类漏网（假绿）。改为剥掉 var()/calc() 与「/行高」后看还有没有裸 px；
  // 先剥 var() 再剥 calc()——calc(12px * var(--fs-scale, 1)) 内嵌一层括号，直接剥 calc 会整条误报
  for (const hit of hardPx(s)) raw.push(`${f}: ${hit}`);
  for (const m of s.matchAll(/calc\((\d+(?:\.\d+)?)px \* var\(--fs-scale, 1\)\)/g)) { scaled++; if (Number(m[1]) < 11) tiny.push(`${f}: ${m[0]}`); }
}
{ const probe = s => hardPx(s, new Set(['--fs-1']));  // 自证用固定集合，不依赖仓库 tokens 现名
  const bad = ['.a{font:11px var(--mono)}', '.b{font:600 12px var(--mono)}', '.c{font: bold 12px/1 x}', '.d{font: italic 600 12px/1 x}', '.e{font-size:13px}',
               '.j{font-size:var(--x, 12px)}', "el.style.font = '12px x'", "el.style.fontSize = '13px'"];
  const good = ['.k{font:var(--fs-1,13px)}', '.f{font:600 calc(12px * var(--fs-scale, 1)) var(--mono)}', '.g{font:500 calc(13px * var(--fs-scale, 1))/18px var(--font)}', '.h{font-size:var(--fs-1)}', '.i{font:inherit}'];
  ok(bad.every(x => probe(x).length === 1) && good.every(x => !probe(x).length), `守卫自证：${bad.length} 种写死写法都抓到、${good.length} 种合规写法不误报`); }
ok(!raw.length, '没有写死的 px 字号（会无视字号设置）' + (raw.length ? '：\n    ' + raw.slice(0, 8).join('\n    ') : ''));
ok(!tiny.length, '跟随缩放的字号基准都 ≥ 11px' + (tiny.length ? '：' + tiny.slice(0, 5).join('; ') : ''));
ok(scaled >= 150, `跟随缩放的字号写法 ${scaled} 处`);
const tokens = readFileSync(join(repo, 'web/v2/styles/tokens.css'), 'utf8');
ok(/--fs-0:\s*calc\(12px \* var\(--fs-scale\)\)/.test(tokens), 'tokens.css 的 --fs-* 仍按 --fs-scale 缩放');
// 迷你趋势图
const core = readFileSync(join(repo, 'web/v2/js/core.js'), 'utf8');
const src = core.slice(core.indexOf('export function sparkline'), core.indexOf('export function ring')).replace('export ', '');
const sparkline = new Function(src + '; return sparkline;')();
for (const v of [[], [5]]) { const h = sparkline(v); ok(h.includes('class="spark empty"') && !h.includes('area'), `sparkline(${JSON.stringify(v)})：虚线空态，不画面积`); }
const h3 = sparkline([1, 3, 2]); ok(h3.includes('class="spark"') && h3.includes('class="area raw"'), 'sparkline([1,3,2])：正常趋势线');
const css = readFileSync(join(repo, 'web/v2/styles/components.css'), 'utf8');
ok(/\.spark\.empty path\{[^}]*stroke-dasharray/.test(css), 'CSS：.spark.empty 画虚线');
// 提示气泡：平时 display:none 不参与布局（透明气泡曾把页面撑出横向滚动），出现时关键帧淡入
ok(/\.wb-tip\{[^}]*display:none/.test(css) && !/\.wb-tip\{[^}]*opacity:0/.test(css) && !/\[data-tip\]::after/.test(css), '说明层平时 display:none（不再用 opacity:0 占位；旧的 ::after 气泡已移除，不会出双份）');
ok(/\.wb-tip\.on\{display:block;animation:tip-in/.test(css) && /@keyframes tip-in/.test(css), '悬停 / 键盘聚焦时显示并淡入（js/tip.js 加 .on）');
// 导入的超大字号：宽表格任何宽度都可横向滚动
const prefs = readFileSync(join(repo, 'web/v2/js/prefs.js'), 'utf8');
ok(/export const FONT_LARGE = 1\.15;/.test(prefs) && /if \(v\.theme\.fontScale > FONT_LARGE\) r\.dataset\.fsLarge = ''; else delete r\.dataset\.fsLarge;/.test(prefs), 'prefs.apply：字号 > 1.15 时打 data-fs-large');
ok(/html\[data-fs-large\] \.table-scroll\{overflow-x:auto\}/.test(css), 'CSS：data-fs-large 下 .table-scroll 横向滚动');
const arena = readFileSync(join(repo, 'plugins/arena/web/arena.js'), 'utf8');
ok((arena.match(/class="card table-scroll"><table class="table/g) || []).length >= 3, 'Arena 账号 / 会话 / 排行榜表格都在 .table-scroll 里');
const audit = readFileSync(join(repo, 'scripts/ui_audit.py'), 'utf8');
ok(/"--font-scale"/.test(audit) && /fontScale:\{a\.font_scale:g\}/.test(audit), 'ui_audit 支持 --font-scale（模拟放大字号查溢出）');
console.log(`\n${fails ? fails + ' 项失败' : '全部通过'}`); process.exit(fails ? 1 : 0);
