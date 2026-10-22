# runtime_exec：bus_mcp 的静默子进程执行器（Windows 优先，POSIX 降级）。
# 背景：bus_mcp 之前用默认 creation flags 从一个无控制台的父进程启动子进程，Windows 会为
# 每个控制台程序新分配一个控制台窗口，于是每次 ws_run、每次 bus_tool 都在屏幕上弹出黑框；
# 超时时只 kill 直接子进程，孙进程（node、py 启动器拉起的 python）会残留。
# 做法：CREATE_NO_WINDOW + STARTUPINFO 隐藏；Job Object（KILL_ON_JOB_CLOSE）让超时时整棵进程树
# 一起终止；流式读取只保留尾部 tail_bytes；Semaphore 限制并发；Job 不可用时退化为进程组 + taskkill /T。
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional, Sequence

IS_WIN = os.name == "nt"
RUN_TAIL = 8 * 1024
# 两条并发车道：bus 工具（wait/console 是长阻塞轮询，一个Agent占一个位）与工作区命令（ws_run）分开限流，
# 避免几个Agent的 wait 把车道占满后Lead的 console/status 排队等到超时而被判离线
MAX_CONCURRENT = 64
MAX_CONCURRENT_RUN = 12
_sem: Optional[asyncio.Semaphore] = None
_sem_run: Optional[asyncio.Semaphore] = None
_sem_loop: Optional[asyncio.AbstractEventLoop] = None

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_SUSPENDED = 0x00000004
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# ---- Windows Job Object plumbing (ctypes, no extra dependency) ---------------------------
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
    _PROCESS_ALL_ACCESS = 0x1FFFFF
    _THREAD_SUSPEND_RESUME = 0x0002

    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _k32.TerminateJobObject.restype = wintypes.BOOL
    _k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _k32.OpenThread.restype = wintypes.HANDLE
    _k32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _k32.ResumeThread.restype = wintypes.DWORD
    _k32.ResumeThread.argtypes = (wintypes.HANDLE,)

    def _make_job() -> Optional[int]:
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_BREAKAWAY_OK
        ok = _k32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            _k32.CloseHandle(job)
            return None
        return job

    def _assign(job: int, pid: int) -> bool:
        h = _k32.OpenProcess(_PROCESS_ALL_ACCESS, False, pid)
        if not h:
            return False
        try:
            return bool(_k32.AssignProcessToJobObject(job, h))
        finally:
            _k32.CloseHandle(h)

    def _kill_job(job: int) -> None:
        _k32.TerminateJobObject(job, 1)

    def _close(job: int) -> None:
        _k32.CloseHandle(job)


