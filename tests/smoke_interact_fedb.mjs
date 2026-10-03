// fedb 前端隔离测试：不起浏览器，用最小 DOM 桩（matches / closest / contains / dataset / querySelector / insertAdjacentHTML）驱动 interact.js 纯函数，
// 覆盖：点击子按钮不跳父卡、内部链接 / 输入框不穿透、选中文字不跳转、手势不同源放弃、IME / 输入中不触发快捷键、裸数字不切页而 Alt+数字切页、
// morph 守卫（data-keep / 聚焦输入）、合并稿附加段同键收回异键切换、data-keep 正文的路径 / 版本陈旧判定、实体深层链接解析与非法 ID、搜索防抖。
// 另加源码级守卫（模板 data-keep / time 订阅拆分 / 裸数字快捷键已删 / 宏动作不再 renderView）。跑法：node tests/smoke_interact_fedb.mjs
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { frontSrc } from './_frontsrc.mjs';  // ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」
const repo = process.cwd();
const I = await import(pathToFileURL(join(repo, 'web/v2/js/interact.js')).href);
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };

// ---- 最小 DOM 桩 ----
class El {
  constructor(tag, attrs = {}, children = []) { this.nodeType = 1; this.tag = tag.toLowerCase(); this.attrs = { ...attrs }; this.children = []; this.parent = null; this.isContentEditable = attrs.contenteditable === '' || attrs.contenteditable === 'true'; children.forEach(c => this.append(c)); }
  get dataset() { const o = {}; for (const [k, v] of Object.entries(this.attrs)) if (k.startsWith('data-')) o[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v; const self = this; return new Proxy(o, { set(t, k, v) { self.attrs['data-' + String(k).replace(/[A-Z]/g, c => '-' + c.toLowerCase())] = String(v); return true; }, deleteProperty(t, k) { delete self.attrs['data-' + String(k).replace(/[A-Z]/g, c => '-' + c.toLowerCase())]; return true; } }); }
  get classList() { const self = this; return { contains: c => (self.attrs.class || '').split(/\s+/).includes(c) }; }
  append(c) { c.parent = this; this.children.push(c); return c; }
  remove() { if (this.parent) { this.parent.children = this.parent.children.filter(x => x !== this); this.parent = null; } }
  contains(n) { while (n) { if (n === this) return true; n = n.parent; } return false; }
  matches(sel) { return sel.split(',').some(s => this._matchOne(s.trim())); }
  _matchOne(s) {
    const m = s.match(/^([a-z0-9]+)?((?:[.#\[][^.#\[]*)*)$/i); if (!m) return false;
    if (m[1] && m[1].toLowerCase() !== this.tag) return false;
    const parts = (m[2] || '').match(/[.#\[][^.#\[]*/g) || [];
    return parts.every(p => {
      if (p[0] === '.') return (this.attrs.class || '').split(/\s+/).includes(p.slice(1));
      if (p[0] === '#') return this.attrs.id === p.slice(1);
      const a = p.slice(1, -1).match(/^([\w-]+)(?:=["']?([^"']*)["']?)?$/); if (!a) return false;
      return a[2] === undefined ? a[1] in this.attrs : this.attrs[a[1]] === a[2];
    });
  }
  closest(sel) { let n = this; while (n) { if (n.matches(sel)) return n; n = n.parent; } return null; }
  querySelector(sel) { for (const c of this.children) { if (c.matches(sel)) return c; const r = c.querySelector(sel); if (r) return r; } return null; }
  insertAdjacentHTML(_, html) { const kind = (html.match(/data-kind="([^"]+)"/) || [])[1] || ''; this.append(new El('div', { class: 'md-extra', 'data-kind': kind })); }
}
const el = (t, a, c) => new El(t, a, c);

// ---- 1. 委托解析 ----
const inner = el('button', { 'data-action': 'view', 'data-view': 'running' });
const txt = el('strong', {});
const link = el('a', { href: '#x' });
const input = el('input', { type: 'text' });
const card = el('button', { class: 'focus-item', 'data-action': 'open-decision', 'data-id': 'd1' }, [txt, inner, link, input]);
el('div', {}, [card]);
ok(I.resolveAction(inner) === inner, '点子按钮：命中子按钮而不是父卡');
ok(I.resolveAction(txt) === card, '点父卡内普通文字：命中父卡');
ok(I.resolveAction(link) === null, '点父卡内 <a href>：不穿透父卡');
ok(I.resolveAction(input) === null, '点父卡内 input：不穿透父卡');
ok(I.resolveAction(el('input', { type: 'checkbox', 'data-action': 'x' })) === null, 'checkbox 交给 change');
ok(I.resolveAction(txt, el('div', {})) === null && I.resolveAction(txt, inner) === card, '手势不同源（pointerdown 在卡外）：放弃；卡内子元素按下仍算同源');
ok(I.resolveAction(txt, txt) === card && I.resolveAction(inner, inner) === inner, '手势同源：正常命中');
ok(I.resolveAction(txt, null, txt) === null && I.resolveAction(txt, null, el('p', {})) === card, '父卡内拖选文字：不跳转；选区在别处不影响');
ok(I.selectionAnchor({ getSelection: () => ({ isCollapsed: true, toString: () => '' }) }) === null && I.selectionAnchor({ getSelection: () => ({ isCollapsed: false, toString: () => 'ab', anchorNode: { nodeType: 3, parentNode: txt } }) }) === txt, 'selectionAnchor：折叠选区 null，文本节点锚点回到父元素');
ok(I.resolveAction(el('span', {})) === null, '无 data-action 祖先：null');

// ---- 2. 输入目标 / 快捷键 ----
const ce = el('div', { contenteditable: 'true' }); const ceChild = el('span', {}); ce.append(ceChild);
ok(I.isTypingTarget(input) && I.isTypingTarget(el('textarea', {})) && I.isTypingTarget(el('select', {})) && I.isTypingTarget(ce) && I.isTypingTarget(ceChild) && !I.isTypingTarget(inner), 'isTypingTarget：input/textarea/select/contenteditable 及其内部为真，按钮为假');
const body = el('body', {});
ok(I.shortcutAllowed({ target: body, key: 'r' }) === true, '正文上按 r：允许');
ok(I.shortcutAllowed({ target: input, key: 'r' }) === false, 'input 内按键：不触发快捷键');
ok(I.shortcutAllowed({ target: ceChild, key: '3' }) === false, 'contenteditable 内部节点：不触发');
ok(I.shortcutAllowed({ target: body, key: 'a', isComposing: true }) === false && I.shortcutAllowed({ target: body, key: 'a', keyCode: 229 }) === false, 'IME 合成中（isComposing / keyCode 229）：不触发');
ok(I.shortcutAllowed({ target: body, key: 'k', ctrlKey: true }) === false, '带修饰键：不当单键快捷键');
ok(I.shortcutAllowed({ target: body, key: 'r' }, { drawerOpen: true }) === false, '抽屉打开：不触发');
const NAV = [['overview'], ['running'], ['decisions'], ['artifacts'], ['compose'], ['peers'], ['settings']];
ok(I.digitView({ key: '3', target: body }, NAV) === '', '裸数字 3：不再切视图');
ok(I.digitView({ key: '3', altKey: true, target: body }, NAV) === 'decisions' && I.digitView({ key: '7', altKey: true }, NAV) === 'settings', 'Alt+3 → decisions，Alt+7 → settings');
ok(I.digitView({ key: '8', altKey: true }, NAV) === '' && I.digitView({ key: '3', altKey: true, ctrlKey: true }, NAV) === '', 'Alt+8 / Ctrl+Alt+3：无效');

// ---- 3. morph 守卫 ----
const keep = el('div', { 'data-keep': '' }); const ta = el('textarea', {}); const inp = el('input', {});
ok(I.keepNode(keep, null) === false, 'data-keep 子树不参与 morph');
ok(I.keepNode(ta, ta) === false && I.keepNode(inp, inp) === false && I.keepNode(ce, ce) === false, '聚焦中的 textarea / input / contenteditable 不被替换');
ok(I.keepNode(inp, ta) === true && I.keepNode(el('div', {}), null) === true && I.keepNode({ nodeType: 3 }, null) === true, '未聚焦输入 / 普通节点 / 文本节点照常 morph');

// ---- 4. 合并稿附加段切换 ----
const host = el('div', { 'data-merge-body': '', 'data-keep': '' });
ok(I.toggleExtra(host, 'difference', '<div class="md-extra" data-kind="difference"></div>') === true && host.querySelector('.md-extra').dataset.kind === 'difference', '点「分歧」：面板出现');
ok(I.toggleExtra(host, 'difference', '<div class="md-extra" data-kind="difference"></div>') === false && host.querySelector('.md-extra') === null, '再点「分歧」：收回');
I.toggleExtra(host, 'difference', '<div class="md-extra" data-kind="difference"></div>');
ok(I.toggleExtra(host, 'consensus', '<div class="md-extra" data-kind="consensus"></div>') === true && host.querySelector('.md-extra').dataset.kind === 'consensus' && host.children.length === 1, '点「共识」：切换内容且只有一个面板');

// ---- 5. data-keep 正文陈旧判定 ----
const det = el('details', { 'data-merge': 'log/a.md', 'data-ver': '2026-09-24T00:00:00' });
const mb = el('div', { 'data-merge-body': '', 'data-keep': '' }); det.append(mb);
ok(I.mergeBodyStale(det, mb) === false, '未加载：不算陈旧');
mb.dataset.loaded = '1'; mb.dataset.path = 'log/a.md'; mb.dataset.ver = '2026-09-24T00:00:00';
ok(I.mergeBodyStale(det, mb) === false, '路径版本一致：保留（心跳 / 其它状态变化不重载）');
det.attrs['data-ver'] = '2026-09-24T00:05:00';
ok(I.mergeBodyStale(det, mb) === true, '决策 updated 变化：解除旧缓存');
det.attrs['data-ver'] = '2026-09-24T00:00:00'; det.attrs['data-merge'] = 'log/b.md';
ok(I.mergeBodyStale(det, mb) === true, '合并路径变化：解除旧缓存');

// ---- 6. 实体深层链接 ----
const P = o => new Map(Object.entries(o));
ok(JSON.stringify(I.parseEntity(P({ d: '20260923-000001-aaaa' }))) === JSON.stringify({ kind: 'decision', key: 'd', id: '20260923-000001-aaaa', invalid: '' }), '?d= 解析为决策');
ok(I.parseEntity(P({ t: 'pending-child-dddd-abc123' })).kind === 'task' && I.parseEntity(P({ a: 'fable6' })).kind === 'agent', '?t= 任务 / ?a= Agent');
ok(I.parseEntity(P({ d: '../../etc/passwd' })).invalid === '../../etc/passwd' && I.parseEntity(P({ d: '<script>' })).id === '', '非法 ID 标记 invalid，不当实体用');
ok(I.parseEntity(P({ d: '协作工作台-任务.1' })).id === '协作工作台-任务.1', '中文 / 点 / 横线 ID 允许');
ok(I.parseEntity(P({})) === null && I.parseEntity(P({ d: '' })) === null, '无实体参数 → null');
ok(JSON.stringify(I.entityPatch('task', 'x1')) === JSON.stringify({ d: '', t: 'x1', a: '' }) && JSON.stringify(I.entityPatch()) === JSON.stringify({ d: '', t: '', a: '' }), 'entityPatch 只保留一种实体并清空其余');

// ---- 7. 防抖 ----
const calls = []; const dq = I.debounce(v => calls.push(v), 30);
dq('a'); dq('ab'); dq('abc'); await new Promise(r => setTimeout(r, 80));
ok(calls.length === 1 && calls[0] === 'abc', '三次连续输入只写一次 URL，且是整词');
dq.flush('zzz'); ok(calls[1] === 'zzz', 'flush 立即写入');

// ---- 8. 源码级守卫（补充，不是唯一依据） ----
const app = frontSrc('app.js'), views = frontSrc('views.js'), core = readFileSync(join(repo, 'web/v2/js/core.js'), 'utf8');
ok(views.includes('data-merge-body data-keep') && app.includes('data-merge-body data-keep'), '两处合并稿正文容器都带 data-keep');
ok(views.includes('data-ver="${attr(d.updated') && app.includes('data-ver="${attr(d.updated'), '外层 details 带 data-ver（决策 updated）');
ok((app.includes("store.on(['time'], s => renderShell(s))") || app.includes("store.on(['time'], s => { renderShell(s); tickRelTime(); })")) && !/store\.on\(\[[^\]]*'live', 'time'\]/.test(app), 'time 订阅已拆出 renderView（cfa6 起附带相对时间局部更新）');
ok(!/\/\^\[1-7\]\$\/\.test\(e\.key\)\) switchView/.test(app) && app.includes('digitView(e, NAV)'), '裸数字切视图已删，改 Alt+数字');
ok(core.includes('keepNode(oldNode, document.activeElement, newNode)'), 'core.js morph 使用 keepNode 守卫');
ok(views.includes('data-key="metric-${view}"') && views.includes('data-key="${attr(it.action + \':\' + it.id)}"'), 'metric / focus-item 带 data-key');
ok(!/'composer-team'[^\n]*renderView\(S\(\)\)/.test(app) && !/'composer-group'[^\n]*renderView\(S\(\)\)/.test(app) && app.includes('syncComposer(form, true)'), 'Team / 按模型宏走 syncComposer(form, true)，不再 renderView');
ok(app.includes('__busHandled') && app.includes('pointerdown') && (app.includes('resolveAction(e.target, downEl') || app.includes('resolveAction(e.target, e.detail === 0 ? null : downEl')), '点击委托接入 resolveAction + 手势同源 + 互斥标记（cfa6：detail=0 不用陈旧 pointerdown）');
ok(app.includes('openEntityFromUrl()') && app.includes("markEntityUrl('decision', taskId)") && app.includes("markEntityUrl('task', taskId)"), '深层链接：启动 / popstate 解析，打开抽屉时标记 URL');
console.log(fails ? `\n${fails} FAILED` : '\nALL PASS'); process.exit(fails ? 1 : 0);
