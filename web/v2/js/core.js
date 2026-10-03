// 基础工具、状态存储与 DOM 同步（idiomorph 做 morph，不整块重绘）
import { keepNode } from './interact.js';
import { Idiomorph } from '../vendor/idiomorph.esm.js';

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const esc = v => String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
export const attr = v => esc(v).replace(/`/g, '&#96;');
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const uid = () => Math.random().toString(36).slice(2, 9);

const dtf = new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
const tf = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
const nf = new Intl.NumberFormat('zh-CN');
export const fmtNum = n => nf.format(Number(n) || 0);
export function parseIso(v) { if (!v) return null; const d = new Date(String(v).replace(' ', 'T')); return Number.isNaN(d.getTime()) ? null : d; }
export function fmtDate(v) { const d = parseIso(v); return d ? dtf.format(d) : '—'; }
export function fmtTime(v) { const d = parseIso(v); return d ? tf.format(d) : '—'; }
export function minutesSince(v) { const d = parseIso(v); return d ? Math.max(0, Math.round((Date.now() - d.getTime()) / 60000)) : 0; }
export function relTime(v) {
  const m = minutesSince(v); if (!parseIso(v)) return '—';
  if (m < 1) return '刚刚'; if (m < 60) return `${m} 分钟前`; if (m < 1440) return `${Math.floor(m / 60)} 小时前`; return `${Math.floor(m / 1440)} 天前`;
}
// ADR 0010 第 3 步：在线/离线与「多久没心跳」一律以后端算好的 age_sec 为准。
// 浏览器本地时间去跟时间戳相减，在机器时区与 Agent 时区不一致时会差好几个小时；
// 后端已经用带偏移的时间戳算过了，前端只负责把秒数翻译成人话。age_sec 缺失时才退回 relTime。
export function relAge(sec, fallbackIso) {
  if (sec === null || sec === undefined || Number.isNaN(Number(sec))) return relTime(fallbackIso);
  // 后端 heartbeat_age 解析失败返回 1e9 哨兵：渲染成「失联/未知」，不翻译成天文数字天数
  if (Number(sec) >= 1e9) return '失联/未知';
  const m = Math.max(0, Math.floor(Number(sec) / 60));
  if (m < 1) return '刚刚'; if (m < 60) return `${m} 分钟前`; if (m < 1440) return `${Math.floor(m / 60)} 小时前`; return `${Math.floor(m / 1440)} 天前`;
}

// 状态存储：单一状态树，按 key 订阅
export function createStore(initial) {
  let state = { ...initial };
  const subs = new Map();
  return {
    get: () => state,
    set(patch) {
      const changed = [];
      for (const k of Object.keys(patch)) { if (state[k] !== patch[k]) { changed.push(k); } }
      if (!changed.length) return;
      state = { ...state, ...patch };
      const fired = new Set();
      for (const k of changed) for (const fn of (subs.get(k) || [])) { if (!fired.has(fn)) { fired.add(fn); fn(state, k); } }
      for (const fn of (subs.get('*') || [])) { if (!fired.has(fn)) { fired.add(fn); fn(state, changed); } }
    },
    on(keys, fn) { for (const k of [].concat(keys)) { if (!subs.has(k)) subs.set(k, new Set()); subs.get(k).add(fn); } return () => { for (const k of [].concat(keys)) subs.get(k)?.delete(fn); }; }
  };
}

// morph：把新 HTML 合并进现有节点，保留焦点、输入值、滚动位置
export function morph(node, html, opts = {}) {
  if (!node) return;
  // ignoreActiveValue 只在焦点落在输入类元素上时打开：它会让「有焦点的元素」只换属性、不换内容。
  // 焦点在按钮上时（刚点完「显示其余 N 项」）按钮被对到另一个元素上，就会变成「属性是新卡片、文字还是旧按钮」——
  // 竖着一列字、其余全空的怪块（2026-10-02 概览实测）。输入框照旧保护，正在打的字不丢。
  const act = document.activeElement, typing = !!act && (act.isContentEditable === true || !!act.matches?.('input,textarea,select'));
  Idiomorph.morph(node, html, {
    morphStyle: opts.outer ? 'outerHTML' : 'innerHTML',
    ignoreActiveValue: typing,
    callbacks: {
      beforeNodeMorphed(oldNode, newNode) { return keepNode(oldNode, document.activeElement, newNode); },  // e131：data-keep 子树、聚焦中的 input/textarea/select/contenteditable 不参与 morph
      beforeAttributeUpdated(name, node) { if (node.matches?.('details') && name === 'open') return false; return true; },
      ...(opts.callbacks || {})
    }
  });
}

// 数字滚动动画
export function animateNumber(el, to, ms = 600) {
  const from = Number(el.dataset.num ?? el.textContent) || 0;
  if (from === to || matchMedia('(prefers-reduced-motion: reduce)').matches) { el.textContent = fmtNum(to); el.dataset.num = to; return; }
  const t0 = performance.now();
  const step = t => { const k = clamp((t - t0) / ms, 0, 1); const e = 1 - Math.pow(1 - k, 3); el.textContent = fmtNum(Math.round(from + (to - from) * e)); if (k < 1) requestAnimationFrame(step); else el.dataset.num = to; };
  requestAnimationFrame(step);
}

// FLIP：列表重排动画
export function flip(container, mutate) {
  const before = new Map();
  for (const el of container.children) if (el.dataset.key) before.set(el.dataset.key, el.getBoundingClientRect());
  mutate();
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  for (const el of container.children) {
    const b = before.get(el.dataset.key); if (!b) { el.classList.add('fade-up'); continue; }
    const a = el.getBoundingClientRect(); const dx = b.left - a.left, dy = b.top - a.top;
    if (!dx && !dy) continue;
    el.animate([{ transform: `translate(${dx}px,${dy}px)` }, { transform: 'none' }], { duration: 320, easing: 'cubic-bezier(.16,1,.3,1)' });
  }
}

export function viewTransition(fn) {
  if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) return document.startViewTransition(fn);
  fn(); return null;
}

// URL 状态：筛选、标签页写入 query
export const url = {
  get(k, d = '') { return new URLSearchParams(location.search).get(k) ?? d; },
  set(patch, replace = true) {
    const p = new URLSearchParams(location.search);
    for (const [k, v] of Object.entries(patch)) { if (v === '' || v == null || v === false) p.delete(k); else p.set(k, v); }
    const next = `${location.pathname}${p.toString() ? '?' + p : ''}${location.hash}`;
    history[replace ? 'replaceState' : 'pushState'](null, '', next);
  }
};

// 图标（内联 SVG，stroke 1.6）
const ICONS = {
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  close: '<path d="M18 6 6 18M6 6l12 12"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  refresh: '<path d="M20 11A8 8 0 1 0 18.3 16"/><path d="M20 4v7h-7"/>',
  check: '<path d="m5 12 5 5L20 7"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  edit: '<path d="M4 20h4l10.5-10.5a2 2 0 0 0-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
  send: '<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4z"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><circle cx="17" cy="9" r="2.5"/><path d="M21.5 19a5 5 0 0 0-5-5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  play: '<path d="M6 4l14 8-14 8z"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  box: '<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
  compose: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  warn: '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>'
};
export const icon = (name, cls = '') => `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ''}</svg>`;

// 迷你趋势线 / 进度环
export function sparkline(values, w = 72, h = 26) {
  // 不足两个点画不出趋势：画一条虚线基线（.spark.empty），不要伪造一条"一直是 0"的实线
  if (!values || values.length < 2) return `<svg class="spark empty" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true"><path d="M0 ${h - 1.5}H${w}"/></svg>`;
  const v = values;   // 不足两个点的情况上面已经返回
  const max = Math.max(...v, 1), min = Math.min(...v, 0);
  const pts = v.map((y, i) => [i / (v.length - 1) * w, h - ((y - min) / (max - min || 1)) * (h - 3) - 1.5]);
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join('');
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true"><path class="area raw" d="${d}L${w} ${h}L0 ${h}Z"/><path d="${d}"/></svg>`;
}
export function ring(pct, label = '') {
  const r = 18, c = 2 * Math.PI * r, off = c * (1 - clamp(pct, 0, 1));
  return `<svg class="ring" viewBox="0 0 44 44" role="img" aria-label="进度 ${Math.round(pct * 100)}%"><circle class="bg" cx="22" cy="22" r="${r}"/><circle class="fg" cx="22" cy="22" r="${r}" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"/><text x="22" y="22">${esc(label || Math.round(pct * 100) + '%')}</text></svg>`;
}

