// app-drawers.js：从 app.js 拆出（ADR 0015，按页面拆模块；scripts/split_module.py 生成，函数体未改）
import * as Prefs from './prefs.js';
import { profileEditor, profilePatch } from './profiles_ui.js';
import { notify } from './notify.js';
// 应用控制器：数据同步（SSE 优先，轮询兜底）、路由、事件委托、抽屉内容
import { $, $$, esc, attr, icon, morph, animateNumber, viewTransition, url, api, textApi, markdown, fmtDate, relTime, clamp } from './core.js';
import { toast, openDrawer, closeDrawer, drawerOpen, confirmDialog, registerCommands, openCmdk, closeCmdk, cmdkOpen, bindCmdk, mountVirtualList, teamChips } from './components.js';
import { store, S, F, project, decisions, activeDecisions, runningDecisions, agentList, onlineAgents, dispatchableAgents, undispatchableReason, teamPeersOf, pickStats, pickStatsText, dispatchEstimate, DYNAMIC_TEAM, PROJECT_TEAM, agentByName, displayName, agentMeta, titleOf, displayTitle, parentOf, childrenOf, BUCKETS, artifactsOf, effectiveTeams, ensureComposer, defaultComposerPeers, countPending, winPath, progressOf, NAV, STATUS, PEER_STATE, EXEC_LABEL, looksLikeImage, taskOrder, agentWeights, WEIGHT_OPTIONS, secondsLeft, fmtLeft, defaultProject, stallInfo } from './model.js';
import { resolveAction, selectionAnchor, shortcutAllowed, isTypingTarget, digitView, debounce, toggleExtra, mergeBodyStale, parseEntity, entityPatch, mergeVisibleOrder, moveInList } from './interact.js';
import { externalTaskInfo } from './page-running.js';
import { renderOverview, renderRunning, renderDecisions, renderArtifacts, chip, implChip, implActions, taskListHtml, runningMeta, toggleStuck, briefOf, briefFeedback, setOvTab } from './views.js';
import { modeLabel, renderCompose, renderPeers, renderSettings, groupOrder, saveGroupOrder, groupOfModel, leaderBoard, relayCardHtml, accentHint, groupOfAgent, agentDetail, groupForm } from './views2.js';
import { HISTORY_NAV, HISTORY_ACTIONS, HOOKS as HIST_HOOKS, renderHistory, historyKeydown, startHistoryView, histState, readUrlFilters } from './history.js';

import { LEAD_REASON, followupPanel, sectionFromMerge } from './main.js';

