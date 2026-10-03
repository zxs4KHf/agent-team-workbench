// 文字对比度护栏（ADR 0007）：每个主题里，正文类颜色在 bg / surface / surface-2 上都要达到 WCAG AA 4.5:1
import { readFileSync } from 'node:fs';
const css = readFileSync(new URL('../web/v2/styles/tokens.css', import.meta.url), 'utf8');
const prefs = readFileSync(new URL('../web/v2/js/prefs.js', import.meta.url), 'utf8');
let fails = 0; const ok = (c, m) => { console.log((c ? 'ok ' : 'FAIL ') + m); if (!c) fails++; };
const block = sel => { const i = css.indexOf(sel + '{'); if (i < 0) return {}; const body = css.slice(i + sel.length + 1, css.indexOf('}', i)); const v = {}; for (const m of body.matchAll(/(--[\w-]+):\s*(#[0-9a-fA-F]{6})/g)) v[m[1]] = m[2]; return v; };
const lum = hex => { const c = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255).map(v => v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
const base = block(':root');
const themes = { light: base, dark: { ...base, ...block(':root[data-theme=dark]') }, graphite: { ...base, ...block(':root[data-theme=graphite]') }, sand: { ...base, ...block(':root[data-theme=sand]') }, contrast: { ...base, ...block(':root[data-theme=contrast]') } };
for (const [name, v] of Object.entries(themes)) {
  ok(Object.keys(v).length > 10, `${name}: 解析到颜色变量`);
  for (const fg of ['--text', '--muted', '--faint', '--warn', '--danger', '--accent', '--ok', '--info', '--violet']) {
    if (!v[fg]) continue;
    const worst = Math.min(...['--bg', '--surface', '--surface-2'].filter(b => v[b]).map(b => ratio(v[fg], v[b])));
    ok(worst >= 4.5, `${name}: ${fg} ${v[fg]} 最差 ${worst.toFixed(2)}:1 ≥ 4.5`);
  }
  // 状态色放在自己的浅底上（chip.ok / chip.warn …，--x-soft）也要达标（fable2 8c1c）
  for (const x of ['--ok', '--warn', '--danger', '--info', '--violet', '--accent']) if (v[x] && v[x + '-soft']) ok(ratio(v[x], v[x + '-soft']) >= 4.5, `${name}: ${x} 在 ${x}-soft 上 ${ratio(v[x], v[x + '-soft']).toFixed(2)}:1 ≥ 4.5`);
  // 约定：--surface-3（代码块 / hover 底）上不放 --faint 文字；真实页面扫描（scripts/ui_audit.py）未发现此用法
  if (v['--accent'] && v['--accent-ink']) ok(ratio(v['--accent'], v['--accent-ink']) >= 4.5, `${name}: 主按钮文字 ${ratio(v['--accent'], v['--accent-ink']).toFixed(2)}:1 ≥ 4.5`);
  const m = muted => ratio(v['--muted'], v['--surface']) > ratio(v['--faint'], v['--surface']);
  ok(m(), `${name}: muted 仍比 faint 更醒目（层次保留）`);
}
for (const m of prefs.matchAll(/(\w+): \{ label: '[^']+', accent: '(#[0-9a-f]{6})' \}/g)) {
  const t = themes[m[1]]; if (!t) continue;
  ok(m[2].toLowerCase() === (t['--accent'] || '').toLowerCase(), `${m[1]}: prefs 预设强调色与 tokens.css 一致 (${m[2]})`);
}
// 首屏防闪脚本：两个入口页共用 web/v2/js/boot.js（同步加载），预设强调色表须与 tokens.css 一致
const boot = readFileSync(new URL('../web/v2/js/boot.js', import.meta.url), 'utf8');
for (const [name, t] of Object.entries(themes)) if (t['--accent']) ok(boot.toLowerCase().includes(`${name}:'${t['--accent'].toLowerCase()}'`), `${name}: boot.js 防闪表强调色与 tokens.css 一致`);
for (const page of ['index.html', 'hub.html']) {
  const h = readFileSync(new URL('../web/v2/' + page, import.meta.url), 'utf8');
  const tag = h.match(/<script[^>]*src="\/v2\/js\/boot\.js"[^>]*>/);
  const css1 = h.search(/<link[^>]+rel="stylesheet"/);  // 两页都校验：在第一个样式表之前（fable2 a180：原来 hub 只查了在 <body> 之前）
  ok(tag && !/defer|async|module/.test(tag[0]) && h.indexOf(tag[0]) < h.indexOf('<body') && css1 > 0 && h.indexOf(tag[0]) < css1, `${page}：在 <head> 里、样式表之前同步加载 boot.js`);
  ok(!/<script>[^<]*wb\.prefs/.test(h), `${page}：不再有内联的防闪脚本副本`);
}
const baseCss = readFileSync(new URL('../web/v2/styles/base.css', import.meta.url), 'utf8');
ok(/:focus-visible\{outline:2px solid var\(--focus-ring\)/.test(baseCss) && !/:focus-visible\{[^}]*color-mix/.test(baseCss) && /--focus-ring:var\(--accent\)/.test(css), '全局焦点环用实色 --focus-ring（预设=--accent，守卫 ≥4.5 传递到 ≥3:1），不用半透明混色');
for (const f of ['base.css', 'components.css', 'hub.css']) { const c = readFileSync(new URL('../web/v2/styles/' + f, import.meta.url), 'utf8'); const bad = [...c.matchAll(/[^{}]*:focus[^{]*\{[^}]*outline:[^;}]*var\(--accent\)/g)].map(m => m[0].trim().slice(0, 60)); ok(!bad.length, `${f}：焦点规则都用 --focus-ring，不直接用 --accent（自定义低对比强调色会让焦点看不见）${bad.length ? ' → ' + bad.join(' | ') : ''}`); }
process.exit(fails ? 1 : 0);
