from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import subprocess
from typing import Protocol

from PIL import Image

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


class AgentNativeCreoWorker:
    """Product-neutral adapter for Creo-native DisplayList arrow rendering."""

    _PREPARED_PATTERN = re.compile(
        r"^\[AGENT_RENDER\]\s+prepared_models\s+(.+?)\s*$",
        re.MULTILINE,
    )

    def __init__(
        self,
        *,
        powershell: str,
        batch_script: Path,
        models_root: Path,
        render_plan_json: Path,
        runner: CommandRunner | None = None,
    ) -> None:
        self.powershell = powershell
        self.batch_script = batch_script
        self.models_root = models_root
        self.render_plan_json = render_plan_json
        self.runner = runner or SubprocessCommandRunner()

    def open_session(self, run_workspace: Path, plan: RenderPlan) -> CreoSession:
        if plan.schema_version != "render-plan/v2":
            raise ValueError("Agent native worker requires render-plan/v2")
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
        if task.payload.get("execution_mode") != "formal":
            return RenderAttempt.failed("TASK_NOT_FORMAL")
        if task.payload.get("arrow_renderer") != "creo_display_list/v1":
            return RenderAttempt.failed("ARROW_RENDERER_NOT_FORMAL")
        plan_index = task.payload.get("plan_index")
        if not isinstance(plan_index, int) or plan_index < 0:
            return RenderAttempt.failed("INVALID_RENDER_TASK")
        command = [
            self.powershell,
            "-NoProfile",
            "-File",
            str(self.batch_script),
            "-ModelsRoot",
            str(self.models_root),
            "-RenderPlanJson",
            str(self.render_plan_json),
            "-OutputFolder",
            str(session.output_directory),
            "-StartIndex",
            str(plan_index),
            "-Count",
            "1",
        ]
        if session.prepared_models_root is not None:
            command.extend(["-PreparedModelsRoot", str(session.prepared_models_root)])
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
        image_path = session.output_directory / f"{task.task_id}.jpg"
        audit_path = session.output_directory / f"{task.task_id}.arrow.json"
        error = _validate_native_output(image_path, audit_path, task)
        if error is not None:
            return RenderAttempt.failed(error)
        return RenderAttempt.passed(f"sha256:{sha256(image_path.read_bytes()).hexdigest()}")

    def close_session(self, session: CreoSession) -> None:
        del session


def _validate_native_output(
    image_path: Path,
    audit_path: Path,
    task: RenderTask,
) -> str | None:
    if not image_path.is_file() or image_path.stat().st_size == 0:
        return "RENDER_OUTPUT_MISSING"
    try:
        with Image.open(image_path) as image:
            if image.size != (1600, 1600):
                return "RENDER_FRAME_INVALID"
            image.verify()
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return "ARROW_AUDIT_INVALID"
    if (
        audit.get("schema_version") != "arrow-projection/v1"
        or audit.get("policy") != "same_cad_point/v1"
        or audit.get("status") != "passed"
    ):
        return "ARROW_AUDIT_INVALID"
    arrows = audit.get("arrows")
    if not isinstance(arrows, list) or not arrows:
        return "ARROW_COVERAGE_INVALID"
    covered: list[str] = []
    expected_translation = task.payload.get("translation_vector_root")
    if not isinstance(expected_translation, (list, tuple)) or len(expected_translation) != 3:
        return "TRANSLATION_AUDIT_INVALID"
    for arrow in arrows:
        if not isinstance(arrow, dict):
            return "ARROW_AUDIT_INVALID"
        if arrow.get("anchor_source") == "occurrence_origin_fallback":
            return "ARROW_SURFACE_ANCHOR_UNAVAILABLE"
        covered.extend(str(value) for value in arrow.get("covered_occurrences", []))
        complete = arrow.get("complete_root")
        exploded = arrow.get("exploded_root")
        if (
            not isinstance(complete, list)
            or not isinstance(exploded, list)
            or len(complete) != 3
            or len(exploded) != 3
        ):
            return "TRANSLATION_AUDIT_INVALID"
        actual = [float(exploded[index]) - float(complete[index]) for index in range(3)]
        if any(
            not math.isclose(actual[index], float(expected_translation[index]), abs_tol=1.0e-5)
            for index in range(3)
        ):
            return "TRANSLATION_AUDIT_INVALID"
    if sorted(covered) != sorted(str(value) for value in task.payload.get("moving_occurrences", [])):
        return "ARROW_COVERAGE_INVALID"
    return None
