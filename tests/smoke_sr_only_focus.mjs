// .sr-only 的聚焦显示只给跳转链接；选择片复选框、文件框的焦点画在外层 label 上（fable2 1b23）
import { readFileSync, readdirSync } from 'node:fs';
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
const base = readFileSync(new URL('../web/v2/styles/base.css', import.meta.url), 'utf8');
const comp = readFileSync(new URL('../web/v2/styles/components.css', import.meta.url), 'utf8');
ok(/(^|\n)a\.sr-only:focus-visible\{[^}]*position:fixed/.test(base), '跳转链接聚焦时固定显示在左上角');
ok(!/(^|[\n,}])\s*\.sr-only:focus-visible/.test(base), '不对所有 .sr-only 放开（否则 Tab 到隐藏复选框会弹空白小方块）');
ok(/\.fchip:has\(input:focus-visible\)\{outline:2px solid var\(--focus-ring\)/.test(comp), '选择片：键盘聚焦复选框时整片出焦点环');
ok(/\.file-btn:has\(input:focus-visible\)\{outline:2px solid var\(--focus-ring\)/.test(comp), '文件按钮：键盘聚焦时出焦点环（与选择片同一写法，鼠标点击不出）');
// 扫全部前端 JS/HTML：label 里（不必紧跟）的任何 input.sr-only（复选/单选/文件…），外层都得是有焦点环规则的类（fable2 4419 非阻塞 1）
const RINGED = ['fchip', 'file-btn'];
const found = [];
const dir = new URL('../web/v2/js/', import.meta.url);
const srcs = [...readdirSync(dir).filter(f => f.endsWith('.js')).map(f => ['js/' + f, readFileSync(new URL(f, dir), 'utf8')]),
  ...['index.html', 'hub.html'].map(f => [f, readFileSync(new URL('../web/v2/' + f, import.meta.url), 'utf8')])];
for (const [f, s] of srcs) for (const m of s.matchAll(/<label[^>]*class="([^"]*)"[^>]*>(?:(?!<\/label>)[\s\S])*?<input[^>]*class="[^"]*\bsr-only\b/g)) found.push([f, m[1]]);
const bad = found.filter(([, c]) => !c.split(/\s+/).some(x => RINGED.includes(x)));  // 看全部类名，class="on fchip" 也算（fable2 df87）
ok(found.length >= 4 && !bad.length, `label 包的隐藏输入框 ${found.length} 处，外层都有焦点环规则（${[...new Set(found.map(x => x[1]))].join(',')}）` + (bad.length ? '；缺环：' + bad.map(x => x.join(':')).join(' ') : ''));
const probe = s => [...s.matchAll(/<label[^>]*class="([^"]*)"[^>]*>(?:(?!<\/label>)[\s\S])*?<input[^>]*class="[^"]*\bsr-only\b/g)].map(m => m[1]);
ok(probe('<label class="x"><span>y</span><input type="radio" class="sr-only extra"></label>').join() === 'x' && !probe('<label class="x">y</label><input class="sr-only">').length,
   '守卫自证：input 不紧跟 label、多类名、radio 都能抓到；label 外的不算');
console.log(`\n${fails ? fails + ' 项失败' : '全部通过'}`); process.exit(fails ? 1 : 0);
