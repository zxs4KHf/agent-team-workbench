// page-overview.js：从 views.js 拆出（ADR 0015，按页面拆模块；scripts/split_module.py 生成，函数体未改）
// 视图：概览、进行中、决策、成果
import { esc, attr, icon, sparkline, ring, fmtDate, relTime, minutesSince } from './core.js';
import { S, F, project, decisions, activeDecisions, isUnbound, UNBOUND_HINT, runningDecisions, unboundTasks, doneDecisions, agentList, onlineAgents, agentByName, projectPeers, displayName, titleOf, displayTitle, bucketOf, BUCKETS, threadsOf, childrenOf, artifactsOf, deliveredDecisions, weekDelivered, progressOf, stallInfo, countPending, STATUS, STATUS_TONE, EXEC_LABEL, PEER_STATE, ARTIFACT_KIND, orderedTasks, taskTier } from './model.js';

import { BRIEF_FALLBACK, WAIT_FOR_YOU_COPY, artifactCard, briefKindOf, capped, chip, empty, head, ovTabKey } from './views.js';

// 进行中任务的「谁在做」（反馈 fb-2154160fae）：后端 task_progress() 已算好 states/ages 并随 /api/state 下发，
// 这里只做渲染，不新增数据请求。
const PEER_TONE = { answered: 'ok', working: 'violet', stalled: 'warn', queued: 'info', expired: 'bad', missing: 'plain' };
export function peerStateChips(states) {
  const es = Object.entries(states || {});
  if (!es.length) return '';
  return es.map(([pe, st]) => `<span class="chip ${PEER_TONE[st] || 'plain'}">${esc(displayName(pe))}·${esc(PEER_STATE[st] || st)}</span>`).join('');
}

export function ovTabs(tabs, group = 'main') {
  let want = ''; try { want = localStorage.getItem(ovTabKey(group)) || ''; } catch { /* 存储不可用 */ }
  // 右栏：简介兜底句超过提醒线时默认打开「简介体检」，提醒不被收起来
  const pref = group === 'main' ? ['decide', 'focus', 'landing', 'master'] : ['brief'];
  const cur = tabs.find(x => x.key === want) || pref.map(k => tabs.find(x => x.key === k && x.n)).find(Boolean) || tabs[0];
  return `<div class="segmented ov-tabs" role="tablist">${tabs.map(x => `<button type="button" role="tab" class="${x.key === cur.key ? 'active' : ''}" aria-selected="${x.key === cur.key}" data-action="ov-tab" data-group="${group}" data-tab="${x.key}">${esc(x.label)}${x.n ? ` <span class="n">${x.n}</span>` : ''}</button>`).join('')}</div>
    ${tabs.map(x => `<div class="ov-panel" role="tabpanel" data-key="ov-panel:${x.key}"${x.key === cur.key ? '' : ' hidden'}>${x.html}</div>`).join('')}`;
}
function decideCount(p) { return pendingDecisionItems(p).filter(x => !x.it.decided && !x.it.answer).length; }

// 2026-10-02 用户：「全部都是这句话，没有意义」。第二行改成这张卡自己的简介（brief，写入时已校验不含术语）；
// 没写简介就只写事实：多久前整理好、几份回答到齐。WAIT_FOR_YOU_COPY.body 留作空态与说明用。
export const waitForYouCard = d => {
  const pr = progressOf(d), brief = String(d.brief || '').trim();
  const facts = `${relTime(d.updated || d.created)}整理好${pr && pr.expected ? ` · ${pr.answered}/${pr.expected} 份回答` : ''}`;
  return { kind: 'violet', title: titleOf(d), body: brief || facts, action: 'open-decision', id: d.task_id, label: WAIT_FOR_YOU_COPY.label };
};

