// 通用组件：提示条（可撤销）、抽屉、确认对话框、命令面板、虚拟列表（Pretext 测高）
import { $, $$, esc, attr, icon, clamp } from './core.js';
import { prepare, layout } from '../vendor/pretext/layout.js';

// ---------- 提示条（8c2b 五级）----------
// level：transient 1200ms（读屏静默、不进收件）/ normal 3600ms / sticky 手动关 / center 只进收件不弹 / banner 顶部常驻归 #banner 既有用途，这里按 sticky 处理。
// 旧签名 toast(message, tone, opts) 不变：缺省 bad→sticky、其余 normal；opts.ms 仍可覆盖时长（0 = 不自动关）。
export const TOAST_LEVELS = { transient: 1200, normal: 3600, sticky: 0, center: 0, banner: 0 };
export const TOAST_STACK_MAX = 3;
const liveByKey = new Map();
const inbox = [];                       // center 级与被上限挤掉的 sticky 都进这里，不静默丢；本批只在内存，不写 localStorage
export const notifyInbox = () => inbox.slice();
function pushInbox(item) { inbox.unshift({ ...item, at: Date.now() }); if (inbox.length > 200) inbox.length = 200; renderInboxEntry(); document.dispatchEvent(new CustomEvent('wb-inbox', { detail: { size: inbox.length } })); }
function renderInboxEntry() {
  const region = $('#toasts'); if (!region) return;
  let b = region.querySelector('[data-inbox-entry]');
  if (!inbox.length) { b?.remove(); return; }
  if (!b) {
    b = document.createElement('button'); b.type = 'button'; b.className = 'small ghost inbox-entry';
    b.setAttribute('data-inbox-entry', ''); b.onclick = () => openInbox();
    // 溢出的重要消息不上屏，入口本身必须可被读屏播报，否则等于静默丢失
    b.setAttribute('role', 'status'); b.setAttribute('aria-live', 'polite');
    region.prepend(b);
  }
  const urgent = inbox.filter(n => n.level === 'sticky' || n.tone === 'bad').length;
  b.textContent = urgent ? `未读通知 ${inbox.length} 条（${urgent} 条重要）` : `未读通知 ${inbox.length} 条`;
  b.setAttribute('aria-label', `打开通知收件箱，${inbox.length} 条未读${urgent ? `，其中 ${urgent} 条重要` : ''}`);
}
export function openInbox() {
  openDrawer({ title: '通知', subtitle: `${inbox.length} 条 · 关闭抽屉不会清空`, kind: 'notif',
    body: inbox.length ? `<div class="stack">${inbox.map((n, i) => `<div class="card pad" data-inbox-item="${i}"><div class="row"><b class="grow">${esc(n.message)}</b><span class="faint mono" style="font-size:calc(11px * var(--fs-scale, 1))">${esc(new Date(n.at).toLocaleTimeString())}</span></div>${n.detail ? `<pre class="faint" style="font-size:calc(11px * var(--fs-scale, 1));white-space:pre-wrap;margin:6px 0 0">${esc(n.detail)}</pre>` : ''}</div>`).join('')}</div>` : '<div class="empty"><strong>没有通知</strong></div>',
    foot: inbox.length ? '<button type="button" class="ghost" data-inbox-clear>全部清除</button>' : '' });
  $('#drawerFoot')?.querySelector('[data-inbox-clear]')?.addEventListener('click', () => { inbox.length = 0; renderInboxEntry(); closeDrawer(); });
}
export function toast(message, tone = '', opts = {}) {
  const region = $('#toasts'); if (!region) return () => {};
  const level = opts.level || (opts.ms === 0 || tone === 'bad' ? 'sticky' : 'normal');
  if (level === 'center') { pushInbox({ message, tone, detail: opts.detail || '' }); return () => {}; }
  if (opts.key && liveByKey.has(opts.key)) { const l = liveByKey.get(opts.key); l.bump(); return l.close; }   // 同 key 合并 ×N 并重置计时
  const ms = opts.ms ?? TOAST_LEVELS[level] ?? 3600;
  const shown = () => [...region.querySelectorAll('.toast:not(.leaving)')];
  // R2-1：上限 3 是硬上限，对每一种 level 都成立（原实现的 break 会让 normal 在三条 sticky 之后突破到 4 条）。
  // 腾位只允许挤掉非 sticky；挤不动时新消息一律入收件箱，绝不定时偷关既有 sticky——重要错误只能由用户手动关。
  while (shown().length >= TOAST_STACK_MAX) {
    const victim = shown().find(n => !n.classList.contains('lv-sticky'));
    if (victim) { victim._close?.(); continue; }
    pushInbox({ message, tone, detail: opts.detail || '', level, overflow: true });
    return () => {};
  }
  const node = document.createElement('div');
  node.className = `toast ${tone} lv-${level}`;
  if (level === 'transient') { node.setAttribute('role', 'presentation'); node.setAttribute('aria-hidden', 'true'); }
  else node.setAttribute('role', level === 'sticky' || tone === 'bad' ? 'alert' : 'status');
  if (opts.key) node.dataset.key = opts.key;
  node.innerHTML = `<span class="grow"><span class="msg">${esc(message)}</span><span class="count" hidden></span>${opts.detail ? `<details class="detail"><summary>详情</summary><pre style="white-space:pre-wrap;font-size:calc(11px * var(--fs-scale, 1));margin:4px 0 0">${esc(opts.detail)}</pre></details>` : ''}</span>${opts.undo ? `<button type="button" class="small undo">${icon('undo')}撤销</button>` : ''}${opts.action ? `<button type="button" class="small act">${esc(opts.action.label)}</button>` : ''}${opts.detail ? '<button type="button" class="small ghost copy">复制详情</button>' : ''}<button type="button" class="ghost icon" aria-label="关闭">${icon('close')}</button>${ms ? `<i class="bar" style="animation-duration:${ms}ms"></i>` : ''}`;
  region.appendChild(node);
  let done = false, count = 1, timer = 0, startedAt = 0, remaining = ms;
  const close = () => { if (done) return; done = true; clearTimeout(timer); if (opts.key) liveByKey.delete(opts.key); node.classList.add('leaving'); setTimeout(() => node.remove(), 160); };
  node._close = close;
  const arm = () => { if (!ms || done) return; clearTimeout(timer); startedAt = Date.now(); timer = setTimeout(() => { close(); opts.onExpire?.(); }, remaining); };
  const pause = () => { if (!ms || done || !startedAt) return; clearTimeout(timer); remaining = Math.max(0, remaining - (Date.now() - startedAt)); startedAt = 0; };
  node.querySelector('[aria-label="关闭"]').onclick = close;
  if (opts.undo) node.querySelector('.undo').onclick = () => { close(); opts.undo(); };
  if (opts.action) node.querySelector('.act').onclick = () => { close(); opts.action.go?.(); };
  if (opts.detail) node.querySelector('.copy').onclick = () => navigator.clipboard?.writeText(opts.detail).catch(() => {});
  node.onmouseenter = pause;                                                    // 悬停暂停
  node.onmouseleave = arm;                                                      // 离开后按剩余时间续跑
  const bump = () => { count += 1; const c = node.querySelector('.count'); c.hidden = false; c.textContent = ` ×${count}`; remaining = ms; arm(); };
  if (opts.key) liveByKey.set(opts.key, { node, bump, close });
  arm();
  return close;
}

