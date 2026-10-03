// 品牌与外观个性化：只存本机浏览器的 localStorage 单 key `wb.prefs`，不写服务端 settings.json（外观属于「这台浏览器」，settings.json 含 peer_tokens，塞装饰字段会扩大暴露面）。
// 两条路径的行为差异是刻意的：load() 宽容归一，任何脏数据都回退到默认，启动绝不白屏；importPrefs() 严格校验并指名字段抛错，导入必须让用户知道哪里错。
export const KEY = 'wb.prefs';
export const SCHEMA = 1;
export const TITLE_MAX = 16;
export const SUBTITLE_MAX = 32;
export const MARK_DATAURL_MAX_BYTES = 400 * 1024;

const deep = v => JSON.parse(JSON.stringify(v));

export const DEFAULTS = {
  version: SCHEMA,
  brand: { mark: { type: 'text', text: '对', dataUrl: '', url: '' }, title: 'Agent 协作台', subtitle: 'Collaborative Workbench', subtitleUpper: true },
  theme: { mode: 'system', accent: '', radius: 1, fontScale: 1, density: 'comfortable', navWidth: 224 },
  layout: { groupOrder: [] },
  updatedAt: ''
};

// 预设名直接就是 documentElement.dataset.theme 的值；配色变量组在 tokens.css，这里只留「该预设的默认强调色」
export const PRESETS = {
  light: { label: '亮色', accent: '#28664b' },
  dark: { label: '暗色', accent: '#77c49a' },
  graphite: { label: '石墨', accent: '#8fb0c9' },
  sand: { label: '暖沙', accent: '#8e5b27' },
  contrast: { label: '高对比', accent: '#0a57f2' }
};
export const THEME_MODES = ['system', 'light', 'dark', 'graphite', 'sand', 'contrast'];
export const DENSITIES = ['compact', 'comfortable', 'spacious'];
export const SP_SCALE = { compact: 0.85, comfortable: 1, spacious: 1.15 };
export const RADIUS_TIERS = [{ key: 'tight', label: '紧凑', value: 0.5 }, { key: 'round', label: '标准', value: 1 }, { key: 'soft', label: '大圆角', value: 1.75 }];
export const FONT_TIERS = [{ key: 's', label: '小', value: 0.9 }, { key: 'm', label: '标准', value: 1 }, { key: 'l', label: '大', value: 1.15 }];

const cpLen = s => Array.from(String(s == null ? '' : s)).length;
const clampN = (v, lo, hi, fb) => { const n = Number(v); return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : fb; };
const HEX = /^#[0-9a-f]{6}$/i;
const DATAURL = /^data:image\/(png|jpeg|webp);base64,/;
const RADIUS_SET = RADIUS_TIERS.map(t => t.value);
export const FONT_MIN = 0.85;
export const FONT_MAX = 1.3;
export const FONT_LARGE = 1.15;  // 界面档位的上限；超过它只可能来自导入

