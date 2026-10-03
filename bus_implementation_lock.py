"""Reentrant process/thread lock for implementation check-and-enqueue transitions.

Separate from the controller lease mutex: no ownership or lease changes here.
The constant lock file must not be removed while services are running.
"""
import contextlib
import os
import threading
import time

_THREAD_LOCK = threading.RLock()
_LOCAL = threading.local()


@contextlib.contextmanager
def implementation_mutex(commands_dir, timeout=5.0):
    # A sibling of commands/, so queue scanners never mistake it for a command.
    path = os.path.join(os.path.dirname(commands_dir), 'implementation.mutex')
    with _THREAD_LOCK:
        if getattr(_LOCAL, 'depth', 0):
            if _LOCAL.path != path:
                raise ValueError('nested implementation lock path mismatch')
            _LOCAL.depth += 1
            try:
                yield
            finally:
                _LOCAL.depth -= 1
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a+b') as fh:
            if os.fstat(fh.fileno()).st_size == 0:
                fh.write(b'0')
                fh.flush()
            end = time.monotonic() + timeout
            while True:
                try:
                    if os.name == 'nt':
                        import msvcrt
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= end:
                        raise ValueError('实施队列正忙，请稍后重试；本次未入队')
                    time.sleep(0.02)
            _LOCAL.path, _LOCAL.depth = path, 1
            try:
                yield
            finally:
                _LOCAL.depth = 0
                if os.name == 'nt':
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
