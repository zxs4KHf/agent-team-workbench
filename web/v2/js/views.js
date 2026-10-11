// 视图：概览、进行中、决策、成果
import { esc, attr, icon, sparkline, ring, fmtDate, relTime, minutesSince } from './core.js';
import { S, F, project, decisions, activeDecisions, isUnbound, UNBOUND_HINT, runningDecisions, doneDecisions, agentList, onlineAgents, agentByName, projectPeers, displayName, titleOf, displayTitle, bucketOf, BUCKETS, threadsOf, childrenOf, artifactsOf, deliveredDecisions, weekDelivered, progressOf, stallInfo, countPending, STATUS, STATUS_TONE, EXEC_LABEL, PEER_STATE, ARTIFACT_KIND, orderedTasks, taskTier } from './model.js';


export { renderArtifacts } from './page-artifacts.js';
export { renderDecisions, introForCard } from './page-decisions.js';
export { ovTabs, waitForYouCard, BRIEF_KIND_LABEL, BRIEF_ALERT_DEFAULT, briefAlertRatio, briefStats, briefHealthSection, pendingDecisionItems, dnDecidedLine, dnAnswerLine, dnForm, decisionsNeededSection, renderOverview, implChip, implActions } from './page-overview.js';
export { renderRunning, taskListHtml, runningMeta, toggleStuck } from './page-running.js';

export const chip = (status) => `<span class="chip ${STATUS_TONE[status] || ''}">${esc(STATUS[status] || status)}</span>`;
export const head = (kicker, title, desc, actions = '') => `<header class="view-head"><div><div class="eyebrow">${esc(kicker)}</div><h1>${esc(title)}</h1><p>${esc(desc)}</p></div><div class="view-actions">${actions}</div></header>`;
export const empty = (t, s = '', act = '') => `<div class="empty"><strong>${esc(t)}</strong>${s ? `<span>${esc(s)}</span>` : ''}${act}</div>`;
const initials = n => Array.from(String(n || '?')).slice(0, 2).join('').toUpperCase();

// 长列表分页（2026-10-02，用户：「弄几个分页让用户切换」）：每页 LIST_CAP 条，页码放 F.pages（本次会话内记住）。
// 以前是「显示其余 N 项」一次全展开，概览被拉到 12 屏长。
export const LIST_CAP = 8;
// 表格之类「分页条不能和行放在一起」的地方用 pageParts：{ rows: 当前页, pager: 翻页条 HTML（一页放得下时为空） }
export function pageParts(key, items, cap = LIST_CAP) {
  if (items.length <= cap) return { rows: items, pager: '' };
  F.pages = F.pages || {};
  const pages = Math.ceil(items.length / cap), pg = Math.min(Math.max(0, F.pages[key] || 0), pages - 1);
  return { rows: items.slice(pg * cap, pg * cap + cap), pager: pagerHtml(key, pg, pages, items.length) };
}
export function capped(key, items, render, cap = LIST_CAP) {
  const { rows, pager } = pageParts(key, items, cap);
  return rows.map(render).join('') + pager;
}
// 翻页条（2026-10-11 用户：「这个选项会跑」「没有直接输入页码跳页」）。原来「上一页 · 第 x/y 页 · 下一页」挤在中间、跟着列表走，
// 每页内容高矮不同，按钮就上下左右挪位，连点下一页会点空。现在：上一页贴左、下一页贴右；中间页码格数固定——不超过 7 页全列，
// 多了固定 7 格（首页、…、当前页前后、…、末页），翻页时格子不增不减；超过 7 页再给「跳到第 __ 页」输入框（回车或离开即跳）。
// 贴面板底由 components.css「分页条贴底」负责。任务历史（游标分页）也用这一套：act / idAttr / jumpAttr 换成它自己的动作。
export function pageSlots(pg, pages) {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i);
  if (pg <= 3) return [0, 1, 2, 3, 4, -1, pages - 1];
  if (pg >= pages - 4) return [0, -1, pages - 5, pages - 4, pages - 3, pages - 2, pages - 1];
  return [0, -1, pg - 1, pg, pg + 1, -1, pages - 1];
}
export function pagerNav({ pg, pages, status, act, idAttr = '', jumpAttr = '', key = '', extra = '', atEnd = pg >= pages - 1 }) {
  const ghost = 'small ghost', step = (dir, label, off) => `<button type="button" class="${ghost}" data-action="${act}"${idAttr} data-to="${pg + dir}" data-dir="${dir}" ${off ? 'disabled aria-disabled="true"' : ''}>${label}</button>`;
  const nums = pageSlots(pg, pages).map(i => i < 0 ? '<span class="pg-gap" aria-hidden="true">…</span>'
    : i === pg ? `<span class="pg-num on" aria-current="page">${i + 1}</span>`
      : `<button type="button" class="${ghost} pg-num" data-action="${act}"${idAttr} data-to="${i}" aria-label="第 ${i + 1} 页">${i + 1}</button>`).join('');
  const jump = pages > 7 ? `<label class="pg-jump">跳到 <input type="number" min="1" max="${pages}" step="1" inputmode="numeric" placeholder="${pg + 1}"${jumpAttr} aria-label="跳到第几页（1–${pages}）"> 页</label>` : '';
  return `<nav class="pager" aria-label="分页"${key ? ` data-key="${key}"` : ''}><span class="pg-side">${step(-1, '上一页', pg <= 0)}<span class="pg-status faint" aria-live="polite"${act === 'hist-page' ? ' data-hist-pagestatus' : ''}>${status}</span></span>`
    + `<span class="pg-nums">${nums}</span><span class="pg-side end">${extra}${jump}${step(1, '下一页', atEnd)}</span></nav>`;
}
function pagerHtml(key, pg, pages, total) {
  return pagerNav({ pg, pages, status: `第 ${pg + 1} / ${pages} 页 · 共 ${total} 项`, act: 'page', idAttr: ` data-id="${attr(key)}"`, jumpAttr: ` data-page-jump="${attr(key)}"`, key: `pager:${attr(key)}` });
}

