// 顶栏「扩展与来源」入口：显示待确认条目 + 未处理反馈的数量。独立脚本，失败静默。
(function () {
  var st = document.createElement('style');
  st.textContent = '.hub-link{display:inline-flex;align-items:center;gap:6px;height:32px;padding:0 10px;border-radius:var(--r-ctl,8px);color:var(--muted);text-decoration:none;border:1px solid transparent;font-size:var(--fs-1,13px);white-space:nowrap}' +
    '.hub-link:hover{color:var(--text);border-color:var(--line);background:var(--surface-2)}.hub-link .i{width:16px;height:16px}' +
    '.hub-link b{min-width:18px;padding:0 5px;border-radius:999px;background:var(--accent);color:var(--accent-ink);font-size:calc(11px * var(--fs-scale, 1));line-height:18px;text-align:center}' +
    '@media (max-width:900px){.hub-link span{display:none}}';
  document.head.appendChild(st);
  // 计数只认非负整数：字符串 "3" 直接相加会拼成 "30"，负数/小数/NaN 会显示假数字。不合格的那项按「缺席」处理（只隐藏那项）
  function count(o, k) {
    if (!o || typeof o !== 'object' || Array.isArray(o)) return null;  // 数组也是 object，要单独拒绝（fable2 764d）
    var v = o[k];
    if (v === undefined) return 0;  // 扩展在、字段缺省：沿用旧语义记 0；显式 null 不算缺省
    return (typeof v === 'number' && Number.isSafeInteger(v) && v >= 0) ? v : null;
  }
  function tick() {
    var b = document.getElementById('hubBadge'), a = document.getElementById('hubLink');
    if (!b || document.hidden) return;
    // fetch 遇 4xx/5xx 不会 reject：先看 r.ok；带 error 或两项都没有也算失败（fable2 efe4）
    fetch('/api/ext/summary', { cache: 'no-store' }).then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }).then(function (d) {
      if (!d || d.error) throw new Error('bad summary');
      var pi = count(d.intake, 'pending'), fo = count(d.feedback, 'open');
      if (pi === null && fo === null) throw new Error('bad summary');
      d = { intake: pi === null ? null : { pending: pi }, feedback: fo === null ? null : { open: fo } };  // 只广播校验过的数
      var n = (pi || 0) + (fo || 0);
      b.hidden = !n; b.textContent = n > 99 ? '99+' : String(n);
      a.setAttribute('aria-label', '扩展与来源' + (n ? '（' + n + ' 项待处理）' : ''));
      window.dispatchEvent(new CustomEvent('wb:ext-summary', { detail: d }));  // 概览行动条复用这次请求，不另起轮询
    }).catch(function () {
      b.hidden = true; b.textContent = ''; a.setAttribute('aria-label', '扩展与来源');  // 失败时不留旧数字
      window.dispatchEvent(new CustomEvent('wb:ext-summary', { detail: null }));
    });
  }
  tick();
  setInterval(tick, 15000);
  document.addEventListener('visibilitychange', tick);
})();
