from __future__ import annotations

import os
import signal
import subprocess
from typing import Any


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
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
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
