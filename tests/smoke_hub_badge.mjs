// hub_badge.js 在 vm 里真跑：/api/ext/summary 的成功与各种失败路径（fable2 efe4：fetch 遇 4xx/5xx 不 reject，400 {error} 曾被当成功）
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」
const repo = join(dirname(fileURLToPath(import.meta.url)), '..');
const code = readFileSync(join(repo, 'web/v2/js/hub_badge.js'), 'utf8');
let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
const tick = () => new Promise(r => setTimeout(r, 20));

async function run(responses) {
  const badge = { hidden: true, textContent: '' }, link = { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
  const events = [];
  let i = 0, intervalFn = null, visFn = null;
  const ctx = {
    document: { createElement: () => ({}), head: { appendChild() {} }, hidden: false, getElementById: id => (id === 'hubBadge' ? badge : link), addEventListener: (_, fn) => { visFn = fn; } },
    window: { dispatchEvent: e => events.push(e.detail) },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    fetch: () => { const r = responses[Math.min(i++, responses.length - 1)]; return Promise.resolve({ ok: r.status < 400, status: r.status, json: () => (r.text ? Promise.reject(new SyntaxError('not json')) : Promise.resolve(r.body)) }); },
    setInterval: fn => { intervalFn = fn; return 1; }
  };
  vm.runInNewContext(code, ctx);
  await tick();
  const steps = [{ badge: { ...badge }, aria: link.attrs['aria-label'], detail: events.at(-1) }];
  for (let k = 1; k < responses.length; k++) { intervalFn(); await tick(); steps.push({ badge: { ...badge }, aria: link.attrs['aria-label'], detail: events.at(-1) }); }
  return steps;
}

const good = { intake: { pending: 2 }, feedback: { open: 1 } };
let s = await run([{ status: 200, body: good }]);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
ok(same(s[0].detail, good) && s[0].badge.textContent === '3' && !s[0].badge.hidden, '200 正常：广播摘要，角标 3');
s = await run([{ status: 200, body: good }, { status: 400, body: { error: '统计失败' } }]);
ok(s[1].detail === null, '400 {error}：广播 null（不能当成功，否则概览显示假的 0）');
ok(s[1].badge.hidden && s[1].aria === '扩展与来源', '400 后清掉旧角标与 aria-label 里的数量');
s = await run([{ status: 200, body: good }, { status: 404, text: true }]);
ok(s[1].detail === null && s[1].badge.hidden, '404 纯文本（扩展关闭）：广播 null，旧角标清掉');
s = await run([{ status: 200, body: { error: 'x' } }]);
ok(s[0].detail === null, '200 但带 error 字段：按失败处理');
s = await run([{ status: 200, body: { feedback: { open: 4 } } }]);
ok(s[0].detail && s[0].detail.feedback && !s[0].detail.intake && s[0].badge.textContent === '4', '只有反馈扩展在：照常广播（缺的那项由概览单独隐藏）');
// 计数校验（非负整数才算数）
s = await run([{ status: 200, body: { intake: { pending: '3' }, feedback: { open: 1 } } }]);
ok(same(s[0].detail, { intake: null, feedback: { open: 1 } }) && s[0].badge.textContent === '1', '字符串 "3"：收件箱按缺席处理，角标 1（不是拼接出的 "31"）');
s = await run([{ status: 200, body: { intake: { pending: -2 }, feedback: { open: 5 } } }]);
ok(s[0].detail.intake === null && s[0].badge.textContent === '5', '负数：该项按缺席处理');
s = await run([{ status: 200, body: { intake: { pending: 1.5 }, feedback: { open: NaN } } }]);
ok(s[0].detail === null && s[0].badge.hidden, '小数 + NaN（两项都不合格）：按失败处理，广播 null');
s = await run([{ status: 200, body: { intake: {}, feedback: { open: 0 } } }]);
ok(same(s[0].detail, { intake: { pending: 0 }, feedback: { open: 0 } }) && s[0].badge.hidden, '字段缺省记 0；总数 0 时隐藏角标');
s = await run([{ status: 200, body: { intake: 'x', feedback: { open: 2 } } }]);
ok(s[0].detail.intake === null && s[0].badge.textContent === '2', 'intake 不是对象：按缺席处理');
s = await run([{ status: 200, body: { intake: { pending: null } } }]);
ok(s[0].detail === null && s[0].badge.hidden, '显式 pending:null（另一项缺席）：整条失败，不显示「收件箱 0」');
s = await run([{ status: 200, body: { intake: [] } }]);
ok(s[0].detail === null && s[0].badge.hidden, 'intake 是数组（另一项缺席）：整条失败');
s = await run([{ status: 200, body: { intake: { pending: null }, feedback: { open: 2 } } }]);
ok(s[0].detail.intake === null && s[0].badge.textContent === '2', '显式 null + 合格反馈：只隐藏收件箱');
s = await run([{ status: 200, body: { intake: { pending: 2 ** 60 }, feedback: [] } }]);
ok(s[0].detail === null, '超出安全整数 + 数组：整条失败');
s = await run([{ status: 200, body: { hello: 1 } }]);
ok(s[0].detail === null, '两项都没有：按失败处理');
// 概览按项降级：缺哪项隐藏哪项
const app = frontSrc('app.js'), views = frontSrc('views.js');
ok(/inbox: d\.intake \? /.test(app) && /feedback: d\.feedback \? /.test(app), 'app.js：收件箱 / 反馈各自映射，缺失为 null');
ok(/ext && ext\.inbox != null && \{/.test(views) && /ext && ext\.feedback != null && \{/.test(views), 'views.js：每项单独判断是否显示');
console.log(`\n${fails ? fails + ' 项失败' : '全部通过'}`);
process.exit(fails ? 1 : 0);
