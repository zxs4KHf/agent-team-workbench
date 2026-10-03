// 交互守卫与深层链接（fedb：a667 / e131 / 018d）。纯函数、不依赖 core.js，node 下用最小 DOM 桩即可测试。
export const CONTROL_SEL = 'button,a[href],[role="button"],input,select,textarea,label,summary,[contenteditable=""],[contenteditable="true"]';

// 正在输入的目标：input / textarea / select / contenteditable（含其内部节点）
export function isTypingTarget(el) {
  if (!el || el.nodeType !== 1) return false;
  if (el.matches?.('input,textarea,select')) return true;
  if (el.isContentEditable === true) return true;
  return !!el.closest?.('[contenteditable=""],[contenteditable="true"]');
}

// 解析一次点击应执行的 [data-action] 元素；返回 null 表示这次点击不归委托处理。
// 规则：checkbox 交给 change；容器级 action 遇到内部真实控件（button / a / input / textarea / contenteditable…）不穿透；
// 手势同源（pointerdown 与 click 不在同一 action 元素内 → 视为中途被 morph 换了节点，放弃）；容器内选中文字不跳转。
export function resolveAction(target, downEl = null, selAnchor = null) {
  const t = target?.closest?.('[data-action]');
  if (!t) return null;
  if (t.matches?.('input[type="checkbox"]')) return null;
  const ctl = target.closest?.(CONTROL_SEL);
  if (ctl && ctl !== t) return null;
  if (selAnchor && t.contains(selAnchor)) return null;            // 刚在这块里拖选了文字：不是点击
  if (downEl && downEl !== t && !t.contains(downEl)) return null;  // pointerdown 不在这块里：中途被换了节点
  return t;
}

// 当前非空选区的锚点节点（元素），没有选区返回 null；调用方只在锚点落在 action 块内时才当作「在选字」
export function selectionAnchor(win = globalThis) {
  try {
    const s = win.getSelection?.(); if (!s || s.isCollapsed || !String(s).length) return null;
    const n = s.anchorNode; return n ? (n.nodeType === 1 ? n : n.parentNode || n) : null;
  } catch (_) { return null; }
}
export const hasTextSelection = win => !!selectionAnchor(win);

// 全局单键快捷键是否允许：输入中 / 输入法合成中 / 带修饰键 / 抽屉、命令面板、对话框打开时一律不触发
export function shortcutAllowed(e, ctx = {}) {
  if (!e) return false;
  if (isTypingTarget(e.target)) return false;
  if (e.isComposing || e.keyCode === 229) return false;
  if (e.altKey || e.ctrlKey || e.metaKey) return false;
  if (ctx.drawerOpen || ctx.cmdkOpen || ctx.dialogOpen) return false;
  return true;
}

// 数字切视图改为 Alt+数字；裸数字键不再切页（e131 主控裁定的首要嫌疑）
export function digitView(e, nav) {
  if (!e || !e.altKey || e.ctrlKey || e.metaKey) return '';
  if (!/^[1-7]$/.test(String(e.key))) return '';
  const n = nav?.[Number(e.key) - 1];
  return n ? n[0] : '';
}

// morph 守卫：data-keep 子树、聚焦中的输入类节点不参与 morph（e131 采纳 return false 路线）
export function keepNode(oldNode, activeEl, newNode) {
  if (!oldNode || oldNode.nodeType !== 1) return true;
  if (oldNode.matches?.('[data-keep]')) {
    // 2026-10-02 用户：「选了之后，后面的问题全部变成我做过的决策」。data-keep 只该保护「同一个对象」：
    // 回答后列表上移、翻页时，这个位置换成了另一道题，旧表单却原样留着，看上去就是下一题也被答过了。
    // 新内容不再带 data-keep，或 data-keep-id 不同（换了对象）→ 正常更新；同一对象才冻结。
    if (newNode && newNode.nodeType === 1) {
      if (!newNode.matches?.('[data-keep]')) return true;
      if ((oldNode.getAttribute('data-keep-id') || '') !== (newNode.getAttribute('data-keep-id') || '')) return true;
    }
    return false;
  }
  if (activeEl && activeEl === oldNode && (oldNode.isContentEditable === true || oldNode.matches?.('input,textarea,select'))) return false;
  return true;
}

