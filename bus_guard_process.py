"""Read process identity for an already configured tunnel; never stop processes.

An unavailable/partial inventory is unknown, not permission to spawn another.
Only exact executable + command shape + token-file path matches are reusable.
"""
import ctypes
import json
import os
import subprocess

_QUERY = r"""$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); @(Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | Select-Object ProcessId,ExecutablePath,CommandLine) | ConvertTo-Json -Compress""".replace('\\"','"')


def command_args(command):
    if os.name != 'nt':
        raise RuntimeError('Windows process identity inspection required')
    from ctypes import wintypes
    shell=ctypes.WinDLL('shell32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    shell.CommandLineToArgvW.argtypes=(wintypes.LPCWSTR,ctypes.POINTER(ctypes.c_int))
    shell.CommandLineToArgvW.restype=ctypes.POINTER(wintypes.LPWSTR)
    kernel.LocalFree.argtypes=(wintypes.HLOCAL,);kernel.LocalFree.restype=wintypes.HLOCAL
    count=ctypes.c_int();pointer=shell.CommandLineToArgvW(command,ctypes.byref(count))
    if not pointer:raise RuntimeError('Process command could not be parsed')
    try:return [pointer[i] for i in range(count.value)]
    finally:kernel.LocalFree(pointer)


def canonical(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def matches(record, executable, token_file):
    if not isinstance(record,dict) or type(record.get('ProcessId')) is not int:
        raise RuntimeError('Incomplete tunnel process inventory')
    image=record.get('ExecutablePath');command=record.get('CommandLine')
    if not image or not command:
        raise RuntimeError('Tunnel process identity unavailable; no duplicate will be started')
    if canonical(image)!=canonical(executable):return False
    argv=command_args(command)
    if not argv or canonical(argv[0])!=canonical(executable):return False
    # This supervisor only owns the command shape it starts. Other instances
    # (system services, raw-token commands, different tunnels) are not adopted.
    if len(argv)!=6 or argv[1:5]!=['tunnel','--no-autoupdate','run','--token-file']:
        return False
    target=argv[5]
    return os.path.isabs(target) and canonical(target)==canonical(token_file)


def find_instances(executable, token_file):
    if os.name!='nt':raise RuntimeError('Tunnel supervisor requires Windows')
    try:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',_QUERY],
                              capture_output=True,timeout=15,creationflags=0x08000000)
        if result.returncode:raise RuntimeError('Process inventory command failed')
        payload=result.stdout.decode('utf-8-sig').strip()
        rows=json.loads(payload) if payload else []
        if isinstance(rows,dict):rows=[rows]
        if not isinstance(rows,list):raise ValueError('Invalid inventory')
        return sorted({r['ProcessId'] for r in rows if matches(r,executable,token_file)})
    except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
        raise RuntimeError('Tunnel process inventory unavailable; refusing blind restart') from None
