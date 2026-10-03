// app-wake.js：唤醒掉线的 Agent（2026-10-02 用户：「掉线之后能不能按一下按钮就把它拉起来」）
// 唤醒语由后端生成（带接入地址）。POST /api/wake/send：声明了 wake 的插件（如 Arena 中继）管着这个 Agent 的会话就直接发进去；
// 否则返回唤醒语，前端复制，用户粘贴到它的对话里。核心前端不认识具体插件（ADR 0014）。醒没醒以心跳为准，这里不假装成功。
import { esc, attr, icon, api, relTime } from './core.js';
import { toast, openDrawer } from './components.js';
import { S, F, agentList, displayName } from './model.js';

// 能自动发送的 Agent（某个插件管着它的会话）；拿不到就当没有
export async function loadWakeBound() {
  try { F.wakeBound = Object.keys((await api('/api/wake/auto')).peers || {}); } catch { F.wakeBound = []; }
  return F.wakeBound;
}
export const offlineAgents = () => agentList().filter(a => a.online === false).sort((x, y) => String(y.last_seen || '').localeCompare(String(x.last_seen || '')));

// 剪贴板接口要权限（内嵌浏览器、非安全上下文会拒绝）：退到选中隐藏文本框 + execCommand
async function copy(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* 退到下面 */ }
  const ta = Object.assign(document.createElement('textarea'), { value: text, readOnly: true, className: 'offscreen-copy' });
  document.body.append(ta); ta.select();
  let ok = false; try { ok = document.execCommand('copy'); } catch { ok = false; }
  ta.remove(); return ok;
}
// 都复制不了：把唤醒语摆出来，已选中，用户 Ctrl+C
function showText(peer, text) {
  openDrawer({ title: `${displayName(peer)} 的唤醒语`, subtitle: '自动复制没成功：文字已选中，按 Ctrl+C 复制，粘贴到它所在的对话里发送', body: `<div class="field"><textarea id="wakeText" class="mono wake-text" readonly>${esc(text)}</textarea></div>` });
  const ta = document.getElementById('wakeText'); ta?.focus(); ta?.select();
}

// 唤醒一个：返回 'sent'（已发进会话）/ 'copied'（已复制）/ 'shown'（复制不了，摆出来让用户复制）
export async function wakePeer(peer, { quiet = false } = {}) {
  const w = await api('/api/wake/send', { method: 'POST', body: JSON.stringify({ peer }) });
  if (w.sent) {
    if (!quiet) toast(w.duplicate ? `已经在给 ${displayName(peer)} 发唤醒语了，等它的心跳` : `已把唤醒语发进 ${displayName(peer)} 的会话；心跳回来就会显示在线（一般 1–2 分钟）`, 'ok', { ms: 5000 });
    return 'sent';
  }
  if (await copy(w.text)) {
    if (!quiet) toast(`已复制 ${displayName(peer)} 的唤醒语：粘贴到它所在的对话里发送`, 'ok', { ms: 6000 });
    return 'copied';
  }
  if (!quiet) showText(peer, w.text);
  return 'shown';
}

// 批量：能自动发的一次发完；其余每行一个「复制唤醒语」按钮（每个对话要各自粘贴，合成一段没有用）
export async function openWakeAll() {
  await loadWakeBound();
  const list = offlineAgents(), bound = new Set(F.wakeBound || []);
  const autoN = list.filter(a => bound.has(a.peer)).length;
  const rows = list.map(a => `<div class="wake-row" data-key="${attr(a.peer)}"><span class="min0 grow"><b class="truncate">${esc(displayName(a.peer))}</b><small class="muted truncate">${esc(a.model || '模型未登记')} · 最近心跳 ${esc(a.last_seen ? relTime(a.last_seen) : '从未')}</small></span>${bound.has(a.peer) ? '<span class="chip ok plain">可自动发送</span>' : ''}<button type="button" class="small" data-action="wake-peer" data-peer="${attr(a.peer)}">${icon(bound.has(a.peer) ? 'send' : 'copy')}<span>${bound.has(a.peer) ? '发送唤醒语' : '复制唤醒语'}</span></button></div>`).join('');
  openDrawer({
    title: `唤醒掉线的 Agent（${list.length}）`,
    subtitle: '掉线多半是宿主那边的原因（单轮工具调用到顶、上下文满、会话被结束），工作台拦不住；这里让拉起来只要一步',
    body: list.length ? `<div class="stack">
      <p class="help">${autoN ? `${autoN} 个经 Arena 中继接入的可以直接发进会话；` : ''}其余的工作台够不着它们的对话框：点「复制唤醒语」，粘贴到对应的对话里发送。唤醒语会让它立刻调用 bus_wait_task 回到待命；宿主断了 MCP 的，里面附了重新接入的地址。</p>
      ${autoN ? `<div class="row"><button type="button" class="primary" data-action="wake-auto-all">${icon('send')}一次发给可自动发送的 ${autoN} 个</button></div>` : ''}
      <div class="wake-list">${rows}</div></div>` : '<div class="empty">现在没有掉线的 Agent</div>',
  });
}

export async function wakeAutoAll(btn) {
  const bound = new Set(F.wakeBound || []);
  const list = offlineAgents().filter(a => bound.has(a.peer));
  btn?.classList.add('busy');
  let ok = 0;
  for (const a of list) if ((await wakePeer(a.peer, { quiet: true })) === 'sent') ok++;
  btn?.classList.remove('busy');
  toast(`已发出 ${ok} / ${list.length} 个；心跳回来的会自动变成在线`, ok === list.length ? 'ok' : 'warn', { ms: 5000 });
}