// 决策 20260929-181437-30f6 第 5 / 6 步：概览「简介体检」+ 卡片「看不懂」反馈闸。
// 口径只有一处：卡片类型与兜底句仍由上面的 briefKindOf / BRIEF_FALLBACK 决定，看板的数直接从它们算，不另写一份规则。
// 不用内联样式 / 新字号 / 新按钮样式组合：tests/design_ratchet.json 的上限只降不升。
export const BRIEF_KIND_LABEL = { plan: '方案卡', review: '审查卡', artifact: '交付物卡' };
export const BRIEF_ALERT_DEFAULT = 0.5;
// 提醒线由服务端下发（settings.json 的 brief_alert_ratio）；缺失 / 不是数 / 越界一律回到默认，看板不会因配置写坏而挂
export const briefAlertRatio = (s = S()) => { const v = s && s.brief_alert_ratio; return typeof v === 'number' && v >= 0 && v <= 1 ? v : BRIEF_ALERT_DEFAULT; };
// 统计对象 = 决策页上的卡（终态决策）里带 brief 槽位的新记录：老记录没有槽位，只走兜底句，不计入占比，也不倒灌
export function briefStats(p = project(), ratio = briefAlertRatio()) {
  const cards = doneDecisions(p), fresh = cards.filter(d => d && Object.prototype.hasOwnProperty.call(d, 'brief'));
  const missing = fresh.filter(d => !String(d.brief || '').trim());
  const fallback = Object.fromEntries(Object.keys(BRIEF_FALLBACK).map(k => [k, 0]));
  missing.forEach(d => { fallback[briefKindOf(d)] += 1; });
  const share = fresh.length ? missing.length / fresh.length : 0;
  return { total: fresh.length, written: fresh.length - missing.length, missing: missing.length, legacy: cards.length - fresh.length, fallback, share, threshold: ratio, alert: fresh.length > 0 && share > ratio, rewrite: cards.filter(d => d && d.brief_rewrite).length };
}
export function briefHealthSection(p) {
  const st = briefStats(p), pct = n => `${Math.round(n * 100)}%`;
  const rows = Object.keys(BRIEF_FALLBACK).map(k => `<div class="row" data-brief-kind="${attr(k)}"><b class="num">${st.fallback[k]}</b><span class="grow clamp">次 · ${esc(BRIEF_KIND_LABEL[k])}：${esc(BRIEF_FALLBACK[k])}</span></div>`).join('');
  const alert = st.alert ? `<div class="banner" role="status" data-brief-alert>${icon('warn')}<span>兜底句占 ${pct(st.share)}，超过提醒线 ${pct(st.threshold)}：卡片读起来会千篇一律，请 Leader 给这些卡补写一句人话。</span></div>` : '';
  const note = ['只统计带简介槽位的新卡', st.legacy ? `另有 ${st.legacy} 张老卡只走兜底句，不计入占比` : '', st.rewrite ? `${st.rewrite} 张在等 Leader 重写` : '', `提醒线 ${pct(st.threshold)}`].filter(Boolean).join('；');
  return `<section class="card pad" data-brief-health><div class="section-head"><h2>简介体检</h2><span class="hint">${st.total ? `新卡 ${st.total} 张 · 缺简介 ${st.missing}（${pct(st.share)}）` : '还没有新卡'}</span></div>${alert}<div class="stack">${rows}</div><p class="faint">${esc(note)}。</p></section>`;
}
// fb-486f8ac59c：Leader 能定的自己定（只读 + 一句理由），定不了的给 2–4 条可点选项。
// 硬要求：内联表单，就地提交，**不开新抽屉、不跳页**。数据只来自结构化字段 decision.decisions_needed，
// 绝不去解析 summary / next 的自然语言猜选项。没有该字段的旧决策完全不受影响。
export function pendingDecisionItems(p) {
  const out = [];
  decisions(p).forEach(d => {
    const list = Array.isArray(d.decisions_needed) ? d.decisions_needed : [];
    list.forEach(it => { if (it && it.id) out.push({ d, it }); });
  });
  return out;
}
const dnKey = (d, it) => `${d.task_id}/${it.id}`;
const dnOption = (d, it, o) => `<label class="fchip" style="cursor:pointer"><input type="radio" name="dn-${attr(dnKey(d, it))}" value="${attr(o.key)}" class="sr-only"><span>${esc(o.label)}</span>${o.detail ? `<small class="faint" style="margin-left:6px">${esc(o.detail)}</small>` : ''}</label>`;
// 已回复：原地变成「已回复：<所选项> + <补充>」，不是弹一个成功提示就没了
// 931d：Leader 定的与用户答的两行并存；回答显示送达状态；题面改过要提示；可以改答 / 推翻 Leader 的决定
const dnWhat = (it, key, none) => { if (!key) return none; const o = (it.options || []).find(x => x.key === key); return o ? `${o.key} · ${o.label}` : key; };
export const dnDecidedLine = it => it.decided ? `<div class="row wrap"><span class="chip info">Leader 定的：${esc(dnWhat(it, it.decided.choice, '（未选具体项）'))}</span></div>${it.decided.reason ? `<span class="help">理由：${esc(it.decided.reason)}</span>` : ''}` : '';
export const dnAnswerLine = it => {
  const a = it.answer; if (!a) return '';
  const sent = a.acked_at ? '<span class="chip ok dn-sent">Leader 已收到</span>' : '<span class="chip warn dn-sent" title="回答已存进工作台；Leader 下一轮等待时会收到，Leader 确认后这里会变绿">等 Leader 确认</span>';
  const changed = a.question && a.question !== it.question ? `<span class="help">你回答时的题目是「${esc(a.question)}」，Leader 之后改过题面</span>` : '';
  const what = a.choice && a.choice_label ? `${a.choice} · ${a.choice_label}` : dnWhat(it, a.choice, '没选项，只给了补充说明');
  const curOpt = a.choice ? (it.options || []).find(o => o.key === a.choice) : null;
  const relabeled = !a.choice || !a.choice_label ? '' : !curOpt ? '<span class="help">你选的这个选项后来被 Leader 删除了</span>'
    : curOpt.label !== a.choice_label ? `<span class="help">这个选项后来被 Leader 改成了「${esc(curOpt.label)}」</span>` : '';
  return `<div class="row wrap"><span class="chip ok">你的回答：${esc(what)}</span>${sent}</div>${a.text ? `<div class="q dn-text">补充：${esc(a.text)}</div>` : ''}${changed}${relabeled}`;
};
const dnHead = ({ d, it }) => `<div class="min0"><strong class="clamp">${esc(it.question)}</strong><span class="faint" style="font-size:calc(12px * var(--fs-scale, 1))">${esc(titleOf(d))}${it.retired ? ' · Leader 已撤下此题，回答已存档' : ''}</span></div>`;
// data-keep：SSE 每两秒重绘，没有它展开的面板会被收起、打到一半的字会丢
const dnReanswer = (x, slug, label) => `<details class="dn-more" data-keep data-keep-id="dnr:${attr(dnKey(x.d, x.it))}"><summary class="link">${esc(label)}</summary>${dnForm(x, slug, '提交', true)}</details>`;
const dnAnswered = (x, slug) => `<div class="card pad stack dn-item dn-answered" data-dn="${attr(dnKey(x.d, x.it))}" style="background:var(--surface-2)">${dnHead(x)}${dnDecidedLine(x.it)}${dnAnswerLine(x.it)}${x.it.retired ? '' : dnReanswer(x, slug, '改答')}</div>`;
const dnDecided = (x, slug) => `<div class="card pad stack dn-item dn-decided" data-dn="${attr(dnKey(x.d, x.it))}" style="background:var(--surface-2)">${dnHead(x)}${dnDecidedLine(x.it)}${dnReanswer(x, slug, '不同意？写下你的意见')}</div>`;
// data-keep：SSE 每两秒重绘一次，没有它用户打到一半的补充说明会被 morph 抹掉
export const dnForm = ({ d, it }, slug, submitLabel = '提交', compact = false) => `<form class="${compact ? 'stack dn-reanswer' : 'card pad stack dn-item'}" data-form="decision-answer" data-keep data-keep-id="dn:${attr(dnKey(d, it))}" data-project="${attr(slug)}" data-task="${attr(d.task_id)}" data-item="${attr(it.id)}" style="${compact ? 'margin-top:8px' : 'background:var(--surface-2)'}">
  ${compact ? '' : dnHead({ d, it })}
  ${(it.options || []).length ? `<div class="row wrap" style="gap:8px">${it.options.map(o => dnOption(d, it, o)).join('')}</div>` : ''}
  <div class="field"><label for="dnText-${attr(dnKey(d, it))}">补充说明</label><textarea id="dnText-${attr(dnKey(d, it))}" name="text" rows="2" placeholder="也可以一个选项都不选，直接写你的指示"></textarea></div>
  <div class="row"><button type="submit" class="primary small">${esc(submitLabel)}</button><span class="muted dn-picked" data-dn-picked aria-live="polite">先点一个选项或写补充说明，再点「${esc(submitLabel)}」· 就地提交，不会打开新窗口</span></div>
</form>`;
export function decisionsNeededSection(p, inTab = false) {
  const all = pendingDecisionItems(p);
  const waiting = all.filter(x => !x.it.decided);
  const decided = all.filter(x => x.it.decided);
  if (!all.length) return '';
  const askBlock = waiting.length ? `<section class="card pad"><div class="section-head"><h2>等你拍板</h2><span class="hint">${waiting.filter(x => !x.it.answer).length ? `${waiting.filter(x => !x.it.answer).length} 项` : '都回完了'}</span></div><div class="stack" id="decideList">${waiting.map(x => x.it.answer ? dnAnswered(x, p.slug) : dnForm(x, p.slug)).join('')}</div></section>` : '';
  const doneBlock = decided.length ? `<section class="card pad"><div class="section-head"><h2>Leader 已替你定</h2><span class="hint">${decided.length} 项 · 只读</span></div><div class="stack" id="decidedList">${decided.map(x => x.it.answer ? dnAnswered(x, p.slug) : dnDecided(x, p.slug)).join('')}</div></section>` : '';
  if (!inTab) return askBlock + doneBlock;
  // 概览标签页里：待答的问题放大字号、分页；Leader 已替你定的（只读）默认收起，点一下展开
  const ask = waiting.length ? `<section class="dn-ask"><div class="stack dn-big" id="decideList">${capped('decide', waiting, x => x.it.answer ? dnAnswered(x, p.slug) : dnForm(x, p.slug), 3)}</div></section>` : '<p class="faint">都回完了。</p>';
  const done = decided.length ? `<section class="dn-done"><h3 class="ov-sub">Leader 已替你定 <span class="n">${decided.length}</span><span class="faint">（只读，不同意可以改）</span></h3><div class="stack" id="decidedList">${capped('decided', decided, x => x.it.answer ? dnAnswered(x, p.slug) : dnDecided(x, p.slug))}</div></section>` : '';
  return ask + done;
}

