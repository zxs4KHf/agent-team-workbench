import * as Prefs from './prefs.js';
import { profileEditor, profilePatch } from './profiles_ui.js';
import { notify } from './notify.js';
// 应用控制器：数据同步（SSE 优先，轮询兜底）、路由、事件委托、抽屉内容
import { $, $$, esc, attr, icon, morph, animateNumber, viewTransition, url, api, textApi, markdown, fmtDate, relTime, clamp } from './core.js';
import { toast, openDrawer, closeDrawer, drawerOpen, confirmDialog, registerCommands, openCmdk, closeCmdk, cmdkOpen, bindCmdk, mountVirtualList, teamChips } from './components.js';
import { store, S, F, project, decisions, activeDecisions, runningDecisions, agentList, onlineAgents, dispatchableAgents, undispatchableReason, teamPeersOf, pickStats, pickStatsText, dispatchEstimate, DYNAMIC_TEAM, PROJECT_TEAM, agentByName, displayName, agentMeta, titleOf, displayTitle, parentOf, childrenOf, BUCKETS, artifactsOf, effectiveTeams, ensureComposer, defaultComposerPeers, countPending, winPath, progressOf, NAV, STATUS, PEER_STATE, EXEC_LABEL, looksLikeImage, taskOrder, agentWeights, WEIGHT_OPTIONS, secondsLeft, fmtLeft, defaultProject, stallInfo } from './model.js';
import { resolveAction, selectionAnchor, shortcutAllowed, isTypingTarget, digitView, debounce, toggleExtra, mergeBodyStale, parseEntity, entityPatch, mergeVisibleOrder, moveInList } from './interact.js';
import { renderOverview, renderRunning, renderDecisions, renderArtifacts, chip, implChip, implActions, taskListHtml, runningMeta, toggleStuck, briefOf, briefFeedback, setOvTab } from './views.js';
import { modeLabel, renderCompose, renderPeers, renderSettings, groupOrder, saveGroupOrder, groupOfModel, leaderBoard, relayCardHtml, accentHint, groupOfAgent, agentDetail, groupForm } from './views2.js';
import { HISTORY_NAV, HISTORY_ACTIONS, HOOKS as HIST_HOOKS, renderHistory, historyKeydown, startHistoryView, histState, readUrlFilters } from './history.js';

import { PREF_ACTIONS, renderBrand, setMarkFromFile } from './app-brand.js';
import { hideTask, loadSettings, reviewNow, saveInline, saveSettings, sendCommand, unhideTask } from './app-commands.js';
import { addAttachments, composerStateChanged, openDispatch, releaseAttachments, renderTray, submitComposer, submitDispatch, syncComposer } from './app-composer.js';
import { loadMerge, openCommand, openDecision, openFiles, openLeadDetail, openPreview, openTask, reconcileMerges } from './app-drawers.js';
import { nextPeerName, openAgentEditor, openJoin, openPlanEditor, openProjectForm, openTeamEditor, saveLabel } from './app-editors.js';
import { openRedispatch } from './app-redispatch.js';
import { connectLive, refresh } from './app-sync.js';
import { loadWakeBound, openWakeAll, wakeAutoAll, wakePeer } from './app-wake.js';
export { renderBrand, PREF_ACTIONS, setMarkFromFile } from './app-brand.js';
export { hideTask, unhideTask, sendCommand, reviewNow, loadSettings, saveSettings, saveInline } from './app-commands.js';
export { renderTray, addAttachments, releaseAttachments, submitComposer, submitDispatch, openDispatch, syncComposer, composerStateChanged } from './app-composer.js';
export { openLeadDetail, loadMerge, reconcileMerges, openTask, openDecision, openCommand, openPreview, openFiles } from './app-drawers.js';
export { openPlanEditor, openTeamEditor, openAgentEditor, openProjectForm, nextPeerName, saveLabel, openJoin } from './app-editors.js';
export { openRedispatch } from './app-redispatch.js';
export { refresh, connectLive } from './app-sync.js';
export { wakePeer, openWakeAll } from './app-wake.js';

