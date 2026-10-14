// 体验反馈按钮：任何页面引入一行 <script src="/v2/js/feedback.js" defer></script> 即自动挂载。
// 记下「在哪、点了什么、遇到什么」，存到工作台 feedback/，之后可在「扩展与来源 → 体验反馈」一键转成任务。
// 不依赖其它模块，出错也不影响主界面。
(function () {
  if (window.__wbFeedback) return;
  window.__wbFeedback = true;
  var lastTarget = '', lastTag = '';
  // 应用信息（版本、反馈仓库）：拿不到就只保留「记到本机」
  var app = { name: 'Agent 协作台', version: '', feedback: { github_repo: '', labels: '' } };
  fetch('/api/app').then(function (r) { return r.ok ? r.json() : null; }).then(function (d) { if (d) app = d; }).catch(function () {});
  function tagOnly(el) { if (!el || !el.tagName) return ''; var s = el.tagName.toLowerCase(); if (el.id) s += '#' + el.id; var a = el.getAttribute('data-action'); if (a) s += '[' + a + ']'; return s; }
  function describe(el) {
    if (!el || !el.tagName) return '';
    var s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    var cls = (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 2);
    if (cls.length) s += '.' + cls.join('.');
    var label = el.getAttribute('aria-label') || el.getAttribute('data-tip') || (el.textContent || '').trim();
    if (label) s += ' “' + label.replace(/\s+/g, ' ').slice(0, 40) + '”';
    return s;
  }
  document.addEventListener('pointerdown', function (e) {
    if (!e.target.closest || e.target.closest('.wbfb')) return;
    var hit = e.target.closest('button,a,[role=button],input,select,textarea,[data-action]') || e.target;
    lastTarget = describe(hit); lastTag = tagOnly(hit);
  }, true);

  var css = '.wbfb-btn{position:fixed;right:18px;bottom:18px;z-index:9000;display:flex;align-items:center;gap:6px;padding:8px 14px;border-radius:999px;border:1px solid var(--line,#d5d8d2);background:var(--surface,#fff);color:var(--text,#1c211e);box-shadow:var(--shadow-lg,0 6px 24px rgba(0,0,0,.14));font:500 calc(13px * var(--fs-scale, 1))/1 var(--font,system-ui);cursor:pointer;transition:transform .18s cubic-bezier(.2,.8,.2,1),box-shadow .18s}' +
    '.wbfb-btn:hover{transform:translateY(-2px)}.wbfb-btn svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:2}' +
    '.wbfb-panel{position:fixed;right:18px;bottom:66px;z-index:9001;width:min(360px,calc(100vw - 36px));padding:16px;border-radius:14px;border:1px solid var(--line,#d5d8d2);background:var(--surface,#fff);color:var(--text,#1c211e);box-shadow:var(--shadow-lg,0 10px 40px rgba(0,0,0,.18));font:calc(14px * var(--fs-scale, 1))/1.5 var(--font,system-ui);transform-origin:bottom right;animation:wbfbIn .2s cubic-bezier(.2,.8,.2,1)}' +
    '@keyframes wbfbIn{from{opacity:0;transform:translateY(8px) scale(.97)}to{opacity:1;transform:none}}' +
    '@media (prefers-reduced-motion:reduce){.wbfb-panel{animation:none}.wbfb-btn{transition:none}}' +
    '.wbfb-panel h3{margin:0 0 4px;font-size:calc(15px * var(--fs-scale, 1))}.wbfb-panel p{margin:0 0 10px;color:var(--muted,#667);font-size:calc(12px * var(--fs-scale, 1))}' +
    '.wbfb-seg{display:flex;gap:6px;margin-bottom:10px}.wbfb-seg button{flex:1;display:inline-flex;align-items:center;justify-content:center;padding:6px 0;border-radius:8px;border:1px solid var(--line,#d5d8d2);background:var(--surface-2,#f4f5f2);color:inherit;cursor:pointer;font:inherit;font-size:calc(13px * var(--fs-scale, 1))}' +
    '.wbfb-seg button[aria-pressed=true]{background:var(--accent-soft,#dcebe2);border-color:var(--accent,#28664b);color:var(--accent-strong,#1d4a37);font-weight:600}' +
    '.wbfb-panel textarea{width:100%;box-sizing:border-box;min-height:96px;padding:8px 10px;border-radius:8px;border:1px solid var(--line,#d5d8d2);background:var(--surface-2,#f8f9f6);color:inherit;font:inherit;resize:vertical}' +
    '.wbfb-ctx{margin:8px 0 12px;font-size:calc(12px * var(--fs-scale, 1));color:var(--muted,#667);word-break:break-all}' +
    '.wbfb-row{display:flex;justify-content:flex-end;gap:8px}.wbfb-row button{display:inline-flex;align-items:center;gap:6px;min-height:34px;padding:6px 14px;border-radius:8px;border:1px solid var(--line,#d5d8d2);background:var(--surface,#fff);color:inherit;cursor:pointer;font:inherit}' +
    '.wbfb-row .go{background:var(--accent,#28664b);border-color:var(--accent,#28664b);color:var(--accent-ink,#fff);font-weight:600}' +
    '.wbfb-row{flex-wrap:wrap}.wbfb-note{margin:6px 0 0;font-size:calc(11px * var(--fs-scale, 1));color:var(--muted)}' +
    '.wbfb-msg{margin-top:8px;font-size:calc(12px * var(--fs-scale, 1))}.wbfb-msg.err{color:var(--danger,#b3261e)}.wbfb-msg.ok{color:var(--ok,#28664b)}';
  var st = document.createElement('style');
  st.textContent = css;
  document.head.appendChild(st);

  var btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'wbfb wbfb-btn';
  btn.setAttribute('aria-label', '记录体验反馈（Alt+B）');
  btn.title = '记录体验反馈（Alt+B）';
  btn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg><span>反馈</span>';
  var panel = null;

  function view() {
    var cur = document.querySelector('#nav [aria-current="page"], #nav .active, .tabs [aria-selected="true"]');
    return (cur && (cur.textContent || '').trim().replace(/\s*\d+$/, '').slice(0, 40)) || (location.hash || '').slice(1, 40);
  }
  function close() { if (panel) { panel.remove(); panel = null; btn.focus(); } }
  function open() {
    if (panel) return close();
    var kind = 'ux', target = lastTarget, v = view();
    panel = document.createElement('div');
    panel.className = 'wbfb wbfb-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', '记录体验反馈');
    panel.innerHTML = '<h3>记下这个问题</h3><p>随手写，不用组织语言。之后可以一键转成任务交给 Leader。</p>' +
      '<div class="wbfb-seg" role="group" aria-label="类型"><button type="button" data-k="bug" aria-pressed="false">出错了</button><button type="button" data-k="ux" aria-pressed="true">不好用</button><button type="button" data-k="idea" aria-pressed="false">有个想法</button></div>' +
      '<label class="sr-only" for="wbfbText">内容</label><textarea id="wbfbText" placeholder="例：点「派发任务」后找不到刚派的任务在哪"></textarea>' +
      '<div class="wbfb-ctx"></div><div class="wbfb-row"><button type="button" data-x="cancel">取消</button><button type="button" data-x="copy">复制内容</button>' +
      (app.feedback && app.feedback.github_repo ? '<button type="button" data-x="github">发到 GitHub</button>' : '') +
      '<button type="button" class="go" data-x="send">记到本机 <small>Ctrl+Enter</small></button></div>' +
      '<p class="wbfb-note">' + (app.feedback && app.feedback.github_repo ? '「发到 GitHub」「复制内容」' : '「复制内容」') + '只带你写的文字、类型、版本、页面和浏览器，不带项目名和你点过的内容。</p><div class="wbfb-msg" aria-live="polite"></div>';
    panel.querySelector('.wbfb-ctx').textContent = '会一起记录：' + [location.pathname, v && ('视图 ' + v), target && ('最后点击 ' + target)].filter(Boolean).join(' · ');
    document.body.appendChild(panel);
    var ta = panel.querySelector('textarea'), msg = panel.querySelector('.wbfb-msg');
    ta.focus();
    panel.addEventListener('click', function (e) {
      var k = e.target.closest('[data-k]');
      if (k) { kind = k.dataset.k; panel.querySelectorAll('[data-k]').forEach(function (b) { b.setAttribute('aria-pressed', String(b === k)); }); }
      var x = e.target.closest('[data-x]');
      if (x && x.dataset.x === 'cancel') close();
      if (x && x.dataset.x === 'send') send();
      if (x && x.dataset.x === 'copy') outward('copy');
      if (x && x.dataset.x === 'github') outward('github');
    });
    panel.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') close();
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) send();
    });
    var KIND = { bug: '出错了', ux: '不好用', idea: '有个想法' };
    // 对外的反馈正文：只放用户自己写的话和环境信息（不放项目名、不放点过的按钮文字——那可能是任务标题）
    function outwardBody(text) {
      return text + '\n\n---\n' + ['类型：' + (KIND[kind] || kind), '版本：' + (app.version || '未知'), '页面：' + location.pathname + (v ? '（' + v + '）' : ''),
        lastTag ? '最后操作：' + lastTag : '', '窗口：' + innerWidth + '×' + innerHeight, '浏览器：' + navigator.userAgent.replace(/\s*\([^)]*\)/g, ' ').replace(/\s+/g, ' ').slice(0, 120)].filter(Boolean).join('\n');
    }
    function outward(how) {
      var text = ta.value.trim();
      if (text.length < 2) { msg.className = 'wbfb-msg err'; msg.textContent = '请先写下遇到的问题或想法'; ta.focus(); return; }
      var body = outwardBody(text), title = '[' + (KIND[kind] || '反馈') + '] ' + text.split('\n')[0].slice(0, 60);
      if (how === 'github') {
        var repo = app.feedback.github_repo, labels = app.feedback.labels ? '&labels=' + encodeURIComponent(app.feedback.labels) : '';
        // 链接太长浏览器会截断：正文超过 6000 字就截掉，并提醒用户把全文粘进去
        var short = body.length > 6000 ? body.slice(0, 6000) + '\n\n（内容太长被截断，完整内容已复制到剪贴板，可直接粘贴替换）' : body;
        if (short !== body && navigator.clipboard) navigator.clipboard.writeText(title + '\n\n' + body).catch(function () {});
        window.open('https://github.com/' + repo + '/issues/new?title=' + encodeURIComponent(title) + '&body=' + encodeURIComponent(short) + labels, '_blank', 'noopener');
        msg.className = 'wbfb-msg ok'; msg.textContent = '已打开 GitHub 新建 Issue 页面，登录后点「Submit new issue」即可；本机也会存一份。';
        // 反馈 fb-abd29e3511：下一步动作已经在新标签页里，面板留着只会挡路，提示看过就收起（与「记到本机」一致）。
        // 「复制内容」不加这句：用户还要回到本页面把剪贴板贴出去。
        setTimeout(close, 1600);
      } else {
        var done = function () { msg.className = 'wbfb-msg ok'; msg.textContent = '已复制。可以贴到微信、邮件或群里发给作者；本机也会存一份。'; };
        if (navigator.clipboard) navigator.clipboard.writeText(title + '\n\n' + body).then(done, function () { msg.className = 'wbfb-msg err'; msg.textContent = '复制失败：浏览器不允许访问剪贴板，请手动选中文字复制'; });
        else { ta.select(); try { document.execCommand('copy'); done(); } catch (e) { msg.className = 'wbfb-msg err'; msg.textContent = '复制失败，请手动选中文字复制'; } }
      }
      send(true);
    }
    var sending = false;
    function send(quiet) {
      if (sending) return;
      var text = ta.value.trim();
      if (text.length < 2) { msg.className = 'wbfb-msg err'; msg.textContent = '请写下遇到的问题或想法'; ta.focus(); return; }
      sending = true;
      var project = (document.getElementById('projectSelect') || {}).value || '';
      fetch('/api/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text, kind: kind, page: location.pathname + location.hash, view: v, target: target,
          viewport: innerWidth + '×' + innerHeight, project: project }) })
        .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status)); return d; }); })
        .then(function () {
          if (quiet === true) return;   // 发到 GitHub / 复制：提示已经写过了，面板留着方便用户看
          msg.className = 'wbfb-msg ok';
          msg.innerHTML = '已记录。可在 <a href="/hub#feedback">扩展与来源 → 体验反馈</a> 转成任务。';
          ta.value = '';
          setTimeout(close, 1600);
        })
        .catch(function (err) { msg.className = 'wbfb-msg err'; msg.textContent = '没记上：' + err.message + '（内容还在，可重试）'; })
        .then(function () { sending = false; });
    }
  }
  btn.addEventListener('click', open);
  document.addEventListener('keydown', function (e) { if (e.altKey && (e.key === 'b' || e.key === 'B')) { e.preventDefault(); open(); } });
  (document.body ? Promise.resolve() : new Promise(function (r) { document.addEventListener('DOMContentLoaded', r); }))
    .then(function () { document.body.appendChild(btn); });
})();