// 校验一份原始数据。strict=false：非法字段逐项回退默认并把回退记进 notes；strict=true：非法字段进 errors，调用方据此拒绝整份导入
export function validate(raw, { strict = false } = {}) {
  const errors = [], notes = [];
  const base = deep(DEFAULTS);
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    if (strict) errors.push('顶层必须是对象');
    return { ok: !strict, errors, unknown: [], value: base };
  }
  if (raw.version !== SCHEMA) {
    if (strict) errors.push(`version 必须为 ${SCHEMA}，收到 ${JSON.stringify(raw.version)}`);
    else notes.push('version 非 1，按默认结构归一');
  }
  const known = new Set(['version', 'brand', 'theme', 'layout', 'updatedAt']);
  const unknown = Object.keys(raw).filter(k => !known.has(k));
  if (strict && unknown.length) notes.push(`忽略未知顶层字段：${unknown.join(', ')}`);

  const b = raw.brand && typeof raw.brand === 'object' ? raw.brand : {};
  const m = b.mark && typeof b.mark === 'object' ? b.mark : {};
  if ('type' in m) {
    if (!['text', 'image'].includes(m.type)) { if (strict) errors.push('brand.mark.type 只能是 text 或 image'); else notes.push('brand.mark.type 非法，回退默认'); }
    else base.brand.mark.type = m.type;
  }
  if ('text' in m) {
    if (cpLen(m.text) > 2) { if (strict) errors.push(`brand.mark.text 最多 2 个字符（当前 ${cpLen(m.text)}）`); else notes.push('brand.mark.text 超 2 字，回退默认'); }
    else base.brand.mark.text = String(m.text == null ? '' : m.text);
  }
  for (const f of ['dataUrl', 'url']) {
    if (!(f in m)) continue;
    const v = String(m[f] || '');
    if (f === 'dataUrl') {
      if (!v) { base.brand.mark.dataUrl = ''; continue; }
      if (!DATAURL.test(v)) { if (strict) errors.push('brand.mark.dataUrl 必须是 png/jpeg/webp 的 base64 data URL'); else notes.push('brand.mark.dataUrl 前缀不合法，回退默认'); continue; }
      if (dataUrlBytes(v) > MARK_DATAURL_MAX_BYTES) { if (strict) errors.push(`brand.mark.dataUrl 超过 ${MARK_DATAURL_MAX_BYTES / 1024} KB`); else notes.push('brand.mark.dataUrl 过大，回退默认'); continue; }
      base.brand.mark.dataUrl = v;
    } else {
      if (!v) { base.brand.mark.url = ''; continue; }
      if (!v.startsWith('/') || v.length > 256) { if (strict) errors.push('brand.mark.url 必须是以 / 开头的站内相对路径且不超过 256 字符'); else notes.push('brand.mark.url 非法，回退默认'); continue; }
      base.brand.mark.url = v;
    }
  }
  if ('title' in b) {
    const v = String(b.title == null ? '' : b.title);
    if (cpLen(v) > TITLE_MAX) { if (strict) errors.push(`brand.title 最多 ${TITLE_MAX} 个字（当前 ${cpLen(v)}）`); else notes.push('brand.title 超长，回退默认'); }
    else base.brand.title = v;
  }
  if ('subtitle' in b) {
    const v = String(b.subtitle == null ? '' : b.subtitle);
    if (cpLen(v) > SUBTITLE_MAX) { if (strict) errors.push(`brand.subtitle 最多 ${SUBTITLE_MAX} 个字（当前 ${cpLen(v)}）`); else notes.push('brand.subtitle 超长，回退默认'); }
    else base.brand.subtitle = v;
  }
  if ('subtitleUpper' in b) {
    if (typeof b.subtitleUpper !== 'boolean') { if (strict) errors.push('brand.subtitleUpper 必须是布尔值'); else notes.push('brand.subtitleUpper 非法，回退默认'); }
    else base.brand.subtitleUpper = b.subtitleUpper;
  }

  const t = raw.theme && typeof raw.theme === 'object' ? raw.theme : {};
  if ('mode' in t) {
    if (!THEME_MODES.includes(t.mode)) { if (strict) errors.push(`theme.mode 只能是 ${THEME_MODES.join(' / ')}`); else notes.push('theme.mode 非法，回退默认'); }
    else base.theme.mode = t.mode;
  }
  if ('accent' in t) {
    const v = String(t.accent || '');
    if (v && !HEX.test(v)) { if (strict) errors.push('theme.accent 必须是 #rrggbb 或留空（留空＝跟随预设）'); else notes.push('theme.accent 非法，回退默认'); }
    else base.theme.accent = v.toLowerCase();
  }
  if ('radius' in t) {
    const v = Number(t.radius);
    if (RADIUS_SET.includes(v)) base.theme.radius = v;
    else if (strict) errors.push(`theme.radius 只能是 ${RADIUS_SET.join(' / ')}`);
    else { notes.push('theme.radius 非法，回退默认'); }
  }
  if ('fontScale' in t) {
    const v = Number(t.fontScale);
    if (!Number.isFinite(v)) { if (strict) errors.push('theme.fontScale 必须是数字'); else notes.push('theme.fontScale 非法，回退默认'); }
    // 导入按区间放行：界面档位是 0.9 / 1 / 1.15，但滑杆与手改可以停在 [0.85, 1.3] 内任何值，导出再导入不该被打回
    else if (strict && (v < 0.85 || v > 1.3)) errors.push(`theme.fontScale 必须在 0.85–1.3 之间，收到 ${v}`);
    else base.theme.fontScale = clampN(v, FONT_MIN, FONT_MAX, DEFAULTS.theme.fontScale);
  }
  if ('density' in t) {
    if (!DENSITIES.includes(t.density)) { if (strict) errors.push(`theme.density 只能是 ${DENSITIES.join(' / ')}`); else notes.push('theme.density 非法，回退默认'); }
    else base.theme.density = t.density;
  }
  if ('navWidth' in t) {
    const v = Number(t.navWidth);
    if (!Number.isFinite(v) || v < 180 || v > 320) { if (strict) errors.push('theme.navWidth 必须在 180–320 之间'); else notes.push('theme.navWidth 越界，回退默认'); }
    else base.theme.navWidth = Math.round(v);
  }

  const l = raw.layout && typeof raw.layout === 'object' ? raw.layout : {};
  if (Array.isArray(l.groupOrder)) base.layout.groupOrder = l.groupOrder.filter(x => typeof x === 'string');
  else if ('groupOrder' in l) { if (strict) errors.push('layout.groupOrder 必须是字符串数组'); else notes.push('layout.groupOrder 非法，回退默认'); }
  if (typeof raw.updatedAt === 'string') base.updatedAt = raw.updatedAt;

  return { ok: strict ? errors.length === 0 : true, errors, unknown, notes, value: base };
}

