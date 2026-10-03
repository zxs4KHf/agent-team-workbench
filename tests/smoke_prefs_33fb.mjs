// 任务 33fb 前端冒烟：品牌与外观个性化（prefs.js 单入口 + wb.prefs 单 key + data-theme 主题）
// 跑法：node tests/smoke_prefs_33fb.mjs（仓库根目录）。不起服务、不碰 console/，状态全在临时目录与桩 localStorage 里造。
import { mkdtempSync, mkdirSync, cpSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」

const repo = process.cwd();
const tmp = mkdtempSync(join(tmpdir(), 'bus33fb-ui-'));
cpSync(join(repo, 'web/v2/js'), join(tmp, 'web/v2/js'), { recursive: true });
mkdirSync(join(tmp, 'web/v2/vendor/pretext'), { recursive: true });
cpSync(join(repo, 'web/v2/vendor/idiomorph.esm.js'), join(tmp, 'web/v2/vendor/idiomorph.esm.js'));
writeFileSync(join(tmp, 'web/v2/vendor/pretext/layout.js'), 'export const prepare = () => ({}); export const layout = () => ({ lines: [] });');
process.on('exit', () => rmSync(tmp, { recursive: true, force: true }));

let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };

// ---- 最小 DOM 垫片：只要 prefs.js 用到的那几点（dataset、style、childNodes 文本节点、querySelector） ----
class Txt { constructor(v) { this.nodeType = 3; this.nodeValue = String(v); } }
const kebab = k => k.replace(/[A-Z]/g, c => '-' + c.toLowerCase());
const camel = k => k.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
// style 必须是常驻对象：真实 DOM 的 `el.style.backgroundImage = …` 与 setProperty 都落到同一个声明块
function mkStyle() {
  const s = {
    setProperty(k, v) { s[camel(k)] = String(v); },
    removeProperty(k) { delete s[camel(k)]; },
    getPropertyValue(k) { return s[camel(k)] ?? ''; }
  };
  return new Proxy(s, { set(t, k, v) { t[k] = v; if (k !== 'length') t[kebab(String(k))] = String(v); return true; } });
}
class El {
  constructor(tag) { this.nodeType = 1; this.tagName = tag; this.childNodes = []; this.dataset = {}; this.attrs = {}; this.style = mkStyle(); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  appendChild(n) { this.childNodes.push(n); return n; }
  insertBefore(n, ref) { this.childNodes.splice(this.childNodes.indexOf(ref), 0, n); return n; }
  get textContent() { return this.childNodes.map(n => (n.nodeType === 3 ? n.nodeValue : n.textContent)).join(''); }
  set textContent(v) { this.childNodes = v === '' ? [] : [new Txt(v)]; }
}
const store = new Map();
globalThis.localStorage = { getItem: k => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k) };
globalThis.location = { search: '', pathname: '/', hash: '', href: 'http://x/' };
globalThis.history = { state: null, pushState() {}, replaceState() {} };
let darkMatches = false;
globalThis.matchMedia = q => ({ media: q, matches: !!darkMatches && /dark/.test(q), addEventListener() {} });
globalThis.requestAnimationFrame = fn => fn();
const root = new El('html');
const mark = new El('span');
const small = new El('small');
// 标题节点故意挂一个 <small> 兄弟文本节点，用来证明 childNodes[0].nodeValue 与 textContent 的差别
const title = new El('span');
title.appendChild(new Txt('Agent 协作台'));
title.appendChild(small);
small.appendChild(new Txt('OLD SUB'));
const sub = new El('small');
sub.appendChild(new Txt('COLLABORATIVE WORKBENCH'));
const nodes = { '.brand-mark': mark, '[data-inline="brand"][data-field="title"]': title, '[data-inline="brand"][data-field="subtitle"]': sub };
globalThis.document = {
  documentElement: root, title: '', activeElement: null, body: new El('body'), hidden: false,
  querySelector: sel => nodes[sel] || null, querySelectorAll: () => [],
  createTextNode: v => new Txt(v), createElement: t => new El(t),
  addEventListener() {}
};
globalThis.window = globalThis;
const mod = rel => import(pathToFileURL(join(tmp, rel)).href);

