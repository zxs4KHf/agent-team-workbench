"""Workspace text operations for trusted agents, NOT an OS security sandbox.
Python execution remains host-equivalent. Credential paths are not served by file tools.
"""
import hashlib
import os
import tempfile
import threading
from pathlib import Path
from bus_locks import file_mutex

_LOCK = threading.RLock()
PROTECTED = ('.git', '.shuncode', '.cloudflared', '.ssh', '.private', '.workbench-locks')
# Relative to the served workspace; overridden by bus_mcp for a relocated workbench.
WRITE_PROTECTED = ('console', 'peers', 'workbench/bus/console', 'workbench/bus/peers')

def resolve(root, path, write=False):
    base = Path(root).resolve()
    full = (base / path).resolve()
    try:
        rel = full.relative_to(base).as_posix()
    except ValueError:
        raise ValueError('Path is outside the workspace')
    lower = rel.lower()
    parts = lower.split('/')
    if any(p in PROTECTED for p in parts):
        raise PermissionError('Protected directory')
    name = full.name.lower()
    if (name == '.env' or name.startswith('.env.') or name in
        ('mcp_token.txt', 'bridge.json', 'credentials.json', 'credentials', '.netrc', 'auth.json',
         'settings.json', 'workspace.json')
        or name.endswith(('.pem', '.key')) or ('token' in name and name.endswith(('.txt', '.json')))):
        raise PermissionError('Credential file is not available through workspace file tools')
    if write and any(lower == p or lower.startswith(p + '/') for p in WRITE_PROTECTED):
        raise PermissionError('Bus control state must be changed through bus tools')
    return full, rel

def version(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()

def read(root, paths, start_line=1, end_line=None, limit=204800):
    blocks = []
    for path in paths:
        try:
            full, rel = resolve(root, path)
            data = full.read_bytes()
            text = data.decode('utf-8-sig')
            if '\x00' in text:
                raise ValueError('Binary file is not supported; use a local image-capable agent')
            lines = text.splitlines()
            lo = max(1, int(start_line))
            hi = min(len(lines), int(end_line)) if end_line is not None else len(lines)
            chosen = '\n'.join(f'{i+1}: {lines[i]}' for i in range(lo-1, max(lo-1, hi)))
            raw = chosen.encode('utf-8')
            truncated = len(raw) > limit
            if truncated: chosen = raw[:limit].decode('utf-8', 'ignore')
            blocks.append(f'=== {rel} ===\nversion: {version(data)}\nlines: {lo}-{hi}/{len(lines)}\n{chosen}' + ('\n[TRUNCATED: request a smaller line range]' if truncated else ''))
        except (OSError, ValueError, UnicodeError) as exc:
            blocks.append(f'=== {path} ===\nERROR: {type(exc).__name__}: {exc}')
    return '\n\n'.join(blocks)

def _check(full, expected_version):
    exists = full.exists()
    data = full.read_bytes() if exists else None
    if expected_version == 'missing':
        if exists: raise ValueError('Conflict: file already exists')
    elif expected_version:
        if data is None or version(data) != expected_version:
            raise ValueError('Conflict: version changed; reread before writing')
    elif exists:
        raise ValueError('Overwriting requires expected_version from ws_read')
    return data

def _atomic(full, text):
    body = text.replace('\r\n', '\n').replace('\r', '\n').encode('utf-8')
    full.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.mcp-write-', dir=full.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, full)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return version(body)

def write(root, path, content, expected_version=None):
    full, rel = resolve(root, path, write=True)
    key = hashlib.sha256(os.path.normcase(str(full)).encode("utf-8")).hexdigest()
    with file_mutex(Path(root).resolve() / ".workbench-locks" / (key + ".mutex")):
        _check(full, expected_version)
        result = _atomic(full, content)
    return f'OK: {rel}\nversion: {result}'

def patch(root, path, old_text, new_text, expected_version):
    if not old_text: raise ValueError('old_text must not be empty')
    if not expected_version or expected_version == 'missing':
        raise ValueError('Patch requires an existing file version')
    full, rel = resolve(root, path, write=True)
    key = hashlib.sha256(os.path.normcase(str(full)).encode("utf-8")).hexdigest()
    with file_mutex(Path(root).resolve() / ".workbench-locks" / (key + ".mutex")):
        data = _check(full, expected_version)
        text = data.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
        old = old_text.replace('\r\n', '\n').replace('\r', '\n')
        if text.count(old) != 1: raise ValueError('old_text must match exactly once')
        result = _atomic(full, text.replace(old, new_text, 1))
    return f'OK: {rel}\nversion: {result}'
