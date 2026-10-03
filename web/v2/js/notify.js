// 8c2b 通知门面：kind → 级别/语气/去重 key，调用方不再自己拼 toast 参数；错误统一走 notify.error。
// 本模块不 import model.js / app.js；不写 localStorage；「重试」只在调用方显式传入可安全重入的函数时出现，绝不自动重发副作用命令。
import { toast, notifyInbox, openInbox } from './components.js';
import { ApiError, scrubText } from './core.js';

export const EVENTS = {
  copied: { message: '已复制', tone: 'ok', level: 'transient', key: 'copied' },
  opened_local: { message: '已在本机打开', tone: 'ok', level: 'transient', key: 'opened_local' },
  saved: { message: '已保存', tone: 'ok', level: 'transient', key: 'saved' },
  refreshed: { message: '已刷新', tone: 'ok', level: 'transient', key: 'refreshed' },
  acked: { message: '已确认', tone: 'ok', level: 'transient', key: 'acked' },
  queued: { message: '指令已排队', tone: 'ok', level: 'normal' },
  done: { message: '已完成', tone: 'ok', level: 'normal' },
  warn: { message: '', tone: 'warn', level: 'normal' },
  lease_changed: { message: 'Leader 已易主', tone: 'warn', level: 'sticky', key: 'lease' },
  restart_required: { message: '设置已保存，需要重启服务才生效', tone: 'warn', level: 'sticky', key: 'restart' },
  info: { message: '', tone: '', level: 'normal' },
};

export function notify(kind, payload = {}) {
  const ev = EVENTS[kind] || EVENTS.info;
  const message = payload.message || ev.message || String(kind);
  return toast(message, payload.tone ?? ev.tone, { level: payload.level || ev.level, key: payload.key ?? ev.key, detail: payload.detail, action: payload.action, undo: payload.undo, ms: payload.ms });
}

// 4xx / business：服务端可读原因原文直出（bus_web 的 400 已是中文），不提供重试。
// network / 5xx：sticky + 可复制详情（safeDetail 白名单，无认证头 / 请求 body / 凭据 URL）；仅当 retry 是函数才出现「重试」。
notify.error = (e, { retry = null, prefix = '' } = {}) => {
  const err = e instanceof ApiError ? e : new ApiError(e?.message || String(e), { kind: 'client', status: 0 });
  const safe = err.safeDetail();                       // 去重 key 与详情都只用脱敏后的 path，原始 query 不落 DOM 属性
  const detail = JSON.stringify(safe, null, 1);
  // R2-3：屏上可见的正文同样要脱敏。服务端错误文案可能原样回带 URL / Bearer / 密钥，
  // 只洗 safeDetail 而不洗 message，凭据照样进 DOM（这正是 R2 复审抓到的漏洞）。
  const base = `${prefix}${safe.message}`;
  if (!err.retryable) return toast(base, 'bad', { level: 'sticky', key: `err:${err.kind}:${err.status}:${safe.path}`, detail });
  const action = typeof retry === 'function' ? { label: '重试', go: () => { try { const r = retry(); if (r && typeof r.catch === 'function') r.catch(e2 => notify.error(e2, { prefix })); } catch (e2) { notify.error(e2, { prefix }); } } } : null;
  return toast(err.kind === 'server' ? `服务端错误 ${err.status}：${base}` : base, 'bad', { level: 'sticky', key: `err:${err.kind}:${safe.path}`, detail, action });
};
notify.inbox = notifyInbox;
notify.openInbox = openInbox;
