// page-agents.js：从 views2.js 拆出（ADR 0015，按页面拆模块；scripts/split_module.py 生成，函数体未改）
// 视图：发起、Agent、设置
import { esc, attr, icon, relTime, relAge, fmtDate } from './core.js';
import { S, F, project, agentList, onlineAgents, dispatchableAgents, undispatchableReason, DYNAMIC_TEAM, pickStats, pickStatsText, dispatchEstimate, agentByName, projectPeers, displayName, agentMeta, effectiveTeams, ensureComposer, IMAGE_ACCEPT, CONTROLLER_PEER, secondsLeft, fmtLeft } from './model.js';
import { head, empty, ovTabs, pageParts } from './views.js';
import { teamChips } from './components.js';
import * as Prefs from './prefs.js';

import { GROUP_ORDER_KEY, badge, groupOfAgent, initials } from './views2.js';
import { directCard, directBanner } from './direct-ui.js';

// 心跳迷你波形：按 last_seen 距今分钟数画一条衰减曲线，直观表现活跃度
function heartbeat(p) {
  const m = Math.min(180, (p.age_sec || 0) / 60), w = 120, h = 22;
  const pts = []; for (let i = 0; i <= 24; i++) { const x = i / 24 * w; const amp = p.online ? Math.max(2, 8 - m / 30) : 1; const y = h / 2 + (i % 6 === 3 ? -amp * 1.4 : i % 6 === 4 ? amp : 0); pts.push(`${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`); }
  return `<svg class="beat" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true"><path d="${pts.join('')}"/></svg>`;
}

export function groupOrder() { try { return JSON.parse(localStorage.getItem(GROUP_ORDER_KEY) || '[]'); } catch { return []; } }
export function renderPeers() {
  const q = F.filters.peers.trim().toLowerCase();
  const all = agentList(), p = project(), team = new Set(projectPeers(p));
  const online = all.filter(isOn).length, busy = all.filter(a => isOn(a) && a.status === 'busy').length, stale = all.filter(a => a.stale_with_queue).length;
  const offlineCount = all.filter(a => !isOn(a) && a.peer !== 'controller').length;
  // 被占着不动的单：离线 Agent 手上的全部 + 在线空闲 Agent 的「已领未回」（后端 bus_reassign 同口径再判一次）
  const heldN = all.reduce((n, a) => n + (!isOn(a) ? (a.queued || 0) + (a.claimed || 0) : a.status !== 'busy' ? (a.claimed || 0) : 0), 0);
  const c = S().controller || {};
  const groups = (S().agent_groups || []).slice();
  const saved = groupOrder(); const pos = id => { const i = saved.indexOf(id); return i < 0 ? 1000 + (groups.find(g => g.id === id)?.order || 0) : i; };
  groups.sort((a, b) => pos(a.id) - pos(b.id));
  const buckets = new Map(groups.map(g => [g.id, []])); const other = [];
  all.filter(a => !q || a.peer.includes(q) || (a.model || '').toLowerCase().includes(q) || displayName(a.peer).toLowerCase().includes(q)).forEach(a => { const g = groupOfAgent(a); (g ? buckets.get(g.id) : other).push(a); });
  const sortA = (x, y) => isOn(y) - isOn(x) || x.peer.localeCompare(y.peer);
  return `<div class="view">${head('协作阵列', 'Agent', 'Agent 按模型组归类，同组即同一模型，来源账号不重要。拖动组标题可调整顺序，默认按模型能力与使用频率排列。', `<button type="button" data-action="group-new">${icon('plus')}新建分组</button>${heldN ? `<button type="button" class="primary" data-action="reassign-held" data-peer="" title="离线 Agent 手上的单、在线却空闲的 Agent 已领未回的单，一次改派给在线 Agent">${icon('send')}一键改派 ${heldN} 单</button>` : ''}${offlineCount ? `<button type="button" data-action="wake-all" title="给掉线的 Agent 发唤醒语：Arena 中继接入的直接发进会话，其余复制后粘贴">${icon('send')}唤醒掉线的 ${offlineCount} 个</button>` : ''}${offlineCount ? `<button type="button" class="warn" data-action="peers-prune" title="清理离线超过12小时且无任务的Agent">${icon('trash')}清理离线</button>` : ''}<button type="button" data-action="join-controller">Leader 接入</button><button type="button" class="primary" data-action="join-peer">${icon('plus')}接入 Agent</button>`)}
    ${directBanner()}
    ${opsFold(all)}
    <section class="card fit-panel" data-key="fit-peers"><div class="filters fit-head"><div class="search">${icon('search')}<input type="search" placeholder="按名称或模型搜索…" value="${attr(F.filters.peers)}" data-filter="peers" aria-label="搜索 Agent" autocomplete="off"></div><span class="result-count">${all.length} 个 Agent · ${groups.length} 组</span>${saved.length ? `<button type="button" class="small ghost" data-action="reset-group-order">恢复默认顺序</button>` : ''}</div>
    <div class="fit-body"><div class="group-grid" id="groupSections">${directCard(q)}${groups.filter(g => !q || buckets.get(g.id).length).map(g => groupCard(g, buckets.get(g.id).sort(sortA), team)).join('')}
    ${other.length ? groupCard({ id: '_other', name: '未归组', label: '', match: [] }, other.sort(sortA), team) : ''}</div></div></section>
  </div>`;
}

