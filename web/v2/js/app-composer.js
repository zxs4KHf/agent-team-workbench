// app-composer.js：从 app.js 拆出（ADR 0015，按页面拆模块；scripts/split_module.py 生成，函数体未改）
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

import { sendCommand } from './app-commands.js';
import { clearSendConfirm, renderView, switchView, uploadPending } from './main.js';

export function renderTray(target) {
  // 派发抽屉（#qdTray）与发起页（#attachmentTray）共用 composer 草稿，两处同时刷新
  const trays = target === F.followup ? [$('#followupTray')] : [$('#attachmentTray'), $('#qdTray')];
  const html = (target.attachments || []).map((a, i) => `<div class="th fade-up"><img src="${attr(a.previewUrl)}" alt="${attr(a.name)}"><button type="button" data-action="remove-attachment" data-index="${i}" aria-label="移除 ${attr(a.name)}">×</button></div>`).join('');
  trays.forEach(tray => { if (tray) tray.innerHTML = html; });
}
export function addAttachments(files, target = ensureComposer()) {
  const f = target; if (!f) return;
  for (const file of files) { if (!looksLikeImage(file)) { toast(`${file.name} 不是图片（仅 PNG/JPEG/GIF/WebP）`, 'warn'); continue; }  /* 空 file.type 按扩展名放行，交服务端裁决 */ if (file.size > 8 * 1024 * 1024) { toast(`${file.name} 超过 8 MB`, 'warn'); continue; } f.attachments.push({ file, name: file.name, previewUrl: URL.createObjectURL(file) }); }
  if (f !== F.followup) F.composerDirty = true;
  renderTray(f);
}
export function releaseAttachments(target) {
  if (!target?.attachments) return;
  target.attachments.forEach(a => a.previewUrl && URL.revokeObjectURL(a.previewUrl)); target.attachments = [];
}
export async function submitComposer(form) {
  const f = ensureComposer();
  f.question = $('[data-field="question"]', form).value; f.mode = $('[data-field="mode"]', form).value;
  f.ttl_min = clamp(Number($('[data-field="ttl_min"]', form).value) || 30, 1, 1440);
  f.peers = $$('[data-agent-pick]:checked', form).map(x => x.value);
  return submitDispatch(f, $('#composerSubmit'));
}
// 44f1：发送确认是一次性凭据，绑定「项目 + 名单 + 问题 + 模式 + 上下文」。任何一项变了都要重新确认。
function sendSignature(f) {
  // sota2 0e85：时限也是派发内容的一部分，改了必须重新确认 / 上传后中止
  return JSON.stringify([f.project || S().project, [...new Set(f.peers || [])].sort(), String(f.question || '').trim(), f.mode || '', [...(f.context_files || [])].sort(), clamp(Number(f.ttl_min) || 30, 1, 1440)]);
}
export async function submitDispatch(f, btn, confirmSig = null) {
  // compose 页与快速抽屉共用的唯一提交点：只读 F.composer，不碰任何表单 DOM
  // 44f1：入口先取出并作废凭据、撤掉旧提示条——缺问题 / 缺人 / 上传失败等任何早退都不留下发送权限
  const issued = F.sendConfirm; F.sendConfirm = null; clearSendConfirm();
  if (!f) return toast('请先创建项目', 'warn');
  if (!f.question.trim()) return toast('请先填写问题', 'warn');
  if (!f.peers.length) return toast('至少选择一个 Agent', 'warn');
  btn?.classList.add('busy');
  // e020：不静默剔除任何人。用户明确勾了谁就发给谁——paused 是「暂停自动推荐」的软语义（仍可手动点名），
  // 离线者更要能派：任务会以 .json 落进它的 inbox 安静等它回来领（stale_with_queue 正是为此存在）。
  // 与 bfde B3「统计与实发不一致」的矛盾改由「提交前内联确认」消灭，而不是改变既有语义。
  const blocked = f.peers.filter(n => undispatchableReason(n));
  const sig = sendSignature(f);
  // 只有点了提示条里的「仍要发送」（confirmSig）且它正是刚签发、并与此刻内容一致的那张凭据才放行
  if (blocked.length && !(confirmSig && confirmSig === issued && confirmSig === sig)) {
    btn?.classList.remove('busy');
    F.sendConfirm = sig;
    return inlineConfirmSend(btn, blocked);          // 内联提示条，不开新窗口/新抽屉
  }
  // sota2 4837：上传是异步的，期间页面仍可编辑。发出的必须是此刻（获准时）的内容——先拍快照，上传后再核一次，
  // 签名或附件有变就中止，让用户看清改动后重新发送（被拦的名单会重新要求确认）。
  const snap = { project: f.project, question: f.question.trim(), peers: [...f.peers], mode: f.mode, context_files: [...(f.context_files || [])], ttl_min: f.ttl_min };
  const pending = [...(f.attachments || [])];
  let attachments = [];
  try { attachments = await uploadPending(pending); }
  catch (e) { btn?.classList.remove('busy'); return notify.error(e, { prefix: '图片上传失败：' }); }
  // sota3 4485：切项目 / 清空草稿会把 F.composer 换成新对象，旧对象的内容不变、签名照样相等——必须先核草稿身份与项目
  if (F.composer !== f || (f.project && S().project && f.project !== S().project)) {
    btn?.classList.remove('busy');
    return toast('上传期间切换了项目或清空了草稿，原内容未发送', 'warn');
  }
  const atts = f.attachments || [];
  if (sendSignature(f) !== sig || atts.length !== pending.length || atts.some((a, i) => a !== pending[i])) {
    btn?.classList.remove('busy');
    return toast('上传期间内容有改动，未发送——请确认后重新点发送', 'warn');
  }
  const est0 = dispatchEstimate(snap.peers);
  const ok = await sendCommand('dispatch', { project: snap.project, question: snap.question, peers: snap.peers, mode: snap.mode, context_files: snap.context_files, attachments, ttl_min: snap.ttl_min, min_answers: null, angles: false, auto_context: true }, `已交给 Leader 发给 ${f.peers.length} 人 · 预计 ${est0.copies} 份回应`, btn);
  if (!ok) return;
  f.attachments.forEach(a => a.previewUrl && URL.revokeObjectURL(a.previewUrl)); f.attachments = []; f.question = '';
  // 发送期间用户可能已开了新草稿 / 切了项目：只有草稿仍是这一份时才清脏标记、收抽屉、跳到运行页
  if (F.composer === f) { F.composerDirty = false; if (drawerOpen()) closeDrawer(); switchView('running'); }
}