const VIEWS = { overview: renderOverview, running: renderRunning, decisions: renderDecisions, artifacts: renderArtifacts, compose: renderCompose, peers: renderPeers, settings: renderSettings, history: renderHistory };
// ---------- 本机外观与品牌（wb.prefs 单 key，服务端 0 改动）----------
export const PREF = { titleMax: Prefs.TITLE_MAX, subtitleMax: Prefs.SUBTITLE_MAX };
export function setPrefs(patch) {
  const r = Prefs.update(patch);
  if (!r.ok) toast('外观未能写入本机存储（隐私模式或配额已满），本次只在当前页面生效', 'warn');
  return r;
}
// 按 'theme.radius' 这样的路径写一份副本；range / color 的 input 事件只改内存再 apply，落盘留给 change
export function applyPath(p, path, value) {
  const [group, key] = String(path).split('.');
  if (!p[group] || !key) return p;
  p[group][key] = value;
  return p;
}
export function coerce(path, raw) {
  if (raw === 'true' || raw === 'false') return raw === 'true';
  if (/^(theme\.radius|theme\.fontScale|theme\.navWidth)$/.test(path)) return Number(raw);
  return String(raw == null ? '' : raw);
}
function previewPref(path, raw) { Prefs.apply(applyPath(Prefs.load(), path, coerce(path, raw))); }
// ---------- 渲染 ----------
export const LEAD_REASON = { takeover: '代理接管', handback: '交还 Leader', user_handback: '用户收回 Leader', lease_lost: 'Leader 被他人持有', guard_forced: '守护强制释放' };
function renderLeadEvents(s) {
  // 常驻交接通知：由未 ack 事件驱动，绝不自动消失（不复用 toast）。点卡片才开详情，× 调服务端 ack 销账。
  const box = $('#leadEvents'); if (!box) return;
  const evs = (s.features && s.features.lead_events === false) ? [] : (s.lead_events_open || []);
  box.hidden = !evs.length;
  if (!evs.length) { if (box.innerHTML) box.innerHTML = ''; return; }
  const unstable = evs.some(e => e.unstable);
  // 事后已恢复（现在有人持有租约，且 epoch 比事件新）：仍需手动关闭，但降为灰色，不再像正在发生的警报
  const lease = s.lease || {};
  const resolved = e => !!lease.holder && Number(lease.epoch) > Number(e.epoch);
  const head = unstable ? `<div class="row between lead-events-head"><b>Leader 不稳定：10 分钟内交接 ${esc(String(evs[0].recent_total || evs.length))} 次</b><button type="button" class="small" data-action="lead-ack-all">全部关闭</button></div>`
    : (evs.length > 1 ? `<div class="row between lead-events-head"><b>${evs.length} 条 Leader 交接</b><button type="button" class="small" data-action="lead-ack-all">全部关闭</button></div>` : '');
  morph(box, head + evs.map(e => {
    const from = e.from ? displayName(e.from) : '无人';
    const to = e.to ? displayName(e.to) : '无人';
    const done = resolved(e);
    return `<div class="lead-event${done ? ' resolved' : ''}" data-action="lead-detail" data-id="${attr(e.id)}" role="button" tabindex="0">
      <span class="chip ${done ? '' : 'warn '}plain">${esc(LEAD_REASON[e.reason] || e.reason)}</span>
      <span class="min0 grow truncate">Leader 从 <b>${esc(from)}</b> 变为 <b>${esc(to)}</b>${Number(e.count) > 1 ? `　×${esc(String(e.count))}` : ''}${done ? `<span class="faint">　· 已恢复，现在由 <b>${esc(displayName(lease.holder))}</b> 负责</span>` : ''}</span>
      <span class="faint">epoch ${esc(String(e.epoch))} · ${esc(relTime(e.at))}</span>
      <button type="button" class="ghost icon x" data-action="lead-ack" data-id="${attr(e.id)}" aria-label="关闭这条通知">${icon('close')}</button>
    </div>`;
  }).join(''));
}
function renderShell(s) {
  const p = project(s);
  const db = $('#dispatchBtn'); if (db) { if (s.view === 'compose') db.setAttribute('aria-current', 'page'); else db.removeAttribute('aria-current'); }
  const options = s.projects.length ? s.projects.map(x => `<option value="${attr(x.slug)}" ${p && p.slug === x.slug ? 'selected' : ''}>${esc(x.name || x.slug)}</option>`).join('') : '<option value="">暂无项目</option>';
  const sel = $('#projectSelect'); if (sel.innerHTML !== options) sel.innerHTML = options; if (p) sel.value = p.slug;
  const counters = { overview: p ? countPending(p) : 0, running: p ? runningDecisions(p).length : 0, decisions: p ? decisions(p).filter(d => ['merged', 'adopted', 'archived'].includes(d.status)).length : 0, peers: onlineAgents(s).length };
  morph($('#nav'), `<div class="nav-heading">工作区</div>${NAV.map(([key, ic, label]) => `<button type="button" class="${s.view === key ? 'active' : ''}" data-action="view" data-view="${key}" aria-current="${s.view === key ? 'page' : 'false'}">${icon(ic)}<span>${label}</span>${counters[key] ? `<span class="count" data-count-key="${key}">${counters[key]}</span>` : ''}</button>`).join('')}${HISTORY_NAV.map(([key, ic, label]) => `<button type="button" class="${s.view === key ? 'active' : ''}" data-action="view" data-view="${key}" aria-current="${s.view === key ? 'page' : 'false'}">${icon(ic)}<span>${label}</span></button>`).join('')}${pluginNav()}`);
  const c = s.controller || {}, st = !s.connected ? 'offline' : c.online === false ? 'offline' : c.status === 'busy' || c.status === 'relay' ? 'busy' : 'online';
  $('#service').className = `service ${st}`;
  const labelOn = !(s.features && s.features.lead_label === false);
  // 多活：两位及以上 Leader 在线时顶栏写人数（名单在 Agent 页 Leader 面板），只有一位时写它自己报的名字
  const leaders = Array.isArray(s.leaders) ? s.leaders : [];
  const ctlName = leaders.length === 1 && leaders[0].label ? leaders[0].label : displayName('controller');
  const ctlLabel = leaders.length > 1 ? ` ×${leaders.length}` : ctlName !== 'controller' ? ` ${esc(ctlName)}` : '';
  const renameBtn = labelOn && s.connected ? `<button type="button" class="ghost icon small" data-action="rename-peer" data-peer="${c.status === 'relay' && c.relay ? attr(c.relay) : 'controller'}" aria-label="给 Leader 起显示名" data-tip="改显示名（协议 ID 不变）">${icon('edit')}</button>` : '';
  morph($('#service'), `<i></i><span>${!s.connected ? '工作台失联' : st === 'offline' ? `Leader${ctlLabel} 离线` : c.status === 'relay' ? `代理 Leader ${esc(displayName(c.relay))}` : st === 'busy' ? `Leader${ctlLabel} 处理中` : `Leader${ctlLabel} 在线`}</span><span class="faint">· ${s.live === 'sse' ? '实时' : '轮询'}</span>${renameBtn}`);  // morph 而不是 innerHTML：每次刷新整块重写会换掉「改显示名」按钮，键盘焦点和悬停说明都会丢
  morph($('#sideStats'), `<div class="line"><span>在线 Agent</span><b>${onlineAgents(s).length}/${agentList(s).length}</b></div><div class="line"><span>排队指令</span><b>${(s.commands || []).length}</b></div><div class="line"><span>同步</span><b>${esc(relTime(s.time))}</b></div>`);
  const banner = $('#banner'); const offline = s.connected && c.online === false;
  banner.hidden = !offline && s.connected;
  banner.className = `banner ${s.connected ? 'clickable' : 'danger'}`;
  banner.setAttribute('role', 'button'); banner.dataset.action = s.connected ? 'join-controller' : '';
  banner.innerHTML = `${icon('warn')}<span>${!s.connected ? '无法连接工作台服务，正在重试…' : onlineAgents(s).length === 0 ? '全部 Agent 与 Leader 失联，点此重新接入 Leader' : `Leader 离线 ${esc(relTime(c.last_seen || c.updated))}，页面操作会排队 · 点此接入 Leader`}</span>${s.connected ? `<button type="button" class="small" data-action="join-controller" style="margin-left:auto">${icon('plus')}接入 Leader</button>` : ''}`;
  // 品牌标题是本机偏好（wb.prefs），applyBrand 把它写到 <html data-brand-title>；这里读属性而不是硬编码，SSE 重绘才不会打回默认文案
  document.title = `${counters.overview ? `(${counters.overview}) ` : ''}${document.documentElement.dataset.brandTitle || 'Agent 协作台'}`;
}
// 插件自带页面（ADR 0014：Arena 等可选功能做成插件）：启用且声明了 page 的插件在导航里各占一个入口，打开插件页
function pluginNav() { const ps = F.pluginPages || []; return ps.length ? `<div class="nav-heading nav-heading-gap">插件</div>${ps.map(x => `<button type="button" data-action="open-plugin-page" data-href="${attr(x.href)}">${icon('box')}<span>${esc(x.name)}</span></button>`).join('')}` : ''; }
async function loadPluginPages() { try { const r = await api('/api/plugins'); loadWakeBound(); F.pluginPages = (r.plugins || []).filter(x => x.enabled && x.page && !(x.errors || []).length).map(x => ({ name: x.name || x.id, href: `/plugins/${encodeURIComponent(x.id)}/${x.page}` })); renderShell(S()); } catch { /* 插件列表拿不到：不显示入口，不影响工作台 */ } }
function flipList(container, mutate) {
  // FLIP 离场+重排：两段循环都按 data-id 键控，idiomorph 保留或替换节点都成立。
  // 关键：idiomorph 对无 id 但同 tag 的节点会「就地复用」——删除中间项 b 时，b 的 DOM 对象会被改写成 c 的内容。
  // 所以 mutate 之前就按 data-id 预 clone 一份完整快照，幽灵只从快照取，绝不在 mutate 之后再碰旧引用。
  if (!container || !container.isConnected) { mutate(); return; }
  container.querySelectorAll('.leaving').forEach(g => g.remove());   // 残留幽灵不参与测量，也不被当成真实项
  const snapById = new Map(), first = new Map();
  for (const k of [...container.children]) {
    const id = k.dataset && k.dataset.id;
    if (!id || k.classList.contains('leaving')) continue;
    snapById.set(id, k.cloneNode(true)); first.set(id, k.getBoundingClientRect());
  }
  mutate();
  if (!container.isConnected) return;
  const nowById = new Map();
  for (const k of [...container.children]) {
    const id = k.dataset && k.dataset.id;
    if (id && !k.classList.contains('leaving')) nowById.set(id, k);
  }
  for (const [id, a] of first) {
    const k = nowById.get(id); if (!k) continue;                      // 保留项：按 id 取新节点，不依赖旧引用
    if (typeof k._flipRestore === 'function') k._flipRestore();       // 上一轮位移动画未完成就被打断：先恢复，不叠加临时 transition
    const b = k.getBoundingClientRect();
    const dx = a.left - b.left, dy = a.top - b.top;
    if (!dx && !dy) continue;
    const savedTransition = k.style.transition || '';                 // 记住行内原值（通常为空，即回到样式表的 border-color 过渡）
    k.style.transition = 'none'; k.style.transform = `translate(${dx}px,${dy}px)`;
    k.getBoundingClientRect();                                        // 强制回流提交起始帧
    let timer = 0;
    const restore = () => { k.style.transition = savedTransition; k.style.transform = ''; k.removeEventListener('transitionend', onEnd); clearTimeout(timer); k._flipRestore = null; };
    const onEnd = e => { if (e.target === k && (!e.propertyName || e.propertyName === 'transform')) restore(); };
    k._flipRestore = restore;
    k.addEventListener('transitionend', onEnd);
    timer = setTimeout(restore, 400);                                 // transitionend 不触发（被隐藏 / 同帧回到原位）也能恢复
    requestAnimationFrame(() => { if (k._flipRestore !== restore) return; k.style.transition = 'transform .22s cubic-bezier(.2,.8,.2,1)'; k.style.transform = ''; });  // 后台页定时器已先 restore、rAF 晚到：不再写 style，避免留下临时 transition
  }
  const c = container.getBoundingClientRect();
  for (const [id, a] of first) {
    if (nowById.has(id)) continue;                                    // 离场项：新列表里没有这个 id
    const ghost = snapById.get(id); if (!ghost) continue;             // 只用 mutate 前的快照
    ghost.style.position = 'absolute';
    ghost.style.left = `${a.left - c.left - container.clientLeft}px`; // 减 clientLeft/Top：视口坐标换算到 padding box
    ghost.style.top = `${a.top - c.top - container.clientTop}px`;
    ghost.style.width = `${a.width}px`;
    ghost.style.maxHeight = `${a.height}px`;                          // leave 关键帧从真实高度收到 0，正常卡片不再背 max-height
    ghost.classList.add('leaving');
    // 幽灵是纯视觉残影：去掉业务 data-id / 动作 / id，不可聚焦、不可点、辅助技术不可见，防止用户对着残影重复操作
    ghost.removeAttribute('data-id'); ghost.removeAttribute('data-action'); ghost.removeAttribute('id'); ghost.removeAttribute('data-key');
    ghost.setAttribute('aria-hidden', 'true'); ghost.setAttribute('inert', ''); ghost.setAttribute('tabindex', '-1');
    ghost.style.pointerEvents = 'none';
    if (typeof ghost.querySelectorAll === 'function') ghost.querySelectorAll('[data-action],[data-id],[id],button,a,input,select,textarea,[tabindex]').forEach(el => { el.removeAttribute('data-action'); el.removeAttribute('data-id'); el.removeAttribute('id'); el.setAttribute('tabindex', '-1'); if ('disabled' in el) el.disabled = true; });
    container.appendChild(ghost);
    ghost.addEventListener('animationend', () => ghost.remove(), { once: true });
    setTimeout(() => ghost.remove(), 400);
  }
}
// 概览一屏（2026-10-02 用户：「所有操作都能在一页内看到」）：两栏的顶边到窗口底就是两栏的高度，内容在栏内滚动。
// 量的是两栏在文档里的位置（与滚动无关），写进 --ov-top；窄屏（单栏）由 CSS 媒体查询放开，不受影响
// ADR 0015：概览的两栏（.ov-grid）与列表页的面板（.fit-panel）都按「顶边到窗口底」定高，内容在面板里滚动
function fitPage() {
  const g = $('.ov-grid, .fit-panel, .compose-cols'); if (!g) return;
  const top = Math.ceil(g.getBoundingClientRect().top + window.scrollY);   // 向上取整：四舍五入时小数顶边会让整页多出 1px 滚动条
  document.documentElement.style.setProperty(g.matches('.ov-grid') ? '--ov-top' : '--fit-top', `${top}px`);
}
window.addEventListener('resize', fitPage);
// 拍板选项：选中后提交行写明「已选 …」，不然隐藏的单选框点了看不出变化（2026-10-02 用户：「选项点不动」）
document.addEventListener('change', e => {
  const r = e.target; if (!r.matches?.('form[data-form="decision-answer"] input[type=radio]')) return;
  const form = r.closest('form'), out = form && $('[data-dn-picked]', form); if (!out) return;
  const label = r.closest('label')?.querySelector('span')?.textContent || r.value;
  out.textContent = `已选「${label}」，点「提交」生效 · 就地提交，不会打开新窗口`; out.classList.add('on');
});
export function renderView(s) {
  const p = project(s);
  const root = $('#content');
  if (!p && !['peers', 'settings', 'history'].includes(s.view)) { morph(root, `<div class="view"><header class="view-head"><div><div class="eyebrow">开始之前</div><h1>创建一个项目</h1><p>项目保存默认团队、上下文文件与决策流。</p></div></header><div class="empty" style="padding:60px"><strong>还没有可显示的项目</strong><span>创建项目后，概览会集中显示待处理事项、最近决策和下一步。</span><button type="button" class="primary" data-action="new-project" style="margin-top:12px">${icon('plus')}新建项目</button></div></div>`); return; }
  if (s.view === 'compose' && F.composerDirty && $('#composerForm')) return;
  const list = s.view === 'overview' ? $('#focusList') : null;
  const paint = () => morph(root, (VIEWS[s.view] || renderOverview)(p));
  if (list && !matchMedia('(prefers-reduced-motion: reduce)').matches) flipList(list, paint); else paint();
  $$('[data-count]', root).forEach(el => animateNumber(el, Number(el.dataset.count)));
  fitPage();
  if (s.view === 'settings' && !s.settings) loadSettings();
  if (s.view === 'decisions') { reconcileMerges(root); $$('details[data-merge][open]', root).forEach(d => loadMerge(d)); }
}
// 调度表与租约的局部重绘：顶栏与侧栏照常更新，视图内只 morph 身份看板、顶替顺序卡与任务行，整页不重建
export function renderScoped(s) {
  renderShell(s);
  // 两段 HTML 自带同 id 外壳，必须整体替换；按 innerHTML 替换会把 #leaderBoard 套进 #leaderBoard（控制台 duplicate ids 警告）
  const board = $('#leaderBoard'); if (board) morph(board, leaderBoard(), { outer: true });
  const relay = $('#relayCard'); if (relay) morph(relay, relayCardHtml(), { outer: true });
  const list = $('#taskList');
  if (list || s.view === 'running') {
    // 列表清空 / 由空变有 / 筛选片该出该隐：局部 morph 画不出空态和筛选片，整页重画；否则只换行并同步列表外的计数（fable2 9a21）
    const m = runningMeta();
    if (!list !== !m.listN || m.showChip !== !!$('[data-filter-stuck="toggle"]')) { if (s.view === 'running') { renderView(s); return; } }
    else if (list) {
      morph(list, taskListHtml(undefined, m.rows));
      const rc = $('#runningCount'); if (rc && rc.textContent !== m.count) rc.textContent = m.count;
      const sn = $('#stuckN'); if (sn && sn.textContent !== String(m.stuckN)) sn.textContent = String(m.stuckN);
      const ch = sn && sn.closest('[data-filter-stuck]'); if (ch && (ch.dataset.tip || '') !== m.tip) { if (m.tip) ch.dataset.tip = m.tip; else delete ch.dataset.tip; }  // 悬停说明也同步（fable2 0c0f）
    }
  }
  tickCountdown();
}
function tickRelTime() { $$('[data-reltime]').forEach(el => { const v = relTime(el.dataset.reltime); if (el.textContent !== v) el.textContent = v; }); }
function tickCountdown() { $$('[data-countdown]').forEach(el => { const ms = Number(el.dataset.countdown); if (Number.isFinite(ms) && ms) el.textContent = fmtLeft(Math.round((ms - Date.now()) / 1000)); }); }

export function switchView(view, push = true) {
  if (!VIEWS[view]) { toast(`没有「${view}」这个视图，仍停留在${NAV.find(n => n[0] === S().view)?.[2] || '当前页'}`, 'warn', { ms: 2000 }); if (url.get('view', '') !== S().view) url.set({ view: S().view }, true); return; }
  if (view !== S().view) { const now = Date.now(); if (now - (switchView._last || 0) < 300) return; switchView._last = now; }  // e131：只在真切换时记节流戳
  if (cmdkOpen()) closeCmdk();
  if (drawerOpen()) { closeDrawer(true); if (history.state && history.state.drawer) history.replaceState({ ...history.state, drawer: false }, '', location.href); }
  if (view === S().view) { if (url.get('view', 'overview') !== view) url.set({ view }, true); return; }
  document.body.classList.remove('settled'); clearTimeout(switchView._t); switchView._t = setTimeout(() => document.body.classList.add('settled'), 900);
  localStorage.setItem('bus.view', view); url.set({ view }, !push);
  viewTransition(() => { store.set({ view }); window.scrollTo({ top: 0 }); });
  startHistoryView(view);
}

