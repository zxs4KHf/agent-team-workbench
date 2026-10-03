// boot.js 首屏防闪脚本在 vm 里真跑：按 localStorage 'wb.prefs' 设好主题 / 密度 / 字号 / 圆角 / 强调色变量；脏数据不抛错
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const code = readFileSync(new URL('../web/v2/js/boot.js', import.meta.url), 'utf8');
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
function run(prefs, dark = false) {
  const vars = {}, root = { dataset: {}, style: { setProperty: (k, v) => { vars[k] = v; } } };
  const ctx = { localStorage: { getItem: () => (prefs === undefined ? null : typeof prefs === 'string' ? prefs : JSON.stringify(prefs)) }, matchMedia: () => ({ matches: dark }), document: { documentElement: root } };
  vm.runInNewContext(code, ctx); return { ds: root.dataset, vars };
}
let r = run(undefined);
ok(r.ds.theme === 'light' && r.ds.density === 'comfortable' && r.vars['--accent'] === '#28664b', '没有偏好：亮色、标准密度、预设强调色');
r = run(undefined, true); ok(r.ds.theme === 'dark' && r.vars['--accent'] === '#77c49a', '没有偏好 + 系统深色：dark 与其预设强调色');
r = run({ theme: { mode: 'sand', fontScale: 1.15, density: 'compact', radius: 1.75, navWidth: 260 } });
ok(r.ds.theme === 'sand' && r.ds.density === 'compact', '暖沙 + 紧凑');
ok(r.vars['--fs-scale'] === 1.15 && r.vars['--sp-scale'] === 0.85 && r.vars['--r-scale'] === 1.75 && r.vars['--nav-w'] === '260px', '字号 / 间距 / 圆角 / 导航宽度都在首帧设好（hub 页原来没有）');
r = run({ theme: { mode: 'light', accent: '#ffdd00' } });
ok(r.vars['--accent'] === '#ffdd00' && r.vars['--accent-ink'] === '#12171b', '浅色自定义强调色：按钮字用深色');
ok(/color-mix\(in srgb,#ffdd00 16%/.test(r.vars['--accent-soft'] || ''), '浅底 --accent-soft 按自定义色混合');
r = run({ theme: { accent: 'red', mode: 'dark' } }); ok(r.vars['--accent'] === '#77c49a', '非法强调色：回退预设');
let threw = false; try { run('{oops'); } catch { threw = true; } ok(!threw, '脏 JSON 不抛错（启动绝不白屏）');
console.log(`\n${fails ? fails + ' 项失败' : '全部通过'}`); process.exit(fails ? 1 : 0);