// 首页健康条（2026-10-02）：系统现在能不能干活——真正在等任务的人、假在线、被罚下的、Leader、各段积压。一眼看出「为什么没人干活」
function healthStrip() {
  const s = S(), ag = agentList(s);
  const online = ag.filter(a => a.online), waiting = ag.filter(a => a.listening);
  const fake = online.filter(a => a.activity === 'idle' && a.listening === false), benched = ag.filter(a => a.benched);
  const leaders = Array.isArray(s.leaders) ? s.leaders.length : 0;
  const b = (s.allocator && s.allocator.last_plan && s.allocator.last_plan.backlog) || null;
  // 反馈 fb-2154160fae：概览要回答「几个任务在跑 / 几个人在干活」，不能只给「在等任务」的人数
  const runList = runningDecisions(project());
  const workingPeers = new Set();
  runList.forEach(d => { const st = progressOf(d).states || {}; Object.entries(st).forEach(([pe, v]) => { if (v === 'working' || v === 'stalled') workingPeers.add(pe); }); });
  const item = (n, label, tone, attrs, tip) => `<button type="button" class="nb-item ${n ? tone : 'zero'}" ${attrs}${tip ? ` data-tip="${attr(tip)}"` : ''}><b>${esc(String(n))}</b>${esc(label)}</button>`;
  const items = [
    `<button type="button" class="nb-item ${waiting.length ? 'ok' : 'danger'}" data-action="view" data-view="peers" data-tip="正在调用 bus_wait_task、派过去马上有人领的 Agent / 显示在线的 Agent"><b>${waiting.length}/${online.length}</b>在等任务 / 在线</button>`,
    item(fake.length, '在线但没在等任务', 'warn', 'data-action="view" data-view="peers"', '心跳在，但没在等任务：派给它的单不会有人领，30 分钟后系统会收走改派'),
    item(benched.length, '暂停派单', 'danger', 'data-action="view" data-view="peers"', '24 小时内两次领了任务不干活，交出一次回复后恢复'),
    item(leaders, 'Leader 在线', leaders ? 'ok' : 'danger', 'data-action="view" data-view="peers"', ''),
    item(runList.length, '进行中任务', 'violet', 'data-action="view" data-view="running"', '本项目正在跑的任务数：回答没到齐、还没合并的都算'),
    item(workingPeers.size, '正在作答', 'ok', 'data-action="view" data-view="peers"', '已领取任务、正在作答或超时未回的 Agent（按人去重）'),
    b && item(b.review || 0, '待审', 'violet', 'data-action="view" data-view="running"', '已提交、还没人审的施工'),
    b && item(b.build || 0, '施工没人做', 'warn', 'data-action="view" data-view="running"', '施工单压在离线 / 不干活的人手上'),
    b && item(b.answer || 0, '回答没人做', 'warn', 'data-action="view" data-view="running"', '讨论 / 独立作答的名额压在离线 / 不干活的人手上')
  ].filter(Boolean);
  return `<nav class="nextbar healthbar" aria-label="系统健康" data-key="healthbar">${items.join('')}</nav>`;
}