// 简易 Markdown（合并稿预览）
export function markdown(src) {
  const lines = String(src || '').split(/\r?\n/); let out = '', inCode = false, list = '';
  const flush = () => { if (list) { out += `</${list}>`; list = ''; } };
  for (const raw of lines) {
    if (raw.startsWith('```')) { flush(); inCode = !inCode; out += inCode ? '<pre>' : '</pre>'; continue; }
    if (inCode) { out += esc(raw) + '\n'; continue; }
    const h = raw.match(/^(#{1,6})\s+(.*)/); if (h) { flush(); out += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`; continue; }
    const ul = raw.match(/^\s*[-*]\s+(.*)/); const ol = raw.match(/^\s*\d+[.)]\s+(.*)/);
    if (ul || ol) { const tag = ul ? 'ul' : 'ol'; if (list !== tag) { flush(); list = tag; out += `<${tag}>`; } out += `<li>${inline((ul || ol)[1])}</li>`; continue; }
    if (/^\s*>\s?/.test(raw)) { flush(); out += `<blockquote>${inline(raw.replace(/^\s*>\s?/, ''))}</blockquote>`; continue; }
    if (/^\|.*\|$/.test(raw.trim())) { if (/^\|[\s:-|]+\|$/.test(raw.trim())) continue; flush(); const cells = raw.trim().slice(1, -1).split('|'); out += `<table><tr>${cells.map(c => `<td>${inline(c.trim())}</td>`).join('')}</tr></table>`; continue; }
    if (!raw.trim()) { flush(); continue; }
    flush(); out += `<p>${inline(raw)}</p>`;
  }
  // 宽表格由外层 .md-scroll 横向滚动；表格本身保持 display:table，保留读屏器的行列语义（fable2 17fd）
  flush(); return out.replace(/<\/table><table>/g, '').replace(/<table>/g, '<div class="md-scroll"><table>').replace(/<\/table>/g, '</table></div>');
  function inline(t) { return esc(t).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\*([^*]+)\*/g, '<em>$1</em>').replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>'); }
}

// 8c2b：请求错误分类。kind = network（fetch 抛错/无响应）| client（4xx，服务端可读业务原因直出）| server（5xx）| business（2xx 但 body.error）。
// body 只留在内存对象上供调用方判断；可复制/可存储的只有 safeDetail() 白名单字段，绝不带认证头、请求 body、原始 URL 凭据。
// R2-3：默认隐藏全部 query 值，而不是去猜敏感键名——猜键名永远漏（access_token / refresh_token / sid / 自定义名）。
// 同时剥掉 userinfo（user:pw@host）与 hash；路径段里形似凭据的长随机串也打码（/mcp/<token> 这种把密钥放在路径里的形态）。
const CRED_SEG = /^(?=.*[0-9])[A-Za-z0-9_-]{16,}$|^[A-Fa-f0-9]{16,}$/;   // 含数字的长串 或 长 hex：按凭据处理
export function redactPath(p) {
  let s = String(p || '').split('#')[0];                                  // 剥 hash：片段永远不该出现在详情里
  let origin = '', rest = s;
  const m = s.match(/^([a-z][\w+.-]*:\/\/)([^/?#]*)(.*)$/i);
  if (m) {                                                                // 绝对 URL：剥 userinfo，只留 host
    const host = m[2].includes('@') ? m[2].slice(m[2].lastIndexOf('@') + 1) : m[2];
    origin = m[1] + host; rest = m[3] || '';
  }
  const qi = rest.indexOf('?');
  const pathname = qi < 0 ? rest : rest.slice(0, qi);
  const query = qi < 0 ? '' : rest.slice(qi + 1);
  const segs = pathname.split('/').map(seg => (CRED_SEG.test(seg) ? '***' : seg)).join('/');
  // 默认对所有 query 值打码，只保留键名：白名单不可靠，黑名单更不可靠
  const q = query ? '?' + query.split('&').filter(Boolean).map(kv => {
    const i = kv.indexOf('=');
    return i < 0 ? encodeURIComponent(decodeURIComponent(kv)) : `${kv.slice(0, i)}=***`;
  }).join('&') : '';
  return origin + segs + q;
}
// message 也可能被上游塞进整段 URL / Bearer / 密钥，不能当安全白名单原样复制
const SECRET_IN_TEXT = /(Bearer\s+\S+)|(https?:\/\/\S+)|((?:access_token|refresh_token|api[_-]?key|session|sid|pwd|password|secret|token)\s*[=:]\s*\S+)|(\b[A-Fa-f0-9]{24,}\b)|(\bsk-[A-Za-z0-9_-]{12,})/gi;
export const scrubText = t => String(t ?? '').replace(SECRET_IN_TEXT, m => (/^https?:/i.test(m) ? redactPath(m) : '***'));
export class ApiError extends Error {
  constructor(message, { kind = 'network', status = 0, path = '', body = null } = {}) { super(message); this.name = 'ApiError'; this.kind = kind; this.status = status; this.path = path; this.body = body; }
  get retryable() { return this.kind === 'network' || this.kind === 'server'; }
  // 可复制 / 可入 DOM 的只有这五个有限结构字段：没有 body、没有原始响应、没有请求头
  safeDetail() { return { kind: this.kind, status: this.status, path: redactPath(this.path), message: scrubText(this.message).slice(0, 300), time: new Date().toISOString() }; }
}
export async function api(path, options = {}) {
  const request = { cache: 'no-store', ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) } };
  let res;
  try { res = await fetch(path, request); } catch (e) { throw new ApiError('网络不可达，请检查工作台服务是否在线', { kind: 'network', status: 0, path }); }
  const text = await res.text();
  let data = text; try { data = text ? JSON.parse(text) : {}; } catch (_) { /* 文本响应 */ }
  if (!res.ok || (data && data.error)) {
    const kind = res.status >= 500 ? 'server' : res.status >= 400 ? 'client' : 'business';
    throw new ApiError(data && data.error ? data.error : `请求失败 ${res.status}`, { kind, status: res.status, path, body: data });
  }
  return data;
}
export async function textApi(path) { const res = await fetch(path, { cache: 'no-store' }); if (!res.ok) throw new Error(`请求失败 ${res.status}`); return res.text(); }
