// app-redispatch.js：从 app.js 拆出（ADR 0015，按页面拆模块；scripts/split_module.py 生成，函数体未改）
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

import { addAttachments, openDispatch } from './app-composer.js';
import { followupPanel } from './main.js';

// ===== 11e7 + d0a7：条件重派弹层 / 催 Leader 合并 / 催审（独立块，不触碰 openDispatch / addAttachments / followupPanel）=====
export function openRedispatch(taskId) {
  const d = decisions().find(x => x.task_id === taskId) || { task_id: taskId, peers: [] };
  const pr = progressOf(d);
  const answeredList = (d.progress && d.progress.answered) || []; const answered = new Set(Array.isArray(answeredList) ? answeredList : []);
  const orig = (pr.meta.peers || d.peers || []);
  const cands = agentList().map(a => a.peer);
  const all = [...new Set([...orig, ...cands])];
  const pending = orig.filter(pn => !answered.has(pn));
  const defaultSel = new Set(pending.length ? pending : orig);
  const row = pn => { const a = agentByName(pn); const off = a.online === false; const done = answered.has(pn); return `<label class="fchip ${defaultSel.has(pn) && !done ? 'on' : ''} ${off ? 'dim' : ''}" style="cursor:pointer" title="${done ? '已回答' : off ? '离线' : '在线'}"><input type="checkbox" name="rpeer" value="${attr(pn)}" ${defaultSel.has(pn) && !done ? 'checked' : ''} class="sr-only">${esc(displayName(pn))}${done ? ' ✓' : off ? ' ·离线' : ''}</label>`; };
  const modes = ['independent', 'discuss', 'redteam', 'relay'];
  const curMode = ['build', 'review'].includes(d.mode || pr.meta.mode) ? 'independent' : (d.mode || pr.meta.mode || 'independent');
  openDrawer({
    title: `条件重派「${titleOf(d)}」`, subtitle: `${taskId} · 已回 ${pr.answered}/${pr.expected}`, kind: 'redispatch',
    body: `<form data-form="redispatch" data-task="${attr(taskId)}" class="stack">
      <label class="row" style="gap:8px;align-items:center"><input type="checkbox" name="only_pending" ${pending.length && pr.answered ? 'checked' : ''}> 只重派未回的名额（已答者不再派）</label>
      <div class="field"><label>参与 Agent（在线优先，离线可选）</label><div class="row wrap" id="rpeers">${all.map(row).join('')}</div></div>
      <div class="row wrap" style="gap:12px">
        <div class="field"><label for="rmode">模式</label><select id="rmode" name="mode">${modes.map(m => `<option value="${m}" ${m === curMode ? 'selected' : ''}>${esc(modeLabel(m))}</option>`).join('')}</select></div>
        <div class="field"><label for="rttl">时限（分钟）</label><input id="rttl" name="ttl_min" type="number" min="1" max="1440" value="${Number(pr.meta.ttl_min) || 30}" style="width:90px"></div>
        <div class="field"><label for="rmin">法定份数</label><input id="rmin" name="min_answers" type="number" min="1" value="${Math.max(1, defaultSel.size - [...defaultSel].filter(x => answered.has(x)).length || 1)}" style="width:90px"><span class="help">默认 = 所选人数，自动钳到不超过人数</span></div>
      </div>
      ${pr.answered ? `<label class="row" style="gap:8px;align-items:center"><input type="checkbox" name="carry_answers"> 带上已到的 ${pr.answered} 份回答作为上下文（输入会变长）</label>` : ''}
      <div class="row"><button type="submit" class="primary">${icon('send')}重派</button><button type="button" class="ghost" data-action="close-drawer">取消</button><span class="muted" style="font-size:calc(12px * var(--fs-scale, 1))">原任务标记被取代并移出队列</span></div>
    </form>`,
  });
  const box = $('#rpeers'); if (box) box.addEventListener('change', e => { const l = e.target.closest('.fchip'); if (l) l.classList.toggle('on', e.target.checked); const n = $$('input[name="rpeer"]:checked').length; const mi = $('#rmin'); if (mi && n) mi.value = Math.min(Number(mi.value) || n, n) || n; });
}
