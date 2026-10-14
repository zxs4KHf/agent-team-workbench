"""Local-only initial repository binding (ADR 0020); not a network authorization API.

Preview never writes. Apply needs a fresh plan digest and explicit operator intent,
uses the ordinary project mutex, and atomically persists binding + audit receipt.
Existing nonempty bindings cannot be redirected by this tool. It is not an OS sandbox.
"""
import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess

import bus_project_store as store
from bus_locks import atomic_write_json


class Conflict(ValueError):
    """The project or repository changed since preview; produce a new preview."""


def canonical(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def repository_identity(path: str) -> dict:
    if not isinstance(path, str) or not path.strip() or not os.path.isabs(path):
        raise ValueError('Select an explicit absolute local Git working-tree root')
    if path.startswith(('\\\\', '//')) or '\x00' in path:
        raise ValueError('Network and invalid repository paths are not supported')
    root = Path(canonical(path))
    if not root.is_dir() or not (root / '.git').exists():
        raise ValueError('Repository must already exist; this tool never initializes Git')
    # Git location overrides must not redirect inspection into a different repo.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    def git(*args):
        try:
            result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-C', str(root),
                                     'rev-parse', *args], env=env, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                                    timeout=10, creationflags=0x08000000 if os.name == 'nt' else 0)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('Git repository inspection failed; no binding changed') from None
        if result.returncode:
            raise ValueError('Git repository must have a valid committed HEAD')
        return result.stdout.strip()
    top = canonical(git('--show-toplevel'))
    if top != str(root):
        raise ValueError('Select the Git working-tree root, not a subdirectory')
    git_dir = canonical(git('--absolute-git-dir'))
    head = git('--verify', 'HEAD^{commit}')
    if not re.fullmatch(r'[0-9a-f]{40,64}', head):
        raise ValueError('Invalid committed repository identity')
    return {'root': top, 'git_dir': git_dir, 'head': head}


def _record(projects: str, slug: str):
    if not isinstance(projects, str) or not os.path.isabs(projects):
        raise ValueError('Specify an absolute project-store directory')
    path = Path(store.directory(projects, slug)) / 'project.json'
    if not path.is_file() or path.is_symlink():
        raise ValueError('Project record must exist as a regular, non-symlink file')
    try:
        with path.open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('Project record exceeds the local binding limit')
        record = json.loads(raw.decode('utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError('Project record unavailable or invalid; nothing changed') from None
    if not isinstance(record, dict) or record.get('slug') != slug:
        raise ValueError('Project identifier does not match its record')
    old = record.get('repo_dir', '')
    if not isinstance(old, str):
        raise ValueError('Existing repository binding must be text')
    return path, raw, record


def _plan(path, raw, record, identity):
    old = record.get('repo_dir', '')
    if old and (not os.path.isabs(old) or canonical(old) != identity['root']):
        raise ValueError('Refusing to redirect an existing binding; use a separately reviewed migration')
    body = {'schema': 1, 'project': record['slug'], 'project_record': canonical(str(path)),
            'project_sha256': hashlib.sha256(raw).hexdigest(), 'previous_repo_dir': old,
            'repository': identity, 'action': 'already_bound' if old else 'bind'}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    return dict(body, plan_id=digest, authorization='explicit local operator action required')


def preview(projects: str, slug: str, repository: str) -> dict:
    """Inspect one explicit target. No lock file, project, worktree or task is created."""
    path, raw, record = _record(projects, slug)
    return _plan(path, raw, record, repository_identity(repository))


def apply(projects: str, slug: str, repository: str, *, expected: str,
          confirm_project: str, actor: str, reason: str) -> dict:
    """CAS-style initial bind; the digest is not a secret or authentication token."""
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('A preview plan_id is required; preview again')
    if confirm_project != slug:
        raise ValueError('Confirm the exact project identifier before applying')
    for name, value, limit in [('actor', actor, 120), ('reason', reason, 1000)]:
        if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
            raise ValueError(name + ' must be nonempty bounded text')
    # Validate before acquiring a mutex so invalid/missing stores are never created.
    _record(projects, slug)
    with store.mutex(projects, slug):
        path, raw, record = _record(projects, slug)
        identity = repository_identity(repository)
        receipt = record.get('repo_binding_receipt')
        if (isinstance(receipt, dict) and receipt.get('plan_id') == expected
                and record.get('repo_dir') == identity['root']
                and receipt.get('repository', {}).get('git_dir') == identity['git_dir']):
            return {'changed': False, 'status': 'already_applied', 'project': slug,
                    'repo_dir': record['repo_dir'], 'plan_id': expected}
        plan = _plan(path, raw, record, identity)
        if not hmac.compare_digest(plan['plan_id'], expected):
            raise Conflict('Project or repository changed after preview; nothing was overwritten')
        if plan['action'] == 'already_bound':
            return {'changed': False, 'status': 'already_bound', 'project': slug,
                    'repo_dir': identity['root'], 'plan_id': expected}
        record['repo_dir'] = identity['root']
        record['repo_binding_receipt'] = {'schema': 1, 'plan_id': expected, 'actor': actor.strip(),
            'reason': reason.strip(), 'at': dt.datetime.now().astimezone().isoformat(),
            'previous_repo_dir': plan['previous_repo_dir'], 'before_sha256': plan['project_sha256'],
            'repository': identity, 'scope': 'initial-local-binding'}
        # One replacement persists both values. A write failure cannot leave only one updated.
        atomic_write_json(path, record)
        return {'changed': True, 'status': 'bound', 'project': slug,
                'repo_dir': identity['root'], 'plan_id': expected}
