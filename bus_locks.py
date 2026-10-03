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

def _replace_posix(src, dst):
    """Windows：用 POSIX 语义改名覆盖目标（Win10 1709+，FileRenameInfoEx + FILE_RENAME_FLAG_POSIX_SEMANTICS）。
    普通 os.replace（MoveFileEx）只要目标被别人打开着就拒绝，工作台每秒读决策表、写方 1 秒内 6 次重试全失败（2026-10-03 实测）；
    POSIX 语义下，读方只要用 FILE_SHARE_DELETE 打开（bus_core._open_shared），替换照常成功，读方拿到的是打开那一刻的旧内容。
    不支持（老系统、非 NTFS）时返回 False，由调用方退回 os.replace。"""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    k32.SetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    k32.SetFileInformationByHandle.restype = wintypes.BOOL
    DELETE, SHARE_ALL, OPEN_EXISTING, NORMAL = 0x10000, 0x1 | 0x2 | 0x4, 3, 0x80
    FileRenameInfoEx, REPLACE_IF_EXISTS, POSIX_SEMANTICS = 22, 0x1, 0x2
    h = k32.CreateFileW(src, DELETE, SHARE_ALL, None, OPEN_EXISTING, NORMAL, None)
    if h in (None, wintypes.HANDLE(-1).value):
        return False
    try:
        name = os.fspath(dst)

        class Info(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("RootDirectory", wintypes.HANDLE), ("FileNameLength", wintypes.DWORD),
                        ("FileName", wintypes.WCHAR * (len(name) + 1))]
        info = Info(REPLACE_IF_EXISTS | POSIX_SEMANTICS, None, len(name) * 2, name)
        return bool(k32.SetFileInformationByHandle(h, FileRenameInfoEx, ctypes.byref(info), ctypes.sizeof(info)))
    finally:
        k32.CloseHandle(h)


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
                if os.name == "nt" and _replace_posix(tmp, path):
                    return
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