export function dataUrlBytes(v) {
  const b64 = String(v || '').split(',')[1] || '';
  const pad = b64.endsWith('==') ? 2 : b64.endsWith('=') ? 1 : 0;
  return Math.max(0, Math.floor(b64.length * 3 / 4) - pad);
}

function readRaw() {
  try { const s = localStorage.getItem(KEY); return s ? JSON.parse(s) : null; } catch (_) { return null; }
}

export function load() {
  const r = validate(readRaw(), { strict: false });
  return r.value;
}

export function save(patch) {
  const merged = typeof patch === 'function' ? patch(load()) : { ...load(), ...(patch || {}) };
  const next = validate(merged, { strict: false }).value;
  next.updatedAt = new Date().toISOString();
  try { localStorage.setItem(KEY, JSON.stringify(next)); return { ok: true, prefs: next, persistent: true }; }
  catch (_) { return { ok: false, prefs: next, persistent: false }; }
}

// 局部改动唯一入口：改完立刻落盘并重绘，UI 不再各写各的 key
export function update(patch) {
  const r = save(patch);
  apply(r.prefs);
  applyBrand(r.prefs);
  return r;
}

// system 由 JS 决解：把 'system' 折算成 dark / light，两处（防闪脚本与本函数）逻辑一致，CSS 不双写变量组
export function resolveMode(mode) {
  const m = mode && THEME_MODES.includes(mode) ? mode : DEFAULTS.theme.mode;
  if (m !== 'system') return m;
  try { return matchMedia('(prefers-color-scheme:dark)').matches ? 'dark' : 'light'; } catch (_) { return 'light'; }
}

// 强调色覆盖：soft / strong 交给 color-mix，ink 用相对亮度翻转（color-mix 推不出「深底该配白字」这种关系）
export function accentVars(accent) {
  const a = HEX.test(String(accent || '')) ? String(accent).toLowerCase() : '';
  if (!a) return {};
  return { '--accent': a, '--accent-soft': `color-mix(in srgb, ${a} 16%, var(--surface))`, '--accent-strong': `color-mix(in srgb, ${a} 72%, var(--text))`, '--accent-ink': accentInk(a), '--focus-ring': focusRing(a) };
}

