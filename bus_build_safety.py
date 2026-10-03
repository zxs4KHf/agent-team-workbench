"""Verified build lifecycle. Mutable branch names are never review authority.

This coordinates cooperating workbench processes, not arbitrary external editors.
The final workspace update is a normal fast-forward Git merge: no forced checkout,
reset, stash, or deletion of the user's dirty/untracked files.
"""
import os
import re
import subprocess
import tempfile
from pathlib import Path
from bus_locks import file_mutex


def _git(repo, *args, check=True):
    proc = subprocess.run(['git', '-c', 'core.quotepath=false', *args], cwd=repo,
                          capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=180, creationflags=0x08000000 if os.name == 'nt' else 0)
    if check and proc.returncode:
        raise RuntimeError('git ' + args[0] + ' failed: ' + (proc.stderr or proc.stdout)[-2000:])
    return proc.stdout if check else proc


def _commit(repo, value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{7,64}', value):
        raise ValueError('A concrete Git commit is required')
    return _git(repo, 'rev-parse', '--verify', value + '^{commit}').strip()


def review_target(task_id, teammate):
    import bus_build as BB
    rec = BB.record(task_id)
    tm = rec.get('teammates', {}).get(teammate)
    if not tm or not tm.get('commit'):
        raise ValueError('Builder has not submitted a commit')
    commit = _commit(rec['repo'], tm['commit'])
    main = _git(rec['repo'], 'rev-parse', 'HEAD').strip()
    base = _git(rec['repo'], 'merge-base', main, commit).strip()
    return {'task_id': task_id, 'teammate': teammate, 'commit': commit,
            'base': base, 'main_head': main}


def review_pack(task_id, teammate, max_chars=60000, target=None):
    import bus_build as BB
    rec = BB.record(task_id)
    target = target or review_target(task_id, teammate)
    if target['task_id'] != task_id or target['teammate'] != teammate:
        raise ValueError('Review target does not match the build')
    commit = _commit(rec['repo'], target['commit'])
    diff = _git(rec['repo'], 'diff', '--no-ext-diff', target['base'], commit)
    tm = rec['teammates'][teammate]
    header = ('# Review an exact commit\n\n'
              f'Build: {task_id}\nBuilder: {teammate}\n'
              f'Reviewed commit: {commit}\nDiff base: {target["base"]}\n'
              f'Main snapshot: {target["main_head"]}\n\n'
              'Reply first line PASS or RETURN. Do not edit the builder workspace. '
              'A PASS applies only to the exact commit above, never to a later branch tip.\n\n')
    import json
    header += '## Reported builder gates (verify, do not assume)\n' + json.dumps(tm.get('gates', {}), ensure_ascii=False) + '\n\n## Diff\n```diff\n'
    if len(diff) > max_chars:
        header += diff[:max_chars] + '\n```\nTRUNCATED: inspect the remaining diff before issuing PASS.\n'
    else:
        header += diff + '\n```\n'
    return header


def set_review(task_id, teammate, verdict, reviewer, note='', expected_commit=''):
    import bus_build as BB
    if verdict not in ('PASS', 'RETURN'):
        raise ValueError('Review verdict must be PASS or RETURN')
    if not reviewer or reviewer == teammate:
        raise ValueError('A distinct named reviewer is required')
    if not expected_commit:
        raise ValueError('Legacy/unbound review: an exact reviewed commit is required')
    if verdict == 'PASS':
        rec0 = BB.record(task_id) or {}
        tm0 = ((rec0.get('teammates') or {}).get(teammate) or {})
        # 返工点 7：builder 这次没有任何改动（HEAD 就是任务基线）时，PASS 没有可绑的产物 —— 拒绝，不记假 PASS。
        # RETURN 仍然放行：审查「什么都没交」正是要退回。
        if tm0.get('commit_unbound') or (tm0.get('commit') and tm0.get('commit') == rec0.get('base')):
            raise ValueError('Unbound commit: builder HEAD equals the task baseline (no change to review); PASS refused')
    with file_mutex(BB.record_path(task_id) + '.mutex'):
        rec = BB.record(task_id)
        tm = rec['teammates'][teammate]
        actual = _commit(rec['repo'], tm.get('commit'))
        expected = _commit(rec['repo'], expected_commit)
        if actual != expected:
            raise ValueError('Stale review: builder commit changed; request a new review')
        # 记下审查时 main 的位置：合并时若 main 已前进，据此标出「需重审」（WORKFLOW 规则 6）
        head = _git(rec['repo'], 'rev-parse', 'HEAD', check=False)
        tm.update(review=verdict, reviewer=reviewer, review_note=note,
                  reviewed_commit=expected, review_at=BB.B.now_iso(),
                  review_main=head.stdout.strip() if head.returncode == 0 else '')
        BB.save_record(task_id, rec)
        return tm


def rereview_hint(repo, base, commit, review_main, main_now):
    """审查后 main 是否前进、前进的部分是否碰了本分支改过的文件。只提示，不拦截（集成门禁照跑）。
    needed=None：旧记录没存 review_main，无法判断；False：main 没动；True：main 已前进，overlap_files 非空时风险高。"""
    if not review_main:
        return {'needed': None, 'message': '审查时未记录 main 位置（旧记录），无法判断是否需要重审'}
    if review_main == main_now:
        return {'needed': False, 'review_main': review_main}
    # 所有 git 调用都不许抛：这是提示，不能挡合并（fable2 a180）。记录的位置不可达（main 被 reset 回退、记录从备份恢复/跨机器搬运）时如实说无法判断
    if _git(repo, 'rev-parse', '--verify', '-q', f'{review_main}^{{commit}}', check=False).returncode != 0:
        return {'needed': None, 'review_main': review_main,
                'message': f'审查时记录的 main 位置 {review_main[:7]} 在仓库里已找不到（main 被回退过或记录被迁移），无法判断是否需要重审，请人工确认'}
    count = _git(repo, 'rev-list', '--count', f'{review_main}..{main_now}', check=False)
    ahead = int(count.stdout.strip() or 0) if count.returncode == 0 else None
    def names(a, b):  # 取不到返回 None，与「确实没改文件」的空集区分开
        r = _git(repo, 'diff', '--name-only', '-z', a, b, check=False)
        return {p for p in r.stdout.split('\0') if p} if r.returncode == 0 else None
    moved, mine = names(review_main, main_now), names(base, commit)
    if moved is None or mine is None:  # 没证据就不报「风险低」（fable2 1b23）
        return {'needed': None, 'review_main': review_main, 'main_now': main_now, 'main_advanced_by': ahead,
                'message': '审查后 main 已前进，但取不到双方改动的文件清单，无法判断是否碰了同一文件，请人工确认'}
    overlap = sorted(moved & mine)
    n = f'{ahead} 个' if ahead is not None else '若干个（数量无法确定）'
    msg = (f'审查后 main 前进了 {n}提交，其中改到了本分支也改过的文件：{", ".join(overlap[:8])}——合并结果未经审查，建议重审'
           if overlap else f'审查后 main 前进了 {n}提交（未碰本分支改过的文件），合并结果未经审查，风险较低')
    return {'needed': True, 'review_main': review_main, 'main_now': main_now, 'main_advanced_by': ahead,
            'overlap_files': overlap, 'risk': 'high' if overlap else 'low', 'message': msg}


def dirty_paths(repo):
    entries = _git(repo, 'status', '--porcelain=v1', '-z', '--untracked-files=all').split('\0')
    result = set()
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        result.add(path.replace('\\', '/'))
        if 'R' in status or 'C' in status:
            if index < len(entries):
                result.add(entries[index].replace('\\', '/'))
                index += 1
    return result


def merge(task_id, teammate, by='controller'):
    import bus_build as BB
    initial = BB.record(task_id)
    repo = initial['repo']
    common = _git(repo, 'rev-parse', '--git-common-dir').strip()
    common = os.path.abspath(os.path.join(repo, common))
    with file_mutex(os.path.join(common, 'workbench-merge.mutex'), timeout=10):
        with file_mutex(BB.record_path(task_id) + '.mutex'):
            rec = BB.record(task_id)
            tm = rec['teammates'][teammate]
            commit = _commit(repo, tm.get('commit'))
            if tm.get('review') != 'PASS' or tm.get('reviewed_commit') != commit:
                raise ValueError('Merge requires PASS bound to the exact current submitted commit')
            if not (tm.get('gates') or {}).get('ok'):
                raise ValueError('Builder gates are missing or failed')
            _git(repo, 'symbolic-ref', '--quiet', '--short', 'HEAD')
            before = _git(repo, 'rev-parse', 'HEAD').strip()
            parent = os.path.join(repo, '.wt', '_verified_merge')
            os.makedirs(parent, exist_ok=True)
            temporary = tempfile.mkdtemp(prefix=task_id + '-', dir=parent)
            added = False
            try:
                _git(repo, 'worktree', 'add', '--detach', temporary, before)
                added = True
                proc = _git(temporary, 'merge', '--no-ff', '-m',
                            f'merge reviewed {task_id}/{teammate} [{commit[:12]}]', commit, check=False)
                if proc.returncode:
                    conflicts = _git(temporary, 'diff', '--name-only', '--diff-filter=U').splitlines()
                    tm['merge_error'] = (proc.stderr or proc.stdout)[-2000:]
                    BB.save_record(task_id, rec)
                    return {'ok': False, 'conflicts': conflicts, 'reason': 'Git merge failed; main unchanged'}
                merged = _git(temporary, 'rev-parse', 'HEAD').strip()
                changed = [p for p in _git(temporary, 'diff', '--name-only', '-z', before, merged).split('\0') if p]
                overlap = sorted(dirty_paths(repo) & set(changed))
                if overlap:
                    return {'ok': False, 'conflicts': overlap, 'reason': 'Local changes overlap; main unchanged'}
                gates = BB.run_gates(temporary, changed)
                tm['integration_gates'] = gates
                tm['integration_base'] = before
                if not gates.get('ok'):
                    BB.save_record(task_id, rec)
                    return {'ok': False, 'reason': 'Integration gates failed; main unchanged', 'gates': gates}
                if _git(repo, 'rev-parse', 'HEAD').strip() != before:
                    return {'ok': False, 'reason': 'Main advanced; rerun integration against its new head'}
                # Git itself refuses dirty/untracked overlap. Never force the workspace.
                try:  # 纯提示：门禁已过、合并提交已生成，这里任何异常都不能让合并回滚（fable2 a180）
                    hint = rereview_hint(repo, rec.get('base') or before, commit, tm.get('review_main', ''), before)
                except Exception as e:  # 别删：里面虽都 check=False，_git 的 timeout=180 抛 TimeoutExpired 只有这里接得住（fable2 1b23）
                    hint = {'needed': None, 'message': f'重审判断失败（{type(e).__name__}: {str(e)[:120]}），请人工确认 main 是否在审查后前进'}
                _git(repo, 'merge', '--ff-only', merged)
                tm.update(merged=True, merge_commit=merged, merged_at=BB.B.now_iso(), merged_by=by, rereview=hint)
                tm.pop('merge_error', None)
                # 一单一个结果：任一成员合入即收口，其余成员是同题候选，不再算「在建」（否则记录永远停在 building）
                rec['status'] = 'merged'
                BB.save_record(task_id, rec)
                return {'ok': True, 'commit': merged, 'merge_commit': merged,
                        'reviewed_commit': commit, 'files': changed, 'gates': gates, 'rereview': hint}
            finally:
                if added:
                    # Only this function's newly-created disposable worktree is removed.
                    _git(repo, 'worktree', 'remove', '--force', temporary, check=False)
                else:
                    try:
                        Path(temporary).rmdir()
                    except OSError:
                        pass


def abandoned(rec, tm):
    """零交付的废单成员：分支相对基线 0 提交、工作副本干净、也没有 stash 里的差异（3089）。

    close() 原先只看 merged 标志，导致「改派后没人干活」的废单连清理都做不了——本次 5724 只能手工
    worktree remove + branch -D 再 close(remove_worktrees=False)。放宽必须保守：任何一项拿不到证据
    （git 命令失败、路径不在）都返回 False，宁可把 worktree 留着。
    """
    repo, branch, base, path = rec.get('repo'), tm.get('branch'), rec.get('base'), tm.get('path') or ''
    if not (repo and branch and base) or tm.get('commit'):
        return False
    counted = _git(repo, 'rev-list', '--count', base + '..' + branch, check=False)
    if counted.returncode or counted.stdout.strip() != '0':
        return False
    if not os.path.isdir(path):
        return False
    if dirty_paths(path):
        return False
    stashed = _git(path, 'stash', 'list', check=False)
    return not stashed.returncode and not stashed.stdout.strip()


def close(task_id, remove_worktrees=True):
    import bus_build as BB
    with file_mutex(BB.record_path(task_id) + '.mutex'):
        rec = BB.record(task_id)
        members = rec.get('teammates', {})
        if remove_worktrees:
            for name, tm in members.items():
                if not tm.get('merged') and not abandoned(rec, tm):
                    raise ValueError('Preserving unmerged builder worktree: ' + name)
                if os.path.isdir(tm.get('path', '')) and dirty_paths(tm['path']):
                    raise ValueError('Preserving dirty builder worktree: ' + name)
            for tm in members.values():
                if os.path.isdir(tm.get('path', '')):
                    _git(rec['repo'], 'worktree', 'remove', tm['path'])
                if tm.get('branch'):
                    _git(rec['repo'], 'branch', '-d', tm['branch'], check=False)
        rec['closed'] = BB.B.now_iso()
        rec['status'] = 'closed'
        rec['worktrees_retained'] = not remove_worktrees
        BB.save_record(task_id, rec)
        return True