export function openLeadDetail(id) {
  const e = (S().lead_events_open || []).find(x => x.id === id); if (!e) return;
  const lease = S().lease || {};
  // 当前合并权威取 lease 实时真值，不能由事件台账推断——否则旧事件会把当前权威显示错
  const nowHolder = lease.holder ? `${esc(displayName(lease.holder))}（${esc(lease.holder)}，epoch ${esc(String(lease.epoch ?? '?'))}）` : '无人持有租约';
  openDrawer({ title: 'Leader 交接', subtitle: `${esc(LEAD_REASON[e.reason] || e.reason)} · ${esc(fmtDate(e.at))}`, body: `<div class="stack">
    <div class="field"><label>交接</label><div>${esc(e.from ? displayName(e.from) : '无人')} → ${esc(e.to ? displayName(e.to) : '无人')}</div></div>
    <div class="field"><label>真实 peer</label><div class="mono">${esc(e.from || '-')} → ${esc(e.to || '-')}</div></div>
    <div class="field"><label>原因 / epoch / 次数</label><div>${esc(LEAD_REASON[e.reason] || e.reason)} · epoch ${esc(String(e.epoch))} · ×${esc(String(e.count || 1))}</div></div>
    <div class="field"><label>首次 / 最近</label><div>${esc(fmtDate(e.first_at || e.at))} → ${esc(fmtDate(e.at))}</div></div>
    <div class="field"><label>当前合并权威（实时）</label><div>${nowHolder}</div></div>
    <div class="field"><label>交接提示词（已脱敏，不含 token）</label><pre class="pre">${esc(e.prompt || '（无）')}</pre>
      <button type="button" class="small" data-action="copy-text" data-text="${attr(e.prompt || '')}">${icon('copy')}复制</button></div>
    <div class="row"><button type="button" class="primary" data-action="lead-ack" data-id="${attr(e.id)}">知道了，关闭这条</button></div>
  </div>` });
}
// ---------- 合并稿懒加载 ----------
export async function loadMerge(details, force = false) {
  const body = $('[data-merge-body]', details); const path = details.dataset.merge;
  if (!body || (body.dataset.loaded && !force)) return;
  try {
    const cached = F.mergeCache.get(path);
    const text = !force && cached && Date.now() - cached.at < 30000 ? cached.text : await textApi(`/api/file?path=${encodeURIComponent(path)}`);
    F.mergeCache.set(path, { text, at: Date.now() });
    const section = sectionFromMerge(text, 'final') || text;
    body.innerHTML = markdown(section) + `<div class="row wrap" style="margin-top:8px">${['consensus', 'difference', 'evidence'].map(k => `<button type="button" class="small ghost" data-action="merge-section" data-path="${attr(path)}" data-kind="${k}">${{ consensus: '共识', difference: '分歧', evidence: '验证依据' }[k]}</button>`).join('')}<button type="button" class="small ghost" data-action="open-file" data-path="${attr(path)}">${icon('external')}完整合并稿</button></div>`;
    body.dataset.loaded = '1'; body.dataset.path = path; body.dataset.ver = details.dataset.ver || '';
  } catch (e) {
    // 404 = 合并稿文件从来没写出来（只登记了路径）。不是网络错误，重试没用：直接给「看各 Agent 原始回答」
    const tid = details.dataset.task || F.drawer?.key || '';
    body.innerHTML = /\b404\b/.test(e.message)
      ? `<div class="banner warn">这份合并稿的文件没有写出来（Leader 只登记了路径）。可以先看各 Agent 的原始回答。${tid ? ` <button type="button" class="small" data-action="open-task" data-id="${attr(tid)}">看原始回答</button>` : ''}</div>`
      : `<div class="banner danger">读取失败：${esc(e.message)} <button type="button" class="small" data-action="retry-merge">重试</button></div>`;
  }
}
// a667：正文容器 data-keep 不参与 morph；合并路径 / 决策版本变化时解除旧缓存并重载（只在展开时重新请求）
export function reconcileMerges(root) {
  $$('details[data-merge]', root).forEach(d => {
    const body = $('[data-merge-body]', d);
    if (!mergeBodyStale(d, body)) return;
    delete body.dataset.loaded; body.innerHTML = '<div class="sk" style="width:60%"></div><div class="sk" style="margin-top:8px"></div>';
    F.mergeCache.delete(body.dataset.path); delete body.dataset.path; delete body.dataset.ver;
    if (d.open) loadMerge(d, true);
  });
}
// ---------- 抽屉内容 ----------
export async function openTask(taskId) {
  const d = decisions().find(x => x.task_id === taskId) || { task_id: taskId };
  F.drawer = { kind: 'task', key: taskId };
  openDrawer({ title: titleOf(d), subtitle: taskId, kind: 'task', body: `<div class="stack"><div class="sk" style="width:50%"></div><div class="sk"></div><div class="sk" style="width:80%"></div></div>` });
  markEntityUrl('task', taskId);
  try {
    const data = await api(`/api/task?id=${encodeURIComponent(taskId)}`);
    if (F.drawer.key !== taskId) return;
    const meta = data.progress?.meta || {}, answers = data.answers || {}, states = data.progress?.states || {};
    const external = externalTaskInfo(d, meta);
    const winner = meta.race_winner ? displayName(meta.race_winner) : '';
    const source = external ? `<div class="row wrap">来源：${esc(external.owner === 'level-design' ? 'Level Design 插件' : external.owner)}${external.team ? `<span>· Team：${esc(external.team)}</span>` : ''}<span>· ${esc(modeLabel(d.mode || meta.mode || 'independent'))}</span>${winner ? `<span>· ${esc(winner)} 抢得</span>` : ''}${external.requestId ? `<span class="mono">· 请求 ${esc(external.requestId)}</span>` : ''}</div>` : '';
    const names = [...new Set([...Object.keys(answers), ...(meta.peers || [])])];
    F.filters.answersTab = names.find(n => answers[n]) || '';
    const tabs = names.map(n => `<button type="button" class="${F.filters.answersTab === n ? 'active' : ''}" data-answer-tab="${attr(n)}" ${answers[n] ? '' : 'disabled'}>${esc(displayName(n))}${answers[n] ? '' : ` <span class="faint">${esc(PEER_STATE[states[n]] || '等待')}</span>`}</button>`).join('');
    $('#drawerBody').innerHTML = `<div class="detail-label">任务问题</div><div class="q" style="font-size:calc(13px * var(--fs-scale, 1));background:var(--surface-2);padding:10px 12px;border-radius:8px;white-space:pre-wrap">${esc(meta.question || d.title || '未返回任务原文')}</div>
      ${source}<div class="row wrap" style="margin:12px 0;font-size:calc(12px * var(--fs-scale, 1));color:var(--muted)">${chip(d.status || 'dispatched')}<span>${esc(external ? (d.mode || meta.mode || '') : (meta.mode || d.mode || ''))}</span>${d.tier ? `<span>·</span><span data-tier="${attr(d.tier)}" data-tip="任务档位：按派给几个 Agent 自动记录（1 个 = 快速单做），用来观察实际习惯">${d.tier === 'fast' ? '快速单做' : '多模型对读'}</span>` : ''}<span>·</span><span>${esc(fmtDate(meta.created || d.created))}</span><span>·</span><span>${Object.keys(answers).length}/${names.length} 已回答</span></div>
      <div class="detail-label">Agent 回答</div><div class="answer-tabs">${tabs || '<span class="muted">尚未登记 Agent</span>'}</div><div id="answerPane" class="md"></div>`;
    $('#drawerFoot').innerHTML = `<button type="button" class="ghost" data-action="close-drawer">关闭</button>${external ? (external.link ? `<a class="link" href="${attr(external.link)}">返回来源工作台</a>` : '') : `${d.status === 'merged' ? `<button type="button" class="primary" data-action="adopt" data-id="${attr(taskId)}">${icon('check')}采纳</button>` : ''}<button type="button" class="ghost" data-action="followup" data-id="${attr(taskId)}">继续讨论</button>`}`;
    $('#drawerFoot').hidden = false;
    const showAnswer = name => {
      const pane = $('#answerPane'); if (!pane) return;
      const text = answers[name] || '';
      $$('[data-answer-tab]').forEach(b => b.classList.toggle('active', b.dataset.answerTab === name));
      if (text.length > 6000) {
        // 超长回答：按段落切块进虚拟列表（Pretext 预算行高）
        pane.innerHTML = `<div class="muted" style="font-size:calc(12px * var(--fs-scale, 1));margin-bottom:6px">回答较长（${text.length} 字），按段落虚拟滚动</div><div class="vlist" style="max-height:56vh"></div>`;
        const items = text.split(/\n{2,}/).map((t, i) => ({ key: `${name}-${i}`, text: t }));
        mountVirtualList($('.vlist', pane), items, { prefix: (it, i) => `¶${i + 1}` });
      } else pane.innerHTML = markdown(text) || '<div class="empty">尚无内容</div>';
    };
    $('.answer-tabs').addEventListener('click', e => { const b = e.target.closest('[data-answer-tab]'); if (b && !b.disabled) showAnswer(b.dataset.answerTab); });
    if (F.filters.answersTab) showAnswer(F.filters.answersTab); else $('#answerPane').innerHTML = '<div class="empty"><strong>还没有回答</strong><span>Agent 领取任务后回答会出现在这里</span></div>';
  } catch (e) { if (F.drawer.key === taskId) $('#drawerBody').innerHTML = `<div class="banner danger">读取失败：${esc(e.message)} <button type="button" class="small" data-action="open-task" data-id="${attr(taskId)}">重试</button></div>`; }
}
function lineagePanel(d) {
  // 903f：谱系（主题 / 轮次 / 动作、父子链）与分页归属；旧记录只显示「未归类」，不猜
  const lin = d.lineage || {}, par = parentOf(d), kids = childrenOf(d), h = d.handoff || {};
  const link = x => `<button type="button" class="link" data-action="open-decision" data-id="${attr(x.task_id)}">${esc(displayTitle(x))}</button>`;
  const bucket = d.bucket ? `<span class="chip plain">${esc(BUCKETS[d.bucket] || d.bucket)}</span>` : '';
  const handoff = h.state ? `<div class="faint" style="font-size:calc(12px * var(--fs-scale, 1))">人工交接 · ${esc(h.state)}：${esc(h.user_step || '')}${h.reason ? ` · 原因：${esc(h.reason)}` : ''}${h.verify ? ` · 验证：${esc(h.verify)}` : ''}</div>` : '';
  const actions = d.status === 'adopted' ? `<span class="row wrap" style="gap:6px;margin-top:4px"><button type="button" class="small ghost" data-action="spawn-child" data-id="${attr(d.task_id)}" data-tip="失败且需要你决策时开关联子单；不取消原施工">开子单（需决策）</button>${d.needs_decision ? `<button type="button" class="small ghost" data-action="resolve-decision" data-id="${attr(d.task_id)}">子单已定夺，回到 Agent 处理</button>` : ''}</span>` : '';
  return `<div class="field"><label>归类</label><div class="row wrap" style="gap:6px">${lin.has_lineage ? `<span class="chip info plain">〔${esc(lin.topic || '未命名主题')}〕${lin.round ? ` · R${lin.round}` : ''}${lin.kind_label ? ` · ${esc(lin.kind_label)}` : ''}</span>` : '<span class="chip plain">未归类（旧单）</span>'}${bucket}<span class="faint mono" style="font-size:calc(11px * var(--fs-scale, 1))">root ${esc(lin.root_task || d.task_id)}</span></div>
    ${par ? `<div class="faint" style="font-size:calc(12px * var(--fs-scale, 1));margin-top:4px">上一轮：${link(par)}</div>` : ''}${kids.length ? `<div class="faint" style="font-size:calc(12px * var(--fs-scale, 1));margin-top:4px">子单：${kids.map(link).join('、')}</div>` : ''}${handoff}${actions}</div>`;
}
export function openDecision(taskId, discuss = false) {
  const d = decisions().find(x => x.task_id === taskId); if (!d) return toast('找不到这条决策', 'bad', { level: 'normal' });
  F.drawer = { kind: 'decision', key: taskId };
  const exec = d.execution || 'pending';
  const body = `<div class="field"><label>决策标题</label><div class="inline-edit" contenteditable="true" data-inline="decision" data-task="${attr(taskId)}" data-field="title" data-keep data-keep-id="dtitle:${attr(taskId)}">${esc(titleOf(d))}</div></div>
    <div class="field"><label>简介</label><div class="q">${esc(briefOf(d))}</div><span class="help">卡面上唯一给你看的一句话；Leader 没写时按类型显示兜底句</span>${briefFeedback(d, (project() || {}).slug)}</div>
    <details class="field" data-keep data-keep-id="dsum:${attr(d.task_id)}"><summary class="muted" style="cursor:pointer">决策摘要（给 Leader 的流水账）</summary><div class="inline-edit" contenteditable="true" data-inline="decision" data-task="${attr(taskId)}" data-field="summary" data-placeholder="合并稿尚未回写摘要" data-keep data-keep-id="dsummary:${attr(taskId)}">${esc(d.summary || '')}</div><span class="help">点击文字编辑，失焦自动保存，保存后标记「已由用户修订」</span>${d.user_revised ? '<span class="chip info plain">已由用户修订</span>' : ''}</details>
    ${d.user_comment ? `<div class="field"><label>${d.status === 'rejected' ? '驳回原因' : '用户说明'}</label><div class="q">${esc(d.user_comment)}</div></div>` : ''}
    ${lineagePanel(d)}
    <div class="row wrap" style="margin-bottom:14px">${chip(d.status)}<button type="button" class="chip plain ${exec === 'released' ? 'ok' : exec === 'partial' ? 'warn' : ''}" data-action="set-execution" data-id="${attr(d.task_id)}" data-tip="手动标记落地状态，只是记号，不会触发施工">标记：${esc(EXEC_LABEL[exec])}</button>${d.implementation ? `<span class="row wrap" style="gap:6px">${implChip(d)}${implActions(d)}</span>` : ''}<span class="faint mono" style="font-size:calc(11px * var(--fs-scale, 1))">${esc(fmtDate(d.updated || d.created))}</span></div>
    ${(d.risks || []).length ? `<div class="field"><label>风险</label><ul style="margin:0;padding-left:18px;font-size:calc(13px * var(--fs-scale, 1));color:var(--warn)">${d.risks.map(r => `<li>${esc(r)}</li>`).join('')}</ul></div>` : ''}
    ${artifactsOf(d).length ? `<div class="field"><label>交付物</label><div class="stack" style="gap:4px">${artifactsOf(d).map(a => `<button type="button" class="link mono truncate" style="text-align:left;font-size:calc(12px * var(--fs-scale, 1))" data-action="open-file" data-path="${attr(a.path)}">${esc(a.path)}</button>`).join('')}</div></div>` : ''}
    ${d.merge_path ? `<details data-merge="${attr(d.merge_path)}" data-ver="${attr(d.updated || '')}" open style="border:1px solid var(--line);border-radius:8px;background:var(--surface-2)"><summary style="cursor:pointer;padding:8px 10px;font-size:calc(12px * var(--fs-scale, 1));color:var(--muted)">合并稿 · 最终方案</summary><div class="md" data-merge-body data-keep data-keep-id="mb:${attr(d.task_id)}" style="padding:0 12px 12px;font-size:calc(13px * var(--fs-scale, 1))"><div class="sk" style="width:60%"></div><div class="sk" style="margin-top:8px"></div></div></details>` : '<div class="empty">Leader 尚未生成合并稿</div>'}
    <div class="field" style="margin-top:14px"><label>历史</label><div class="timeline">${(d.history || []).slice().reverse().map(h => `<div class="tl ${['adopted', 'archived'].includes(h.status) ? 'ok' : ['cancelled', 'rejected'].includes(h.status) ? 'bad' : 'info'}"><time>${esc(fmtDate(h.time))}</time>${esc(STATUS[h.status] || h.status)}${h.by ? ` · ${esc(h.by)}` : ''}</div>`).join('') || '<span class="muted">无</span>'}</div></div>
    ${followupPanel(d, discuss)}`;
  const reason = d.status === 'merged' ? '' : d.status === 'ready_for_merge' ? '待 Leader 核验合并后开放采纳' : d.status === 'adopted' ? '已采纳' : d.status === 'archived' ? '已归档' : d.status === 'cancelled' ? '已取消' : '待核验后开放';
  const foot = `${d.status === 'merged' ? `<button type="button" class="primary" data-action="adopt" data-id="${attr(taskId)}">${icon('check')}采纳</button><button type="button" class="danger" data-action="reject" data-id="${attr(taskId)}">驳回</button>` : `<span class="chip warn plain">${esc(reason)}</span>`}<button type="button" class="ghost" data-action="toggle-discussion">${discuss ? '收起讨论' : '继续讨论'}</button><button type="button" class="ghost" data-action="open-task" data-id="${attr(taskId)}">原始回答</button>`;
  openDrawer({ title: displayTitle(d), subtitle: `${STATUS[d.status] || d.status} · ${taskId}`, body, foot, kind: 'decision' });
  markEntityUrl('decision', taskId);
  const det = $('#drawer details[data-merge]'); if (det) loadMerge(det);
}
export function openCommand(id) {
  const cmd = (S().commands || []).find(c => c.id === id) || (S().recent_done || []).find(c => c.id === id);
  if (!cmd) return openDrawer({ title: '指令详情', subtitle: id, body: '<div class="empty"><strong>未找到指令记录</strong><span>可能已被 Leader 处理并归档</span></div>' });
  const mask = v => JSON.stringify(v, (k, val) => /(token|password|authorization|secret|credential|api[_-]?key)/i.test(k) ? '******' : val, 2);
  const status = { queued: '排队中', processing: 'Leader 处理中', done: '已完成', failed: '执行失败' }[cmd.status] || cmd.status;
  openDrawer({ title: `${cmd.kind || '工作台'} 指令`, subtitle: `${status} · ${id}`, body: `<div class="row wrap" style="margin-bottom:12px"><span class="chip ${cmd.status === 'done' ? 'ok' : cmd.status === 'failed' ? 'bad' : 'info live'}">${esc(status)}</span><span class="chip plain">${esc(cmd.kind)}</span><span class="faint mono" style="font-size:calc(11px * var(--fs-scale, 1))">${esc(fmtDate(cmd.created))}</span></div>${cmd.status === 'processing' && cmd.claimed ? `<p class="faint" style="font-size:calc(12px * var(--fs-scale, 1));margin:0 0 10px">由 ${esc(cmd.claimed_by || 'Leader')} 于 ${esc(fmtDate(cmd.claimed))} 领取${(cmd.claimed_age_sec || 0) > 600 ? '，<strong>已超过 10 分钟没有回执</strong>：如果确认 Leader 已断线，可以退回队列让它重新领取' : ''}</p>` : ''}${cmd.result ? `<div class="field"><label>执行结果</label><div class="md">${markdown(String(cmd.result))}</div></div>` : ''}<div class="field"><label>负载</label><pre class="md" style="font:calc(12px * var(--fs-scale, 1))/1.5 var(--mono);background:var(--surface-2);padding:10px;border-radius:8px;overflow:auto">${esc(mask(cmd.payload || {}))}</pre></div>`, foot: `${cmd.payload?.task_id ? `<button type="button" class="primary" data-action="open-decision" data-id="${attr(cmd.payload.task_id)}">查看关联决策</button>` : ''}${cmd.status === 'processing' ? `<button type="button" class="ghost" data-action="requeue-command" data-id="${attr(id)}" title="Leader 领取后中途断线、指令一直卡在处理中时使用">退回队列</button>` : ''}<button type="button" class="ghost" data-action="close-drawer">关闭</button>` });
}
export async function openPreview(path) {
  F.drawer = { kind: 'file', key: path };
  openDrawer({ title: path.split('/').pop(), subtitle: path, kind: 'file', body: '<div class="sk" style="height:200px"></div>' });
  try {
    const info = await api(`/api/fileinfo?path=${encodeURIComponent(path)}`);
    if (F.drawer.key !== path) return;
    const raw = `/api/raw?path=${encodeURIComponent(path)}`;
    let body = '';
    if (info.kind === 'markdown' || info.kind === 'text') { const text = await textApi(`/api/file?path=${encodeURIComponent(path)}`).catch(() => textApi(raw)); body = info.kind === 'markdown' ? `<div class="md">${markdown(text)}</div>` : `<pre class="md" style="font:calc(12px * var(--fs-scale, 1))/1.5 var(--mono);background:var(--surface-2);padding:10px;border-radius:8px;overflow:auto;max-height:70vh">${esc(text)}</pre>`; }
    else if (info.kind === 'image') body = `<img class="preview-img" src="${raw}" alt="${attr(path)}">`;
    else if (info.kind === 'html' || info.kind === 'pdf') body = `<iframe class="preview-frame" src="${raw}" title="${attr(path)}" sandbox="allow-same-origin"></iframe>`;
    else if (info.kind === 'video') body = `<video controls src="${raw}" style="width:100%;border-radius:8px"></video>`;
    else if (info.kind === 'audio') body = `<audio controls src="${raw}" style="width:100%"></audio>`;
    else if (info.kind === 'csv') { const text = await textApi(raw); const rows = text.split(/\r?\n/).filter(Boolean).slice(0, 200).map(r => r.split(',')); body = `<div style="overflow:auto;max-height:70vh"><table class="table"><thead><tr>${(rows[0] || []).map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.slice(1).map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`; }
    else body = `<div class="empty"><strong>此类型不支持内嵌预览</strong><span>${esc(info.ext)} · ${(info.size / 1024).toFixed(1)} KB</span></div>`;
    $('#drawerBody').innerHTML = `<div class="row wrap" style="margin-bottom:10px;font-size:calc(12px * var(--fs-scale, 1));color:var(--muted)"><span class="chip plain">${esc(info.kind)}</span><span>${(info.size / 1024).toFixed(1)} KB</span><span>·</span><span>${esc(fmtDate(info.mtime))}</span></div>${body}`;
    $('#drawerFoot').innerHTML = `<a class="ghost" href="${raw}&download=1" style="text-decoration:none"><button type="button" class="ghost">下载</button></a>${info.same_machine ? `<button type="button" data-action="open-local" data-path="${attr(path)}">本机打开</button><button type="button" class="ghost" data-action="open-local" data-path="${attr(path)}" data-reveal="1">在资源管理器定位</button>` : ''}<button type="button" class="ghost" data-action="copy-text" data-text="${attr(winPath(path))}">${icon('copy')}复制路径</button>`;
    $('#drawerFoot').hidden = false;
  } catch (e) { if (F.drawer.key === path) $('#drawerBody').innerHTML = `<div class="banner danger">读取失败：${esc(e.message)}</div>`; }
}
export async function openFiles() {
  const f = ensureComposer(); const sel = new Set(f.context_files || []);
  openDrawer({ title: '选择上下文文件', subtitle: '文件内容会被嵌入任务包', kind: 'files', body: '<div class="sk"></div>' });
  try {
    const files = await api('/api/files?prefix=');
    const render = q => { const list = files.filter(x => !q || x.toLowerCase().includes(q)); $('#fileList').innerHTML = list.slice(0, 600).map(x => `<label class="pick ${sel.has(x) ? 'on' : ''}" style="padding:6px 8px"><input type="checkbox" value="${attr(x)}" ${sel.has(x) ? 'checked' : ''} data-file-check><span class="t mono truncate" style="font-size:calc(12px * var(--fs-scale, 1))">${esc(x)}</span></label>`).join('') || '<div class="empty">没有匹配文件</div>'; $('#fileCount').textContent = `${sel.size} 已选 · ${list.length} 匹配`; };
    $('#drawerBody').innerHTML = `<div class="filters"><div class="search grow">${icon('search')}<input type="search" id="fileFilter" placeholder="例如 design-v2…" autocomplete="off" aria-label="过滤文件"></div><span class="result-count" id="fileCount"></span></div><div id="fileList" class="stack" style="gap:4px;max-height:60vh;overflow:auto"></div>`;
    $('#drawerFoot').innerHTML = `<button type="button" class="ghost" data-action="close-drawer">取消</button><button type="button" class="primary" data-action="save-files">保存选择</button>`; $('#drawerFoot').hidden = false;
    render('');
    $('#fileFilter').addEventListener('input', e => render(e.target.value.toLowerCase()));
    $('#fileList').addEventListener('change', e => { const cb = e.target.closest('[data-file-check]'); if (!cb) return; cb.checked ? sel.add(cb.value) : sel.delete(cb.value); cb.closest('.pick').classList.toggle('on', cb.checked); $('#fileCount').textContent = `${sel.size} 已选`; });
    $('#drawer')._fileSel = sel;
  } catch (e) { $('#drawerBody').innerHTML = `<div class="banner danger">读取文件列表失败：${esc(e.message)}</div>`; }
}
function markEntityUrl(kind, id) { const cur = parseEntity(new URLSearchParams(location.search)); if (cur && cur.kind === kind && cur.id === id) return; url.set(entityPatch(kind, id), true); }
