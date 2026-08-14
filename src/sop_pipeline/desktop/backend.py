from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any


class SubprocessAgentBackend:
    """Runs each durable Agent command in a separate Python process."""

    def __init__(
        self,
        workspace: Path,
        *,
        python_executable: str | None = None,
        timeout_seconds: int = 24 * 60 * 60,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.python_executable = python_executable or sys.executable
        self.timeout_seconds = timeout_seconds
        self._process_lock = threading.Lock()
        self._current_process: subprocess.Popen[str] | None = None
        self._pause_requested = False

    def start_analysis(self, bom_file: Path, cad_directory: Path) -> dict[str, Any]:
        return self._call(
            "start-analysis",
            {"bom_file": str(bom_file), "cad_directory": str(cad_directory)},
        )

    def confirm(self, run_id: str, answers: dict[str, str]) -> dict[str, Any]:
        return self._call("confirm", {"run_id": run_id, "answers": answers})

    def generate(self, run_id: str) -> dict[str, Any]:
        return self._call("generate", {"run_id": run_id})

    def resolve(self, run_id: str, resolution: dict[str, Any]) -> dict[str, Any]:
        return self._call(
            "resolve", {"run_id": run_id, "resolution": resolution}
        )

    def resume(self, run_id: str) -> dict[str, Any]:
        return self._call("resume", {"run_id": run_id})

    def pause(self) -> bool:
        with self._process_lock:
            process = self._current_process
            if process is None or process.poll() is not None:
                return False
            self._pause_requested = True
            process.terminate()
            return True

    def _call(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if getattr(sys, "frozen", False):
            command = [self.python_executable, "--agent-worker"]
        else:
            command = [
                self.python_executable,
                "-m",
                "sop_pipeline.agent.worker_cli",
            ]
        command.extend(
            [
                "--workspace",
                str(self.workspace),
                "--action",
                action,
            ]
        )
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with self._process_lock:
            self._current_process = process
            self._pause_requested = False
        try:
            stdout, stderr = process.communicate(
                input=json.dumps(payload, ensure_ascii=False),
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise RuntimeError("Agent worker timed out")
        finally:
            with self._process_lock:
                paused = self._pause_requested
                self._current_process = None
                self._pause_requested = False
        if paused:
            return {
                "run_id": str(payload.get("run_id", "")),
                "status": "GENERATING",
                "paused": True,
            }
        try:
            response = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"Agent worker returned invalid output: {stderr.strip()}"
            ) from error
        if process.returncode != 0 or not response.get("ok"):
            raise RuntimeError(str(response.get("error", "Agent worker failed")))
        return dict(response["result"])
