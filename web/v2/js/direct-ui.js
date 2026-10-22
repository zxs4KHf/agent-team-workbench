// direct-ui.js：直连 Agent 在工作台里的界面（2026-10-22 重做，ADR 0026 第 7 条修订）。
// 原来是独立页 direct.html：没有侧栏顶栏、页签套页签、长表单铺满，跟工作台割裂。现在跟其它 Agent 同一套：
// Agent 页一张「直连 Agent」分组卡、每人一个胶囊；点开走抽屉，分「工作 / 设置 / 接入 / 活动」四块；新建也是抽屉表单。
// 抽屉不随实时刷新整块重画：设置表单填到一半的内容留在 DOM 里；只有标了 data-direct-live 的几块在状态变化时局部 morph。
import { $, $$, esc, attr, icon, morph, api, relTime, fmtDate, minutesSince } from './core.js';
import { toast, openDrawer, closeDrawer, drawerOpen, confirmDialog } from './components.js';
import { store, S, F } from './model.js';
import { notify } from './notify.js';

const KIND = { write: '写文件', patch: '改文件', run: '运行命令' };
const AP_STATUS = { pending: ['等你批准', 'warn'], running: ['执行中', 'info'], done: ['已执行', 'ok'], failed: ['执行失败', 'bad'], denied: ['已拒绝', 'plain'], expired: ['已过期', 'plain'], cancelled: ['已作废', 'plain'], unknown: ['结果不明', 'bad'] };
const TOOL = { info: '读守则', list: '列目录', search: '搜索', read: '读', write: '写', patch: '改', run: '命令', task: '任务', config: '配置', user: '你' };
const TASK_ST = { open: ['待办', 'warn'], done: ['已完成', 'ok'], cancelled: ['已取消', 'plain'] };
const OUTCOME = { ok: 'ok', pending: 'warn', denied: 'bad', failed: 'bad' };
const PANES = [['work', '工作'], ['settings', '设置'], ['connect', '接入'], ['activity', '活动']];
const MODES_FALLBACK = [{ key: 'readonly', label: '只读', detail: '' }, { key: 'ask', label: '逐项审批', detail: '' }, { key: 'auto_edit', label: '自动改文件', detail: '' }, { key: 'bypass', label: '完全放开', detail: '' }];

// 详情（活动、任务、审批全文）不进整页状态，抽屉开着时按需拉 GET /api/direct
const D = { data: null, at: 0, links: {}, pane: 'work' };
export const DIRECT_HOOKS = { refresh: () => {} };   // main.js 注入整页刷新，避免这里反向 import 同步模块
async function loadDetail() { D.data = await api('/api/direct'); D.at = Date.now(); return D.data; }
const agentOf = id => (D.data?.agents || []).find(a => a.id === id);
const pendingOf = id => (D.data?.approvals || []).filter(x => x.status === 'pending' && (!id || x.agent === id));
const modes = () => D.data?.modes?.length ? D.data.modes : MODES_FALLBACK;
const lines = v => String(v || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) });

