// Agent profile editor: global routing hints and project aliases are separate transactions.
import { esc, attr } from './core.js';
const lines = value => Array.isArray(value) ? value.join('\n') : '';
export function uniqueLines(value) {
  return [...new Set(String(value || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean))];
}
export function profilePatch(form) {
  const data = new FormData(form);
  return {
    display_name: String(data.get('global_display_name') || '').trim(),
    scope_paths: uniqueLines(data.get('scope_paths')),
    strengths: { tags: uniqueLines(data.get('strength_tags')), note: String(data.get('strength_note') || '').trim() },
    task_prefs: uniqueLines(data.get('task_prefs')),
    paused: data.get('paused') === 'on',
    answer_style: String(data.get('answer_style') || '')
  };
}
export function profileEditor(peer, profile = {}, alias = {}) {
  const strengths = profile.strengths || {};
  return `<div class="stack">
  <form data-form="agent-profile" data-peer="${attr(peer)}" class="stack">
    <h3>全局 Agent 资料</h3><p class="muted" style="font-size:calc(12px * var(--fs-scale, 1))">用于推荐与任务包提示；负责路径不是文件访问权限。连接真名始终为 <code>${esc(peer)}</code>。</p>
    <div class="field"><label for="globalDisplayName">全局资料名</label><input id="globalDisplayName" name="global_display_name" maxlength="80" value="${attr(profile.display_name || '')}" placeholder="留空使用连接显示名"></div>
    <div class="field"><label for="agentScopes">负责路径（每行一个）</label><textarea id="agentScopes" name="scope_paths" rows="3" placeholder="web/v2\nbus_core.py">${esc(lines(profile.scope_paths))}</textarea></div>
    <div class="field"><label for="agentStrengths">擅长项（每行一个）</label><textarea id="agentStrengths" name="strength_tags" rows="3" placeholder="前端交互\n安全审查">${esc(lines(strengths.tags))}</textarea></div>
    <div class="field"><label for="agentStrengthNote">能力说明</label><textarea id="agentStrengthNote" name="strength_note" maxlength="4000" rows="3">${esc(strengths.note || '')}</textarea></div>
    <div class="field"><label for="agentTaskPrefs">个人任务偏好（从高到低，每行一个）</label><textarea id="agentTaskPrefs" name="task_prefs" rows="4" placeholder="P0 阻塞\n设计问题\n代码实现\n调研\n评审">${esc(lines(profile.task_prefs))}</textarea><span class="help">仅作为软推荐，不覆盖全局任务优先顺序，也不剥夺其他任务类型。</span></div>
    <div class="field"><label for="agentAnswerStyle">作答风格</label><select id="agentAnswerStyle" name="answer_style">${[['','跟随任务'],['redteam','红队审查'],['实现','实现'],['设计','设计']].map(([value, label]) => `<option value="${value}" ${profile.answer_style === value ? 'selected' : ''}>${label}</option>`).join('')}</select></div>
    <label class="row"><input type="checkbox" name="paused" ${profile.paused ? 'checked' : ''}>暂停自动推荐（全局，所有项目生效；不撤销已派任务，仍可手动点名）</label>
    <div class="row"><button type="submit" class="primary">保存全局资料</button></div>
  </form>
  <form data-form="agent-meta" data-peer="${attr(peer)}" class="stack profile-section">
    <h3>当前项目备注</h3><p class="muted" style="font-size:calc(12px * var(--fs-scale, 1))">只影响本项目；项目别名优先于全局资料名，不会修改协议参数。</p>
    <div class="field"><label for="agentDisplayName">本项目别名</label><input id="agentDisplayName" name="display_name" maxlength="80" value="${attr(alias.display_name || '')}"></div>
    <label class="row"><input type="checkbox" name="paused_in_project" ${alias.paused_in_project ? 'checked' : ''}>本项目暂停接单（只影响当前项目的默认勾选与「全员在线」展开）</label>
      <p class="muted" style="font-size:calc(12px * var(--fs-scale, 1))">取消暂停：把勾去掉再点一次保存。</p>
      <div class="field"><label for="agentNote">本项目备注</label><textarea id="agentNote" name="note" rows="3" maxlength="4000">${esc(alias.note || '')}</textarea></div>
    <div class="row"><button type="submit" class="primary">保存项目备注</button><button type="button" class="ghost" data-action="close-drawer">关闭</button></div>
  </form></div>`;
}
