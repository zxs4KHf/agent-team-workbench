// app-sync.js：从 app.js 拆出（ADR 0015，按页面拆模块；scripts/split_module.py 生成，函数体未改）
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

import { renderScoped } from './main.js';

let lastKey = '';

// ---------- 数据同步 ----------
// 状态指纹分四组：base 是整页依赖，order / leader 只涉及调度表与租约，events 只涉及交接通知。仅后三组变化时走局部 morph，见 refresh / renderScoped
// stateKey 必须自包含（回归测试 tests/smoke_relay_card_f14f.mjs 会把它单独取出来执行）
function stateKey(s) {
  const pr = s.priority || {}, l = s.lease || {}, w = pr.agent_weight || {};
  const base = JSON.stringify([s.peers.map(p => [p.peer, p.status, p.online, p.queued, p.task_id, p.listening, p.benched]), s.projects.map(p => [p.slug, p.updated, p.next, p.notes?.length, p.teams, p.peers, p.decisions.map(d => [d.task_id, d.status, d.updated, d.execution, d.summary, d.progress && d.progress.answered, d.progress && d.progress.states, d.implementation && d.implementation.label, d.implementation && d.implementation.blocked, d.bucket || '', d.lineage && d.lineage.display_title || '', d.lineage && d.lineage.round || 0, d.handoff && d.handoff.state || '', d.needs_decision ? 1 : 0, (d.children || []).length])]), s.commands.map(c => [c.id, c.status]), s.controller && [s.controller.online, s.controller.status], s.nudges && Object.entries(s.nudges).map(([k, v]) => [k, v && v.count, v && v.at]), (s.relay_resolution?.items || []).map(i => [i.item, i.pick, i.online_n]), s.relay_resolution?.pick || '',
    (s.peers || []).map(p => p.label || ''), s.profiles || {}, Object.entries(s.pending_labels || {}), s.features ? [s.features.lead_label !== false] : '',
    // 多活 Leader：有人接入 / 离开 / 开始处理指令 / 出现孤儿指令都要重画（顶栏人数与 Leader 面板）
    (s.leaders || []).map(r => [r.session, r.label, (r.claimed || []).length]), s.leader_queue ? [s.leader_queue.pending, s.leader_queue.claimed, s.leader_queue.orphaned] : '',
    s.allocator ? [s.allocator.mode, s.allocator.last_plan && s.allocator.last_plan.time] : '']);
  const events = JSON.stringify([(s.lead_events_open || []).map(e => [e.id, e.count, e.acked, e.pending_handback ? 1 : 0, e.unstable ? 1 : 0, e.epoch || '', e.from || '', e.to || '', e.reason || '', e.at || '']), s.features ? s.features.lead_events !== false : true]);
  const order = JSON.stringify([pr.rev || '', (pr.task_order || []).join(','), Object.keys(w).sort().map(k => `${k}:${w[k]}`).join(',')]);
  const leader = JSON.stringify([l.holder || '', l.epoch || '', l.expires || '']);
  return `${base}\u0001${order}\u0001${leader}\u0001${events}`;
}
function stateGroups(s) { const [base, order, leader, events] = stateKey(s).split('\u0001'); return { base, order, leader, events }; }
let lastGroups = null;
function pushHistory(s) {
  const p = s.projects.find(x => x.slug === s.project) || defaultProject(s.projects);
  const h = { pending: p ? countPending(p) : 0, running: p ? runningDecisions(p).length : 0, done: p ? decisions(p).filter(d => ['adopted', 'archived', 'merged'].includes(d.status)).length : 0, online: onlineAgents(s).length, t: Date.now() };
  const hist = [...(s.history || []), h].slice(-48);
  try { localStorage.setItem('bus.history', JSON.stringify(hist)); } catch (_) { /* 存储满 */ }
  return hist;
}
export async function refresh(force = false) {
  try {
    const data = await api('/api/state');
    const s = S();
    refresh._fails = 0; const patch = { ...data, connected: true, lastRefresh: Date.now() };
    // 只在当前项目不存在时才换：首屏 s.projects 还是空的，旧写法会把地址栏 ?p= 和上次的选择一起冲掉
    if (!data.projects.some(p => p.slug === s.project)) patch.project = (defaultProject(data.projects) || {}).slug || '';
    const next = { ...s, ...patch }, g = stateGroups(next), key = stateKey(next);
    if (key !== lastKey || force) {
      // 仅调度表 / 租约变化：只落这两键，让 store.on(['priority','lease']) 做局部 morph，整页不动
      const minorOnly = !force && !!lastGroups && lastGroups.base === g.base && lastGroups.events === g.events && (g.order !== lastGroups.order || g.leader !== lastGroups.leader);
      // 仅交接通知变化：只落通知两键，让 store.on(['lead_events_open','features','lease']) 局部 morph 通知卡，#content 不重绘
      const eventsOnly = !force && !!lastGroups && lastGroups.base === g.base && lastGroups.order === g.order && lastGroups.leader === g.leader && g.events !== lastGroups.events;
      lastKey = key; lastGroups = g;
      if (eventsOnly) { store.set({ lead_events_open: next.lead_events_open, features: next.features, connected: true, lastRefresh: Date.now(), time: data.time }); return; }
      if (minorOnly) { store.set({ priority: next.priority, lease: next.lease, connected: true, lastRefresh: Date.now(), time: data.time }); return; }  // af57 R2：局部刷新同样是一次成功刷新，不能假离线 / 旧 time 配新 expires
      patch.history = pushHistory(next); store.set(patch);
    }
    else store.set({ connected: true, lastRefresh: Date.now(), time: data.time });
  } catch (e) { refresh._fails = (refresh._fails || 0) + 1; if (refresh._fails >= 2) store.set({ connected: false }); return; }
  refresh._fails = 0;
}
export function connectLive() {
  if (!window.EventSource) return startPoll(4000);
  let es; let backoff = 1000;
  const open = () => {
    es = new EventSource('/api/events');
    es.onopen = () => { backoff = 1000; store.set({ live: 'sse' }); };
    es.onmessage = () => refresh();
    es.addEventListener('ping', () => store.set({ lastRefresh: Date.now() }));
    es.onerror = () => { es.close(); store.set({ live: 'poll' }); setTimeout(open, backoff); backoff = Math.min(backoff * 2, 30000); };
  };
  open();
  startPoll(15000);
}
function startPoll(ms) { setInterval(() => { if (!document.hidden && (S().live !== 'sse' || Date.now() - S().lastRefresh > ms * 2)) refresh(); }, ms); }

