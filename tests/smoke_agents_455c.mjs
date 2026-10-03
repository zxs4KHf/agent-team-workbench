import { readFileSync } from 'node:fs';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」
// Agent 阵列与自定义（cd14 / 任务 455c）：真正 import model.js 跑纯函数，不是 grep 字符串。
//   node tests/smoke_agents_455c.mjs
// model.js 在模块顶层读 location / localStorage / WB_IMAGE，这里先补最小桩再动态 import。
globalThis.location = { search: '' };
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
globalThis.WB_IMAGE = { ACCEPT: '', looksLikeImage: () => false };
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
globalThis.document = { documentElement: { dataset: {}, style: { setProperty() {} }, classList: { add() {}, remove() {}, toggle() {} } }, addEventListener() {}, querySelector: () => null, querySelectorAll: () => [] };

let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
const eq = (a, b, m) => ok(JSON.stringify(a) === JSON.stringify(b), `${m}（实际 ${JSON.stringify(a)}）`);

const M = await import('../web/v2/js/model.js');

// 一个假的 state：4 个 Agent —— 在线、在线但 relay、在线但本项目 paused、离线
const peers = [
  { peer: 'controller', online: true, status: 'idle' },
  { peer: 'alpha1', model: 'claude-opus-5', online: true, status: 'idle' },
  { peer: 'relay1', model: 'gpt-5', online: true, status: 'relay' },
  { peer: 'paused1', model: 'gemini-3', online: true, status: 'idle' },
  { peer: 'off1', model: 'grok-4', online: false, status: 'offline' },
];
const proj = {
  slug: 'demo', name: '演示', peers: ['alpha1', 'off1'], teams: [{ name: '前端组', peers: ['alpha1', 'paused1'] }],
  agent_meta: { paused1: { paused_in_project: true } }, decisions: [], default_team: '',
};
M.store.set({ peers, projects: [proj], project: 'demo', connected: true });
const P = () => M.project();

// 1. 在线 ≠ 可派发
eq(M.onlineAgents().map(a => a.peer), ['alpha1', 'relay1', 'paused1'], '在线总数含 relay 与 paused，且不再看 enabled');
eq(M.dispatchableAgents(P()).map(a => a.peer), ['alpha1'], '可派发排除 relay / paused / 离线');
eq(M.undispatchableReason('relay1', P()), '代理 Leader 中', 'relay 的原因');
eq(M.undispatchableReason('paused1', P()), '本项目已暂停', '项目级 paused_in_project 的原因（与全局暂停区分）');
eq(M.undispatchableReason('off1', P()), '离线', '离线的原因');
eq(M.undispatchableReason('alpha1', P()), '', '可派发者没有原因');
// 恢复 paused 后重新进入候选
proj.agent_meta.paused1.paused_in_project = false;
eq(M.dispatchableAgents(P()).map(a => a.peer), ['alpha1', 'paused1'], '取消暂停后重新可派发');
proj.agent_meta.paused1.paused_in_project = true;

// 2. 默认团队
eq(M.effectiveTeams(P())[0].name, M.DYNAMIC_TEAM, '动态「全员在线」永远第一项');
eq(M.defaultTeamName(P()), M.DYNAMIC_TEAM, 'default_team 为空时缺省动态全员在线');
eq(M.defaultComposerPeers(P()), ['alpha1'], '缺省默认 = 当前可派发者（不是项目静态 peers）');
proj.default_team = '前端组';
eq(M.defaultTeamName(P()), '前端组', '固定团队作为项目默认');
eq(M.defaultComposerPeers(P()), ['alpha1', 'paused1'], '固定团队按登记名单展开（不可派发者也在名单里，由界面划掉）');
proj.default_team = '已删除的组';
eq(M.defaultTeamName(P()), M.DYNAMIC_TEAM, '默认团队被删/改名后回退动态全员在线');
eq(M.defaultComposerPeers(P()), ['alpha1'], '回退后按可派发展开');
proj.default_team = '';
eq(M.teamPeersOf(M.DYNAMIC_TEAM, P()), ['alpha1'], '动态团队展开 = 可派发者');
eq(M.teamPeersOf('前端组', P()), ['alpha1', 'paused1'], '固定团队展开 = 登记名单');

