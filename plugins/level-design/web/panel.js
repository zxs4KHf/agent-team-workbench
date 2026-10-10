'use strict';
const frame = document.getElementById('level-design');
const status = document.getElementById('connection');
const error = document.getElementById('connection-error');
const standalone = document.getElementById('standalone');

async function connect() {
  error.hidden = true;
  status.textContent = '正在连接关卡工作台…';
  try {
    const response = await fetch('/api/level-design/plugin-info', {signal: AbortSignal.timeout(10000)});
    const data = await response.json();
    if (!response.ok || data.id !== 'level-design' || !data.ready) throw new Error('请确认关卡服务正在运行，并核对插件端口。');
    const target = new URL(data.url);
    if (target.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(target.hostname)
        || target.username || target.password || target.pathname !== '/web/') throw new Error('关卡服务返回的页面地址无效。');
    frame.src = target.href;
    frame.hidden = false;
    standalone.href = target.origin + '/web/';
    standalone.hidden = false;
    status.textContent = '已连接 · 成果由本人审阅';
  } catch (problem) {
    frame.hidden = true;
    standalone.hidden = true;
    error.hidden = false;
    status.textContent = '等待连接';
    document.getElementById('error-detail').textContent = problem.message;
  }
}
document.getElementById('retry').addEventListener('click', connect);
connect();
