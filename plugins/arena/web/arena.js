// Arena 插件页面（ADR 0014，原 web/v2/js/arena.js）：账号池、会话池、路由与 API Key、榜单。
// 数据来自 /api/arena/*（工作台按插件清单的 proxy 转发到插件自己的控制面）。共用工作台的 core / components，不引 views.js（它会拉起整套工作台数据模型）。
import { $, $$, esc, attr, icon, api, fmtDate, relTime, fmtNum } from '/v2/js/core.js';
import { toast, openDrawer, closeDrawer, confirmDialog } from '/v2/js/components.js';

const head = (kicker, title, desc, actions = '') => `<header class="view-head"><div><div class="eyebrow">${esc(kicker)}</div><h1>${esc(title)}</h1><p>${esc(desc)}</p></div><div class="view-actions">${actions}</div></header>`;
const empty = (t, s = '', act = '') => `<div class="empty"><strong>${esc(t)}</strong>${s ? `<span>${esc(s)}</span>` : ''}${act}</div>`;

export const ARENA_NAV = [
  ['arena-groups', 'box', '模型组'], ['arena-leaderboard', 'grid', '榜单'], ['arena-accounts', 'users', '账号'], ['arena-sessions', 'layers', '会话'], ['arena-routes', 'bolt', '路由与 Key']
];
export const isArenaView = v => String(v || '').startsWith('arena-');

const HEALTH = { ready: ['可用', 'ok'], exhausted: ['额度用尽', 'warn'], cooldown: ['冷却中', 'info'], banned: ['已失效', 'bad'], degraded: ['异常', 'bad'], unauthenticated: ['未登录', 'warn'] };
const HEALTH_ORDER = ['ready', 'exhausted', 'cooldown', 'unauthenticated', 'degraded', 'banned'];
const BOARD_LABEL = { text: '文本', code: '代码', vision: '视觉', search: '搜索' };

export const A = {
  loaded: false, down: false, error: '',
  overview: null, accounts: null, sessions: null, routes: null, keys: null, leaderboard: null, groups: null, jobs: [], settings: null, openGroup: '',
  filters: { account: '', health: '', session: '', family: '', onlyProbe: false, board: 'text' },
  selected: new Set(), sort: { key: 'created_at', dir: 'desc' }, lastJobs: {}, timer: 0, rerender: null,
};

// ---------- 数据 ----------
export async function loadArena(view) {
  try {
    const wants = { 'arena-groups': ['overview', 'groups', 'accounts'], 'arena-accounts': ['overview', 'accounts'], 'arena-sessions': ['overview', 'sessions', 'routes', 'groups'], 'arena-routes': ['overview', 'routes', 'keys', 'sessions', 'groups'], 'arena-leaderboard': ['overview', 'leaderboard', 'routes', 'groups'] }[view] || ['overview', 'groups'];
    const results = await Promise.all([...wants, 'jobs'].map(k => api(`/api/arena/${k}`)));
    [...wants, 'jobs'].forEach((k, i) => { A[k] = k === 'jobs' ? (results[i] && results[i].jobs) || [] : results[i]; if (k === 'jobs' && results[i] && results[i].join_config) A.joinConfig = results[i].join_config; });
    A.loaded = true; A.down = false; A.error = '';
    announceJobs();
  } catch (e) {
    A.down = e.status === 503 || e.status === 404 || /没有响应|控制面/.test(e.message); A.error = e.message;
  }
  A.rerender?.();
}
function announceJobs() {
  for (const j of A.jobs) {
    const prev = A.lastJobs[j.id];
    if (prev && prev !== j.status && !isActiveJob(j)) {
      const label = { join: '接入 Workbench', sync: '同步', check_all: '状态检测', import: '账号导入', register: '自动注册', detect: '模型识别', delete_sessions: '删除会话', batch_create: '批量开会话', leaderboard: '榜单刷新' }[j.kind] || j.kind;
      const js = j.kind === 'join' && JOIN_STATE[j.status];
      if (js) {
        const peer = (j.payload && j.payload.peer_name) || (j.result && j.result.peer) || '';
        const why = REASON[j.reason_code] ? REASON[j.reason_code][0] : '';
        toast(`${label}${js[0]}${peer ? ` · Agent ${peer}` : ''}${j.status === 'connected' ? '' : `：${j.error || why || j.message || ''}`}`, js[1] === 'plain' ? 'info' : js[1], { ms: j.status === 'connected' ? 4000 : 8000 });
      } else {
        j.status === 'done' ? toast(`${label}完成 ${summarize(j.result)}`, 'ok') : toast(`${label}失败：${j.error}`, 'bad', { ms: 6000 });
      }
    }
    A.lastJobs[j.id] = j.status;
  }
}
const summarize = r => r && typeof r === 'object' ? Object.entries(r).filter(([, v]) => typeof v !== 'object').map(([k, v]) => `${k} ${v}`).join(' · ') : '';
const currentViewName = () => new URLSearchParams(location.search).get('view') || 'arena-groups';
export function startArenaPolling(view) {
  clearInterval(A.timer);
  if (!isArenaView(view)) return;
  loadArena(view);
  A.timer = setInterval(() => { if (!document.hidden && isArenaView(currentViewName())) loadArena(view); else clearInterval(A.timer); }, (A.jobs || []).some(isActiveJob) ? 2000 : 6000);
}
async function post(path, body, okMsg) {
  try { const r = await api(`/api/arena/${path}`, { method: 'POST', body: JSON.stringify(body || {}) }); if (okMsg) toast(okMsg, 'ok'); return r; }
  catch (e) { toast(REASON[e.reason_code] ? `${REASON[e.reason_code][0]}：${e.message}` : e.message, 'bad', { ms: 9000 }); throw e; }
}

