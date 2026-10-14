// 领域模型：状态字典、选择器（纯函数，不接触 DOM）
import { createStore, minutesSince } from './core.js';

export const STATUS = { draft: '草稿', dispatched: '已派发', collecting: '收集中', ready_for_merge: '待合并', merged: '已合并', adopted: '已采纳', archived: '已归档', cancelled: '已取消', superseded: '已被取代', stalled: '停滞', rejected: '已驳回' };
export const STATUS_TONE = { draft: '', dispatched: 'info', collecting: 'violet', ready_for_merge: 'warn', merged: 'info', adopted: 'ok', archived: 'ok', cancelled: 'bad', superseded: '', stalled: 'bad', rejected: 'bad' };
export const EXEC_LABEL = { released: '已落地', pending: '未落地', partial: '部分落地', 'n-a': '无需落地' };
export const PEER_STATE = { queued: '排队中', working: '作答中', stalled: '已领取未回', answered: '已回答', expired: '已过期', missing: '未派到（无任务包）', connection_timeout: '连接超时', failed: '失败', late: '迟到回答' };
export const NAV = [
  ['overview', 'grid', '概览'], ['running', 'play', '进行中'], ['decisions', 'layers', '决策'], ['artifacts', 'box', '成果'],
  ['compose', 'compose', '发起'], ['peers', 'users', 'Agent'], ['settings', 'settings', '设置']
];
export const ARTIFACT_KIND = { page: '页', doc: '文', code: '码', image: '图', data: '表' };

// 图片附件白名单：唯一实现在 /v2/js/image_types.js（经典脚本，index.html 先于本模块加载，v1 也复用）；这里只转发，不再写第二份规则
const WB_IMAGE = globalThis.WB_IMAGE || (console.error('image_types.js 未加载：图片附件将全部被拒'), { ACCEPT: '', looksLikeImage: () => false });
export const IMAGE_ACCEPT = WB_IMAGE.ACCEPT;
export const looksLikeImage = WB_IMAGE.looksLikeImage;

export const store = createStore({
  time: '', peers: [], projects: [], commands: [], recent_done: [], open_tasks: [], bridge_url: '', controller: null,
  view: new URLSearchParams(location.search).get('view') || localStorage.getItem('bus.view') || 'overview',
  project: new URLSearchParams(location.search).get('p') || localStorage.getItem('bus.project') || '',
  settings: null, connected: false, live: 'poll', lastRefresh: 0, history: [], ext: null
});

// 界面易失状态（不触发订阅）
export const F = {
  composer: null, composerDirty: false, expanded: new Set(JSON.parse(localStorage.getItem('bus.expanded') || '[]')),
  mergeCache: new Map(), drawer: { kind: '', key: '' }, pending: new Set(), followup: null,
  filters: { running: '', runningStuck: false, decisions: '', decisionStatus: '', artifacts: 'project', peers: '', answersTab: '' },
  selection: new Set(), sort: { key: 'updated', dir: 'desc' }
};

