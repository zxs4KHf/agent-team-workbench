// 反馈 fb-2154160fae 前端冒烟：概览要能看懂「几个任务在跑 / 几个人在干活 / 每一单是谁在做」，切回标签页要立即刷新。
// 跑法：node tests/smoke_overview_activity_fb2154.mjs（仓库根目录）
// 不起服务、不碰运行数据，只读 web/v2 源码（与 smoke_design_ratchet.mjs 同套路：ADR 0015 拆模块后用 frontSrc）。
import { frontSrc } from './_frontsrc.mjs';

let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };

const ov = frontSrc('views.js');    // 概览本体在 page-overview.js
const sync = frontSrc('app.js');    // 数据同步在 app-sync.js

// 1) 每一单「谁在做」：进行中列表要渲染每个 peer 的实时状态
ok(/export function peerStateChips\(states\)/.test(ov), '导出 peerStateChips(states)');
ok(/\$\{peerStateChips\(pr\.states\)\}/.test(ov), '进行中列表渲染 peerStateChips(pr.states)');
ok(/PEER_STATE\[st\]/.test(ov), 'chip 文案取自 PEER_STATE');
ok(/answered: 'ok'/.test(ov) && /working: 'violet'/.test(ov), '状态→色调映射存在');

// 2) 概览要给数量：进行中任务数 + 正在作答人数
ok(/'进行中任务'/.test(ov), '健康条有「进行中任务」');
ok(/'正在作答'/.test(ov), '健康条有「正在作答」');
ok(/runningDecisions\(project\(\)\)/.test(ov), '按当前项目统计进行中任务');
ok(/v === 'working' \|\| v === 'stalled'/.test(ov), '「在干活」口径含 working 与 stalled');

// 3) 切回标签页立即刷新（原先轮询在 document.hidden 时整体跳过，回来要等一个周期）
ok(/visibilitychange/.test(sync), 'app-sync 注册 visibilitychange');
ok(/addEventListener\('visibilitychange'[\s\S]{0,80}refresh\(\)/.test(sync), '回到前台立刻 refresh()');

// 4) 不许为这次改动新增内联样式（design ratchet 面）
const i = ov.indexOf('export function peerStateChips');
ok(i >= 0 && !/style=/.test(ov.slice(i, i + 600)), 'peerStateChips 不写内联 style');

console.log(fails ? `\n${fails} 项失败` : '\nALL PASS');
process.exit(fails ? 1 : 0);