// 一键接入的原因码 → [中文说明, 色调]。用户看 phase + reason_code + first_text 即可自行分辨。
const REASON = {
  ok: ['已接入', 'ok'], name_collision: ['名字冲突，已自动改名', 'warn'], cf_403: ['Cloudflare 403，需带浏览器 UA', 'bad'],
  no_text_but_running: ['模型在跑但没回文本（正常，等心跳）', 'info'], spend_limit: ['账号额度或限额受限', 'bad'],
  no_token: ['账号凭据失效', 'bad'], bridge_missing: ['Bridge 地址未登记', 'bad'], model_mismatch: ['心跳模型与会话不符', 'bad'],
  no_heartbeat: ['没等到心跳', 'warn'], bad_status: ['心跳状态异常', 'warn'], timeout: ['超时', 'warn'], session_missing: ['会话不存在', 'bad'],
  restarted: ['控制面重启过，消息可能已发出，未自动重发', 'warn'], cancelled: ['已取消（未发送）', 'info'], aborted_batch: ['同批次凭据失效，未发送即中止', 'warn'],
};
// 持久 join 队列的状态 → [文案, 色调]。unconfirmed 绝不是成功：消息已发，但没等到本次接入之后的有效心跳。
const JOIN_STATE = { queued: ['排队中', 'info'], sending: ['发送中', 'info'], waiting_peer: ['等待 Agent 心跳', 'info'], connected: ['已接入', 'ok'], unconfirmed: ['未确认', 'warn'], failed: ['失败', 'bad'], cancelled: ['已取消', 'plain'] };
const ACTIVE_STATES = ['running', 'queued', 'sending', 'waiting_peer'];
const isActiveJob = j => ACTIVE_STATES.includes(j.status);
const isCancellable = j => j.kind === 'join' && ['queued', 'sending', 'waiting_peer'].includes(j.status);
const RECENT_MS = 30 * 60 * 1000;
const isRecent = j => { const t = Date.parse(String(j.finished_at || j.updated_at || j.started_at || '').replace(' ', 'T')); return !t || Date.now() - t < RECENT_MS; };
const newRequestId = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`);
function jobReason(j) {
  const r = REASON[j.reason_code]; if (!r && !j.error) return '';
  const ft = (j.payload && j.payload.first_text) || (j.result && j.result.first_text) || '';
  return `<div class="row wrap" style="gap:6px;margin-top:4px">${r ? `<span class="chip ${r[1]}" style="font-size:calc(11px * var(--fs-scale, 1))">${esc(r[0])}</span>` : ''}${j.payload && j.payload.peer_name ? `<span class="chip plain" style="font-size:calc(11px * var(--fs-scale, 1))">${esc(j.payload.peer_name)}</span>` : ''}${j.error ? `<span class="faint" style="font-size:calc(11px * var(--fs-scale, 1))">${esc(String(j.error).slice(0, 160))}</span>` : ''}${ft ? `<span class="mono faint" style="font-size:calc(11px * var(--fs-scale, 1))">“${esc(ft)}”</span>` : ''}</div>`;
}

// ---------- 公共片段 ----------
const healthChip = a => { const [label, tone] = HEALTH[a.health] || [a.label || a.health, '']; return `<span class="chip ${tone}">${esc(label)}</span>`; };
function jobsBanner() {
  const jobs = A.jobs || [];
  const running = jobs.filter(isActiveJob);
  const shown = [...running, ...jobs.filter(j => !isActiveJob(j) && (j.status === 'failed' || j.kind === 'join') && isRecent(j)).slice(0, 3)];
  if (!shown.length) return '';
  const fs = 'font-size:calc(11px * var(--fs-scale, 1))';
  return `<div class="banner ${running.length ? 'info' : 'warn'} stack" style="gap:6px">${shown.map(j => { const st = j.kind === 'join' && JOIN_STATE[j.status]; return `<div class="row between"><span>${icon(j.status === 'failed' ? 'warn' : j.status === 'connected' ? 'check' : 'clock')} ${st ? `<span class="chip ${st[1]}" style="${fs}">${esc(st[0])}</span> ` : ''}${esc(j.message || j.kind)}${j.payload && j.payload.group ? ` <span class="mono faint" style="${fs}">${esc(j.payload.group)}</span>` : ''}</span><span class="row" style="gap:6px;flex:none"><span class="mono faint">${j.progress}/${j.total || '?'}</span>${isCancellable(j) ? `<button type="button" class="small ghost" data-action="arena-join-cancel" data-id="${attr(j.id)}" ${j.cancel_requested ? 'disabled' : ''}>取消</button>` : ''}</span></div>${isActiveJob(j) ? `<div class="bar"><i style="width:${j.total ? Math.round(j.progress / j.total * 100) : 10}%"></i></div>` : ''}${j.cancel_note ? `<div class="faint" style="${fs}">${esc(j.cancel_note)}</div>` : ''}${jobReason(j)}`; }).join('')}</div>`;
}
function downState() {
  return `<div class="view">${head('Arena', '控制面没有响应', '账号池、会话池与中继由 Arena 插件自己的服务提供。')}<div class="empty" style="padding:60px"><strong>arena 控制面（9091）没有响应</strong><span>${esc(A.error)}</span><div class="row" style="justify-content:center;margin-top:12px"><button type="button" class="primary" data-action="arena-reload">${icon('refresh')}重试</button><a class="link" href="/hub">${icon('box')}到「扩展与来源 → 插件」看状态和日志</a></div></div></div>`;
}
function loadingState(title) { return `<div class="view">${head('Arena', title, '正在读取控制面数据…')}<div class="stack">${[1, 2, 3].map(() => '<div class="card pad skeleton" style="height:56px"></div>').join('')}</div></div>`; }
const upstreamChip = () => { const u = A.overview?.upstream; return u ? `<span class="chip ${u.ok ? 'ok' : 'bad'} ${u.ok ? 'live' : ''}">登录器 ${u.ok ? '在线' : '离线'}</span>` : ''; };

// ---------- 账号 ----------
export function renderArenaAccounts() {
  if (A.down) return downState();
  if (!A.accounts) return loadingState('账号');
  const { accounts, summary, synced_at, active_email } = A.accounts;
  const f = A.filters;
  const rows = accounts.filter(a => (!f.health || a.health === f.health) && (!f.account || (a.email + ' ' + (a.note || '') + ' ' + (a.tags || []).join(' ')).toLowerCase().includes(f.account.toLowerCase())))
    .sort((a, b) => HEALTH_ORDER.indexOf(a.health) - HEALTH_ORDER.indexOf(b.health) || String(a.email).localeCompare(String(b.email)));
  const metric = (k, label) => `<button type="button" class="metric ${f.health === k ? 'hot' : ''}" data-action="arena-health" data-health="${k}"><span>${label}</span><b class="num">${summary[k] || 0}</b><small>${k === 'ready' ? '可直接中继' : k === 'exhausted' ? '等额度重置' : k === 'cooldown' ? '自动恢复' : '需要处理'}</small></button>`;
  return `<div class="view">${head('Arena 账号池', '账号', '每个账号一行。「模型」列是该账号会话按模型归一后的分布；Arena 不提供剩余额度数字，用量列显示今日经中继的次数与用尽 / 重置时间。', `<button type="button" class="ghost danger" data-action="arena-purge-ungrouped" title="删除所有不属于任何模型组的会话（含探针会话）">${icon('trash')}清理非常用</button><button type="button" data-action="arena-check-all">${icon('refresh')}检测全部</button><button type="button" data-action="arena-import">${icon('plus')}批量接入</button><button type="button" class="primary" data-action="arena-register">${icon('bolt')}自动注册</button>`)}
    ${jobsBanner()}
    <div class="metrics" style="grid-template-columns:repeat(4,minmax(0,1fr))">${metric('ready', '可用')}${metric('exhausted', '额度用尽')}${metric('cooldown', '冷却中')}${metric('banned', '已失效')}</div>
    <div class="filters"><div class="search">${icon('search')}<input data-arena-filter="account" value="${attr(f.account)}" placeholder="搜索邮箱、备注、标签…" autocomplete="off"></div>${f.health ? `<button type="button" class="chip" data-action="arena-health" data-health="">${esc(HEALTH[f.health]?.[0] || f.health)} ×</button>` : ''}<span class="hint" style="margin-left:auto">${upstreamChip()} <span class="faint">同步 ${esc(relTime(synced_at))}</span> <button type="button" class="link" data-action="arena-sync">立即同步</button></span></div>
    ${rows.length ? `<div class="card table-scroll"><table class="table"><thead><tr><th>账号</th><th>状态</th><th class="r">会话</th><th>模型</th><th>今日用量</th><th>最近检测</th><th>备注</th><th></th></tr></thead><tbody>${rows.map(a => `<tr class="${a.email === active_email ? 'sel' : ''}"><td><button type="button" class="link mono" data-action="arena-account" data-email="${attr(a.email)}">${esc(a.email)}</button>${a.email === active_email ? ' <span class="chip plain">主账号</span>' : ''}</td><td>${healthChip(a)}${a.reason ? `<div class="faint" style="font-size:calc(11px * var(--fs-scale, 1));margin-top:2px">${esc(a.reason)}</div>` : ''}${a.until ? `<div class="faint" style="font-size:calc(11px * var(--fs-scale, 1))">恢复 ${esc(fmtDate(new Date(a.until * 1000).toISOString()))}</div>` : ''}</td><td class="r num">${a.sessions_count || 0}</td><td>${modelChips(a.models)}</td><td>${usageCell(a)}</td><td class="faint">${esc(relTime(a.last_checked_at))}</td><td class="muted truncate" style="max-width:200px">${(a.tags || []).map(t => `<span class="chip plain">${esc(t)}</span>`).join(' ')} ${esc(a.note || '')}</td><td class="r nowrap"><button type="button" class="small ghost" data-action="arena-check-one" data-email="${attr(a.email)}">检测</button><button type="button" class="small ghost danger" data-action="arena-purge-account" data-email="${attr(a.email)}" data-count="${a.sessions_count || 0}" title="删除该账号全部会话">清空会话</button></td></tr>`).join('')}</tbody></table></div>` : empty('没有匹配的账号', accounts.length ? '换个筛选条件' : '先批量接入或自动注册', accounts.length ? '' : `<button type="button" class="primary" data-action="arena-import" style="margin-top:12px">${icon('plus')}批量接入</button>`)}
  </div>`;
}
function modelChips(models) {
  const list = Object.entries(models || {});
  if (!list.length) return '<span class="faint">—</span>';
  const shown = list.slice(0, 3), rest = list.length - shown.length;
  return `<div class="row wrap" style="gap:4px">${shown.map(([m, n]) => `<span class="chip plain" title="${attr(m)}">${esc(m)} <b>${n}</b></span>`).join('')}${rest > 0 ? `<span class="chip plain">+${rest}</span>` : ''}</div>`;
}
function usageCell(a) {
  const used = a.used_today || 0;
  if (a.health === 'exhausted') return `<span class="chip warn">已用尽</span><div class="faint" style="font-size:calc(11px * var(--fs-scale, 1))">${esc(a.reset_time_text || (a.until ? '恢复 ' + fmtDate(new Date(a.until * 1000).toISOString()) : ''))}</div>`;
  return `<b class="num">${used}</b><span class="faint" style="font-size:calc(11px * var(--fs-scale, 1))"> 次经中继</span>`;
}
function openAccount(email) {
  const a = (A.accounts?.accounts || []).find(x => x.email === email); if (!a) return;
  const active = A.accounts.active_email === email;
  openDrawer({ title: email, subtitle: `${HEALTH[a.health]?.[0] || a.health} · 创建于 ${fmtDate(a.created_at)}`, body: `
    <div class="row wrap" style="margin-bottom:12px">${healthChip(a)}<span class="chip plain">HTTP ${esc(a.status_code || '—')}</span><span class="chip plain">${a.has_token ? '有登录态' : '无登录态'}</span><span class="chip plain">${a.cookie_count || 0} 个 cookie</span></div>
    <dl class="kv"><dt>原始状态</dt><dd>${esc(a.status_category || a.status)}</dd><dt>原因</dt><dd>${esc(a.reason || a.error_reason || '—')}</dd><dt>额度提示</dt><dd>${esc(a.reset_time_text || '—')}</dd><dt>会话数</dt><dd>${a.sessions_count || 0}</dd><dt>模型分布</dt><dd>${modelChips(a.models)}</dd><dt>今日经中继</dt><dd>${a.used_today || 0} 次${a.exhausted_at ? ` · 上次用尽 ${esc(a.exhausted_at)}` : ''}</dd><dt>路由</dt><dd>${esc(Object.entries(a.routes || {}).map(([m, s]) => `${m} → ${String(s).slice(0, 8)}`).join('，') || '—')}</dd><dt>最近检测</dt><dd>${esc(fmtDate(a.last_checked_at))}</dd></dl>
    <form data-form="arena-account-note" data-email="${attr(email)}" style="margin-top:16px"><div class="field"><label for="acctTags">标签（逗号分隔）</label><input id="acctTags" name="tags" value="${attr((a.tags || []).join(', '))}" autocomplete="off"></div><div class="field"><label for="acctNote">备注</label><textarea id="acctNote" name="note" rows="3">${esc(a.note || '')}</textarea></div><button type="submit" class="small primary">保存备注</button></form>
    <div class="section-head" style="margin-top:20px"><h2>操作</h2><span class="hint">凭据不会在页面回显</span></div>
    <div class="row wrap"><button type="button" class="small" data-action="arena-check-one" data-email="${attr(email)}">${icon('refresh')}重新检测</button>${a.health === 'cooldown' ? `<button type="button" class="small" data-action="arena-reset-cooldown" data-email="${attr(email)}">解除冷却</button>` : ''}${active ? '' : `<button type="button" class="small" data-action="arena-switch" data-email="${attr(email)}">设为主账号</button>`}<button type="button" class="small" data-action="arena-create-sessions" data-email="${attr(email)}">开探针会话</button><button type="button" class="small danger" data-action="arena-delete-account" data-email="${attr(email)}">${icon('trash')}移出账号池</button></div>`, foot: `<button type="button" class="ghost" data-action="close-drawer">关闭</button>` });
}
function openImport() {
  openDrawer({ title: '批量接入账号', subtitle: '每行一个：邮箱 密码。支持空格、逗号、冒号、竖线分隔', body: `<form data-form="arena-import" class="stack"><div class="field"><label for="importText">账号列表</label><textarea id="importText" name="raw_text" rows="10" class="mono" style="font-size:calc(12px * var(--fs-scale, 1))" placeholder="user1@example.com  password1&#10;user2@example.com, password2" spellcheck="false"></textarea><span class="help">提交后由登录器逐个登录并抓取会话，过程在页面顶部显示进度；密码落盘时按 Windows 当前用户加密。</span></div><div id="importPreview" class="faint" style="font-size:calc(12px * var(--fs-scale, 1))"></div><div class="row"><button type="submit" class="primary">${icon('plus')}开始接入</button><button type="button" class="ghost" data-action="close-drawer">取消</button></div></form>` });
  const ta = $('#importText'); ta.addEventListener('input', async () => { const v = ta.value.trim(); if (!v) { $('#importPreview').textContent = ''; return; } try { const r = await api('/api/arena/accounts/preview', { method: 'POST', body: JSON.stringify({ raw_text: v }) }); $('#importPreview').textContent = `解析到 ${r.rows.length} 个账号，其中 ${r.rows.filter(x => !x.has_password).length} 个缺密码`; } catch (_) { /* 预览失败不打断输入 */ } });
  ta.focus();
}
function openRegister() {
  openDrawer({ title: '自动注册账号', subtitle: '登录器申请临时邮箱并完成注册，注册后自动登录入池', body: `<form data-form="arena-register" class="stack"><div class="form-row"><div class="field"><label for="regCount">数量</label><input id="regCount" name="count" type="number" min="1" max="20" value="3"></div><div class="field"><label for="regPwd">统一密码</label><input id="regPwd" name="password" type="password" autocomplete="new-password" placeholder="${A.settings?.register_password ? '留空使用已保存密码' : '必填'}"></div></div><div class="banner warn">验证码环节若需要人工，登录器会弹出浏览器窗口；此页面只显示进度。</div><div class="row"><button type="submit" class="primary">${icon('bolt')}开始注册</button><button type="button" class="ghost" data-action="close-drawer">取消</button></div></form>` });
}

// ---------- 会话 ----------
export function renderArenaSessions() {
  if (A.down) return downState();
  if (!A.sessions) return loadingState('会话');
  const { sessions, families, synced_at, probe_count } = A.sessions;
  const f = A.filters, routed = new Set((A.routes?.routes || []).map(r => r.session_id));
  let rows = sessions.filter(s => (!f.family || (f.family === '未识别' ? !s.detected_model || s.detected_model === 'Unknown' : s.detected_model === f.family)) && (!f.onlyProbe || s.is_probe) && (!f.session || (s.title + ' ' + s.account + ' ' + s.id).toLowerCase().includes(f.session.toLowerCase())));
  const { key, dir } = A.sort; rows.sort((a, b) => (String(a[key] ?? '').localeCompare(String(b[key] ?? ''))) * (dir === 'asc' ? 1 : -1));
  const sel = A.selected; const allSel = rows.length && rows.every(r => sel.has(r.id));
  const th = (k, label, cls = '') => `<th class="${cls}" data-action="arena-sort" data-key="${k}" aria-sort="${key === k ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}">${label}</th>`;
  const famChips = Object.entries(families).sort((a, b) => b[1] - a[1]).map(([name, n]) => `<button type="button" class="chip ${f.family === name ? 'info' : ''}" data-action="arena-family" data-family="${attr(name)}">${esc(name)} <b>${n}</b></button>`).join('');
  return `<div class="view">${head('Arena 会话池', '会话', '按识别出的模型归类；勾选后可批量识别、加入路由或删除。探针会话可一键清理。', `<button type="button" class="ghost danger" data-action="arena-purge-ungrouped">${icon('trash')}删除未归组会话</button><button type="button" data-action="arena-select-probes">${icon('warn')}选中全部探针 (${probe_count})</button><button type="button" class="primary" data-action="arena-create-sessions">${icon('plus')}批量开会话</button>`)}
    ${jobsBanner()}
    <div class="filters"><div class="search">${icon('search')}<input data-arena-filter="session" value="${attr(f.session)}" placeholder="搜索标题、账号、ID…" autocomplete="off"></div><label class="chip" style="cursor:pointer"><input type="checkbox" data-action="arena-only-probe" ${f.onlyProbe ? 'checked' : ''} style="width:auto;margin-right:4px">只看探针</label><span class="hint" style="margin-left:auto">共 ${sessions.length} 条 · 同步 ${esc(relTime(synced_at))} <button type="button" class="link" data-action="arena-sync">立即同步</button></span></div>
    <div class="row wrap" style="margin-bottom:12px">${f.family ? `<button type="button" class="chip bad" data-action="arena-family" data-family="">清除筛选 ×</button>` : ''}${famChips}</div>
    ${sel.size ? `<div class="banner info row between"><span>已选 ${sel.size} 条</span><span class="row"><button type="button" class="small" data-action="arena-detect-selected">${icon('eye')}识别模型</button><button type="button" class="small" data-action="arena-route-selected">${icon('bolt')}设为路由</button><button type="button" class="small danger" data-action="arena-delete-selected">${icon('trash')}删除</button><button type="button" class="small ghost" data-action="arena-clear-selection">取消选择</button></span></div>` : ''}
    ${rows.length ? `<div class="card table-scroll"><table class="table"><thead><tr><th class="cb"><input type="checkbox" data-action="arena-select-all" ${allSel ? 'checked' : ''} aria-label="全选"></th>${th('title', '标题')}${th('detected_model', '识别模型')}${th('account', '账号')}${th('created_at', '创建')}<th>标记</th></tr></thead><tbody>${rows.slice(0, 400).map(s => `<tr class="${sel.has(s.id) ? 'sel' : ''}"><td class="cb"><input type="checkbox" data-action="arena-select" data-id="${attr(s.id)}" ${sel.has(s.id) ? 'checked' : ''} aria-label="选择 ${attr(s.title)}"></td><td><button type="button" class="link truncate" style="max-width:320px;text-align:left" data-action="arena-session" data-id="${attr(s.id)}">${esc(s.title || s.id)}</button>${s.is_probe ? ' <span class="chip warn plain">探针</span>' : ''}</td><td>${s.detected_model && s.detected_model !== 'Unknown' ? `<span class="chip ${s.verified ? 'ok' : ''}">${esc(s.detected_model)}</span>${s.drift ? ' <span class="chip bad plain">漂移</span>' : ''}` : '<span class="faint">未识别</span>'}</td><td class="mono faint" style="font-size:calc(11px * var(--fs-scale, 1))">${esc(s.account)}</td><td class="faint">${esc(relTime(s.created_at))}</td><td>${routed.has(s.id) ? '<span class="chip info plain">路由中</span>' : ''}${s.pinned ? '<span class="chip plain">钉住</span>' : ''}</td></tr>`).join('')}</tbody></table>${rows.length > 400 ? `<div class="faint" style="padding:10px 12px">仅显示前 400 条，请加筛选</div>` : ''}</div>` : empty('没有匹配的会话', sessions.length ? '换个筛选条件' : '先接入账号并同步')}
  </div>`;
}
function openSession(id) {
  const s = (A.sessions?.sessions || []).find(x => x.id === id); if (!s) return;
  const routes = (A.routes?.routes || []).filter(r => r.session_id === id).map(r => r.model);
  openDrawer({ title: s.title || id, subtitle: `${s.account} · ${fmtDate(s.created_at)}`, body: `<dl class="kv"><dt>会话 ID</dt><dd>${esc(id)}</dd><dt>识别模型</dt><dd>${esc(s.detected_model || '未识别')}${s.verified ? '（已验证）' : ''}</dd><dt>标注模型</dt><dd>${esc(s.model || '—')}</dd><dt>验证次数</dt><dd>${s.verification_count}</dd><dt>最近验证</dt><dd>${esc(s.last_verified_at || '—')}</dd><dt>路由</dt><dd>${esc(routes.join('，') || '—')}</dd></dl>
    <form data-form="arena-route-set" data-session="${attr(id)}" style="margin-top:16px"><div class="field"><label for="routeModel">设为路由（模型名）</label><div class="row"><input id="routeModel" name="model" value="${attr(s.detected_model && s.detected_model !== 'Unknown' ? s.detected_model : '')}" placeholder="例如 claude-fable-5" autocomplete="off" spellcheck="false"><button type="submit" class="small primary">保存</button></div><span class="help">外部客户端用这个模型名调用时，请求走此会话。</span></div></form>
    ${(A.groups?.groups || []).length ? `<div class="field" style="margin-top:12px"><label>加入分组</label><div class="row wrap">${A.groups.groups.map(g => `<button type="button" class="chip ${g.members.some(m => m.id === id) ? 'ok' : ''}" data-action="arena-group-member" data-id="${attr(g.id)}" data-session="${attr(id)}" data-add="${g.members.some(m => m.id === id) ? 0 : 1}">${esc(g.name)}</button>`).join('')}</div></div>` : ''}
    <div class="row wrap" style="margin-top:12px"><button type="button" class="small" data-action="arena-detect-one" data-id="${attr(id)}">${icon('eye')}识别模型</button><button type="button" class="small" data-action="arena-pin" data-id="${attr(id)}" data-pinned="${s.pinned ? 0 : 1}">${s.pinned ? '取消钉住' : '钉住'}</button><button type="button" class="small danger" data-action="arena-delete-one" data-id="${attr(id)}">${icon('trash')}删除会话</button></div>`, foot: `<button type="button" class="ghost" data-action="close-drawer">关闭</button>` });
}
function openCreateSessions(email = '') {
  const ready = (A.accounts?.accounts || A.overview ? (A.accounts?.accounts || []) : []).filter(a => a.health === 'ready');
  openDrawer({ title: '批量开会话', subtitle: '为选中账号新建会话并可选自动识别模型', body: `<form data-form="arena-create-sessions" class="stack"><div class="field"><label>账号${ready.length ? '' : '（先到账号页同步）'}</label><div class="pick-grid">${ready.map(a => `<label class="pick ${!email || a.email === email ? 'on' : ''}"><input type="checkbox" name="emails" value="${attr(a.email)}" ${!email || a.email === email ? 'checked' : ''}><span class="t"><b class="truncate">${esc(a.email)}</b><small>${a.sessions_count || 0} 会话</small></span></label>`).join('')}</div></div><div class="form-row"><div class="field"><label for="csCount">每账号数量</label><input id="csCount" name="count" type="number" min="1" max="10" value="1"></div><div class="field"><label><input type="checkbox" name="auto_detect" checked style="width:auto;margin-right:6px">开完后立即识别模型</label></div></div><div class="row"><button type="submit" class="primary" ${ready.length ? '' : 'disabled'}>开始</button><button type="button" class="ghost" data-action="close-drawer">取消</button></div></form>` });
}

// ---------- 路由 ----------
export function renderArenaRoutes() {
  if (A.down) return downState();
  if (!A.routes || !A.keys) return loadingState('路由');
  const { routes = [], default_model = '' } = A.routes || {}; const allKeys = (A.keys && A.keys.keys) || []; const keys = allKeys.filter(k => !k.revoked); const revoked = allKeys.filter(k => k.revoked);
  const base = `${location.protocol}//${location.hostname}:9091`;
  const stats = A.overview?.stats || {};
  return `<div class="view">${head('Arena 中继', '路由', '外部 Agent Harness 用 OpenAI 或 Anthropic 格式接入这里，按模型名转到对应会话。', `<button type="button" class="primary" data-action="arena-new-key">${icon('plus')}生成 API Key</button>`)}
    ${jobsBanner()}
    <div class="metrics" style="grid-template-columns:repeat(4,minmax(0,1fr))"><div class="metric"><span>累计请求</span><b class="num">${fmtNum(stats.count || 0)}</b><small>成功 ${fmtNum(stats.ok || 0)} · 失败 ${fmtNum(stats.fail || 0)}</small></div><div class="metric"><span>输出 token</span><b class="num">${fmtNum(stats.output_tokens || 0)}</b><small>输入 ${fmtNum(stats.input_tokens || 0)}</small></div><div class="metric"><span>路由条数</span><b class="num">${routes.length}</b><small>默认 ${esc(default_model || 'auto')}</small></div><div class="metric"><span>有效 Key</span><b class="num">${keys.length}</b><small>${revoked.length} 个已吊销</small></div></div>
    <div class="grid-2" style="align-items:start">
      <section class="stack">
        <div class="card pad"><div class="section-head"><h2>接入地址</h2><span class="hint">复制到客户端环境变量</span></div>
          <div class="stack" style="gap:8px">${[['OpenAI 兼容', 'OPENAI_BASE_URL', `${base}/v1`], ['Anthropic 兼容', 'ANTHROPIC_BASE_URL', base], ['Claude Code 一键', 'set', `set ANTHROPIC_BASE_URL=${base}&& set ANTHROPIC_API_KEY=<你的 Key>&& claude`]].map(([label, k, v]) => `<div class="row between" style="gap:12px"><div class="min0"><div style="font-size:calc(12px * var(--fs-scale, 1));font-weight:600">${label}</div><code class="mono" style="display:block;font-size:calc(12px * var(--fs-scale, 1));color:var(--muted);white-space:normal">${esc(k === 'set' ? v : `${k}=${v}`)}</code></div><button type="button" class="small ghost" data-action="copy-text" data-text="${attr(k === 'set' ? v : v)}">${icon('copy')}复制</button></div>`).join('')}</div></div>
        <div class="card pad"><div class="section-head"><h2>API Key</h2><span class="hint">明文只在生成时显示一次</span></div>
          ${keys.length ? `<table class="table"><thead><tr><th>名称</th><th>前缀</th><th class="r">请求</th><th>最近使用</th><th>限定模型</th><th></th></tr></thead><tbody>${keys.map(k => `<tr><td>${esc(k.name)}</td><td class="mono">${esc(k.prefix)}…</td><td class="r num">${k.requests || 0}</td><td class="faint">${esc(relTime(k.last_used_at) || '未使用')}</td><td>${(k.allowed_models || []).map(m => `<span class="chip plain">${esc(m)}</span>`).join(' ') || '<span class="faint">全部</span>'}</td><td class="r"><button type="button" class="small ghost danger" data-action="arena-revoke-key" data-id="${attr(k.id)}" data-name="${attr(k.name)}">吊销</button></td></tr>`).join('')}</tbody></table>` : empty('还没有 API Key', '生成后填到客户端的 OPENAI_API_KEY 或 ANTHROPIC_API_KEY')}</div>
      </section>
      <section class="stack">
        <div class="card pad"><div class="section-head"><h2>模型路由</h2><button type="button" class="small" data-action="arena-add-route">${icon('plus')}新增</button></div>
          ${routes.length ? `<div class="stack" style="gap:6px">${routes.map(r => `<div class="row between" style="padding:8px 10px;border:1px solid var(--line);border-radius:var(--r-ctl)"><div class="min0"><div class="row"><b class="mono" style="font-size:calc(13px * var(--fs-scale, 1))">${esc(r.model)}</b>${r.model === default_model ? '<span class="chip plain">默认</span>' : ''}${r.verified ? '<span class="chip ok plain">已验证</span>' : (r.detected_model ? '' : '<span class="chip warn plain">未识别</span>')}</div><div class="faint truncate" style="font-size:calc(11px * var(--fs-scale, 1))">${esc(r.title || r.session_id)} · ${esc(r.account || '未知账号')}${r.pool_size ? ` · 池 ${r.pool_size}` : ''}</div></div><div class="row"><button type="button" class="small ghost" data-action="arena-session" data-id="${attr(r.session_id)}">会话</button><button type="button" class="small ghost danger" data-action="arena-delete-route" data-model="${attr(r.model)}">删除</button></div></div>`).join('')}</div>` : empty('没有路由', '在会话页选中会话后「设为路由」')}</div>
        <div class="card pad"><div class="section-head"><h2>最近请求</h2><button type="button" class="link" data-action="arena-open-stats">查看全部</button></div>${recentRequests(8)}</div>
      </section>
    </div>
  </div>`;
}
function recentRequests(n) {
  const rec = (A.statsFull?.recent || []).slice(-n).reverse();
  if (!rec.length) return `<div class="faint" style="font-size:calc(12px * var(--fs-scale, 1))">还没有经过中继的请求。<button type="button" class="link" data-action="arena-open-stats">刷新</button></div>`;
  return `<table class="table"><thead><tr><th>时间</th><th>Key</th><th>模型</th><th class="r">耗时</th><th>结果</th></tr></thead><tbody>${rec.map(r => `<tr><td class="faint">${esc(relTime(r.at))}</td><td>${esc(r.key)}</td><td class="mono">${esc(r.model)}</td><td class="r num">${r.ms} ms</td><td><span class="chip ${r.ok ? 'ok' : 'bad'} plain">${r.status}${r.stream ? ' 流式' : ''}</span></td></tr>`).join('')}</tbody></table>`;
}
function openNewKey() {
  const models = (A.routes?.models || []);
  openDrawer({ title: '生成 API Key', subtitle: '给每个客户端单独一把，便于吊销与统计', body: `<form data-form="arena-new-key" class="stack"><div class="field"><label for="keyName">名称</label><input id="keyName" name="name" placeholder="例如 claude-code 笔记本" autocomplete="off" required></div><div class="field"><label>限定模型（不选 = 全部）</label><div class="pick-grid">${models.map(m => `<label class="pick"><input type="checkbox" name="allowed_models" value="${attr(m)}"><span class="t"><b>${esc(m)}</b></span></label>`).join('') || '<span class="faint">还没有路由</span>'}</div></div><button type="submit" class="primary">${icon('bolt')}生成</button><div id="keyResult"></div></form>` });
  $('#keyName').focus();
}
function openAddRoute() {
  const sessions = (A.sessions?.sessions || []).filter(s => s.detected_model && s.detected_model !== 'Unknown').slice(0, 200);
  openDrawer({ title: '新增路由', subtitle: '模型名 → 会话', body: `<form data-form="arena-route-add" class="stack"><div class="field"><label for="arModel">模型名</label><input id="arModel" name="model" list="arModels" placeholder="客户端请求里的 model 值" autocomplete="off" spellcheck="false" required><datalist id="arModels">${[...new Set(sessions.map(s => s.detected_model))].map(m => `<option value="${attr(m)}">`).join('')}</datalist></div><div class="field"><label for="arSession">会话</label><select id="arSession" name="session_id" required>${sessions.map(s => `<option value="${attr(s.id)}">${esc(s.detected_model)} · ${esc(s.title)} · ${esc(s.account)}</option>`).join('')}</select>${sessions.length ? '' : '<span class="help">没有已识别模型的会话，先到会话页识别</span>'}</div><div class="row"><button type="submit" class="primary" ${sessions.length ? '' : 'disabled'}>保存</button><button type="button" class="ghost" data-action="close-drawer">取消</button></div></form>` });
}


