// app-brand.js：从 app.js 拆出（ADR 0015，按页面拆模块；scripts/split_module.py 生成，函数体未改）
import * as Prefs from './prefs.js';
import { profileEditor, profilePatch } from './profiles_ui.js';
import { notify } from './notify.js';
// 应用控制器：数据同步（SSE 优先，轮询兜底）、路由、事件委托、抽屉内容
import { $, $$, esc, attr, icon, morph, animateNumber, viewTransition, url, api, textApi, markdown, fmtDate, relTime, clamp } from './core.js';
import { toast, openDrawer, closeDrawer, drawerOpen, confirmDialog, registerCommands, openCmdk, closeCmdk, cmdkOpen, bindCmdk, mountVirtualList, teamChips } from './components.js';
import { store, S, F, project, decisions, activeDecisions, runningDecisions, agentList, onlineAgents, dispatchableAgents, undispatchableReason, teamPeersOf, pickStats, pickStatsText, dispatchEstimate, DYNAMIC_TEAM, PROJECT_TEAM, agentByName, displayName, agentMeta, titleOf, displayTitle, parentOf, childrenOf, BUCKETS, artifactsOf, effectiveTeams, ensureComposer, defaultComposerPeers, countPending, winPath, progressOf, NAV, STATUS, PEER_STATE, EXEC_LABEL, looksLikeImage, taskOrder, agentWeights, WEIGHT_OPTIONS, secondsLeft, fmtLeft, defaultProject, stallInfo } from './model.js';
import { resolveAction, selectionAnchor, shortcutAllowed, isTypingTarget, digitView, debounce, toggleExtra, mergeBodyStale, parseEntity, entityPatch, mergeVisibleOrder, moveInList } from './interact.js';
import { renderOverview, renderRunning, renderDecisions, renderArtifacts, chip, implChip, implActions, taskListHtml, runningMeta, toggleStuck, briefOf, briefFeedback, setOvTab } from './views.js';
import { modeLabel, renderCompose, renderPeers, renderSettings, groupOrder, saveGroupOrder, groupOfModel, leaderBoard, relayCardHtml, accentHint, groupOfAgent, agentDetail, groupForm } from './views2.js';
import { HISTORY_NAV, HISTORY_ACTIONS, HOOKS as HIST_HOOKS, renderHistory, historyKeydown, startHistoryView, histState, readUrlFilters } from './history.js';

import { ACTIONS, PREF, applyPath, coerce, renderView, setPrefs, uploadAttachment } from './main.js';

