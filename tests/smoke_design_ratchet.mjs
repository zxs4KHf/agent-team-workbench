// ADR 0011 阶段 0 · 设计护栏（棘轮）：前端绕过设计变量 / 组件的写法，数量只能降不能升。
// 跑法：node tests/smoke_design_ratchet.mjs            检查（超上限失败；低于上限提示可以收紧）
//       node tests/smoke_design_ratchet.mjs --update   把上限收紧到当前值（只降不升，升高要手改 json 并在合入说明里写理由）
// 不起服务、不碰运行数据，只读 web/v2 源码。上限在 tests/design_ratchet.json。
import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, dirname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..');
const WEB = join(ROOT, 'web', 'v2');
const LIMITS_PATH = join(HERE, 'design_ratchet.json');

// 只有这些文件允许出现字面颜色：主题变量本身、首帧防闪脚本、预设强调色表（两者与 tokens.css 一致由
// smoke_contrast_tokens.mjs 守着）、浏览器 theme-color meta（必须是字面值）。
const COLOR_EXEMPT = new Set(['styles/tokens.css', 'js/boot.js', 'js/prefs.js', 'index.html']);

// ---- 计数器：每个都是「源码文本 → 命中列表」，纯函数，下面先自测再用于真实文件 ----
const RX = {
  inlineStyle: /\bstyle=\\?["']/g,                                   // 模板里的 style="…"（含转义引号）
  color: /(?:#[0-9a-fA-F]{8}|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3,4})(?![0-9a-zA-Z_-])|\brgba?\(/g,
  fontSize: /font-size\s*:\s*([^;"'`}<>]+)/g,
  button: /<button\b[^>]*?\bclass=\\?["']([^"'\\]*)/g,
  innerHTML: /\.innerHTML\s*\+?=(?!=)/g,
  radius: /border-radius\s*:\s*([^;"'`}<>]+)/g,
};
// 颜色只在「值的位置」才算：跳过选择器里的 #id（例如 #nav、#add）——
// 前一个非空白字符是 { 或 }（规则开头）、或后一个非空白字符是 {（选择器结尾），就是选择器
const isSelector = (src, start, end) => {
  const before = src.slice(Math.max(0, start - 40), start).trimEnd();
  const after = src.slice(end, end + 40).trimStart();
  return before === '' || /[{}]$/.test(before) || after.startsWith('{') || /^[^;{}]*\{/.test(after.split('\n')[0]) && /[{}]\s*$/.test(before.split('\n').pop() || '');
};

export const counters = {
  inline_style: src => src.match(RX.inlineStyle) || [],
  hardcoded_color: src => {
    const out = [];
    for (const m of src.matchAll(RX.color)) {
      if (m[0].startsWith('#') && isSelector(src, m.index, m.index + m[0].length)) continue;
      out.push(m[0]);
    }
    return out;
  },
  // 种类数：把 calc(12px * var(--fs-scale, 1)) 与 calc(12px*var(--fs-scale,1)) 视为同一种
  font_size_value: src => [...src.matchAll(RX.fontSize)].map(m => m[1].replace(/\s+/g, '').replace(/!important$/, '')).filter(v => !v.includes('${')),
  // 按钮 class 组合：只算字面写死的（含 ${…} 的动态 class 无法静态归类）
  button_class: src => [...src.matchAll(RX.button)].map(m => m[1].trim().replace(/\s+/g, ' ')).filter(v => v && !v.includes('${')),
  inner_html_assign: src => src.match(RX.innerHTML) || [],
  // 圆角：只允许 var(--r-*)、0、inherit；50% / 999px / 6px 这类都算绕过（阶段 1 收敛到 --r-pill 等）
  raw_radius: src => [...src.matchAll(RX.radius)].map(m => m[1].trim()).filter(v => !/^(var\(--r-[\w-]+\)|0|inherit|initial)$/.test(v)),
};
// 「种类数」型指标数 distinct，其余数出现次数
const DISTINCT = new Set(['font_size_value', 'button_class']);

function selfTest() {
  const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`计数器自测失败 ${what}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`); };
  eq(counters.inline_style(`<div style="a"></div><i style=\\"b\\"></i><p class="style"></p>`).length, 2, 'inline_style');
  eq(counters.hardcoded_color(`#nav{color:#fff;background:rgba(0,0,0,.1)} #add{border:1px solid #a1b2c3}`), ['#fff', 'rgba(', '#a1b2c3'], 'hardcoded_color');
  eq(counters.font_size_value(`a{font-size:var(--fs-1)} <i style="font-size:calc(12px * var(--fs-scale, 1))">`), ['var(--fs-1)', 'calc(12px*var(--fs-scale,1))'], 'font_size_value');
  eq(counters.button_class(`<button type="button" class="small  ghost" data-x><button class="\${k}">`), ['small ghost'], 'button_class');
  eq(counters.inner_html_assign(`a.innerHTML = x; b.innerHTML += y; if (c.innerHTML === d) {}`).length, 2, 'inner_html_assign');
  eq(counters.raw_radius(`a{border-radius:var(--r-ctl)}b{border-radius:999px}c{border-radius:0}d{border-radius:50%}`), ['999px', '50%'], 'raw_radius');
}

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) { if (name !== 'vendor') out.push(...walk(full)); continue; }
    if (/\.(js|mjs|css|html)$/.test(name)) out.push(full);
  }
  return out;
}

export function measure() {
  const totals = {}, perFile = {};
  const distinct = Object.fromEntries([...DISTINCT].map(k => [k, new Set()]));
  for (const k of Object.keys(counters)) totals[k] = 0;
  for (const full of walk(WEB)) {
    const rel = relative(WEB, full).split(sep).join('/');
    const src = readFileSync(full, 'utf8');
    for (const [k, fn] of Object.entries(counters)) {
      if (k === 'hardcoded_color' && COLOR_EXEMPT.has(rel)) continue;
      if (k === 'inline_style' && rel.endsWith('.css')) continue;
      const hits = fn(src);
      if (!hits.length) continue;
      (perFile[k] ||= {})[rel] = hits.length;
      if (DISTINCT.has(k)) hits.forEach(h => distinct[k].add(h)); else totals[k] += hits.length;
    }
  }
  for (const k of DISTINCT) totals[k] = distinct[k].size;
  return { totals, perFile };
}

selfTest();
const { totals, perFile } = measure();
const limits = JSON.parse(readFileSync(LIMITS_PATH, 'utf8'));
const update = process.argv.includes('--update');
let failed = 0;
for (const [k, v] of Object.entries(totals)) {
  const lim = limits.limits[k];
  if (lim === undefined) { console.log(`FAIL  ${k}=${v}：design_ratchet.json 里没有上限`); failed++; continue; }
  if (v > lim) {
    failed++;
    const top = Object.entries(perFile[k] || {}).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([f, n]) => `${f} ${n}`).join('，');
    console.log(`FAIL  ${k}=${v} 超过上限 ${lim}（${limits.why[k] || ''}）。分布：${top}`);
  } else if (v < lim) {
    console.log(`PASS  ${k}=${v} ≤ ${lim}（可以收紧：node tests/smoke_design_ratchet.mjs --update）`);
    if (update) limits.limits[k] = v;
  } else {
    console.log(`PASS  ${k}=${v} = 上限`);
  }
}
if (update && !failed) {
  limits.updated = new Date().toISOString().slice(0, 10);
  writeFileSync(LIMITS_PATH, JSON.stringify(limits, null, 2) + '\n');
  console.log('上限已收紧并写回 tests/design_ratchet.json');
}
if (failed) {
  console.log(`\n${failed} 项超限：改用 styles/tokens.css 的变量与 components.css 的组件类（ADR 0011）。确需例外，手改 design_ratchet.json 并在合入说明里写理由。`);
  process.exit(1);
}
console.log('\nALL PASS');