export function sectionFromMerge(text, kind) {
  const aliases = { consensus: ['共识要点', '共识'], difference: ['分歧点', '分歧'], final: ['最终方案'], evidence: ['验证依据', '验证'] }[kind] || [];
  const lines = String(text || '').split(/\r?\n/); let start = -1, end = lines.length;
  lines.forEach((line, i) => { const m = line.match(/^#{1,4}\s*(.+?)\s*$/); if (!m) return; const h = m[1].replace(/[：:]/g, '').trim(); if (start < 0 && aliases.some(a => h.includes(a))) start = i + 1; else if (start >= 0 && i >= start) end = Math.min(end, i); });
  return start < 0 ? '' : lines.slice(start, end).join('\n').trim();
}

// 追问 Team 快捷选（6d2a）：原 Team（上一轮全部，含离线/已移除，count 在线/总数）、动态在线、项目自定义 Team；全员离线的 chip 只 dim 不 disabled
function followupTeams(d) {
  const orig = (d?.progress?.meta?.peers?.length ? d.progress.meta.peers : d?.peers) || [];
  const alive = new Set(agentList().map(a => a.peer)), live = names => (names || []).filter(n => alive.has(n) && agentByName(n).online);
  const on = live(orig), absent = orig.filter(n => !on.includes(n)), online = onlineAgents().map(a => a.peer);
  const custom = effectiveTeams().filter(t => ![PROJECT_TEAM, DYNAMIC_TEAM].includes(t.name)).map(t => { const l = live(t.peers); return { name: t.name, peers: t.peers || [], count: `${l.length}/${(t.peers || []).length}`, dim: !l.length, tip: l.length ? (t.note || '') : '全员离线', mode: t.mode }; });
  return [{ name: '原 Team', peers: orig, count: `${on.length}/${orig.length}`, dim: !on.length, tip: absent.length ? `缺席：${absent.map(displayName).join('、')}` : '上一轮成员全部在线' }, { name: '动态在线', peers: online, count: online.length, dim: !online.length, tip: '当前全部在线 Agent' }, ...custom];
}
export function followupPanel(d, show) {
  const orig = (d.progress?.meta?.peers?.length ? d.progress.meta.peers : d.peers) || [];
  const exists = n => agentList().some(x => x.peer === n);                       // 先判存在，不用 agentByName 的 {online:false} 兜底（会把已移除误判成离线）
  const peers = orig.filter(n => exists(n) && agentByName(n).online);            // 默认只勾在线者；离线者变暗仍可手动勾
  releaseAttachments(F.followup);
  F.followup = { taskId: d.task_id, peers: [...peers], mode: d.mode || 'independent', attachments: [], teamName: '原 Team' };
  const names = [...new Set([...agentList().map(a => a.peer), ...orig])];
  const row = n => {
    if (!exists(n)) return `<label class="fchip dim" aria-disabled="true"><input type="checkbox" name="fpeer" value="${attr(n)}" disabled class="sr-only">${esc(displayName(n))}<small class="faint" style="margin-left:6px">已移除</small></label>`;
    const a = agentByName(n), on = peers.includes(n);
    return `<label class="fchip ${on ? 'on' : ''} ${a.online ? '' : 'dim'}" style="cursor:pointer"><input type="checkbox" name="fpeer" value="${attr(n)}" ${on ? 'checked' : ''} class="sr-only">${esc(displayName(n))}${a.online ? '' : `<small class="faint" style="margin-left:6px">离线 ${esc(relTime(a.last_seen))}</small>`}</label>`;
  };
  return `<form data-form="followup" data-followup-panel ${show ? '' : 'hidden'} class="card pad stack" style="margin-top:14px;background:var(--surface-2)"><div class="field"><label for="followupQ">继续讨论</label><textarea id="followupQ" rows="4" data-dropzone placeholder="指出哪一处需要补证据、反驳或澄清…">请基于「${esc(titleOf(d))}」的合并稿，补充仍不确定的依据。</textarea></div><div class="row wrap" style="gap:8px;align-items:center"><button type="button" class="small ghost" data-action="pick-images">${icon('plus')}图片</button><span class="faint" style="font-size:calc(12px * var(--fs-scale, 1))">可粘贴或拖入上方输入框；部分 Agent 可能无法查看图片</span><div class="thumbs" id="followupTray"></div></div><div class="field"><label>发送给</label>${teamChips({ teams: followupTeams(d), active: F.followup.teamName, action: 'followup-team', editAction: '' })}<div class="row wrap">${names.map(row).join('')}</div></div><div class="row"><button type="submit" class="primary">${icon('send')}发送追问</button><span class="muted" style="font-size:calc(12px * var(--fs-scale, 1))">会新开一轮任务并关联本决策</span></div></form>`;
}
export function teamRow(t = {}, i = 0) { return `<div class="card pad stack" data-team-row style="gap:8px"><div class="row between"><strong>Team ${i + 1}</strong><button type="button" class="ghost small danger" data-action="remove-team-row">删除</button></div><div class="form-row"><div class="field"><label>名称</label><input data-team-field="name" value="${attr(t.name || '')}" placeholder="例如：快审" autocomplete="off"></div><div class="field"><label>模式</label><select data-team-field="mode">${['independent', 'redteam', 'relay'].map(m => `<option value="${m}" ${t.mode === m ? 'selected' : ''}>${esc(modeLabel(m))}</option>`).join('')}</select></div></div><div class="field"><label>Agent（逗号分隔，空 = 全部在线）</label><input data-team-field="peers" value="${attr((t.peers || []).join(', '))}" autocomplete="off"></div><div class="field"><label>备注</label><input data-team-field="note" value="${attr(t.note || '')}" autocomplete="off"></div></div>`; }
window.__nextPeerName = nextPeerName;
export async function uploadAttachment(file) {
  const b64 = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(',')[1]); r.onerror = rej; r.readAsDataURL(file); });
  return api('/api/upload', { method: 'POST', body: JSON.stringify({ name: file.name, type: file.type, data: b64 }) });
}
// 附件目标按事件源 DOM 决定：在追问面板内 → F.followup，否则 → 发起页草稿；两个对象各持一份 attachments，互不串
const attachTarget = el => el?.closest?.('[data-followup-panel]') ? F.followup : ensureComposer();
const trayOf = target => target === F.followup ? $('#followupTray') : $('#attachmentTray');
// 提交前把尚未上传的附件逐个传到 /api/upload，返回相对工作区的路径列表；composer 与追问共用
export async function uploadPending(list) {
  const paths = [];
  for (const a of list || []) { if (!a.path) a.path = (await uploadAttachment(a.file)).path; paths.push(a.path); }
  return paths;
}
export function clearSendConfirm() { $$('[data-send-confirm]').forEach(el => el.remove()); }
// ---------- 事件委托 ----------
export const ACTIONS = {
  ...Object.fromEntries(Object.entries(HISTORY_ACTIONS).map(([k, fn]) => [k, (t, e) => fn(t, () => renderView(S()), e)])),
  view: (t) => switchView(t.dataset.view),
  'goto-status': (t) => { F.filters.decisionStatus = t.dataset.status || ''; url.set({ status: F.filters.decisionStatus }); switchView(t.dataset.view); },
  'lead-detail': t => openLeadDetail(t.dataset.id),
  'requeue-command': async t => { if (!(await confirmDialog({ title: '把指令退回队列', body: '只在确认 Leader 已经断线、没有在处理这条指令时使用。退回后 Leader 会重新领取并执行一次；如果它其实还在处理，可能被执行两次。', confirmText: '退回队列', danger: true }))) return; t.classList.add('busy'); try { await api('/api/command/requeue', { method: 'POST', body: JSON.stringify({ id: t.dataset.id }) }); toast('已退回队列，Leader 会重新领取', 'ok'); closeDrawer(); await refresh(true); } catch (e) { toast(e.message || '退回失败', 'bad'); } finally { t.classList.remove('busy'); } },
  'join-pick-model': t => { const i = $('#joinModel'); if (!i) return; i.value = t.dataset.model; i.dispatchEvent(new Event('change', { bubbles: true })); $$('.join-models .chip').forEach(b => b.classList.toggle('ok', b === t)); },
  'join-save-label': async t => { const v = ($('#joinLabel')?.value || '').trim(); t.classList.add('busy'); try { const r = await saveLabel('controller', v, !(S().peers || []).some(x => x.peer === 'controller')); if (r && openJoin._render) await openJoin._render(); } finally { t.classList.remove('busy'); } },
  'lead-ack': async (t, e) => { e?.stopPropagation(); try { await api('/api/lead_event/ack', { method: 'POST', body: JSON.stringify({ id: t.dataset.id }) }); closeDrawer(); await refresh(); } catch (err) { toast(`关闭通知失败：${err.message}`, 'bad'); } },  // ac1a：ack 后走指纹比对，纯通知变化只局部收起 banner
  'lead-ack-all': async (t, e) => { e?.stopPropagation(); try { await api('/api/lead_event/ack', { method: 'POST', body: JSON.stringify({ all: true }) }); await refresh(); } catch (err) { toast(`关闭通知失败：${err.message}`, 'bad'); } },
  'rename-peer': async t => {
    const peer = t.dataset.peer; if (!peer) return;
    if (S().features && S().features.lead_label === false) return toast('显示名功能已关闭（console/features.json lead_label=false）', 'warn');
    const rec = (S().peers || []).find(x => x.peer === peer); const cur = (rec || {}).label || (S().pending_labels || {})[peer] || '';
    const alias = agentMeta(peer).display_name || '';
    const aliasNote = alias ? `<div class="banner warn" style="margin-top:6px;font-size:calc(12px * var(--fs-scale, 1))">本项目给它设了别名「${esc(alias)}」，项目别名优先于全局显示名：改完后本项目里仍显示「${esc(alias)}」，其它项目与 Leader 区显示新名。要在本项目看到新名，请在 Agent 资料里清掉别名。</div>` : '';
    const r = await confirmDialog({ title: `重命名 ${peer === 'controller' ? 'Leader（controller）' : peer}`, confirmText: '保存',
      body: `<span class="faint" style="font-size:calc(12px * var(--fs-scale, 1))">只改显示名。工具参数、租约 holder、心跳里仍然是 <b class="mono">${esc(peer)}</b>，协议不受影响。显示名是数据，不会进入任何指令。${rec ? '' : '<br>该 Agent 还没有连接记录：将保存为「首次连接前的显示名」，连上后自动生效。'}</span>${aliasNote}`,
      input: { label: '显示名（最多 24 字，留空恢复原名；不能含引号 / 反引号 / 尖括号）', value: cur, maxlength: 24 } });
    if (!r || !r.ok) return;
    await saveLabel(peer, r.value, !rec);
  },
  // 多活：给某一位 Leader 会话起名。还没在 wait 里报名字的老会话只显示会话号，这里补
  'rename-leader': async t => {
    const session = t.dataset.session; if (!session) return;
    const r = await confirmDialog({ title: `给 Leader 会话 ${session} 起名`, confirmText: '保存',
      body: '<span class="faint">只改这一位 Leader 的显示名，别的 Leader 不变。Leader 下次在 wait 里自己报名字时会覆盖这里。</span>',
      input: { label: '显示名（最多 24 字，留空恢复会话号）', value: t.dataset.label || '', maxlength: 24 } });
    if (!r || !r.ok) return;
    try { await api('/api/peer/label', { method: 'POST', body: JSON.stringify({ peer: 'controller', session, label: r.value }) }); toast('已保存', 'ok'); await refresh(true); }
    catch (e) { notify.error(e); }
  },
  refresh: () => refresh(true).then(() => notify('refreshed')),
  cmdk: () => openCmdk(),
  'close-drawer': () => { releaseAttachments(F.followup); closeDrawer(); },
  'confirm-send': t => { const f = ensureComposer(); if (!f) return; submitDispatch(f, $('#composerSubmit') || $('#qdSubmit'), F.sendConfirm); },
  'cancel-send': () => { F.sendConfirm = null; clearSendConfirm(); toast('已取消，没有发送', 'warn'); },
  'dispatch': t => openDispatch({ peers: (t.dataset.dispatchPeers || '').split(',').map(x => x.trim()).filter(Boolean), files: (t.dataset.dispatchFiles || '').split(',').map(x => x.trim()).filter(Boolean), mode: t.dataset.dispatchMode || '', hint: t.dataset.dispatchHint || '' }),
  'dispatch-team': t => { const team = effectiveTeams().find(x => x.name === t.dataset.team); const f = ensureComposer(); if (!f) return; f.teamName = t.dataset.team; f.peers = teamPeersOf(f.teamName); if (team?.mode) { f.mode = team.mode; const s = $('[data-qd="mode"]'); if (s) s.value = f.mode; } $$('.team-chips .fchip', $('#drawer')).forEach(b => b.classList.toggle('on', b.dataset.team === f.teamName)); const c = $('[data-qd-count]'); if (c) c.textContent = `${f.peers.length} 人 · 预计 ${dispatchEstimate(f.peers).copies} 份回应`; },
  'dispatch-expand': () => { const f = ensureComposer(); const q = $('#qdQuestion'), m = $('[data-qd="mode"]'); if (f && q) { f.question = q.value; if (m) f.mode = m.value; } closeDrawer(); switchView('compose'); },
  'new-project': () => openProjectForm(false), 'edit-project': () => openProjectForm(true), 'edit-plan': () => openPlanEditor(), 'edit-teams': () => openTeamEditor(),
  'set-relay-order': () => { const cur = S().priority?.relay_order || []; const gs = S().agent_groups || []; openDrawer({ title: '顶替顺序 · 高级编辑', subtitle: `逐行填模型组 id 或 Agent 名，整表覆盖（rev ${S().priority?.rev ?? 0}）。日常调整请直接在 Agent 页拖动。`, body: `<form data-form="relay-order" class="stack"><div class="field"><label for="relayOrder">每行一项（模型组 id 或 Agent 名）</label><textarea id="relayOrder" rows="8" class="mono" style="font-size:calc(12px * var(--fs-scale, 1))" spellcheck="false">${esc(cur.join('\n'))}</textarea><span class="help">可用的模型组：${esc(gs.map(g => g.id).join('、') || '无')}</span></div><div class="row"><button type="submit" class="primary">保存</button><button type="button" class="ghost" data-action="close-drawer">取消</button></div></form>` }); },
  'relay-factory-reset': async () => { if (!await confirmDialog({ title: '恢复出厂 SOTA 序', body: '整表改写为内置的模型组白名单顺序，现有手填的 Agent 名会被移除。', confirmText: '恢复出厂序' })) return; await commitRelayOrder(null, '已恢复出厂 SOTA 序'); },
  'relay-remove': t => commitRelayOrder((S().priority?.relay_order || []).filter(x => x !== t.dataset.item), '已移出顶替顺序'),
  'relay-add': () => { const v = $('#relayAdd')?.value; if (!v) return toast('先选一个模型组或 Agent', 'warn'); commitRelayOrder([...(S().priority?.relay_order || []), v], '已加入顶替顺序'); },
  'reset-group-order': () => { localStorage.removeItem('wb.groupOrder'); renderView(S()); },
  'peers-prune': async () => {
    try {
      const probe = await api('/api/peers/prune', { method: 'POST', body: JSON.stringify({ dry_run: true }) });
      if (!probe.pruned?.length) {
        return toast('当前没有满足清理条件（离线超过12小时且无排队任务）的 Agent', 'info');
      }
      const names = probe.pruned.map(displayName).join('、');
      if (!await confirmDialog({
        title: '清理失效 Agent',
        body: `检测到 ${probe.pruned.length} 个失效 Agent：<br><b>${esc(names)}</b><br><br>将归档并清理其登记文件。当前在线或有排队任务的 Agent 不受影响。`,
        confirmText: '确定清理',
        danger: true,
      })) return;
      const res = await api('/api/peers/prune', { method: 'POST', body: '{}' });
      if (res.ok) {
        toast(`已清理 ${res.count || 0} 个失效 Agent${res.pruned?.length ? '：' + res.pruned.map(displayName).join('、') : ''}`, 'ok');
        await refresh(true);
      } else {
        toast(res.error || '清理失败', 'warn');
      }
    } catch (e) {
      notify.error(e);
    }
  },
  'group-pin-top': t => {
    const gid = t.dataset.group;
    if (!gid) return;
    const cur = groupOrder().filter(x => x !== gid);
    saveGroupOrder([gid, ...cur]);
    toast('已置顶该模型组', 'ok');
    renderView(S());
  },
  // 唤醒掉线的 Agent（app-wake.js）：按钮文字跟着结果变，醒没醒看心跳
  'wake-peer': async t => { t.classList.add('busy'); try { const r = await wakePeer(t.dataset.peer); if (r !== 'shown' && t.isConnected) t.lastChild.textContent = r === 'sent' ? '已发送，等心跳' : '已复制，去粘贴'; } catch (e) { notify.error(e); } finally { t.classList.remove('busy'); } },
  'wake-all': () => openWakeAll(), 'wake-auto-all': t => wakeAutoAll(t),
  'edit-agent': t => openAgentEditor(t.dataset.peer), 'join-peer': t => openJoin('peer', t.dataset.peer || '', t.dataset.model || ''), 'join-controller': () => openJoin('controller'),
  'add-team-row': () => $('#teamRows').insertAdjacentHTML('beforeend', teamRow({}, $$('[data-team-row]').length)),
  'remove-team-row': t => t.closest('[data-team-row]').remove(),
  'pick-images': t => { const target = attachTarget(t); const inp = t.closest('form')?.querySelector('input[type="file"]') || $('#imagePicker'); if (!inp) return; inp.onchange = () => { addAttachments([...inp.files], target); inp.value = ''; }; inp.click(); },
  'composer-group': t => { const f = ensureComposer(); const members = agentList().filter(a => groupOfAgent(a)?.id === t.dataset.group && a.online).map(a => a.peer); const all = members.every(p => f.peers.includes(p)); f.peers = all ? f.peers.filter(p => !members.includes(p)) : [...new Set([...f.peers, ...members])]; f.teamName = ''; composerStateChanged(); },
  'open-plugin-page': t => { location.href = t.dataset.href; },
  'choose-files': () => openFiles(), 'clear-files': () => { ensureComposer().context_files = []; F.composerDirty = true; store.set({ time: S().time + ' ' }); },
  'save-files': () => { const sel = $('#drawer')._fileSel; if (sel) { ensureComposer().context_files = [...sel]; F.composerDirty = true; } closeDrawer(); F.composerDirty = false; renderView(S()); F.composerDirty = !!ensureComposer().question; },
  'remove-attachment': t => { const f = attachTarget(t); const a = f.attachments.splice(Number(t.dataset.index), 1)[0]; if (a?.previewUrl) URL.revokeObjectURL(a.previewUrl); renderTray(f); },
  'clear-composer': async () => { if (F.composerDirty && !(await confirmDialog({ title: '清空当前草稿', body: '问题、附件与选择都会重置。', confirmText: '清空' }))) return; F.composer = null; F.composerDirty = false; renderView(S()); },
  // 只改抽屉里已有 DOM，绝不 renderView/morph（抽屉内 textarea 会丢输入与焦点）；重置语义：先把全部 fpeer 置 false 再勾该 Team 在线者
  'followup-team': t => { const form = t.closest('[data-followup-panel]'); if (!form || !F.followup) return; const d = decisions().find(x => x.task_id === F.followup.taskId) || {}; const team = followupTeams(d).find(x => x.name === t.dataset.team); const pick = new Set((team?.peers || []).filter(n => agentList().some(a => a.peer === n) && agentByName(n).online)); $$('input[name="fpeer"]', form).forEach(cb => { cb.checked = false; cb.closest('.fchip')?.classList.remove('on'); }); $$('input[name="fpeer"]', form).forEach(cb => { if (pick.has(cb.value) && !cb.disabled) { cb.checked = true; cb.closest('.fchip')?.classList.add('on'); } }); $$('[data-action="followup-team"]', form).forEach(b => b.classList.toggle('on', b === t)); F.followup.teamName = t.dataset.team; F.followup.peers = [...pick]; if (team?.mode) F.followup.mode = team.mode; if (!pick.size) toast('该 Team 0 人在线', 'warn'); },
  // 团队按钮（含「全员在线」）：第一次点 = 选上这个团队（即使它本来就是默认）；紧接着再点同一个 = 全不选
  'composer-team': t => { const team = effectiveTeams().find(x => x.name === t.dataset.team); const f = ensureComposer(); const peers = teamPeersOf(t.dataset.team); const same = F.lastTeamClick === t.dataset.team && f.teamName === t.dataset.team && peers.length && peers.every(n => (f.peers || []).includes(n)); if (same) { f.teamName = ''; f.peers = []; F.lastTeamClick = ''; } else { f.teamName = t.dataset.team; f.peers = teamPeersOf(f.teamName); F.lastTeamClick = t.dataset.team; if (team?.mode) f.mode = team.mode; } composerStateChanged(); },
  'toggle-task': t => { const id = t.dataset.id; F.expanded.has(id) ? F.expanded.delete(id) : F.expanded.add(id); localStorage.setItem('bus.expanded', JSON.stringify([...F.expanded])); renderView(S()); },
  'toggle-more': t => { const id = 'more:' + t.dataset.id; F.expanded.has(id) ? F.expanded.delete(id) : F.expanded.add(id); localStorage.setItem('bus.expanded', JSON.stringify([...F.expanded])); renderView(S()); },
  'toggle-relay-card': () => { const id = 'relay-card'; F.expanded.has(id) ? F.expanded.delete(id) : F.expanded.add(id); localStorage.setItem('bus.expanded', JSON.stringify([...F.expanded])); renderView(S()); },
  'open-task': t => openTask(t.dataset.id), 'open-decision': t => openDecision(t.dataset.id), 'open-command': t => openCommand(t.dataset.id), 'followup': t => openDecision(t.dataset.id, true),
  'open-file': t => openPreview(t.dataset.path),
  'open-local': t => api('/api/open', { method: 'POST', body: JSON.stringify({ path: t.dataset.path, reveal: !!t.dataset.reveal }) }).then(() => notify('opened_local')).catch(e => notify.error(e)),
  'copy-text': t => navigator.clipboard.writeText(t.dataset.text).then(() => notify('copied')).catch(() => toast('复制失败，请手动复制', 'warn')),
  'copy-artifact-paths': t => { const proj = S().projects.find(x => x.slug === t.dataset.project) || project(); const d = decisions(proj).find(x => x.task_id === t.dataset.id); const text = artifactsOf(d || {}).map(a => winPath(a.path)).join('\n'); if (!text) return toast('没有可复制的路径', 'warn'); navigator.clipboard.writeText(text).then(() => toast(`已复制 ${text.split('\n').length} 个 Windows 路径`, 'ok')); },
  'copy-prompt': () => { const ta = $('#promptText'); if (ta) navigator.clipboard.writeText(ta.value).then(() => toast('提示词已复制', 'ok')); },
  'artifact-scope': t => { F.filters.artifacts = t.dataset.scope; url.set({ scope: t.dataset.scope === 'all' ? 'all' : '' }); renderView(S()); },
  'toggle-discussion': t => { const p = $('[data-followup-panel]'); if (p) { p.hidden = !p.hidden; t.textContent = p.hidden ? '继续讨论' : '收起讨论'; if (!p.hidden) $('textarea', p).focus(); } },
  'merge-now': t => sendCommand('merge_now', { project: S().project, task_id: t.dataset.id }, '已进入待合并', t),
  'supersede': t => sendCommand('supersede', { project: S().project, task_id: t.dataset.id }, '已标记被取代并移出队列', t),
  'redispatch': async t => { const d = decisions().find(x => x.task_id === t.dataset.id); if (!(await confirmDialog({ title: `重派「${titleOf(d || { task_id: t.dataset.id })}」`, body: '用原任务包开一轮新任务，离线的 Agent 自动换成同组在线 Agent；原任务标记被取代并移出队列。', confirmText: '重派' }))) return; t.classList.add('busy'); try { const r = await api('/api/command', { method: 'POST', body: JSON.stringify({ kind: 'redispatch', payload: { project: S().project, task_id: t.dataset.id } }) }); toast(`已重派为 ${r.task_id}，参与：${(r.peers || []).map(displayName).join('、')}`, 'ok'); F.expanded.delete(t.dataset.id); await refresh(true); } catch (e) { if (await confirmDialog({ title: '无法重派', body: `${esc(e.message)}<br><br>要直接取消这个任务吗？`, confirmText: '取消任务', danger: true })) await ACTIONS['cancel-task-direct'](t); else notify.error(e); } finally { t.classList.remove('busy'); } },
  'requeue-agent': async t => { t.classList.add('busy'); try { const r = await api('/api/command', { method: 'POST', body: JSON.stringify({ kind: 'reassign', payload: { project: S().project, task_id: t.dataset.id, peer: t.dataset.peer } }) }); toast((r.to === r.from ? `已退回给 ${displayName(r.to)} 并续期 60 分钟` : `已改派给 ${displayName(r.to)}`) + (r.listening === false ? `；注意：${displayName(r.to)} 现在没在等任务，要等它上线才会领` : ''), r.listening === false ? 'warn' : 'ok', { ms: r.listening === false ? 6000 : 3000 }); await refresh(true); } catch (e) { if (await confirmDialog({ title: '无法改派', body: `${esc(e.message)}<br><br>要直接取消这个任务吗？取消后会清空队列并归档。`, confirmText: '取消任务', danger: true })) await ACTIONS['cancel-task-direct'](t); else notify.error(e); } finally { t.classList.remove('busy'); } },
  'set-execution': t => { const d = decisions().find(x => x.task_id === t.dataset.id); const order = ['pending', 'released', 'partial', 'n-a']; const nxt = order[(order.indexOf(d?.execution || 'pending') + 1) % 4]; return sendCommand('execution', { project: S().project, task_id: t.dataset.id, execution: nxt }, `已标记为 ${EXEC_LABEL[nxt]}（只是记号，不触发施工）`, t); },
  'handoff-finish': async t => { const done = t.dataset.result === 'done'; if (done && !(await confirmDialog({ title: '确认已完成这一步？', body: '确认后该决策标记为已落地（execution=released）。', confirmText: '已完成' }))) return; t.classList.add('busy'); try { await api('/api/decision/handoff-finish', { method: 'POST', body: JSON.stringify({ project: S().project, task_id: t.dataset.id, result: done ? 'done' : 'failed', by: 'user' }) }); toast(done ? '已记为落地' : '已退回 Agent 处理中', done ? 'ok' : 'warn'); await refresh(true); } catch (e) { notify.error(e); } finally { t.classList.remove('busy'); } },
  'spawn-child': async t => { const r0 = await confirmDialog({ title: '开关联子单', body: '失败且需要你决策时使用：不取消原施工，同一原因不会重复开单，每单最多 3 条。', confirmText: '开子单', input: { label: '失败原因与需要你决定的事', required: true, maxlength: 1000 } }); const reason = r0 && r0.ok ? r0.value : ''; if (reason.trim().length < 4) return; t.classList.add('busy'); try { const r = await api('/api/decision/child', { method: 'POST', body: JSON.stringify({ project: S().project, task_id: t.dataset.id, reason, kind: 'redecide', by: 'user' }) }); toast(r.dedup ? `未新建：${r.note}` : `已开子单 ${r.task_id}`, r.dedup ? 'warn' : 'ok'); await refresh(true); openDecision(r.task_id); } catch (e) { notify.error(e); } finally { t.classList.remove('busy'); } },
  'resolve-decision': async t => { t.classList.add('busy'); try { const r = await api('/api/decision/resolve', { method: 'POST', body: JSON.stringify({ project: S().project, task_id: t.dataset.id, by: 'user' }) }); toast(r.ok ? '已回到 Agent 处理中' : `仍有未定夺的子单：${(r.open || []).join('、')}`, r.ok ? 'ok' : 'warn'); await refresh(true); } catch (e) { notify.error(e); } finally { t.classList.remove('busy'); } },
  'implement-nudge': async t => { t.classList.add('busy'); try { const r = await api('/api/command', { method: 'POST', body: JSON.stringify({ kind: 'implement_nudge', payload: { project: S().project, task_id: t.dataset.id } }) }); toast(r.dedup ? `未新建：${r.note}` : r.note, r.dedup ? 'warn' : 'ok'); await refresh(true); } catch (e) { notify.error(e); } finally { t.classList.remove('busy'); } },
  // 第 6 步「看不懂」：排一条 brief_rewrite 指令给 Leader（带任务号 + 卡面这句话），卡片标「待重写」；已排过的不重复排
  'brief-confusing': async t => { t.classList.add('busy'); try { const r = await api('/api/decision/brief-rewrite', { method: 'POST', body: JSON.stringify({ project: t.dataset.project || S().project, task_id: t.dataset.id, shown: t.dataset.shown || '', by: 'user' }) }); toast(r.dedup ? `未重复发送：${r.note}` : r.note, r.dedup ? 'warn' : 'ok'); await refresh(true); if (F.drawer.kind === 'decision' && F.drawer.key === t.dataset.id) openDecision(t.dataset.id); } catch (e) { notify.error(e); } finally { t.classList.remove('busy'); } },
  'export-task-pack': async t => { try { const r = await api('/api/task-pack', { method: 'POST', body: JSON.stringify({ project: S().project, task_id: t.dataset.id }) }); const blob = new Blob([r.markdown], { type: 'text/markdown;charset=utf-8' }); const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = r.filename; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000); toast(`已导出任务包（${r.files.length} 个涉及文件）`, 'ok'); } catch (e) { notify.error(e); } },
  'adopt': async t => { const d = decisions().find(x => x.task_id === t.dataset.id); const r = await confirmDialog({ title: `采纳「${titleOf(d || { task_id: t.dataset.id })}」`, body: '合并稿的最终方案会写入项目既定决策，并归档原始回答。可以附上落地要求，Leader 会按它执行。', confirmText: '采纳', input: { label: '给 Leader 的落地要求（可选）', placeholder: '例如：先改设置页，再改派发页；不要动 arena 目录' } }); if (!r) return; closeDrawer(); sendCommand('adopt', { project: S().project, task_id: t.dataset.id, comment: r.value || '' }, '已采纳', t); },
  'reject': async t => { const d = decisions().find(x => x.task_id === t.dataset.id); const r = await confirmDialog({ title: `驳回「${titleOf(d || { task_id: t.dataset.id })}」`, body: '驳回后此轮合并稿作废。写明原因，Leader 会带着原因再发一轮。', confirmText: '驳回', danger: true, input: { label: '驳回原因（必填）', required: true, placeholder: '哪里不对、希望怎么改' } }); if (!r) return; closeDrawer(); sendCommand('reject', { project: S().project, task_id: t.dataset.id, comment: r.value || '' }, '已驳回', t); },
  'cancel-task-direct': t => sendCommand('cancel', { project: S().project, task_id: t.dataset.id }, '已取消', t),
  'cancel-task': async t => { const d = decisions().find(x => x.task_id === t.dataset.id); if (!(await confirmDialog({ title: `取消「${titleOf(d || { task_id: t.dataset.id })}」`, body: '取消后 Leader 不会再等待这轮对读的回答，已到的回答会归档。', confirmText: '取消任务', danger: true }))) return; sendCommand('cancel', { project: S().project, task_id: t.dataset.id }, '已取消', t); },
  'plan': t => { const p = project(); const item = p?.next?.[Number(t.dataset.index)]; if (item) sendCommand('plan', { project: S().project, item, index: Number(t.dataset.index) }, '下一步已交给 Leader', t); },
  'merge-section': async t => { const text = (F.mergeCache.get(t.dataset.path) || {}).text || ''; const sec = sectionFromMerge(text, t.dataset.kind) || '该段为空'; const host = t.closest('[data-merge-body]'); const on = toggleExtra(host, t.dataset.kind, `<div class="md-extra fade-up" data-kind="${attr(t.dataset.kind)}" style="margin-top:10px;padding-top:10px;border-top:1px dashed var(--line)"><div class="detail-label">${esc(t.textContent)}</div>${markdown(sec)}</div>`); $$('[data-action="merge-section"]', host).forEach(b => b.setAttribute('aria-pressed', String(on && b.dataset.kind === t.dataset.kind))); },
  'retry-merge': t => loadMerge(t.closest('details'), true),
  'save-bridge': async () => { const v = $('#bridgeUrl')?.value.trim(); if (!v) return toast('Bridge 地址不能为空', 'warn'); try { await api('/api/bridge', { method: 'POST', body: JSON.stringify({ url: v }) }); store.set({ bridge_url: v }); toast('Bridge 地址已保存', 'ok'); } catch (e) { notify.error(e); } },
  'preset-ws': t => { $('#setWorkspace').value = t.dataset.value; saveSettings({ workspace: t.dataset.value }, '工作区已保存'); },
  'save-workspace': () => saveSettings({ workspace: $('#setWorkspace').value.trim() }, '工作区已保存'),
  'toggle-run': async t => { if (t.checked && !(await confirmDialog({ title: '开启命令执行', body: '开启后 Agent 拥有等同宿主账户的权限，目录范围对它无效。', confirmText: '仍然开启', danger: true }))) { t.checked = false; return; } saveSettings({ run_enabled: t.checked }, t.checked ? '命令执行已开启' : '命令执行已关闭'); },
  'toggle-audit': t => saveSettings({ audit_enabled: t.checked }),
  'save-run': () => saveSettings({ run_timeout_max: clamp(Number($('#setTimeout').value) || 120, 5, 600), run_allow: $('#setAllow').value.split(/\r?\n/).map(x => x.trim()).filter(Boolean) }, '命令设置已保存'),
  'reset-allow': () => saveSettings({ run_allow: null }, '已恢复默认白名单'),
  'mcp-health': () => loadSettings().then(() => toast('已重新探测', 'ok')),
  'guard-restart': async t => { const svc = t.dataset.service || 'all'; if (svc === 'all' && !(await confirmDialog({ title: '一键拉起全部服务', body: '会重启 MCP 与工作台（插件随工作台一起重启），约 15 到 30 秒。', confirmText: '拉起' }))) return; t.classList.add('busy'); try { const r = await api('/api/guard/restart', { method: 'POST', body: JSON.stringify({ service: svc }) }); toast(r.note || '已下发', 'ok'); setTimeout(loadSettings, 20000); } catch (e) { notify.error(e); } finally { t.classList.remove('busy'); } },
  'guard-start': async t => { t.classList.add('busy'); try { await api('/api/guard/restart', { method: 'POST', body: JSON.stringify({ service: 'none' }) }); toast('守护已启动', 'ok'); setTimeout(loadSettings, 5000); } catch (e) { notify.error(e); } finally { t.classList.remove('busy'); } },
  'mcp-restart': async t => { if (!(await confirmDialog({ title: '重启 MCP 服务', body: '约 5 到 15 秒，期间 Agent 的工具调用会失败。', confirmText: '重启' }))) return; t.classList.add('busy'); try { const r = await api('/api/mcp/restart', { method: 'POST', body: '{}' }); toast(r.ok ? `MCP 已重启（PID ${r.after?.pid}）` : `重启后仍无法连接：${r.after?.error || r.launcher_stderr || '未知'}`, r.ok ? 'ok' : 'bad'); await loadSettings(); } catch (e) { notify.error(e); } finally { t.classList.remove('busy'); } },
  'peer-token-rotate': async t => { let peer = t.dataset.peer; if (peer === '__new__') { peer = $('#peerNewName').value.trim(); if (!/^[a-z0-9_-]+$/.test(peer)) return toast('Agent 名只能是小写字母、数字、下划线或连字符', 'warn'); } if (t.dataset.peer !== '__new__' && !(await confirmDialog({ title: `轮换 ${peer} 的 token`, body: '旧 token 立即失效，需要把新 token 贴回该 Agent 的会话。', confirmText: '轮换' }))) return; await saveSettings({ peer_token_rotate: peer }, `已为 ${peer} 签发新 token`); },
  'peer-token-revoke': async t => { if (!(await confirmDialog({ title: `吊销 ${t.dataset.peer} 的专属 token`, body: '吊销后该 Agent 回退到主 token。', confirmText: '吊销', danger: true }))) return; saveSettings({ peer_token_revoke: t.dataset.peer }, '已吊销'); },
  'peer-token-reveal': async t => { try { const r = await api(`/api/settings/peer_token?peer=${encodeURIComponent(t.dataset.peer)}`); await navigator.clipboard.writeText(r.token); toast(`${t.dataset.peer} 的 token 已复制到剪贴板（未在页面显示）`, 'ok'); } catch (e) { notify.error(e); } },
  'save-peer-roots': () => { const roots = {}; $$('.peer-root-input').forEach(i => { if (i.value.trim()) roots[i.dataset.peer] = i.value.trim(); }); saveSettings({ peer_roots: roots }, '根目录已保存'); }
};
const FORMS = {
  'agent-group': async form => {
    const fd = new FormData(form);
    const body = { id: form.dataset.id || '', name: String(fd.get('name') || '').trim(), label: String(fd.get('label') || '').trim(),
      match: String(fd.get('match') || ''), reviewer: fd.get('reviewer') === 'on', builder: fd.get('builder') === 'on' };
    try { await api('/api/agent_groups/save', { method: 'POST', body: JSON.stringify(body) }); toast('分组已保存', 'ok'); closeDrawer(); await refresh(true); } catch (e) { notify.error(e); }
  },
  composer: submitComposer,
  'decision-answer': async form => {
    // fb-486f8ac59c：就地提交——不开抽屉、不弹窗、不换页（smoke_decision_inline_486f.mjs 对本函数体做源码断言）。允许「只补充不选项」。
    const btn = $('button[type=submit]', form), picked = $('input[type=radio]:checked', form);
    const text = ($('textarea', form)?.value || '').trim();
    if (!picked && !text) return toast('请选一个选项，或写一句补充说明', 'warn');
    btn?.classList.add('busy');
    try {
      await api('/api/decision/answer', { method: 'POST', body: JSON.stringify({ project: form.dataset.project, task_id: form.dataset.task, item_id: form.dataset.item, choice: picked ? picked.value : '', text }) });
      toast('已存进工作台，Leader 确认收到后会标出来', 'ok');
      form.reset(); form.closest('details')?.removeAttribute('open');
      await refresh(true);
    } catch (e) { notify.error(e); }
    finally { btn?.classList.remove('busy'); }
  },
  dispatch: form => { const f = ensureComposer(); if (!f) return; f.question = $('[data-qd="question"]', form).value; f.mode = $('[data-qd="mode"]', form).value; return submitDispatch(f, $('#qdSubmit', form)); },
  plan: async form => { const next = String(new FormData(form).get('plan') || '').split(/\r?\n/).map(x => x.trim()).filter(Boolean); try { await api('/api/project/update', { method: 'POST', body: JSON.stringify({ slug: S().project, next }) }); closeDrawer(); toast('下一步已保存', 'ok'); refresh(true); } catch (e) { notify.error(e); } },
  teams: async form => { const teams = $$('[data-team-row]', form).map(r => ({ name: $('[data-team-field="name"]', r).value.trim(), peers: $('[data-team-field="peers"]', r).value.split(',').map(x => x.trim()).filter(Boolean), mode: $('[data-team-field="mode"]', r).value, note: $('[data-team-field="note"]', r).value.trim() })).filter(t => t.name); try { await api('/api/project/update', { method: 'POST', body: JSON.stringify({ slug: S().project, teams }) }); closeDrawer(); toast('Team 已保存', 'ok'); refresh(true); } catch (e) { notify.error(e); } },
  'agent-profile': async form => { const button = form.querySelector('[type=submit]'); button.disabled = true; try { await api('/api/profile', { method: 'POST', body: JSON.stringify({ peer: form.dataset.peer, ...profilePatch(form) }) }); await refresh(true); toast('全局 Agent 资料已保存', 'ok'); } catch (error) { notify.error(error); } finally { button.disabled = false; } },
  'agent-meta': async form => { const p = project(); const fd = new FormData(form); const agent_meta = { [form.dataset.peer]: { display_name: String(fd.get('display_name') || '').trim(), note: String(fd.get('note') || '').trim(), paused_in_project: fd.get('paused_in_project') === 'on' } }; try { await api('/api/project/update', { method: 'POST', body: JSON.stringify({ slug: p.slug, agent_meta }) }); closeDrawer(); toast('Agent 资料已保存', 'ok'); refresh(true); } catch (e) { notify.error(e); } },
  project: async form => { const edit = form.dataset.edit === '1'; const fd = new FormData(form); const payload = { name: String(fd.get('name') || '').trim(), description: String(fd.get('description') || '').trim(), peers: fd.getAll('peers'), mode: String(fd.get('mode') || 'independent'), context_files: String(fd.get('context_files') || '').split(/\r?\n/).map(x => x.trim()).filter(Boolean), default_team: String(fd.get('default_team') || '') }; if (!edit && !payload.name) return toast('项目名称不能为空', 'warn'); try { const r = edit ? await api('/api/project/update', { method: 'POST', body: JSON.stringify({ ...payload, slug: S().project }) }) : await api('/api/project', { method: 'POST', body: JSON.stringify(payload) }); closeDrawer(); toast(edit ? '项目已保存' : '项目已创建', 'ok'); await refresh(true); if (r.slug) store.set({ project: r.slug }); } catch (e) { notify.error(e); } },
  join: async () => {},
  join_legacy: async form => { const kind = form.dataset.kind, fd = new FormData(form), peer = String(fd.get('peer') || '').trim(), model = String(fd.get('model') || '').trim(); if (kind !== 'controller' && (!peer || !model)) return toast('Agent 名称和模型名称不能为空', 'warn'); try { const text = await textApi(`/api/prompt?${new URLSearchParams({ kind, ...(kind === 'controller' ? {} : { peer, model }) })}`); $('#promptResult').innerHTML = `<div class="field fade-up"><label for="promptText">接入提示词</label><textarea id="promptText" class="mono" style="min-height:300px;font-size:calc(12px * var(--fs-scale, 1))" readonly>${esc(text)}</textarea></div><button type="button" class="small" data-action="copy-prompt">${icon('copy')}复制提示词</button>`; } catch (e) { notify.error(e); } },
  'relay-order': async form => { const order = $('#relayOrder', form).value.split(/\n/).map(x => x.trim()).filter(Boolean); if (await commitRelayOrder(order)) closeDrawer(); },
  followup: async form => { const text = $('textarea', form).value.trim(); const peers = $$('input[name="fpeer"]:checked', form).map(x => x.value).filter(n => agentList().some(a => a.peer === n)); if (!text) return toast('请填写追问内容', 'warn'); if (!peers.length) return toast('至少选择一个 Agent', 'warn'); const btn = $('button[type=submit]', form); btn.classList.add('busy'); let attachments = []; try { attachments = await uploadPending(F.followup.attachments); } catch (e) { btn.classList.remove('busy'); return notify.error(e, { prefix: '图片上传失败：' }); } const ok = await sendCommand('followup', { project: S().project, task_id: F.followup.taskId, text, peers, mode: F.followup.mode, attachments }, '继续讨论已排队', btn); if (ok) { releaseAttachments(F.followup); closeDrawer(); } }
};

