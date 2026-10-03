// page-running.js：从 views.js 拆出（ADR 0015，按页面拆模块；scripts/split_module.py 生成，函数体未改）
// 视图：概览、进行中、决策、成果
import { esc, attr, icon, sparkline, ring, fmtDate, relTime, minutesSince } from './core.js';
import { S, F, project, decisions, activeDecisions, isUnbound, UNBOUND_HINT, runningDecisions, doneDecisions, agentList, onlineAgents, agentByName, projectPeers, displayName, titleOf, displayTitle, bucketOf, BUCKETS, threadsOf, childrenOf, artifactsOf, deliveredDecisions, weekDelivered, progressOf, stallInfo, countPending, STATUS, STATUS_TONE, EXEC_LABEL, PEER_STATE, ARTIFACT_KIND, orderedTasks, taskTier } from './model.js';

import { capped, chip, empty, head } from './views.js';

export function renderRunning(p) {
  const data = runningData(p), { all, list, stalled } = data, stuckOnly = !!F.filters.runningStuck, m = metaOf(data);  // 只算一遍（fable2 0c0f）
  // 片上的数 = 符合当前关键词的卡住数，点开看到的就是这么多（fable2 9a21：原来用全部卡住数，关键词无交集时片上 1、列表空）
  const stuckChip = m.showChip ? `<button type="button" class="fchip ${stuckOnly ? 'on' : ''}" data-filter-stuck="toggle" aria-pressed="${stuckOnly ? 'true' : 'false'}"${m.tip ? ` data-tip="${attr(m.tip)}"` : ''}>只看卡住<span class="n" id="stuckN">${m.stuckN}</span></button>` : '';
  // 待审的施工 / 审查单一键全部派审（直接派给在线 Reviewer，不经 Leader）
  // 已过期且一份没交的不算「待催审」（没东西可审），它们归「清理已过期」
  const reviewN = all.filter(d => ['build', 'review'].includes(d.mode || (d.progress && d.progress.meta && d.progress.meta.mode)) && !(stallInfo(d).terminal && !progressOf(d).answered)).length;
  const reviewAll = reviewN ? `<button type="button" class="small primary" data-action="review-all">${icon('send')}全部催审 ${reviewN}</button>` : '';
  // 一键清掉「全员过期、一份没交」的任务（2026-10-02 用户：「没办法把已过期的给清掉」）
  const deadN = all.filter(d => stallInfo(d).terminal && !progressOf(d).answered).length;
  const clearDead = deadN ? `<button type="button" class="small danger" data-action="clear-expired">${icon('trash')}清理已过期 ${deadN}</button>` : '';
  return `<div class="view">${head('任务队列', '进行中', '每个任务一行，卡住原因写在行上，点开看每个 Agent 的状态。拖动行首 ⋮⋮ 调整处理顺序，前三位标 P0 / P1 / P2。', clearDead + reviewAll)}
    ${stalled.length ? `<div class="banner" style="background:var(--surface-2)">${icon('info')}<span>${stalled.length} 个任务有名额长期未回：守护会自动改派或摘掉；也可以直接点「现在合并」或「取消」，点完即从这里移除</span></div>` : ''}
    <section class="card fit-panel" data-key="fit-running"><div class="filters fit-head"><div class="search">${icon('search')}<input type="search" placeholder="搜索标题或任务号…" value="${attr(F.filters.running)}" data-filter="running" aria-label="搜索进行中任务" autocomplete="off"></div>${stuckChip}<span class="result-count" id="runningCount">${m.count}</span></div>
    <div class="fit-body">${list.length ? `<div class="stack" id="taskList">${taskListHtml(p, list)}</div>` : empty(all.length ? (stuckOnly && !F.filters.running.trim() ? '没有卡住的任务' : '没有匹配的任务') : '没有进行中的任务', all.length ? (stuckOnly ? '再点一次「只看卡住」取消筛选' : '换个关键词') : '从「发起」创建一轮独立作答、redteam 或 relay')}</div></section>
  </div>`;
}
// 单独导出任务行：调度顺序或租约变化时只 morph 这个容器（app.js renderScoped）
// 调用方已算好列表就传进来，免得再过一遍 stallInfo（fable2 470d）
export function taskListHtml(p = project(), list = runningList(p)) {
  return capped('running', list, taskRow, 10);   // ADR 0015：长列表一律分页，分页条贴面板底
}
// 进行中页的筛选：关键词 + 「只看卡住」（stallInfo.terminal，与概览行动条「卡住的施工」同一口径）
function kwMatch(d, q = F.filters.running.trim().toLowerCase()) { return !q || titleOf(d).toLowerCase().includes(q) || d.task_id.includes(q); }
// 不做跨渲染缓存：「卡住」按当前时间算（stallInfo），数据没变也会随时间变化
function runningData(p) {
  const all = orderedTasks(runningDecisions(p)), stalled = all.filter(d => stallInfo(d).terminal), stuckSet = new Set(stalled);
  const stuckOnly = !!F.filters.runningStuck, list = all.filter(d => kwMatch(d) && (!stuckOnly || stuckSet.has(d)));
  return { all, list, stalled, stuckN: stalled.filter(d => kwMatch(d)).length };
}
function runningList(p) { return runningData(p).list; }
// 列表外的计数与筛选片：局部 morph（renderScoped）也要同步，否则某项解卡后片上数字 / N / M 停在旧值（fable2 9a21）
// rows = 当前要画的任务行（数组）；listN 等其余字段是计数（原名 list，与数组同名绊倒过人，fable2 814d 改名）。renderScoped 用 rows 局部重画，不再重算一遍
export function runningMeta(p = project()) { const data = runningData(p); return { ...metaOf(data), rows: data.list }; }
function metaOf({ all, list, stalled, stuckN }) {
  return { listN: list.length, all: all.length, stalled: stalled.length, stuckN,
           showChip: stalled.length > 0 || !!F.filters.runningStuck, count: `${list.length} / ${all.length}`,
           tip: stuckN !== stalled.length ? `共 ${stalled.length} 个卡住，符合关键词的 ${stuckN} 个` : '' };
}
// 「只看卡住」开关：on=行动条直达（总是打开），off=关，其它=切换。返回新状态（app.js 据此写 URL 并重画）
export function toggleStuck(mode) {
  F.filters.runningStuck = mode === 'on' ? true : mode === 'off' ? false : !F.filters.runningStuck;
  return F.filters.runningStuck;
}
// 按钮组：固定顺序，当前可用者 primary 置首、不可用隐藏（11e7 + d0a7）
function taskActions(d, st, pr) {
  const id = attr(d.task_id), mode = d.mode || pr.meta.mode || 'independent';
  const quorumMet = pr.answered >= Math.min(pr.quorum, Math.max(1, pr.expected));
  const lost = !Object.keys(pr.states).length && !pr.meta.id;
  const expired = st.terminal && !pr.answered;
  const nudge = (S().nudges || {})[d.task_id];
  const nudged = nudge ? `<span class="chip plain" title="已催 Leader ${nudge.count} 次">已催 ${esc(String(nudge.at || '').slice(11, 16))} ×${nudge.count}</span>` : '';
  const b = (action, label, cls = 'small', extra = '') => `<button type="button" class="${cls}" data-action="${action}" data-id="${id}" ${extra}>${label}</button>`;
  const items = [];
  if (d.status === 'ready_for_merge') {
    items.push(b('nudge-merge', '催 Leader 合并', 'small primary'), nudged);
  } else if (mode === 'relay') {
    if (pr.complete && pr.answered >= 1) items.push(b('merge-now', '直接合并（接力已完整）', 'small primary'), b('open-task', '查看接力进度'));
    else items.push(b('open-task', '查看接力进度', 'small primary'));
    if (expired || lost) items.push(b('open-redispatch', '条件重派…'), b('cancel-task', '取消', 'small danger'));
  } else if (mode === 'build' && !pr.answered && (expired || lost || st.terminal)) {
    // 施工人过期 / 没交：没有可审的东西，催审没用。给「改派施工」（只派给正在等任务的施工 Agent）和「取消」
    const stuckPeer = Object.entries(pr.states).find(([, s]) => s !== 'answered')?.[0] || (d.peers || [])[0] || '';
    if (stuckPeer && !lost) items.push(`<button type="button" class="small primary" data-action="requeue-agent" data-id="${id}" data-peer="${attr(stuckPeer)}">改派施工</button>`);
    items.push(b('cancel-task', '取消并归档', 'small danger'));
  } else if (mode === 'build' || mode === 'review') {
    items.push(b('nudge-review', '催审', 'small primary'));
    if (lost) items.push(b('cancel-task', '取消并归档', 'small danger'));
  } else if (lost) {
    items.push(b('cancel-task', '取消并归档', 'small primary danger'));
  } else if (pr.answered >= 1 && (quorumMet || st.terminal)) {
    items.push(b('merge-now', `直接合并已到的 ${pr.answered} 份`, 'small primary', `data-tip="${pr.complete ? '' : `未回的 ${Math.max(0, pr.expected - pr.answered)} 个名额会被摘掉`}"`));
    if (st.terminal) items.push(b('open-redispatch', '条件重派…'));
  } else if (expired) {
    items.push(b('open-redispatch', '条件重派…', 'small primary'), b('cancel-task', '取消', 'small danger'));
  }
  return items.filter(Boolean).join('');
}
function taskRow(d) {
  const pr = progressOf(d), open = F.expanded.has(d.task_id), st = stallInfo(d);
  const pct = pr.answered / Math.max(1, pr.expected);
  const actions = taskActions(d, st, pr);
  const tier = taskTier(d.task_id);
  const handle = `<span class="drag-handle task-handle" draggable="true" data-task-drag role="button" tabindex="0" aria-keyshortcuts="ArrowUp ArrowDown" aria-label="拖动或按上下方向键调整「${attr(titleOf(d))}」的处理顺序" title="拖动 / 聚焦后按 ↑↓ 调整处理顺序">⋮⋮</span>`;
  return `<article class="task ${open ? 'open' : ''} ${st.terminal ? 'terminal' : ''}" data-key="${attr(d.task_id)}">
    <div class="task-main" data-action="toggle-task" data-id="${attr(d.task_id)}" role="button" tabindex="0" aria-expanded="${open}">
      <div class="min0"><h3 class="truncate">${handle}${tier ? `<span class="chip ${tier === 'P0' ? 'violet' : tier === 'P1' ? 'info' : 'plain'} tier ${tier.toLowerCase()}" data-tier="${tier}">${tier}</span>` : ''}${esc(displayTitle(d))}</h3><p class="truncate"><span class="mono">${esc(d.task_id)}</span> · ${esc(d.mode || 'independent')} · <time data-reltime="${attr(pr.meta.created || d.created || '')}">${esc(relTime(pr.meta.created || d.created))}</time> · <span class="${st.tone === 'bad' ? 'faint' : 'muted'}" style="color:var(--${st.tone === 'bad' ? 'danger' : st.tone === 'warn' ? 'warn' : 'muted'})">${esc(st.text)}</span></p></div>
      <div class="mid"><div class="pbar ${pr.answered ? '' : 'indeterminate'}"><i style="transform:scaleX(${pct.toFixed(3)})"></i></div><div class="plabel"><span>${pr.answered}/${pr.expected} 已回答</span><span>法定 ${pr.quorum}</span></div></div>
      <div class="mid">${pr.complete ? '<span class="chip ok">可合并</span>' : chip(d.status)}</div>
      <div class="row">${actions}<button type="button" class="ghost icon" data-action="toggle-task" data-id="${attr(d.task_id)}" aria-label="${open ? '收起' : '展开'}" style="transform:rotate(${open ? 90 : 0}deg);transition:transform var(--m-base) var(--ease-out)">${icon('chevron')}</button></div>
    </div>
    ${open ? `<div class="task-body fade-up"><div><div class="detail-label">任务问题</div><div class="q">${esc(pr.meta.question || d.title || '任务原文尚未返回')}</div><div class="row wrap" style="margin-top:12px"><button type="button" class="small" data-action="open-task" data-id="${attr(d.task_id)}">${icon('eye')}查看回答</button><button type="button" class="small" data-action="open-redispatch" data-id="${attr(d.task_id)}">条件重派…</button><button type="button" class="small" data-action="supersede" data-id="${attr(d.task_id)}">标记被取代</button><button type="button" class="small danger" data-action="cancel-task" data-id="${attr(d.task_id)}">取消</button></div></div>
      <div><div class="detail-label">Agent 状态</div><div class="peers-state">${(pr.meta.peers || d.peers || []).map(n => agentState(d, n)).join('') || '<span class="muted">尚未登记 Agent</span>'}</div></div></div>` : ''}
  </article>`;
}
function agentState(d, name) {
  // 按这个人接手 / 进它收件箱的时间算（后端 task_progress.ages）；没有时才退回任务创建时间。以前一律按任务创建时间，改派后新人一接手就显示「已领 1500 分钟」
  const pr = progressOf(d), state = pr.states[name] || 'missing', sec = (pr.ages || {})[name];
  const age = Math.max(1, sec != null ? Math.floor(sec / 60) : minutesSince(pr.meta.created || d.created));
  const offline = (pr.meta.offline_at_dispatch || []).includes(name);
  let label = PEER_STATE[state] || state;
  if (state === 'working') label = `已领取 ${age} 分钟`; if (state === 'stalled') label = `已领取 ${age} 分钟未回`;
  if (state === 'queued' && sec != null) label = `排队 ${age} 分钟`;
  const ag = agentByName(name); const retry = state === 'stalled' || state === 'expired' || state === 'missing' || (state === 'queued' && !ag.online) ? `<button type="button" class="ghost small" data-action="requeue-agent" data-id="${attr(d.task_id)}" data-peer="${attr(name)}">${state === 'expired' || state === 'missing' || !ag.online ? '改派' : '退回改派'}</button>` : '';
  return `<div class="pstate ${state}"><span class="dot"></span><span class="who truncate">${esc(displayName(name))}</span><span class="st">${esc(label)}${offline ? ' · 派发时离线' : ''}</span>${retry}</div>`;
}

