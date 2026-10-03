// 扩展与来源页：收件箱 / 体验反馈 / 插件 / 事件流。独立页面，不依赖工作台主界面的模块。
// 例外：外观偏好走 prefs.js（无依赖的叶子模块，外观唯一入口）。原来只靠头部脚本设 --accent，自定义强调色时
// 按钮字色 / 浅底 / 焦点环 / 字号密度都和主工作台不一致，低对比强调色下焦点环只有 1.2:1（fable2 a7ad 后续实测）。
import { apply as applyPrefs } from './prefs.js';
applyPrefs();
window.addEventListener('storage', e => { if (e.key === 'wb.prefs') applyPrefs(); });  // 别的标签页改了外观，这里同步
const $ = (s, r = document) => r.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const KIND = { source: ['数据源', 'info'], notifier: ['通知', 'violet'], panel: ['页面', 'plain'] };
const FB_KIND = { bug: ['出错了', 'bad'], ux: ['不好用', 'warn'], idea: ['想法', 'violet'] };
const FB_STATUS = { open: '未处理', converted: '已转任务', done: '已解决', dismissed: '不处理' };
const IN_STATUS = { pending: '待确认', accepted: '已转任务', dismissed: '已忽略' };

const S = { tab: '', intake: [], feedback: [], plugins: [], events: [], projects: [], inFilter: 'pending', fbFilter: 'open', open: new Set(), logs: {} };

async function api(path, body) {
  const r = await fetch(path, body === undefined ? { cache: 'no-store' } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
  return d;
}
function rel(v) {
  const t = Date.parse(String(v || '').replace(' ', 'T')); if (!t) return '—';
  const m = Math.max(0, Math.round((Date.now() - t) / 60000));
  return m < 1 ? '刚刚' : m < 60 ? `${m} 分钟前` : m < 1440 ? `${Math.floor(m / 60)} 小时前` : `${Math.floor(m / 1440)} 天前`;
}
let toastTimer;
function toast(msg, err) {
  const t = $('#hubToast'); t.textContent = msg; t.className = 'hub-toast show' + (err ? ' err' : '');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.className = 'hub-toast'; }, err ? 5000 : 2400);
}
const chip = (text, cls = 'plain') => `<span class="chip ${cls}">${esc(text)}</span>`;

async function load(which = ['intake', 'feedback', 'plugins', 'events']) {
  const jobs = {
    intake: () => api('/api/intake').then(d => { S.intake = d.items || []; }),
    feedback: () => api('/api/feedback').then(d => { S.feedback = d.items || []; }),
    plugins: () => api('/api/plugins').then(d => { S.plugins = d.plugins || []; }),
    events: () => api('/api/event-log/recent?n=150').then(d => { S.events = d.events || []; }),
  };
  await Promise.allSettled(which.map(k => jobs[k]()));
  counts(); render();
}
async function loadProjects() {
  try {
    const st = await api('/api/state');
    const list = Array.isArray(st.projects) ? st.projects : Object.values(st.projects || {});
    S.projects = list.map(p => ({ slug: p.slug || p.name, name: p.name || p.slug })).filter(p => p.slug);
  } catch { S.projects = []; }
}
function counts() {
  const set = (k, n) => { const b = document.querySelector(`[data-count="${k}"]`); if (b) b.textContent = n ? String(n) : ''; };
  set('inbox', S.intake.filter(x => x.status === 'pending').length);
  set('feedback', S.feedback.filter(x => x.status === 'open').length);
  set('plugins', S.plugins.filter(x => x.running).length);
}

function setTab(tab) {
  S.tab = ['inbox', 'feedback', 'plugins', 'events'].includes(tab) ? tab : 'inbox';
  document.querySelectorAll('[data-tab]').forEach(b => { const on = b.dataset.tab === S.tab; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; });
  $('#hubMain').setAttribute('aria-labelledby', 'tab-' + S.tab);
  if (location.hash.slice(1) !== S.tab) history.replaceState(null, '', '#' + S.tab);
  render();
}

function filterBar(cur, opts, key) {
  return `<div class="hub-filter" role="group" aria-label="筛选">${opts.map(([v, l, n]) => `<button type="button" data-filter="${key}" data-v="${v}" aria-pressed="${cur === v}">${esc(l)}${n != null ? ` ${n}` : ''}</button>`).join('')}</div>`;
}
function textBlock(id, text) {
  if (!text) return '';
  const long = text.length > 280;
  return `<p class="hub-text${S.open.has(id) ? ' open' : ''}">${esc(text)}</p>${long ? `<button type="button" class="hub-btn quiet" data-act="more" data-id="${esc(id)}">${S.open.has(id) ? '收起' : '展开全文'}</button>` : ''}`;
}

