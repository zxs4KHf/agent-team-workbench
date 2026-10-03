// page-artifacts.js：从 views.js 拆出（ADR 0015，按页面拆模块；scripts/split_module.py 生成，函数体未改）
// 视图：概览、进行中、决策、成果
import { esc, attr, icon, sparkline, ring, fmtDate, relTime, minutesSince } from './core.js';
import { S, F, project, decisions, activeDecisions, isUnbound, UNBOUND_HINT, runningDecisions, doneDecisions, agentList, onlineAgents, agentByName, projectPeers, displayName, titleOf, displayTitle, bucketOf, BUCKETS, threadsOf, childrenOf, artifactsOf, deliveredDecisions, weekDelivered, progressOf, stallInfo, countPending, STATUS, STATUS_TONE, EXEC_LABEL, PEER_STATE, ARTIFACT_KIND, orderedTasks, taskTier } from './model.js';

import { artifactCard, capped, empty, head } from './views.js';

export function renderArtifacts(p) {
  const scope = F.filters.artifacts;
  const list = deliveredDecisions(scope);
  return `<div class="view">${head('交付物', '成果', '已采纳决策登记的页面、文档、代码与数据，点击路径直接预览。', `<div class="segmented" role="group" aria-label="范围"><button type="button" class="${scope === 'project' ? 'active' : ''}" data-action="artifact-scope" data-scope="project">当前项目</button><button type="button" class="${scope === 'all' ? 'active' : ''}" data-action="artifact-scope" data-scope="all">全部项目</button></div>`)}
    <section class="card fit-panel" data-key="fit-artifacts"><div class="fit-body">${list.length ? `<div class="artifact-grid stagger">${capped('artifacts', list, ({ d, p: proj }) => artifactCard(d, proj), 12)}</div>` : empty('还没有交付物', '采纳决策并由 Leader 登记交付物后出现在这里')}</div></section>
  </div>`;
}