export const S = () => store.get();
// 没有指定项目（首次打开、或记住的项目已删除）时，选最近有动静的项目，而不是列表第一个——否则总落在空项目上
const tsKey = t => String(t || '').replace(' ', 'T').slice(0, 19);  // 统一成 YYYY-MM-DDTHH:MM:SS 再比，防止混入空格分隔 / 微秒 / 时区后字典序错乱
const lastActivity = p => (p.decisions || []).reduce((m, d) => { const t = tsKey(d.updated || d.created); return t > m ? t : m; }, tsKey(p.updated));
export function defaultProject(list) { return (list || []).reduce((best, p) => (!best || lastActivity(p) > lastActivity(best) ? p : best), null); }
export function project(s = S()) { return s.projects.find(p => p.slug === s.project) || defaultProject(s.projects); }
export const decisions = (p = project()) => (p && Array.isArray(p.decisions) ? p.decisions : []);
const hiddenNow = d => { const h = F.hidden?.get(d.task_id); if (!h) return false; if (Date.now() - h.t > 600000 || d.status !== h.status) { F.hidden.delete(d.task_id); return false; } return true; };
// b342：ack 绑不上任务时后端只挂 warning、不再写 cancelled。这类记录必须留在面板上，否则用户的派发又一次静默消失。
export const isUnbound = d => !!(d && d.warning && d.warning.code === 'ack_task_unbound');
export const UNBOUND_HINT = '派发已完成，但面板未绑定任务，等待 Leader 修复';
export const activeDecisions = (p = project()) => decisions(p).filter(d => (isUnbound(d) || !['archived', 'cancelled', 'superseded', 'rejected'].includes(d.status)) && !hiddenNow(d));
export const runningDecisions = (p = project()) => activeDecisions(p).filter(d => ['dispatched', 'collecting', 'ready_for_merge'].includes(d.status));
export const unboundTasks = (s = S()) => (Array.isArray(s.unbound) ? s.unbound : []);
export const unboundCount = (s = S()) => unboundTasks(s).length;
// 终态口径由服务端 /api/state 下发（bus_projects.TERMINAL_STATUSES 的同一份）；下面只是服务端缺席时的兜底
export const TERMINAL_FALLBACK = ['merged', 'adopted', 'archived', 'cancelled', 'superseded', 'rejected'];
export const terminalStatuses = (s = S()) => (Array.isArray(s.terminal_statuses) && s.terminal_statuses.length ? s.terminal_statuses : TERMINAL_FALLBACK);
export const doneDecisions = (p = project()) => decisions(p).filter(d => terminalStatuses().includes(d.status)).sort((a, b) => String(b.updated || b.created).localeCompare(String(a.updated || a.created)));
export const agentList = (s = S()) => (s.peers || []).filter(p => p.peer !== 'controller');
export const onlineAgents = (s = S()) => agentList(s).filter(p => p.online !== false);
// 「在线」与「可派发」是两件事（cd14 第 1 项）：relay 正在替 Leader 干活、paused 是本项目里临时不接单，
// 两者都还在线、通讯录里照样看得见，但不进默认选择和动态团队展开。在线总数仍用 onlineAgents()。
export const DYNAMIC_TEAM = '全员在线';
export const PROJECT_TEAM = '项目团队';   // 内置：展开为项目静态成员（project.peers）
// relay 判定：f0ca（335cd7a 已入 main）把代理 Leader 的心跳改成 status='busy' + role='relay'，
// 角色只在新字段 role 里；旧心跳仍可能写 status='relay'，所以双写期两边都认（e51b 必改 1）。
// 这里刻意不读 activity / state —— 那是 e839 第 3-5 步的范围。
export const isRelay = a => !!a && (a.role === 'relay' || a.status === 'relay');
// 暂停有两个来源，且都必须有真实写入入口（e51b 必改 2）：
//   profile.paused          —— 全局资料表单（profiles_ui 的「暂停自动推荐」），落 profiles/<peer>.json
//   agent_meta.paused_in_project —— 本项目备注表单，落 projects/<slug>/project.json，按键浅合并
// 原来判定读 agent_meta.paused，而没有任何表单写它，功能从界面不可达；改名是为了两个来源不再同名。
export const isPausedGlobally = (name, s = S()) => (s.profiles || {})[name]?.paused === true;
export const isPausedInProject = (name, p = project()) => agentMeta(name, p).paused_in_project === true;
export const isPaused = (name, p = project(), s = S()) => isPausedGlobally(name, s) || isPausedInProject(name, p);
export const dispatchableAgents = (p = project(), s = S()) => onlineAgents(s).filter(a => !isRelay(a) && !isPaused(a.peer, p, s));
export function undispatchableReason(name, p = project()) {
  const a = agentByName(name);
  if (!a || a.online === false) return '离线';
  if (isRelay(a)) return '代理 Leader 中';
  if (isPausedGlobally(name)) return '已暂停接单';
  if (isPausedInProject(name, p)) return '本项目已暂停';
  return '';
}
export const agentByName = name => agentList().find(p => p.peer === name) || { peer: name, model: '', online: false, status: 'offline' };
export const projectPeers = (p = project()) => ((p && p.peers) || []).filter(Boolean);
export const agentMeta = (name, p = project()) => ((p && p.agent_meta) || {})[name] || {};
const peerLabel = (name, s = S()) => ((s.peers || []).find(x => x.peer === name) || {}).label || '';
export const displayName = (name, p = project()) => agentMeta(name, p).display_name || S().profiles?.[name]?.display_name || peerLabel(name) || name;
export const titleOf = d => d.title || (d.progress && d.progress.meta && d.progress.meta.question || '').split(/\n/)[0] || d.task_id || '未命名决策';
// 903f 谱系 / 分页：服务端派生键 lineage / bucket 缺省时本地兜底，旧记录 self-root、显示原标题
export const displayTitle = d => (d.lineage && d.lineage.display_title) || titleOf(d);
export const rootOf = d => (d.lineage && d.lineage.root_task) || d.root_task || d.task_id;
export const BUCKETS = { agent: 'Agent 处理中', user: '等你一步', decide: '等待决策', done: '已完成' };
export const bucketOf = d => d.bucket || (d.status !== 'adopted' ? '' : (['released', 'n-a'].includes(d.execution) || d.implementation?.state === 'merged') ? 'done' : 'agent');
export const parentOf = (d, p = project()) => { const pid = d.lineage?.parent_task || d.parent_task; return pid ? decisions(p).find(x => x.task_id === pid) || null : null; };
export const childrenOf = (d, p = project()) => decisions(p).filter(x => (x.lineage?.parent_task || x.parent_task) === d.task_id);
export function threadsOf(list, p = project()) {
  // 按 root_task 分组；组内按 round / created 升序；组间按最近更新降序。没有谱系字段的旧记录各自成组，不做猜测合并
  const groups = new Map();
  for (const d of list) { const r = rootOf(d); if (!groups.has(r)) groups.set(r, []); groups.get(r).push(d); }
  const ts = d => String(d.updated || d.created || '');
  return [...groups].map(([root, items]) => {
    items.sort((a, b) => ((a.lineage?.round || a.round || 0) - (b.lineage?.round || b.round || 0)) || String(a.created || '').localeCompare(String(b.created || '')));
    const latest = items.reduce((m, d) => ts(d) > ts(m) ? d : m, items[0]);
    return { root, topic: (items.find(d => d.lineage?.topic || d.topic) || {}).lineage?.topic || items.find(d => d.topic)?.topic || '', items, latest };
  }).sort((a, b) => ts(b.latest).localeCompare(ts(a.latest)));
}
export const artifactsOf = d => Array.isArray(d && d.artifacts) ? d.artifacts.filter(a => a && a.path) : [];
export const isDelivered = d => ['adopted', 'archived'].includes(d.status) && artifactsOf(d).length > 0;
export function deliveredDecisions(scope = F.filters.artifacts) {
  const list = scope === 'all' ? S().projects.flatMap(p => decisions(p).map(d => ({ d, p }))) : decisions().map(d => ({ d, p: project() }));
  return list.filter(x => isDelivered(x.d)).sort((a, b) => String(b.d.updated || b.d.created).localeCompare(String(a.d.updated || a.d.created)));
}
export function weekDelivered(p = project()) { const lim = Date.now() - 7 * 86400000; return decisions(p).filter(d => isDelivered(d) && new Date(String(d.updated || d.created).replace(' ', 'T')).getTime() >= lim).length; }
export function progressOf(d) {
  const pr = d.progress || {};
  const expected = Array.isArray(pr.expected) ? pr.expected.length : (d.peers || []).length || 1;
  const answered = Array.isArray(pr.answered) ? pr.answered.length : 0;
  const quorum = pr.quorum || expected;
  return { answered, expected, quorum, complete: !!pr.complete || (answered >= quorum && answered > 0), states: pr.states || {}, meta: pr.meta || {} };
}
export function builtinTeams(p = project()) {
  const teamPeers = projectPeers(p);
  // 项目默认团队里没有一个在线成员时不显示该快捷项，避免出现指向已失效 peer 的按钮
  const alive = teamPeers.filter(n => agentByName(n).online);
  const team = alive.length ? [{ name: '项目团队', peers: alive, mode: p?.mode || 'independent', note: '当前项目团队中在线者' }] : [];
  return [...team, { name: '全员在线', peers: [], mode: 'independent', note: '派发时取全部在线 Agent' }];
}
export function effectiveTeams(p = project()) {
  const custom = Array.isArray(p?.teams) ? p.teams : []; const names = new Set(custom.map(t => t.name));
  const all = [...custom, ...builtinTeams(p).filter(t => !names.has(t.name))];
  // 动态「全员在线」永远排第一（cd14 第 3 项）：它是缺省口径，不该被自定义团队挤到后面
  return [...all.filter(t => t.name === DYNAMIC_TEAM), ...all.filter(t => t.name !== DYNAMIC_TEAM)];
}
// 项目默认团队（project.default_team，后端白名单校验）。空、或指向已被删除/改名的团队时，回退到动态全员在线
export function defaultTeamName(p = project()) {
  const name = (p && p.default_team) || '';
  // 44f1：「项目团队」是保存过的明确选择；成员暂时全离线时快捷项会隐藏，但默认值不能因此被偷换成全员在线
  if (name === PROJECT_TEAM && projectPeers(p).length) return PROJECT_TEAM;
  return name && effectiveTeams(p).some(t => t.name === name) ? name : DYNAMIC_TEAM;
}
// 团队 → 实际名单：动态全员在线取当前可派发者（不是静态名单）；固定团队按 teams 里登记的成员展开
export function teamPeersOf(name, p = project()) {
  if (!name || name === DYNAMIC_TEAM) return dispatchableAgents(p).map(a => a.peer);
  const t = effectiveTeams(p).find(x => x.name === name);
  if (t) return (t.peers || []).filter(Boolean);
  // 项目团队无人在线时不在快捷项里，作默认值时展开为全部静态成员；离线者由提交前的内联确认提示
  if (name === PROJECT_TEAM) return projectPeers(p).filter(Boolean);
  return [];
}
// 默认勾选 = 项目默认团队；缺省是动态全员在线。project.peers 仍是项目静态成员关系，只用于「项目团队」这个快捷项
export function defaultComposerPeers(p = project()) { return teamPeersOf(defaultTeamName(p), p); }
// 选择区的三个数：已选 / 其中可派发 / 离线或不可派发（cd14 第 3 项）
export function pickStats(peers, p = project()) {
  const list = [...new Set((peers || []).filter(Boolean))];
  const ok = list.filter(n => !undispatchableReason(n, p));
  return { selected: list.length, dispatchable: ok.length, blocked: list.length - ok.length };
}
// 选择区文案的唯一真值：views2 首帧与 app.js 的 syncComposer 都调它，不各写各的格式（e4e0 必改 2）
export function pickStatsText(peers, p = project()) {
  const s = pickStats(peers, p);
  return `已选 ${s.selected} · 可派发 ${s.dispatchable} · 离线/不可派发 ${s.blocked}`;
}
// 成本提示（cd14 第 4 项）：份数 = 真正会收到任务的人数。
// 「最慢约 X 分钟」需要每个 Agent 的历史回答耗时，而 /api/state 里没有这个数据，全仓也没有任何写入者。
// 所以这里只给份数，时间一律如实写「暂无数据」——不为一个还不存在的字段预先写取值 / 中位数 / 跨项目取样
// （e4e0 非阻塞 4、5：那属于替假想未来设计；真要有这个数，应连同落盘一起补，届时再改这里）。
export function dispatchEstimate(peers, p = project()) {
  const list = [...new Set((peers || []).filter(Boolean))].filter(n => !undispatchableReason(n, p));
  return { copies: list.length, slowestMin: null, text: `预计 ${list.length} 份 · 最慢时间暂无数据` };
}
export function ensureComposer(p = project()) {
  if (!p) { F.composer = null; return null; }
  if (!F.composer || F.composer.project !== p.slug) {
    F.composer = { project: p.slug, question: '', mode: p.mode || 'independent', peers: defaultComposerPeers(p), teamName: defaultTeamName(p), context_files: [...(p.context_files || [])], attachments: [], ttl_min: 10, min_answers: '', angles: false };
    F.composerDirty = false;
  }
  return F.composer;
}
export function countPending(p = project()) { return activeDecisions(p).filter(d => ['ready_for_merge', 'merged'].includes(d.status)).length + (S().commands || []).length; }
export function stallInfo(d) {
  const pr = progressOf(d); const created = pr.meta.created || d.created; const age = minutesSince(created);
  const ttl = Number(pr.meta.ttl_min) || 30;
  const waiting = Object.entries(pr.states).filter(([, s]) => s !== 'answered');
  if (d.status === 'ready_for_merge') return { text: '回答已到齐，等待 Leader 生成合并稿', tone: 'warn', terminal: false };
  if (waiting.some(([, s]) => s === 'expired')) return { text: `${waiting.filter(([, s]) => s === 'expired').length} 个 Agent 已过期未回`, tone: 'bad', terminal: true, suggest: '退回改派或直接合并已到的回答' };
  if (waiting.some(([, s]) => s === 'stalled')) return { text: '有 Agent 领取超过 15 分钟未回', tone: 'bad', terminal: false, suggest: '可退回改派' };
  if (!Object.keys(pr.states).length && !pr.meta.id) return { text: '任务包已丢失（已归档或被清理），只能重派或取消', tone: 'bad', terminal: true, suggest: '重派或取消' };
  if (age > ttl) return { text: `已超过时限 ${ttl} 分钟`, tone: 'bad', terminal: true, suggest: '重派或取消' };
  if (!pr.answered) return { text: '已派发，等待 Agent 领取', tone: 'info', terminal: false };
  return { text: `仍在等待 ${waiting.map(([n, s]) => `${displayName(n)}（${PEER_STATE[s] || s}）`).join('、')}`, tone: 'info', terminal: false };
}
// 本机绝对路径：根目录由服务端下发（state.workspace_root = 工作台服务的工作区），不在代码里写死任何人的目录
export const winPath = path => { const root = String(S().workspace_root || '').replace(/[\\/]+$/, ''); const rel = String(path || '').replace(/^\.?\//, '').replace(/\//g, '\\'); return root ? `${root}\\${rel}` : rel; };

// ---------- 调度（priority.json 的 task_order / agent_weight）----------
export const CONTROLLER_PEER = 'controller';
export const priorityOf = (s = S()) => s.priority || {};
export const taskOrder = (s = S()) => Array.isArray(priorityOf(s).task_order) ? priorityOf(s).task_order : [];
export const agentWeights = (s = S()) => priorityOf(s).agent_weight && typeof priorityOf(s).agent_weight === 'object' ? priorityOf(s).agent_weight : {};
export const agentWeight = (peer, s = S()) => agentWeights(s)[peer];
// 前三位给 P0 / P1 / P2 标记，其余只按位次排序不标注；未进表的任务排在已排序者之后
export const taskTier = (id, s = S()) => { const i = taskOrder(s).indexOf(id); return i === 0 ? 'P0' : i === 1 ? 'P1' : i === 2 ? 'P2' : ''; };
export function orderedTasks(list, s = S()) {
  const ord = taskOrder(s), pos = t => { const i = ord.indexOf(t); return i < 0 ? 10 ** 6 : i; };
  return list.slice().sort((a, b) => pos(a.task_id) - pos(b.task_id) || String(a.created || '').localeCompare(String(b.created || '')));
}
export const WEIGHT_OPTIONS = [['', '默认'], ['1', '低'], ['2', '中'], ['3', '高']];
// 租约与心跳剩余秒数：expires / time 都是服务端本地时间，按同一基准做差，不引入时区换算
export function secondsLeft(target, base) {
  const t = Date.parse(String(target || '').replace(' ', 'T')), b = Date.parse(String(base || '').replace(' ', 'T'));
  if (!Number.isFinite(t) || !Number.isFinite(b)) return null;
  return Math.round((t - b) / 1000);
}
export const fmtLeft = sec => sec == null ? '—' : sec <= 0 ? '已到期' : sec >= 60 ? `${Math.floor(sec / 60)} 分 ${sec % 60} 秒` : `${sec} 秒`;