function renderInbox() {
  const n = s => S.intake.filter(x => x.status === s).length;
  const list = S.intake.filter(x => x.status === S.inFilter);
  const projOpts = `<option value="">交给 Leader 判断归属</option>` + S.projects.map(p => `<option value="${esc(p.slug)}">${esc(p.name)}</option>`).join('');
  const running = S.plugins.filter(p => p.kind === 'source' && p.running);
  let html = `<div class="hub-bar">${filterBar(S.inFilter, [['pending', '待确认', n('pending')], ['accepted', '已转任务', n('accepted')], ['dismissed', '已忽略', n('dismissed')]], 'in')}<span class="grow"></span></div>`;
  if (S.inFilter === 'pending' && list.length) html += `<p class="hub-next">下一步：逐条看一眼，值得做的点「转成任务」（可选项目），其余「忽略」。忽略不会删除，随时能在「已忽略」里找到。</p>`;
  if (!list.length) {
    html += S.inFilter !== 'pending' ? `<div class="hub-empty">这里还没有条目。</div>`
      : running.length ? `<div class="hub-empty"><strong>收件箱是空的</strong>数据源「${esc(running.map(p => p.name).join('、'))}」正在运行，有新条目会自动出现在这里。</div>`
        : `<div class="hub-empty"><strong>还没有启用数据源</strong>去「插件」里打开「个人助理数据源」，小米待办、笔记、飞书、四象限里的新条目就会自动送到这里等你确认。<br><br><button type="button" class="hub-btn go" data-goto="plugins">去启用</button></div>`;
    return html;
  }
  html += `<div class="hub-list">` + list.map(it => `
    <article class="card hub-item" data-item="${esc(it.id)}">
      <div class="hub-meta">${chip(it.origin || it.source, 'info')}${it.due ? chip('截止 ' + it.due, 'warn') : ''}<span>${esc(rel(it.created))}</span>${it.url ? `<a href="${esc(it.url)}" target="_blank" rel="noopener">原文</a>` : ''}</div>
      <h3>${esc(it.title)}</h3>${it.text && it.text.trim() !== it.title.trim() ? textBlock(it.id, it.text) : ''}
      ${it.status === 'pending' ? `<div class="hub-actions"><label class="sr-only" for="p-${esc(it.id)}">归属项目</label><select id="p-${esc(it.id)}">${projOpts}</select><input id="n-${esc(it.id)}" placeholder="补充说明（可选）" aria-label="补充说明"><span class="grow"></span>
        <button type="button" class="hub-btn quiet" data-act="in-dismiss" data-id="${esc(it.id)}">忽略</button><button type="button" class="hub-btn go" data-act="in-accept" data-id="${esc(it.id)}">转成任务</button></div>`
      : `<div class="hub-meta" style="margin-top:8px">${chip(IN_STATUS[it.status] || it.status, it.status === 'accepted' ? 'ok' : 'plain')}<span>${esc(rel(it.decided))}</span>${it.command_id ? `<span>指令 ${esc(it.command_id)}</span>` : ''}</div>`}
    </article>`).join('') + `</div>`;
  return html;
}