// ---------- 抽屉 ----------
let drawerReturnFocus = null;
export function openDrawer({ title, subtitle = '', body = '', foot = '', kind = '' }) {
  const d = $('#drawer'), bd = $('#backdrop');
  drawerReturnFocus = document.activeElement;
  $('#drawerTitle').textContent = title; $('#drawerSubtitle').textContent = subtitle;
  $('#drawerBody').innerHTML = body; $('#drawerFoot').innerHTML = foot; $('#drawerFoot').hidden = !foot;
  d.dataset.kind = kind;
  const wasOpen = d.classList.contains('show');
  d.classList.add('show'); bd.classList.add('show'); d.setAttribute('aria-hidden', 'false'); d.inert = false;
  document.body.style.overflow = 'hidden';
  // 抽屉占一条历史记录：手机返回键或浏览器后退先关抽屉，不会退出当前视图
  if (!wasOpen && !(history.state && history.state.drawer)) history.pushState({ ...(history.state || {}), drawer: true }, '', location.href);
  requestAnimationFrame(() => { const f = d.querySelector('input,textarea,select,button:not(.x)'); (f || $('.x', d)).focus({ preventScroll: true }); });
}
export function closeDrawer(fromPop = false) {
  const d = $('#drawer'); if (!d.classList.contains('show')) return;
  d.classList.remove('show'); $('#backdrop').classList.remove('show'); d.setAttribute('aria-hidden', 'true');
  if (!fromPop && history.state && history.state.drawer) { closeDrawer._skipPop = true; history.back(); }
  document.body.style.overflow = '';
  d.dataset.kind = '';
  drawerReturnFocus?.focus?.({ preventScroll: true });
  // 关着的抽屉只是移到屏幕外：不加 inert，键盘 Tab 会走进去，焦点落在看不见、读屏也不念的「关闭」上。
  // 先还原焦点再 inert（opus2 09e8 提出）：按规范，焦点所在的元素变 inert 后浏览器要把焦点挪走；Chrome 实测把这一步延到下一帧，
  // 同步还原已经先到，两种顺序表现一样（2026-09-29 记录焦点事件对照过）。按这个顺序写是不依赖各浏览器挪焦点的时机。
  d.inert = true;
}
export const drawerOpen = () => $('#drawer').classList.contains('show');

