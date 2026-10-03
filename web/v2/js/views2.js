// 视图：发起、Agent、设置
import { esc, attr, icon, relTime, relAge, fmtDate } from './core.js';
import { S, F, project, agentList, onlineAgents, dispatchableAgents, undispatchableReason, DYNAMIC_TEAM, pickStats, pickStatsText, dispatchEstimate, agentByName, projectPeers, displayName, agentMeta, effectiveTeams, ensureComposer, IMAGE_ACCEPT, CONTROLLER_PEER, secondsLeft, fmtLeft } from './model.js';
import { head, empty, ovTabs, pageParts } from './views.js';
import { teamChips } from './components.js';
import * as Prefs from './prefs.js';


export { groupOrder, renderPeers, agentDetail, groupForm, allocatorCard, leaderBoard, relayCardHtml, RELAY_CARD_KEY } from './page-agents.js';
export { renderCompose } from './page-compose.js';
export { accentHint, renderSettings } from './page-settings.js';

export const initials = n => Array.from(String(n || '?')).slice(0, 2).join('').toUpperCase();
export const MODE_HELP = {
  race: { label: '抢单（谁先领谁做）', help: '发给选中的人，第一个领到的人做，其他人的副本立刻撤回。不经 Leader 排队，工作台直接派，最快拿到结果。适合一件事一个人做就够。' },
  discuss: { label: '方案讨论', help: 'Agent 只出结论文档，没有写代码的权限。适合先把做法定下来。' },
  build: { label: '施工', help: '每个 Agent 拿到自己的一份代码副本（git worktree）直接改，回复时自动提交；Reviewer 看过 diff 通过后，Leader 合并进主线。改同一文件也不会互相覆盖。' },
  independent: { label: '各自独立回答', help: '每个 Agent 看不到别人的答案，各自给结论、理由和一条反对意见。适合做决策前听多方意见。' },
  redteam: { label: '红队', help: '把你给的方案发给每个 Agent，扮演对手：找出方案会失败的具体场景、触发条件与后果，并给修改建议。适合方案定稿前压力测试。' },
  relay: { label: '接力补充', help: '把你给的骨架或别人的回答发给 Agent，在原结构上继续补充，改动处标 [新增] / [修改]。适合已有框架、需要填内容。' }
};
// 下拉框里显示中文名，提交的仍是英文值
export const modeLabel = m => (MODE_HELP[m] && MODE_HELP[m].label) || m;
// 某个 Agent 属于哪个组：后端算好的 effective_group（含人工归类，例如 sonnet1 → claude-sonnet）优先，再按模型名匹配
export function groupOfAgent(a) { const gs = S().agent_groups || []; return (a && a.effective_group && gs.find(g => g.id === a.effective_group)) || groupOfModel(a && a.model); }
export const GROUP_ORDER_KEY = 'wb.groupOrder';
export function saveGroupOrder(ids) { localStorage.setItem(GROUP_ORDER_KEY, JSON.stringify(ids)); }
const canon = m => String(m || '').toLowerCase().split(' · ')[0].replace(/^.*\//, '').replace(/[_ ]/g, '-');
export function groupOfModel(model) {
  // 2026-10-02：分组是工作台自己的数据（S().agent_groups ← console/agent_groups.json），不再看 Arena
  const k = canon(model), gs = S().agent_groups || [];
  return gs.find(g => (g.match || []).some(m => k === m || k.startsWith(m + '-') || k.startsWith(m + '.')));
}
export const badge = (ok, label) => `<span class="chip ${ok ? 'ok' : 'warn'}">${esc(label)}</span>`;