function renderFeedback() {
  const n = s => S.feedback.filter(x => x.status === s).length;
  const list = S.feedback.filter(x => S.fbFilter === 'all' || x.status === S.fbFilter);
  let html = `<div class="hub-bar">${filterBar(S.fbFilter, [['open', '未处理', n('open')], ['converted', '已转任务', n('converted')], ['done', '已解决', n('done')], ['dismissed', '不处理', n('dismissed')], ['all', '全部']], 'fb')}<span class="grow"></span></div>`;
  if (!list.length) {
    return html + `<div class="hub-empty"><strong>${S.fbFilter === 'open' ? '没有未处理的反馈' : '这里还没有反馈'}</strong>任何页面右下角都有「反馈」按钮（快捷键 <code>Alt+B</code>）。用着不顺手时点一下、写一句，系统会记下你当时在哪、点了什么。</div>`;
  }
  if (S.fbFilter === 'open') html += `<p class="hub-next">下一步：挑值得改的「转成任务」，Leader 会先复现、判断，再按分支流程改，改完回来标「已解决」并写提交编号。</p>`;
  html += `<div class="hub-list">` + list.map(f => {
    const [kl, kc] = FB_KIND[f.kind] || ['反馈', 'plain'];
    const where = [f.view && '视图 ' + f.view, f.target && '点击 ' + f.target, f.page].filter(Boolean).join(' · ');
    const last = (f.history || []).slice(-1)[0];
    return `<article class="card hub-item" data-item="${esc(f.id)}">
      <div class="hub-meta">${chip(kl, kc)}${f.status !== 'open' ? chip(FB_STATUS[f.status] || f.status, f.status === 'done' ? 'ok' : 'plain') : ''}<span>${esc(rel(f.created))}</span><span>${esc(f.id)}</span></div>
      ${textBlock(f.id, f.text)}
      ${where ? `<div class="hub-meta" style="margin-top:6px">${esc(where)}</div>` : ''}
      ${last && last.note ? `<div class="hub-meta" style="margin-top:6px">备注：${esc(last.note)}</div>` : ''}
      <div class="hub-actions"><input id="fn-${esc(f.id)}" placeholder="${f.status === 'open' ? '补充要求（可选）' : '备注（可选）'}" aria-label="备注"><span class="grow"></span>
        ${f.status === 'open' ? `<button type="button" class="hub-btn quiet" data-act="fb" data-s="dismissed" data-id="${esc(f.id)}">不处理</button><button type="button" class="hub-btn" data-act="fb" data-s="done" data-id="${esc(f.id)}">已解决</button><button type="button" class="hub-btn go" data-act="fb" data-s="converted" data-id="${esc(f.id)}">转成任务</button>`
        : f.status === 'converted' ? `<button type="button" class="hub-btn" data-act="fb" data-s="done" data-id="${esc(f.id)}">标为已解决</button>`
          : `<button type="button" class="hub-btn quiet" data-act="fb" data-s="open" data-id="${esc(f.id)}">重新打开</button>`}
      </div></article>`;
  }).join('') + `</div>`;
  return html;
}

function cfgValue(v) { return typeof v === 'string' ? v : JSON.stringify(v); }
function renderPlugins() {
  if (!S.plugins.length) return `<div class="hub-empty"><strong>还没有插件</strong>在 <code>plugins/&lt;名字&gt;/plugin.json</code> 放一个清单即可被发现，写法见 <code>docs/PLUGINS.md</code>。</div>`;
  return `<div class="hub-list">` + S.plugins.map(p => {
    const [kl, kc] = KIND[p.kind] || [p.kind || '未知', 'plain'];
    const state = p.errors?.length ? chip('清单有误', 'bad') : p.running ? chip('运行中', 'ok live') : p.enabled ? chip(p.error ? '已停止' : '启动中', p.error ? 'bad' : 'warn') : chip('未启用', 'plain');
    const keys = Object.keys(p.config || {});
    return `<article class="card hub-item" data-plugin="${esc(p.id)}">
      <div class="hub-plugin-head"><div class="grow"><div class="hub-meta">${chip(kl, kc)}${state}<span>${esc(p.id)} · v${esc(p.version || '?')}</span></div><h3>${esc(p.name)}</h3><p class="hub-text open" style="color:var(--muted)">${esc(p.description || '')}</p></div>
        <button type="button" class="hub-switch" role="switch" aria-checked="${!!p.enabled}" aria-label="${p.enabled ? '停用' : '启用'} ${esc(p.name)}" data-act="toggle" data-id="${esc(p.id)}" ${p.errors?.length ? 'disabled' : ''}></button></div>
      ${p.errors?.length ? `<ul class="hub-errs">${p.errors.map(e => `<li>${esc(e)}</li>`).join('')}</ul>` : ''}
      ${p.error ? `<div class="hub-meta" style="margin-top:6px;color:var(--danger)">${esc(p.error)}</div>` : ''}
      ${keys.length ? `<details style="margin-top:10px"><summary>配置</summary><form class="hub-cfg" data-cfg="${esc(p.id)}">${keys.map(k => `<label for="c-${esc(p.id)}-${esc(k)}">${esc(k)}</label><input id="c-${esc(p.id)}-${esc(k)}" name="${esc(k)}" data-type="${typeof p.config[k]}" value="${esc(cfgValue(p.config[k]))}">${p.config_help?.[k] ? `<small>${esc(p.config_help[k])}</small>` : ''}`).join('')}
        <span></span><div class="hub-actions" style="margin:0"><button type="submit" class="hub-btn go">保存并重启插件</button></div></form></details>` : ''}
      <div class="hub-actions"><button type="button" class="hub-btn quiet" data-act="log" data-id="${esc(p.id)}">${S.logs[p.id] != null ? '收起日志' : '查看日志'}</button>${p.page ? `<a class="hub-btn" href="/plugins/${esc(p.id)}/${esc(p.page)}" target="_blank">打开页面</a>` : ''}</div>
      ${S.logs[p.id] != null ? `<pre class="hub-log">${esc(S.logs[p.id] || '（还没有日志）')}</pre>` : ''}
    </article>`;
  }).join('') + `</div><section class="card hub-help"><h4>插件是怎么工作的</h4>每个插件是 <code>plugins/</code> 下的一个文件夹，工作台把它当独立进程启动，只通过 HTTP 接口交流——插件崩了工作台不受影响，关掉开关就立即停止。启用状态和配置只存在本机 <code>plugins.local.json</code>，不进 git。</section>`;
}

