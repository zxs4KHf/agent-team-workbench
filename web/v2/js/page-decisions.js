// page-decisions.js：从 views.js 拆出（ADR 0015，按页面拆模块；scripts/split_module.py 生成，函数体未改）
// 视图：概览、进行中、决策、成果
import { esc, attr, icon, sparkline, ring, fmtDate, relTime, minutesSince } from './core.js';
import { S, F, project, decisions, activeDecisions, isUnbound, UNBOUND_HINT, runningDecisions, doneDecisions, agentList, onlineAgents, agentByName, projectPeers, displayName, titleOf, displayTitle, bucketOf, BUCKETS, threadsOf, childrenOf, artifactsOf, deliveredDecisions, weekDelivered, progressOf, stallInfo, countPending, STATUS, STATUS_TONE, EXEC_LABEL, PEER_STATE, ARTIFACT_KIND, orderedTasks, taskTier } from './model.js';

import { briefFeedback, briefOf, capped, chainStepOf, chip, empty, head } from './views.js';

// 决策页默认 20 组（原来 164 组全渲染，整页 4.7 万 px）；有搜索或筛选时全量显示，免得搜到的结果被折叠
const DECISION_CAP = 20;
export function renderDecisions(p) {
  const q = F.filters.decisions.trim().toLowerCase(), st = F.filters.decisionStatus;
  const all = doneDecisions(p);
  const counts = {}; all.forEach(d => { counts[d.status] = (counts[d.status] || 0) + 1; });
  const list = all.filter(d => (!st || d.status === st) && (!q || displayTitle(d).toLowerCase().includes(q) || titleOf(d).toLowerCase().includes(q) || (d.summary || '').toLowerCase().includes(q) || (d.brief || '').toLowerCase().includes(q)));
  const fchip = (k, label) => `<button type="button" class="fchip ${st === k ? 'on' : ''}" data-filter-status="${k}">${esc(label)}<span class="n">${k ? counts[k] || 0 : all.length}</span></button>`;
  return `<div class="view">${head('决策记录', '决策', '合并稿默认只展开「最终方案」；共识、分歧和验证依据按需打开。')}
    <section class="card fit-panel" data-key="fit-decisions"><div class="filters fit-head"><div class="search">${icon('search')}<input type="search" placeholder="搜索标题或摘要…" value="${attr(F.filters.decisions)}" data-filter="decisions" aria-label="搜索决策" autocomplete="off"></div>${fchip('', '全部')}${['merged', 'adopted', 'archived', 'rejected', 'cancelled', 'superseded'].filter(k => counts[k]).map(k => fchip(k, STATUS[k])).join('')}<span class="result-count">${list.length} 条</span></div>
    <div class="fit-body"><div class="stack" id="decisionList">${list.length ? capped((q || st) ? 'decisions-filtered' : 'decisions', threadsOf(list, p), threadBlock, DECISION_CAP) : empty(all.length ? '没有匹配的决策' : '还没有决策', all.length ? '换个筛选条件' : '完成一轮对读并合并后出现在这里')}</div></div></section>
  </div>`;
}
function threadBlock(t) {
  // 903f：同一 root_task 的各轮折叠成一组，只展开最新一轮；单条（旧记录 self-root）直接平铺
  if (t.items.length === 1) return decisionCard(t.items[0]);
  const label = t.topic || displayTitle(t.items[0]);
  const latest = t.latest;
  return `<details class="thread" data-thread="${attr(t.root)}" open><summary style="cursor:pointer;padding:6px 4px;font-size:calc(13px * var(--fs-scale, 1))"><b>〔${esc(label)}〕</b> · 共 ${t.items.length} 轮 · 最新：${esc(displayTitle(latest))} ${chip(latest.status)}<span class="faint mono" style="font-size:calc(11px * var(--fs-scale, 1));margin-left:6px">${esc(t.root)}</span></summary>
    <div class="stack" style="margin-top:6px">${t.items.map(d => d === latest ? decisionCard(d) : `<details data-key="${attr(d.task_id)}"><summary style="cursor:pointer;font-size:calc(12px * var(--fs-scale, 1));padding:4px">${esc(displayTitle(d))} ${chip(d.status)} <span class="faint mono" style="font-size:calc(11px * var(--fs-scale, 1))">${esc(d.task_id)}</span></summary>${decisionCard(d)}</details>`).join('')}</div></details>`;
}
export const introForCard = d => {
  const own = String((d || {}).intro || '').trim();
  if (own) return own;                                  // auto / manual 一视同仁：写了就显示
  const rootId = ((d || {}).lineage || {}).root_task || (d || {}).parent_task || '';
  const root = rootId && rootId !== (d || {}).task_id && decisions().find(x => x.task_id === rootId);
  const inherited = root ? String(root.intro || '').trim() : '';
  if (inherited) {
    const { step, total } = chainStepOf(d);
    return `${inherited} 这是这条任务链的第 ${step}/${total} 步。`;
  }
  return briefOf(d);                                    // 两级都空：交回 briefOf 的既有兜底
};
function decisionCard(d) {
  // 30f6：卡面只放 brief（缺失走兜底句）；summary 是给 Leader 的流水账，收进下面默认折叠的详情里
  const brief = introForCard(d);
  const exec = d.execution || 'pending';
  return `<article class="card dcard hover" data-key="${attr(d.task_id)}" id="decision-${attr(d.task_id)}">
    <header><h3 class="truncate" title="${attr(d.task_id)}">${esc(displayTitle(d))}</h3>${chip(d.status)}<span class="chip plain ${exec === 'released' ? 'ok' : exec === 'partial' ? 'warn' : ''}" data-tip="落地标记，只是记号；施工进度看「已采纳，等待落地」">${esc(d.implementation?.label || EXEC_LABEL[exec])}</span><span class="faint mono" style="font-size:calc(11px * var(--fs-scale, 1))">${esc(fmtDate(d.updated || d.created))}</span></header>
    <p class="summary">${esc(brief)}</p>
    ${briefFeedback(d, (project() || {}).slug)}
    ${d.summary ? `<details class="dsum"><summary class="muted" style="cursor:pointer;font-size:calc(12px * var(--fs-scale, 1))">详情：给 Leader 的流水账</summary><p class="faint" style="margin:6px 0 0;font-size:calc(12px * var(--fs-scale, 1))">${esc(d.summary)}</p></details>` : ''}
    ${isUnbound(d) ? `<div class="row wrap" style="font-size:calc(12px * var(--fs-scale, 1));color:var(--warn)">${icon('warn')}<span class="grow">${esc(UNBOUND_HINT)}</span><button type="button" class="chip plain" data-action="open-command" data-id="${attr(d.warning.command_id || '')}">指令 ${esc(d.warning.command_id || '未知')}</button></div>` : ''}
    ${artifactsOf(d).length ? `<div class="row wrap">${artifactsOf(d).map(a => `<button type="button" class="chip plain" data-action="open-file" data-path="${attr(a.path)}">${esc(ARTIFACT_KIND[a.kind] || '件')} ${esc(a.title || a.path.split('/').pop())}</button>`).join('')}</div>` : ''}
    ${(d.risks || []).length ? `<details class="drisk"><summary>${icon('warn')}风险 ${d.risks.length} 条</summary><ul class="dbody drisk-list">${d.risks.map(r => `<li>${esc(r)}</li>`).join('')}</ul></details>` : ''}
    ${d.merge_path ? `<details data-merge="${attr(d.merge_path)}" data-ver="${attr(d.updated || '')}" data-task="${attr(d.task_id)}"><summary>合并稿 · 最终方案</summary><div class="dbody md" data-merge-body data-keep data-keep-id="mb:${attr(d.task_id)}"><div class="sk" style="width:60%"></div><div class="sk" style="width:90%;margin-top:8px"></div><div class="sk" style="width:75%;margin-top:8px"></div></div></details>` : ''}
    <div class="row wrap">${d.status === 'merged' ? `<button type="button" class="small primary" data-action="adopt" data-id="${attr(d.task_id)}">${icon('check')}采纳</button><button type="button" class="small danger" data-action="reject" data-id="${attr(d.task_id)}">驳回</button>` : ''}<button type="button" class="small" data-action="open-decision" data-id="${attr(d.task_id)}">详情与编辑</button><button type="button" class="small ghost" data-action="followup" data-id="${attr(d.task_id)}">继续讨论</button><button type="button" class="small ghost" data-action="open-task" data-id="${attr(d.task_id)}">原始回答</button></div>
  </article>`;
}

