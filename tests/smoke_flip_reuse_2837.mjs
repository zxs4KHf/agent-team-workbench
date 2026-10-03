// 2837 R2 回归：idiomorph「就地复用」场景下的 FLIP 幽灵快照。
// 没有 jsdom / linkedom / playwright（node -e require.resolve 四者均 no），无法在真实 DOM 跑 idiomorph；
// 这里用精确等价的复用 fixture：与 idiomorph 对「无 id、同 tag」节点的 softMatch 行为一致——
// 新列表第 i 项复用旧列表第 i 个同 tag 的 DOM 对象，就地改写其属性与内容，多余旧节点 remove。
// 删除中间项 b：b 的 DOM 对象被改写成 c，旧 c 对象被移除 —— 旧版 flipList 拿旧引用 cloneNode 会克隆出 c。
// 跑法：node tests/smoke_flip_reuse_2837.mjs（仓库根目录）
import { readFileSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」

let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
const ROW_H = 40, GAP = 8;
class El {
  constructor(tag = 'div', id = '', text = '', tone = '') {
    this.tagName = tag.toUpperCase(); this._attrs = {}; this.dataset = {};
    if (id) this.setAttribute('data-id', id);
    this.textContent = text; this.style = {}; this.children = []; this.parentNode = null;
    this._cls = new Set(tone ? ['focus-item', tone] : ['focus-item']); this._listeners = {};
    this.classList = { add: (...c) => c.forEach(x => this._cls.add(x)), remove: (...c) => c.forEach(x => this._cls.delete(x)), contains: c => this._cls.has(c) };
  }
  get isConnected() { return this._connected === true || (this.parentNode ? this.parentNode.isConnected : false); }
  get tone() { return ['violet', 'warn', 'bad', 'info'].find(t => this._cls.has(t)) || ''; }
  appendChild(n) { n.parentNode = this; this.children.push(n); return n; }
  remove() { if (!this.parentNode) return; const i = this.parentNode.children.indexOf(this); if (i >= 0) this.parentNode.children.splice(i, 1); this.parentNode = null; }
  setAttribute(k, v) { this._attrs[k] = String(v); if (k === 'data-id') this.dataset.id = String(v); }
  getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; }
  hasAttribute(k) { return k in this._attrs; }
  removeAttribute(k) { delete this._attrs[k]; if (k === 'data-id') delete this.dataset.id; }
  addEventListener(t, fn) { (this._listeners[t] ||= []).push(fn); }
  removeEventListener(t, fn) { this._listeners[t] = (this._listeners[t] || []).filter(f => f !== fn); }
  fire(t, extra = {}) { (this._listeners[t] || []).slice().forEach(fn => fn({ target: this, ...extra })); }
  cloneNode(deep) { const c = new El(this.tagName, '', this.textContent, ''); c._cls = new Set(this._cls); c.style = { ...this.style }; Object.entries(this._attrs).forEach(([k, v]) => c.setAttribute(k, v)); if (deep) this.children.forEach(ch => c.appendChild(ch.cloneNode(true))); return c; }
  querySelectorAll(sel) { if (sel === '.leaving') return this.children.filter(c => c._cls.has('leaving')); return []; }
  getBoundingClientRect() {
    if (this._rect) return this._rect;
    const p = this.parentNode; if (!p) return { left: 0, top: 0, width: 300, height: ROW_H };
    const i = p.children.filter(c => !c._cls.has('leaving')).indexOf(this);
    return { left: 10, top: 100 + Math.max(i, 0) * (ROW_H + GAP), width: 300, height: this._h || ROW_H };
  }
}
const ITEMS = { a: ['正文 a', 'violet'], b: ['正文 b', 'warn'], c: ['正文 c', 'info'] };
function makeContainer(ids) { const c = new El('div'); c._cls = new Set(); c._connected = true; c.clientLeft = 1; c.clientTop = 2; c._rect = { left: 5, top: 50, width: 320, height: 500 }; ids.forEach(id => c.appendChild(new El('button', id, ...ITEMS[id]))); return c; }
// 与 idiomorph softMatch 等价的就地复用：第 i 个新项复用第 i 个旧同 tag 节点（改属性、改内容），多余旧节点移除
function idiomorphLikeMorph(container, newIds) {
  const real = container.children.filter(k => !k._cls.has('leaving'));
  newIds.forEach((id, i) => {
    const node = real[i];
    if (node && node.tagName === 'BUTTON') { node.setAttribute('data-id', id); node.textContent = ITEMS[id][0]; ['violet', 'warn', 'bad', 'info'].forEach(t => node._cls.delete(t)); node._cls.add(ITEMS[id][1]); }
    else container.appendChild(new El('button', id, ...ITEMS[id]));
  });
  real.slice(newIds.length).forEach(n => n.remove());
}
// 全局垫片
const rafQueue = []; globalThis.requestAnimationFrame = fn => { rafQueue.push(fn); return rafQueue.length; };
const flushRaf = () => { while (rafQueue.length) rafQueue.shift()(0); };
globalThis.matchMedia = () => ({ matches: false });
const timers = []; globalThis.setTimeout = fn => { timers.push(fn); return timers.length; }; globalThis.clearTimeout = () => {};
const extract = src => { const m = src.match(/function flipList\(container, mutate\) \{[\s\S]*?\n\}/); return m ? new Function(m[0] + '; return flipList;')() : null; };

