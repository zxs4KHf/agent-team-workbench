// 悬停说明：全页只有一个固定定位的说明层，替代原来的 [data-tip]::after。
// 为什么换：伪元素只能画在元素上方、逃不出 overflow:hidden 的容器——顶栏按钮的说明画到了屏幕外，概览卡片里的说明被裁掉。
// 现在：上方放不下翻到下方，左右收进视口，长说明换行；键盘聚焦同样显示，Esc 收起；滚动时先收起、停下后重新校正。
// 核心规则「光标下是谁就显示谁」：鼠标移动、页面局部重绘（定时刷新换掉了元素）之后都按记住的光标位置校正一次，
// 不依赖浏览器是否补发移入事件（selftest 里屏蔽掉移入 / 移动事件验证过这条路径）；滚动停下后同样按光标位置校正。
// 任何带 data-tip 的元素自动生效，调用方不用改；不依赖其它模块，出错也不影响主界面。
(function () {
  if (window.__wbTip) return;
  window.__wbTip = true;
  var tip = null, cur = null, pt = null, mutedEl = null, mutedText = null, queued = false, settle = 0, viaFocus = false, GAP = 6, EDGE = 8;
  function layer() {
    if (!tip || !tip.isConnected) {
      tip = document.createElement('div');
      tip.className = 'wb-tip'; tip.id = 'wbTip'; tip.setAttribute('role', 'tooltip');
      document.body.appendChild(tip);
    }
    return tip;
  }
  function hide() {
    if (tip) tip.classList.remove('on');
    if (cur && cur.getAttribute('aria-describedby') === 'wbTip') cur.removeAttribute('aria-describedby');
    cur = null;
  }
  // 注意：这里读 offsetWidth 再写位置，是一次强制同步布局。定时刷新换掉元素时每个刷新周期最多一次；
  // 如果将来有每帧改 DOM 的视图（进度条、日志流），上界就是每帧一次，届时要改成只在元素变了才量。
  function show(el) {
    var text = el.getAttribute('data-tip');
    if (!text) return hide();
    if (cur !== el) hide();
    var t = layer();
    t.textContent = text; t.classList.add('on');
    var r = el.getBoundingClientRect(), w = t.offsetWidth, h = t.offsetHeight;
    var vw = document.documentElement.clientWidth, vh = window.innerHeight;
    var top = r.top - GAP - h, below = top < EDGE;
    if (below) top = Math.min(r.bottom + GAP, vh - h - EDGE);
    var left = Math.max(EDGE, Math.min(r.left + r.width / 2 - w / 2, vw - w - EDGE));
    t.style.left = Math.round(left) + 'px'; t.style.top = Math.round(Math.max(EDGE, top)) + 'px';
    t.setAttribute('data-side', below ? 'below' : 'above');
    cur = el;
    if (!el.hasAttribute('aria-describedby')) el.setAttribute('aria-describedby', 'wbTip');
  }
  function tipOf(node) { return node && node.closest ? node.closest('[data-tip]') : null; }
  // Esc 静音：同一个节点一直静音；节点被重绘替换了才退化成按文字比较（相邻的同名按钮不受影响）
  function isMuted(el) {
    return !!mutedEl && (el === mutedEl || (!mutedEl.isConnected && el.getAttribute('data-tip') === mutedText));
  }
  // 没有光标位置时看键盘焦点：焦点还在带说明的元素上（键盘聚焦、没被 Esc 静音）就算它。
  // 另外两个条件（opus1 5631）：①元素还在视口里——滚出上方时说明会被夹进视口、钉在顶边，描述一个看不见的元素；
  // ②最后一次显示来自聚焦（viaFocus）——鼠标点进输入框、移开、离开窗口后，刷新不该把说明自己弹回来
  function focusTip() {
    var a = document.activeElement, el = a && a !== document.body ? tipOf(a) : null;
    if (!viaFocus || !el || !a.matches || !a.matches(':focus-visible') || isMuted(el)) return null;
    var r = a.getBoundingClientRect();
    return r.bottom < 0 || r.top > window.innerHeight || r.right < 0 || r.left > window.innerWidth ? null : el;
  }
  // 按光标位置校正：光标下有说明就显示，没有就收起。没有光标位置（纯键盘）时按焦点校正——
  // 否则键盘聚焦的说明被滚动收起后回不来，得重新 Tab 一圈（opus1 1c4f）
  function sync() {
    queued = false;
    if (!pt) {
      var f = focusTip();
      if (f) { if (f !== cur || !tip || !tip.isConnected || !tip.classList.contains('on')) show(f); } else if (cur && !cur.isConnected) hide();
      return;
    }
    var el = tipOf(document.elementFromPoint(pt.x, pt.y));
    if (el && isMuted(el)) return;
    mutedEl = mutedText = null;
    if (el) { if (el !== cur || !tip || !tip.isConnected || !tip.classList.contains('on')) { show(el); viaFocus = false; } } else if (cur) { hide(); viaFocus = false; }
  }
  function later() { if (!queued) { queued = true; requestAnimationFrame(sync); } }
  function at(e) { pt = { x: e.clientX, y: e.clientY }; }
  document.addEventListener('pointerover', function (e) { if (e.pointerType !== 'touch') { at(e); sync(); } }, true);
  document.addEventListener('pointermove', function (e) { if (e.pointerType !== 'touch') { at(e); later(); } }, true);
  // 真的离开窗口才忘掉光标位置；光标下的节点被移除时浏览器也会派发 relatedTarget 为空的 out，那时要留着位置交给校正
  document.addEventListener('pointerout', function (e) { if (!e.relatedTarget && e.target.isConnected) { pt = null; hide(); } }, true);
  document.addEventListener('focusin', function (e) {
    var el = tipOf(e.target);
    if (el && e.target.matches && e.target.matches(':focus-visible')) { show(el); viaFocus = true; }
  }, true);
  document.addEventListener('focusout', function () { if (!pt) hide(); else later(); }, true);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && cur) { mutedEl = cur; mutedText = cur.getAttribute('data-tip'); hide(); } }, true);
  // 滚动 / 缩放：先收起（位置已经不对），停下 150ms 后按光标位置重新校正——和浏览器原生 :hover 一样。
  // pt 是视口坐标，滚动时光标在屏幕上没动，它一直有效；重新校正拿到的就是此刻光标下的元素。
  // 以前滚动时把 pt 清掉，结果刷新引起的滚动锚定（上方内容高度变了，浏览器自动调滚动位置）会把正悬停的说明收掉、
  // 鼠标不动就再也回不来（体检里「导出任务包」6 个稳定丢 5 个）。别处的日志面板自己滚动时不收起。
  function settleLater() { clearTimeout(settle); settle = setTimeout(later, 150); }
  // 不论鼠标还是键盘都排一次校正（sync 重复触发是幂等的；别处面板自滚也只是多一个定时器）
  window.addEventListener('scroll', function (e) {
    var s = e.target;
    if (cur && (s === document || s === document.documentElement || (s.contains && s.contains(cur)))) hide();
    settleLater();
  }, true);
  window.addEventListener('resize', function () { if (cur) hide(); settleLater(); });
  // 页面局部重绘：光标没动，但光标下的元素可能被换掉了
  // 说明层自己插进 / 移出 body 不算重绘：否则第一次显示就会再排一次校正，负载高时这次校正晚到、落在滚动收起之后，
  // 把刚收起的说明又弹回来（ui_audit_selftest「键盘聚焦的说明滚动后丢掉」对照在全量跑时偶发失败的根因）
  function onlyTip(m) { var n = [].concat([].slice.call(m.addedNodes), [].slice.call(m.removedNodes)); return n.length > 0 && n.every(function (x) { return x === tip || x.id === 'wbTip'; }); }
  function watch() { new MutationObserver(function (ms) { for (var i = 0; i < ms.length; i++) if (ms[i].target !== tip && !onlyTip(ms[i])) return later(); }).observe(document.body, { childList: true, subtree: true }); }
  if (document.body) watch(); else document.addEventListener('DOMContentLoaded', watch);
})();
