'use strict';

const frame = document.getElementById('daily-workbench');
const status = document.getElementById('connection');
const error = document.getElementById('connection-error');
const detail = document.getElementById('error-detail');
const standalone = document.getElementById('standalone');
const retry = document.getElementById('retry');
let connecting = false;

function loopbackRoot(value) {
  // Check the literal authority too: URL() normalizes alternate IP spellings and backslashes.
  if (typeof value !== 'string' || /[\s\\]/.test(value)
      || !/^http:\/\/(?:127\.0\.0\.1|localhost|\[::1\]):[1-9]\d{3,4}(?:\/|$)/.test(value)) {
    throw new Error('每日工作台返回的本机服务地址无效。');
  }
  const target = new URL(value);
  if (target.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(target.hostname)
      || target.username || target.password || target.pathname !== '/' || target.hash
      || Number(target.port) < 1024 || Number(target.port) > 65535) {
    throw new Error('每日工作台返回的本机服务地址无效。');
  }
  return target;
}

function connectionInfo(data) {
  if (!data || data.id !== 'daily-workbench' || data.ready !== true) {
    throw new Error('请确认每日工作台服务正在运行，并核对插件端口。');
  }
  const target = loopbackRoot(data.url);
  const team = loopbackRoot(data.team_url);
  if (target.search !== '?embedded=1' || team.search) {
    throw new Error('每日工作台返回的页面地址无效。');
  }
  return { embedded: target.href, standalone: target.origin + '/' };
}

async function connect() {
  if (connecting) return;
  connecting = true;
  retry.disabled = true;
  error.hidden = true;
  frame.hidden = true;
  frame.removeAttribute('src');
  standalone.hidden = true;
  standalone.removeAttribute('href');
  status.textContent = '正在连接每日工作台…';
  try {
    const response = await fetch('/api/daily-workbench/plugin-info', {
      signal: AbortSignal.timeout(10000), cache: 'no-store', redirect: 'error',
    });
    if (!response.ok) throw new Error('请确认插件已启用、每日工作台服务已启动，并核对插件端口。');
    const info = connectionInfo(await response.json());
    frame.src = info.embedded;
    frame.hidden = false;
    standalone.href = info.standalone;
    standalone.hidden = false;
    status.textContent = '服务已就绪 · 成果由本人审阅';
  } catch (problem) {
    error.hidden = false;
    status.textContent = '等待连接';
    detail.textContent = problem.name === 'TimeoutError' || problem.name === 'AbortError'
      ? '连接超时，请确认每日工作台服务已启动后重试。'
      : problem instanceof TypeError ? '无法连接每日工作台，请确认服务和插件端口后重试。' : problem.message;
  } finally {
    connecting = false;
    retry.disabled = false;
  }
}

retry.addEventListener('click', connect);
connect();