// ===== 2026-10-02 Agent 页：分组卡片网格（宽屏一行约 5 张），卡里每个 Agent 一个胶囊，点开看全部详情 =====
// 在线只认 online === true：缺失（心跳文件不完整）按离线算——卡片、计数、排序同一口径（110c：卡片按「不等于 false」、计数按「为真」，缺失时两边对不上）
export const isOn = a => a.online === true;
const agentTone = a => !isOn(a) ? 'off' : a.status === 'busy' ? 'info' : a.listening ? 'ok' : a.listening === false ? 'warn' : 'ok';
const agentStateText = a => a.online === undefined ? '状态未知' : !isOn(a) ? '离线' : a.status === 'busy' ? '作答中' : a.listening ? '在等任务' : a.listening === false ? '在线没在等任务' : '在线';
function agentPill(a) {
  const tone = agentTone(a), held = (a.queued || 0) + (a.claimed || 0);
  const tip = `${displayName(a.peer)} · ${a.model || '模型未登记'} · ${agentStateText(a)} · 心跳 ${relAge(a.age_sec, a.last_seen)}${held ? ` · 手上 ${held} 单` : ''}${a.strikes ? ` · 领了不做 ×${a.strikes}` : ''}`;
  const badge = a.benched ? '<span class="pill-badge danger">停</span>' : a.strikes ? `<span class="pill-badge warn">×${a.strikes}</span>` : held ? `<span class="pill-badge">${held}</span>` : '';
  return `<button type="button" class="agent-pill ${tone}" data-action="agent-open" data-peer="${attr(a.peer)}" data-key="${attr(a.peer)}" data-tip="${attr(tip)}"><span class="status-dot ${tone}" aria-hidden="true"></span><span class="truncate">${esc(displayName(a.peer))}</span>${badge}</button>`;
}
function groupCard(g, list, team) {
  const on = list.filter(isOn).length, real = g.id !== '_other';
  const roles = real ? `${g.reviewer ? '<span class="chip info plain" data-tip="在 Reviewer 组：会被派审查">审</span>' : ''}${g.builder ? '<span class="chip plain" data-tip="在施工组：会被派施工">施</span>' : ''}` : '';
  return `<section class="card group-card group-section${on ? '' : ' quiet'}" data-group="${attr(g.id)}" data-key="g:${attr(g.id)}"${real ? ' draggable="true"' : ''}>
    <header class="gc-head">${real ? `<button type="button" class="link" data-action="group-edit" data-group="${attr(g.id)}" data-tip="改名、标签、包含哪些模型、能否审查 / 施工、合并或删除">${esc(g.name || g.id)}</button>` : `<b class="gc-name">${esc(g.name)}</b>`}
      <span class="grow"></span><span class="faint gc-count">${on}/${list.length}</span></header>
    ${g.label || roles ? `<div class="gc-tags">${g.label ? `<span class="chip ok plain">${esc(g.label)}</span>` : ''}${roles}</div>` : ''}
    <div class="gc-pills">${list.length ? list.map(agentPill).join('') : '<span class="faint gc-empty">还没有 Agent</span>'}</div>
    ${real ? `<footer class="gc-foot"><button type="button" class="small ghost" data-action="join-peer" data-model="${attr((g.match || [])[0] || g.id)}">${icon('plus')}接入</button></footer>` : ''}
  </section>`;
}
// 抽屉里的完整详情（原来的大卡片，含资料 / 改名 / 接入提示词 / 改派 / 移除）
export function agentDetail(peer) { const a = agentList().find(x => x.peer === peer); return a ? agentCard(a, projectPeers(project()).includes(peer)) : ''; }
// 分组编辑表单（抽屉）：名称、标签、包含模型、能审查 / 能施工、并入另一组、删除
export function groupForm(id) {
  const gs = S().agent_groups || [], g = gs.find(x => x.id === id) || { id: '', name: '', label: '', match: [], reviewer: false, builder: false };
  const others = gs.filter(x => x.id !== g.id);
  return `<form data-form="agent-group" data-id="${attr(g.id)}" class="stack">
    <div class="field"><label>名称</label><input name="name" value="${attr(g.name || '')}" maxlength="40" placeholder="例如 Claude Opus 系列" required></div>
    <div class="field"><label>标签（自由填写，留空不显示）</label><input name="label" value="${attr(g.label || '')}" maxlength="12" placeholder="例如 主力 / 备用 / 便宜"></div>
    <div class="field"><label>包含哪些模型（每行一个，按前缀匹配：写 claude-opus 就包含 5 和 5.5）</label><textarea name="match" rows="3">${esc((g.match || []).join('\n'))}</textarea></div>
    <label class="check"><input type="checkbox" name="reviewer" ${g.reviewer ? 'checked' : ''}> 能审查（进 Reviewer 组，催审 / 自动分配会派审查给它）</label>
    <label class="check"><input type="checkbox" name="builder" ${g.builder ? 'checked' : ''}> 能施工（进施工组，自动分配会派施工给它）</label>
    ${g.id ? `<div class="danger-zone stack"><h3>危险操作</h3>
    ${others.length ? `<div class="field"><label>并入另一组（这组的模型和 Agent 都并过去，这组删除）</label><div class="row wrap"><select name="merge_into">${others.map(o => `<option value="${attr(o.id)}">${esc(o.name || o.id)}</option>`).join('')}</select><button type="button" class="small" data-action="group-merge" data-group="${attr(g.id)}">并入</button></div></div>` : ''}
    <div class="row wrap"><button type="button" class="small ghost danger" data-action="group-delete" data-group="${attr(g.id)}">${icon('trash')}删除这个分组</button><span class="faint">组里的 Agent 会变成「未归组」，Agent 本身不删</span></div>
</div>` : ''}
    <div class="row wrap"><button type="submit" class="primary">保存</button><button type="button" class="ghost" data-action="close-drawer">取消</button></div>
  </form>`;
}
function opsFold(all) {
  const s = S(), leaders = Array.isArray(s.leaders) ? s.leaders.length : 0, al = s.allocator || {};
  const online = all.filter(isOn).length, waiting = all.filter(a => a.listening).length, stale = all.filter(a => a.stale_with_queue).length;
  const mode = { off: '关', preview: '预演', on: '真派' }[al.mode] || '—';
  return `<div class="ops-summary"><span><b>${waiting}</b> 在等任务</span><span><b>${online}</b>/${all.length} 在线</span>${stale ? `<span class="warn-text"><b>${stale}</b> 离线但有排队</span>` : ''}<span>Leader <b>${leaders}</b> 位在线</span><span>自动分配：${mode}</span></div>
    <details class="card pad ops-fold" id="opsFold"><summary>调度：Leader、自动分配、顶替顺序</summary>
      ${leaderBoard()}
      ${allocatorCard()}
      ${relayCard()}
    </details>`;
}
// Leader 身份看板：租约归属 + 心跳倒计时。代理持锁、无租约或 Leader 心跳离线时整卡转琥珀并显示「接管中：X」。
// 「收回 Leader」：代理持有租约时可点，POST /api/handback 写交回请求，代理下一次等待指令时交回（f4f5，2026-10-02 接上）。
// ADR 0012：按积压自动分配。默认「预演」——只显示它会怎么派，你看一段时间觉得对了再切「真派」
const ALLOC_MODES = [['off', '关'], ['preview', '预演'], ['on', '真派']];
export function allocatorCard() {
  const a = S().allocator; if (!a) return '';
  const p = a.last_plan || {}, labels = a.labels || {}, acts = p.actions || [], b = p.backlog || {};
  const seg = ALLOC_MODES.map(([k, l]) => `<button type="button" class="${a.mode === k ? 'active' : ''}" data-action="allocator-mode" data-mode="${k}" aria-pressed="${a.mode === k}">${l}</button>`).join('');
  const counts = Object.keys(labels).map(k => `<span class="chip plain">${esc(labels[k])} <b>${esc(String(b[k] ?? 0))}</b></span>`).join('');
  const verb = a.mode === 'on' ? '已派' : '会派';
  const rows = acts.map(x => `<li class="alloc-row" data-key="${attr(x.stage + ':' + x.task_id)}"><span class="chip plain">${esc(labels[x.stage] || x.stage)}</span><span class="mono alloc-meta">${esc(x.task_id)}</span><span class="grow"></span><span class="muted alloc-meta">${x.peer ? `${esc(displayName(x.peer))} → ` : ''}<b>${esc(displayName(x.to))}</b>${x.result ? (x.result.ok ? ' ✓' : ` · 没派成：${esc(x.result.error || '')}`) : ''}</span></li>`).join('');
  const note = a.mode === 'off' ? '已关闭：不看积压、不派人。' : !p.time ? '守护下一圈会算出第一份计划。'
    : `${relTime(p.time)}：空闲 ${(p.idle || []).length} 人；每圈最多派 ${a.limits?.per_cycle ?? 5} 单、每人 1 单、同一名额最多换人 ${a.limits?.moves_per_slot ?? 2} 次。`;
  return `<div class="card pad alloc-card" id="allocatorCard">
    <div class="row wrap leader-head"><b>自动分配</b><div class="segmented">${seg}</div>${counts}</div>
    ${acts.length ? `<div class="faint alloc-meta alloc-sub">上一圈${verb}：</div><ul class="leader-list">${rows}</ul>` : p.time && a.mode !== 'off' ? '<div class="faint alloc-meta alloc-sub">上一圈没有要派的：要么没积压，要么没有空闲的人。</div>' : ''}
    <div class="faint alloc-meta alloc-sub">${esc(note)}</div>
  </div>`;
}