export function renderBrand() { Prefs.applyBrand(Prefs.load()); }
// 导入前先算差异，让用户在确认框里看见到底改了什么；未确认前一份配置都不写
function prefsDiff(a, b, prefix = '') {
  const out = [];
  for (const k of Object.keys(b)) {
    const path = prefix ? `${prefix}.${k}` : k, x = a && a[k], y = b[k];
    if (y && typeof y === 'object' && !Array.isArray(y)) out.push(...prefsDiff(x || {}, y, path));
    else if (JSON.stringify(x) !== JSON.stringify(y)) out.push(`${path}：${JSON.stringify(x === undefined ? null : x)} → ${JSON.stringify(y)}`);
  }
  return out;
}
async function compressMark(file) {
  if (!file || typeof file.size !== 'number') throw new Error('没有选择图片');
  if (file.size > 5 * 1024 * 1024) throw new Error('原图超过 5 MB，请先缩小');
  if (file.type && !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) throw new Error('图标只支持 PNG/JPEG/WebP');
  const size = 96;
  let source, objectUrl = '';
  try {
    if (window.createImageBitmap) {
      try { source = await createImageBitmap(file); } catch (_) { /* Decode using Image below. */ }
    }
    if (!source) {
      objectUrl = URL.createObjectURL(file);
      source = await new Promise((resolve, reject) => {
        const image = new Image(); image.onload = () => resolve(image);
        image.onerror = () => reject(new Error('图片解码失败')); image.src = objectUrl;
      });
    }
    const width = source.width || source.naturalWidth, height = source.height || source.naturalHeight;
    if (!width || !height) throw new Error('图片尺寸无效');
    const side = Math.min(width, height), canvas = document.createElement('canvas');
    canvas.width = canvas.height = size;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('画布不可用');
    context.drawImage(source, (width-side)/2, (height-side)/2, side, side, 0, 0, size, size);
    const dataUrl = canvas.toDataURL('image/png'), bytes = Prefs.dataUrlBytes(dataUrl);
    return bytes > Prefs.MARK_DATAURL_MAX_BYTES ? { type: 'upload', dataUrl: '', bytes } : { type: 'data', dataUrl, bytes };
  } finally {
    source?.close?.();
    if (objectUrl) URL.revokeObjectURL(objectUrl);
  }
}
function openBrandEditor(tab = 'text') {
  const b = Prefs.load().brand;
  const tabs = [['text', '文字'], ['upload', '上传'], ['paste', '粘贴']];
  const pane = {
    text: `<div class="field"><label for="brandMarkText">图标文字（1–2 个字符）</label><div class="row"><input id="brandMarkText" value="${attr(b.mark.text || '')}" maxlength="2" autocomplete="off" spellcheck="false"><button type="button" class="primary" data-action="brand-set-text">保存文字</button></div><span class="help">按字符计，emoji 不会被切成半截。</span></div>`,
    upload: `<div class="field"><label>从本机选择图片</label><div class="row"><input type="file" id="brandMarkFile" accept="${'image/png,image/jpeg,image/webp'}"><button type="button" class="primary" data-action="brand-pick">压缩并套用</button></div><span class="help">压到 96×96（30px 显示 × 3 倍 DPR）后存成本机 dataUrl；超过 400 KB 改走工作台上传，只留相对路径。</span></div>`,
    paste: `<div class="field"><label>直接粘贴</label><div class="q" data-brand-drop style="min-height:56px;display:grid;place-items:center;border:1px dashed var(--line-strong);border-radius:8px;color:var(--muted);font-size:calc(12px * var(--fs-scale, 1))">在这个面板里按 Ctrl+V</div><span class="help">与派发图片同一条 paste 管道，落点是品牌图标。</span></div>`
  }[tab] || '';
  openDrawer({ title: '品牌图标', subtitle: '只改这台浏览器的显示', kind: 'brand',
    body: `<div class="row wrap" style="gap:6px;margin-bottom:10px">${tabs.map(([k, l]) => `<button type="button" class="fchip ${k === tab ? 'on' : ''}" data-action="brand-tab" data-tab="${k}">${l}</button>`).join('')}</div>${pane}<div class="field"><label for="brandTitle">标题（≤${PREF.titleMax} 字）</label><input id="brandTitle" value="${attr(b.title)}" maxlength="${PREF.titleMax}" autocomplete="off" spellcheck="false"></div><div class="field"><label for="brandSubtitle">副标题（≤${PREF.subtitleMax} 字）</label><div class="row"><input id="brandSubtitle" value="${attr(b.subtitle)}" maxlength="${PREF.subtitleMax}" autocomplete="off" spellcheck="false"><label class="switch" style="flex:none"><input type="checkbox" ${b.subtitleUpper ? 'checked' : ''} data-action="brand-upper"><span class="track"></span><span>显示为大写</span></label></div></div><div class="row"><button type="button" class="primary" data-action="brand-save-text">保存标题与副标题</button><button type="button" class="ghost" data-action="close-drawer">关闭</button></div>` });
  F.brandTab = tab;
}
// 动作表在文件末尾并入 ACTIONS（ACTIONS 是本模块后面的 const 声明，这里提前 Object.assign 会踩到暂时性死区）
export const PREF_ACTIONS = {
  'brand-tab': t => openBrandEditor(t.dataset.tab),
  'edit-brand': () => openBrandEditor(F.brandTab || 'text'),
  'brand-set-text': t => {
    const v = $('#brandMarkText')?.value || '';
    if (Array.from(v).length > 2) return toast('图标文字最多 2 个字符', 'warn');
    t.classList.add('busy');
    setPrefs(p => { p.brand.mark = { ...p.brand.mark, type: 'text', text: v }; return p; });
    toast('已套用文字图标', 'ok', { ms: 1400 }); closeDrawer();
  },
  'brand-pick': async t => {
    const file = $('#brandMarkFile')?.files?.[0];
    t.classList.add('busy');
    try { await setMarkFromFile(file); closeDrawer(); }
    catch (e) { toast(e.message, 'bad'); }
    finally { t.classList.remove('busy'); }
  },
  'brand-save-text': () => {
    const title = $('#brandTitle')?.value || '', sub = $('#brandSubtitle')?.value || '', upper = !!$('[data-action="brand-upper"]')?.checked;
    if (!Array.from(title).length || !Array.from(sub).length) return toast('标题与副标题不能为空', 'warn');
    if (Array.from(title).length > PREF.titleMax || Array.from(sub).length > PREF.subtitleMax) return toast(`标题最多 ${PREF.titleMax} 字、副标题最多 ${PREF.subtitleMax} 字`, 'warn');
    setPrefs(p => { p.brand.title = title; p.brand.subtitle = sub; p.brand.subtitleUpper = upper; return p; });
    toast('品牌已更新', 'ok', { ms: 1400 }); closeDrawer();
  },
  'brand-upper': t => { setPrefs(p => { p.brand.subtitleUpper = !!t.checked; return p; }); },
  'pref-preset': t => { setPrefs(p => { p.theme.mode = t.dataset.mode; return p; }); renderView(S()); },
  'pref-set': t => {
    const path = t.dataset.pref; if (!path) return;
    setPrefs(p => applyPath(p, path, coerce(path, t.dataset.value !== undefined ? t.dataset.value : t.value)));
    renderView(S());
  },
  'pref-accent-fix': () => { const cur = $('#prefAccent')?.value; const fixed = Prefs.accentFix(cur); if (!cur || fixed === cur) return; setPrefs(p => { p.theme.accent = fixed; return p; }); renderView(S()); toast(`强调色已在原色基础上调整为 ${fixed}（刚好达标）`, 'ok', { ms: 1800 }); },
  'pref-accent-follow': () => { setPrefs(p => { p.theme.accent = ''; return p; }); renderView(S()); },
  'export-prefs': () => {
    const text = Prefs.exportPrefs();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
    a.download = 'wb-prefs.json'; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    toast('外观配置已导出', 'ok', { ms: 1400 });
  },
  'import-prefs': async t => {
    const file = t.files?.[0] || $('#prefsFile')?.files?.[0];
    if (!file) return toast('先选择一份导出文件', 'warn');
    const text = await file.text();
    let next;
    try { next = Prefs.importPrefs(text); }
    catch (e) { return toast(e.message, 'bad'); }
    const diff = prefsDiff(Prefs.load(), next);
    if (!diff.length) return toast('这份配置与当前完全相同', 'ok', { ms: 1400 });
    if (!await confirmDialog({ title: '导入外观配置', body: `<span class="faint" style="font-size:calc(12px * var(--fs-scale, 1))">只有确认后才写入，取消不影响现状。</span><ul style="margin:8px 0 0;padding-left:18px;font-size:calc(12px * var(--fs-scale, 1));line-height:1.8">${diff.slice(0, 24).map(x => `<li class="mono">${esc(x)}</li>`).join('')}${diff.length > 24 ? `<li>…共 ${diff.length} 项</li>` : ''}</ul>`, confirmText: '导入' })) return;
    Prefs.commitImport(text); renderView(S()); toast('外观配置已导入', 'ok');
  },
  'reset-prefs': async t => {
    if (!await confirmDialog({ title: '恢复默认外观与品牌', body: '图标、标题、主题、字号、密度与侧栏宽度全部回到出厂值。导出过的配置可以再次导入。', confirmText: '恢复默认', danger: true })) return;
    Prefs.resetPrefs(); renderView(S()); toast('已恢复默认', 'ok');
  }
};
export async function setMarkFromFile(file) {
  const r = await compressMark(file);
  if (r.type === 'upload') {
    const up = await uploadAttachment(file);
    setPrefs(p => { p.brand.mark = { type: 'image', text: '', dataUrl: '', url: up.path }; return p; });
    toast('图片较大，已上传本机工作区并按路径引用', 'ok');
    return true;
  }
  setPrefs(p => { p.brand.mark = { type: 'image', text: '', dataUrl: r.dataUrl, url: '' }; return p; });
  toast('品牌图标已更新', 'ok', { ms: 1400 });
  return true;
}

