// page-compose.js：从 views2.js 拆出（ADR 0015，按页面拆模块；scripts/split_module.py 生成，函数体未改）
// 视图：发起、Agent、设置
import { esc, attr, icon, relTime, relAge, fmtDate } from './core.js';
import { S, F, project, agentList, onlineAgents, dispatchableAgents, undispatchableReason, DYNAMIC_TEAM, pickStats, pickStatsText, dispatchEstimate, agentByName, projectPeers, displayName, agentMeta, effectiveTeams, ensureComposer, IMAGE_ACCEPT, CONTROLLER_PEER, secondsLeft, fmtLeft } from './model.js';
import { head, empty, ovTabs, pageParts } from './views.js';
import { teamChips } from './components.js';
import * as Prefs from './prefs.js';

import { MODE_HELP, groupOfAgent, initials } from './views2.js';

export function renderCompose(p) {
  const f = ensureComposer(p);
  const agents = agentList(), selected = new Set(f.peers || []);
  // 团队按钮：动态「全员在线」永远第一项，数字是「可派发 / 登记人数」——离线、代理 Leader、已暂停都不算可派发（cd14 第 1/3 项）
  const pool = dispatchableAgents(p);
  const teams = effectiveTeams(p);
  const recv = [...selected].filter(Boolean), stats = pickStats(recv, p), est = dispatchEstimate(recv, p);
  const groups = (S().agent_groups || []).filter(g => agents.some(a => groupOfAgent(a)?.id === g.id));
  const len = Array.from(f.question || '').length;
  const mode = MODE_HELP[f.mode] || MODE_HELP.independent;
  return `<div class="view">${head('发起一轮讨论', '发起', '写清楚要判断的事，选好参与的 Agent，交给 Leader 派发；上下文由 Leader 按问题自行检索。')}
    <form id="composerForm" class="composer" data-form="composer" data-keep data-keep-id="composer">
      <div class="card pad compose-bar">
        <div class="field"><label for="mode">怎么回答</label><select id="mode" data-field="mode">${Object.entries(MODE_HELP).map(([k, v]) => `<option value="${k}" ${f.mode === k ? 'selected' : ''}>${v.label}</option>`).join('')}</select></div>
        <div class="field compose-ttl"><label for="ttl">最多等（分钟）</label><input id="ttl" type="number" min="1" max="1440" inputmode="numeric" value="${attr(f.ttl_min)}" data-field="ttl_min"></div>
        <span class="grow"></span>
        <button type="button" class="ghost" data-action="clear-composer">清空重填</button>
        <button type="submit" class="primary" id="composerSubmit" ${!f.question.trim() || !selected.size ? 'disabled' : ''}>${icon('send')}派发给 ${selected.size} 个 Agent</button>
        <span class="help compose-help">${mode.help} 到时未答的算迟到，已到的先合并。</span>
      </div>
      <div class="stack compose-cols">
        <div class="card pad stack">
          <div class="field"><label for="question">问题</label><textarea id="question" data-field="question" placeholder="例如：比较两个方案，判断哪一个更适合当前阶段，并说明验证方法。" data-dropzone>${esc(f.question)}</textarea><div class="row between"><span class="help">短句、明确判断标准；可 Ctrl+V 粘贴图片</span><span class="counter" data-counter>${len} 字</span></div></div>
          <button type="button" class="dropzone" data-dropzone-hint data-action="pick-images">${icon('plus')} 点击选择图片，或拖到这里 / 在问题框内粘贴</button><input type="file" id="imagePicker" accept="${IMAGE_ACCEPT}" multiple hidden>
          <div class="thumbs" id="attachmentTray">${(f.attachments || []).map((a, i) => `<div class="th"><img src="${attr(a.previewUrl || '')}" alt="${attr(a.name)}"><button type="button" data-action="remove-attachment" data-index="${i}" aria-label="移除 ${attr(a.name)}">×</button></div>`).join('')}</div>
          <div class="field"><label>上下文</label><div class="chips-in"><span class="chip ok plain">Leader 自动检索工作区相关文件</span>${(f.context_files || []).map(x => `<span class="chip plain mono">${esc(x.split('/').pop())}</span>`).join('')}<button type="button" class="small ghost" data-action="choose-files">${icon('file')}${(f.context_files || []).length ? '改选' : '手动指定'}</button>${(f.context_files || []).length ? `<button type="button" class="small ghost" data-action="clear-files">清空</button>` : ''}</div><span class="help">默认不用指定；只有你明确要它们必读某几份文件时才手动加。</span></div>
        </div>
        <div class="card pad">
          <div class="section-head"><h2>参与 Agent</h2><span class="hint">${esc(pickStatsText(recv, p))}</span></div>
          ${teamChips({ teams: teams.map(t => t.name === DYNAMIC_TEAM ? ({ name: t.name, count: pool.length, tip: '派发时取当前可派发的 Agent（离线 / 代理 Leader / 已暂停都不算）' }) : ({ name: t.name, count: (t.peers || []).length ? `${(t.peers || []).filter(n => !undispatchableReason(n, p)).length}/${(t.peers || []).length}` : '', tip: t.note || '' })), active: f.teamName, action: 'composer-team' })}
          <div class="row wrap" data-recv style="gap:6px;margin-bottom:10px"><span class="faint" style="font-size:calc(12px * var(--fs-scale, 1));width:56px">实际收到</span>${recv.length ? recv.map(n => { const why = undispatchableReason(n, p); return `<span class="chip plain ${why ? 'dim' : ''}"${why ? ` data-tip="${attr(why)}" style="text-decoration:line-through"` : ''}>${esc(displayName(n))}${why ? ` · ${esc(why)}` : ''}</span>`; }).join('') : `<span class="muted">还没选 Agent</span>`}</div>
          <div class="muted" data-estimate style="font-size:calc(12px * var(--fs-scale, 1));margin-bottom:10px">${esc(est.text)}</div>
          ${groups.length ? `<div class="row wrap" style="margin-bottom:12px"><span class="faint" style="font-size:calc(12px * var(--fs-scale, 1));width:56px">按模型</span>${groups.map(g => { const n = agents.filter(a => groupOfAgent(a)?.id === g.id && a.online).length; const on = agents.filter(a => groupOfAgent(a)?.id === g.id).every(a => selected.has(a.peer)) && n; return `<button type="button" class="fchip ${on ? 'on' : ''}" data-action="composer-group" data-group="${attr(g.id)}" title="选中或取消该组全部在线 Agent">${esc(g.name)}<span class="n">${n}</span></button>`; }).join('')}</div>` : ''}
          <div class="pick-grid">${agents.map(a => `<label class="pick ${selected.has(a.peer) ? 'on' : ''} ${a.online ? '' : 'dim'}"><input type="checkbox" name="agent" value="${attr(a.peer)}" ${selected.has(a.peer) ? 'checked' : ''} data-agent-pick><span class="avatar ${a.online ? '' : 'off'}">${esc(initials(displayName(a.peer)))}</span><span class="min0"><b class="truncate">${esc(displayName(a.peer))}</b><small class="truncate">${esc(groupOfAgent(a)?.name || a.model || '模型未登记')}${a.online ? '' : ' · 离线'}</small></span></label>`).join('') || empty('还没有 Agent', '到「Agent」页接入')}</div>
        </div>
      </div>
    </form>
  </div>`;
}
