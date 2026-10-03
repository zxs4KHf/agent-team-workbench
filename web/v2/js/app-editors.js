// app-editors.js：从 app.js 拆出（ADR 0015，按页面拆模块；scripts/split_module.py 生成，函数体未改）
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
import { teamRow } from './main.js';

export function openPlanEditor() { const p = project(); if (!p) return; openDrawer({ title: '编辑下一步', subtitle: '每行一项，保存后覆盖当前项目的计划列表', body: `<form data-form="plan"><div class="field"><label for="planText">计划列表</label><textarea id="planText" name="plan" rows="10">${esc((p.next || []).join('\n'))}</textarea></div><div class="row"><button type="submit" class="primary">保存</button><button type="button" class="ghost" data-action="close-drawer">取消</button></div></form>` }); }
// 权重下拉放在团队抽屉里（5b61 分歧点第 3 条：主看板不放常驻控件）。逐项即时保存，不走表单提交
function weightRows() {
  const w = agentWeights();
  return `<section class="card pad stack" id="agentWeightBox" style="gap:6px">
    <div class="section-head"><h2>Agent 权重</h2><span class="hint">写入 priority.json 的 agent_weight；Leader 调度目前只消费 task_order，权重暂不影响派发顺序</span></div>
    ${agentList().map(a => `<div class="row" style="gap:8px"><span class="grow truncate">${esc(displayName(a.peer))}</span><select data-agent-weight="${attr(a.peer)}">${WEIGHT_OPTIONS.map(([v, l]) => `<option value="${v}" ${String(w[a.peer] ?? '') === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div>`).join('') || '<div class="empty">还没有 Agent</div>'}
  </section>`;
}
export function openTeamEditor() { openDrawer({ title: '编辑 Team', subtitle: '预设可改可删，保存后写入当前项目', body: `<form data-form="teams" class="stack"><div id="teamRows" class="stack">${effectiveTeams().map(teamRow).join('')}</div><button type="button" class="small" data-action="add-team-row">${icon('plus')}添加 Team</button><div class="row"><button type="submit" class="primary">保存 Team</button><button type="button" class="ghost" data-action="close-drawer">取消</button></div></form>${weightRows()}` }); }
export function openAgentEditor(peer) { openDrawer({ title: '编辑 Agent 资料', subtitle: peer, body: profileEditor(peer, S().profiles?.[peer] || {}, agentMeta(peer)) }); }
export function openProjectForm(edit) { const p = edit ? project() : { name: '', description: '', peers: [], mode: 'independent', context_files: [] }; openDrawer({ title: edit ? '项目配置' : '新建项目', subtitle: edit ? p.slug : '保存默认团队与上下文文件', body: `<form data-form="project" data-edit="${edit ? 1 : 0}"><div class="field"><label for="projectName">项目名称</label><input id="projectName" name="name" value="${attr(p.name || p.slug || '')}" ${edit ? 'readonly aria-readonly="true" title="项目名称创建后不能改"' : 'required'} placeholder="例如：重力回廊" autocomplete="off"></div><div class="field"><label for="projectDesc">一句话说明</label><input id="projectDesc" name="description" value="${attr(p.description || '')}" autocomplete="off"></div><div class="field"><label>默认团队</label><div class="pick-grid">${agentList().map(a => `<label class="pick ${(p.peers || []).includes(a.peer) ? 'on' : ''}"><input type="checkbox" name="peers" value="${attr(a.peer)}" ${(p.peers || []).includes(a.peer) ? 'checked' : ''}><span class="t"><b>${esc(displayName(a.peer))}</b><small>${esc(a.model || '')}</small></span></label>`).join('')}</div></div><div class="field"><label for="projectDefaultTeam">默认团队（发起页缺省勾选哪一组）</label><select id="projectDefaultTeam" name="default_team">${[DYNAMIC_TEAM, PROJECT_TEAM, ...(Array.isArray(p.teams) ? p.teams.map(t => t && t.name) : [])].filter((n, i, a) => n && a.indexOf(n) === i).map(n => `<option value="${attr(n)}" ${(p.default_team || DYNAMIC_TEAM) === n ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select><span class="help">「${esc(DYNAMIC_TEAM)}」= 派发时取当前可派发者；「${esc(PROJECT_TEAM)}」= 上面勾选的默认团队成员；也可选自定义团队。</span></div><div class="field"><label for="projectMode">默认模式</label><select id="projectMode" name="mode">${['independent', 'redteam', 'relay'].map(m => `<option value="${m}" ${p.mode === m ? 'selected' : ''}>${esc(modeLabel(m))}</option>`).join('')}</select></div><div class="field"><label for="projectCtx">默认上下文文件（每行一个）</label><textarea id="projectCtx" name="context_files" rows="4" class="mono" style="font-size:calc(12px * var(--fs-scale, 1))">${esc((p.context_files || []).join('\n'))}</textarea></div><div class="row"><button type="submit" class="primary">${edit ? '保存' : '创建项目'}</button><button type="button" class="ghost" data-action="close-drawer">取消</button></div></form>` }); }
const SHORT_NAMES = { 'gpt-6-astra': 'astra', 'claude-fable-5': 'fable', 'kimi-k3': 'kimi', 'claude-opus-5': 'opus', 'glm-5.3': 'glm', 'deepseek-flash': 'deepseek', 'qwen3.8-flash': 'qwenflash', 'qwen3.8-max': 'qwenmax', 'gemini-3.8-flash': 'gemini', 'hy4-preview': 'hy', 'grok-4.6': 'grok', 'gemini-4-pro': 'geminipro', 'gpt-5.6': 'gpt' };
export function nextPeerName(model) {
  // 自动命名：先沿用同模型已有 Agent 的命名规律（sol-6-1…sol-6-5 → sol-6-6）；没有再用模型组短名（astra、fable、kimi…）加递增序号；
  // 未知模型取名字里最有辨识度的词；数字结尾的短名加连字符避免与序号混淆
  const taken = new Set(agentList().map(a => a.peer)), learned = new Map();
  agentList().filter(a => model && a.model === model).forEach(a => { const m = a.peer.match(/^(.*?\D)(\d+)$/); if (m) learned.set(m[1], Math.max(learned.get(m[1]) || 0, Number(m[2]))); });
  if (learned.size) { const [pre, max] = [...learned].sort((x, y) => y[1] - x[1])[0]; for (let i = max + 1; i < max + 1000; i++) if (!taken.has(pre + i)) return pre + i; }
  const g = groupOfModel(model); const id = (g ? g.id : String(model || 'agent')).toLowerCase();
  let base = SHORT_NAMES[id];
  if (!base) {
    const vendor = new Set(['claude', 'gpt', 'gemini', 'openai', 'google', 'anthropic', 'zai', 'org', 'preview', 'flash', 'series', 'pro', 'max']);
    const parts = id.split(/[-_\s]+/).filter(Boolean);
    base = (parts.filter(w => !vendor.has(w) && /[a-z]/.test(w)).map(w => w.replace(/[\d.]+$/, '')).find(Boolean) || parts[0] || 'agent').replace(/[^a-z0-9]/g, '').slice(0, 12) || 'agent';
  }
  if (/\d$/.test(base)) base += '-';
  for (let i = 1; i < 1000; i++) if (!taken.has(base + i)) return base + i;
  return base + Date.now();
}
// 显示名唯一写入口：POST /api/peer/label；404（无连接记录）→ 以 pending 方式再存一次；非 2xx 一律提示，不假装成功
export async function saveLabel(peer, label, pending = false) {
  try {
    let r;
    try { r = await api('/api/peer/label', { method: 'POST', body: JSON.stringify({ peer, label, pending }) }); }
    catch (e) { if (!pending && /不存在|missing/.test(e.message)) r = await api('/api/peer/label', { method: 'POST', body: JSON.stringify({ peer, label, pending: true }) }); else throw e; }
    toast(r.pending ? '已保存为首次连接前的显示名，连上后生效' : (label ? '已改名' : '已恢复原名'), 'ok', { ms: 1600 });
    await refresh(true); return r;
  } catch (e) { toast(`显示名未保存：${e.message}`, 'bad'); return null; }
}
// 接入新 Agent：把各分组的模型列成可点的芯片，点一下就填好模型、自动编号、生成链接（不用记模型名怎么拼）
// 接入新 Agent（2026-10-02 用户：「下拉不全，把常用模型都列出来」「根据模型名和已有 Agent 自动推荐命名」）
// 常用模型 = 现有 Agent 实际在用的模型，按人数从多到少；再补上分组里配置过、还没人用的模型前缀
export function commonModels() {
  const cnt = new Map();
  agentList().forEach(a => { const m = String(a.model || '').trim(); if (m && m !== 'controller' && !m.startsWith('_')) cnt.set(m, (cnt.get(m) || 0) + 1); });
  (S().agent_groups || []).forEach(g => (g.match || []).forEach(m => { if (m && !cnt.has(m)) cnt.set(m, 0); }));
  return [...cnt].sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0])).map(([model, n]) => ({ model, n, group: groupOfModel(model)?.name || '' }));
}
function joinModelChips() {
  const top = commonModels().filter(m => m.n > 0).slice(0, 14);
  const list = top.length ? top : commonModels().slice(0, 14);
  return list.length ? `<div class="row wrap join-models">${list.map(m => `<button type="button" class="chip plain" data-action="join-pick-model" data-model="${attr(m.model)}" title="${attr(m.group ? m.group + ' 组' : '未归组')}${m.n ? ` · 已有 ${m.n} 个` : ''}">${esc(m.model)}${m.n ? ` <b>×${m.n}</b>` : ''}</button>`).join('')}</div>` : '';
}
// 名字推荐：按模型自动编号的下一个可用名，再给两个备选；同模型已有的名字写在提示里；撞名当场说明后果
function nameSuggestions(model) {
  const first = nextPeerName(model); const m = first.match(/^(.*?)(\d+)$/);
  if (!m) return [first];
  const taken = new Set(agentList().map(a => a.peer)); const out = [first];
  for (let i = Number(m[2]) + 1; out.length < 3 && i < Number(m[2]) + 50; i++) if (!taken.has(m[1] + i)) out.push(m[1] + i);
  return out;
}
function joinNameHint() {
  const model = ($('#joinModel')?.value || '').trim(), name = ($('#joinPeer')?.value || '').trim(), box = $('#joinNameHint'), dl = $('#joinPeerOptions');
  if (!box) return;
  const sugg = model ? nameSuggestions(model) : [];
  if (dl) dl.replaceChildren(...sugg.map(s => Object.assign(document.createElement('option'), { value: s })));
  const same = model ? agentList().filter(a => a.model === model).map(a => a.peer).sort((x, y) => x.localeCompare(y, undefined, { numeric: true })) : [];
  const clash = name && agentList().some(a => a.peer === name);
  box.className = clash ? 'help warn-text' : 'help';
  box.textContent = clash ? `已有叫「${name}」的 Agent：用这个名字会接管它的身份（适合原 Agent 掉线后换个会话重连）。要新接入一个，建议用 ${sugg[0] || '别的名字'}。`
    : model ? `按模型自动编号${same.length ? `；这个模型已有 ${same.slice(0, 6).join('、')}${same.length > 6 ? ` 等 ${same.length} 个` : ''}` : ''}${sugg.length > 1 ? `；也可以用 ${sugg.slice(1).join('、')}` : ''}。`
    : '先选模型，名称会自动编号。';
}
// 组卡片「接入」传的是分组前缀（如 claude-opus）：换成这一组里实际用得最多的具体模型；没人用就保持前缀
function preferredModel(model) {
  if (!model || agentList().some(a => a.model === model)) return model;
  const g = groupOfModel(model); if (!g) return model;
  const hit = commonModels().find(m => m.n > 0 && groupOfModel(m.model)?.id === g.id);
  return hit ? hit.model : model;
}
export function openJoin(kind, peer = '', model = '') {
  model = preferredModel(model);
  const ctl = kind === 'controller'; const name = peer || (model ? nextPeerName(model) : '');
  openDrawer({ title: ctl ? '接入 Leader' : '接入新 Agent', subtitle: ctl ? '复制下面的链接，粘贴到宿主（Claude、ChatGPT、Cursor 等）的 MCP 服务器地址栏，连上即成为 Leader' : '复制链接给该 Agent 所在的宿主添加为 MCP 服务器；名称已按模型自动编号', body: `<form data-form="join" data-kind="${kind}" class="stack">${ctl ? (S().features && S().features.lead_label === false ? '' : `<div class="field"><label for="joinLabel">Leader 显示名（可选，先保存再生成提示词）</label><div class="row"><input id="joinLabel" name="label" value="${attr(displayName('controller') !== 'controller' ? displayName('controller') : ((S().pending_labels || {}).controller || ''))}" maxlength="24" placeholder="例如 小队长" autocomplete="off" spellcheck="false"><button type="button" class="small" data-action="join-save-label">保存显示名</button></div><span class="help">只是显示；协议 ID 仍是 <b class="mono">controller</b>。保存成功后下面的链接与提示词会带上这个名字；Leader 尚未连接时先存为首次连接前的显示名。</span></div>`) : `<div class="form-row"><div class="field"><label for="joinModel">模型</label><input id="joinModel" name="model" value="${attr(model)}" list="modelOptions" placeholder="例如 claude-fable-5" autocomplete="off" spellcheck="false"><datalist id="modelOptions">${commonModels().map(m => `<option value="${attr(m.model)}">${esc([m.group, m.n ? `已有 ${m.n} 个` : ''].filter(Boolean).join(' · '))}</option>`).join('')}</datalist>${joinModelChips()}</div><div class="field"><label for="joinPeer">Agent 名称（自动编号，可改）</label><input id="joinPeer" name="peer" value="${attr(name)}" list="joinPeerOptions" autocomplete="off" spellcheck="false"><datalist id="joinPeerOptions"></datalist><span class="help" id="joinNameHint"></span></div></div>`}<div id="linkBox" class="stack"></div><div id="promptResult"></div></form>` });
  const render = async () => {
    const box = $('#linkBox'); if (!box) return;
    const p = ctl ? '' : $('#joinPeer')?.value.trim(); const m = ctl ? '' : $('#joinModel')?.value.trim();
    if (!ctl && (!p || !m)) { box.innerHTML = '<div class="banner">先在上面点一个模型（或自己填模型名），名称会自动编号，接入链接马上生成。</div>'; return; }
    box.innerHTML = '<div class="sk" style="height:40px"></div>';
    try {
      const r = await api(`/api/connect_link?${new URLSearchParams({ kind, ...(ctl ? {} : { peer: p, model: m }) })}`);
      box.innerHTML = `<div class="field"><label>MCP 链接（含身份，直接粘贴）</label><div class="row"><code class="mono keybox" style="font-size:calc(12px * var(--fs-scale, 1))">${esc(r.url)}</code><button type="button" class="primary" data-action="copy-text" data-text="${attr(r.url)}">${icon('copy')}复制链接</button></div><span class="help">宿主没有 MCP 地址栏时，改用下面的提示词，它会指导模型自己连。</span></div>
        <details><summary class="muted" style="cursor:pointer;font-size:calc(13px * var(--fs-scale, 1))">备用：完整接入提示词</summary><div class="field" style="margin-top:8px"><textarea id="promptText" class="mono" style="min-height:220px;font-size:calc(12px * var(--fs-scale, 1))" readonly>${esc(r.prompt)}</textarea><button type="button" class="small" data-action="copy-text" data-text="${attr(r.prompt)}" style="margin-top:6px">${icon('copy')}复制提示词</button></div></details>`;
    } catch (e) { box.innerHTML = `<div class="banner danger">${esc(e.message)}</div>`; }
  };
  openJoin._render = render;
  if (!ctl) { let timer; const onInput = () => { clearTimeout(timer); timer = setTimeout(render, 400); }; $('#joinModel').addEventListener('change', e => { const n = $('#joinPeer'); if (n && (!n.value || /\d+$/.test(n.value))) n.value = nextPeerName(e.target.value); joinNameHint(); onInput(); }); $('#joinPeer').addEventListener('input', () => { joinNameHint(); onInput(); }); joinNameHint(); if (model) $('#joinPeer').focus(); else $('#joinModel').focus(); }
  render();
}

