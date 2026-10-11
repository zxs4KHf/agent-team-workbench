// 任务历史视图（3847 按 2c0a 返工重落地）：只读分页，数据来自 /api/inbox/history。
// 与 hub#inbox（bus_intake 外部来源收件箱）无关：视图名 history、动作前缀 hist-、本地存储键 bus.hist*，状态不共用。
// 已读只记在本机浏览器，不是命令 ACK，也不等于采纳。
// canonical id 一律用服务端下发的 canonical_id，前端不按 workspace 目录名重算（返工点 5/11：换工作区路径不该把全部历史重新标成未读）。
import { esc, attr, icon, api, url, relTime, fmtDate } from './core.js';
import { S, F, terminalStatuses } from './model.js';
import { head, empty, pagerNav } from './views.js';

export const HISTORY_NAV = [['history', 'clock', '任务历史']];
export const PAGE_SIZE = 20;
export const KIND_LABEL = { all: '全部', commands: '已完成指令', decisions: '终态决策' };
export const BUCKET_LABEL = { done: '已完成', failed: '执行失败', closed: '已关闭', other: '其他' };
export const HIST_URL = '/api/inbox/history';   // 兼容旧设计的接口名；这是任务历史，不是外部来源收件箱
const READ_KEY = 'bus.histread';

// app.js 启动时注入，避免 core ↔ 视图互相 import 成环
export const HOOKS = { rerender: null, openDecision: null, openCommand: null, setProject: null };

// 分桶只决定标题归类；「是不是终态」跟着服务端下发的 terminal_statuses 走（返工点 2：口径只有一份）
export const CLOSED_STATUSES = ['cancelled', 'rejected', 'superseded'];
export function histBucket(rec) {
  const st = String((rec || {}).status || '');
  if ((rec || {}).kind === 'command') return st === 'failed' ? 'failed' : 'done';
  if (CLOSED_STATUSES.includes(st)) return 'closed';
  return terminalStatuses().includes(st) ? 'done' : 'other';
}

// 语义修订号：状态 / 标题 / 结果变了才重新算，服务端给了 revision 就用它（决策每改一次 +1）
export function hashRev(parts) {
  const s = parts.map(p => String(p == null ? '' : p)).join('|');
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return 'h' + (h >>> 0).toString(36);
}
export const revisionOf = rec => (rec && rec.revision ? 'r' + rec.revision : hashRev([rec && rec.status, rec && rec.title, rec && rec.result]));

// ---------- 已读状态（只在本机 localStorage）----------
export function readStore() {
  let m = {}, degraded = false;
  try { const raw = localStorage.getItem(READ_KEY); m = raw ? JSON.parse(raw) : {}; if (!m || typeof m !== 'object' || Array.isArray(m)) m = {}; }
  catch (_) { m = {}; degraded = true; }
  const save = () => { try { localStorage.setItem(READ_KEY, JSON.stringify(m)); degraded = false; return true; } catch (_) { degraded = true; return false; } };
  return {
    map: m,
    isUnread: it => m[it.id] === undefined || m[it.id] !== it.revision,
    markRead: (id, rev) => { if (m[id] === rev) return true; m[id] = rev; return save(); },
    markPage: list => { list.forEach(it => { m[it.id] = it.revision; }); return save(); },
    clear: () => { m = {}; return save(); },
    status: () => ({ degraded, stored: Object.keys(m).length }),
  };
}

// ---------- 视图状态（易失，不进 store，避免与 state.history 趋势快照撞键）----------
export function histState() {
  if (!F.hist) F.hist = {
    filters: { kind: 'all', unread: false, scope: 'current' },
    pages: new Map(),        // 每个 (项目范围|类型) 独立：trail 游标链 + 当前页码 + 各页响应
    frozen: null, applyNew: false,
    read: readStore(), lastGood: null, stale: false, staleError: '', expired: '',
  };
  return F.hist;
}
export const dataKey = (st = histState(), s = S()) => `${st.filters.scope === 'all' ? '*' : (s.project || '')}|${st.filters.kind}`;
export function pageOf(st, key) {
  if (!st.pages.has(key)) st.pages.set(key, { trail: [''], idx: 0, resp: {}, loading: false, error: '' });
  return st.pages.get(key);
}
export const filterKey = (st = histState(), s = S()) => `${dataKey(st, s)}|${(pageOf(st, dataKey(st, s)) || {}).idx || 0}`;  // 未读是本地视图过滤，不参与冻结键：勾掉未读不该把差异提示一起清掉