// ---- 场景 A：旧版（5fe284f）在等价复用 fixture 上必须复现错误（ghost 克隆到 c）----
let oldSrc = '';
try { oldSrc = execSync('git show 5fe284f9e7:web/v2/js/app.js', { encoding: 'utf8' }); } catch { console.log('SKIP  无法 git show 5fe284f9e7（非 git 环境），跳过旧版复现'); }
if (oldSrc) {
  const flipOld = extract(oldSrc); ok(!!flipOld, '旧版 5fe284f 抽到 flipList');
  const c = makeContainer(['a', 'b', 'c']); const bObj = c.children[1];
  flipOld(c, () => idiomorphLikeMorph(c, ['a', 'c']));
  const ghosts = c.querySelectorAll('.leaving');
  ok(bObj.dataset.id === 'c' && bObj.textContent === '正文 c', '前置：b 的 DOM 对象已被就地改写成 c（复用发生）');
  ok(ghosts.length === 1 && ghosts[0].textContent === '正文 c' && ghosts[0].tone === 'info', `旧版复现阻断：ghost 正文/色条是 c 而非 b（实际 "${ghosts[0]?.textContent}" / ${ghosts[0]?.tone}）`);
}

// ---- 场景 B：新版在同一 fixture 上 ghost 必须是 b，真实列表与新数据一致 ----
const src = frontSrc('app.js'); const flipList = extract(src); ok(!!flipList, '当前 app.js 抽到 flipList');
{
  const c = makeContainer(['a', 'b', 'c']); const bObj = c.children[1];
  flipList(c, () => idiomorphLikeMorph(c, ['a', 'c']));
  const ghosts = c.querySelectorAll('.leaving'); const g = ghosts[0];
  const real = c.children.filter(k => !k._cls.has('leaving'));
  ok(bObj.dataset.id === 'c', '前置：复用仍发生（b 对象现为 c）');
  ok(ghosts.length === 1 && g.textContent === '正文 b' && g.tone === 'warn', `新版：ghost 正文/色条仍是 b（实际 "${g?.textContent}" / ${g?.tone}）`);
  ok(real.length === 2 && real.map(k => k.dataset.id).join(',') === 'a,c', `真实列表项数/ID 与新数据一致（实际 ${real.map(k => k.dataset.id).join(',')}）`);
  ok(!g.hasAttribute('data-id') && !g.dataset.id && !g.hasAttribute('data-action') && !g.hasAttribute('id'), 'ghost 不带业务 data-id / data-action / id');
  ok(g.getAttribute('aria-hidden') === 'true' && g.hasAttribute('inert') && g.getAttribute('tabindex') === '-1' && g.style.pointerEvents === 'none', 'ghost aria-hidden / inert / tabindex=-1 / pointer-events:none');
  ok(g.style.position === 'absolute' && g.style.top === '96px' && g.style.left === '4px' && g.style.width === '300px', `ghost 坐标仍减 clientLeft/Top（top=${g.style.top} left=${g.style.left}）`);
  ok(g.style.maxHeight === '40px', `ghost max-height 取自真实高度（实际 ${g.style.maxHeight}）`);
  flushRaf(); const cObj = real[1];
  ok(cObj.style.transform === '' && cObj.style.transition === 'transform .22s cubic-bezier(.2,.8,.2,1)', '保留项 c 回弹：rAF 后 transform 清空、临时 transition 生效');
  cObj.fire('transitionend', { propertyName: 'transform' });
  ok(cObj.style.transition === '' && cObj._flipRestore === null, 'transitionend 后临时 transition 已恢复为行内原值（空 → 回到样式表 border-color 过渡）');
}
// ---- 场景 C：长卡片不截断 —— 正常 .focus-item 规则无 max-height；高卡片 ghost 用真实高度 ----
{
  const css = readFileSync('web/v2/styles/components.css', 'utf8');
  const rule = css.match(/\.focus-item\{[^}]*\}/)[0];
  ok(!/max-height/.test(rule), '.focus-item 规则不再含 max-height:200px');
  ok(/\.focus-item\.leaving\{animation:leave/.test(css) && /@keyframes leave\{to\{[^}]*max-height:0/.test(css), 'leave 关键帧保留（max-height → 0）');
  const c = makeContainer(['a', 'b', 'c']); c.children[1]._h = 420;
  flipList(c, () => idiomorphLikeMorph(c, ['a', 'c']));
  ok(c.querySelectorAll('.leaving')[0].style.maxHeight === '420px', '420px 高卡片离场：ghost max-height=420px，不被 200px 截断');
}
// ---- 场景 D：动画连续触发 —— 第二轮开头清掉残留 ghost；被打断的位移动画先恢复再重设，不叠加 ----
{
  const c = makeContainer(['a', 'b', 'c']);
  flipList(c, () => idiomorphLikeMorph(c, ['a', 'c'])); flushRaf();
  ok(c.querySelectorAll('.leaving').length === 1, '第一轮：1 个残影');
  const cObj = c.children.filter(k => !k._cls.has('leaving'))[1]; const restore1 = cObj._flipRestore;
  ok(typeof restore1 === 'function' && cObj.style.transition !== '', '第一轮：c 处于位移动画中（未触发 transitionend）');
  // 第二轮：把 c 挪到最前（a,c → c,a），残影仍在
  flipList(c, () => { const [A, C] = c.children.filter(k => !k._cls.has('leaving')); C.remove(); A.remove(); c.children.unshift(C); C.parentNode = c; c.children.splice(1, 0, A); A.parentNode = c; }); flushRaf();
  ok(c.querySelectorAll('.leaving').length === 0, '第二轮开头：残留 .leaving 已清理，未产生新残影');
  ok(cObj._flipRestore && cObj._flipRestore !== restore1 && (cObj._listeners.transitionend || []).length === 1, '被打断的位移动画：旧 restore 已执行、监听器只剩 1 个（不叠加）');
  timers.splice(0).forEach(fn => fn());                              // 400ms 兜底：即使 transitionend 不来也恢复
  ok(cObj.style.transition === '' && cObj.style.transform === '' && cObj._flipRestore === null, '兜底定时器后：transition / transform 全部恢复，无永久覆盖');
}
console.log(fails ? `\n${fails} FAILED` : '\nALL PASS'); process.exit(fails ? 1 : 0);