// ---- 1 单入口与单 key：prefs.js 暴露约定 API，只写 localStorage，不碰服务端 ----
const P = await mod('web/v2/js/prefs.js');
const prefsSrc = readFileSync(join(repo, 'web/v2/js/prefs.js'), 'utf8');
ok(['DEFAULTS', 'PRESETS', 'validate', 'load', 'save', 'apply', 'applyBrand', 'accentVars', 'exportPrefs', 'importPrefs']
  .every(k => P[k] !== undefined), '约定导出齐备（DEFAULTS / PRESETS / validate / load / save / apply / applyBrand / accentVars / exportPrefs / importPrefs）');
ok(P.KEY === 'wb.prefs' && P.SCHEMA === 1, `单 key 为 wb.prefs、schema version ${P.SCHEMA}`);
ok(!/fetch\(|api\(|\/api\/settings/.test(prefsSrc) && (prefsSrc.match(/localStorage/g) || []).length >= 2, 'prefs.js 只有 localStorage 读写，没有任何服务端请求（服务端 0 改动）');

// ---- 2 两条路径的行为差异 ----
store.set('wb.prefs', '{"version":1,"brand":{"title":"这是一份超过十六个字的很长很长很长很长很标题","subtitle":"ok"},"theme":{"mode":"neon","accent":"red","fontScale":9,"navWidth":9999,"density":"huge","radius":0.3},"layout":{"groupOrder":"nope"}}');
const lenient = P.load();
ok(lenient.brand.title === P.DEFAULTS.brand.title && lenient.theme.mode === 'system' && lenient.theme.accent === '' &&
  lenient.theme.fontScale === P.FONT_MAX && lenient.theme.navWidth === 224 && lenient.theme.density === 'comfortable' &&
  lenient.theme.radius === 1 && Array.isArray(lenient.layout.groupOrder),
  'load() 宽容归一：脏字段（超长标题 / 非法 mode / 非 hex accent / 非数组 groupOrder / 越界 navWidth）回落默认，fontScale 9 夹到上限 ' + P.FONT_MAX);
ok(JSON.stringify(lenient) === JSON.stringify(P.load()) && lenient.version === 1, 'load() 结果结构完整且带 version:1，可安全再落盘');
const strictBad = ['{"version":2}', '{"theme":{"mode":"neon"}}', '{"brand":{"title":"' + '字'.repeat(17) + '"}}', '{"theme":{"fontScale":2}}', '{"theme":{"accent":"#12345"}}', '{not json'];
let named = strictBad.map(t => { try { P.importPrefs(t); return ''; } catch (e) { return e.message; } });
ok(named.every((m, i) => m && (i === 5 ? m.includes('不是合法的 JSON') : /version|mode|title|fontScale|accent/.test(m))),
  'importPrefs() 严格校验：非法即抛错并指名字段 —— ' + named.filter(Boolean).slice(0, 5).join(' ｜ '));
ok(P.load().theme.mode === 'system', '导入抛错后 load() 仍是原状（严格路径不落盘）');
store.set('wb.prefs', '{"version":1,"brand":{"title":"我的台子"},"theme":{"mode":"graphite","accent":"#ff7a00","radius":1.75,"fontScale":1.15,"density":"compact","navWidth":260}}');
const round = P.importPrefs(P.exportPrefs());
ok(round.brand.title === '我的台子' && round.theme.mode === 'graphite' && round.theme.accent === '#ff7a00' && round.theme.radius === 1.75 && round.theme.navWidth === 260,
  '导出→导入往返一致（含自定义强调色与三档系数）');
ok(P.importPrefs('{"version":1,"theme":{"mode":"dark"},"fancy":1}').theme.mode === 'dark', '未知顶层字段忽略但保留合法字段');
const big = 'data:image/png;base64,' + 'A'.repeat(Math.ceil((P.MARK_DATAURL_MAX_BYTES + 10) / 3) * 4);
ok(P.dataUrlBytes('data:image/png;base64,QUJD') === 3 && !P.validate({ version: 1, brand: { mark: { dataUrl: big } } }, { strict: true }).ok &&
  !P.validate({ version: 1, brand: { mark: { dataUrl: 'data:image/svg+xml;base64,QUJD' } } }, { strict: true }).ok,
  `dataUrl 限定 png/jpeg/webp 且上限 ${P.MARK_DATAURL_MAX_BYTES / 1024} KB（超长与非白名单类型都拒绝）`);
ok(!P.validate({ version: 1, brand: { mark: { url: 'https://evil/x.png' } } }, { strict: true }).ok &&
  P.validate({ version: 1, brand: { mark: { url: '/uploads/x.png' } } }, { strict: true }).ok,
  'mark.url 只允许站内相对路径（绝对外链拒绝）');

// ---- 3+4 主题属性与 system 决解 ----
darkMatches = true;
let a = P.apply({ ...P.DEFAULTS, theme: { ...P.DEFAULTS.theme, mode: 'system' } });
ok(a.mode === 'dark' && root.dataset.theme === 'dark', 'mode=system 时由 JS 按 matchMedia 决解成 dark（CSS 不双写变量组）');
darkMatches = false;
a = P.apply({ ...P.DEFAULTS, theme: { ...P.DEFAULTS.theme, mode: 'system' } });
ok(a.mode === 'light' && root.dataset.theme === 'light', '系统为亮色时 system 决解成 light');
a = P.apply({ ...P.DEFAULTS, theme: { ...P.DEFAULTS.theme, mode: 'contrast' } });
ok(a.mode === 'contrast' && root.dataset.theme === 'contrast', '具体预设直接写进 documentElement.dataset.theme');
const appSrc = frontSrc('app.js');
ok(/addEventListener\('change'[\s\S]{0,160}?theme\.mode === 'system'/.test(appSrc), 'app.js 监听 prefers-color-scheme change，system 下跟随系统切换');

// ---- 5 缩放系数与 --accent-ink 翻转 ----
a = P.apply({ ...P.DEFAULTS, theme: { ...P.DEFAULTS.theme, mode: 'dark', accent: '', fontScale: 1.15, radius: 1.75, density: 'spacious', navWidth: 300 } });
ok(a.vars['--fs-scale'] === '1.15' && a.vars['--sp-scale'] === '1.15' && a.vars['--r-scale'] === '1.75' && a.vars['--nav-w'] === '300px',
  'fontScale / density / radius / navWidth 落成 --fs-scale、--sp-scale、--r-scale、--nav-w 变量');
ok(P.accentInk('#28664b') === '#ffffff' && P.accentInk('#77c49a') === '#12171b' && P.accentInk('#ff7a00') === '#12171b' && P.accentInk('#0a5cff') === '#ffffff',
  '--accent-ink 按感知亮度翻转：深绿配白字、浅绿配深字（与 tokens.css 现值一致，不用 color-mix 推）');
const av = P.accentVars('#ff7a00');
ok(av['--accent'] === '#ff7a00' && /color-mix\(in srgb, #ff7a00 16%, var\(--surface\)\)/.test(av['--accent-soft']) && av['--accent-ink'] === '#12171b',
  'accentVars 覆盖 --accent / -soft / -strong / -ink 四项');
ok(Object.keys(P.accentVars('')).length === 0 && Object.keys(P.accentVars('garbage')).length === 0, '强调色留空或非法 → 不覆盖，回落到预设变量组');
const tokSrc = readFileSync(join(repo, 'web/v2/styles/tokens.css'), 'utf8');
ok(!/prefers-color-scheme\s*:\s*dark/.test(tokSrc), 'tokens.css 不再用 @media 暗色变量组（改由 data-theme 决定，避免双写）');
ok(['light', 'dark', 'graphite', 'sand', 'contrast'].every(m => tokSrc.includes(`:root[data-theme=${m}]`)), '五套主题变量组都以 :root[data-theme=…] 提供');
ok(/--fs-0:calc\(12px \* var\(--fs-scale\)\)/.test(tokSrc) && /--sp-4:calc\(16px \* var\(--sp-scale\)\)/.test(tokSrc) && /--r-card:calc\(12px \* var\(--r-scale\)\)/.test(tokSrc),
  '--fs-* / --sp-* / --r-* 全部为 calc(Npx * 系数)');
ok(Object.keys(P.PRESETS).every(k => tokSrc.includes(P.PRESETS[k].accent)), 'PRESETS 里每个默认强调色都能在 tokens.css 对应变量组找到（两处不打架）');

// ---- 6 品牌三节点直写 ----
store.set('wb.prefs', '');
P.applyBrand({ ...P.DEFAULTS, brand: { ...P.DEFAULTS.brand, title: '新标题', subtitle: 'keep me lowercase', subtitleUpper: true } });
ok(title.childNodes[0].nodeValue === '新标题' && title.childNodes[1] === small && small.textContent === 'OLD SUB',
  'applyBrand 写标题用 childNodes[0].nodeValue：改文案后标题节点里的 <small> 子节点仍然存活（textContent 会抹掉）');
ok(sub.childNodes[0].nodeValue === 'KEEP ME LOWERCASE', '副标题只在渲染时大写，写入的是大写串');
ok(root.dataset.brandTitle === '新标题' && document.title === '新标题', 'documentElement.dataset.brandTitle 与 document.title 同步');
ok(mark.textContent === P.DEFAULTS.brand.mark.text, '文字图标写入 .brand-mark');
P.applyBrand({ ...P.DEFAULTS, brand: { mark: { type: 'image', text: '', dataUrl: 'data:image/png;base64,QUJD', url: '' }, title: 'T', subtitle: 'S', subtitleUpper: false } });
ok(mark.style.getPropertyValue('background-image') === 'url("data:image/png;base64,QUJD")' && mark.textContent === '', '图片图标改背景图并清空文字');
const htmlSrc = readFileSync(join(repo, 'web/v2/index.html'), 'utf8');
// 防闪脚本已抽到 web/v2/js/boot.js（index.html 与 hub.html 共用，同步加载）；这里对它的代码行做同样的检查
const script = readFileSync(join(repo, 'web/v2/js/boot.js'), 'utf8').split('\n').filter(l => !/^\s*\/\//.test(l) && l.trim()).join('\n');
ok(htmlSrc.indexOf('<script src="/v2/js/boot.js"></script>') > -1 && htmlSrc.indexOf('<script src="/v2/js/boot.js"></script>') < htmlSrc.indexOf('<link rel="stylesheet"'), '防闪脚本（boot.js）位于 <link rel=stylesheet> 之前');
ok(script.split('\n').length <= 10, `防闪脚本 ${script.split('\n').length} 行代码（要求 ≤10 行）`);
ok(/localStorage\.getItem\('wb\.prefs'\)/.test(script) && script.includes("r.dataset.theme=m") && /prefers-color-scheme:dark/.test(script),
  '防闪脚本读同一 key、写 data-theme 并决解 system');
// 一处真值：各主题 --accent 直接从 tokens.css 读，不再在测试里抄一份色值（fable2 8310 建议）
const tokenAccents = [...readFileSync(join(repo, 'web/v2/styles/tokens.css'), 'utf8').matchAll(/--accent:(#[0-9a-fA-F]{6})/g)].map(m => m[1]);
ok(tokenAccents.length === Object.keys(P.PRESETS).length && tokenAccents.every(h => script.toUpperCase().includes(h.toUpperCase())) &&
  Object.values(P.PRESETS).every(v => script.includes(v.accent) && tokenAccents.map(h => h.toLowerCase()).includes(v.accent.toLowerCase())),
  '防闪脚本的预设强调色表与 prefs.js PRESETS 一致');
ok(/0\.2126[\s\S]{0,80}0\.7152[\s\S]{0,80}0\.0722/.test(script) &&
  script.includes('>0.55?') && script.includes("'#12171b'") && script.includes("'#ffffff'"),
  '防闪脚本的 --accent-ink 阈值与 prefs.js accentInk 完全同式（首帧与刷新后不会反色）');
ok(/--fs-scale[\s\S]*--r-scale[\s\S]*--sp-scale[\s\S]*--nav-w/.test(script), '防闪脚本同样写入四个缩放系数');
ok(/data-inline="brand" data-field="title"/.test(htmlSrc) && /data-inline="brand" data-field="subtitle"/.test(htmlSrc), 'index.html 品牌三节点齐备（图标 + 可编辑标题 + 可编辑副标题）');

// ---- 7 设置页卡片 + 8 档位 ----
const { store: st } = await mod('web/v2/js/model.js');
const V2 = await mod('web/v2/js/views2.js');
st.set({ connected: true, peers: [], controller: {}, projects: [{ slug: 'p', name: 'p', peers: [], decisions: [] }], project: 'p', commands: [], nudges: {}, settings: { settings: {}, mcp: {}, peer_root_errors: {}, audit: [] } });
const settingsHtml = V2.renderSettings();
ok(settingsHtml.includes('外观与品牌'), '设置页出现「外观与品牌」卡');
ok(Object.values(P.PRESETS).every(v => settingsHtml.includes(v.label)) && settingsHtml.includes('跟随系统'), '五档预设 + 跟随系统都可选');
ok(settingsHtml.includes('type="color"') && settingsHtml.includes('data-pref="theme.accent"') && settingsHtml.includes('type="range"') && settingsHtml.includes('data-pref="theme.navWidth"'),
  '强调色 color 与侧栏宽度 range 都带 data-pref');
ok(/data-pref="theme\.radius"[\s\S]{0,400}data-value="0\.5"[\s\S]{0,200}data-value="1"[\s\S]{0,200}data-value="1\.75"/.test(settingsHtml), '圆角三段：0.5 / 1 / 1.75');
ok(/data-pref="theme\.fontScale"[\s\S]{0,400}data-value="0\.9"[\s\S]{0,200}data-value="1"[\s\S]{0,200}data-value="1\.15"/.test(settingsHtml), '字号三档：0.9 / 1 / 1.15');
ok(P.validate({ version: 1, theme: { fontScale: 1.15 } }, { strict: true }).ok && P.validate({ version: 1, theme: { fontScale: 0.85 } }, { strict: true }).ok &&
  !P.validate({ version: 1, theme: { fontScale: 0.8 } }, { strict: true }).ok && !P.validate({ version: 1, theme: { fontScale: 1.31 } }, { strict: true }).ok,
  'fontScale 合法域 [0.85, 1.3]（超界严格拒绝）');
ok(P.validate({ version: 1, theme: { fontScale: 1.2 } }, { strict: false }).value.theme.fontScale === 1.2, '宽容路径允许非档位的域内值（滑杆可用连续值，档位只是快捷键）');
ok(settingsHtml.includes('data-action="export-prefs"') && settingsHtml.includes('data-action="import-prefs"') && settingsHtml.includes('data-action="reset-prefs"'), '导出 / 导入 / 恢复默认三个入口');
ok(/t\.dataset\.pref && \(t\.type === 'range' \|\| t\.type === 'color'\)/.test(appSrc) && appSrc.includes('previewPref('), 'input 事件对 range / color 只做 CSS 预览');
ok(/if \(t\.matches\('input\[type=color\],input\[type=range\]'\)\) return;/.test(appSrc), 'click 分发排除 color / range 输入（否则点一下就落盘）');
ok(/'pref-set'[\s\S]{0,260}?t\.dataset\.pref[\s\S]{0,260}?setPrefs\(/.test(appSrc) &&
  /addEventListener\('change'[\s\S]{0,200}?matches\('\[data-action\]'\)/.test(appSrc),
  '落盘只发生在 change：文档级 change 监听按 data-action 分发到 pref-set，pref-set 内部才 setPrefs 写 localStorage');

// ---- 9 旧 key 不迁移不删除 ----
store.set('bus.view', 'running'); store.set('bus.project', 'p'); store.set('bus.expanded', '[]'); store.set('bus.history', '[]');
P.resetPrefs();
ok(store.get('bus.view') === 'running' && store.get('bus.project') === 'p' && store.get('bus.expanded') === '[]' && store.set !== undefined,
  'resetPrefs 之后 bus.view / bus.project / bus.expanded 原样保留（旧 key 不迁移也不删除）');
ok(!/bus\.view|bus\.expanded|bus\.history|bus\.project/.test(prefsSrc), 'prefs.js 里没有任何旧 key 的读写或迁移代码');
ok(P.load().brand.title === P.DEFAULTS.brand.title && P.load().theme.mode === 'system', '恢复默认后回到出厂外观');

// ---- 视图重绘不冲掉品牌 ----
ok(/dataset\.brandTitle/.test(appSrc), 'renderShell 的标题走 dataset.brandTitle（SSE 重绘与切视图后仍保留自定义品牌）');
ok(appSrc.includes("import * as Prefs from './prefs.js'") && frontSrc('views2.js').includes("import * as Prefs from './prefs.js'"),
  'app.js 与 views2.js 都只经 prefs.js 这一个入口读写外观');

console.log(fails ? `\n${fails} 项失败` : '\nALL PASS');
process.exit(fails ? 1 : 0);
