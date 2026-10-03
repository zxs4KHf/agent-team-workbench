// 测试用：读「某个前端模块 + 从它拆出去的文件」的源码（ADR 0015 按页面拆模块后，钉源码的测试改从这里读，断言不变）。
// app.js ← app-*.js；views.js ← page-overview / running / decisions / artifacts；views2.js ← page-compose / agents / settings
import { readFileSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const JS = join(dirname(fileURLToPath(import.meta.url)), '..', 'web', 'v2', 'js');
export const SPLITS = {
  'app.js': f => f === 'main.js' || /^app-[\w-]+\.js$/.test(f),   // app.js 只是入口，本体在 main.js
  'views.js': f => ['page-overview.js', 'page-running.js', 'page-decisions.js', 'page-artifacts.js'].includes(f),
  'views2.js': f => ['page-compose.js', 'page-agents.js', 'page-settings.js'].includes(f),
};
export function frontSrc(name) {
  const extra = SPLITS[name] ? readdirSync(JS).filter(SPLITS[name]).sort() : [];
  return [name, ...extra].map(f => readFileSync(join(JS, f), 'utf8')).join('\n');
}