// ---------- 模型组 ----------
const TIER = { strong: ['最强', 'ok'], daily: ['常用', 'info'], custom: ['自建', 'violet'] };
const SRC = { detected: '已识别', title: '按标题', manual: '手动' };
export function renderArenaGroups() {
  if (A.down) return downState();
  if (!A.groups) return loadingState('模型组');
  const { groups, unmatched, base_url } = A.groups;
  const total = groups.reduce((n, g) => n + g.member_count, 0);
  return `<div class="view">${head('Arena 模型组', '模型组', '按模型归组，不关心会话来自哪个账号。每组固定一个模型名与一把 Key，外部客户端用组名当 model 调用；组内会话自动轮转。点卡片进配置面板，拖卡片调顺序。', `<button type="button" data-action="arena-sync">${icon('refresh')}重新归组</button><button type="button" data-action="arena-common-join" title="只读预览常用组的候选会话，确认后才排队发送接入消息">${icon('bolt')}接入常用组${groups.some(g => g.common) ? ` (${groups.filter(g => g.common).length})` : ''}</button><button type="button" class="primary" data-action="arena-group-new">${icon('plus')}自建分组</button>`)}
    ${jobsBanner()}
    <div class="metrics" style="grid-template-columns:repeat(4,minmax(0,1fr))"><div class="metric"><span>分组</span><b class="num">${groups.length}</b><small>${groups.filter(g => g.tier === 'strong').length} 组最强 · ${groups.filter(g => g.custom).length} 组自建</small></div><div class="metric"><span>已归组会话</span><b class="num">${total}</b><small>可用 ${groups.reduce((n, g) => n + g.ready_count, 0)}</small></div><div class="metric"><span>未归组模型</span><b class="num">${Object.keys(unmatched).length}</b><small>${Object.values(unmatched).reduce((a, b) => a + b, 0)} 条会话</small></div><div class="metric"><span>Base URL</span><b style="font-size:calc(14px * var(--fs-scale, 1))" class="mono">${esc(base_url)}</b><small><button type="button" class="link" data-action="copy-text" data-text="${attr(base_url)}">复制</button> · OpenAI 加 /v1</small></div></div>
    <div class="group-grid stagger" id="groupGrid">${groups.map(groupCard).join('')}</div>
    ${Object.keys(unmatched).length ? `<section class="card pad" style="margin-top:16px"><div class="section-head"><h2>未归组的模型</h2><span class="hint">点一个即可为它建组</span></div><div class="row wrap">${Object.entries(unmatched).map(([m, n]) => `<button type="button" class="chip" data-action="arena-group-new" data-match="${attr(m)}">${esc(m)} <b>${n}</b></button>`).join('')}</div></section>` : ''}
  </div>`;
}
function groupCard(g) {
  const [tl, tt] = TIER[g.tier] || TIER.custom;
  return `<article class="card group hover ${g.ready_count ? '' : 'off'}" data-key="${attr(g.id)}" data-group-card="${attr(g.id)}" draggable="true" data-action="arena-group-open" data-id="${attr(g.id)}" tabindex="0" role="button" aria-label="打开 ${attr(g.name)} 配置">
    <header class="row" style="gap:10px;align-items:flex-start"><span class="drag-handle" aria-hidden="true">⋮⋮</span><div class="min0 grow"><div class="row wrap" style="gap:6px"><b style="font-size:calc(15px * var(--fs-scale, 1))">${esc(g.name)}</b><span class="chip ${tt} plain">${tl}</span>${g.custom ? '' : '<span class="chip plain">预置</span>'}</div><div class="muted mono" style="font-size:calc(11px * var(--fs-scale, 1));margin-top:2px;overflow-wrap:anywhere">model = ${esc(g.id)}${g.match?.length ? ` · 匹配 ${esc(g.match.join(', '))}` : ''}</div></div><span class="chip ${g.ready_count ? 'ok' : 'warn'}" style="flex:none">${g.ready_count} 可用 / ${g.member_count}</span><button type="button" class="ghost icon xbtn" data-action="arena-group-purge" data-id="${attr(g.id)}" data-name="${attr(g.name)}" data-count="${g.member_count}" aria-label="删除该组全部会话" title="删除该组全部会话">×</button></header>
    ${g.rule ? `<div class="banner warn" style="margin:8px 0 0;padding:6px 10px;font-size:calc(12px * var(--fs-scale, 1))">${esc(g.rule)}</div>` : ''}
    <div class="meta" style="margin-top:8px"><span>已识别 <b>${g.detected_count}</b></span><span>成功率 <b>${g.success_rate != null ? Math.round(g.success_rate * 100) + '%' : '—'}</b></span><span>耗时 <b>${g.avg_ms ? g.avg_ms + ' ms' : '—'}</b></span><span>Key <b class="mono">${g.key_prefix ? esc(g.key_prefix) + '…' : '无'}</b></span></div>
    <div class="row wrap" style="margin-top:10px"><button type="button" class="small primary" data-action="arena-group-key" data-id="${attr(g.id)}" data-name="${attr(g.name)}">${icon('bolt')}接入信息</button><button type="button" class="small" data-action="arena-group-open" data-id="${attr(g.id)}">成员 (${g.member_count})</button><button type="button" class="small ghost" data-action="arena-group-verify" data-id="${attr(g.id)}" title="按识别结果复核成员，不属于本组的移出">${icon('check')}一键检查</button><button type="button" class="small ghost ${g.common ? 'active' : ''}" data-action="arena-group-common" data-id="${attr(g.id)}" data-common="${g.common ? 0 : 1}" title="常用组会出现在「接入常用组」预览里；这只是用户标记，不自动发送">${g.common ? '★ 常用' : '☆ 设为常用'}</button></div>
  </article>`;
}
function memberRow(g, m) {
  return `<div class="row between member ${m.account_health === 'ready' ? '' : 'dim'}"><div class="min0"><div class="row wrap" style="gap:6px"><span class="mono" style="font-size:calc(12px * var(--fs-scale, 1))">${esc(m.canon)}</span><span class="chip plain" style="font-size:calc(11px * var(--fs-scale, 1))">${SRC[m.source] || m.source}</span>${m.verified ? '<span class="chip ok plain" style="font-size:calc(11px * var(--fs-scale, 1))">已验证</span>' : ''}${m.peer ? `<span class="chip info plain" style="font-size:calc(11px * var(--fs-scale, 1))">peer ${esc(m.peer)}</span>` : ''}</div><div class="faint" style="font-size:calc(11px * var(--fs-scale, 1));overflow-wrap:anywhere">${esc(m.title)} · ${esc(m.account)}${m.account_health !== 'ready' ? ' · 账号' + esc(HEALTH[m.account_health]?.[0] || m.account_health) : ''}</div></div><div class="row" style="flex:none">${m.peer ? '' : `<button type="button" class="small ghost" data-action="arena-group-join" data-id="${attr(g.id)}" data-session="${attr(m.id)}" title="把这条会话接入 Workbench 成为 Agent">接入</button>`}<button type="button" class="small ghost" data-action="arena-session" data-id="${attr(m.id)}">详情</button><button type="button" class="small ghost danger" data-action="arena-group-member" data-id="${attr(g.id)}" data-session="${attr(m.id)}" data-add="0" aria-label="移出">×</button></div></div>`;
}
function keyPanel(g) {
  const masked = g.key_prefix ? `${g.key_prefix}${'•'.repeat(14)}` : '';
  return `<div class="card pad" id="groupKeyPanel"><div class="section-head"><h2>该组 Key</h2><span class="hint">${g.key_prefix ? '完整值只在本机显示' : '还没有 Key'}</span></div>
    ${g.key_prefix ? `<div class="row" style="gap:8px"><code class="mono keybox" id="groupKeyText" data-masked="${attr(masked)}">${esc(masked)}</code><button type="button" class="small" data-action="arena-group-reveal" data-id="${attr(g.id)}" title="显示或隐藏完整 Key">${icon('eye')}</button><button type="button" class="small" data-action="arena-group-copykey" data-id="${attr(g.id)}">${icon('copy')}复制 Key</button></div>` : ''}
    <div id="groupKeyResult"></div>
    <button type="button" class="small ${g.key_prefix ? 'danger ghost' : 'primary'}" data-action="arena-group-rotate" data-id="${attr(g.id)}" style="margin-top:8px">${icon('refresh')}${g.key_prefix ? '轮换 Key（旧 Key 立即失效）' : '生成 Key'}</button></div>`;
}
function openGroupDetail(gid, tab = 'members') {
  const g = (A.groups?.groups || []).find(x => x.id === gid); if (!g) return;
  const base = A.groups?.base_url || 'http://127.0.0.1:9091';
  const [tl] = TIER[g.tier] || TIER.custom;
  A.openGroup = gid; A.groupTab = tab;
  const tabs = [['members', `成员 ${g.member_count}`], ['access', '接入信息'], ['edit', '编辑']];
  const body = tab === 'members' ? `<div class="row wrap" style="margin-bottom:10px"><button type="button" class="small" data-action="arena-group-verify" data-id="${attr(g.id)}">${icon('check')}一键检查成员</button><button type="button" class="small ghost" data-action="arena-sync">${icon('refresh')}重新归组</button><span class="grow"></span><button type="button" class="small ghost danger" data-action="arena-group-purge" data-id="${attr(g.id)}" data-name="${attr(g.name)}" data-count="${g.member_count}">${icon('trash')}删除全部会话</button></div><div class="stack" style="gap:4px">${g.members.length ? g.members.map(m => memberRow(g, m)).join('') : '<div class="faint" style="font-size:calc(12px * var(--fs-scale, 1))">还没有会话。到「会话」页选一条后「加入分组」，或在「账号」页为可用账号开新会话。</div>'}</div>`
    : tab === 'access' ? `<div class="stack">${keyPanel(g)}
    <div class="card pad"><div class="section-head"><h2>OpenAI 格式</h2></div><pre class="mono codeblock">OPENAI_BASE_URL=${esc(base)}/v1\nOPENAI_API_KEY=&lt;该组 Key&gt;\nmodel=${esc(gid)}</pre><button type="button" class="small" data-action="arena-group-copycfg" data-id="${attr(gid)}" data-fmt="openai">${icon('copy')}复制（含 Key）</button></div>
    <div class="card pad"><div class="section-head"><h2>Anthropic 格式（Claude Code）</h2></div><pre class="mono codeblock">set ANTHROPIC_BASE_URL=${esc(base)}\nset ANTHROPIC_API_KEY=&lt;该组 Key&gt;\nset ANTHROPIC_MODEL=${esc(gid)}\nclaude</pre><button type="button" class="small" data-action="arena-group-copycfg" data-id="${attr(gid)}" data-fmt="anthropic">${icon('copy')}复制一行命令（含 Key）</button></div></div>`
    : groupEditorForm(g);
  openDrawer({ title: g.name, subtitle: `model = ${gid} · ${tl}${g.custom ? '' : ' · 预置'} · ${g.ready_count} 可用 / ${g.member_count}`, body: `<div class="segmented" role="tablist" style="margin-bottom:14px">${tabs.map(([k, l]) => `<button type="button" role="tab" class="${tab === k ? 'active' : ''}" data-action="arena-group-tab" data-id="${attr(gid)}" data-tab="${k}">${l}</button>`).join('')}</div>${body}`, foot: `<button type="button" class="ghost" data-action="close-drawer">关闭</button>${g.custom ? `<button type="button" class="ghost danger" data-action="arena-group-delete" data-id="${attr(g.id)}" data-name="${attr(g.name)}">删除分组</button>` : ''}` });
}
function groupEditorForm(g, match = '') {
  return `<form data-form="arena-group" data-id="${attr(g?.id || '')}" class="stack">
    <div class="form-row"><div class="field"><label for="grpName">显示名</label><input id="grpName" name="name" value="${attr(g?.name || (match ? match : ''))}" required autocomplete="off"></div><div class="field"><label for="grpId">model 值（英文小写）</label><input id="grpId" name="id" value="${attr(g?.id || match)}" ${g ? 'disabled' : ''} placeholder="例如 claude-strong" autocomplete="off" spellcheck="false"></div></div>
    <div class="field"><label for="grpMatch">匹配的模型前缀（逗号分隔）</label><input id="grpMatch" name="match" value="${attr((g?.match || (match ? [match] : [])).join(', '))}" placeholder="claude-fable-5, claude-opus-5" autocomplete="off" spellcheck="false"><span class="help">前缀匹配，思考档位（low / medium / high / thinking）与日期后缀自动忽略：填 gpt-5.6 会包含 luna、sol、terra；填 gpt-5.6-sol 只含 sol。</span></div>
    <div class="form-row"><div class="field"><label for="grpTier">档位</label><select id="grpTier" name="tier">${['strong', 'daily', 'custom'].map(t => `<option value="${t}" ${(g?.tier || 'custom') === t ? 'selected' : ''}>${TIER[t][0]}</option>`).join('')}</select></div><div class="field"><label for="grpOrder">排序（小的靠前）</label><input id="grpOrder" name="order" type="number" value="${g?.order ?? ''}"></div></div>
    <div class="field"><label for="grpRule">备注 / 判定规则</label><textarea id="grpRule" name="rule" rows="2">${esc(g?.rule || '')}</textarea></div>
    <div class="row"><button type="submit" class="primary">${g ? '保存' : '创建并生成 Key'}</button><button type="button" class="ghost" data-action="close-drawer">取消</button></div><div id="groupKeyResult"></div></form>`;
}
const SKIP = { already_online: '已有在线 Agent', in_flight: '已有在途作业', group_missing: '分组不存在', duplicate_selection: '重复选择', aborted: '前一项凭据失效后中止', account_exhausted: '账号额度用尽', account_cooldown: '账号冷却中', account_banned: '账号已失效', account_unauthenticated: '账号未登录', account_degraded: '账号异常', account_unknown: '账号状态未知' };
async function openCommonJoin(extra = {}) {
  const groups = A.groups?.groups || [];
  const common = groups.filter(g => g.common).map(g => g.id);
  if (!common.length) return toast('还没有常用组：先在模型组卡片上点「设为常用」', 'warn', { ms: 6000 });
  const pv = await post('join/preview', { group_ids: common, extra });  // 只读预览：服务端不发送任何消息
  const titles = {};
  pv.groups.forEach(g => (g.candidates || []).forEach(c => { titles[`${g.group}|${c.session_id}`] = c.title || c.session_id; }));
  A.commonPreview = { preview_token: pv.preview_token, request_id: newRequestId(), titles };
  const fs = 'font-size:calc(11px * var(--fs-scale, 1))';
  const body = pv.groups.map(g => `<section class="card pad" style="margin-bottom:10px"><div class="row wrap between" style="gap:6px"><b>${esc(g.name)}</b><span class="row wrap" style="gap:6px">${g.error ? `<span class="chip bad">${esc(g.error)}</span>` : ''}<span class="chip plain" style="${fs}">${g.source === 'custom' ? '自建' : '预置'}</span>${(g.online_agents || []).map(a => `<span class="chip ok plain" style="${fs}">在线 Agent ${esc(a.peer)}</span>`).join('')}${(g.in_flight || []).map(j => `<span class="chip info plain" style="${fs}">在途 ${esc(JOIN_STATE[j.status] ? JOIN_STATE[j.status][0] : j.status)}</span>`).join('')}${g.skip_reason ? `<span class="chip warn plain" style="${fs}">默认跳过：${esc(SKIP[g.skip_reason] || g.skip_reason)}</span>` : ''}</span></div>
    <div class="stack" style="gap:4px;margin-top:8px">${(g.candidates || []).length ? g.candidates.map(c => `<label class="row between member ${c.eligible ? '' : 'dim'}" style="gap:8px"><span class="row min0" style="gap:8px"><input type="checkbox" name="sel" value="${attr(g.group + '|' + c.session_id)}" ${c.selected ? 'checked' : ''} ${c.eligible ? '' : 'disabled'} style="width:auto"><span class="min0"><b class="truncate">${esc(c.title || c.session_id)}</b><div class="faint" style="${fs};overflow-wrap:anywhere">${esc(c.canon)} · ${esc(c.account)} · <span class="mono">${esc(c.session_id)}</span></div></span></span><span style="flex:none;${fs}">${c.skip_reason ? `<span class="chip plain">${esc(SKIP[c.skip_reason] || c.skip_reason)}</span>` : c.selected ? '<span class="chip ok plain">默认选中</span>' : '<span class="chip plain">可加选</span>'}</span></label>`).join('') : '<div class="faint">该组没有会话</div>'}</div></section>`).join('');
  const n = pv.will_send_total || 0;
  openDrawer({ title: '接入常用组（预览）', subtitle: '只读预览：确认前不发送任何消息。默认每组 1 个可用会话；已有在线 Agent 或在途作业的组默认跳过，可手动加选同组第二个。', body: `<form data-form="arena-common-confirm" class="stack">${body}<div class="banner info"><b>将发送 <span id="commonJoinCount">${n}</span> 条接入消息</b> · 每条消耗对应账号 1 次对话；服务端按队列（并发 ${esc(String((A.joinConfig && A.joinConfig.max_concurrency) || 1))}）逐个发送，等待心跳最长约 ${esc(String(pv.groups.length ? '2 分钟/条' : '—'))}。</div><div class="row"><button type="submit" class="primary" ${n ? '' : 'disabled'}>${icon('bolt')}确认入队</button><button type="button" class="ghost" data-action="arena-common-join">重新预览</button><button type="button" class="ghost" data-action="close-drawer">取消</button></div><div id="commonJoinResult"></div></form>` });
  setTimeout(() => { const box = $('form[data-form="arena-common-confirm"]'); if (!box) return; box.addEventListener('change', () => { const k = $$('input[name="sel"]:checked', box).length; const el = $('#commonJoinCount', box); if (el) el.textContent = String(k); const btn = $('button[type="submit"]', box); if (btn) btn.disabled = !k; }); }, 0);
}
function openGroupKey(gid) { openGroupDetail(gid, 'access'); }
function openGroupEditor(gid = '', match = '') {
  if (gid) return openGroupDetail(gid, 'edit');
  openDrawer({ title: '自建分组', subtitle: '组名会成为客户端的 model 值；匹配前缀决定哪些模型自动进组', body: groupEditorForm(null, match) });
  $('#grpName').focus();
}
async function fetchGroupKey(gid) { const r = await api(`/api/arena/groups/key?id=${encodeURIComponent(gid)}`); if (!r.key) throw new Error('这把 Key 生成于旧版本，没有留存明文；点「轮换 Key」生成新的'); return r.key; }

