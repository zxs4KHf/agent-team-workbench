"""Shared filesystem primitives for the local workbench (not an OS sandbox).

Permanent mutex files are never replaced/unlinked while services are running.
Lock order: implementation queue -> project -> individual file. Repository merge
locks must not be nested inside project/queue locks. All timeouts are bounded.
"""
import contextlib
import errno
import json
import os
import tempfile
import threading
import time

_registry = {}
_guard = threading.Lock()
_local = threading.local()

def _canonical(path):
    return os.path.normcase(os.path.realpath(os.fspath(path)))

@contextlib.contextmanager
def file_mutex(path, timeout=10.0):
    """Reentrant per-path thread + process mutex; OS releases it on crash."""
    path = _canonical(path)
    deadline = time.monotonic() + max(0.0, float(timeout))
    with _guard:
        lock = _registry.setdefault(path, threading.RLock())
    if not lock.acquire(timeout=max(0.0, deadline-time.monotonic())):
        raise TimeoutError('Resource lock busy: ' + os.path.basename(path))
    held = getattr(_local, 'held', None)
    if held is None:
        held = _local.held = {}
    fh = None
    acquired = False
    try:
        if held.get(path):
            held[path] += 1
            try:
                yield
            finally:
                held[path] -= 1
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh = open(path, 'a+b')
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    fh.seek(0)
                    # Windows permits locking past EOF. Do NOT initialize a byte
                    # before locking: concurrent initializers can hit WinError 33.
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError('Resource lock busy: ' + os.path.basename(path)) from exc
                time.sleep(min(0.02, max(0.0, deadline-time.monotonic())))
        held[path] = 1
        yield
    finally:
        if acquired:
            held.pop(path, None)
            try:
                if os.name == 'nt':
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()
        elif fh is not None:
            fh.close()
        lock.release()

def atomic_write_json(path, data, retries=6):
    """Unique temp + fsync + replace; failures propagate, never false success.

    Atomic replacement does NOT make a read-modify-write transaction. Callers
    protecting such a transaction must hold the corresponding file_mutex.
    """
    path = os.path.abspath(os.fspath(path))
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    body = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    fd, tmp = tempfile.mkstemp(prefix='.' + os.path.basename(path) + '.', suffix='.tmp', dir=parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(max(1, retries)):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt + 1 >= max(1, retries):
                    raise PermissionError("文件被占用，原文件已保留：" + os.path.basename(path))
                time.sleep(0.05 * (attempt + 1))
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