// ---------- Agent 页：分组卡 ----------
function pillTone(a) { if (!a.enabled) return 'off'; if (a.pending) return 'warn'; return a.last_seen && minutesSince(a.last_seen) < 30 ? 'ok' : ''; }
function pill(a) {
  const tone = pillTone(a);
  const tip = `${a.name} · ${a.mode_label || a.mode}${a.enabled ? '' : ' · 已停用'} · ${a.last_seen ? `最近活动 ${relTime(a.last_seen)}` : '还没连上过'} · ${a.folders} 个文件夹（${a.writable} 个可写）${a.pending ? ` · ${a.pending} 条等你批准` : ''}${a.open_tasks ? ` · ${a.open_tasks} 个待办任务` : ''}`;
  const badge = a.pending ? `<span class="pill-badge warn">批 ${a.pending}</span>` : a.open_tasks ? `<span class="pill-badge">${a.open_tasks}</span>` : '';
  return `<button type="button" class="agent-pill ${tone}" data-action="direct-open" data-id="${attr(a.id)}" data-key="d:${attr(a.id)}" data-tip="${attr(tip)}"><span class="status-dot ${tone}" aria-hidden="true"></span><span class="truncate">${esc(a.name)}</span>${badge}</button>`;
}
export function directCard(q = '') {
  const d = S().direct || {}, all = d.list || [];
  const list = q ? all.filter(a => `${a.name} ${a.model}`.toLowerCase().includes(q)) : all;
  if (q && !list.length) return '';
  const on = all.filter(a => a.enabled).length;
  return `<section class="card group-card direct-card${all.length ? '' : ' quiet'}" data-key="g:_direct">
    <header class="gc-head"><b class="gc-name">直连 Agent</b><span class="grow"></span><span class="faint gc-count" data-tip="启用的 / 全部">${on}/${all.length}</span></header>
    <div class="gc-tags"><span class="chip info plain" data-tip="不归 Leader 管、不用挂着待命：你在它自己的窗口里对话、派活，它按你给的文件夹和权限模式读写本机文件">单独对话</span>${d.pending ? `<span class="chip warn plain">${d.pending} 条等你批准</span>` : ''}</div>
    <div class="gc-pills">${list.length ? list.map(pill).join('') : '<span class="faint gc-empty">还没有。新建一个，给它文件夹和权限模式，就能在你的 AI 窗口里让它改本机文件</span>'}</div>
    <footer class="gc-foot">${d.pending ? `<button type="button" class="small ghost" data-action="direct-approvals">去批准</button>` : ''}<button type="button" class="small ghost" data-action="direct-new">${icon('plus')}新建</button></footer>
  </section>`;
}
export function directBanner() {
  const n = (S().direct || {}).pending || 0;
  return n ? `<div class="banner"><span class="grow">直连 Agent 有 ${n} 条操作等你批准（写文件或跑命令），批准前它会一直停在那一步。</span><button type="button" class="small" data-action="direct-approvals">去批准</button></div>` : '';
}

