// 自定义强调色的对比度提示（设置页）：公式与 ADR 0007 / smoke_contrast_tokens 一致；只提示、不拦截保存
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」
const repo = join(dirname(fileURLToPath(import.meta.url)), '..');
const P = await import(join(repo, 'web/v2/js/prefs.js').replace(/\\/g, '/').replace(/^([A-Za-z]):/, 'file:///$1:'));
let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };

ok(Math.abs(P.hexRatio('#000000', '#ffffff') - 21) < 0.01 && Math.abs(P.hexRatio('#777777', '#777777') - 1) < 1e-9, 'hexRatio：黑白 21:1，同色 1:1');
// 各主题底色与正文色从 tokens.css 读（一处真值），逐主题验证：预设强调色自己必须通过检查
const css = readFileSync(join(repo, 'web/v2/styles/tokens.css'), 'utf8');
const block = sel => { const i = css.indexOf(sel + '{'); if (i < 0) return {}; const body = css.slice(i + sel.length + 1, css.indexOf('}', i)); return Object.fromEntries([...body.matchAll(/(--[\w-]+):\s*(#[0-9a-fA-F]{6})/g)].map(x => [x[1], x[2]])); };
const base = block(':root');
const blocks = [['light', base], ...['dark', 'graphite', 'sand', 'contrast'].map(n => [n, { ...base, ...block(`:root[data-theme=${n}]`) }])];
ok(blocks.every(([n, v]) => n === 'light' || block(`:root[data-theme=${n}]`)['--surface']), '5 个主题的底色都解析到了（选择器变了会在这里失败，而不是悄悄少检查）');
for (const [name, v] of blocks) {
  const th = { bg: v['--bg'] || v['--surface'], surface: v['--surface'], surface2: v['--surface-2'], surface3: v['--surface-3'], text: v['--text'] };
  const c = P.accentCheck(v['--accent'], th);
  ok(c && c.ok, `${name}：预设强调色 ${v['--accent']} 自检通过（文字 ${c && c.text.toFixed(2)} · 按钮 ${c && c.button.toFixed(2)}）`);
  for (const bad of ['#ffdd00', '#66ccff', '#ff9900', '#3a3a3a', '#2b3a2f']) {
    const r = P.accentCheck(bad, th), f = P.accentFix(bad, th), rf = P.accentCheck(f, th);
    if (r.ok) { ok(f === bad.toLowerCase(), `${name}：已达标的 ${bad} 不改动`); continue; }
    ok(rf.ok, `${name}：${bad}（文字 ${r.text.toFixed(2)}）→ 自动调整 ${f} 后达标（文字 ${rf.text.toFixed(2)} · 按钮 ${rf.button.toFixed(2)}）`);
  }
}
const th = { bg: '#f4f5f2', surface: '#ffffff', surface2: '#eef1ed', surface3: '#e7ebe5', text: '#1c241f' };
const f = P.accentFix('#ffdd00', th);
const hue = h => { const [r, g, b] = [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255); const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn; if (!d) return 0; const x = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4; return (x * 60 + 360) % 360; };
ok(Math.abs(hue(f) - hue('#ffdd00')) < 12, `自动调整保持色相（#ffdd00 ${hue('#ffdd00').toFixed(0)}° → ${f} ${hue(f).toFixed(0)}°）`);
for (const [name, v] of blocks) {
  const t2 = { bg: v['--bg'] || v['--surface'], surface: v['--surface'], surface2: v['--surface-2'], surface3: v['--surface-3'], text: v['--text'] };
  ok(P.focusRing(v['--accent'], t2) === v['--accent'].toLowerCase(), `${name}：预设强调色直接当焦点环`);
  const all4 = c => Math.min(...['bg', 'surface', 'surface2', 'surface3'].map(k => P.hexRatio(c, t2[k])));
  ok(t2.surface2 && t2.surface3, `${name}：解析到 surface-2 / surface-3`);
  for (const bad of ['#ffdd00', '#66ccff', '#3a3a3a', '#8c8c8c', '#7a7a7a', '#999999']) { const f = P.focusRing(bad, t2); ok(all4(f) >= 3, `${name}：自定义 ${bad} 时焦点环 ${f} 在四种底色上最差 ${all4(f).toFixed(2)} ≥3:1`); }
}
ok(P.accentVars('#ffdd00')['--focus-ring'] && P.accentVars('#1d5c9e')['--focus-ring'] === '#1d5c9e', 'accentVars 带出 --focus-ring（达标用强调色）');
ok(P.accentCheck('nonsense', th) === null && P.accentFix('nonsense', th) === 'nonsense', '非法输入：检查返回 null，调整原样返回');
// 拿不到主题色时不能算「通过」（以前 Math.min() 对空列表得 Infinity）
const none = P.accentCheck('#336699', {});
ok(none && none.text === 0 && none.ok === false, '主题色一个都拿不到：文字对比算 0、不通过（不是 Infinity）');
ok(P.focusRing('#336699', { text: '#111111' }) === '#111111' && P.focusRing('#336699', {}) === 'var(--text)', '拿不到底色：焦点环退回正文色；连正文色也没有就用 var(--text)');
ok(P.accentFix('#336699', {}) === '#336699', '连正文色都拿不到：自动调整原样返回，不报错');
// 只提示不拦截：validate 不因对比度拒绝自定义色
ok(P.validate({ theme: { accent: '#ffdd00' } }).value.theme.accent === '#ffdd00', '低对比自定义色仍可保存（只提示，不拦截）');
const v2 = frontSrc('views2.js'), app = frontSrc('app.js');
ok(/id="accentCheck"[^>]*aria-live="polite"/.test(v2) && v2.includes('data-action="pref-accent-fix"'), '设置页有 aria-live 的对比度提示与「自动调整」按钮');
ok(/prefAccent[\s\S]{0,160}accentHint\(/.test(app) && /'pref-accent-fix'\s*:/.test(app), '拖动取色器时实时刷新提示；自动调整有对应 action');
console.log(`\n${fails ? fails + ' 项失败' : '全部通过'}`);
process.exit(fails ? 1 : 0);