// ---------- 榜单 ----------
export function renderArenaLeaderboard() {
  if (A.down) return downState();
  if (!A.leaderboard) return loadingState('榜单');
  const { boards, fetched_at, date, age_hours, refreshing, stale } = A.leaderboard; const f = A.filters; const board = boards[f.board] || { models: [], meta: {} };
  const seg = Object.keys(BOARD_LABEL).map(k => `<button type="button" class="${f.board === k ? 'active' : ''}" data-action="arena-board" data-board="${k}">${BOARD_LABEL[k]}${boards[k] ? ` <span class="faint">${boards[k].models.length}</span>` : ''}</button>`).join('');
  const top = Math.max(1, ...(board.models.map(m => Number(m.score) || 0)));
  return `<div class="view">${head('Arena 榜单', '榜单', 'arena.ai 公开排行榜快照，叠加本地会话池与中继成功率；有本地会话的模型可以直接设为路由。', `<button type="button" data-action="arena-refresh-lb">${icon('refresh')}刷新快照</button>`)}
    ${jobsBanner()}
    <div class="filters"><div class="segmented">${seg}</div><span class="grow"></span>${refreshing ? `<span class="chip info live">正在从上游拉取最新快照</span>` : stale ? `<span class="chip warn">快照已 ${esc(String(age_hours ?? '?'))} 小时没更新，拉取失败可点「刷新快照」重试</span>` : ''}<span class="hint">快照日期 ${esc(date || '—')} · 榜单更新 ${esc(board.meta?.last_updated || '—')} · 拉取于 ${esc(relTime(fetched_at))}</span></div>
    ${board.models.length ? `<div class="card table-scroll"><table class="table lb"><thead><tr><th class="r">#</th><th>模型</th><th>厂商</th><th class="r">分数</th><th class="r">投票</th><th>所属分组</th><th>本地会话</th><th>中继成功率</th><th></th></tr></thead><tbody>${board.models.map(m => `<tr><td class="r num">${m.rank}</td><td><b>${esc(m.model)}</b>${m.routed ? ' <span class="chip info plain">已路由</span>' : ''}</td><td class="muted">${esc(m.vendor || '')}</td><td class="r"><div class="row" style="justify-content:flex-end;gap:8px"><div class="bar" style="width:80px"><i style="width:${Math.round((Number(m.score) || 0) / top * 100)}%"></i></div><span class="num">${m.score}</span><span class="faint" style="font-size:calc(11px * var(--fs-scale, 1))">±${m.ci}</span></div></td><td class="r num faint">${fmtNum(m.votes || 0)}</td><td>${lbGroup(m.model) ? `<span class="chip info plain">${esc(lbGroup(m.model).name)}</span>` : '<span class="faint">—</span>'}</td><td>${m.local_sessions ? `<button type="button" class="link" data-action="arena-lb-sessions" data-model="${attr(m.model)}">${m.local_sessions} 条${m.local_verified ? `（${m.local_verified} 已验证）` : ''}</button>` : '<span class="faint">无</span>'}</td><td>${m.success_rate != null ? `<span class="chip ${m.success_rate >= 0.9 ? 'ok' : m.success_rate >= 0.6 ? 'warn' : 'bad'} plain">${Math.round(m.success_rate * 100)}% · ${m.avg_ms} ms</span>` : '<span class="faint">—</span>'}</td><td class="r">${lbGroup(m.model) ? `<button type="button" class="small" data-action="arena-group-key" data-id="${attr(lbGroup(m.model).id)}" data-name="${attr(lbGroup(m.model).name)}">${icon('bolt')}接入信息</button>` : `<button type="button" class="small" data-action="arena-lb-group" data-model="${attr(m.model)}">${icon('plus')}建组</button>`}</td></tr>`).join('')}</tbody></table></div>` : empty('还没有榜单数据', board.meta?.error || '点击右上角刷新快照', `<button type="button" class="primary" data-action="arena-refresh-lb" style="margin-top:12px">${icon('refresh')}刷新快照</button>`)}
  </div>`;
}
const lbGroup = model => { const k = norm(model); return (A.groups?.groups || []).find(g => (g.match || []).some(m => k.startsWith(norm(m)))); };
const norm = s => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
function sessionsForModel(model) { const k = norm(model).slice(0, 12); return (A.sessions?.sessions || []).filter(s => norm(s.detected_model).slice(0, 12) === k || norm(s.detected_model).startsWith(k) || k.startsWith(norm(s.detected_model)) && s.detected_model && s.detected_model !== 'Unknown'); }

// ---------- 动作 ----------
export const ARENA_ACTIONS = {
  'arena-reload': () => loadArena(currentView()),
  'arena-sync': () => post('sync', {}, '已开始同步').then(() => loadArena(currentView())),
  'arena-health': (t, rerender) => { A.filters.health = t.dataset.health; rerender(); },
  'arena-account': t => openAccount(t.dataset.email),
  'arena-import': () => openImport(),
  'arena-register': () => openRegister(),
  'arena-check-all': async () => { if (!(await confirmDialog({ title: '检测全部账号', body: '对每个账号做一次状态请求，24 小时内已检测过的会跳过。不会新开会话。', confirmText: '开始检测' }))) return; await post('accounts/check', {}, '检测已开始'); loadArena(currentView()); },
  'arena-check-one': t => post('accounts/check', { emails: [t.dataset.email] }, `已开始检测 ${t.dataset.email}`).then(() => loadArena(currentView())),
  'arena-reset-cooldown': t => post('accounts/reset_cooldown', { email: t.dataset.email }, '已解除冷却').then(() => { closeDrawer(); loadArena(currentView()); }),
  'arena-switch': t => post('accounts/switch', { email: t.dataset.email }, '已切换主账号').then(() => { closeDrawer(); loadArena(currentView()); }),
  'arena-delete-account': async t => { if (!(await confirmDialog({ title: `移出 ${t.dataset.email}`, body: '从登录器账号池删除，该账号下的会话不再可用于中继。', confirmText: '移出', danger: true }))) return; await post('accounts/delete', { email: t.dataset.email }, '已移出'); closeDrawer(); loadArena(currentView()); },
  'arena-create-sessions': t => openCreateSessions(t.dataset.email || ''),
  'arena-family': (t, rerender) => { A.filters.family = t.dataset.family; rerender(); },
  'arena-only-probe': (t, rerender) => { A.filters.onlyProbe = t.checked; rerender(); },
  'arena-sort': (t, rerender) => { const k = t.dataset.key; A.sort = { key: k, dir: A.sort.key === k && A.sort.dir === 'desc' ? 'asc' : 'desc' }; rerender(); },
  'arena-select': (t, rerender) => { t.checked ? A.selected.add(t.dataset.id) : A.selected.delete(t.dataset.id); rerender(); },
  'arena-select-all': (t, rerender) => { const ids = $$('[data-action="arena-select"]').map(x => x.dataset.id); ids.forEach(id => t.checked ? A.selected.add(id) : A.selected.delete(id)); rerender(); },
  'arena-select-probes': (t, rerender) => { (A.sessions?.sessions || []).filter(s => s.is_probe).forEach(s => A.selected.add(s.id)); A.filters.onlyProbe = true; rerender(); },
  'arena-clear-selection': (t, rerender) => { A.selected.clear(); rerender(); },
  'arena-session': t => openSession(t.dataset.id),
  'arena-detect-one': t => post('sessions/detect', { session_ids: [t.dataset.id] }, '识别已开始').then(() => loadArena(currentView())),
  'arena-detect-selected': () => post('sessions/detect', { session_ids: [...A.selected] }, `已开始识别 ${A.selected.size} 条`).then(() => loadArena(currentView())),
  'arena-delete-one': async t => { if (!(await confirmDialog({ title: '删除会话', body: '在 arena.ai 删除该会话，不可恢复。', confirmText: '删除', danger: true }))) return; await post('sessions/delete', { session_ids: [t.dataset.id] }, '删除已开始'); closeDrawer(); loadArena(currentView()); },
  'arena-delete-selected': async () => { const n = A.selected.size; if (!(await confirmDialog({ title: `删除 ${n} 条会话`, body: '在 arena.ai 删除这些会话，不可恢复。路由中的会话删除后对应路由会失效。', confirmText: `删除 ${n} 条`, danger: true }))) return; await post('sessions/delete', { session_ids: [...A.selected] }, '删除已开始'); A.selected.clear(); loadArena(currentView()); },
  'arena-route-selected': () => { const id = [...A.selected][0]; if (A.selected.size !== 1) return toast('设为路由一次只能选一条会话', 'warn'); openSession(id); setTimeout(() => $('#routeModel')?.focus(), 50); },
  'arena-pin': t => post('sessions/flag', { session_id: t.dataset.id, pinned: t.dataset.pinned === '1' }).then(() => { closeDrawer(); loadArena(currentView()); }),
  'arena-new-key': () => openNewKey(),
  'arena-group-open': (t, _r, e) => { const b = e?.target?.closest?.('button'); if (b && b !== t && b.dataset.action !== 'arena-group-open') return; openGroupDetail(t.dataset.id, 'members'); },
  'arena-group-tab': t => openGroupDetail(t.dataset.id, t.dataset.tab),
  'arena-group-verify': async t => { const r = await post('groups/verify', { id: t.dataset.id }); const kicked = (r && r.kicked) || []; toast(`检查完成：保留 ${(r && r.kept) ?? 0}，移出 ${kicked.length}，无识别结果 ${(r && r.unknown) ?? 0}（未识别的不动）`, kicked.length ? 'warn' : 'ok'); await loadArena(currentView()); if ($('.drawer')) openGroupDetail(t.dataset.id, 'members'); },
  'arena-group-purge': async (t, _r, e) => { e?.stopPropagation(); if (!(await confirmDialog({ title: `删除 ${t.dataset.name} 的全部会话`, body: `将从 Arena 删除 ${t.dataset.count} 条会话，分组本身与 Key 保留。此操作不可撤销。`, confirmText: `删除 ${t.dataset.count} 条`, danger: true }))) return; await post('groups/purge', { id: t.dataset.id }, '删除已开始'); closeDrawer(); loadArena(currentView()); },
  'arena-purge-ungrouped': async () => { const n = Object.values(A.groups?.unmatched || {}).reduce((a, b) => a + b, 0); if (!(await confirmDialog({ title: '清理非常用会话', body: `删除所有不属于任何模型组的会话（约 ${n} 条，含探针会话与标题无法归一的会话）。模型组里的会话不受影响。`, confirmText: '删除', danger: true }))) return; await post('sessions/purge_ungrouped', {}, '清理已开始'); loadArena(currentView()); },
  'arena-purge-account': async t => { if (!(await confirmDialog({ title: `删除 ${t.dataset.email} 的全部会话`, body: `该账号 ${t.dataset.count} 条会话全部从 Arena 删除，账号本身保留。`, confirmText: '删除', danger: true }))) return; await post('sessions/purge_account', { email: t.dataset.email }, '删除已开始'); loadArena(currentView()); },
  'arena-group-reveal': async t => { const el = $('#groupKeyText'); if (!el) return; if (el.dataset.shown) { el.textContent = el.dataset.masked; delete el.dataset.shown; return; } try { el.textContent = await fetchGroupKey(t.dataset.id); el.dataset.shown = '1'; } catch (e) { toast(e.message, 'warn'); } },
  'arena-group-copykey': async t => { try { await navigator.clipboard.writeText(await fetchGroupKey(t.dataset.id)); toast('Key 已复制', 'ok'); } catch (e) { toast(e.message, 'warn'); } },
  'arena-group-copycfg': async t => { const base = A.groups?.base_url || 'http://127.0.0.1:9091'; let key = '<该组 Key>'; try { key = await fetchGroupKey(t.dataset.id); } catch (e) { toast(e.message, 'warn'); } const txt = t.dataset.fmt === 'openai' ? `OPENAI_BASE_URL=${base}/v1\nOPENAI_API_KEY=${key}\nmodel=${t.dataset.id}` : `set ANTHROPIC_BASE_URL=${base}&& set ANTHROPIC_API_KEY=${key}&& set ANTHROPIC_MODEL=${t.dataset.id}&& claude`; await navigator.clipboard.writeText(txt); toast('已复制', 'ok'); },
  'arena-group-new': t => openGroupEditor('', t.dataset.match || ''),
  'arena-group-edit': t => openGroupEditor(t.dataset.id),
  'arena-group-key': t => openGroupKey(t.dataset.id, t.dataset.name),
  'arena-group-rotate': async t => { const has = (A.groups?.groups || []).find(x => x.id === t.dataset.id)?.key_prefix; if (has && !(await confirmDialog({ title: '轮换该组 Key', body: '旧 Key 立即失效，使用它的客户端会收到 401。', confirmText: '轮换', danger: true }))) return; const r = await post('groups/rotate_key', { id: t.dataset.id }); await loadArena(currentView()); openGroupDetail(t.dataset.id, 'access'); $('#groupKeyResult').innerHTML = `<div class="banner ok fade-up" style="margin-top:8px"><b>新 Key：</b><div class="row" style="margin-top:6px"><code class="mono" style="font-size:calc(12px * var(--fs-scale, 1));overflow-wrap:anywhere">${esc(r.key)}</code><button type="button" class="small" data-action="copy-text" data-text="${attr(r.key)}">${icon('copy')}复制</button></div></div>`; },
  'arena-group-delete': async t => { if (!(await confirmDialog({ title: `删除分组 ${t.dataset.name}`, body: '该组 Key 一并吊销；会话本身不受影响。', confirmText: '删除', danger: true }))) return; await post('groups/delete', { id: t.dataset.id }, '已删除'); loadArena(currentView()); },
  'arena-group-member': t => post('groups/member', { id: t.dataset.id, session_id: t.dataset.session, add: t.dataset.add === '1' }, t.dataset.add === '1' ? '已加入分组' : '已移出').then(() => { closeDrawer(); loadArena(currentView()); }),
  'arena-group-join': async t => { if (!t.dataset.session) return toast('该组暂无可用且未接入的会话', 'warn'); if (!(await confirmDialog({ title: '把这条会话接入 Workbench', body: '会经中继把接入提示词发进该会话（消耗一次对话），模型连接自建 MCP 后成为 Agent，名称自动编号。', confirmText: '接入' }))) return; const r = await post('groups/join', { id: t.dataset.id, session_id: t.dataset.session, request_id: newRequestId() }); toast(r.dedup ? '该会话已有在途接入作业，沿用同一作业' : '已排队，进度见顶部（以服务端状态为准）', r.dedup ? 'info' : 'ok'); closeDrawer(); loadArena(currentView()); },
  'arena-join-cancel': async t => { t.disabled = true; const r = await post('join/cancel', { id: t.dataset.id }).catch(() => null); if (r && r.cancelled) toast('已取消，未发送', 'ok'); else if (r && r.note) toast(r.note, 'warn', { ms: 7000 }); loadArena(currentView()); },
  'arena-group-common': async t => { await post('groups/common', { id: t.dataset.id, common: t.dataset.common === '1' }, t.dataset.common === '1' ? '已标记为常用组' : '已取消常用标记'); loadArena(currentView()); },
  'arena-common-join': () => openCommonJoin(),
  'arena-lb-group': t => openGroupEditor('', t.dataset.model),
  'arena-revoke-key': async t => { if (!(await confirmDialog({ title: `吊销「${t.dataset.name}」`, body: '使用这把 Key 的客户端会立即收到 401。', confirmText: '吊销', danger: true }))) return; await post('keys/revoke', { id: t.dataset.id }, '已吊销'); loadArena(currentView()); },
  'arena-add-route': () => openAddRoute(),
  'arena-delete-route': async t => { if (!(await confirmDialog({ title: `删除路由 ${t.dataset.model}`, body: '客户端再用这个模型名请求会回落到默认会话。', confirmText: '删除', danger: true }))) return; await post('routes/delete', { model: t.dataset.model }, '已删除'); loadArena(currentView()); },
  'arena-open-stats': async () => { A.statsFull = await api('/api/arena/stats'); A.rerender?.(); },
  'arena-board': (t, rerender) => { A.filters.board = t.dataset.board; rerender(); },
  'arena-refresh-lb': () => post('leaderboard/refresh', {}, '正在拉取榜单').then(() => loadArena(currentView())),
  'arena-lb-sessions': (t, rerender) => { const list = sessionsForModel(t.dataset.model); A.filters.family = list[0]?.detected_model || ''; A.filters.onlyProbe = false; ARENA_ACTIONS._go('arena-sessions'); },
  'arena-lb-route': t => { const list = sessionsForModel(t.dataset.model).sort((a, b) => Number(b.verified) - Number(a.verified)); if (!list.length) return toast('本地没有该模型的会话', 'warn'); openSession(list[0].id); setTimeout(() => { const i = $('#routeModel'); if (i) { i.value = t.dataset.model; i.focus(); } }, 50); },
  _go: null,
};
export const ARENA_FORMS = {
  'arena-common-confirm': async form => {
    const pv = A.commonPreview; if (!pv) return toast('预览已失效，请重新预览', 'warn');
    const selections = {};
    $$('input[name="sel"]:checked', form).forEach(i => { const [g, s] = i.value.split('|'); (selections[g] = selections[g] || []).push(s); });
    if (!Object.keys(selections).length) return toast('没有选择任何会话', 'warn');
    const btn = $('button[type="submit"]', form); if (btn) btn.disabled = true;  // 防连点只是辅助：同一 request_id 重放在服务端幂等
    try {
      const r = await post('join/confirm', { preview_token: pv.preview_token, request_id: pv.request_id, selections });
      const rows = r.items.map(it => `<div class="row between member"><span class="min0"><span class="mono">${esc(it.group)}</span> · ${esc(pv.titles[`${it.group}|${it.session_id}`] || it.session_id)}</span><span style="flex:none">${it.job ? `<span class="chip ${JOIN_STATE[it.status] ? JOIN_STATE[it.status][1] : 'info'}">${esc(JOIN_STATE[it.status] ? JOIN_STATE[it.status][0] : it.status)}${it.dedup ? '（沿用在途作业）' : ''}</span> <span class="mono faint">${esc(it.job)}</span>` : it.skip ? `<span class="chip plain">跳过：${esc(SKIP[it.skip] || it.skip)}</span>` : `<span class="chip bad">${esc(it.error || '失败')}</span>`}</span></div>`).join('');
      ($('#commonJoinResult', form) || {}).innerHTML = `<div class="banner ${r.aborted ? 'warn' : 'ok'} stack" style="gap:6px"><b>已入队 ${r.queued} 项${r.aborted ? ` · ${esc(r.abort_reason)}` : ''}</b>${rows}<div class="faint" style="font-size:calc(11px * var(--fs-scale, 1))">进度见页面顶部作业条；刷新或换标签页仍读同一作业。unconfirmed 表示消息已发但没等到有效心跳，不算成功。</div></div>`;
      toast(`已入队 ${r.queued} 项接入作业`, r.queued ? 'ok' : 'warn');
      loadArena(currentView());
    } catch (e) { if (btn) btn.disabled = false; }
  },
  'arena-group': async form => { const fd = new FormData(form); const r = await post('groups/save', { id: form.dataset.id || fd.get('id'), name: fd.get('name'), match: String(fd.get('match') || '').split(/[,，]/).map(x => x.trim()).filter(Boolean), tier: fd.get('tier'), order: fd.get('order') ? Number(fd.get('order')) : undefined, rule: fd.get('rule') }, '分组已保存'); if (r.key) { $('#groupKeyResult').innerHTML = `<div class="banner ok fade-up" style="margin-top:12px"><b>该组 Key（以后可在「接入信息」里再查看）：</b><div class="row" style="margin-top:6px"><code class="mono" style="font-size:calc(12px * var(--fs-scale, 1));overflow-wrap:anywhere">${esc(r.key)}</code><button type="button" class="small" data-action="copy-text" data-text="${attr(r.key)}">${icon('copy')}复制</button></div></div>`; } else closeDrawer(); loadArena(currentView()); },
  'arena-account-note': form => { const fd = new FormData(form); return post('accounts/note', { email: form.dataset.email, note: fd.get('note'), tags: String(fd.get('tags') || '').split(/[,，]/).map(s => s.trim()).filter(Boolean) }, '已保存').then(() => loadArena(currentView())); },
  'arena-import': async form => { const raw = String(new FormData(form).get('raw_text') || '').trim(); if (!raw) return toast('先粘贴账号', 'warn'); await post('accounts/import', { raw_text: raw }, '接入已开始，进度见页面顶部'); closeDrawer(); loadArena(currentView()); },
  'arena-register': async form => { const fd = new FormData(form); await post('accounts/register', { count: Number(fd.get('count') || 1), password: fd.get('password') || '' }, '注册已开始'); closeDrawer(); loadArena(currentView()); },
  'arena-create-sessions': async form => { const fd = new FormData(form); const emails = fd.getAll('emails'); if (!emails.length) return toast('至少选一个账号', 'warn'); await post('sessions/create', { emails, count: Number(fd.get('count') || 1), auto_detect: !!fd.get('auto_detect') }, '已开始开会话'); closeDrawer(); loadArena(currentView()); },
  'arena-route-set': async form => { const model = String(new FormData(form).get('model') || '').trim(); if (!model) return toast('填写模型名', 'warn'); await post('routes/set', { model, session_id: form.dataset.session }, `已将 ${model} 指向该会话`); closeDrawer(); loadArena(currentView()); },
  'arena-route-add': async form => { const fd = new FormData(form); await post('routes/set', { model: String(fd.get('model') || '').trim(), session_id: fd.get('session_id') }, '路由已保存'); closeDrawer(); loadArena(currentView()); },
  'arena-new-key': async form => { const fd = new FormData(form); const r = await post('keys/new', { name: fd.get('name'), allowed_models: fd.getAll('allowed_models') }); $('#keyResult').innerHTML = `<div class="banner warn fade-up" style="margin-top:12px"><b>只显示这一次，请立即复制。</b><div class="row" style="margin-top:8px"><code class="mono" style="font-size:calc(12px * var(--fs-scale, 1));overflow-wrap:anywhere">${esc(r.key)}</code><button type="button" class="small" data-action="copy-text" data-text="${attr(r.key)}">${icon('copy')}复制</button></div></div>`; loadArena(currentView()); },
};
const currentView = () => new URLSearchParams(location.search).get('view') || 'arena-groups';