// 顶替序是服务端配置：整表提交带 rev，服务端不等返回 conflict 且不写；这里只回滚 UI（重新拉 state），不静默覆盖。order=null 表示恢复出厂序
async function commitRelayOrder(order, note = '顶替顺序已保存') {
  const pr = S().priority || {}, gs = new Set((S().agent_groups || []).map(g => g.id));
  const meta = order && order.map(id => (pr.relay_order_meta || []).find(m => m && m.id === id) || { id, kind: gs.has(id) ? 'group' : 'peer', source: 'ui', added_at: new Date().toISOString().slice(0, 19) });
  const body = order ? { relay_order: order, relay_order_meta: meta, rev: pr.rev ?? 0 } : { factory_reset: true, rev: pr.rev ?? 0 };
  let ok = false;
  try { await api('/api/priority', { method: 'POST', body: JSON.stringify(body) }); toast(note, 'ok'); ok = true; }
  catch (e) { /conflict/.test(e.message) ? toast(`顶替顺序刚被别处修改，已放弃本次提交并恢复服务端版本（${e.message}）`, 'bad', { level: 'normal' }) : notify.error(e, { prefix: '顶替顺序保存失败：' }); }
  await refresh(true);
  return ok;
}
// 写 priority.json 的统一出口：整表带 rev 做冲突检测（409 只提示并回滚，不覆盖别处改动）；
// 服务端可能对提交值做去重或裁剪，提交后逐字段比对，不一致要给出可见提示
async function commitPriority(patch, note = '调度已保存') {
  const pr = S().priority || {}, body = { ...patch, rev: pr.rev ?? 0 };
  let r;
  try { r = await api('/api/priority', { method: 'POST', body: JSON.stringify(body) }); }
  catch (e) {
    if (/conflict/.test(e.message)) { toast(`${note} 未提交：${e.message}`, 'warn'); await refresh(true); return false; }
    notify.error(e); return false;
  }
  store.set({ priority: r });
  // 提交值原样比对：服务端会去重（`list(dict.fromkeys(...))`）或裁剪，差异必须让用户看见，不能本地跟着去重再装作一致
  const norm = (k, v) => v === undefined ? null : k === 'agent_weight' ? Object.entries(v).sort().map(([a, b]) => `${a}=${b}`).join(',') : v;
  const changed = Object.keys(patch).filter(k => JSON.stringify(norm(k, r[k])) !== JSON.stringify(norm(k, patch[k])));
  if (changed.length) toast(`${note}，但服务端调整了 ${changed.join('、')}（重复项会被去掉），页面已按服务端版本显示`, 'warn');
  else toast(note, 'ok');
  await refresh();  // 不强制整页：priority 已 store.set 触发局部 morph，这里只做一次常规同步，避免全页 + 局部双重绘
  return true;
}
const commitTaskOrder = ids => commitPriority({ task_order: ids }, '任务顺序已保存');
// 清空权重 = 删键（不写空串占位）；权重目前只落 priority.json，Leader 调度尚未消费，提示语如实说明
const saveAgentWeight = (peer, weight) => { const w = { ...agentWeights() }; if (weight) w[peer] = weight; else delete w[peer]; return commitPriority({ agent_weight: w }, weight ? `${displayName(peer)} 权重已保存（仅记录，调度端尚未读取）` : `${displayName(peer)} 权重已清除`); };
async function moveTaskByKeyboard(handle, delta) {
  const row = handle.closest('.task'); const id = row?.dataset.key; if (!id) return;
  const visible = $$('.task').map(r => r.dataset.key).filter(Boolean);
  const next = moveInList(visible, id, delta); if (next.join() === visible.join()) return;
  const ok = await commitTaskOrder(mergeVisibleOrder(taskOrder(), next));
  if (ok) requestAnimationFrame(() => { $(`.task[data-key="${CSS.escape(id)}"] [data-task-drag]`)?.focus(); });
}