// 焦点陷阱
document.addEventListener('keydown', e => {
  if (e.key !== 'Tab') return;
  const layer = $('.cmdk.show .panel') || $('.dialog.show .box') || ($('#drawer.show'));
  if (!layer) return;
  const f = $$('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"]),[contenteditable="true"]', layer).filter(el => el.offsetParent !== null);
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
  else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
});

// ---------- 确认对话框 ----------
export function confirmDialog({ title, body = '', confirmText = '确认', danger = false, input = null }) {
  return new Promise(resolve => {
    const host = $('#dialog');
    host.innerHTML = `<div class="box" role="dialog" aria-modal="true" aria-labelledby="dlgTitle"><h2 id="dlgTitle">${esc(title)}</h2><p>${body}</p>${input ? `<div class="field" style="margin:10px 0 4px"><label>${esc(input.label || '给 Leader 的说明')}</label><textarea id="dlgInput" rows="4" maxlength="${Number(input.maxlength) || 1000}" placeholder="${attr(input.placeholder || '')}" style="width:100%;resize:vertical">${esc(input.value || '')}</textarea><div class="faint" style="font-size:calc(11px * var(--fs-scale, 1));text-align:right"><span id="dlgCount">0</span> / ${Number(input.maxlength) || 1000}${input.required ? ' · 必填' : ''} · Ctrl+Enter 提交</div></div>` : ''}<div class="acts"><button type="button" data-r="0">取消</button><button type="button" class="${danger ? 'danger' : 'primary'}" data-r="1">${esc(confirmText)}</button></div></div>`;
    const prev = document.activeElement;
    host.classList.add('show'); host.style.pointerEvents = 'auto';
    $('#backdrop').classList.add('show');
    const end = r => { host.classList.remove('show'); host.style.pointerEvents = ''; if (!drawerOpen()) $('#backdrop').classList.remove('show'); document.removeEventListener('keydown', onKey); prev?.focus?.(); setTimeout(() => { host.innerHTML = ''; }, 200); resolve(r); };
    const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); end(false); } else if (input && e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $('[data-r="1"]', host)?.click(); } };
    document.addEventListener('keydown', onKey);
    host.onclick = e => { const b = e.target.closest('[data-r]'); if (b) { const v = input ? ($('#dlgInput', host)?.value || '').trim() : ''; if (b.dataset.r === '1' && input && input.required && !v) { $('#dlgInput', host)?.focus(); return; } end(b.dataset.r === '1' ? (input ? { ok: true, value: v } : true) : false); } else if (e.target === host) end(false); };
    if (input) { const ta = $('#dlgInput', host), cnt = $('#dlgCount', host), ok = $('[data-r="1"]', host); const upd = () => { const n = Array.from(ta.value.trim()).length; cnt.textContent = n; ok.disabled = !!input.required && n === 0; }; ta.addEventListener('input', upd); upd(); }
    requestAnimationFrame(() => (input ? $('#dlgInput', host) : $('[data-r="1"]', host)).focus());
  });
}