export function leaderBoard() {
  // 多活：每位在线 Leader 一行（data-key=会话号），新会话接入是多出一行，不是把唯一的名字换掉
  const s = S(), c = s.controller || {}, l = s.lease || {}, holder = l.holder || '';
  const relay = !!holder && holder !== CONTROLLER_PEER;
  const leaders = Array.isArray(s.leaders) ? s.leaders : [], q = s.leader_queue || {};
  const n = relay ? 1 : leaders.length;
  const amber = relay || !holder || c.online === false;
  const left = secondsLeft(l.expires, s.time), ms = left == null ? '' : Date.now() + left * 1000;
  const title = relay ? `代理 Leader 接管中：${displayName(holder)}` : n ? `${n} 位 Leader 在线` : holder ? 'Leader 在任' : '无有效租约 · 无人接管';
  const row = r => {
    const busy = (r.claimed || []).length;
    return `<li class="leader-row" data-key="${attr(r.session)}" data-state="${busy ? 'busy' : 'idle'}">
      <span class="leader-dot" aria-hidden="true"></span>
      <b class="leader-name">${esc(r.label || `Leader 会话 ${r.session}`)}</b>
      ${r.label ? `<span class="faint mono leader-meta">${esc(r.session)}</span>` : ''}
      ${relay ? '' : `<button type="button" class="ghost icon small" data-action="rename-leader" data-session="${attr(r.session)}" data-label="${attr(r.label || '')}" aria-label="给这位 Leader 起名" data-tip="给这位 Leader 起名（只改显示，其他 Leader 不受影响）">${icon('edit')}</button>`}
      <span class="grow"></span>
      <span class="muted leader-meta">${busy ? `正在处理 ${busy} 条指令` : '待命'}</span>
      <span class="faint leader-meta">心跳 ${esc(relAge(r.age_sec))} · ${esc(relTime(r.joined))}接入</span>
    </li>`;
  };
  const rows = relay ? row({ session: holder, label: displayName(holder), age_sec: c.age_sec, joined: l.since, claimed: [] }) : leaders.map(row).join('');
  const orphan = q.orphaned ? `<span class="leader-warn">${esc(String(q.orphaned))} 条被已断开的会话领走 · 下一位 Leader 等待时自动退回</span>` : '';
  const note = relay ? '代理 Leader 由顶替顺序解析产生，真 Leader 回来后自动交回。'
    : n ? '所有 Leader 会话共用一份租约（身份都是 controller），每条指令只会被其中一位领到，可以同时处理积压。'
    : '还没有 Leader 会话报到：网页指令只在队列里等待，无人领取。';
  return `<div class="card pad leader-board${amber ? ' warn' : ''}" id="leaderBoard">
    <div class="row wrap leader-head">
      <span class="chip ${amber ? 'warn' : 'ok'}${relay ? ' live' : ''}">${esc(title)}</span>
      <span class="faint mono leader-meta">epoch ${esc(String(l.epoch ?? '—'))}</span>
      <span class="grow"></span>
      <span class="muted leader-meta">指令：排队 <b>${esc(String(q.pending ?? 0))}</b> · 处理中 <b>${esc(String(q.claimed ?? 0))}</b></span>
      <span class="muted leader-meta">租约剩余 <b class="mono" data-countdown="${attr(String(ms))}">${esc(fmtLeft(left))}</b></span>
      <button type="button" class="small ghost" data-action="lead-handback" ${relay ? '' : 'disabled'} title="${relay ? '让代理 Leader 交回租约：它下一次等待指令时交回，真 Leader 接着处理' : '只有代理 Leader 持有租约时才需要收回'}">收回 Leader</button>
    </div>
    ${rows ? `<ul class="leader-list">${rows}</ul>` : ''}
    ${orphan}
    <div class="faint leader-meta leader-note">${esc(note)}</div>
  </div>`;
}// 顶替顺序卡：服务端逐项解析结果（relay_resolution）+ 可拖拽的列表；relay_order_meta 为 null = 从未在界面编辑过，常驻提示并给出厂 SOTA 序按钮
export const relayCardHtml = () => relayCard();
function relayCard() {
  const pr = S().priority || {}, rr = S().relay_resolution || {}, order = pr.relay_order || [], items = rr.items || [];
  const gs = S().agent_groups || [], gname = id => gs.find(g => g.id === id)?.name || id;
  const never = pr.relay_order_meta == null, rev = pr.rev ?? 0;
  const row = (it, i) => `<li class="relay-item card" data-relay-item="${attr(it.item)}" draggable="true">
      <span class="drag-handle" aria-hidden="true">⋮⋮</span><b class="num muted" style="width:18px">${i + 1}</b>
      <span class="min0 grow"><b class="truncate">${esc(it.kind === 'group' ? gname(it.item) : displayName(it.item))}</b><small class="muted" style="margin-left:6px">${it.kind === 'group' ? `模型组 · 在线 ${it.online_n}/${it.total_n}${it.skipped_n ? ` · 跳过离线有排队 ${it.skipped_n}` : ''}` : it.kind === 'peer' ? `Agent · ${it.online_n ? '在线' : '离线'}` : '未匹配到模型组或 Agent'}</small></span>
      ${it.pick ? `<span class="chip ${!rr.fallback && rr.pick === it.pick ? 'ok' : 'plain'}">${!rr.fallback && rr.pick === it.pick ? '将顶替 ' : '→ '}${esc(displayName(it.pick))}</span>` : '<span class="chip warn plain">无可用</span>'}
      <button type="button" class="ghost icon small" data-action="relay-remove" data-item="${attr(it.item)}" aria-label="移出顶替顺序" data-tip="移出">${icon('close')}</button></li>`;
  const inList = new Set(order), addable = gs.filter(g => !inList.has(g.id)).map(g => `<option value="${attr(g.id)}">${esc(g.name)}（模型组）</option>`).join('') + agentList().filter(a => a.online && !inList.has(a.peer)).map(a => `<option value="${attr(a.peer)}">${esc(displayName(a.peer))}（Agent）</option>`).join('');
  const status = rr.pick ? (rr.fallback ? `<span class="chip warn">名单无人可用，兜底 ${esc(displayName(rr.pick))}</span>` : `<span class="chip ok">当前顶替 ${esc(displayName(rr.pick))}${rr.via_group ? ` · 来自 ${esc(gname(rr.via_group))}` : ''}</span>`) : rr.refused ? '<span class="chip plain">已关闭替补</span>' : '<span class="chip bad">无人可顶替</span>';
  // 平时用不到：默认折叠，只留一行摘要；展开状态复用 F.expanded / localStorage 'bus.expanded'（key 'relay-card'），刷新与 SSE morph 后保持。
  // 折叠时不渲染 #relayList / #relayBody，拖拽靠 document 级委托匹配 .relay-item，列表不存在即不会有拖拽。
  const open = F.expanded.has(RELAY_CARD_KEY);
  const summary = `<span class="faint" style="font-size:calc(12px * var(--fs-scale, 1))">名单 ${order.length} 项 · rev ${esc(String(rev))}</span>`;
  const toggle = `<button type="button" class="small ghost" data-action="toggle-relay-card" aria-expanded="${open ? 'true' : 'false'}" aria-controls="relayBody" title="${open ? '收起名单与编辑控件' : '展开名单与编辑控件'}">${open ? '收起' : '展开'}</button>`;
  const body = `<div id="relayBody" class="stack" style="gap:8px">
    <div class="faint" style="font-size:calc(12px * var(--fs-scale, 1))">失联 3 分钟后按此序逐项解析，组内取最近心跳者；Leader 回来自动交回</div>
    ${never ? `<div class="banner" style="margin:0"><span>当前序列是手填的 Agent 名，从未在界面编辑过。Agent 名随会话关闭失效，模型组才稳定。</span><button type="button" class="small primary" data-action="relay-factory-reset">恢复出厂 SOTA 序</button></div>` : ''}
    ${order.length ? `<ol class="relay-list" id="relayList" data-rev="${attr(String(rev))}">${items.map(row).join('')}</ol>` : '<div class="faint" style="font-size:calc(12px * var(--fs-scale, 1))">顶替顺序为空：Leader 掉线时不会有人代理（视为明确关闭替补）。</div>'}
    ${rr.fallback ? `<div class="faint" style="font-size:calc(12px * var(--fs-scale, 1))">兜底名单（按名字）：${esc((rr.fallback_peers || []).map(displayName).join('、'))}</div>` : ''}
    <div class="row wrap" style="gap:6px"><select id="relayAdd" aria-label="加入顶替顺序"><option value="">加入模型组或 Agent…</option>${addable}</select><button type="button" class="small" data-action="relay-add">加入</button><button type="button" class="small ghost" data-action="set-relay-order">高级编辑</button><span class="faint" style="font-size:calc(12px * var(--fs-scale, 1))">拖动 ⋮⋮ 调整顺序，保存即生效（rev ${esc(String(rev))}）</span></div>
  </div>`;
  return `<div class="card pad stack" id="relayCard" style="margin-bottom:14px;gap:8px">
    <div class="row wrap" style="align-items:center;gap:8px"><b style="font-size:calc(13px * var(--fs-scale, 1))">Leader 掉线时的顶替顺序</b>${summary}<span class="grow"></span>${rr.policy_note ? `<span class="chip warn plain">${esc(rr.policy_note)}</span>` : ''}${never ? '<span class="chip warn plain" title="当前序列是手填的 Agent 名，从未在界面编辑过">手填名单</span>' : ''}${status}${toggle}</div>
    ${open ? body : ''}
  </div>`;
}
export const RELAY_CARD_KEY = 'relay-card';
// 「排队」只能指真正待领的 .json。已领未回（.taken 孤儿）与已过期是另外两回事：
// 它们不会再被任何 Agent 领走，混进「排队」会让空闲的 Agent 看上去积压了一堆活（fb-8a7c79854b）。
function queueWarnings(a) {
  const bits = [];
  // 2026-10-02：在线不等于能接活——在线、空闲却不在 wait 里，派过去没人领；领了不做被收回的次数也摆出来
  if (isOn(a) && a.activity === 'idle' && a.listening === false) bits.push(`<span class="chip warn plain" data-tip="心跳在，但没有在等任务（没在调用 bus_wait_task）：派给它的单不会有人领">在线但没在等任务</span>`);
  if (a.strikes) bits.push(`<span class="chip ${a.benched ? 'danger' : 'warn'} plain" data-tip="24 小时内领了任务 30 分钟没干活、被系统收回改派的次数；满 2 次暂停派单，交出一次回复后恢复">领了不做 ×${a.strikes}${a.benched ? ' · 暂停派单' : ''}</span>`);
  if (a.claimed) bits.push(`<span class="chip warn plain" data-tip="任务包已被某个会话领走却一直没回复；不会再被别人领取，需要人工回收">已领未回 ${a.claimed}</span>`);
  if (a.expired) bits.push(`<span class="chip bad plain" data-tip="过了 deadline 仍没有被领取">已过期 ${a.expired}</span>`);
  return bits.length ? `<div class="row wrap" style="gap:6px">${bits.join('')}</div>` : '';
}
function agentCard(a, inTeam) {
  const online = isOn(a), meta = agentMeta(a.peer);
  const state = !online ? '离线' : a.status === 'busy' ? '作答中' : a.status === 'relay' ? '接力中' : '空闲待命';
  return `<article class="card agent hover ${online ? '' : 'off'}" data-key="${attr(a.peer)}">
    <header><span class="avatar ${online ? '' : 'off'}">${esc(initials(displayName(a.peer)))}</span><div class="min0 grow"><b class="truncate">${esc(displayName(a.peer))}</b><small class="truncate">${esc(a.model || '模型未登记')}</small></div><span class="chip ${online ? (a.status === 'busy' ? 'info live' : 'ok') : 'bad'}">${esc(state)}</span></header>
    ${heartbeat(a)}
    <div class="meta"><span>最近心跳 <b>${esc(relAge(a.age_sec, a.last_seen))}</b></span>${a.clock_skew ? `<span class="chip bad plain" data-tip="这台机器写下的时间戳落在未来，时钟或时区不可信；在线判定已按失联处理">时钟异常</span>` : ''}<span>待领任务 <b>${a.queued || 0}</b></span><span>当前任务 <b class="mono" style="font-size:calc(11px * var(--fs-scale, 1))">${esc(a.task_id || '—')}</b></span><span>项目团队 <b>${inTeam ? '是' : '否'}</b></span></div>
    ${queueWarnings(a)}
    ${a.stale_with_queue ? `<div class="banner danger" style="margin:0;padding:6px 10px;font-size:calc(12px * var(--fs-scale, 1))">${icon('warn')}离线但 inbox 还有 ${a.queued_total ?? ((a.queued || 0) + (a.claimed || 0))} 个任务没了结（待领 ${a.queued || 0} · 已领未回 ${a.claimed || 0}），进程疑似退出 <button type="button" class="small" data-action="reassign-held" data-peer="${attr(a.peer)}">改派这 ${a.queued_total ?? ((a.queued || 0) + (a.claimed || 0))} 单</button></div>` : ''}
    ${meta.note ? `<div class="muted clamp" style="font-size:calc(12px * var(--fs-scale, 1))">${esc(meta.note)}</div>` : ''}
    <div class="row wrap">${online ? '' : `<button type="button" class="small" data-action="wake-peer" data-peer="${attr(a.peer)}" data-tip="让它回到待命：Arena 中继接入的直接发进会话，其余复制唤醒语后粘贴到它的对话里">${icon('send')}<span>唤醒</span></button>`}<button type="button" class="small" data-action="edit-agent" data-peer="${attr(a.peer)}">${icon('edit')}资料</button><button type="button" class="small ghost" data-action="rename-peer" data-peer="${attr(a.peer)}" data-tip="只改显示名，协议仍用真名">${icon('edit')}改名</button><button type="button" class="small ghost" data-action="join-peer" data-peer="${attr(a.peer)}" data-model="${attr(a.model || '')}">${icon('copy')}接入提示词</button><button type="button" class="small ghost danger" data-action="remove-peer" data-peer="${attr(a.peer)}" data-tip="移除这个 Agent：先把手上的单挪给别人，再删登记、吊销接入令牌">${icon('trash')}移除</button></div>
  </article>`;
}

