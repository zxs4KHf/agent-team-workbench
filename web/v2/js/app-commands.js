// app-commands.js：从 app.js 拆出（ADR 0015，按页面拆模块；scripts/split_module.py 生成，函数体未改）
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

import { refresh } from './app-sync.js';
import { PREF, renderView, setPrefs } from './main.js';

// ---------- 指令与保存 ----------
// 用户点了「让它走开」类操作后，先从视野里移除；后端处理失败再回来
const HIDE_KINDS = new Set(['merge_now', 'cancel', 'supersede', 'redispatch', 'seal', 'adopt', 'reject']);
// nudge 不隐藏任务（只是催办，任务仍在待合并列表）
export function hideTask(id) { if (!id) return; const d = decisions().find(x => x.task_id === id); F.hidden = F.hidden || new Map(); F.hidden.set(id, { t: Date.now(), status: d?.status || '' }); F.expanded.delete(id); renderView(S()); }
export function unhideTask(id) { F.hidden?.delete(id); renderView(S()); }
export async function sendCommand(kind, payload, msg = '指令已排队', btn) {
  btn?.classList.add('busy');
  if (HIDE_KINDS.has(kind)) hideTask(payload?.task_id);
  try { await api('/api/command', { method: 'POST', body: JSON.stringify({ kind, payload }) }); toast(msg, 'ok'); await refresh(true); return true; }
  catch (e) { if (HIDE_KINDS.has(kind)) unhideTask(payload?.task_id); notify.error(e); return false; }
  finally { btn?.classList.remove('busy'); }
}
// 催审 / 全部催审：结果逐条说清——派给了谁、哪些已有人在审、哪些派不出去（例如没有在线 Reviewer）
export async function reviewNow(btn, taskId) {
  btn?.classList.add('busy');
  try {
    const r = await api('/api/review/now', { method: 'POST', body: JSON.stringify({ task_id: taskId || '' }) });
    const sent = r.dispatched || [], skip = r.skipped || [], err = r.errors || [];
    const parts = [];
    if (sent.length) parts.push(`已派审 ${sent.length} 单（${[...new Set(sent.flatMap(d => d.reviewers || []))].map(displayName).join('、')}）`);
    if (skip.length) parts.push(`${skip.length} 单已有人在审`);
    if (err.length) parts.push(`${err.length} 单派不出去：${err[0].error}`);
    toast(parts.join('；') || '没有待审的提交', err.length && !sent.length ? 'warn' : 'ok', { ms: err.length ? 6000 : 3000 });
    await refresh(true);
  } catch (e) { notify.error(e); } finally { btn?.classList.remove('busy'); }
}
export async function loadSettings() { try { store.set({ settings: await api('/api/settings') }); } catch (e) { notify.error(e); } }
export async function saveSettings(patch, msg = '已保存') {
  try { const next = await api('/api/settings', { method: 'POST', body: JSON.stringify(patch) }); store.set({ settings: next }); toast(next._notice || (next.restart_required ? '已保存，需重启 MCP 服务才生效' : msg), next._notice || next.restart_required ? 'warn' : 'ok'); }
  catch (e) { notify.error(e); }
}
export async function saveInline(el) {
  const kind = el.dataset.inline, field = el.dataset.field, value = el.innerText.replace(/\u00a0/g, ' ').trim();
  el.classList.add('saving');
  try {
    if (kind === 'brand') {
      // 品牌是本机外观，不发服务端命令；超长由 keydown 拦，这里兜底并还原，避免出现「看着存了其实没存」
      const max = field === 'subtitle' ? PREF.subtitleMax : PREF.titleMax;
      if (Array.from(value).length > max) { el.innerText = el.dataset.orig || ''; toast(`品牌${field === 'subtitle' ? '副标题' : '标题'}最多 ${max} 个字`, 'warn'); return; }
      setPrefs(p => { p.brand[field] = value; return p; });
      toast('已保存到本机外观', 'ok', { ms: 1400 });
      return;
    }
    if (kind === 'project') {
      const p = project(); if (!p) return;
      const body = field === 'note' ? { notes: value ? [...(p.notes || []), { time: new Date().toISOString(), text: value }].slice(-50) : [] } : { [field]: value };
      await api('/api/project/update', { method: 'POST', body: JSON.stringify({ slug: p.slug, ...body }) });
    } else if (kind === 'decision') {
      await sendCommand('custom', { project: S().project, action: 'revise_decision', task_id: el.dataset.task, [field]: value, user_revised: true }, '修订已提交 Leader');
      return;
    }
    notify('saved'); await refresh(true);
  } catch (e) { notify.error(e, { prefix: '行内保存失败：' }); } finally { el.classList.remove('saving'); }
}