// ---------- 命令面板 ----------
let cmdItems = [], cmdIndex = 0, cmdQuery = '';
export function registerCommands(items) { cmdItems = items; }
function fuzzy(q, s) {
  q = q.toLowerCase(); s = s.toLowerCase(); if (!q) return 1;
  let i = 0, score = 0, last = -2;
  for (const ch of s) { if (ch === q[i]) { score += last === i - 1 + 0 ? 3 : 1; i++; last = i; if (i === q.length) break; } }
  return i === q.length ? score + (s.startsWith(q) ? 6 : 0) + (s.includes(q) ? 3 : 0) : 0;
}
function mark(s, q) { if (!q) return esc(s); const i = s.toLowerCase().indexOf(q.toLowerCase()); return i < 0 ? esc(s) : esc(s.slice(0, i)) + '<mark>' + esc(s.slice(i, i + q.length)) + '</mark>' + esc(s.slice(i + q.length)); }
function renderCmd() {
  const q = cmdQuery.trim();
  const list = cmdItems.map(it => ({ it, s: fuzzy(q, it.label + ' ' + (it.keywords || '')) })).filter(x => x.s > 0).sort((a, b) => b.s - a.s).slice(0, 24).map(x => x.it);
  cmdIndex = clamp(cmdIndex, 0, Math.max(0, list.length - 1));
  const groups = new Map();
  list.forEach((it, i) => { const g = it.group || '命令'; if (!groups.has(g)) groups.set(g, []); groups.get(g).push([it, i]); });
  $('#cmdkList').innerHTML = list.length ? [...groups].map(([g, arr]) => `<div class="grp">${esc(g)}</div>${arr.map(([it, i]) => `<div class="it ${i === cmdIndex ? 'act' : ''}" data-i="${i}" role="option" aria-selected="${i === cmdIndex}">${icon(it.icon || 'bolt')}<span>${mark(it.label, q)}</span>${it.sub ? `<span class="sub">${esc(it.sub)}</span>` : ''}${it.kbd ? `<kbd>${esc(it.kbd)}</kbd>` : ''}</div>`).join('')}`).join('') : `<div class="empty" style="margin:8px"><strong>没有匹配的命令</strong><span>换个关键词试试</span></div>`;
  $('#cmdkList').dataset.count = list.length;
  $('#cmdkList')._list = list;
  $('.it.act', $('#cmdkList'))?.scrollIntoView({ block: 'nearest' });
}
export function openCmdk(prefill = '') {
  const c = $('#cmdk'); c.classList.add('show'); c.style.pointerEvents = 'auto';
  $('#backdrop').classList.add('show');
  const input = $('#cmdkInput'); input.value = prefill; cmdQuery = prefill; cmdIndex = 0; renderCmd(); input.focus();
}
export function closeCmdk() { const c = $('#cmdk'); if (!c.classList.contains('show')) return; c.classList.remove('show'); c.style.pointerEvents = ''; $('#cmdkInput').blur(); if (!drawerOpen()) $('#backdrop').classList.remove('show'); }
export const cmdkOpen = () => $('#cmdk').classList.contains('show');
export function bindCmdk() {
  const input = $('#cmdkInput');
  input.addEventListener('input', () => { cmdQuery = input.value; cmdIndex = 0; renderCmd(); });
  input.addEventListener('keydown', e => {
    const list = $('#cmdkList')._list || [];
    if (e.key === 'ArrowDown') { cmdIndex = (cmdIndex + 1) % Math.max(1, list.length); renderCmd(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { cmdIndex = (cmdIndex - 1 + list.length) % Math.max(1, list.length); renderCmd(); e.preventDefault(); }
    else if (e.key === 'Enter') { const it = list[cmdIndex]; if (it) { closeCmdk(); it.run(); } }
  });
  $('#cmdkList').addEventListener('click', e => { const el = e.target.closest('.it'); if (!el) return; const it = ($('#cmdkList')._list || [])[Number(el.dataset.i)]; if (it) { closeCmdk(); it.run(); } });
  $('#cmdk').addEventListener('click', e => { if (e.target === $('#cmdk')) closeCmdk(); });
}

// ---------- 虚拟列表（Pretext 预算行高） ----------
const FONT_CACHE = new Map();
function fontOf(el) {
  const cs = getComputedStyle(el);
  const key = `${cs.fontSize}|${cs.fontFamily}`;
  if (!FONT_CACHE.has(key)) FONT_CACHE.set(key, { font: `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`, lineHeight: parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.6 });
  return FONT_CACHE.get(key);
}
export function mountVirtualList(container, items, { padY = 17, padX = 26, render, prefix }) {
  // items: [{ key, text }]；render(item) 返回行内 HTML；高度用 Pretext 算出，滚动时只渲染可见窗口
  container.innerHTML = '<div class="spacer"></div>';
  const spacer = container.firstElementChild;
  const { font, lineHeight } = fontOf(container);
  const prepared = items.map(it => prepare(it.text || '', font, { whiteSpace: 'pre-wrap' }));
  let offsets = [], total = 0, width = 0;
  const measure = () => {
    const w = container.clientWidth - padX;
    if (w === width) return; width = w; total = 0; offsets = prepared.map(p => { const o = total; total += layout(p, w, lineHeight).height + padY + 1; return o; });
    spacer.style.height = total + 'px';
  };
  const draw = () => {
    measure();
    const top = container.scrollTop, bottom = top + container.clientHeight;
    let lo = 0, hi = offsets.length - 1;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (offsets[mid + 1] <= top) lo = mid + 1; else hi = mid; }
    const start = Math.max(0, lo - 3);
    let html = '';
    for (let i = start; i < items.length && offsets[i] < bottom + 200; i++) {
      html += `<div class="vrow" data-key="${attr(items[i].key)}" style="transform:translateY(${offsets[i]}px)">${prefix ? `<b>${esc(prefix(items[i], i))}</b>` : ''}${render ? render(items[i], i) : esc(items[i].text)}</div>`;
    }
    for (const old of spacer.children) { if (!html.includes(`data-key="${old.dataset.key}"`)) old.remove(); }
    spacer.innerHTML = html;
  };
  container.addEventListener('scroll', () => requestAnimationFrame(draw), { passive: true });
  new ResizeObserver(() => draw()).observe(container);
  draw();
  return { total: () => total, count: items.length, scrollToIndex(i) { container.scrollTop = offsets[i] || 0; } };
}

// 用 Pretext 做两行摘要截断（CJK 断行准确）
export function clampText(text, font, width, lines = 2) {
  const p = prepare(String(text || ''), font);
  const res = layout(p, width, 20);
  if (res.lineCount <= lines) return String(text || '');
  const chars = Array.from(String(text));
  let lo = 0, hi = chars.length;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; const t = chars.slice(0, mid).join('') + '…'; if (layout(prepare(t, font), width, 20).lineCount <= lines) lo = mid; else hi = mid - 1; }
  return chars.slice(0, lo).join('') + '…';
}

