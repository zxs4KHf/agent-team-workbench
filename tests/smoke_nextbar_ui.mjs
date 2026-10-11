// 概览行动条（ROADMAP「首页下一步卡片」）：待拍板 / 收件箱 / 未处理反馈 / 卡住的施工，一键直达；扩展层关闭时两项隐藏
import { readFileSync } from 'node:fs';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const repo = join(dirname(fileURLToPath(import.meta.url)), '..');
const rd = p => readFileSync(join(repo, p), 'utf8');
let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
const views = frontSrc('views.js'), app = frontSrc('app.js'), badge = rd('web/v2/js/hub_badge.js'), model = rd('web/v2/js/model.js'), css = rd('web/v2/styles/components.css');
const ov = views.slice(views.indexOf('export function renderOverview'), views.indexOf('export function renderRunning'));
ok(/class="nextbar"[^>]*aria-label=/.test(ov) && ov.indexOf('${nextbar}') > -1 && ov.indexOf('${nextbar}') < ov.indexOf('ov-main'), '行动条在概览主面板之上，带 aria-label（2026-10-02 起去掉了指标数字行）');
ok(/ext && ext\.inbox != null && \{[^}]*'收件箱'[^}]*href: '\/hub#inbox'/.test(ov) && /ext && ext\.feedback != null && \{[^}]*'未处理反馈'[^}]*href: '\/hub#feedback'/.test(ov), '收件箱 / 反馈只在扩展摘要可用时出现，直达 hub 对应分页');
ok(/userWait\.length === 1 \? `data-action="open-decision"/.test(ov) && ov.includes('data-action="goto-status" data-view="decisions" data-status="merged"'), '待拍板：1 项直接打开，多项跳决策页并筛「待采纳」');
ok(/const asks = pendingDecisionItems\(p\)\.filter\(x => !x\.it\.decided && !x\.it\.answer\)/.test(ov) && /n: toDecide, label: '待拍板'/.test(ov), '待拍板 = 等你采纳的合并稿 + 等你回答的选择题（2026-10-10：只数前者时顶部显示「0 待拍板 · 都处理完了」，下面却有题等你）');
ok(/asks\.length \? 'data-action="ov-tab" data-tab="decide"/.test(ov), '有选择题时点「待拍板」直接切到「等你拍板」面板');
ok(/stallInfo\(d\)\.terminal/.test(ov) && ov.includes("'卡住的施工'") && ov.includes('data-view="running"'), '卡住的施工沿用 stallInfo 判定，直达进行中');
// 松耦合：核心不直接请求扩展接口，只听 hub_badge 的广播（ARCHITECTURE 原则 1）；成功与失败都广播
ok(!/(fetch|api|textApi)\(\s*['"`]\/api\/ext\/summary/.test(app + views + model), '核心代码不直接请求 /api/ext/summary（只看调用，注释里提到不算）');
const then = badge.slice(badge.indexOf('.then(function (d)'), badge.indexOf('.catch('));
ok(/dispatchEvent\(new CustomEvent\('wb:ext-summary', \{ detail: d \}\)\)/.test(then), 'hub_badge 成功时广播摘要（失败路径的行为测试见 smoke_hub_badge.mjs）');
ok(/addEventListener\('wb:ext-summary'/.test(app) && /store\.on\('ext'/.test(app) && /\bext: null\b/.test(model), 'app 监听广播写入 store.ext（初值 null），ext 变化时重绘概览');
ok(/'goto-status'\s*:/.test(app) && /decisionStatus = t\.dataset\.status/.test(app), 'goto-status action 设置决策筛选后切视图');
// 配色只用守卫过的 x-on-x-soft 组合（smoke_contrast_tokens 保证 ≥4.5）
for (const t of ['violet', 'info', 'warn', 'danger']) ok(new RegExp(`\\.nb-item\\.${t}\\{[^}]*background:var\\(--${t}-soft\\)[^}]*color:var\\(--${t}\\)`).test(css), `.nb-item.${t} 用 --${t} on --${t}-soft`);
ok(/\.nb-item\.zero\{[^}]*color:var\(--muted\)/.test(css) && !/\.nb-item[^{]*\{[^}]*opacity/.test(css), '0 项用 muted 色而非半透明（ADR 0007）');
console.log(`\n${fails ? fails + ' 项失败' : '全部通过'}`);
process.exit(fails ? 1 : 0);