// ===== 概览重排（2026-10-02，用户：「往下拉特别长」「提高信息密度」「加分页」）=====
// 四个长列表收进一个标签页面板；默认打开第一个有内容的标签，用户点过的记住（本机）
const OV_TAB_KEY = 'wb.ovTab';
// 2026-10-02 用户：「所有操作都能在一页内看到」。左右两栏各是一个标签面板，高度锁在窗口内（fitOverview），
// 面板内容多了在栏内滚动，分页条贴在栏底。group 区分左栏（main）与右栏（side），各记各的标签。
export const ovTabKey = group => group === 'main' ? OV_TAB_KEY : `${OV_TAB_KEY}.${group}`;
export function setOvTab(key, group = 'main') { try { localStorage.setItem(ovTabKey(group), key); } catch { /* 存储不可用：本次不记 */ } }
function historySeries(key) { return (S().history || []).map(h => h[key] || 0).slice(-24); }

// fb-b35df17376：概览「等你决定」行动卡的文案只讲「发生了什么 + 你下一步做什么」。
// 站内流程词（合并稿 / 核验 / 退回 / 决策）不许写回这里，需要改文案时同步 tests/smoke_wait_copy_b35d.mjs。
export const WAIT_FOR_YOU_COPY = { body: 'Agent 已把方案整理好了。打开看看：满意就采纳，还要改就继续讨论。', label: '打开看看' };
// 决策 20260929-181437-30f6：卡面简介 brief 是唯一写给用户的一句话。
// 缺失时按卡片类型显示固定兜底句，绝不留白、不显示状态词或编号；口径与 917c 一致（方案卡直接复用 WAIT_FOR_YOU_COPY.body）。
// 改文案要同步 tests/smoke_brief_30f6.mjs。
export const BRIEF_FALLBACK = {
  plan: WAIT_FOR_YOU_COPY.body,
  review: '机器已经检查完，打开看结论。',
  artifact: '东西已经做好并通过检查，等接进界面。',
};
// 类型只认结构化字段：审查单看 mode，交付物看有没有登记 artifacts，其余都是方案卡。不解析标题、不猜。
export const briefKindOf = d => (d && d.mode === 'review') ? 'review'  /* 审查单只看 mode（astra2 85c1 RETURN：kind 不在契约里）*/ : (artifactsOf(d).length ? 'artifact' : 'plan');
export const briefOf = (d, kind) => String((d && d.brief) || '').trim() || BRIEF_FALLBACK[kind || briefKindOf(d)];