const pushQ = debounce(v => url.set({ q: v }, true), 400);  // e131：搜索词 400ms 防抖 + replace，后退不再逐字符
function bind() {
  // 拖动排序：Agent 页的分组区块与模型组页的卡片共用；FLIP 让让位过程有位移动画
  const DRAG = { sel: '', id: '', attr: '' };
  const flip = (container, mutate) => { const kids = [...container.children]; const first = new Map(kids.map(k => [k, k.getBoundingClientRect()])); mutate(); kids.forEach(k => { const a = first.get(k), b = k.getBoundingClientRect(); const dx = a.left - b.left, dy = a.top - b.top; if (!dx && !dy) return; k.style.transition = 'none'; k.style.transform = `translate(${dx}px,${dy}px)`; requestAnimationFrame(() => { k.style.transition = 'transform .22s cubic-bezier(.2,.8,.2,1)'; k.style.transform = ''; }); }); };
  document.addEventListener('dragstart', e => { const sec = e.target.closest?.('.group-section, .relay-item, .task'); if (!sec) return; DRAG.sel = sec.matches('.group-section') ? '.group-section' : sec.matches('.relay-item') ? '.relay-item' : '.task'; DRAG.attr = { '.group-section': 'group', '.relay-item': 'relayItem', '.task': 'key' }[DRAG.sel]; DRAG.id = sec.dataset[DRAG.attr]; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', DRAG.id); requestAnimationFrame(() => sec.classList.add('dragging')); });
  document.addEventListener('dragover', e => { if (!DRAG.id) return; const sec = e.target.closest?.(DRAG.sel); if (!sec || sec.dataset[DRAG.attr] === DRAG.id) { if (e.target.closest?.('#groupSections, #relayList, #taskList')) e.preventDefault(); return; } e.preventDefault(); const src = sec.parentElement.querySelector(`${DRAG.sel}.dragging`); if (!src || src === sec) return; const box = sec.getBoundingClientRect(); const horizontal = DRAG.sel === '[data-group-card]'; const before = horizontal ? (e.clientX < box.left + box.width / 2) : (e.clientY < box.top + box.height / 2); const nextSibling = before ? sec : sec.nextElementSibling; if (src.nextElementSibling === nextSibling) return; flip(sec.parentElement, () => sec.parentElement.insertBefore(src, nextSibling)); });
  document.addEventListener('drop', e => { if (DRAG.id) e.preventDefault(); });
  document.addEventListener('dragend', () => { if (!DRAG.id) return; const src = document.querySelector(`${DRAG.sel}.dragging`); if (src) src.classList.remove('dragging'); const ids = $$(DRAG.sel).map(x => x.dataset[DRAG.attr]).filter(x => x && x !== '_other'); const kind = DRAG.sel; DRAG.id = ''; if (kind === '.relay-item') return commitRelayOrder(ids, '顶替顺序已保存');  /* 服务端配置：走 /api/priority 带 rev */ if (kind === '.task') { DRAG.lastEnd = Date.now(); return commitTaskOrder(mergeVisibleOrder(taskOrder(), ids)); }  /* 只改可见子集在完整表里的位置，不可见 / 非 running 项保留 */  /* 任务处理顺序：同一份 priority.json，同样带 rev */ if (kind === '.group-section') saveGroupOrder(ids);  /* 本机 localStorage 偏好 */ DRAG.id = ''; });
  let downEl = null; document.addEventListener('pointerdown', e => { downEl = e.target; }, true);
  document.addEventListener('click', e => {
    // e131 / 018d：真实控件优先、容器 action 不穿透、手势同源、选中文字不跳转；checkbox 由 change 处理
    if (e.defaultPrevented || e.__busHandled) return;
    if (e.target.closest?.('[data-task-drag]')) return;  // 手柄只用于拖动 / 键盘调序，点它不展开行；不在捕获阶段吞全局 click
    const t = resolveAction(e.target, e.detail === 0 ? null : downEl, selectionAnchor(window)); if (!t) return;  // 键盘 / el.click()（detail=0）不看陈旧 pointerdown
    if (t.matches('input[type=color],input[type=range]')) return; // 取色器与滑块由 input（预览）+ change（落盘）处理（F1：9c53ead 解冲突时丢失，按 ef9ced7 原样补回）
    const fn = ACTIONS[t.dataset.action]; if (fn) { e.__busHandled = true; e.preventDefault(); fn(t, e); }
  });
  document.addEventListener('change', e => {
    const t = e.target;
    if (t.matches('[data-action]')) { const fn = ACTIONS[t.dataset.action]; if (fn) fn(t, e); return; }
    if (t.matches('#projectSelect')) { localStorage.setItem('bus.project', t.value); url.set({ p: t.value }); F.composer = null; F.composerDirty = false; viewTransition(() => store.set({ project: t.value })); return; }
    if (t.dataset.agentWeight) { saveAgentWeight(t.dataset.agentWeight, t.value); return; }
    if (t.dataset.qd) { const f = ensureComposer(); if (f) { f[t.dataset.qd] = t.value; F.composerDirty = true; } return; }
    const form = t.closest('#composerForm'); if (form) syncComposer(form);
    if (t.matches('input[name="fpeer"]')) t.closest('.fchip').classList.toggle('on', t.checked);
  });
  document.addEventListener('input', e => {
    const t = e.target;
    if (t.dataset.pref && (t.type === 'range' || t.type === 'color')) { previewPref(t.dataset.pref, t.value); if (t.id === 'prefAccent') { const h = document.getElementById('accentCheck'); if (h) h.innerHTML = accentHint(t.value); } return; }
    if (t.dataset.filter) { F.filters[t.dataset.filter] = t.value; pushQ(t.value); renderView(S()); return; }
    if (t.dataset.qd) { const f = ensureComposer(); if (f) { f[t.dataset.qd] = t.value; F.composerDirty = true; } return; }
    const form = t.closest('#composerForm'); if (form) syncComposer(form);
  });
  document.addEventListener('click', e => { if (e.__busHandled || e.defaultPrevented) return; const b = e.target.closest('[data-filter-status]'); if (b) { e.__busHandled = true; F.filters.decisionStatus = b.dataset.filterStatus; url.set({ status: b.dataset.filterStatus }); renderView(S()); } });
  // 「只看卡住」：概览行动条「卡住的施工」带 on 直达（捕获阶段先设好筛选，再由它自己的 data-action="view" 渲染）；进行中页的筛选片带 toggle
  document.addEventListener('click', e => { const b = e.target.closest('[data-filter-stuck]'); if (!b) return; const mode = b.dataset.filterStuck; toggleStuck(mode); const apply = () => url.set({ stuck: F.filters.runningStuck ? '1' : '' }, true); if (mode === 'on') setTimeout(apply, 0); else apply(); /* on=行动条直达：等 data-action="view" 切完视图再写，stuck 不挂在概览的历史项上（fable2 0c0f）；不用微任务——真实点击时它在两个监听之间就执行 */ if (mode === 'toggle') { e.__busHandled = true; renderView(S()); } }, true);
  document.addEventListener('submit', e => { const form = e.target.closest('form[data-form]'); if (!form) return; e.preventDefault(); FORMS[form.dataset.form]?.(form); });
  document.addEventListener('keydown', e => {
    const t = e.target;
    if (S().view === 'history' && historyKeydown(e)) return;  // 任务历史列表：上下键移焦点、Enter 打开详情
    if (t.matches?.('[data-task-drag]') && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) { e.preventDefault(); e.stopPropagation(); moveTaskByKeyboard(t, e.key === 'ArrowUp' ? -1 : 1); return; }  // af57 R2：手柄真实可用的键盘调序
    if (t.matches('[data-action="toggle-task"]') && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); ACTIONS['toggle-task'](t); return; }
    if (t.isContentEditable) {
      // 品牌长度用 keydown 拦：input 事件里裁剪会把光标跳到末尾；有选区属于替换，不拦
      if (t.dataset.inline === 'brand' && !e.ctrlKey && !e.metaKey && !e.altKey && e.key.length === 1) {
        const max = t.dataset.field === 'subtitle' ? PREF.subtitleMax : PREF.titleMax;
        const sel = window.getSelection();
        if (sel && sel.isCollapsed && Array.from(t.textContent || '').length >= max) { e.preventDefault(); toast(`最多 ${max} 个字`, 'warn', { ms: 1200 }); return; }
      }
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); t.blur(); }
      if (e.key === 'Escape') { t.textContent = t.dataset.orig ?? t.textContent; if (t.dataset.inline === 'brand') renderBrand(); t.blur(); }
      return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); cmdkOpen() ? closeCmdk() : openCmdk(); return; }
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && t.closest('#composerForm, #qdQuestion, form[data-form="dispatch"]')) { e.preventDefault(); const qd = t.closest('form[data-form="dispatch"]'); qd ? FORMS.dispatch(qd) : submitComposer($('#composerForm')); return; }
    if (e.key === 'Escape') { if (cmdkOpen()) closeCmdk(); else if (drawerOpen()) { releaseAttachments(F.followup); closeDrawer(); } return; }
    const dv = digitView(e, NAV); if (dv) { if (isTypingTarget(t)) return; e.preventDefault(); switchView(dv); return; }  // Alt+数字 切视图
    if (!shortcutAllowed(e, { drawerOpen: drawerOpen(), cmdkOpen: cmdkOpen(), dialogOpen: $('#dialog').classList.contains('show') })) return;
    if (e.key === '/') { e.preventDefault(); $('[data-filter]')?.focus() || openCmdk(); return; }
    if (e.key === 'n' && !e.shiftKey) { e.preventDefault(); openDispatch(); return; }
    if (e.key === 'r' && !e.ctrlKey && !e.metaKey) { refresh(true); return; }
    if (e.key === 'g') { const once = ev => { clearTimeout(once._t); const map = { o: 'overview', r: 'running', d: 'decisions', a: 'artifacts', c: 'compose', p: 'peers', s: 'settings' }; if (map[ev.key] && shortcutAllowed(ev)) switchView(map[ev.key]); document.removeEventListener('keydown', once); }; once._t = setTimeout(() => document.removeEventListener('keydown', once), 800); document.addEventListener('keydown', once, { once: true }); return; }
    // 裸数字键不再切视图（e131）：只保留上面的 Alt+数字
  });
  document.addEventListener('focusin', e => {
    const t = e.target;
    if (!t.isContentEditable) return;
    // 副标题存原文、只在渲染时大写；进入编辑态先换回原文，否则用户会在大写文本上改并把大写存回存储
    if (t.dataset.inline === 'brand' && t.dataset.field === 'subtitle') { const b = Prefs.load().brand; if (b.subtitleUpper) t.textContent = b.subtitle; }
    t.dataset.orig = t.textContent;
  });
  document.addEventListener('focusout', e => { const t = e.target; if (t.isContentEditable && t.dataset.inline && t.textContent.trim() !== (t.dataset.orig || '').trim()) saveInline(t); });
  document.addEventListener('toggle', e => { const d = e.target; if (d.matches?.('details[data-merge]') && d.open) loadMerge(d); }, true);
  document.addEventListener('paste', e => {
    // 品牌图标面板打开时，粘贴落点是图标本身（与派发图片共用同一条 paste 管道）
    if (e.target.closest?.('#drawer') && $('[data-brand-drop]')) { const f = [...(e.clipboardData?.files || [])][0]; if (f) { e.preventDefault(); setMarkFromFile(f).then(() => closeDrawer()).catch(err => toast(err.message, 'bad')); return; } }
    if (!e.target.closest('#composerForm, [data-followup-panel], form[data-form="dispatch"]')) return; const files = [...(e.clipboardData?.files || [])]; if (files.length) { e.preventDefault(); addAttachments(files, attachTarget(e.target)); }
  });
  document.addEventListener('dragover', e => { const z = e.target.closest('[data-dropzone],[data-dropzone-hint]'); if (z) { e.preventDefault(); z.classList.add('over'); } });
  document.addEventListener('dragleave', e => e.target.closest?.('[data-dropzone],[data-dropzone-hint]')?.classList.remove('over'));
  document.addEventListener('drop', e => { const z = e.target.closest('[data-dropzone],[data-dropzone-hint]'); if (!z) return; e.preventDefault(); z.classList.remove('over'); addAttachments([...(e.dataTransfer?.files || [])], attachTarget(z)); });
  $('#backdrop').addEventListener('click', () => { closeCmdk(); releaseAttachments(F.followup); closeDrawer(); });
  window.addEventListener('beforeunload', e => { if (F.composerDirty) { e.preventDefault(); e.returnValue = ''; } });
  window.addEventListener('popstate', () => {
    if (cmdkOpen()) closeCmdk();
    if (closeDrawer._skipPop) { closeDrawer._skipPop = false; return; }
    if (drawerOpen()) { closeDrawer(true); return; }
    const v = url.get('view', 'overview');
    if (v === 'decisions') { F.filters.decisions = url.get('q', ''); F.filters.decisionStatus = url.get('status', ''); }
    if (v === 'running') F.filters.runningStuck = url.get('stuck') === '1';
    if (v === 'history') readUrlFilters(histState());  // 历史视图的筛选 / 页码在 URL 上，后退要还原
    if (VIEWS[v] && v !== S().view) { localStorage.setItem('bus.view', v); store.set({ view: v }); }
    else { if (!VIEWS[v]) { toast(`链接里的视图「${v}」不存在，已回到当前页`, 'warn', { ms: 2000 }); url.set({ view: S().view }, true); } renderView(S()); }
    openEntityFromUrl();
  });
  bindCmdk();
}
function buildCommands() {
  const items = NAV.map(([k, ic, label], i) => ({ group: '跳转', label: `前往 ${label}`, icon: ic, kbd: String(i + 1), run: () => switchView(k) }));
  items.push({ group: '动作', label: '派发任务', icon: 'send', keywords: 'dispatch compose n', run: () => openDispatch() }, { group: '动作', label: '打开完整发起页', icon: 'send', keywords: 'dispatch compose', run: () => switchView('compose') }, { group: '动作', label: '新建项目', icon: 'plus', run: () => openProjectForm(false) }, { group: '动作', label: '接入新 Agent', icon: 'users', run: () => openJoin('peer') }, { group: '动作', label: '刷新数据', icon: 'refresh', kbd: 'R', run: () => refresh(true) }, { group: '动作', label: '编辑下一步计划', icon: 'edit', run: () => openPlanEditor() });
  HISTORY_NAV.forEach(([k, ic, label]) => items.push({ group: '跳转', label: `前往 ${label}`, icon: ic, keywords: 'history 历史', run: () => switchView(k) }));
  (F.pluginPages || []).forEach(x => items.push({ group: '插件', label: `打开 ${x.name}`, icon: 'box', keywords: 'plugin 插件 ' + x.name, run: () => { location.href = x.href; } }));
  S().projects.forEach(p => items.push({ group: '项目', label: `切换到 ${p.name || p.slug}`, icon: 'grid', run: () => { store.set({ project: p.slug }); localStorage.setItem('bus.project', p.slug); } }));
  activeDecisions().slice(0, 30).forEach(d => items.push({ group: '任务', label: titleOf(d), sub: STATUS[d.status], icon: 'play', keywords: d.task_id, run: () => (['dispatched', 'collecting'].includes(d.status) ? openTask(d.task_id) : openDecision(d.task_id)) }));
  agentList().forEach(a => items.push({ group: 'Agent', label: displayName(a.peer), sub: a.online ? '在线' : '离线', icon: 'users', keywords: a.model, run: () => openAgentEditor(a.peer) }));
  registerCommands(items);
}