// 服务端记录 → 列表项。id 用服务端 canonical_id；bucket 一并算好，冻结页删除项要靠它保留快照。
export function normalize(rec) {
  const kind = rec.kind === 'command' ? 'command' : 'decision';
  const title = kind === 'command'
    ? `${rec.cmd_kind || '指令'} · ${rec.task_id ? '关联任务 ' + rec.task_id : rec.id}`
    : String(rec.title || rec.id || '');
  const it = { id: String(rec.canonical_id || ''), kind, id_native: String(rec.id || ''), project: rec.project || '',
    title, time: String(rec.time || ''), status: rec.status || '', bucket: histBucket(rec), revision: revisionOf(rec),
    cmd_kind: rec.cmd_kind || '', task_id: rec.task_id || '', merge_path: rec.merge_path || '', result: rec.result || '',
    failure: kind === 'command' && rec.status === 'failed' ? String(rec.result || '执行失败') : '' };
  return it;
}

// 冻结页 vs 最新快照：显示的还是用户正在读的那一页（不自动漂），差异只做提示。
// 必须有 itemsById；被删掉的项仍以冻结快照里的 title/time/bucket 留在列表里，只多一个 removed 标记。
export function freeze(items, key, page) { return { key, page, items, at: Date.now() }; }
export function reconcile(frozen, fresh, key, page) {
  const itemsById = new Map(fresh.map(it => [it.id, it]));      // 最新快照按 id 索引
  const frozenIds = new Set(frozen.items.map(it => it.id));
  const newCount = fresh.filter(it => !frozenIds.has(it.id)).length;
  const removed = frozen.items.filter(it => !itemsById.has(it.id)).map(it => ({ ...it, removed: true }));
  const items = frozen.items.map(it => {
    const now = itemsById.get(it.id);
    return { ...it, removed: !now, changed: now && now.revision !== it.revision ? 1 : 0 };
  });
  return { items, newCount, removed, key, page, stale: true };
}

