// 默认项目：没有指定或指定的项目不存在时，选最近有动静的项目，不再落在列表第一个（常是空项目）
import { readFileSync } from 'node:fs';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」
const src = readFileSync(new URL('../web/v2/js/model.js', import.meta.url), 'utf8');
let fails = 0; const ok = (c, m) => { console.log((c ? 'ok ' : 'FAIL ') + m); if (!c) fails++; };
const tk = src.match(/const tsKey = [^\n]*/), la = src.match(/const lastActivity = [^\n]*/), dp = src.match(/export function defaultProject[^\n]*/);
ok(!!la && !!dp, '从 model.js 抽到 lastActivity / defaultProject');
const defaultProject = new Function(tk[0] + '\n' + la[0] + '\n' + dp[0].replace('export ', '') + '\nreturn defaultProject;')();
const empty = { slug: 'aios', decisions: [] };
const busy = { slug: '协作工作台', decisions: [{ updated: '2026-09-28T10:00:00' }, { created: '2026-09-20T00:00:00' }] };
const old = { slug: 'old', decisions: [{ updated: '2026-09-01T00:00:00' }] };
ok(defaultProject([empty, old, busy]).slug === '协作工作台', '选最近有更新的项目');
ok(defaultProject([empty]).slug === 'aios', '只有空项目时仍返回它');
ok(defaultProject([]) === null, '没有项目返回 null');
const spaced = { slug: 'spaced', decisions: [{ updated: '2026-09-28 11:00:00.123456' }] };
ok(defaultProject([busy, spaced]).slug === 'spaced', '空格分隔 + 微秒的时间也能正确比较');
ok(defaultProject([empty, { slug: 'b', decisions: [] }]).slug === 'aios', '都没有动静时保持原顺序第一个');
const app = frontSrc('app.js');
ok(!app.includes('!s.projects.length || !data.projects.some'), '首屏不再无条件冲掉 ?p= 与上次选择');
process.exit(fails ? 1 : 0);