// ---------- 启动 ----------
store.on(['view', 'project'], s => { renderShell(s); renderView(s); buildCommands(); });
store.on(['peers', 'projects', 'commands', 'controller', 'connected', 'live'], s => { renderShell(s); renderView(s); });
store.on(['time'], s => { renderShell(s); tickRelTime(); });  // a667：心跳只刷壳（时钟 / 连接态）+ 相对时间局部更新，不再每几秒整页 morph
// 调度表与租约：只 morph 看板与任务列表，不整页重绘，避免打断用户正在写的备注
store.on(['priority', 'lease'], s => renderScoped(s));
// 交接通知单独订阅：只 morph #leadEvents 节点，不触发整页重画，避免打断正在写的备注
store.on(['lead_events_open', 'features', 'lease'], s => renderLeadEvents(s));
store.on('settings', s => { if (s.view === 'settings') renderView(s); });
// 扩展层摘要：hub_badge.js 拉 /api/ext/summary 后广播；核心只听广播、不直接依赖扩展接口（ARCHITECTURE 原则 1）
window.addEventListener('wb:ext-summary', e => { const d = e.detail; const ext = d ? { inbox: d.intake ? d.intake.pending || 0 : null, feedback: d.feedback ? d.feedback.open || 0 : null } : null;  /* 按项降级：哪个扩展缺席就只隐藏哪项 */ const cur = S().ext; if (JSON.stringify(cur) !== JSON.stringify(ext)) store.set({ ext }); });
store.on('ext', s => { if (s.view === 'overview') renderView(s); });
store.on('projects', () => buildCommands());
store.on('history', s => { const last = s.history[s.history.length - 1], prev = s.history[s.history.length - 2]; if (last && prev && last.pending > prev.pending) { const c = $('[data-count-key="overview"]'); if (c) { c.classList.add('bump'); setTimeout(() => c.classList.remove('bump'), 400); } } });
// 018d：实体深层链接（?d= 决策 / ?t= 进行中任务 / ?a= Agent，配合 p= 项目）。刷新等待 state 到位后解析；未知实体只读报错；不递归、不重复 history
function openEntityFromUrl() {
  const ent = parseEntity(new URLSearchParams(location.search));
  if (!ent) return false;
  if (ent.invalid) { toast(`链接里的 ${ent.kind} 标识不合法，已忽略`, 'warn'); url.set(entityPatch(), true); return false; }
  if (F.drawer && F.drawer.key === ent.id && drawerOpen()) return true;
  if (!drawerOpen()) url.set(entityPatch(), true);  // 先把当前历史项洗成干净 URL，抽屉 push 后再标实体参数：后退 = 关抽屉且不残留参数
  if (ent.kind === 'agent') { if (!agentList().some(a => a.peer === ent.id)) { toast(`链接指向的 Agent「${ent.id}」不存在（只读）`, 'warn'); return false; } openAgentEditor(ent.id); return true; }
  const p = S().projects.find(x => x.slug === S().project) || null;
  const d = decisions(p).find(x => x.task_id === ent.id);
  if (!d) { if (S().projects.length) toast(`当前项目里没有「${ent.id}」这条${ent.kind === 'task' ? '任务' : '决策'}（只读，不跳转）`, 'warn'); return false; }
  if (ent.kind === 'task' || ['dispatched', 'collecting'].includes(d.status)) openTask(ent.id); else openDecision(ent.id);
  return true;
}
(function init() {
  // 外观与品牌先落到 DOM，再进首次渲染；系统配色变化只在 mode=system 时接管
  const boot = Prefs.load();
  Prefs.apply(boot); Prefs.applyBrand(boot);
  try { matchMedia('(prefers-color-scheme:dark)').addEventListener('change', () => { const p = Prefs.load(); if (p.theme.mode === 'system') Prefs.apply(p); }); } catch (_) { /* 旧环境无 addEventListener */ }
  const hist = JSON.parse(localStorage.getItem('bus.history') || '[]'); store.set({ history: hist });
  F.filters.decisionStatus = url.get('status', ''); F.filters.artifacts = url.get('scope') === 'all' ? 'all' : 'project';
  F.filters.runningStuck = url.get('stuck') === '1';  // 「只看卡住」进 URL：刷新 / 后退不丢（与状态筛选一致，fable2 9a21）
  const q = url.get('q', ''); if (q) F.filters[S().view] = q;
  Object.assign(HIST_HOOKS, { rerender: () => renderView(S()), openDecision, openCommand, setProject: slug => { localStorage.setItem('bus.project', slug); url.set({ p: slug }); store.set({ project: slug }); } }); readUrlFilters(histState());
  if (!VIEWS[S().view]) store.set({ view: 'overview' });
  if (url.get('view', '') !== S().view) url.set({ view: S().view }, true);
  bind(); renderShell(S()); renderView(S()); startHistoryView(S().view); loadPluginPages();
  refresh(true).then(() => { connectLive(); setTimeout(() => document.body.classList.add('settled'), 900); openEntityFromUrl(); });
  setInterval(tickCountdown, 1000);
})();