// ---------- 取数 ----------
export function histQuery(key, st, s, cursor) {
  const [scope] = key.split('|');
  const params = { filter: st.filters.kind, limit: PAGE_SIZE };
  if (scope !== '*') params.project = s.project || '';
  if (cursor) params.cursor = cursor;
  return params;
}
export function buildUrl(params) { return HIST_URL + '?' + Object.entries(params).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&'); }

// 游标是凭据：只放在这条请求里，不进日志、不进错误文案。
export async function loadHistory(force = false) {
  const s = S(), st = histState(), key = dataKey(st, s), pg = pageOf(st, key);
  if (pg.loading) return pg._p || Promise.resolve();
  if (pg.resp[pg.idx] && !force) { (HOOKS.rerender || (() => {}))(); return; }
  pg.loading = true;
  pg._p = (async () => {
  const cursor = pg.trail[pg.idx] || '';
  try {
    const r = await api(buildUrl(histQuery(key, st, s, cursor)));
    pg.resp[pg.idx] = r; pg.error = '';
    if (r.next_cursor && !pg.trail[pg.idx + 1]) pg.trail[pg.idx + 1] = r.next_cursor;
    st.stale = false; st.staleError = '';
    st.lastGood = { at: Date.now(), key, idx: pg.idx, total: r.total };
  } catch (e) {
    const code = String(e && e.message || '');
    if (e && e.message === 'cursor_expired') {
      // 快照过期 / 游标越界：从头沿 next_cursor 重走到原来那一页，不停在空页上；页数变少就夹到最后一页
      const target = pg.idx;
      pg.trail = ['']; pg.resp = {}; pg.idx = 0;
      let idx = 0;
      for (;;) {
        const r = await api(buildUrl(histQuery(key, st, s, pg.trail[idx] || '')));
        pg.resp[idx] = r;
        if (r.next_cursor && !pg.trail[idx + 1]) pg.trail[idx + 1] = r.next_cursor;
        if (idx >= target || !r.next_cursor) { pg.idx = idx; break; }
        idx++;
      }
      pg.error = ''; st.stale = false; st.staleError = ''; st.expired = pg.idx !== target ? `快照已过期，已重取到最后一页（第 ${pg.idx + 1} 页）` : `快照已过期，已按最新快照重取到第 ${pg.idx + 1} 页`;
    } else {
      // 失联 / 服务报错：保留上次成功快照，页码不动
      pg.error = code; st.stale = true; st.staleError = code;
    }
  }
  pg.loading = false; pg._p = null;
  (HOOKS.rerender || (() => {}))();
  })();
  return pg._p;
}

// ---------- 汇总视图数据 ----------
export function computeView(s = S()) {
  const st = histState(), key = dataKey(st, s), pg = pageOf(st, key);
  const resp = pg.resp[pg.idx];
  const emptyView = { st, pg, resp: null, items: [], total: null, view: { items: [], newCount: 0, removed: [] }, key, loaded: 0, complete: false, loading: pg.loading, error: pg.error, unreadN: 0 };
  if (!resp) return emptyView;
  const all = (resp.items || []).map(normalize);           // 冻结的是整页原始结果
  const keyPage = filterKey(st, s);
  let view = { items: all, newCount: 0, removed: [], stale: false };
  if (st.applyNew || !st.frozen || st.frozen.key !== keyPage) { st.frozen = freeze(all, keyPage, pg.idx); st.applyNew = false; }
  else if (st.frozen.page === pg.idx) view = reconcile(st.frozen, all, keyPage, pg.idx);
  const items = st.filters.unread ? view.items.filter(it => st.read.isUnread(it)) : view.items;
  return { st, pg, resp, items, view: { ...view, items }, key, loaded: all.length, complete: !!resp.complete, loading: pg.loading, error: pg.error, unreadN: all.filter(it => st.read.isUnread(it)).length };
}

// ---------- 渲染 ----------
const chip = (txt, cls = '') => `<span class="chip ${cls}">${esc(txt)}</span>`;
export function histItemHtml(it, st) {
  const unread = st.read.isUnread(it);
  return `<li class="card pad hist-item ${unread && !it.removed ? 'unread' : ''} ${it.removed ? 'removed' : ''}" tabindex="0" data-hist-id="${attr(it.id)}" style="display:flex;gap:12px;align-items:flex-start;margin-bottom:8px;${it.removed ? 'opacity:.6' : ''}">
  <span aria-label="${unread ? '未读' : '已读'}" title="${unread ? '未读' : '已读'}" style="width:8px;height:8px;border-radius:50%;margin-top:8px;flex:none;background:${unread ? 'var(--accent, #3b82f6)' : 'transparent'};border:1px solid var(--border, #ccc)"></span>
  <div style="flex:1;min-width:0">
    <div class="row wrap" style="gap:6px;align-items:center">${chip(it.project || '（全局）')}${chip(BUCKET_LABEL[it.bucket] || it.bucket, it.bucket === 'failed' ? 'bad' : '')}${chip(KIND_LABEL[it.kind === 'command' ? 'commands' : 'decisions'])}${it.removed ? chip('已从最新结果移除', 'warn') : ''}</div>
    <div style="font-weight:600;margin:4px 0">${esc(it.title)}</div>
    <div class="faint" style="font-size:calc(13px * var(--fs-scale, 1))">${esc(fmtDate(it.time))} · ${esc(relTime(it.time))}${it.status ? ` · ${esc(it.status)}` : ''}${it.failure ? ` · <span class="bad">${esc(it.failure)}</span>` : ''}${it.result && it.kind === 'command' && !it.failure ? ` · <span class="mono">${esc(it.result.slice(0, 80))}</span>` : ''}</div>
  </div>
  <div class="row" style="gap:6px;flex:none;align-items:center">${it.removed ? '' : `<button type="button" class="small ghost" data-action="hist-open" data-id="${attr(it.id_native)}" data-item="${attr(it.id)}" data-project="${attr(it.project)}" data-rev="${attr(it.revision)}" data-kind="${attr(it.kind)}">${icon('eye')}详情</button>`}</div>
</li>`;
}

export function renderHistory() {
  const s = S();
  const { st, pg, resp, view, loaded, complete, loading, error, unreadN } = computeView(s);
  // 2026-10-02：首屏请求时当前项目还没定（project=''），数据回来后口径变了、这个口径下没有数据也没人再取，列表一直是空的。
  // 当前口径没数据、没在取、没报错 → 补取一次（loadHistory 自带「正在取就不重复」）
  if (!resp && !loading && !error) setTimeout(() => loadHistory(), 0);
  const f = st.filters;
  const rs = st.read.status();
  const pages = resp ? Math.max(1, Math.ceil((resp.total || 0) / (resp.limit || PAGE_SIZE))) : 1;
  const last = !resp || resp.complete;
  // 翻页取数期间 resp 为空：页码格沿用上次已知的页数，不缩成 1 格（缩了按钮就挪位，2026-10-11「这个选项会跑」）
  if (resp) pg.knownPages = pages;
  const shownPages = Math.max(resp ? pages : (pg.knownPages || 1), pg.idx + 1);
  const filters = `<select data-action="hist-kind" aria-label="类型筛选">${Object.entries(KIND_LABEL).map(([k, v]) => `<option value="${k}" ${f.kind === k ? 'selected' : ''}>${v}</option>`).join('')}</select>
    <button type="button" class="small ${f.unread ? 'primary' : 'ghost'}" data-action="hist-unread" aria-pressed="${f.unread}">只看未读 (${unreadN})</button>
    <button type="button" class="small ghost" data-action="hist-scope" aria-pressed="${f.scope === 'all'}">${f.scope === 'all' ? '全部项目' : '当前项目'}</button>
    <button type="button" class="small ghost" data-action="hist-mark-page-read" ${view.items.length ? '' : 'disabled aria-disabled="true"'}>本页标为已读</button>`;
  const rangeBar = !resp ? '' : `<div class="faint" style="font-size:calc(12px * var(--fs-scale, 1));margin:4px 0" data-hist-range>
    ${complete ? `已到底：本口径共 ${resp.total} 条，已全部载入` : `已加载第 ${(resp.range && resp.range.start) + 1}–${resp.range && resp.range.end} 条 / 共 ${resp.total} 条，还不是完整历史`}
    · 快照 ${resp.snapshot ? esc(String(resp.snapshot.id)) : ''}（冻结 10 分钟内翻页不漂）${resp.snapshot && resp.snapshot.skipped ? ` · 跳过坏记录 ${resp.snapshot.skipped} 条` : ''}</div>`;
  return `<div class="view" data-hist-view>
    ${head('任务历史', '已完成与已关闭的任务', '只读：console/done 的已完成指令 + 各项目的终态决策。已读只存在本机浏览器，不会改动任何任务状态。', filters)}
    ${!s.connected ? '<div class="banner danger" style="font-size:calc(13px * var(--fs-scale, 1))">工作台失联：下面是上次成功取到的快照，内容可能过期</div>' : ''}
    ${error ? `<div class="banner danger" style="font-size:calc(13px * var(--fs-scale, 1))">本口径读取失败：${esc(error)}（保留上次成功页，未清空列表）<button type="button" class="small" data-action="hist-reload">重试</button></div>` : ''}
    ${rs.degraded ? '<div class="banner warn" style="font-size:calc(13px * var(--fs-scale, 1))">本地存储写入失败（配额或隐私模式），已读状态只在本次会话有效，刷新后会恢复未读</div>' : ''}
    ${st.expired ? `<div class="banner warn" style="font-size:calc(13px * var(--fs-scale, 1))">${esc(st.expired)}</div>` : ''}
    ${view.stale && (view.newCount || view.removed.length) ? `<div class="banner" style="font-size:calc(13px * var(--fs-scale, 1))"><button type="button" class="small" data-action="hist-apply-new">${view.newCount ? `有 ${view.newCount} 条新内容` : ''}${view.newCount && view.removed.length ? '，' : ''}${view.removed.length ? `${view.removed.length} 条已移除` : ''} · 应用</button> <span class="faint">翻页与阅读中不自动换页</span></div>` : ''}
    <section class="card fit-panel" data-key="fit-history"><div class="fit-body">${rangeBar}
    <ol id="historyList" tabindex="-1" aria-label="任务历史列表" style="list-style:none;padding:0;margin:8px 0 0">${view.items.length ? view.items.map(it => histItemHtml(it, st)).join('') : `<li class="empty" style="padding:40px">${empty(loading ? '正在读取…' : f.unread ? '本页没有未读项' : '这一页没有记录', '换个筛选条件，或翻到下一页')}</li>`}</ol>
    ${pagerNav({ pg: pg.idx, pages: shownPages, atEnd: last, status: `第 ${pg.idx + 1} / ${pages} 页 · 共 ${resp ? resp.total : '—'} 条${loading ? ' · 加载中' : ''}`, act: 'hist-page', jumpAttr: ' data-hist-jump', extra: `<button type="button" class="small ghost" data-action="hist-reload">${icon('refresh')}重新取本页</button>` })}
    </div></section></div>`;
}

// ---------- 动作 ----------
function syncUrl(st, pg, push) {
  const q = { view: 'history', hkind: st.filters.kind === 'all' ? '' : st.filters.kind, hunread: st.filters.unread ? '1' : '', hscope: st.filters.scope === 'all' ? 'all' : '', hpage: pg.idx ? String(pg.idx) : '' };
  url.set(q, !push);   // 翻页 pushState（可前进/后退），改筛选只 replaceState，不淹没后退栈
}
export function readUrlFilters(st) {
  try {
    const q = new URLSearchParams((globalThis.location && location.search) || '');
    if (['all', 'commands', 'decisions'].includes(q.get('hkind'))) st.filters.kind = q.get('hkind');
    st.filters.unread = q.get('hunread') === '1';
    st.filters.scope = q.get('hscope') === 'all' ? 'all' : 'current';
    const p = Number(q.get('hpage') || 0);
    if (Number.isFinite(p) && p >= 0) pageOf(st, dataKey(st)).idx = Math.floor(p);
    return st;
  } catch (_) { return st; }
}
// 与 ARENA_ACTIONS 同签名：app.js 合并时注入 (t, rerender, e)
export const HISTORY_ACTIONS = {
  'hist-kind': (t, rerender) => { const st = histState(); st.filters.kind = ['all', 'commands', 'decisions'].includes(t.value) ? t.value : 'all'; st.frozen = null; const pg = pageOf(st, dataKey(st)); pg.idx = 0; syncUrl(st, pg, true); rerender(); loadHistory(); },
  'hist-unread': (t, rerender) => { const st = histState(); st.filters.unread = !st.filters.unread; st.frozen = null; syncUrl(st, pageOf(st, dataKey(st)), true); rerender(); },
  'hist-scope': (t, rerender) => { const st = histState(); st.filters.scope = st.filters.scope === 'all' ? 'current' : 'all'; st.frozen = null; const pg = pageOf(st, dataKey(st)); pg.idx = 0; syncUrl(st, pg, true); rerender(); loadHistory(); },
  // 页码按钮 / 跳页框带 data-to（目标页，0 起）；只有 data-dir 时相对翻。跳到后面的页由 loadHistory 沿游标链逐页取到（与 410 重取同一条路）
  'hist-page': (t, rerender) => { const st = histState(), pg = pageOf(st, dataKey(st)); const to = t.dataset.to; const next = to != null && to !== '' ? Number(to) : pg.idx + Number(t.dataset.dir || 1); if (!Number.isFinite(next) || next < 0 || next === pg.idx) return; pg.idx = next; st.frozen = null; syncUrl(st, pg, true); rerender(); loadHistory(); },
  'hist-reload': () => loadHistory(true),
  'hist-apply-new': (t, rerender) => { histState().applyNew = true; rerender(); },
  'hist-mark-page-read': (t, rerender) => { const st = histState(); st.read.markPage(computeView(S()).view.items.filter(it => !it.removed)); rerender(); },
  'hist-open': (t, rerender) => {
    const st = histState(); const key = t.dataset.item, kind = t.dataset.kind, id = t.dataset.id, proj = t.dataset.project;
    const rev = t.dataset.rev || '';   // 修订号由渲染时带在按钮上：冻结页换了键也不会标错
    st.read.markRead(key, rev);
    if (kind === 'command') { HOOKS.openCommand && HOOKS.openCommand(id); return; }
    // 跨项目：先切项目再开，否则 openDecision 只在当前项目里查，会得到「找不到这条决策」
    if (proj && proj !== (S().project || '')) HOOKS.setProject && HOOKS.setProject(proj);
    HOOKS.openDecision && HOOKS.openDecision(id);
    rerender();
  },
};

// ---------- 键盘：列表内上下键移动焦点，Enter / Space 打开详情 ----------
export function historyKeydown(e) {
  const li = e.target && e.target.closest ? e.target.closest('.hist-item') : null;
  if (!li || !li.parentElement) return false;
  const kids = [...li.parentElement.querySelectorAll(':scope > .hist-item')];
  const i = kids.indexOf(li);
  if (e.key === 'ArrowDown' && i >= 0 && i < kids.length - 1) { kids[i + 1].focus(); e.preventDefault(); return true; }
  if (e.key === 'ArrowUp' && i > 0) { kids[i - 1].focus(); e.preventDefault(); return true; }
  if ((e.key === 'Enter' || e.key === ' ') && document.activeElement === li) {
    const btn = li.querySelector('[data-action="hist-open"]'); if (btn) { btn.click(); e.preventDefault(); return true; }
  }
  return false;
}

export function startHistoryView(view) {
  if (view !== 'history') return;
  const st = histState(); readUrlFilters(st); loadHistory();
}
