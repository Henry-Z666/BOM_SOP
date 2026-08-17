from __future__ import annotations

import os
import signal
import subprocess
from typing import Any


def _windows_descendant_pids(root_pid: int) -> list[int]:
    """Snapshot descendants before the root can exit and be re-parented."""
    import ctypes
    from ctypes import wintypes

    process_entry = type(
        "PROCESSENTRY32W",
        (ctypes.Structure,),
        {
            "_fields_": [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]
        },
    )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(process_entry)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(process_entry)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot == invalid_handle:
        return []
    parents: dict[int, int] = {}
    entry = process_entry()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        more = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
        while more:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            more = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot)

    depths = {root_pid: 0}
    changed = True
    while changed:
        changed = False
        for pid, parent_pid in parents.items():
            if pid not in depths and parent_pid in depths:
                depths[pid] = depths[parent_pid] + 1
                changed = True
    return [
        pid
        for pid, _ in sorted(
            ((pid, depth) for pid, depth in depths.items() if pid != root_pid),
            key=lambda item: item[1],
            reverse=True,
        )
    ]


def _windows_force_terminate(pid: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not handle:
        return
    try:
        kernel32.TerminateProcess(handle, 1)
    finally:
        kernel32.CloseHandle(handle)


def owned_process_creation_kwargs() -> dict[str, Any]:
    """Return flags that give the caller a separately terminable process tree."""
    if os.name == "nt":
        return {
            "creationflags": (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        }
    return {"start_new_session": True}


def terminate_process_tree(
    process: subprocess.Popen[Any], *, timeout_seconds: float = 5.0
) -> bool:
    """Terminate an owned process and every descendant still attached to it."""
    if process.poll() is not None:
        return False

    if os.name == "nt":
        descendants = _windows_descendant_pids(process.pid)
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                process.kill()
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
        finally:
            # `taskkill /T` can be denied by a restricted parent process while
            # the direct child remains killable. The pre-kill snapshot keeps
            # descendant identities available even after Windows reparents them.
            for pid in descendants:
                _windows_force_terminate(pid)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.kill()

    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
        else:
            process.kill()
        process.wait(timeout=timeout_seconds)
    return True