export function accentInk(hex) {
  const h = String(hex || '').replace('#', '');
  if (!/^[0-9a-f]{6}$/i.test(h)) return 'var(--accent-ink)';
  const [r, g, b] = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16));
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 > 0.55 ? '#12171b' : '#ffffff';
}

// 自定义强调色的对比度提示（ADR 0007 同一 WCAG 公式）：只提示、不拦截——颜色是审美选择，工具只负责说清楚后果
const rgbOf = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
const relLum = hex => { const [r, g, b] = rgbOf(hex).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
export function hexRatio(a, b) { const x = relLum(a), y = relLum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
// 当前主题的底色与正文色：运行时从 tokens.css 解析后的 CSS 变量读，不在 JS 里再抄一份
// 含 surface-2 / surface-3：hub 筛选栏等控件落在 surface-2 上，只看 bg / surface 会漏（fable2 13b7：#8c8c8c 在 surface-2 上 2.95）
export function themeColors() {
  const fb = { bg: '#f4f5f2', surface: '#ffffff', surface2: '#eef1ed', surface3: '#e7ebe5', text: '#1c241f' };  // 亮色主题的值，仅在读不到 CSS 变量时兜底
  try { const cs = getComputedStyle(document.documentElement); const g = (k, d) => { const v = cs.getPropertyValue(k).trim(); return HEX.test(v) ? v : d; }; return { bg: g('--bg', fb.bg), surface: g('--surface', fb.surface), surface2: g('--surface-2', fb.surface2), surface3: g('--surface-3', fb.surface3), text: g('--text', fb.text) }; } catch (_) { return fb; }
}
// 一个底色都拿不到时算 0（不达标）：Math.min() 对空列表是 Infinity，以前会把「没法算」当成「通过」；宁可提示、焦点环退回正文色
const worstOn = (a, th, keys) => { const r = keys.map(k => th[k]).filter(c => HEX.test(String(c || ''))).map(c => hexRatio(a, c)); return r.length ? Math.min(...r) : 0; };
// text：强调色当文字（链接、选中项）在 bg / surface / surface-2 上的最差值（与 ADR 0007 守卫同口径）；button：按钮底上配 accentInk 字色
export function accentCheck(accent, th = themeColors()) {
  if (!HEX.test(String(accent || ''))) return null;
  const a = String(accent).toLowerCase(), text = worstOn(a, th, ['bg', 'surface', 'surface2']), button = hexRatio(accentInk(a), a);
  return { text, button, ok: text >= 4.5 && button >= 4.5 };
}
// 焦点环颜色：强调色在所有底色（bg / surface / surface-2 / surface-3）上都 ≥ 3:1（WCAG 1.4.11 非文字）就用它，否则回退正文色——用户可以选低对比强调色，但焦点必须看得见
export function focusRing(accent, th = themeColors()) {
  if (!HEX.test(String(accent || ''))) return 'var(--accent)';
  const a = String(accent).toLowerCase();
  return worstOn(a, th, ['bg', 'surface', 'surface2', 'surface3']) >= 3 ? a : HEX.test(String(th.text || '')) ? th.text : 'var(--text)';
}
// 自动调整：在原色基础上往正文色方向每次混 2%（sRGB 插值，色相近似保持而非严格恒定），到两项都刚好达标为止（和 ADR 0007 重算主题色同一思路）
export function accentFix(accent, th = themeColors()) {
  const c0 = accentCheck(accent, th); if (!c0 || c0.ok) return c0 ? String(accent).toLowerCase() : accent;
  if (!HEX.test(String(th.text || ''))) return String(accent).toLowerCase();   // 连正文色都拿不到：没有调整方向，原样返回
  const a = rgbOf(String(accent).toLowerCase()), t = rgbOf(th.text);
  for (let k = 1; k <= 50; k++) {
    const hex = '#' + a.map((v, i) => Math.round(v + (t[i] - v) * k / 50).toString(16).padStart(2, '0')).join('');
    if (accentCheck(hex, th).ok) return hex;
  }
  return th.text;
}

export function apply(p = load()) {
  const v = validate(p, { strict: false }).value;
  const r = document.documentElement;
  const mode = resolveMode(v.theme.mode);
  r.dataset.theme = mode;
  r.dataset.density = v.theme.density;
  // 超过界面最大档位（导入可到 1.3）时打标记：宽表格改为任何宽度都可横向滚动（components.css html[data-fs-large]）
  if (v.theme.fontScale > FONT_LARGE) r.dataset.fsLarge = ''; else delete r.dataset.fsLarge;
  const vars = { ...accentVars(v.theme.accent || (PRESETS[mode] || PRESETS.light).accent), '--fs-scale': String(v.theme.fontScale), '--sp-scale': String(SP_SCALE[v.theme.density] || 1), '--r-scale': String(v.theme.radius), '--nav-w': `${v.theme.navWidth}px` };
  for (const [k, val] of Object.entries(vars)) { if (val == null || val === '') r.style.removeProperty(k); else r.style.setProperty(k, val); }
  return { mode, vars };
}

// 品牌三节点直写：topbar 不参与 morph，所以刷新与 SSE 重绘都不会打回硬编码文案
export function applyBrand(p = load()) {
  const v = validate(p, { strict: false }).value;
  const b = v.brand, r = document.documentElement;
  const mark = $('.brand-mark');
  if (mark) {
    const img = b.mark.type === 'image' && (b.mark.dataUrl || b.mark.url);
    if (img) {
      mark.style.backgroundImage = `url("${b.mark.dataUrl || b.mark.url}")`;
      mark.textContent = '';
    } else {
      mark.style.backgroundImage = '';
      mark.textContent = b.mark.text;
    }
    mark.setAttribute('aria-label', '编辑品牌标识');
  }
  const title = $('[data-inline="brand"][data-field="title"]') || $('.brand-text');
  if (title) setFirstText(title, b.title);
  const sub = $('[data-inline="brand"][data-field="subtitle"]');
  if (sub) setFirstText(sub, b.subtitleUpper ? String(b.subtitle).toUpperCase() : b.subtitle);
  r.dataset.brandTitle = b.title;
  document.title = b.title;
  return b;
}

function $(sel) { return typeof document === 'undefined' ? null : document.querySelector(sel); }

// 必须写 childNodes[0].nodeValue：整棵子树用 textContent 覆盖会抹掉兄弟节点（标题里就挂着 <small>）
function setFirstText(node, text) {
  const t = String(text == null ? '' : text);
  const first = node.childNodes && node.childNodes[0];
  if (first && first.nodeType === 3) first.nodeValue = t;
  else if (first) node.insertBefore(document.createTextNode(t), first);
  else node.appendChild(document.createTextNode(t));
  return t;
}

export function exportPrefs(p = load()) {
  return JSON.stringify({ ...validate(p, { strict: false }).value, updatedAt: new Date().toISOString() }, null, 2);
}

export function importPrefs(text) {
  let raw;
  try { raw = JSON.parse(String(text)); }
  catch (e) { throw new Error(`不是合法的 JSON：${e && e.message || e}`); }
  const r = validate(raw, { strict: true });
  if (!r.ok) throw new Error(`外观配置校验未通过：${r.errors.slice(0, 6).join('；')}`);
  return r.value;
}

// 导入走「先解析校验、再落盘重绘」，任何一步抛错都不碰现状
export function commitImport(text) {
  const next = importPrefs(text);
  const r = save(next);
  apply(r.prefs); applyBrand(r.prefs);
  return r.prefs;
}

export function resetPrefs() {
  const r = save(deep(DEFAULTS));
  apply(r.prefs); applyBrand(r.prefs);
  return r.prefs;
}