@dataclass
class RunResult:
    exit_code: object            # int, or the string "timeout" / "cancelled"
    tail: str
    mechanism: str               # "job" | "group" | "plain"
    truncated: bool = False
    notes: list = field(default_factory=list)

    def render(self, tail_kb: int = RUN_TAIL // 1024) -> str:
        head = f"exit_code: {self.exit_code}"
        if self.notes:
            head += "\n" + "\n".join(f"note: {n}" for n in self.notes)
        return f"{head}\n--- output (tail {tail_kb} KB) ---\n{self.tail}"


def _semaphore(lane: str = "bus") -> asyncio.Semaphore:
    global _sem, _sem_run, _sem_loop
    loop = asyncio.get_running_loop()
    if _sem is None or _sem_loop is not loop:
        _sem = asyncio.Semaphore(MAX_CONCURRENT)
        _sem_run = asyncio.Semaphore(MAX_CONCURRENT_RUN)
        _sem_loop = loop
    return _sem_run if lane == "run" else _sem


def creation_flags(quiet: bool = True) -> int:
    """Flags for any child spawned by the MCP server. Always no-window on Windows."""
    if not IS_WIN:
        return 0
    flags = CREATE_NEW_PROCESS_GROUP
    if quiet:
        flags |= CREATE_NO_WINDOW
    return flags


def popen_kwargs() -> dict:
    """Extra kwargs for subprocess.Popen / asyncio.create_subprocess_exec so the child
    never gets a console. Usable by callers that do not need the full run()."""
    kw = {"stdin": subprocess.DEVNULL, "close_fds": True}
    if IS_WIN:
        kw["creationflags"] = creation_flags()
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE, belt and braces for GUI-subsystem children
        kw["startupinfo"] = si
    else:
        kw["start_new_session"] = True
    return kw


class _TailBuffer:
    """Keeps only the last `limit` bytes; remembers whether anything was dropped."""

    def __init__(self, limit: int):
        self.limit = limit
        self.buf = bytearray()
        self.dropped = False

    def feed(self, chunk: bytes) -> None:
        self.buf += chunk
        if len(self.buf) > self.limit:
            del self.buf[: len(self.buf) - self.limit]
            self.dropped = True

    def text(self) -> str:
        return self.buf.decode("utf-8", "replace")


async def _pump(stream: asyncio.StreamReader, tail: _TailBuffer) -> None:
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return
        tail.feed(chunk)


def _tree_kill_fallback(proc: asyncio.subprocess.Process) -> None:
    """Best effort when no Job Object: kill the process group / use taskkill /T."""
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, **popen_kwargs())
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def run(argv: Sequence[str], *, cwd: str, timeout: float, env: Optional[dict] = None,
              stdin_data: Optional[bytes] = None, tail_bytes: int = RUN_TAIL,
              merge_stderr: bool = True, lane: str = "bus") -> RunResult:
    """Run argv quietly with a hard wall-clock timeout that terminates the whole tree.

    Returns RunResult; never raises for command failure. Raises only on programmer
    errors (bad argv type) or FileNotFoundError when argv[0] cannot be located.
    """
    notes: list = []
    async with _semaphore(lane):
        job = None
        mechanism = "plain"
        kw = popen_kwargs()
        if stdin_data is not None:
            kw["stdin"] = subprocess.PIPE
        if IS_WIN:
            job = _make_job()
            if job is not None:
                # Start suspended so the process cannot spawn children before it is
                # inside the job; resume once assigned.
                kw["creationflags"] |= CREATE_SUSPENDED
                mechanism = "job"
            else:
                mechanism = "group"
                notes.append("job object unavailable; falling back to process group")
        else:
            mechanism = "group"

        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
            **kw,
        )

        if IS_WIN and job is not None:
            if not _assign(job, proc.pid):
                notes.append("AssignProcessToJobObject failed; falling back to process group")
                _close(job)
                job = None
                mechanism = "group"
            _resume_all_threads(proc.pid)

        tail = _TailBuffer(tail_bytes)
        err_tail = _TailBuffer(tail_bytes) if not merge_stderr else None
        pumps = [asyncio.create_task(_pump(proc.stdout, tail))]
        if err_tail is not None:
            pumps.append(asyncio.create_task(_pump(proc.stderr, err_tail)))

        async def feed_stdin():
            if stdin_data is None:
                return
            try:
                proc.stdin.write(stdin_data)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        exit_code: object
        try:
            await feed_stdin()
            await asyncio.wait_for(proc.wait(), timeout=timeout)
            exit_code = proc.returncode
        except asyncio.TimeoutError:
            exit_code = "timeout"
            _terminate(proc, job)
        except asyncio.CancelledError:
            exit_code = "cancelled"
            _terminate(proc, job)
            raise
        finally:
            # Give pumps a moment to drain what is left, then stop them.
            # Not wait_for(gather(...)): if a second cancel lands here (MCP session teardown
            # cancels again), the gather future ends holding CancelledError that nobody reads
            # ("_GatheringFuture exception was never retrieved", 100+ times in the MCP log), and
            # the CancelledError escaping this finally skipped _close(job) below.
            recancel = False
            try:
                _done, pending = await asyncio.wait(pumps, timeout=3)
            except asyncio.CancelledError:
                pending, recancel = pumps, True
            for t in pending:
                t.cancel()
            for t in pumps:
                if t.done() and not t.cancelled():
                    t.exception()  # consume, so a failed pump is not reported as never retrieved
            if proc.returncode is None and not recancel:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                except asyncio.CancelledError:
                    recancel = True
            if job is not None:
                _close(job)  # KILL_ON_JOB_CLOSE reaps any straggler
            if recancel:
                raise asyncio.CancelledError

        text = tail.text()
        if err_tail is not None and err_tail.buf:
            text = (text + "\n" if text else "") + err_tail.text().strip()
        return RunResult(exit_code=exit_code, tail=text, mechanism=mechanism,
                         truncated=tail.dropped or bool(err_tail and err_tail.dropped), notes=notes)


def _terminate(proc: asyncio.subprocess.Process, job) -> None:
    if IS_WIN and job is not None:
        _kill_job(job)
        return
    _tree_kill_fallback(proc)


def _resume_all_threads(pid: int) -> None:
    """Resume a CREATE_SUSPENDED process (all its threads; a fresh process has one)."""
    if not IS_WIN:
        return
    import ctypes
    from ctypes import wintypes
    TH32CS_SNAPTHREAD = 0x00000004

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ThreadID", wintypes.DWORD),
                    ("th32OwnerProcessID", wintypes.DWORD), ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD)]

    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _k32.Thread32First.restype = wintypes.BOOL
    _k32.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(THREADENTRY32))
    _k32.Thread32Next.restype = wintypes.BOOL
    _k32.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(THREADENTRY32))
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap == wintypes.HANDLE(-1).value or not snap:
        return
    try:
        te = THREADENTRY32()
        te.dwSize = ctypes.sizeof(THREADENTRY32)
        ok = _k32.Thread32First(snap, ctypes.byref(te))
        while ok:
            if te.th32OwnerProcessID == pid:
                th = _k32.OpenThread(_THREAD_SUSPEND_RESUME, False, te.th32ThreadID)
                if th:
                    while True:
                        prev = _k32.ResumeThread(th)
                        if prev in (0, 1, 0xFFFFFFFF):
                            break
                    _k32.CloseHandle(th)
            ok = _k32.Thread32Next(snap, ctypes.byref(te))
    finally:
        _k32.CloseHandle(snap)


def self_check() -> dict:
    """Cheap capability report for /health and the workbench settings page."""
    info = {"platform": sys.platform, "job_objects": False, "no_window": IS_WIN, "max_concurrent": MAX_CONCURRENT, "max_concurrent_run": MAX_CONCURRENT_RUN}
    if IS_WIN:
        job = _make_job()
        info["job_objects"] = job is not None
        if job is not None:
            _close(job)
    return info
