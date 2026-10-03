// 首屏防闪：在样式表生效前，按本机外观偏好（localStorage 'wb.prefs'）设好主题、密度、字号、圆角和强调色变量。
// 必须是普通同步脚本，放在 <head> 最前（样式表之前），不要改成 module / defer / async，否则首屏会先按默认外观画一帧再跳。
// index.html 与 hub.html 共用这一份（原来各写一段内联脚本，hub 版只设了主题和强调色，字号 / 密度 / 按钮字色要等 hub.js 加载后才对）。
// 逻辑是从 index.html 原样搬来的，没有改。完整规则（含取值区间校验、焦点环回退）以 prefs.js 的 apply() 为准，它在模块加载后会再覆盖一次；这里只求首帧接近。
// 预设强调色表 P 须与 prefs.js PRESETS、tokens.css 一致（tests/smoke_contrast_tokens.mjs 检查）。
try{
var p=JSON.parse(localStorage.getItem('wb.prefs')||'0'),t=(p&&p.theme)||{},r=document.documentElement,
m=t.mode&&t.mode!=='system'?t.mode:(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'),
D={compact:.85,comfortable:1,spacious:1.15},P={light:'#28664b',dark:'#77c49a',graphite:'#8fb0c9',sand:'#8e5b27',contrast:'#0a57f2'},
a=/^#[0-9a-f]{6}$/i.test(t.accent||'')?t.accent:(P[m]||P.light),c=[1,3,5].map(function(i){return parseInt(a.substr(i,2),16)});
r.dataset.theme=m;r.dataset.density=t.density||'comfortable';
[['--fs-scale',t.fontScale],['--r-scale',t.radius],['--sp-scale',D[t.density]],['--nav-w',t.navWidth?t.navWidth+'px':''],['--accent',a],
['--accent-soft','color-mix(in srgb,'+a+' 16%,var(--surface))'],['--accent-strong','color-mix(in srgb,'+a+' 72%,var(--text))'],
['--accent-ink',(0.2126*c[0]+0.7152*c[1]+0.0722*c[2])/255>0.55?'#12171b':'#ffffff']].forEach(function(e){if(e[1])r.style.setProperty(e[0],e[1])});
}catch(e){}