// e020：提交前的内联确认。只在提交按钮所在的那一行后面插一条提示，不开新窗口 / 新抽屉。
function undispatchableSummary(list) {
  const by = new Map();
  list.forEach(n => { const r = undispatchableReason(n) || '不可派发'; by.set(r, (by.get(r) || 0) + 1); });
  return [...by].map(([r, n]) => `${r} ${n}`).join(' / ');
}
function inlineConfirmSend(btn, blocked) {
  clearSendConfirm();
  const anchor = btn?.closest('.row') || btn?.parentElement;
  const box = document.createElement('div');
  box.setAttribute('data-send-confirm', '1');
  box.className = 'banner warn send-confirm';   // 不用 .hint：syncComposer 会把表单里第一个 .hint 的文字换成人数统计，确认条放进设置条后会被它冲掉
  box.style.marginTop = '8px';
  box.innerHTML = `<span>其中 ${blocked.length} 人当前不可派发（${esc(undispatchableSummary(blocked))}），仍要发送吗？</span> `
    + `<button type="button" class="small primary" data-action="confirm-send">仍要发送</button> `
    + `<button type="button" class="small ghost" data-action="cancel-send">取消</button>`;
  // 发起页的派发按钮在吸顶设置条里：确认条也放进设置条（占满一行），否则滚动时被吸顶条盖住点不到
  const bar = btn?.closest('.compose-bar');
  if (bar) bar.appendChild(box);
  else if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(box, anchor.nextSibling);
  else if (anchor) anchor.appendChild(box);
  else return toast(`其中 ${blocked.length} 人当前不可派发（${undispatchableSummary(blocked)}），请在发起页确认后再发`, 'warn');
  return false;
}
export function openDispatch(prefill = {}) {
  // 全站唯一派发入口：topbar 按钮 / n 键 / cmdk / 各卡片都经这里；prefill 只填空字段，不覆盖草稿
  if (S().view === 'compose') { const q = $('#question'); q?.scrollIntoView({ behavior: 'smooth', block: 'center' }); q?.focus(); return; }
  const f = ensureComposer(); if (!f) return toast('请先创建项目', 'warn');
  if (prefill.peers?.length) f.peers = [...prefill.peers];
  if (prefill.files?.length) f.context_files = [...new Set([...(f.context_files || []), ...prefill.files])];
  if (prefill.mode && !F.composerDirty) f.mode = prefill.mode;
  if (prefill.hint && !f.question) { f.question = prefill.hint; F.composerDirty = true; }
  const pool = dispatchableAgents();
  openDrawer({ title: '派发任务', subtitle: `${f.peers.length} 个 Agent · ${modeLabel(f.mode)}`, kind: 'dispatch', body: `<form data-form="dispatch" class="stack"><div class="field"><label for="qdQuestion">问题</label><textarea id="qdQuestion" rows="6" data-qd="question" data-dropzone placeholder="写清楚要判断的事；可直接粘贴或拖入图片；Ctrl+Enter 发送">${esc(f.question)}</textarea></div><div class="field"><button type="button" class="dropzone" data-dropzone-hint data-action="pick-images">${icon('plus')} 添加图片（点击选择 / 拖到这里 / 在问题框粘贴）</button><input type="file" id="qdImagePicker" accept="image/*" multiple hidden><div class="thumbs" id="qdTray">${(f.attachments || []).map((a, i) => `<div class="th"><img src="${attr(a.previewUrl || '')}" alt="${attr(a.name)}"><button type="button" data-action="remove-attachment" data-index="${i}" aria-label="移除 ${attr(a.name)}">×</button></div>`).join('')}</div></div>${teamChips({ teams: effectiveTeams().map(t => t.name === DYNAMIC_TEAM ? ({ name: t.name, count: pool.length, tip: '派发时取当前可派发的 Agent（离线 / 代理 Leader / 已暂停都不算）' }) : ({ name: t.name, count: (t.peers || []).length ? `${(t.peers || []).filter(n => !undispatchableReason(n)).length}/${(t.peers || []).length}` : '', tip: t.note || '' })), active: f.teamName, action: 'dispatch-team', editAction: '' })}<div class="row wrap"><label class="field" style="margin:0"><span class="faint" style="font-size:calc(12px * var(--fs-scale, 1))">模式</span><select data-qd="mode">${['race', 'independent', 'redteam', 'relay', 'build', 'discuss'].map(m => `<option value="${m}" ${f.mode === m ? 'selected' : ''}>${esc(modeLabel(m))}</option>`).join('')}</select></label><span class="grow"></span><span class="faint" style="font-size:calc(12px * var(--fs-scale, 1))" data-qd-count>${f.peers.length} 人 · 预计 ${dispatchEstimate(f.peers).copies} 份回应${f.context_files?.length ? ` · ${f.context_files.length} 个文件` : ''}</span></div><div class="row"><button type="submit" class="primary" id="qdSubmit">${icon('send')}派发</button><button type="button" class="ghost" data-action="dispatch-expand">展开完整表单</button></div></form>` });
  $('#qdQuestion')?.focus();
}
// 936d：表单带 data-keep 不参与 morph，所以「状态 → 表单」只能由这里写。syncComposer 是表单派生 UI 的唯一写者：
// fromState=true 表示先把 state（Team 宏 / 按模型宏改出来的 f.peers / f.mode / f.teamName）灌回 checkbox / select / fchip，再统一派生计数、按钮、hint。
export function syncComposer(form, fromState = false) {
  const f = ensureComposer(); if (!f || !form) return;
  if (fromState) {
    const want = new Set(f.peers || []);
    $$('[data-agent-pick]', form).forEach(cb => { cb.checked = want.has(cb.value); });
    const ms = $('[data-field="mode"]', form); if (ms && f.mode && ms.value !== f.mode) ms.value = f.mode;
    const tt = $('[data-field="ttl_min"]', form); if (tt && f.ttl_min && Number(tt.value) !== Number(f.ttl_min)) tt.value = f.ttl_min;
    $$('[data-action="composer-team"]', form).forEach(b => b.classList.toggle('on', !!f.teamName && b.dataset.team === f.teamName));
  } else {
    f.question = $('[data-field="question"]', form)?.value ?? f.question; f.peers = $$('[data-agent-pick]:checked', form).map(x => x.value);
    f.mode = $('[data-field="mode"]', form)?.value ?? f.mode;
    const tt = $('[data-field="ttl_min"]', form); if (tt) f.ttl_min = clamp(Number(tt.value) || 10, 1, 1440);
    if (f.teamName && $$('[data-agent-pick]', form).length) { const want = teamPeersOf(f.teamName); if (want.length !== f.peers.length || want.some(pn => !f.peers.includes(pn))) { f.teamName = ''; $$('[data-action="composer-team"]', form).forEach(b => b.classList.remove('on')); } }
  }
  F.composerDirty = !!(f.question.trim() || f.attachments.length);
  const c = $('[data-counter]', form); if (c) c.textContent = `${Array.from(f.question).length} 字`;
  // 按钮数字 = 真正会收到任务的人数（与 dispatchEstimate.copies、提交时的过滤同源）
  const est = dispatchEstimate(f.peers);
  // 按钮数字 = 实发人数（勾了几个就发几个）；预计份数是「预期回应数」，措辞分开，别再混成一个数
  const b = $('#composerSubmit'); if (b) { b.disabled = !f.question.trim() || !f.peers.length; b.innerHTML = `${icon('send')}发给 ${f.peers.length} 人 · 预计 ${est.copies} 份回应`; b.title = !f.question.trim() ? '先写清楚问题' : !f.peers.length ? '至少选 1 个 Agent' : est.copies < f.peers.length ? '其中有当前不可派发的 Agent，提交时会先确认' : ''; }
  $$('[data-agent-pick]', form).forEach(cb => cb.closest('.pick')?.classList.toggle('on', cb.checked));
  const sel = new Set(f.peers);
  $$('[data-action="composer-group"]', form).forEach(b => { const members = agentList().filter(a => groupOfAgent(a)?.id === b.dataset.group); const online = members.filter(a => a.online); b.classList.toggle('on', !!online.length && members.every(a => sel.has(a.peer))); });
  // 统计文案只有一处真值：与 views2 首帧同一个 pickStatsText，不再把「已选 N / 在线 M」覆盖回去（e4e0 必改 2）
  const hint = $('.hint', form); if (hint) hint.textContent = pickStatsText(f.peers);
}
export function composerStateChanged() { const form = $('#composerForm'); if (form) syncComposer(form, true); else renderView(S()); }