// ---------- 共用：文件夹编辑（直连 Agent 设置、新建、项目配置三处共用） ----------
function folderSuggestions(current) {
  const have = new Set(current.map(f => String(f.path || '').replace(/\//g, '\\').toLowerCase()));
  const seen = new Set(), out = [];
  for (const p of S().projects || []) {
    const name = p.name || p.slug;
    const add = (path, access, label) => { const k = String(path || '').replace(/\//g, '\\').toLowerCase(); if (!k || have.has(k) || seen.has(k)) return; seen.add(k); out.push({ path, access, label }); };
    if (p.repo_dir) add(p.repo_dir, 'write', `${name} · 施工仓库`);
    for (const f of p.folders || []) add(f.path, f.access, `${name} · ${f.label || (f.access === 'read' ? '只读' : '可写')}`);
  }
  return out;
}
function folderRow(f = {}) {
  return `<div class="direct-folder" data-folder-row>
    <input name="fpath" class="mono" value="${attr(f.path || '')}" title="${attr(f.path || '')}" placeholder="D:\\项目\\某个文件夹" aria-label="文件夹路径" autocomplete="off" spellcheck="false">
    <select name="faccess" aria-label="读写权限"><option value="write"${f.access !== 'read' ? ' selected' : ''}>可写</option><option value="read"${f.access === 'read' ? ' selected' : ''}>只读</option></select>
    <input name="flabel" value="${attr(f.label || '')}" placeholder="备注（可选）" aria-label="备注" autocomplete="off">
    <button type="button" class="ghost icon small" data-action="direct-folder-del" aria-label="移除这个文件夹" data-tip="移除">${icon('close')}</button></div>`;
}
export function folderEditor(folders, { suggest = true } = {}) {
  const sug = suggest ? folderSuggestions(folders) : [];
  return `<div class="stack direct-folders" data-folder-list>${folders.map(folderRow).join('')}</div>
    <div class="row wrap direct-folder-add"><button type="button" class="small" data-action="direct-folder-add">${icon('plus')}添加文件夹</button>${sug.map(s => `<button type="button" class="small ghost" data-action="direct-folder-pick" data-path="${attr(s.path)}" data-access="${attr(s.access)}" data-tip="${attr(s.path)}">${icon('plus')}${esc(s.label)}</button>`).join('')}</div>`;
}
export function readFolders(form) {
  return $$('[data-folder-row]', form).map(r => ({ path: $('[name=fpath]', r).value.trim(), access: $('[name=faccess]', r).value === 'read' ? 'read' : 'write', label: $('[name=flabel]', r).value.trim() })).filter(f => f.path);
}
function addFolderRow(t, f) {
  const list = t.closest('form')?.querySelector('[data-folder-list]'); if (!list) return;
  list.insertAdjacentHTML('beforeend', folderRow(f));
  markDirty(t.closest('form'));
  if (!f.path) list.lastElementChild?.querySelector('[name=fpath]')?.focus();
}

// ---------- 表单片段 ----------
function modePicks(cur) {
  return `<div class="pick-grid direct-modes" role="radiogroup" aria-label="权限模式">${modes().map(m => `<label class="pick${m.key === cur ? ' on' : ''}${m.key === 'bypass' ? ' risky' : ''}"><input type="radio" name="mode" value="${attr(m.key)}"${m.key === cur ? ' checked' : ''} data-direct-pick><span class="t"><b>${esc(m.label)}</b>${m.detail ? `<small>${esc(m.detail)}</small>` : ''}</span></label>`).join('')}</div>`;
}
function projectPicks(cur) {
  const projs = S().projects || [];
  if (!projs.length) return '<span class="faint">还没有项目</span>';
  return `<div class="pick-grid">${projs.map(p => { const on = cur.includes(p.slug); return `<label class="pick${on ? ' on' : ''}"><input type="checkbox" name="projects" value="${attr(p.slug)}"${on ? ' checked' : ''} data-direct-pick><span class="t"><b>${esc(p.name || p.slug)}</b></span></label>`; }).join('')}</div>`;
}
const MODE_HELP = '不管哪种模式：只能碰下面的文件夹，凭据文件一律拒读。命令在本机直接执行，不是沙箱，审批时能看到完整命令。推送、建 PR、发 Release 除「完全放开」外总要你批准。';
const FOLDER_HELP = '第一个可写文件夹是它跑命令的默认目录，也是相对路径的起点。系统目录和整个磁盘根目录不能加。';
const PROJ_HELP = '勾上的项目，它会看到简介、施工仓库、项目文件夹、下一步和最近 5 条决策标题。';

function settingsForm(a) {
  return `<form data-form="direct-agent" data-id="${attr(a.id)}" class="stack">
    <div class="grid-2"><div class="field"><label for="dName">名字</label><input id="dName" name="name" value="${attr(a.name || '')}" maxlength="40" required autocomplete="off"></div>
      <div class="field"><label for="dModel">模型（备注）</label><input id="dModel" name="model" value="${attr(a.model || '')}" placeholder="例如 GPT-6、Claude Opus 5.5" autocomplete="off"><span class="help">只给你自己看；实际用哪个模型由它所在的窗口决定</span></div></div>
    <div class="field"><label>权限模式</label>${modePicks(a.mode || 'auto_edit')}<span class="help">${MODE_HELP}</span></div>
    <div class="field"><label>文件夹</label>${folderEditor(a.folders || [])}<span class="help">${FOLDER_HELP}</span></div>
    <div class="field"><label>相关项目</label>${projectPicks(a.projects || [])}<span class="help">${PROJ_HELP}</span></div>
    <div class="field"><label for="dIns">写给它的说明</label><textarea id="dIns" name="instructions" rows="5" maxlength="8000" placeholder="例如：你负责整理个人助理项目的文档；改代码前先跟我确认方案；提交信息用中文。">${esc(a.instructions || '')}</textarea><span class="help">原样放进它的工作守则，最多 8000 字</span></div>
    <details class="direct-more"><summary class="muted">更多：建议先读的文件、自动放行的命令、命令时长</summary><div class="stack direct-more-body">
      <div class="field"><label for="dCtx">建议先读的文件（每行一个）</label><textarea id="dCtx" name="context_files" class="mono" rows="3" placeholder="绝对路径，或相对第一个文件夹">${esc((a.context_files || []).join('\n'))}</textarea></div>
      <div class="field"><label for="dAuto">自动放行的命令（每行一个前缀）</label><textarea id="dAuto" name="auto_commands" class="mono" rows="4">${esc((a.auto_commands || []).join('\n'))}</textarea><span class="help">只在「自动改文件」模式生效；带 ; | &amp; &gt; $( ) 的命令不会自动放行</span></div>
      <div class="field"><label for="dTimeout">单条命令最长（秒，10–1800）</label><input id="dTimeout" name="run_timeout_max" type="number" min="10" max="1800" step="10" value="${attr(String(a.run_timeout_max || 600))}"><span class="help">跑完整测试一般要几分钟</span></div>
    </div></details>
    <div class="danger-zone stack"><h3>危险操作</h3>
      <div class="row wrap"><button type="button" class="small" data-action="direct-rotate" data-id="${attr(a.id)}">换令牌</button><span class="faint">旧链接立即失效，要按「接入」里的新链接重新连</span></div>
      <div class="row wrap"><button type="button" class="small ghost danger" data-action="direct-delete" data-id="${attr(a.id)}">${icon('trash')}删除这个直连 Agent</button><span class="faint">连接立即失效，排队的审批作废；活动记录留在本机</span></div>
    </div>
    <div class="row wrap"><button type="submit" class="primary">保存</button><span class="faint" data-direct-dirty></span></div>
  </form>`;
}
function newForm() {
  return `<form data-form="direct-new" class="stack">
    <div class="field"><label for="dnName">名字</label><input id="dnName" name="name" maxlength="40" required placeholder="例如：仓库管家" autocomplete="off"></div>
    <div class="field"><label>权限模式</label>${modePicks('auto_edit')}<span class="help">${MODE_HELP}</span></div>
    <div class="field"><label>文件夹</label>${folderEditor([])}<span class="help">${FOLDER_HELP}</span></div>
    <div class="field"><label>相关项目</label>${projectPicks([])}<span class="help">${PROJ_HELP}</span></div>
    <div class="field"><label for="dnIns">写给它的说明（可选）</label><textarea id="dnIns" name="instructions" rows="4" maxlength="8000" placeholder="它负责什么、有哪些规矩。以后在「设置」里随时改。"></textarea></div>
    <div class="row wrap"><button type="submit" class="primary">${icon('plus')}新建并去接入</button><button type="button" class="ghost" data-action="close-drawer">取消</button></div>
  </form>`;
}

// ---------- 抽屉各块 ----------
function approvalCard(x, withAgent) {
  const [label, tone] = AP_STATUS[x.status] || [x.status, 'plain'];
  const live = x.status === 'pending';
  return `<article class="card pad stack direct-ap" data-key="ap:${attr(x.id)}">
    <div class="row wrap"><span class="chip ${tone}">${esc(label)}</span><span class="chip plain">${esc(KIND[x.kind] || x.kind)}</span>${withAgent ? `<b>${esc(x.agent_name || x.agent)}</b>` : ''}<span class="faint" data-reltime="${attr(x.created)}">${esc(relTime(x.created))}</span></div>
    <b class="direct-ap-title">${esc(x.summary)}</b>
    ${x.why ? `<div class="muted">它说：${esc(x.why)}</div>` : ''}${x.reason ? `<div class="faint">为什么要你批：${esc(x.reason)}</div>` : ''}
    <details${live ? ' open' : ''}><summary class="muted">${x.kind === 'run' ? '完整命令' : '改动预览'}</summary><pre class="codeblock direct-code">${esc(x.preview || '')}</pre></details>
    ${x.result ? `<details><summary class="muted">执行结果</summary><pre class="codeblock direct-code">${esc(x.result)}</pre></details>` : ''}
    ${x.note ? `<div class="faint">你的留言：${esc(x.note)}</div>` : ''}
    ${live ? `<div class="row wrap direct-decide"><input class="grow" data-direct-note="${attr(x.id)}" data-keep data-keep-id="dnote:${attr(x.id)}" placeholder="留言给它（可选，拒绝时建议写原因）" aria-label="留言给它" autocomplete="off"><button type="button" class="ghost" data-action="direct-decide" data-decision="deny" data-id="${attr(x.id)}">拒绝</button><button type="button" class="primary" data-action="direct-decide" data-decision="approve" data-id="${attr(x.id)}">${icon('check')}批准并执行</button></div>` : ''}
  </article>`;
}
function alertsHtml(a) {
  const pend = pendingOf(a.id), out = [];
  if (a.enabled === false) out.push('<div class="banner">已停用：它的连接现在会被拒绝。勾上右上角「启用」恢复。</div>');
  if (!(a.folders || []).length) out.push('<div class="banner"><span class="grow">还没给它文件夹：连上后所有文件工具都会拒绝。</span><button type="button" class="small" data-action="direct-pane" data-pane="settings">去设置</button></div>');
  if (!a.last_seen) out.push('<div class="banner info"><span class="grow">还没接入过：按「接入」里的步骤把它连到你的 AI 窗口。</span><button type="button" class="small" data-action="direct-pane" data-pane="connect">去接入</button></div>');
  if (pend.length) out.push(`<div class="detail-label">等你批准 · ${pend.length}</div><div class="stack">${pend.map(x => approvalCard(x, false)).join('')}</div>`);
  return out.join('');
}
function tasksHtml(a) {
  const list = a.tasks || [];
  if (!list.length) return '<div class="faint">还没派过任务。</div>';
  return `<div class="stack">${list.map(t => { const [label, tone] = TASK_ST[t.status] || [t.status, 'plain']; return `<div class="card pad stack direct-task" data-key="t:${attr(t.id)}">
    <div class="row wrap"><span class="chip ${tone} plain">${esc(label)}</span><span class="faint" data-reltime="${attr(t.created)}">${esc(relTime(t.created))}</span><span class="grow"></span>${t.status === 'open' ? `<button type="button" class="small ghost" data-action="direct-task-cancel" data-id="${attr(a.id)}" data-task="${attr(t.id)}">取消</button>` : ''}</div>
    <div class="direct-text">${esc(t.text)}</div>
    ${t.result ? `<div class="direct-result"><div class="faint">它的回报 · ${esc(relTime(t.updated))}</div><div class="direct-text">${esc(t.result)}</div></div>` : ''}</div>`; }).join('')}</div>`;
}
function activityHtml(a) {
  const acts = a.activity || [];
  if (!acts.length) return '<div class="empty"><strong>还没有活动</strong><span>它连上并调用工具后，这里按时间倒序列出读了什么、改了什么、跑了什么</span></div>';
  return `<div class="timeline">${acts.map((x, i) => `<div class="tl ${OUTCOME[x.outcome] || ''}" data-key="act:${attr(x.time)}:${i}"><time>${esc(fmtDate(x.time))} · ${esc(TOOL[x.tool] || KIND[x.tool] || x.tool)}</time>${esc(x.summary)}</div>`).join('')}</div>`;
}
function codeField(a, which, label) {
  const l = D.links[a.id] || {}, text = { url: l.url, prompt: l.prompt, claude: l.claude_cmd, config: l.config }[which] || '';
  return `<div class="field"><div class="row wrap"><label>${esc(label)}</label><span class="grow"></span><button type="button" class="small" data-action="direct-copy" data-id="${attr(a.id)}" data-which="${which}">${icon('copy')}复制</button></div><pre class="codeblock direct-code">${esc(text)}</pre></div>`;
}
function connectHtml(a) {
  const l = D.links[a.id];
  if (!l) return '<div class="sk"></div><div class="sk"></div>';
  if (l.error) return `<div class="banner danger">${esc(l.error)}</div>`;
  return `<div class="banner">下面的链接和配置里含它的令牌，等于这个 Agent 的钥匙。只贴进你自己的窗口；泄露了就在「设置」里换令牌，旧链接立即失效。</div>
    <div class="detail-label">网页版 AI（Claude.ai、ChatGPT 等支持 MCP 连接器的）</div>
    <p class="muted direct-step">在连接器里新增一个 MCP 服务，地址填这条链接；连上后把开场白发给它。</p>
    ${codeField(a, 'url', '链接')}${codeField(a, 'prompt', '开场白')}
    <div class="detail-label">本机 Claude Code</div>
    <p class="muted direct-step">在要用它的终端里执行一次，之后在 Claude Code 里发开场白。</p>
    ${codeField(a, 'claude', '命令')}
    <div class="detail-label">其它本机宿主（Claude 桌面、Cursor、Codex 等）</div>
    <p class="muted direct-step">把这段合并进宿主的 MCP 配置文件。</p>
    ${codeField(a, 'config', '配置')}`;
}
function segHtml(a) {
  const n = pendingOf(a.id).length;
  return PANES.map(([k, l]) => `<button type="button" class="${D.pane === k ? 'active' : ''}" data-action="direct-pane" data-pane="${k}" aria-pressed="${D.pane === k}">${l}${k === 'work' && n ? ` <span class="pill-badge warn">${n}</span>` : ''}</button>`).join('');
}
function drawerBody(a) {
  const pane = k => D.pane === k ? '' : ' hidden';
  return `<div class="stack direct-drawer" data-direct="${attr(a.id)}">
    <div class="row wrap"><div class="segmented" data-direct-seg>${segHtml(a)}</div><span class="grow"></span><label class="check"><input type="checkbox" data-direct-enable="${attr(a.id)}"${a.enabled !== false ? ' checked' : ''}> 启用</label></div>
    <section class="stack" data-pane="work"${pane('work')}>
      <div class="stack" data-direct-live="alerts">${alertsHtml(a)}</div>
      <div class="detail-label">派任务</div>
      <form data-form="direct-task" data-id="${attr(a.id)}" class="stack direct-task-form"><textarea name="text" rows="3" placeholder="写清楚要它做什么、做到什么程度算完" aria-label="给它派的任务"></textarea>
        <div class="row wrap"><span class="faint grow">它不会自己轮询：派完在它的窗口里说一句「看看工作台的任务」。</span><button type="submit" class="primary">${icon('send')}派给它</button></div></form>
      <div class="detail-label">任务</div>
      <div data-direct-live="tasks">${tasksHtml(a)}</div>
    </section>
    <section data-pane="settings"${pane('settings')}>${settingsForm(a)}</section>
    <section data-pane="connect"${pane('connect')}><div class="stack" data-direct-live="connect">${connectHtml(a)}</div></section>
    <section data-pane="activity"${pane('activity')}><div data-direct-live="activity">${activityHtml(a)}</div></section>
  </div>`;
}
const subtitle = a => `${a.mode_label || a.mode}${a.enabled === false ? ' · 已停用' : ''} · ${a.last_seen ? `最近活动 ${relTime(a.last_seen)}` : '还没连上过'}${a.model ? ` · ${a.model}` : ''}`;

// ---------- 打开抽屉 ----------
export async function openDirect(id, pane = '') {
  try { await loadDetail(); } catch (e) { return notify.error(e, { prefix: '读取直连 Agent 失败：' }); }
  const a = agentOf(id); if (!a) return toast('这个直连 Agent 已经不在了', 'warn');
  D.pane = PANES.some(p => p[0] === pane) ? pane : (a.last_seen || pendingOf(id).length ? 'work' : 'connect');
  F.drawer = { kind: 'direct', key: id };
  openDrawer({ title: a.name, subtitle: subtitle(a), kind: 'direct', body: drawerBody(a) });
  startPoll();
  if (D.pane === 'connect') loadLink(id);
}
export async function openDirectApprovals() {
  try { await loadDetail(); } catch (e) { return notify.error(e, { prefix: '读取审批失败：' }); }
  F.drawer = { kind: 'direct-approvals', key: '_all' };
  openDrawer({ title: '直连 Agent · 等你批准', subtitle: '批准 = 按卡上的内容原样执行一次；拿不准就拒绝并留言', kind: 'direct', body: `<div class="stack" data-direct-live="approvals">${approvalsHtml()}</div>` });
  startPoll();
}
function approvalsHtml() {
  const live = (D.data?.approvals || []), done = D.data?.recent_approvals || [];
  return `${live.length ? `<div class="stack">${live.map(x => approvalCard(x, true)).join('')}</div>` : '<div class="empty"><strong>没有等你批准的操作</strong><span>直连 Agent 在「逐项审批」「自动改文件」模式下写文件、跑命令时会排到这里；桌面通知插件开着时右下角也会弹提醒</span></div>'}
    ${done.length ? `<details class="direct-more"><summary class="muted">最近处理过的 · ${done.length}</summary><div class="stack direct-more-body">${done.map(x => approvalCard(x, true)).join('')}</div></details>` : ''}`;
}
export async function openDirectNew() {
  if (!D.data) { try { await loadDetail(); } catch { /* 模式说明拿不到就用兜底名单 */ } }
  F.drawer = { kind: 'direct-new', key: '_new' };
  openDrawer({ title: '新建直连 Agent', subtitle: '你单独对话、派活的 Agent：不归 Leader 管，用完即走', kind: 'direct', body: newForm() });
}
async function loadLink(id) {
  if (D.links[id] && !D.links[id].error) return;
  try { D.links[id] = await api(`/api/direct/link?id=${encodeURIComponent(id)}`); } catch (e) { D.links[id] = { error: e.message }; }
  paintLive();
}
function showPane(pane) {
  D.pane = pane;
  const host = $('#drawer [data-direct]'); if (!host) return;
  $$('[data-pane]', host).forEach(s => { if (s.tagName === 'SECTION') s.hidden = s.dataset.pane !== pane; });
  const a = agentOf(host.dataset.direct); if (a) morph($('[data-direct-seg]', host), segHtml(a));
  if (pane === 'connect') loadLink(host.dataset.direct);
}

// ---------- 实时：抽屉开着时，状态一变就重拉详情、只换活的几块 ----------
function paintLive() {
  if (!drawerOpen()) return;
  if (F.drawer?.kind === 'direct-approvals') { const box = $('#drawer [data-direct-live="approvals"]'); if (box) morph(box, approvalsHtml()); return; }
  if (F.drawer?.kind !== 'direct') return;
  const a = agentOf(F.drawer.key); if (!a) return;
  const part = (k, html) => { const box = $(`#drawer [data-direct-live="${k}"]`); if (box) morph(box, html); };
  part('alerts', alertsHtml(a)); part('tasks', tasksHtml(a)); part('activity', activityHtml(a)); part('connect', connectHtml(a));
  const seg = $('#drawer [data-direct-seg]'); if (seg) morph(seg, segHtml(a));
  const sub = $('#drawerSubtitle'); if (sub) sub.textContent = subtitle(a);
}
let inflight = false;
const isDirectDrawer = () => ['direct', 'direct-approvals'].includes(F.drawer?.kind) && drawerOpen();   // 先看类型再碰 DOM
export async function refreshDirectDrawer(force = false) {
  if (inflight || !isDirectDrawer()) return;
  if (!force && Date.now() - D.at < 1500) return;
  inflight = true;
  try { await loadDetail(); paintLive(); } catch { /* 下一次状态变化或轮询再试 */ } finally { inflight = false; }
}
store.on('direct', () => refreshDirectDrawer());
// 活动记录太频繁不进 SSE：只在直连抽屉开着时每 5 秒补一次，抽屉一关就停（模块加载时不挂定时器）
let poll = null;
function startPoll() {
  if (poll) return;
  poll = setInterval(() => { if (!isDirectDrawer()) { clearInterval(poll); poll = null; return; } if (!document.hidden) refreshDirectDrawer(); }, 5000);
}

// ---------- 交互 ----------
function markDirty(form) { const out = form && $('[data-direct-dirty]', form); if (out) out.textContent = '有未保存的修改'; }
document.addEventListener('change', e => {
  const t = e.target;
  if (t.matches?.('[data-direct-pick]')) {
    if (t.type === 'radio') $$(`input[name="${t.name}"]`, t.form).forEach(r => r.closest('.pick')?.classList.toggle('on', r.checked));
    else t.closest('.pick')?.classList.toggle('on', t.checked);
  }
  if (t.matches?.('[data-direct-enable]')) toggleEnable(t);
});
document.addEventListener('input', e => { const f = e.target.closest?.('form[data-form="direct-agent"]'); if (f) markDirty(f); });
async function toggleEnable(t) {
  const on = t.checked;
  try { await post('/api/direct/save', { id: t.dataset.directEnable, enabled: on }); toast(on ? '已启用' : '已停用：它的连接立即失效', 'ok'); await refreshDirectDrawer(true); DIRECT_HOOKS.refresh(); }
  catch (err) { t.checked = !on; notify.error(err); }
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch { const ta = document.createElement('textarea'); ta.value = text; document.body.append(ta); ta.select(); document.execCommand('copy'); ta.remove(); }
  toast('已复制', 'ok', { ms: 1500 });
}

export const DIRECT_ACTIONS = {
  'direct-open': t => openDirect(t.dataset.id),
  'direct-new': () => openDirectNew(),
  'direct-approvals': () => openDirectApprovals(),
  'direct-pane': t => showPane(t.dataset.pane),
  'direct-folder-add': t => addFolderRow(t, {}),
  'direct-folder-pick': t => { addFolderRow(t, { path: t.dataset.path, access: t.dataset.access }); t.remove(); },
  'direct-folder-del': t => { const form = t.closest('form'); t.closest('[data-folder-row]')?.remove(); markDirty(form); },
  'direct-copy': t => { const l = D.links[t.dataset.id] || {}; copyText({ url: l.url, prompt: l.prompt, claude: l.claude_cmd, config: l.config }[t.dataset.which] || ''); },
  'direct-decide': async t => {
    const id = t.dataset.id, approve = t.dataset.decision === 'approve';
    const note = $(`[data-direct-note="${CSS.escape(id)}"]`)?.value.trim() || '';
    const row = t.closest('.direct-decide'); $$('button', row).forEach(b => { b.disabled = true; });
    try { await post('/api/direct/decide', { id, decision: approve ? 'approve' : 'deny', note }); toast(approve ? '已批准，正在执行，结果会回到它那边' : '已拒绝，它会停下来问你', 'ok'); await refreshDirectDrawer(true); DIRECT_HOOKS.refresh(); }
    catch (err) { $$('button', row).forEach(b => { b.disabled = false; }); notify.error(err); }
  },
  'direct-task-cancel': async t => {
    try { await post('/api/direct/task-update', { agent: t.dataset.id, task_id: t.dataset.task, status: 'cancelled' }); toast('任务已取消', 'ok'); await refreshDirectDrawer(true); DIRECT_HOOKS.refresh(); }
    catch (err) { notify.error(err); }
  },
  'direct-rotate': async t => {
    const a = agentOf(t.dataset.id); if (!a) return;
    if (!(await confirmDialog({ title: `给「${esc(a.name)}」换令牌`, body: '它现在用的链接会立即失效，要按「接入」里的新链接重新连。', confirmText: '换令牌', danger: true }))) return;
    try { await post('/api/direct/rotate', { id: a.id }); delete D.links[a.id]; toast('已换令牌：去「接入」复制新链接', 'ok'); await refreshDirectDrawer(true); showPane('connect'); }
    catch (err) { notify.error(err); }
  },
  'direct-delete': async t => {
    const a = agentOf(t.dataset.id); if (!a) return;
    if (!(await confirmDialog({ title: `删除直连 Agent「${esc(a.name)}」`, body: '它的连接立即失效，还在排队的审批会作废。活动记录保留在本机。', confirmText: '删除', danger: true }))) return;
    try { await post('/api/direct/delete', { id: a.id }); delete D.links[a.id]; closeDrawer(); toast('已删除', 'ok'); DIRECT_HOOKS.refresh(); }
    catch (err) { notify.error(err); }
  }
};

export const DIRECT_FORMS = {
  'direct-agent': async form => {
    const fd = new FormData(form), id = form.dataset.id;
    const body = { id, name: String(fd.get('name') || '').trim(), model: String(fd.get('model') || '').trim(), mode: String(fd.get('mode') || 'auto_edit'),
      folders: readFolders(form), projects: fd.getAll('projects').map(String), instructions: String(fd.get('instructions') || ''),
      context_files: lines(fd.get('context_files')), auto_commands: lines(fd.get('auto_commands')), run_timeout_max: Number(fd.get('run_timeout_max')) || 600 };
    if (!body.name) return toast('名字不能为空', 'warn');
    try {
      await post('/api/direct/save', body);
      const out = $('[data-direct-dirty]', form); if (out) out.textContent = '';
      $('#drawerTitle').textContent = body.name;
      toast('已保存：它下次调用工具就按新配置来；让它重新调用 direct_info 能读到新守则', 'ok');
      await refreshDirectDrawer(true); DIRECT_HOOKS.refresh();
    } catch (err) { notify.error(err, { prefix: '没保存：' }); }
  },
  'direct-new': async form => {
    const fd = new FormData(form), name = String(fd.get('name') || '').trim();
    if (!name) return toast('先起个名字', 'warn');
    try {
      const r = await post('/api/direct/save', { name, mode: String(fd.get('mode') || 'auto_edit'), folders: readFolders(form), projects: fd.getAll('projects').map(String), instructions: String(fd.get('instructions') || '') });
      toast('已新建：按「接入」里的步骤把它连到你的 AI 窗口', 'ok'); DIRECT_HOOKS.refresh();
      await openDirect(r.id, 'connect');
    } catch (err) { notify.error(err, { prefix: '没建成：' }); }
  },
  'direct-task': async form => {
    const ta = $('textarea[name=text]', form), text = ta.value.trim();
    if (!text) return toast('任务内容是空的', 'warn');
    try { await post('/api/direct/task', { agent: form.dataset.id, text }); ta.value = ''; toast('已派给它：在它的窗口里说一句「看看工作台的任务」', 'ok'); await refreshDirectDrawer(true); DIRECT_HOOKS.refresh(); }
    catch (err) { notify.error(err); }
  }
};

// 深层链接：?direct=<id> 打开某个直连 Agent，?direct=approvals 打开待批列表（旧的 /v2/direct.html 跳到这里）
export function openDirectFromUrl() {
  const v = new URLSearchParams(location.search).get('direct'); if (!v) return false;
  const u = new URL(location.href); u.searchParams.delete('direct'); history.replaceState(history.state, '', u);
  if (v === 'approvals') openDirectApprovals(); else if (v === 'new') openDirectNew(); else openDirect(v);
  return true;
}
