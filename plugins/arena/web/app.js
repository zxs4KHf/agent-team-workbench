// Arena 插件页面外壳（ADR 0014）：五个分区标签 + 事件委托。渲染与动作都在 arena.js（原工作台 Arena 视图原样搬来）。
import { $, $$, icon, morph } from '/v2/js/core.js';
import { toast, closeDrawer, drawerOpen } from '/v2/js/components.js';
import { A, ARENA_NAV, ARENA_ACTIONS, ARENA_FORMS, startArenaPolling, renderArenaAccounts, renderArenaSessions, renderArenaRoutes, renderArenaLeaderboard, renderArenaGroups } from './arena.js';

const VIEWS = { 'arena-groups': renderArenaGroups, 'arena-leaderboard': renderArenaLeaderboard, 'arena-accounts': renderArenaAccounts, 'arena-sessions': renderArenaSessions, 'arena-routes': renderArenaRoutes };
const current = () => { const v = new URLSearchParams(location.search).get('view'); return VIEWS[v] ? v : 'arena-groups'; };

function render() {
  const v = current();
  morph($('#arenaTabs'), ARENA_NAV.map(([key, ic, label]) => `<button type="button" role="tab" data-action="arena-tab" data-view="${key}" aria-selected="${v === key}">${icon(ic)} ${label}</button>`).join(''));
  morph($('#arenaMain'), VIEWS[v]());
}
function go(v, push = true) {
  if (!VIEWS[v]) return;
  const u = new URL(location.href); u.searchParams.set('view', v);
  history[push ? 'pushState' : 'replaceState'](null, '', u);
  render(); startArenaPolling(v);
}

const SHELL = {
  'arena-tab': t => go(t.dataset.view),
  'close-drawer': () => closeDrawer(),
  'copy-text': t => navigator.clipboard.writeText(t.dataset.text).then(() => toast('已复制', 'ok')).catch(() => toast('复制失败，请手动复制', 'warn')),
};

document.addEventListener('click', e => {
  const t = e.target.closest('[data-action]'); if (!t) return;
  const k = t.dataset.action;
  if (SHELL[k]) { e.preventDefault(); return SHELL[k](t, e); }
  const fn = ARENA_ACTIONS[k];
  if (typeof fn === 'function' && !k.startsWith('_')) { if (t.tagName !== 'INPUT') e.preventDefault(); Promise.resolve(fn(t, render, e)).catch(err => toast(err.message || String(err), 'bad')); }
});
document.addEventListener('submit', e => {
  const form = e.target.closest('form[data-form]'); const fn = form && ARENA_FORMS[form.dataset.form]; if (!fn) return;
  e.preventDefault(); Promise.resolve(fn(form)).catch(err => toast(err.message || String(err), 'bad'));
});
document.addEventListener('input', e => { const t = e.target; if (t.dataset && t.dataset.arenaFilter) { A.filters[t.dataset.arenaFilter] = t.value; render(); } });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && drawerOpen()) closeDrawer(); });
$('#backdrop').addEventListener('click', () => closeDrawer());
window.addEventListener('popstate', () => { render(); startArenaPolling(current()); });

// 模型组卡片拖动排序（原来在工作台 app.js 的通用拖拽里，随插件搬过来）
const DRAG = { id: '' };
document.addEventListener('dragstart', e => { const c = e.target.closest?.('[data-group-card]'); if (!c) return; DRAG.id = c.dataset.groupCard; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', DRAG.id); requestAnimationFrame(() => c.classList.add('dragging')); });
document.addEventListener('dragover', e => {
  if (!DRAG.id) return; const over = e.target.closest?.('[data-group-card]'); if (!e.target.closest?.('#groupGrid')) return; e.preventDefault();
  const src = $(`[data-group-card="${CSS.escape(DRAG.id)}"]`); if (!over || !src || over === src) return;
  const r = over.getBoundingClientRect(); over.parentNode.insertBefore(src, e.clientX > r.left + r.width / 2 ? over.nextSibling : over);
});
document.addEventListener('dragend', () => {
  if (!DRAG.id) return; DRAG.id = '';
  $$('[data-group-card].dragging').forEach(x => x.classList.remove('dragging'));
  const ids = $$('[data-group-card]').map(x => x.dataset.groupCard);
  fetch('/api/arena/groups/reorder', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }) })
    .then(r => { if (!r.ok) throw new Error(`保存顺序失败（${r.status}）`); toast('顺序已保存', 'ok'); (A.groups?.groups || []).forEach(g => { g.order = ids.indexOf(g.id) + 1; }); })
    .catch(err => toast(err.message, 'bad'));
});

A.rerender = render; ARENA_ACTIONS._go = v => go(v);
go(current(), false);
