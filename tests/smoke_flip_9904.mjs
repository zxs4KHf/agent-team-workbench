// 9904 FLIP 最小 DOM 桩：从 app.js 源码抽出 flipList 函数体，在纯 JS 桩上跑，不起浏览器、不 import 整个模块。
// 覆盖：① idiomorph 保留原节点  ② idiomorph 替换为新节点（同 data-id）  ③ 删除中间项
// 跑法：node tests/smoke_flip_9904.mjs（仓库根目录）
import { readFileSync } from 'node:fs';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」

let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };

// ---- DOM 桩 ----
const ROW_H = 40, GAP = 8;
class El {
  constructor(tag = 'div', id = '') {
    this.tagName = tag.toUpperCase(); this.dataset = id ? { id } : {};
    this.style = {}; this.children = []; this.parentNode = null;
    this._cls = new Set(); this._attrs = {}; this._listeners = {};
    this.classList = {
      add: (...c) => c.forEach(x => this._cls.add(x)),
      remove: (...c) => c.forEach(x => this._cls.delete(x)),
      contains: c => this._cls.has(c),
    };
  }
  get isConnected() { return this._connected === true || (this.parentNode ? this.parentNode.isConnected : false); }
  appendChild(n) { n.parentNode = this; this.children.push(n); return n; }
  remove() { if (!this.parentNode) return; const i = this.parentNode.children.indexOf(this); if (i >= 0) this.parentNode.children.splice(i, 1); this.parentNode = null; }
  removeAttribute(k) { delete this._attrs[k]; }
  setAttribute(k, v) { this._attrs[k] = v; }
  addEventListener(t, fn) { (this._listeners[t] ||= []).push(fn); }
  cloneNode() { const c = new El(this.tagName, this.dataset.id); c._cls = new Set(this._cls); c.style = { ...this.style }; c._attrs = { ...this._attrs }; return c; }
  querySelectorAll(sel) {
    const cls = sel.replace('.', '');
    return this.children.filter(c => c._cls.has(cls));
  }
  // 位置由「在容器里的当前下标」决定：越靠后 top 越大
  getBoundingClientRect() {
    if (this._rect) return this._rect;
    const p = this.parentNode;
    if (!p) return { left: 0, top: 0, width: 300, height: ROW_H };
    const i = p.children.filter(c => !c._cls.has('leaving')).indexOf(this);
    return { left: 10, top: 100 + Math.max(i, 0) * (ROW_H + GAP), width: 300, height: ROW_H };
  }
}
function makeContainer(ids) {
  const c = new El('div', '');
  c._connected = true;
  c.clientLeft = 1; c.clientTop = 2;                      // 非零边框，用来验证坐标是「减」不是「加」
  c._rect = { left: 5, top: 50, width: 320, height: 500 };
  ids.forEach(id => c.appendChild(new El('button', id)));
  return c;
}
// ---- 全局垫片 ----
const rafQueue = [];
globalThis.requestAnimationFrame = fn => { rafQueue.push(fn); return rafQueue.length; };
const flushRaf = () => { while (rafQueue.length) rafQueue.shift()(0); };
globalThis.matchMedia = () => ({ matches: false });
globalThis.setTimeout = (fn) => 0;                         // 幽灵的 400ms 兜底不在本测范围内，屏蔽掉

// ---- 从 app.js 源码抽 flipList ----
const src = frontSrc('app.js');
const m = src.match(/function flipList\(container, mutate\) \{[\s\S]*?\n\}/);
ok(!!m, '从 app.js 抽到 flipList 源码');
if (!m) { console.log('\n1 FAILED'); process.exit(1); }
const flipList = new Function(m[0] + '; return flipList;')();