function evSummary(e) {
  const d = e.data || {};
  return d.title || d.text || d.summary || (d.count != null ? `${d.count} 条` : '') || d.kind || d.id || e.subject || '';
}
function renderEvents() {
  if (!S.events.length) return `<div class="hub-empty"><strong>还没有事件</strong>工作台的每次状态变化（决策、指令、收件箱、反馈、插件启停）都会追加到 <code>events/日期.jsonl</code>，其它项目可以按游标订阅。</div>`;
  return `<p class="hub-next">这里是 <code>events/</code> 的最近 150 条。其它项目或通知插件用 <code>GET /api/event-log?cursor=…</code> 增量读取，契约见 <code>docs/adr/0003-events-outbox.md</code>。</p><div class="card hub-events">` +
    S.events.map(e => `<div class="hub-ev"><time datetime="${esc(e.time)}">${esc(rel(e.time))}</time><code>${esc(e.type)}</code><span title="${esc(JSON.stringify(e.data))}">${esc(evSummary(e))}</span></div>`).join('') + `</div>`;
}

// 插件页每 8s 轮询：原来每次整页 innerHTML，入场动画反复重播、展开的「配置」被收起（ui_audit 扩到 hub 时扫出）
let lastRender = { tab: '', html: '' };
function render() {
  const main = $('#hubMain');
  const html = { inbox: renderInbox, feedback: renderFeedback, plugins: renderPlugins, events: renderEvents }[S.tab]();
  if (lastRender.tab === S.tab && lastRender.html === html) return;  // 数据没变：不碰 DOM
  main.classList.toggle('settled', lastRender.tab === S.tab);  // 同一分页的刷新不重播入场动画，切分页才播
  const focus = document.activeElement && document.activeElement.id;
  const vals = {}; main.querySelectorAll('input[id],select[id]').forEach(el => { vals[el.id] = el.value; });
  const open = [...main.querySelectorAll('details[open]')].map(d => d.closest('[data-plugin]')?.getAttribute('data-plugin')).filter(Boolean);
  main.innerHTML = html; lastRender = { tab: S.tab, html };
  for (const k of open) main.querySelector(`[data-plugin="${CSS.escape(k)}"] details`)?.setAttribute('open', '');
  for (const [id, v] of Object.entries(vals)) { const el = document.getElementById(id); if (el) el.value = v; }
  if (focus) document.getElementById(focus)?.focus();
}

async function leave(id, fn) {
  const card = document.querySelector(`[data-item="${CSS.escape(id)}"]`);
  card?.querySelectorAll('button').forEach(b => { b.disabled = true; });
  try {
    await fn();
    if (card && !matchMedia('(prefers-reduced-motion: reduce)').matches) { card.classList.add('leaving'); await new Promise(r => setTimeout(r, 200)); }
  } catch (err) { card?.querySelectorAll('button').forEach(b => { b.disabled = false; }); throw err; }
}