// 3. 选择结果的三个数
eq(M.pickStats(['alpha1', 'paused1', 'off1'], P()), { selected: 3, dispatchable: 1, blocked: 2 }, '已选 / 可派发 / 不可派发');
eq(M.pickStats(['alpha1', 'alpha1'], P()), { selected: 1, dispatchable: 1, blocked: 0 }, '重复勾选只算一个');
eq(M.pickStats([], P()), { selected: 0, dispatchable: 0, blocked: 0 }, '空选择');

// 4. 成本提示：没有每 Agent 历史耗时就如实说没有，不编造
const est = M.dispatchEstimate(['alpha1', 'paused1'], P());
eq([est.copies, est.slowestMin], [1, null], '份数只算可派发者；没有数据时最慢时间为 null');
ok(est.text.includes('预计 1 份') && est.text.includes('最慢时间暂无数据'), `文案如实（实际 ${est.text}）`);
// answer_minutes 全仓无写入者，所以不为它预写取值逻辑：即便 state 里塞了这个字段也一律「暂无数据」（e4e0 非阻塞 4）
proj.decisions = [{ task_id: 't1', status: 'adopted', progress: { meta: { answer_minutes: { alpha1: 3, paused1: 9 } } } }];
proj.agent_meta.paused1.paused_in_project = false;
const est2 = M.dispatchEstimate(['alpha1', 'paused1'], P());
eq([est2.copies, est2.slowestMin], [2, null], '份数照算，时间仍为 null——不给假想字段写取值逻辑');
ok(est2.text.includes('最慢时间暂无数据'), `文案始终如实（实际 ${est2.text}）`);
ok(M.agentLatencyMin === undefined, 'agentLatencyMin 已移除，不做跨项目取样');
eq(M.pickStatsText(['alpha1', 'paused1', 'off1'], P()), '已选 3 · 可派发 2 · 离线/不可派发 1', '统计文案由 model 唯一提供（此刻 paused1 已解除暂停）');
proj.agent_meta.paused1.paused_in_project = true; proj.decisions = [];

// 5. Composer 默认值跟着项目默认团队走
M.F.composer = null;
const f = M.ensureComposer(P());
eq([f.teamName, f.peers], [M.DYNAMIC_TEAM, ['alpha1']], 'ensureComposer 的 teamName 是默认团队名，不再写死「团队」');


// 6. app.js 接线（e4e0 必改 1/2）：这里是对源码的静态断言——本机没有 jsdom / linkedom / playwright，
// 无法在真实 DOM 里点团队按钮；真实点击由 Reviewer 或界面体检覆盖，本文件只保证「接线确实存在、旧口径确实没了」。
const appSrc = frontSrc('app.js');
for (const sym of ['dispatchableAgents', 'undispatchableReason', 'teamPeersOf', 'DYNAMIC_TEAM', 'pickStatsText'])
  ok(new RegExp(`import[\\s\\S]{0,600}\\b${sym}\\b[\\s\\S]{0,600}from '\\./model\\.js'`).test(appSrc), `app.js 从 model.js 引入 ${sym}`);
ok(!/'全员在线'/.test(appSrc), "app.js 不再出现字面量 '全员在线'（改用 DYNAMIC_TEAM）");
ok(!/已选 \$\{f\.peers\.length\} \/ 在线/.test(appSrc), 'syncComposer 不再把 hint 覆盖回「已选 N / 在线 M」');
ok(/hint\.textContent = pickStatsText\(f\.peers\)/.test(appSrc), 'hint 改用 model 的唯一真值 pickStatsText');
ok((appSrc.match(/f\.peers = teamPeersOf\(f\.teamName\)/g) || []).length === 2, 'dispatch-team 与 composer-team 两个入口都走 teamPeersOf');
ok(/const want = teamPeersOf\(f\.teamName\)/.test(appSrc), '输入后的对账与 teamPeersOf 同口径（敲字不再清 teamName）');
ok(!/team\.peers\.filter\(n => agentByName\(n\)\.online\)/.test(appSrc), 'app.js 里按 online 过滤团队成员的旧口径已全部移除');
// 7. 同口径自证：固定团队展开 = 登记名单（含不可派发者，由界面划掉）；动态组 = 当前可派发
eq(M.teamPeersOf(M.DYNAMIC_TEAM, P()), ['alpha1'], '动态组只取可派发');
eq(M.teamPeersOf('前端组', P()), ['alpha1', 'paused1'], '固定团队原样展开登记名单');