export function renderOverview(p) {
  const active = activeDecisions(p);
  const userWait = active.filter(d => d.status === 'merged');
  const masterWait = active.filter(d => d.status === 'ready_for_merge');
  const running = runningDecisions(p);
  const queued = (S().commands || []).filter(c => !(c.payload && c.payload.project) || c.payload.project === p.slug);
  const focus = [];
  userWait.forEach(d => focus.push(waitForYouCard(d)));
  // b342：ack 没能把占位换成真实 task_id 的派发，既不是取消也不是进行中，必须在概览里明确告知而不是消失
  active.filter(isUnbound).forEach(d => focus.push({ kind: 'warn', title: titleOf(d), body: `${UNBOUND_HINT}（指令 ${d.warning.command_id || '未知'}）`, action: 'open-command', id: d.warning.command_id || '', label: '查看指令' }));
  running.forEach(d => { const st = stallInfo(d); if (st.terminal) focus.push({ kind: 'bad', title: titleOf(d), body: `${st.text}${st.suggest ? '，建议' + st.suggest : ''}`, action: 'open-task', id: d.task_id, label: '查看原因' }); });
  // 903f：采纳项按服务端 bucket 分四页（agent / user / decide / done）；不再依赖 activeDecisions 的隐藏 / 归档过滤，archived 之前采纳的也能看到
  const buckets = { agent: [], user: [], decide: [], done: [] };
  decisions(p).forEach(d => { const b = bucketOf(d); if (buckets[b]) buckets[b].push(d); });
  const landing = buckets.agent;
  const master = [];
  masterWait.forEach(d => master.push({ kind: 'warn', title: titleOf(d), body: d.merge_path ? '草稿待核验，核验后开放采纳' : `${progressOf(d).answered}/${progressOf(d).expected} 回答到齐，等待 Leader 生成合并稿`, action: d.merge_path ? 'open-decision' : 'open-task', id: d.task_id, label: d.merge_path ? '查看草稿' : '查看进度' }));
  queued.forEach(c => master.push({ kind: 'info', title: `${c.kind || '工作台'} 指令${c.status === 'processing' ? '处理中' : '排队中'}`, body: (c.payload && (c.payload.question || c.payload.title || (c.payload.task_id ? '关联任务 ' + c.payload.task_id : ''))) || '等待 Leader 领取', action: 'open-command', id: c.id, label: '查看指令' }));
  const recent = doneDecisions(p).filter(d => ['adopted', 'archived', 'merged'].includes(d.status)).slice(0, 4);
  const next = Array.isArray(p.next) ? p.next.slice(0, 6) : [];
  const team = projectPeers(p), teamOnline = team.filter(n => agentByName(n).online);
  const latestNote = Array.isArray(p.notes) && p.notes.length ? p.notes[p.notes.length - 1].text : (p.note || '');
  const metric = (label, value, note, view, series, hot = false) => `<button type="button" class="metric ${hot && value ? 'hot' : ''}" data-key="metric-${view}" data-action="view" data-view="${view}"><span>${esc(label)}</span><b class="num" data-count="${value}">${value}</b><small>${esc(note)}</small>${series ? sparkline(series) : ''}</button>`;
  const focusItem = it => `<button type="button" class="focus-item ${it.kind}" data-key="${attr(it.action + ':' + it.id)}" data-action="${it.action}" data-id="${attr(it.id)}"><i></i><div class="min0"><strong class="truncate">${esc(it.title)}</strong><span class="clamp">${esc(it.body)}</span></div><span class="chip plain">${esc(it.label)} ${icon('chevron')}</span></button>`;
  const delivered = deliveredDecisions('project').slice(0, 3);
  // 行动条（ROADMAP「首页下一步」）：跨页面的待办计数，一键直达。收件箱 / 反馈属扩展层：S().ext 由 hub_badge.js 广播，扩展关闭或接口失败时为 null，这两项直接不显示
  const ext = S().ext, stuck = running.filter(d => stallInfo(d).terminal);
  // 「待拍板」= 等你采纳的合并稿 + 等你回答的选择题（同一条决策不重复计）。原来只数前者：选择题挂在一张待核验的合并稿上时，
  // 顶部显示「0 待拍板 · ✓ 都处理完了」，下面面板却是「等你拍板 1」（2026-10-10 README 截图里发现）
  const asks = pendingDecisionItems(p).filter(x => !x.it.decided && !x.it.answer);
  const askIds = new Set(asks.map(x => x.d.task_id));
  const toDecide = asks.length + userWait.filter(d => !askIds.has(d.task_id)).length;
  const unb = unboundTasks();
  const nb = [
    { n: toDecide, label: '待拍板', tone: 'violet', attrs: asks.length ? 'data-action="ov-tab" data-tab="decide" data-group="main"'
      : userWait.length === 1 ? `data-action="open-decision" data-id="${attr(userWait[0].task_id)}"` : 'data-action="goto-status" data-view="decisions" data-status="merged"' },
    ext && ext.inbox != null && { n: ext.inbox, label: '收件箱', tone: 'info', href: '/hub#inbox' },
    ext && ext.feedback != null && { n: ext.feedback, label: '未处理反馈', tone: 'warn', href: '/hub#feedback' },
    { n: stuck.length, label: '卡住的施工', tone: 'danger', attrs: stuck.length ? 'data-action="view" data-view="running" data-filter-stuck="on"' : 'data-action="view" data-view="running"' },
    unb.length ? { n: unb.length, label: '未归属', tone: 'warn', attrs: 'data-action="view" data-view="running"' } : null
  ].filter(Boolean);
  const nbItem = it => { const cls = `nb-item ${it.n ? it.tone : 'zero'}`, inner = `<b>${it.n}</b>${esc(it.label)}`; return it.href ? `<a class="${cls}" href="${it.href}">${inner}</a>` : `<button type="button" class="${cls}" ${it.attrs}>${inner}</button>`; };
  const nextbar = `<nav class="nextbar" aria-label="需要你处理的事" data-key="nextbar">${nb.some(x => x.n) ? '' : '<span class="nb-clear">✓ 都处理完了</span>'}${nb.map(nbItem).join('')}</nav>`;
  return `<div class="view">${head('当前项目', '概览', '只看现在需要你做什么、最近定了什么，以及下一步。', `<button type="button" class="small ghost" data-action="edit-project" data-tip="默认团队、默认模式与上下文文件">${icon('settings')}项目配置</button>`)}
    ${healthStrip()}
    ${nextbar}
    <div class="grid-2 ov-grid">
      <section class="card pad ov-main">${ovTabs([
        { key: 'focus', label: '现在等我', n: focus.length, html: `<section class="ov-focus">${focus.length ? `<div class="focus stagger" id="focusList">${capped('focus', focus, focusItem)}</div>` : empty('没有需要你处理的事项', 'Agent 把方案整理好后会出现在这里')}</section>` },
        { key: 'decide', label: '等你拍板', n: decideCount(p), html: decisionsNeededSection(p, true) || empty('没有要你拍板的问题', 'Leader 定不了的取舍会出现在这里') },
        { key: 'landing', label: '已采纳·待落地', n: buckets.agent.length + buckets.user.length + buckets.decide.length, html: landingSection(buckets, true) },
        { key: 'master', label: '等 Leader', n: master.length, html: master.length ? `<div class="focus">${capped('master', master, focusItem)}</div>` : empty('Leader 队列为空') }
      ])}</section>
      <section class="card pad ov-side">${ovTabs([
        { key: 'running', label: '进行中', n: running.length, html: `<section class="ov-sec"><div class="section-head"><h2>进行中</h2><button type="button" class="link" data-action="view" data-view="running">查看全部</button></div>${running.length ? `<div class="stack">${running.slice(0, 4).map(d => { const pr = progressOf(d); return `<button type="button" class="row focus-item" style="grid-template-columns:auto minmax(0,1fr)" data-action="open-task" data-id="${attr(d.task_id)}">${ring(pr.answered / Math.max(1, pr.expected), `${pr.answered}/${pr.expected}`)}<div class="min0"><strong class="truncate">${esc(titleOf(d))}</strong><span class="truncate">${esc(stallInfo(d).text)}</span><div class="row wrap">${peerStateChips(pr.states)}</div></div></button>`; }).join('')}</div>` : empty('没有进行中的任务')}</section>` },
        { key: 'delivered', label: '最近交付', n: 0, html: `<section class="ov-sec"><div class="section-head"><h2>最近交付</h2><button type="button" class="link" data-action="view" data-view="artifacts">查看全部</button></div>${delivered.length ? `<div class="artifact-grid">${delivered.map(({ d }) => artifactCard(d, p, true)).join('')}</div>` : empty('还没有交付物', '采纳并登记交付物后显示')}</section>` },
        { key: 'recent', label: '最近定了', n: 0, html: `<section class="ov-sec"><div class="section-head"><h2>最近定了什么</h2><button type="button" class="link" data-action="view" data-view="decisions">查看全部</button></div>${recent.length ? `<div class="timeline">${recent.map(d => `<div class="tl ${STATUS_TONE[d.status] || ''}"><time>${esc(fmtDate(d.updated || d.created))} · ${esc(STATUS[d.status])}</time><button type="button" class="link" data-action="open-decision" data-id="${attr(d.task_id)}">${esc(titleOf(d))}</button>${d.summary ? `<div class="muted clamp" style="font-size:calc(12px * var(--fs-scale, 1))">${esc(d.summary)}</div>` : ''}</div>`).join('')}</div>` : empty('还没有完成的决策')}</section>` },
        { key: 'project', label: '项目', n: 0, html: `<section class="ov-sec"><div class="section-head"><h2>下一步</h2><button type="button" class="link" data-action="edit-plan">编辑计划</button></div>${next.length ? `<div class="stack" style="gap:6px">${next.map((item, i) => `<div class="row"><span class="avatar" style="width:22px;height:22px;font-size:calc(11px * var(--fs-scale, 1))">${i + 1}</span><span class="grow">${esc(item)}</span><button type="button" class="ghost small" data-action="plan" data-index="${i}" data-tip="交给 Leader 派发" aria-label="交给 Leader 派发：${attr(item)}">${icon('send')}</button></div>`).join('')}</div>` : empty('还没有下一步计划')}</section><section class="ov-sec"><div class="section-head"><h2>项目团队</h2><span class="row" style="gap:12px"><button type="button" class="link" data-action="edit-project" data-tip="默认团队、默认模式与上下文文件">项目配置</button><button type="button" class="link" data-action="edit-teams">编辑团队</button></span></div><div class="row wrap">${team.length ? team.map(n => `<span class="chip ${agentByName(n).online ? 'ok' : ''}">${esc(displayName(n))}</span>`).join('') : '<span class="muted">未设置团队，派发时取全部在线 Agent</span>'}</div><div class="muted" style="font-size:calc(12px * var(--fs-scale, 1));margin-top:8px">在线 ${teamOnline.length}/${team.length}</div></section><section class="ov-sec"><div class="section-head"><h2>备注</h2><span class="hint">点击文字，失焦保存</span></div><div class="inline-edit" contenteditable="true" data-inline="project" data-field="note" data-placeholder="记录一句当前判断…" data-keep data-keep-id="note:${attr(p.slug)}">${esc(latestNote)}</div></section>` },
        { key: 'brief', label: '简介', n: briefStats(p).alert ? briefStats(p).missing : 0, html: briefHealthSection(p) },
      ], 'side')}</section>
    </div>
  </div>`;
}

