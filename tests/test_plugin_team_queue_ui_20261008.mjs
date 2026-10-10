import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.location = { search: '' };
globalThis.localStorage = { getItem: () => null };
globalThis.WB_IMAGE = { ACCEPT: '', looksLikeImage: () => false };
globalThis.document = { addEventListener: () => {} };

const { store, F } = await import('../web/v2/js/model.js');
const { externalTaskInfo, renderRunning, runningMeta } = await import('../web/v2/js/page-running.js');
const drawer = readFileSync(new URL('../web/v2/js/app-drawers.js', import.meta.url), 'utf8');
const main = readFileSync(new URL('../web/v2/js/main.js', import.meta.url), 'utf8');

const now = new Date().toISOString();
const old = new Date(Date.now() - 90 * 60 * 1000).toISOString();
const external = {
  task_id: '20261008-000000-ab', title: '检查关卡节拍', status: 'dispatched', mode: 'race',
  external_owner: 'level-design', external_request_id: 'req-1', team: '关卡设计', peers: ['agent-a', 'agent-b'], created: old,
  progress: { meta: { id: '20261008-000000-ab', question: '检查关卡节拍', mode: 'independent', race: true,
    peers: ['agent-a', 'agent-b'], created: old, ttl_min: 30 },
  expected: ['agent-a', 'agent-b'], quorum: 1, answered: [], states: { 'agent-a': 'queued', 'agent-b': 'queued' } },
};
const regular = {
  task_id: '20261008-000001-ab', title: '普通对读', status: 'dispatched', mode: 'independent', peers: ['agent-a'],
  progress: { meta: { id: '20261008-000001-ab', question: '普通对读', mode: 'independent', peers: ['agent-a'], created: now, ttl_min: 30 },
    expected: ['agent-a'], quorum: 1, answered: ['agent-a'], complete: true, states: { 'agent-a': 'answered' } },
};
const project = { slug: 'demo', decisions: [external, regular] };
store.set({ project: 'demo', projects: [project], peers: [
  { peer: 'agent-a', online: true }, { peer: 'agent-b', online: false },
] });

assert.equal(externalTaskInfo(external, external.progress.meta).link, '/plugins/level-design/web/index.html');
assert.equal(externalTaskInfo({ external_owner: '../bad' }).link, '');
assert.equal(externalTaskInfo(regular), null);

F.expanded.add(external.task_id);
let html = renderRunning(project);
const row = html.match(/<article class="task[^>]*data-key="20261008-000000-ab">[^]*?<\/article>/)?.[0];
assert.ok(row);
assert.match(row, /Level Design 插件/);
assert.match(row, /Team：关卡设计/);
assert.match(row, /等待领取/);
assert.doesNotMatch(row, /class="task[^\"]*terminal/);
assert.equal(runningMeta(project).stalled, 0);
F.filters.runningStuck = true;
assert.doesNotMatch(renderRunning(project), /data-key="20261008-000000-ab"/);
F.filters.runningStuck = false;
assert.match(row, /href="\/plugins\/level-design\/web\/index.html"/);
for (const action of ['merge-now', 'nudge-merge', 'open-redispatch', 'requeue-agent', 'cancel-task', 'supersede']) {
  assert.doesNotMatch(row, new RegExp(`data-action="${action}"`));
}
assert.match(html, /data-action="merge-now"/);

external.progress.meta.race_winner = 'agent-a';
external.progress.meta.peers = ['agent-a'];
external.progress.states = { 'agent-a': 'working' };
html = renderRunning(project);
assert.match(html, /agent-a 抢得/);
assert.equal(runningMeta(project).stalled, 0);
external.progress.meta.peers = ['agent-a', 'agent-b'];
external.progress.expected = ['agent-a', 'agent-b'];
external.progress.quorum = 2;
external.progress.answered = ['agent-a'];
external.progress.states = { 'agent-a': 'answered', 'agent-b': 'queued' };
html = renderRunning(project);
assert.match(html, /已有 1 份回答 · 等待其余 Agent/);
assert.match(html, /部分回答已回传/);
external.progress.states['agent-b'] = 'expired';
html = renderRunning(project);
assert.match(html, /已有 1 份回答 · 其余名额需处理/);
assert.match(html, /部分回传 · 待处理/);
assert.equal(runningMeta(project).stalled, 1);
assert.match(html.match(/<article class="task[^>]*data-key="20261008-000000-ab">/)?.[0] || '', /terminal/);
F.filters.runningStuck = true;
assert.match(renderRunning(project), /data-key="20261008-000000-ab"/);
F.filters.runningStuck = false;
external.progress.answered = [];
external.progress.states = { 'agent-a': 'expired', 'agent-b': 'queued' };
html = renderRunning(project);
assert.match(html, /Agent 已过期 · 由来源处理/);
external.progress.states = { 'agent-a': 'stalled', 'agent-b': 'queued' };
html = renderRunning(project);
assert.match(html, /Agent 已领取未回 · 由来源处理/);
assert.equal(runningMeta(project).stalled, 1);
external.progress.meta.peers = ['agent-a'];
external.progress.expected = ['agent-a'];
external.progress.quorum = 1;
external.progress.answered = ['agent-a'];
external.progress.states = { 'agent-a': 'answered' };
external.progress.complete = true;
html = renderRunning(project);
assert.match(html, /成果已回传 · 由来源审阅/);
assert.equal(runningMeta(project).stalled, 0);
assert.doesNotMatch(html.match(/<article class="task[^>]*data-key="20261008-000000-ab">[^]*?<\/article>/)?.[0] || '', /可合并/);

assert.match(drawer, /externalTaskInfo\(d, meta\)/);
assert.match(drawer, /external\.link \? `<a class="link"/);
assert.match(drawer, /external \? \(external\.link/);
assert.match(main, /!d\.external_owner && !d\.progress\?\.meta\?\.external_owner/);

console.log('plugin team queue UI fixture PASS');