// 8. e51b 必改 1：f0ca 之后代理 Leader 的心跳是 status:'busy' + role:'relay'，旧夹具测不到
peers.push({ peer: 'relay2', model: 'm', online: true, status: 'busy', role: 'relay' });
M.store.set({ peers: [...peers] });
eq(M.undispatchableReason('relay2', P()), '代理 Leader 中', '新形态（status=busy + role=relay）也判为代理 Leader');
ok(!M.dispatchableAgents(P()).some(a => a.peer === 'relay2'), '新形态的代理 Leader 不进可派发');
ok(!M.teamPeersOf(M.DYNAMIC_TEAM, P()).includes('relay2'), '动态团队不展开新形态的代理 Leader');
ok(M.isRelay({ status: 'relay' }) && M.isRelay({ role: 'relay' }), '双写期两种形态都认');
ok(!M.isRelay({ status: 'busy' }), '普通忙碌不是代理 Leader');
peers.pop();
M.store.set({ peers: [...peers] });

// 9. e51b 必改 2：暂停两个来源都算，且各自有真实写入入口
M.store.set({ profiles: { alpha1: { paused: true } } });
eq(M.undispatchableReason('alpha1', P()), '已暂停接单', '全局 profile.paused 生效');
ok(!M.dispatchableAgents(P()).some(a => a.peer === 'alpha1'), '全局暂停者不进可派发');
M.store.set({ profiles: {} });
eq(M.undispatchableReason('paused1', P()), '本项目已暂停', '项目级 paused_in_project 生效且文案区分来源');

// 10. e51b 必改 4：提交前真过滤（源码断言，本机无浏览器）
ok(!/const send = f\.peers\.filter\(n => !undispatchableReason\(n\)\)/.test(appSrc), '提交时不再静默剔除任何人（e020）');
ok(/peers: \[\.\.\.f\.peers\]/.test(appSrc) && /peers: snap\.peers,/.test(appSrc), '确认后发送的是完整名单，含暂停者与离线者（按获准时快照发送，4837）');
ok(/if \(blocked\.length && !\(confirmSig && confirmSig === issued && confirmSig === sig\)\)/.test(appSrc) && /inlineConfirmSend\(btn, blocked\)/.test(appSrc), '存在不可派发者时先要求确认（44f1：凭据须刚签发且与此刻内容一致）');
ok(/data-action="confirm-send"/.test(appSrc) && /data-action="cancel-send"/.test(appSrc), '确认条有「仍要发送」与「取消」两个动作');
ok(/'confirm-send': t => \{[^}]*submitDispatch\(f, .*?, F\.sendConfirm\)/.test(appSrc) && /const issued = F\.sendConfirm; F\.sendConfirm = null;/.test(appSrc), '确认后带凭据重新提交；入口即作废凭据（44f1）');
ok(/'cancel-send': \(\) => \{[^}]*clearSendConfirm\(\);[^}]*toast\('已取消，没有发送'/.test(appSrc), '取消则不发');
ok(!/openDrawer\(/.test(appSrc.slice(appSrc.indexOf('function inlineConfirmSend'), appSrc.indexOf('function openDispatch'))), '确认是内联的，不开新抽屉');
ok(/发给 \$\{f\.peers\.length\} 人 · 预计 \$\{est\.copies\} 份回应/.test(appSrc), '按钮数字 = 实发人数，预计份数措辞分开');
// 离线者仍可被派发：锁住「任务落进 inbox 的 .json 等它回来领」这条路径（stale_with_queue）
ok(M.teamPeersOf('前端组', P()).length === 2, '固定团队展开不剔除任何人');
eq(M.pickStats(['alpha1', 'off1'], P()), { selected: 2, dispatchable: 1, blocked: 1 }, '离线者计入已选、只是标为不可派发');
eq(M.dispatchEstimate(['alpha1', 'off1'], P()).copies, 1, '预计份数只数可派发者（预期回应数，不是实发人数）');
ok(/paused_in_project: fd\.get\('paused_in_project'\) === 'on'/.test(appSrc), 'agent-meta 表单真的提交 paused_in_project');
ok(/name="default_team"/.test(appSrc) && /default_team: String\(fd\.get\('default_team'\)/.test(appSrc), 'default_team 有表单入口且被提交');

console.log(fails ? `\n${fails} 项失败` : '\n全部通过');
process.exit(fails ? 1 : 0);