document.addEventListener('click', async e => {
  const tab = e.target.closest('[data-tab]'); if (tab) return setTab(tab.dataset.tab);
  const go = e.target.closest('[data-goto]'); if (go) return setTab(go.dataset.goto);
  const f = e.target.closest('[data-filter]');
  if (f) { S[f.dataset.filter === 'in' ? 'inFilter' : 'fbFilter'] = f.dataset.v; return render(); }
  const b = e.target.closest('[data-act]'); if (!b) return;
  const id = b.dataset.id;
  try {
    if (b.dataset.act === 'more') { S.open.has(id) ? S.open.delete(id) : S.open.add(id); return render(); }
    if (b.dataset.act === 'in-accept' || b.dataset.act === 'in-dismiss') {
      const action = b.dataset.act === 'in-accept' ? 'accept' : 'dismiss';
      await leave(id, () => api('/api/intake/decide', { id, action, project: $(`#p-${id}`)?.value || '', note: $(`#n-${id}`)?.value || '' }));
      toast(action === 'accept' ? '已转成任务，Leader 下一轮会处理' : '已忽略');
      return load(['intake', 'events']);
    }
    if (b.dataset.act === 'fb') {
      const status = b.dataset.s;
      await leave(id, () => api('/api/feedback/update', { id, status, note: $(`#fn-${id}`)?.value || '' }));
      toast({ converted: '已转成任务，Leader 会先复现再决定怎么改', done: '已标为解决', dismissed: '已标为不处理', open: '已重新打开' }[status]);
      return load(['feedback', 'events']);
    }
    if (b.dataset.act === 'toggle') {
      const on = b.getAttribute('aria-checked') !== 'true';
      b.setAttribute('aria-checked', String(on)); b.disabled = true;
      await api('/api/plugins/toggle', { id, enabled: on });
      toast(on ? '已启用，插件已在后台启动' : '已停用，插件进程已停止');
      return load(['plugins', 'events']);
    }
    if (b.dataset.act === 'log') {
      if (S.logs[id] != null) { delete S.logs[id]; return render(); }
      S.logs[id] = (await api(`/api/plugins/log?id=${encodeURIComponent(id)}`)).log || '';
      return render();
    }
  } catch (err) { toast('操作失败：' + err.message, true); load(); }
});

document.addEventListener('submit', async e => {
  const form = e.target.closest('[data-cfg]'); if (!form) return;
  e.preventDefault();
  const cfg = {};
  try {
    for (const el of form.querySelectorAll('input[name]')) {
      const t = el.dataset.type;
      cfg[el.name] = t === 'string' ? el.value : t === 'number' ? Number(el.value) : JSON.parse(el.value);
      if (t === 'number' && Number.isNaN(cfg[el.name])) throw new Error(`${el.name} 需要是数字`);
    }
  } catch (err) { return toast('配置格式不对：' + (err.message.includes('JSON') ? '列表请写成 ["a","b"] 的形式' : err.message), true); }
  try { await api('/api/plugins/config', { id: form.dataset.cfg, config: cfg }); toast('配置已保存，插件已重启'); load(['plugins', 'events']); }
  catch (err) { toast('保存失败：' + err.message, true); }
});

document.addEventListener('keydown', e => {
  const tab = e.target.closest?.('[role=tab]'); if (!tab || !['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
  const tabs = [...document.querySelectorAll('[role=tab]')]; const i = tabs.indexOf(tab);
  const next = tabs[(i + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
  next.focus(); setTab(next.dataset.tab);
});
window.addEventListener('hashchange', () => setTab(location.hash.slice(1)));

// 实时：复用工作台的 SSE（目录变化即推 change），断线时退回 15 秒轮询
function live() {
  const dot = $('#hubLive'); let timer = null;
  const setState = (ok, text) => { dot.classList.toggle('ok', ok); dot.classList.toggle('bad', !ok); dot.querySelector('span').textContent = text; };
  try {
    const es = new EventSource('/api/events');
    let pending = null;
    es.onopen = () => { setState(true, '实时'); clearInterval(timer); timer = null; };
    es.onmessage = () => { clearTimeout(pending); pending = setTimeout(() => load(), 300); };
    es.onerror = () => { setState(false, '重连中…'); if (!timer) timer = setInterval(() => load(), 15000); };
  } catch { timer = setInterval(() => load(), 15000); }
}

setTab(location.hash.slice(1) || 'inbox');
loadProjects().then(() => render());
load();
live();
// 插件启停不是文件变化，SSE 不会推；插件页打开时每 8 秒刷新一次运行状态
setInterval(() => { if (S.tab === 'plugins' && !document.hidden) load(['plugins']); }, 8000);