// 卡片上的「看不懂」：点一下，工作台给 Leader 排一条重写指令（带任务号和卡面这句话）；排上以后卡片标「待重写」，Leader 重写后自动消失。
// kind 只在调用方显示的是强制类型的兜底句时才传（成果页的交付物卡），保证带上去的原文就是用户眼前那句
export function briefFeedback(d, slug, kind) {
  if (d && d.brief_rewrite) return `<div class="row wrap" data-brief-feedback><span class="chip warn" data-brief-rewrite>待重写</span><span class="faint">已请 Leader 重写这句简介，重写后这里会更新</span></div>`;
  return `<div class="row wrap" data-brief-feedback><button type="button" class="small ghost" data-action="brief-confusing" data-id="${attr(d.task_id)}" data-project="${attr(slug || '')}" data-shown="${attr(briefOf(d, kind))}" data-tip="这句话没看懂？点一下，让 Leader 重写">看不懂</button></div>`;
}

export function dnChoiceLabel(it) {
  const key = (it.answer && it.answer.choice) || (it.decided && it.decided.choice) || '';
  if (!key) return '没选项，只给了补充说明';
  const o = (it.options || []).find(x => x.key === key);
  return o ? `${o.key} · ${o.label}` : key;
}
// 卡面简介（intro）。口径必须与后端 validate_intro 一致：空 = 没写，不是「写了空话」。
// 回退顺序：本条 intro → 父任务 intro（带「第 N/M 步」）→ briefOf(d)（30f6 已落地的兜底）。
// **本条与父 intro 都为空时回退 briefOf，而不是直接显示「还没写清楚」**——那句话是 briefOf
// 自己的兜底，不该在它之前抢答（本单点名要改的地方）。
export const chainStepOf = d => {
  const all = decisions(), rootId = (d.lineage || {}).root_task || d.parent_task || d.task_id;
  const members = all.filter(x => ((x.lineage || {}).root_task || x.parent_task || x.task_id) === rootId);
  const ordered = members.length ? members : [d];
  const step = Math.max(0, ordered.findIndex(x => x.task_id === d.task_id)) + 1;
  return { step, total: ordered.length, rootId };
};
export const chainMetaOf = d => {
  const { step, total, rootId } = chainStepOf(d);
  const root = decisions().find(x => x.task_id === rootId) || d;
  return `${displayTitle(root)} · 第 ${step}/${total} 步`;
};
export function artifactCard(d, proj, mini = false) {
  const list = artifactsOf(d);
  return `<article class="card artifact hover" id="artifact-${attr(d.task_id)}" data-key="${attr(d.task_id)}"><div class="row"><span class="kind">${esc(ARTIFACT_KIND[list[0]?.kind] || '件')}</span><b class="grow truncate">${esc(titleOf(d))}</b>${!mini ? `<button type="button" class="ghost icon" data-action="copy-artifact-paths" data-id="${attr(d.task_id)}" data-project="${attr(proj.slug)}" aria-label="复制 Windows 路径" data-tip="复制路径">${icon('copy')}</button>` : ''}</div>
    <p class="summary">${esc(briefOf(d, 'artifact'))}</p>
    ${mini ? '' : briefFeedback(d, proj.slug, 'artifact')}
    ${list.slice(0, mini ? 2 : 6).map(a => `<button type="button" class="link path truncate" style="text-align:left" data-action="open-file" data-path="${attr(a.path)}">${esc(a.path)}</button>`).join('')}
    <div class="row" style="font-size:calc(11px * var(--fs-scale, 1));color:var(--faint)"><span>${esc(proj.name || proj.slug)}</span><span>·</span><span>${esc(fmtDate(d.updated || d.created))}</span>${list.length > 2 && mini ? `<span>· 共 ${list.length} 件</span>` : ''}</div></article>`;
}