// 已采纳，等待落地：每条读服务端合成的 implementation（六态 + blocked），概览文案只写计数真值，不断言 Leader 在派
const IMPL_TONE = { unassigned: 'plain', queued: 'info', claimed: 'info', dispatched: 'warn', building: 'ok', review: 'violet', merged: 'ok' };
export function implChip(d) {
  const im = d.implementation; if (!im) return '';
  return `<span class="chip ${IMPL_TONE[im.state] || 'plain'}">${esc(im.label)}</span>${im.blocked ? '<span class="chip bad plain">无在线 builder</span>' : ''}`;
}
export function implActions(d, small = true) {
  const im = d.implementation || {}, cls = small ? 'small' : '';
  const canPush = !['building', 'review', 'merged'].includes(im.state);
  const pushLabel = im.state === 'queued' ? `已在队列 · 第 ${im.queue_pos} 位` : im.state === 'claimed' ? 'Leader 处理中' : im.state === 'dispatched' ? '已建单' : '派落地给 builder';
  return `<button type="button" class="${cls} ${canPush && !['queued', 'claimed', 'dispatched'].includes(im.state) ? 'primary' : 'ghost'}" data-action="implement-nudge" data-id="${attr(d.task_id)}" ${canPush ? '' : 'disabled'} data-tip="把这条 implement 直接推给 Leader；同一任务不会重复建单">${esc(pushLabel)}</button><button type="button" class="${cls} ghost" data-action="implement-direct" data-id="${attr(d.task_id)}" ${canPush && !['claimed', 'dispatched'].includes(im.state) ? '' : 'disabled'} data-tip="不等 Leader：按合并稿的最终方案开工作副本，直接派给一个正在等任务的施工 Agent；没人在等就留在 Leader 队列">直接派施工</button><button type="button" class="${cls} ghost" data-action="export-task-pack" data-id="${attr(d.task_id)}" data-tip="把最终方案、涉及文件、验收条导成 Markdown，给真人或外部会话施工">导出任务包</button>`;
}
// 落地推进器（bus_land）：不需要判断的自动推（审查通过的守护自动合并），需要判断的按类给批量按钮
const LAND_ACTS = {
  stale: [['rereview', '重新审查'], ['rework', '返修'], ['drop', '不再落地']],
  returned: [['rework', '重新派施工'], ['drop', '不再落地']],
  blocked: [['rework', '重新派施工'], ['drop', '不再落地']],
  empty: [['drop', 'main 已有，标记完成'], ['rework', '重新派施工']],
  closed: [['rework', '重新派施工'], ['drop', '不再落地']],
  stalled: [['rework', '换人重新施工'], ['drop', '不再落地']],
  abandoned: [['rework', '重新派施工'], ['drop', '不再落地']],
  unbuilt: [['direct', '直接派施工'], ['drop', '不再落地']],
};
function landActions() {
  const L = S().landing || {};
  const rows = Object.keys(LAND_ACTS).filter(k => L[k] && L[k].n).map(k => `<div class="land-row"><span class="land-label">${esc(L[k].label)} <b class="num">${L[k].n}</b></span><span class="row wrap">${LAND_ACTS[k].map(([a, l]) => `<button type="button" class="${a === 'drop' ? 'small ghost' : 'small'}" data-action="land-bulk" data-cat="${k}" data-act="${a}" data-n="${L[k].n}" data-label="${attr(L[k].label)}">${esc(l)}</button>`).join('')}</span></div>`).join('');
  const auto = L.merge_ready && L.merge_ready.n ? `<p class="faint land-auto">${L.merge_ready.n} 条审查通过的由守护自动合并（每圈最多 3 条，合并前跑集成门禁，失败的转到「合并受阻」）</p>` : '';
  return rows || auto ? `<section class="land-actions" data-key="land-actions"><h3>需要你定的落地事项</h3>${auto}${rows}</section>` : '';
}
function landingSection(buckets, bare = false) {
  const landing = buckets.agent;
  const st = k => landing.filter(d => d.implementation?.state === k);
  const toDispatch = st('unassigned').length + st('queued').length + st('claimed').length, waiting = st('dispatched'), building = st('building').length, review = st('review').length;
  const maxWait = Math.max(0, ...waiting.map(d => d.implementation.wait_sec || 0));
  const builders = S().builders || {}, noBuilder = builders.online_n === 0;
  // 「待审」只数真在等审查的（bus_land.reviewing）；退回 / 空交付 / 已关 / 过期在下面的批量区各自计数
  const realReview = (S().landing && S().landing.reviewing) ? S().landing.reviewing.n : review;
  const hint = `待派 ${toDispatch} · 等 builder ${waiting.length}${waiting.length ? `（最长 ${Math.floor(maxWait / 60)} 分钟）` : ''} · 在建 ${building} · 待审 ${realReview}`;
  const blocked = noBuilder && landing.some(d => d.implementation?.blocked) ? `<div class="banner" style="margin:0 0 8px"><span>无在线 builder，任务已入队。builder 组 ${(builders.groups || []).join('、') || '未配置'}：${(builders.members || []).length ? builders.members.map(m => `${displayName(m.peer)} ${m.online ? '在线' : `离线 ${relTime(m.last_seen)}`}`).join('、') : '没有登记成员'}</span><button type="button" class="small" data-action="join-peer">接入 builder</button></div>` : '';
  const view = d => `<button type="button" class="small ghost" data-action="open-decision" data-id="${attr(d.task_id)}">查看</button>`;
  const row = d => `<div class="focus-item" data-bucket-item="agent" style="grid-template-columns:minmax(0,1fr) auto"><div class="min0"><strong class="truncate">${esc(displayTitle(d))}</strong><span class="row wrap" style="gap:6px;margin-top:4px">${implChip(d)}</span></div><div class="row wrap" style="gap:6px;justify-content:flex-end">${implActions(d)}${view(d)}</div></div>`;
  const userRow = d => { const h = d.handoff || {}; return `<div class="focus-item" data-bucket-item="user" style="grid-template-columns:minmax(0,1fr) auto"><div class="min0"><strong class="truncate">${esc(displayTitle(d))}</strong><div style="font-size:calc(13px * var(--fs-scale, 1));margin-top:4px">你要做的一步：<b>${esc(h.user_step || '')}</b></div><div class="faint" style="font-size:calc(12px * var(--fs-scale, 1))">为什么：${esc(h.reason || '')} · 怎么验证：${esc(h.verify || '')}</div><div class="faint mono" style="font-size:calc(11px * var(--fs-scale, 1))">Agent 已备好（${esc(displayName(h.prepared_by || ''))} ${esc(relTime(h.prepared_at || ''))}）：${(h.evidence || []).map(esc).join('；')}</div></div><div class="row wrap" style="gap:6px;justify-content:flex-end"><button type="button" class="small primary" data-action="handoff-finish" data-id="${attr(d.task_id)}" data-result="done">已完成</button><button type="button" class="small ghost danger" data-action="handoff-finish" data-id="${attr(d.task_id)}" data-result="failed">失败</button>${view(d)}</div></div>`; };
  const decideRow = d => { const kids = childrenOf(d); return `<div class="focus-item" data-bucket-item="decide" style="grid-template-columns:minmax(0,1fr) auto"><div class="min0"><strong class="truncate">${esc(displayTitle(d))}</strong><div class="faint" style="font-size:calc(12px * var(--fs-scale, 1));margin-top:4px">施工未取消，等你定夺子单：${kids.length ? kids.map(c => `<button type="button" class="link" data-action="open-decision" data-id="${attr(c.task_id)}">${esc(displayTitle(c))}</button>`).join('、') : '（子单不在本项目）'}</div></div><div class="row wrap" style="gap:6px;justify-content:flex-end">${view(d)}</div></div>`; };
  const doneRow = d => `${esc(displayTitle(d))}（${esc(d.implementation?.merge_commit || d.implementation?.commit || '')}）`;
  const landed = buckets.done.filter(d => d.implementation?.state === 'merged'), released = buckets.done.filter(d => d.implementation?.state !== 'merged');
  const doneBody = buckets.done.length ? `${landed.length ? `<p class="faint" style="margin:0;font-size:calc(12px * var(--fs-scale, 1))">已合并进主分支、待 Leader 回写 landed：${landed.map(doneRow).join('、')}</p>` : ''}${released.length ? `<p class="faint" style="margin:0;font-size:calc(12px * var(--fs-scale, 1))">已落地：${released.map(d => esc(displayTitle(d))).join('、')}</p>` : ''}` : '';
  const block = (k, body, emptyText) => `<div data-bucket="${k}" style="margin-top:10px"><h3 style="font-size:calc(13px * var(--fs-scale, 1));margin:0 0 6px">${esc(BUCKETS[k])}<span class="n faint" style="margin-left:6px">${buckets[k].length}</span></h3>${body || `<div class="faint" style="font-size:calc(12px * var(--fs-scale, 1))">${esc(emptyText)}</div>`}</div>`;
  const LANDING_NOTE = '决策详情里的「标记」只是给自己做记号，不会触发施工；施工由 Leader 派 build、审查、合并后自动回写为已落地。builder 离线、审查退回都留在「Agent 处理中」，不会推给你。';
  if (bare) return `<div data-landing><p class="faint ov-hint" data-tip="${attr(LANDING_NOTE)}">已采纳，等待落地 · ${landing.length} 项 · ${esc(hint)}</p>${blocked}${landActions()}
    ${block('agent', landing.length ? `<div class="focus">${capped('landing', landing, row)}</div>` : '', '没有 Agent 处理中的项')}
    ${block('user', buckets.user.length ? `<div class="focus">${capped('landing-user', buckets.user, userRow)}</div>` : '', '没有需要你动手的项')}
    ${block('decide', buckets.decide.length ? `<div class="focus">${capped('landing-decide', buckets.decide, decideRow)}</div>` : '', '没有等待你决策的项')}
    ${block('done', doneBody, '还没有完成项')}</div>`;
  return `<section class="card pad" data-landing><div class="section-head"><h2>已采纳，等待落地</h2><span class="hint">${landing.length} 项 · ${hint}</span></div>${blocked}
    ${block('agent', landing.length ? `<div class="focus">${capped('landing', landing, row)}</div>` : '', '没有 Agent 处理中的项')}
    ${block('user', buckets.user.length ? `<div class="focus">${buckets.user.map(userRow).join('')}</div>` : '', '没有需要你动手的项；Agent 准备完成并写明步骤 / 原因 / 验证前不会出现在这里')}
    ${block('decide', buckets.decide.length ? `<div class="focus">${buckets.decide.map(decideRow).join('')}</div>` : '', '没有等待你决策的项')}
    ${block('done', doneBody, '还没有完成项')}
    <p class="faint" style="margin:8px 0 0;font-size:calc(12px * var(--fs-scale, 1))">决策详情里的「标记」只是给自己做记号，不会触发施工；施工由 Leader 派 build、审查、合并后自动回写为已落地。builder 离线、审查退回都留在「Agent 处理中」，不会推给你。</p></section>`;
}