export function debounce(fn, ms) {
  let timer = null;
  const d = (...a) => { clearTimeout(timer); timer = setTimeout(() => { timer = null; fn(...a); }, ms); };
  d.flush = (...a) => { clearTimeout(timer); timer = null; fn(...a); };
  d.cancel = () => { clearTimeout(timer); timer = null; };
  return d;
}

// a667：合并稿附加段「共识 / 分歧 / 验证依据」——同一按钮再点收回，不同按钮切换；返回是否处于展开态
export function toggleExtra(host, kind, html) {
  if (!host) return false;
  const ex = host.querySelector('.md-extra');
  if (ex) { const same = ex.dataset.kind === kind; ex.remove(); if (same) return false; }
  host.insertAdjacentHTML('beforeend', html);
  return true;
}

// a667：data-keep 冻结了正文子树，外层 details 的 data-merge / data-ver 仍随模板更新；两者不一致即视为陈旧，需要重载
export function mergeBodyStale(details, body) {
  if (!details || !body || !body.dataset || !body.dataset.loaded) return false;
  return (body.dataset.path || '') !== (details.dataset.merge || '') || (body.dataset.ver || '') !== (details.dataset.ver || '');
}

// 018d：实体深层链接。?p=<project>&view=…&d=<决策 task_id> | &t=<进行中任务 task_id> | &a=<Agent 名>
export const ENTITY_KEYS = { d: 'decision', t: 'task', a: 'agent' };
export function safeId(v) {
  const s = String(v ?? '').trim();
  return /^[\w.\-\u4e00-\u9fa5]{1,80}$/.test(s) ? s : '';
}
export function parseEntity(params) {
  const get = k => (typeof params?.get === 'function' ? params.get(k) : params?.[k]) ?? '';
  for (const k of Object.keys(ENTITY_KEYS)) {
    const raw = get(k);
    if (raw) { const id = safeId(raw); return { kind: ENTITY_KEYS[k], key: k, id, invalid: id ? '' : String(raw) }; }
  }
  return null;
}
export function entityPatch(kind = '', id = '') {
  const patch = { d: '', t: '', a: '' };
  const key = Object.keys(ENTITY_KEYS).find(k => ENTITY_KEYS[k] === kind);
  if (key && id) patch[key] = id;
  return patch;
}

// cfa6 / af57 R2：把「可见子集」的新顺序合回完整 task_order。
// 可见项只在它们原本占据的槽位内重排；不可见 / 非 running 项与其相对位置原样保留；表里没有的可见新 ID 紧跟其可见前驱插入；重复输入去重；空表 → 直接用可见顺序。
export function mergeVisibleOrder(full, visibleAfter) {
  const uniq = arr => { const seen = new Set(); return (Array.isArray(arr) ? arr : []).map(x => String(x ?? '')).filter(x => x && !seen.has(x) && seen.add(x)); };
  const base = uniq(full), vis = uniq(visibleAfter);
  if (!vis.length) return base;
  if (!base.length) return vis;
  const inBase = new Set(base);
  const slots = base.map((id, i) => vis.includes(id) ? i : -1).filter(i => i >= 0);   // 可见且在表里的项原槽位（升序）
  const known = vis.filter(id => inBase.has(id));                                      // 新顺序里已在表中的可见项
  const out = base.slice();
  slots.forEach((slot, i) => { out[slot] = known[i]; });
  // 表外的可见新 ID：插在它可见前驱之后；没有前驱就插在可见后继之前；都没有放最前
  vis.forEach((id, vi) => {
    if (inBase.has(id) || out.includes(id)) return;
    let at = -1;
    for (let j = vi - 1; j >= 0; j--) { const k = out.indexOf(vis[j]); if (k >= 0) { at = k + 1; break; } }
    if (at < 0) for (let j = vi + 1; j < vis.length; j++) { const k = out.indexOf(vis[j]); if (k >= 0) { at = k; break; } }
    out.splice(at < 0 ? 0 : at, 0, id);
  });
  return out;
}
// 键盘调序：在列表里把 id 上移 / 下移一位（越界不动），返回新数组
export function moveInList(list, id, delta) {
  const arr = (Array.isArray(list) ? list : []).slice(); const i = arr.indexOf(id);
  if (i < 0) return arr; const j = i + delta; if (j < 0 || j >= arr.length) return arr;
  arr.splice(i, 1); arr.splice(j, 0, id); return arr;
}