// ---------- Team 快捷选（纯渲染，不依赖 model.js；数据由调用方算好传入） ----------
// teams: [{ name, peers, count: '在线/总数' 或 数字, tip?, dim? }]；Team chip 带 .team 类与图标，和 Agent 复选框（.fchip 文字）视觉区分
export function teamChips({ teams, active, action, editAction = 'edit-teams', editLabel = '管理团队', label = '快捷选', chipClass = '' }) {
  const chips = (teams || []).map(t => `<button type="button" class="fchip ${chipClass} ${active === t.name ? 'on' : ''} ${t.dim ? 'dim' : ''}" data-action="${attr(action)}" data-team="${attr(t.name)}" ${t.tip ? `data-tip="${attr(t.tip)}"` : ''} ${t.dim ? 'aria-disabled="true"' : ''}>${chipClass ? icon('users') : ''}${esc(t.name)}${t.count !== undefined && t.count !== '' ? `<span class="n">${esc(String(t.count))}</span>` : ''}</button>`).join('');
  return `<div class="row wrap team-chips" style="margin-bottom:10px"><span class="faint" style="font-size:calc(12px * var(--fs-scale, 1));width:56px">${esc(label)}</span>${chips}${editAction ? `<button type="button" class="small ghost" data-action="${attr(editAction)}">${icon('edit')}${esc(editLabel)}</button>` : ''}</div>`;
}
