// 决策 20260929-181437-30f6 · brief 第 3 步：卡片类型规则的前端一半——briefKindOf 与后端 brief_kind_of 共用 tests/brief_kinds_fixture.json。
// 不起服务：复制 web/v2/js 到临时目录（pretext 打桩）+ 最小 DOM 垫片，真实导入 views.js。
// 跑法：node tests/smoke_brief_kinds_30f6.mjs
import { mkdtempSync, mkdirSync, cpSync, writeFileSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
// 仓库根从本文件位置推，不用 process.cwd()：worktree 里跑时 cwd 可能是主仓
const repo = dirname(dirname(fileURLToPath(import.meta.url)));
const tmp = mkdtempSync(join(tmpdir(), 'bus30f6r-ui-'));
cpSync(join(repo, 'web/v2/js'), join(tmp, 'web/v2/js'), { recursive: true });
mkdirSync(join(tmp, 'web/v2/vendor/pretext'), { recursive: true });
cpSync(join(repo, 'web/v2/vendor/idiomorph.esm.js'), join(tmp, 'web/v2/vendor/idiomorph.esm.js'));
writeFileSync(join(tmp, 'web/v2/vendor/pretext/layout.js'), 'export const prepare = () => ({}); export const layout = () => ({ lines: [] });');
process.on('exit', () => rmSync(tmp, { recursive: true, force: true }));
const ls = new Map();
globalThis.localStorage = { getItem: k => ls.has(k) ? ls.get(k) : null, setItem: (k, v) => ls.set(k, String(v)), removeItem: k => ls.delete(k) };
globalThis.location = { search: '', pathname: '/', hash: '', href: 'http://x/' };
globalThis.history = { state: null, pushState() {}, replaceState() {} };
globalThis.matchMedia = () => ({ matches: false });
globalThis.requestAnimationFrame = fn => fn();
globalThis.document = { querySelector: () => null, querySelectorAll: () => [], createElement: () => ({ style: {}, classList: { add() {}, remove() {} }, setAttribute() {}, querySelector: () => ({}) }), activeElement: null, body: { style: {} }, addEventListener() {}, hidden: false };
globalThis.window = globalThis;
const mod = rel => import(pathToFileURL(join(tmp, rel)).href);
const { store: st } = await mod('web/v2/js/model.js');
const V = await mod('web/v2/js/views.js');
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
const fixture = JSON.parse(readFileSync(join(repo, 'tests/brief_kinds_fixture.json'), 'utf8')).cases;
ok(fixture.length >= 8, `共用用例有 ${fixture.length} 条`);
for (const c of fixture) ok(V.briefKindOf(c.d) === c.kind, `briefKindOf：${c.name} → ${c.kind}`);


console.log(fails ? `\n${fails} FAILED` : '\nALL PASS'); process.exit(fails ? 1 : 0);