Object.assign(ACTIONS, {
  'open-redispatch': t => openRedispatch(t.dataset.id),
  'nudge-merge': async t => { t.classList.add('busy'); try { const r = await api('/api/command', { method: 'POST', body: JSON.stringify({ kind: 'nudge', payload: { project: S().project, task_id: t.dataset.id } }) }); store.set({ nudges: { ...(S().nudges || {}), [t.dataset.id]: r } }); toast(`已催 ×${r.count}，Leader 下次心跳处理`, 'ok'); } catch (e) { notify.error(e); } finally { t.classList.remove('busy'); } },
  // ADR 0012：自动分配模式。切到「真派」前确认一次
  'allocator-mode': async t => {
    const mode = t.dataset.mode; if (!mode || (S().allocator || {}).mode === mode) return;
    if (mode === 'on' && !(await confirmDialog({ title: '打开自动分配（真派）', confirmText: '打开',
      body: '守护每圈会把空闲 Agent 派到最缺人的一段：待审的派 Reviewer，被离线者占着的施工 / 回答换人。每圈最多 5 单、每人 1 单、同一名额最多换人 2 次。随时可以切回「预演」。' }))) return;
    try { await api('/api/allocator/mode', { method: 'POST', body: JSON.stringify({ mode }) }); toast(mode === 'on' ? '已打开自动分配' : mode === 'preview' ? '已切到预演：只显示会怎么派' : '已关闭自动分配', 'ok'); await refresh(true); }
    catch (e) { notify.error(e); }
  },
  // 概览标签页与分页（2026-10-02）
  'ov-tab': t => { setOvTab(t.dataset.tab, t.dataset.group || 'main'); renderView(S()); },
  page: t => { F.pages = F.pages || {}; F.pages[t.dataset.id] = Math.max(0, (F.pages[t.dataset.id] || 0) + Number(t.dataset.dir || 0)); renderView(S()); },
  // Agent 页：点胶囊 → 抽屉里看全部详情与操作；点组名 → 编辑分组（工作台自己的分组）
  'agent-open': t => { const a = agentList().find(x => x.peer === t.dataset.peer); if (!a) return; openDrawer({ title: displayName(a.peer), subtitle: `${a.model || '模型未登记'} · ${a.peer}`, body: agentDetail(a.peer) }); },
  'group-edit': t => openDrawer({ title: '编辑分组', subtitle: t.dataset.group, body: groupForm(t.dataset.group) }),
  'group-new': () => openDrawer({ title: '新建分组', subtitle: '工作台自己的分组', body: groupForm('') }),
  'group-merge': async t => { const form = t.closest('form'); const dst = form?.querySelector('[name="merge_into"]')?.value; if (!dst) return;
    if (!(await confirmDialog({ title: '合并分组', body: `把这一组并入「${esc((S().agent_groups || []).find(g => g.id === dst)?.name || dst)}」？这一组的模型匹配与手动归进来的 Agent 都会并过去，这一组删除。`, confirmText: '合并' }))) return;
    try { await api('/api/agent_groups/merge', { method: 'POST', body: JSON.stringify({ src: t.dataset.group, dst }) }); toast('已合并', 'ok'); closeDrawer(); await refresh(true); } catch (e) { notify.error(e); } },
  'group-delete': async t => { if (!(await confirmDialog({ title: '删除分组', body: '组里的 Agent 会变成「未归组」，Agent 本身不删。', confirmText: '删除', danger: true }))) return;
    try { await api('/api/agent_groups/delete', { method: 'POST', body: JSON.stringify({ id: t.dataset.group }) }); toast('已删除', 'ok'); closeDrawer(); await refresh(true); } catch (e) { notify.error(e); } },
  // 点名移除 Agent：先预演列出要挪走的单 → 确认 → 挪单 + 归档登记 + 吊销令牌；有挪不走的单再问一次「仍要移除」
  'remove-peer': async t => {
    const peer = t.dataset.peer; if (!peer) return;
    const post = body => api('/api/peers/remove', { method: 'POST', body: JSON.stringify({ peer, ...body }) });
    t.classList.add('busy');
    try {
      const pre = await post({ dry_run: true });
      const n = (pre.tasks || []).length;
      if (!(await confirmDialog({ title: `移除 ${displayName(peer)}`, confirmText: '移除', danger: true,
        body: `${n ? `它手上还有 ${n} 单，会先挪给别人（普通单换人、审查单重派、施工单给新人开 worktree）。<br>` : '它手上没有未完成的单。<br>'}`
          + `登记会归档后删除，接入令牌会吊销——${pre.online ? '<b>它现在在线</b>，吊销后它的会话会断开、' : ''}以后要用得重新接入。` }))) return;
      let r = await post({});
      if (!r.ok) {
        const list = (r.stuck || []).map(s => `${esc(s.task_id)}：${esc(s.reason)}`).join('<br>');
        if (!(await confirmDialog({ title: `有 ${(r.stuck || []).length} 单挪不走`, confirmText: '仍要移除', danger: true,
          body: `${list}<br><br>仍要移除的话，这些单留在原处，守护会在 30 分钟后再找人接。` }))) { await refresh(true); return; }
        r = await post({ force: true });
      }
      toast(`已移除 ${displayName(peer)}${(r.moved || []).length ? `，挪走 ${r.moved.length} 单` : ''}${r.revoked ? '，令牌已吊销' : ''}`, 'ok', { ms: 4000 });
      await refresh(true);
    } catch (e) { notify.error(e); } finally { t.classList.remove('busy'); }
  },
  // 一键改派：先预演列出会动哪些单，确认后改派；结果逐条说清去了谁、哪些没动及原因
  'reassign-held': async t => {
    const peer = t.dataset.peer || '';
    t.classList.add('busy');
    try {
      const pre = await api('/api/reassign/held', { method: 'POST', body: JSON.stringify({ peer, dry_run: true }) });
      const items = pre.items || [];
      if (!items.length) { toast('没有被占着不动的单', 'ok'); return; }
      const who = [...new Set(items.map(i => displayName(i.peer)))].join('、');
      const builds = items.filter(i => i.mode === 'build').length;
      if (!(await confirmDialog({ title: `改派 ${items.length} 单`, confirmText: '改派',
        body: `来自 ${esc(who)}。普通任务换给同组在线 Agent（没有就跨组），审查单换在线 Reviewer 重派。${builds ? `<br>其中 ${builds} 张施工单：新施工者按同一基线开自己的 worktree，原施工者的 worktree 原样保留。` : ''}` }))) return;
      const r = await api('/api/reassign/held', { method: 'POST', body: JSON.stringify({ peer }) });
      const moved = r.moved || [], skip = r.skipped || [], err = r.errors || [];
      toast([moved.length ? `已改派 ${moved.length} 单` : '', skip.length ? `${skip.length} 单没动` : '', err.length ? `${err.length} 单失败：${err[0].reason}` : ''].filter(Boolean).join('；') || '没有改派',
        err.length && !moved.length ? 'warn' : 'ok', { ms: err.length || skip.length ? 6000 : 3000 });
      await refresh(true);
    } catch (e) { notify.error(e); } finally { t.classList.remove('busy'); }
  },
  // 催审 = 立刻派给在线 Reviewer（/api/review/now），不再只给 Leader 留言等下一圈心跳
  'nudge-review': t => reviewNow(t, t.dataset.id),
  'review-all': t => reviewNow(t, ''),
  // 待落地批量处理（bus_land）：先说清会发生什么再动手；逐条结果汇总
  'land-bulk': async t => {
    const { cat, act, n, label } = t.dataset;
    const what = { rework: `把这 ${n} 条重新派给正在等任务的施工 Agent（从当前 main 开新工作副本，带上一版提交和退回原因；旧施工单关闭，工作副本保留）`,
      drop: cat === 'empty' ? `把这 ${n} 条标记为已完成（提交里没有改动，按 main 已包含处理；可随时改回）` : `这 ${n} 条不再落地（移到已完成、标记无需实施；施工分支保留，可随时改回）`,
      rereview: `作废这 ${n} 条的旧审查结论，按当前 main 重新派审查` }[act];
    if (!(await confirmDialog({ title: `「${label}」· ${t.textContent.trim()}`, body: what, confirmText: '确定', danger: act === 'drop' }))) return;
    t.classList.add('busy');
    try {
      const r = await api('/api/land/bulk', { method: 'POST', body: JSON.stringify({ cat, action: act }) });
      const ok = (r.done || []).length, bad = r.failed || [];
      toast(`已处理 ${ok} 条${bad.length ? `；${bad.length} 条没处理：${bad[0].error}` : ''}${r.remaining ? `；这一类还剩 ${r.remaining} 条` : ''}`, bad.length ? 'warn' : 'ok', { ms: bad.length ? 8000 : 3000 });
      await refresh(true);
    } catch (e) { notify.error(e); } finally { t.classList.remove('busy'); }
  },
  // 清理已过期：全员过期且一份没交的任务逐个取消归档；逐条结果汇总，失败的留在列表里
  'clear-expired': async t => {
    const dead = runningDecisions().filter(d => stallInfo(d).terminal && !progressOf(d).answered);
    if (!dead.length) return toast('没有已过期的任务', 'ok');
    const names = dead.slice(0, 6).map(d => `· ${esc(titleOf(d))}`).join('<br>') + (dead.length > 6 ? `<br>…等 ${dead.length} 个` : '');
    if (!(await confirmDialog({ title: `清理 ${dead.length} 个已过期任务`, body: `这些任务的 Agent 都已过期、一份回答都没交：<br>${names}<br><br>取消后从「进行中」移除并归档，在「任务历史」里还能看到。`, confirmText: `取消 ${dead.length} 个`, danger: true }))) return;
    t.classList.add('busy'); let ok = 0; const bad = [];
    for (const d of dead) {
      try { await api('/api/command', { method: 'POST', body: JSON.stringify({ kind: 'cancel', payload: { project: S().project, task_id: d.task_id } }) }); ok++; }
      catch (e) { bad.push(`${titleOf(d)}：${e.message}`); }
    }
    t.classList.remove('busy');
    toast(`已清理 ${ok} 个${bad.length ? `；${bad.length} 个没清掉：${bad[0]}` : ''}`, bad.length ? 'warn' : 'ok', { ms: bad.length ? 7000 : 3000 });
    await refresh(true);
  },
});
Object.assign(FORMS, {
  redispatch: async form => {
    const taskId = form.dataset.task; const fd = new FormData(form);
    const peers = fd.getAll('rpeer'); if (!peers.length) return toast('至少选择一个 Agent', 'warn');
    const payload = { project: S().project, task_id: taskId, peers, mode: String(fd.get('mode') || ''), ttl_min: clamp(Number(fd.get('ttl_min')) || 30, 1, 1440), min_answers: Math.min(Math.max(1, Number(fd.get('min_answers')) || peers.length), peers.length), only_pending: fd.get('only_pending') === 'on', carry_answers: fd.get('carry_answers') === 'on' };
    const btn = $('button[type=submit]', form); btn.classList.add('busy'); hideTask(taskId);
    try { const r = await api('/api/command', { method: 'POST', body: JSON.stringify({ kind: 'redispatch', payload }) }); toast(`已重派为 ${r.task_id}，参与：${(r.peers || []).map(displayName).join('、')}，法定 ${r.min_answers ?? payload.min_answers}`, 'ok'); F.expanded.delete(taskId); closeDrawer(); await refresh(true); }
    catch (e) { unhideTask(taskId); notify.error(e); }
    finally { btn.classList.remove('busy'); }
  },
});

// 外观与品牌动作并入全站动作表（ACTIONS 在本文件中段声明，只能在声明后合并）
Object.assign(ACTIONS, PREF_ACTIONS);

