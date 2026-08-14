from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
import subprocess
from typing import Protocol

from .render_scheduler import RenderAttempt, RenderPlan, RenderTask


class CommandRunner(Protocol):
    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]: ...


class SubprocessCommandRunner:
    def __init__(self, timeout_seconds: int = 900) -> None:
        self.timeout_seconds = timeout_seconds

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
            check=False,
        )


@dataclass
class CreoSession:
    output_directory: Path
    prepared_models_root: Path | None = None


class PowerShellCreoWorker:
    """Adapter for the current J-Link runner; one model copy per session."""

    _PREPARED_PATTERN = re.compile(
        r"^\[BATCH\]\s+prepared_isolated_models\s+(.+?)\s*$",
        re.MULTILINE,
    )

    def __init__(
        self,
        *,
        powershell: str,
        stage_script: Path,
        product_config: Path,
        jobs_json: Path,
        runner: CommandRunner | None = None,
    ) -> None:
        self.powershell = powershell
        self.stage_script = stage_script
        self.product_config = product_config
        self.jobs_json = jobs_json
        self.runner = runner or SubprocessCommandRunner()

    def open_session(self, run_workspace: Path, plan: RenderPlan) -> CreoSession:
        del plan
        output_directory = run_workspace / "rendered"
        output_directory.mkdir(parents=True, exist_ok=True)
        return CreoSession(output_directory=output_directory)

    def render(
        self,
        session: CreoSession,
        task: RenderTask,
        attempt: int,
    ) -> RenderAttempt:
        del attempt
        contract_index = task.payload.get("contract_index")
        if not isinstance(contract_index, int) or contract_index < 0:
            return RenderAttempt.failed("INVALID_RENDER_TASK")

        command = [
            self.powershell,
            "-NoProfile",
            "-File",
            str(self.stage_script),
            "-ProductConfig",
            str(self.product_config),
            "-JobsJson",
            str(self.jobs_json),
            "-OutputFolder",
            str(session.output_directory),
            "-StartIndex",
            str(contract_index),
            "-Count",
            "1",
        ]
        if session.prepared_models_root is not None:
            command.extend(
                ["-PreparedModelsRoot", str(session.prepared_models_root)]
            )

        try:
            result = self.runner.run(command)
        except subprocess.TimeoutExpired:
            return RenderAttempt.retryable("CREO_TIMEOUT")
        except OSError:
            return RenderAttempt.retryable("CREO_PROCESS_ERROR")

        prepared_match = self._PREPARED_PATTERN.search(result.stdout or "")
        if prepared_match is not None:
            session.prepared_models_root = Path(prepared_match.group(1).strip())
        if result.returncode != 0:
            return RenderAttempt.retryable("CREO_RENDER_FAILED")

        output_file = session.output_directory / f"{task.task_id}.jpg"
        if not output_file.is_file() or output_file.stat().st_size == 0:
            return RenderAttempt.retryable("RENDER_OUTPUT_MISSING")
        digest = sha256(output_file.read_bytes()).hexdigest()
        return RenderAttempt.passed(f"sha256:{digest}")

    def close_session(self, session: CreoSession) -> None:
        del session
