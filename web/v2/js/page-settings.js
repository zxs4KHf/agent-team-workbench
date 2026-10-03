// page-settings.js：从 views2.js 拆出（ADR 0015，按页面拆模块；scripts/split_module.py 生成，函数体未改）
// 视图：发起、Agent、设置
import { esc, attr, icon, relTime, relAge, fmtDate } from './core.js';
import { S, F, project, agentList, onlineAgents, dispatchableAgents, undispatchableReason, DYNAMIC_TEAM, pickStats, pickStatsText, dispatchEstimate, agentByName, projectPeers, displayName, agentMeta, effectiveTeams, ensureComposer, IMAGE_ACCEPT, CONTROLLER_PEER, secondsLeft, fmtLeft } from './model.js';
import { head, empty, ovTabs, pageParts } from './views.js';
import { teamChips } from './components.js';
import * as Prefs from './prefs.js';

import { badge } from './views2.js';

// 强调色对比度提示：floor 到一位小数，避免 4.46 显示成「4.5 达标」
const ratio1 = x => (Math.floor(x * 10) / 10).toFixed(1);
export function accentHint(accent) {
  const c = Prefs.accentCheck(accent); if (!c) return '';
  const nums = `文字 ${ratio1(c.text)}:1 · 按钮 ${ratio1(c.button)}:1`;
  return c.ok ? `<span class="chip ok" title="当文字放在卡片上、当按钮底配字色，都 ≥ 4.5:1（WCAG AA）">${nums} 达标</span>`
    : `<span class="chip warn" title="低于 4.5:1：链接、选中项或按钮上的字会偏难认。仍可保存">${nums} · 偏低</span><button type="button" class="small ghost" data-action="pref-accent-fix" title="在原色基础上加深或提亮，到刚好达标">自动调整</button>`;
}
// 「外观与品牌」卡：全部是本机 localStorage 偏好，不产生任何服务端写入
function renderAppearance() {
  const p = Prefs.load(), t = p.theme, b = p.brand;
  const resolved = Prefs.resolveMode(t.mode);
  const swatch = (mode, label, accent) => `<button type="button" class="swatch ${t.mode === mode ? 'on' : ''}" data-action="pref-set" data-pref="theme.mode" data-value="${mode}" aria-pressed="${t.mode === mode ? 'true' : 'false'}"><i style="background:${attr(accent || (Prefs.PRESETS[mode] || {}).accent || 'var(--accent)')}"></i>${esc(label)}</button>`;
  const seg = (label, path, tiers, active) => `<div class="pref-row"><label>${esc(label)}</label><div class="segmented">${tiers.map(([v, l]) => `<button type="button" class="${String(active) === String(v) ? 'active' : ''}" data-action="pref-set" data-pref="${path}" data-value="${attr(v)}">${esc(l)}</button>`).join('')}</div></div>`;
  const mark = b.mark.type === 'image' && (b.mark.dataUrl || b.mark.url)
    ? `<span class="brand-mark" style="background-image:url(&quot;${attr(b.mark.dataUrl || b.mark.url)}&quot;)"></span>`
    : `<span class="brand-mark">${esc(b.mark.text)}</span>`;
  return `<section class="card pad stack" style="grid-column:1/-1"><div class="section-head"><h2>外观与品牌</h2>${badge(true, '仅本机浏览器')}</div>
    <p class="muted" style="font-size:calc(12px * var(--fs-scale, 1))">存在 <code>wb.prefs</code> 一个键里，换浏览器或清站点数据就回默认；导出可以带走，导入前会先列出差异。</p>
    <div class="field"><label>主题预设</label><div class="swatches">${Object.entries(Prefs.PRESETS).map(([k, v]) => swatch(k, v.label, v.accent)).join('')}${swatch('system', '跟随系统', Prefs.PRESETS[resolved].accent)}</div><span class="help">「跟随系统」由 JS 决解成亮或暗并监听变化${t.mode === 'system' ? `，当前系统解析为 <b>${esc((Prefs.PRESETS[resolved] || {}).label || resolved)}</b>` : '；选具体预设后不再跟随系统'}。</span></div>
    <div class="pref-row"><label for="prefAccent">强调色</label><div class="row" style="gap:8px"><input type="color" id="prefAccent" value="${attr(t.accent || (Prefs.PRESETS[resolved] || Prefs.PRESETS.light).accent)}" data-pref="theme.accent" data-action="pref-set" aria-label="强调色"><button type="button" class="small ghost" data-action="pref-accent-follow">跟随预设</button><span id="accentCheck" class="row" style="gap:6px" aria-live="polite">${accentHint(t.accent || (Prefs.PRESETS[resolved] || Prefs.PRESETS.light).accent)}</span><span class="faint" style="font-size:calc(12px * var(--fs-scale, 1))">${t.accent ? `当前：自定义 ${esc(t.accent)}` : '当前：跟随预设'}</span></div></div>
    ${seg('圆角', 'theme.radius', Prefs.RADIUS_TIERS.map(x => [x.value, x.label]), t.radius)}
    ${seg('字号', 'theme.fontScale', Prefs.FONT_TIERS.map(x => [x.value, x.label]), t.fontScale)}
    ${seg('密度', 'theme.density', Prefs.DENSITIES.map(x => [x, { compact: '紧凑', comfortable: '标准', spacious: '宽松' }[x]]), t.density)}
    <div class="pref-row"><label for="prefNav">侧栏宽度</label><div class="row" style="gap:8px;flex:1"><input type="range" id="prefNav" min="180" max="320" step="8" value="${attr(t.navWidth)}" data-pref="theme.navWidth" data-action="pref-set" style="flex:1"><span class="faint mono" style="font-size:calc(12px * var(--fs-scale, 1));width:52px">${esc(String(t.navWidth))} px</span></div></div>
    <div class="field"><label>品牌</label><div class="row" style="gap:10px;align-items:center">${mark}<span class="min0"><b>${esc(b.title)}</b><small class="muted" style="display:block;font:500 calc(11px * var(--fs-scale, 1))/1 var(--mono);letter-spacing:.14em">${esc(b.subtitleUpper ? b.subtitle.toUpperCase() : b.subtitle)}</small></span><button type="button" class="small" data-action="edit-brand">${icon('edit')}编辑图标与文案</button></div><span class="help">图标支持 1–2 个字、本机图片或粘贴；标题最多 ${Prefs.TITLE_MAX} 字、副标题最多 ${Prefs.SUBTITLE_MAX} 字，超出输入会被拦下。</span></div>
    <div class="row wrap"><button type="button" class="small" data-action="export-prefs">${icon('copy')}导出配置</button><label class="file-btn">导入配置…<input type="file" id="prefsFile" accept="application/json,.json" data-action="import-prefs" class="sr-only"></label><button type="button" class="small ghost danger" data-action="reset-prefs">恢复默认</button></div></section>`;
}
function renderGuard(g) {
  const alive = !!(g && g.alive), svcs = (g && g.services) || {}, ctl = (g && g.controller) || {};
  const rows = Object.entries(svcs).map(([k, v]) => `<div class="row between" style="gap:8px;font-size:calc(13px * var(--fs-scale, 1))"><span>${badge(v.ok !== false, v.ok === false ? '故障' : '正常')} ${esc(v.label || k)}</span><span class="muted mono">${v.ok === false ? esc(v.error || '') : (v.latency_ms != null ? v.latency_ms + ' ms' : '')}${v.restarts ? ` · 已拉起 ${v.restarts} 次` : ''}</span><button type="button" class="small ghost" data-action="guard-restart" data-service="${attr(k)}">拉起</button></div>`).join('');
  return `<section class="card pad stack span-all"><div class="section-head"><h2>服务守护</h2>${alive ? badge(true, `巡检中 · 每 ${g.interval_sec || 15} 秒`) : badge(false, '守护未运行')}</div>
    <p class="muted" style="font-size:calc(12px * var(--fs-scale, 1))">守护进程持续探测下列服务，连续两次失败自动拉起；Leader 心跳超过 ${180} 秒自动释放租约，由顶替顺序里第一个在线 Agent 接管。</p>
    ${rows || '<div class="muted">尚无巡检数据</div>'}
    <div class="row between" style="font-size:calc(13px * var(--fs-scale, 1))"><span>${badge(!!ctl.online, ctl.online ? 'Leader 在线' : 'Leader 离线')} Leader 心跳 ${ctl.age_sec != null ? ctl.age_sec + ' 秒前' : '—'}</span><span class="muted mono">${ctl.lease && ctl.lease.holder ? `租约 ${esc(ctl.lease.holder)} · epoch ${ctl.lease.epoch}` : '无租约'}</span></div>
    <div class="row wrap"><button type="button" class="primary" data-action="guard-restart" data-service="all">${icon('refresh')}一键拉起全部服务</button><button type="button" class="ghost" data-action="guard-start">${alive ? '守护运行中' : '启动守护'}</button></div></section>`;
}
export function renderSettings() {
  const d = S().settings;
  if (!d) return `<div class="view">${head('运行控制', '设置', '正在读取…')}<div class="settings-grid"><div class="card pad stack"><div class="sk" style="width:40%"></div><div class="sk"></div><div class="sk" style="width:70%"></div></div><div class="card pad stack"><div class="sk" style="width:50%"></div><div class="sk"></div></div></div></div>`;
  const s = d.settings || {}, m = d.mcp || {}, rt = m.runtime || {}, up = m.reachable;
  const uptime = up && m.uptime_sec != null ? (m.uptime_sec < 3600 ? `${Math.floor(m.uptime_sec / 60)} 分钟` : `${(m.uptime_sec / 3600).toFixed(1)} 小时`) : '—';
  const envLocked = !!d.env_override;
  const tokens = s.peer_tokens || {}, roots = s.peer_roots || {}, errors = d.peer_root_errors || {};
  const names = [...new Set([...Object.keys(tokens), ...Object.keys(roots), ...agentList().map(a => a.peer)])].sort();
  const credPage = pageParts('settings-creds', names, 15);   // ADR 0015：长表分页
  return `<div class="view">${head('运行控制', '设置', '这些开关直接改变各 Agent 在这台机器上能做什么。改完立即生效，除非标了需要重启。')}
    ${s._config_error ? `<div class="banner danger">${icon('warn')}<span><b>配置文件损坏。</b>${esc(s._config_error)} 修复前命令执行已自动禁用。文件：<code>settings.json</code></span></div>` : ''}
    <section class="card fit-panel settings-panel" data-key="fit-settings"><div class="fit-body">${ovTabs([
      { key: 'service', label: '服务', n: 0, html: `<div class="settings-grid">
      <section class="card pad stack"><div class="section-head"><h2>MCP 服务 <span class="mono muted" style="font-size:calc(12px * var(--fs-scale, 1))">127.0.0.1:8791</span></h2>${up ? badge(true, `运行中 · ${m.version || ''}`) : badge(false, '无法连接')}</div>
        <dl class="kv"><dt>PID</dt><dd>${esc(m.pid ?? '—')}</dd><dt>已运行</dt><dd>${esc(uptime)}</dd><dt>探测耗时</dt><dd>${up ? esc(m.latency_ms) + ' ms' : '—'}</dd><dt>子进程隔离</dt><dd>${rt.job_objects ? 'Job Object（超时整树终止）' : up ? '进程组（降级）' : '—'}</dd><dt>无窗口执行</dt><dd>${rt.no_window ? '是' : up ? '否' : '—'}</dd><dt>并发上限</dt><dd>${esc(rt.max_concurrent ?? '—')}</dd></dl>
        ${!up ? `<div class="banner danger">${esc(m.error || '服务未响应')}</div>` : ''}
        <div class="row wrap"><button type="button" class="primary" data-action="mcp-restart">${icon('refresh')}重启 MCP 服务</button><button type="button" class="ghost" data-action="mcp-health">重新探测</button></div></section>
      <section class="card pad stack" data-key="bridge"><div class="section-head"><h2>接入地址（Bridge）</h2>${S().bridge_url ? badge(true, '已登记') : badge(false, '用本机地址')}</div>
        <p class="help">Agent 和 Leader 的接入链接都以它开头。只写协议和域名，不带 /mcp/令牌。本机的宿主（Claude 桌面、Claude Code、Cursor 等）留空即可，默认 <code>http://127.0.0.1:8791</code>；网页版 AI 连不到你的电脑，需要一个指到本机 8791 端口的公网 HTTPS 地址（例如 Cloudflare Tunnel），填在这里。</p>
        <div class="row"><input type="url" id="bridgeUrl" value="${attr(S().bridge_url || '')}" placeholder="https://mcp.example.com" aria-label="Bridge 地址" spellcheck="false" autocomplete="off"><button type="button" data-action="save-bridge">保存</button></div>
      </section>
      ${renderGuard(d.guard)}
</div>` },
      { key: 'security', label: '权限与安全', n: 0, html: `<div class="settings-grid">
      <section class="card pad stack"><div class="section-head"><h2>工作区范围</h2>${d.restart_required ? badge(false, '需重启 MCP 才生效') : badge(true, '已生效')}</div>
        <p class="muted" style="font-size:calc(12px * var(--fs-scale, 1))">Agent 通过 <code>ws_read</code> / <code>ws_write</code> 和上下文文件选择器能访问的目录，范围越小越安全。</p>
        ${envLocked ? `<div class="banner">环境变量 <code>BUS_WORKSPACE=${esc(d.env_override)}</code> 正在覆盖此设置</div>` : ''}
        <div class="row"><input type="text" id="setWorkspace" value="${attr(s.workspace || '')}" placeholder="." ${envLocked ? 'disabled' : ''} aria-label="工作区路径" spellcheck="false" autocomplete="off"><button type="button" class="primary" data-action="save-workspace" ${envLocked ? 'disabled' : ''}>保存</button></div>
        <div class="row wrap" style="font-size:calc(12px * var(--fs-scale, 1))"><span class="muted">常用：</span>${[['.', '仅工作台目录'], ['..', '上一级目录']].map(([v, l]) => `<button type="button" class="fchip ${s.workspace === v ? 'on' : ''}" data-action="preset-ws" data-value="${attr(v)}">${l}</button>`).join('')}</div>
        <dl class="kv"><dt>解析为</dt><dd>${esc(d.resolved_workspace || '—')}</dd><dt>服务实际使用</dt><dd>${esc(d.runtime_workspace || '—')}</dd></dl>
        ${d.workspace_error ? `<div class="banner danger">${esc(d.workspace_error)}</div>` : ''}</section>
      <section class="card pad stack"><div class="section-head"><h2>命令执行（ws_run）</h2>${s.run_enabled ? badge(false, '开启：等同宿主权限') : badge(true, '已关闭')}</div>
        <div class="banner danger" style="font-size:calc(12px * var(--fs-scale, 1))">${icon('warn')}<span>这是唯一无法被目录范围约束的能力。开启时 Agent 能执行 Python / Node，可读写它的 Windows 账户能碰的任何文件。</span></div>
        <label class="switch"><input type="checkbox" ${s.run_enabled ? 'checked' : ''} data-action="toggle-run"><span class="track"></span><span>允许 Agent 执行命令</span></label>
        <div class="stack" ${s.run_enabled ? '' : 'hidden'}><div class="field"><label for="setTimeout">单次命令超时上限（秒）</label><input type="number" id="setTimeout" min="5" max="600" inputmode="numeric" value="${attr(s.run_timeout_max || 120)}"></div><div class="field"><label for="setAllow">命令白名单前缀，每行一条</label><textarea id="setAllow" rows="7" spellcheck="false" class="mono" style="font-size:calc(12px * var(--fs-scale, 1))">${esc((s.run_allow || []).join('\n'))}</textarea></div><div class="row wrap"><button type="button" class="primary" data-action="save-run">保存命令设置</button><button type="button" class="ghost" data-action="reset-allow">恢复默认白名单</button></div></div></section>
      <section class="card pad stack"><div class="section-head"><h2>审计日志</h2>${s.audit_enabled ? badge(true, '记录中') : badge(false, '未记录')}</div>
        <label class="switch"><input type="checkbox" ${s.audit_enabled ? 'checked' : ''} data-action="toggle-audit"><span class="track"></span><span>记录每一次 ws_run 调用</span></label>
        <div class="audit">${(d.audit || []).length ? d.audit.map(r => `<div><span class="faint">${esc(r.time?.slice(11, 19) || '')}</span> <b>${esc(r.peer || 'Leader')}</b> ${esc(r.command)} ${r.outcome ? `<span class="faint">${esc(r.outcome)}</span>` : ''}</div>`).join('') : '暂无记录'}</div>
        <span class="faint" style="font-size:calc(11px * var(--fs-scale, 1))">文件：${esc(d.audit_path || '')}</span></section>
      <section class="card pad stack"><div class="section-head"><h2>始终受保护</h2>${badge(true, '不可关闭')}</div>
        <ul style="margin:0;padding-left:18px;font-size:calc(13px * var(--fs-scale, 1));color:var(--muted);line-height:1.9"><li><code>console/</code>、<code>peers/</code> 只能由 bus 工具写入</li><li><code>.git</code>、<code>.ssh</code>、<code>.cloudflared</code>、<code>.shuncode</code>、<code>.private</code> 目录一律拒绝</li><li>token、凭据、<code>.env</code>、<code>.pem</code>、<code>.key</code>、<code>settings.json</code>、<code>workspace.json</code> 不经文件工具提供</li><li>路径越出工作区一律拒绝；覆盖已有文件必须带版本号</li></ul></section>

</div>` },
      { key: 'agents', label: 'Agent 凭据', n: Object.keys(tokens).length, html: `<div class="settings-grid">
      <section class="card pad stack"><div class="section-head"><h2>各 Agent 凭据与范围</h2>${Object.keys(tokens).length ? badge(true, `${Object.keys(tokens).length} 个专属 token`) : badge(false, '全部共用主 token')}</div>
        <p class="muted" style="font-size:calc(12px * var(--fs-scale, 1))">签发专属 token 后，该 Agent 的文件工具只能在指定根目录内操作，审计会记下是谁在执行。目录范围只约束文件工具；<code>ws_run</code> 开启时任何 Agent 都可用代码绕过。</p>
        <table class="table"><thead><tr><th>Agent</th><th>token</th><th>文件工具根目录</th></tr></thead><tbody>${credPage.rows.map(n => `<tr><td><b>${esc(n)}</b></td><td>${tokens[n] ? `<code>${esc(tokens[n])}</code> <button type="button" class="ghost small" data-action="peer-token-reveal" data-peer="${attr(n)}">查看</button><button type="button" class="ghost small" data-action="peer-token-rotate" data-peer="${attr(n)}">轮换</button><button type="button" class="ghost small danger" data-action="peer-token-revoke" data-peer="${attr(n)}">吊销</button>` : `<span class="muted">使用主 token</span> <button type="button" class="ghost small" data-action="peer-token-rotate" data-peer="${attr(n)}">签发专属 token</button>`}</td><td><input type="text" class="peer-root-input" data-peer="${attr(n)}" value="${attr(roots[n] || '')}" placeholder="留空 = 服务工作区" aria-label="${attr(n)} 根目录" spellcheck="false" autocomplete="off">${errors[n] ? `<div class="err">${esc(errors[n])}</div>` : ''}</td></tr>`).join('') || '<tr><td colspan="3" class="muted">还没有登记任何 Agent</td></tr>'}</tbody></table>${credPage.pager}
        <div class="row wrap"><input type="text" id="peerNewName" placeholder="新增 Agent 名（小写字母数字）" style="max-width:240px" aria-label="新增 Agent 名" autocomplete="off"><button type="button" class="ghost" data-action="peer-token-rotate" data-peer="__new__">签发</button><button type="button" class="primary" data-action="save-peer-roots">保存根目录</button></div></section>
</div>` },
      { key: 'appearance', label: '外观', n: 0, html: `<div class="settings-grid">
      ${renderAppearance()}
</div>` },
    ], 'settings')}</div></section>
  </div>`;
}