// ================= 场景 1：idiomorph 保留原节点，删除中间项 =================
{
  const c = makeContainer(['a', 'b', 'c']);
  const [A, B, C] = c.children;
  const topBefore = { a: A.getBoundingClientRect().top, b: B.getBoundingClientRect().top, c: C.getBoundingClientRect().top };
  ok(topBefore.a < topBefore.b && topBefore.b < topBefore.c, `桩位置递增 a=${topBefore.a} b=${topBefore.b} c=${topBefore.c}`);

  flipList(c, () => { B.remove(); });                      // mutate：原地删掉中间项，A/C 是同一批节点
  flushRaf();

  ok(C.style.transform === '', '场景1 · 第三项回弹结束后 transform 已清空');
  const ghosts = c.children.filter(x => x._cls.has('leaving'));
  ok(ghosts.length === 1 && ghosts[0].dataset.id === 'b', `场景1 · 被删的 b 生成了 1 个 ghost（实际 ${ghosts.length} 个）`);
  const g = ghosts[0];
  // a.top(=100+1*48=148) - c.top(=50) - clientTop(=2) = 96
  ok(g.style.top === '96px', `场景1 · ghost.top 减 clientTop：期望 96px，实际 ${g.style.top}`);
  ok(g.style.left === '4px', `场景1 · ghost.left 减 clientLeft：期望 4px（10-5-1），实际 ${g.style.left}`);
  ok(g.style.position === 'absolute' && g._attrs['data-action'] === undefined, '场景1 · ghost 绝对定位且已摘掉 data-action');
}

// ================= 场景 2：dy 非零（拦 transform 起始帧） =================
{
  const c = makeContainer(['a', 'b', 'c']);
  const [A, B, C] = c.children;
  const seen = [];
  const origRaf = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = fn => { seen.push([C.style.transform, A.style.transform]); return origRaf(fn); };
  flipList(c, () => { B.remove(); });
  globalThis.requestAnimationFrame = origRaf;
  // 第三项从 index2(top196) 移到 index1(top148)，dy = 196-148 = +48
  ok(seen.length === 1, `场景2 · 只有第三项需要回弹（rAF 次数=${seen.length}，第一项 dy=0 应跳过）`);
  ok(seen[0][0] === 'translate(0px,48px)', `场景2 · 第三项 dy 非零：期望 translate(0px,48px)，实际 ${seen[0][0]}`);
  ok(seen[0][1] === undefined, '场景2 · 第一项位置未变，未被写 transform');
  flushRaf();
}

// ================= 场景 3：idiomorph 替换节点（同 data-id 的全新对象） =================
{
  const c = makeContainer(['a', 'b', 'c']);
  const seen = [];
  const origRaf = globalThis.requestAnimationFrame;
  flipList(c, () => {
    // 模拟 morph 把整段 innerHTML 重建：老节点全丢，新建 a、c（b 不再出现）
    const kids = [...c.children];
    kids.forEach(k => k.remove());
    ['a', 'c'].forEach(id => c.appendChild(new El('button', id)));
  });
  const newC = c.children.find(x => x.dataset.id === 'c' && !x._cls.has('leaving'));
  ok(!!newC, '场景3 · 替换后仍能按 data-id 找到 c');
  ok(newC.style.transform === 'translate(0px,48px)', `场景3 · 键控生效，新节点 c 拿到 dy=48（实际 ${newC.style.transform}）`);
  const ghosts = c.children.filter(x => x._cls.has('leaving'));
  ok(ghosts.length === 1 && ghosts[0].dataset.id === 'b', `场景3 · 节点被整体替换时仍只为 b 生成 ghost（实际 ${ghosts.length}）`);
  flushRaf();
  ok(newC.style.transform === '', '场景3 · rAF 后 transform 清空');
  globalThis.requestAnimationFrame = origRaf;
}

// ================= 场景 4：残留 .leaving 在下一轮被清掉，且不参与测量 =================
{
  const c = makeContainer(['a', 'b', 'c']);
  const stale = new El('button', 'zz'); stale.classList.add('leaving'); c.appendChild(stale);
  ok(c.children.length === 4, '场景4 · 前置：容器里有一个残留 .leaving');
  flipList(c, () => {});
  ok(!c.children.includes(stale), '场景4 · 残留 .leaving 已在 flipList 开头被移除');
  ok(c.children.filter(x => x._cls.has('leaving')).length === 0, '场景4 · 没有内容变化时不产生新 ghost');
  flushRaf();
}

// ================= 场景 5：容器不在 DOM 时直接 mutate，不测量 =================
{
  const c = makeContainer(['a']); c._connected = false;
  let called = 0;
  flipList(c, () => { called++; });
  ok(called === 1, '场景5 · 容器未挂载时 mutate 仍被调用一次');
  ok(c.children.filter(x => x._cls.has('leaving')).length === 0, '场景5 · 未挂载时不生成 ghost');
}

console.log(fails ? `\n${fails} FAILED` : '\nALL PASS');
process.exit(fails ? 1 : 0);
