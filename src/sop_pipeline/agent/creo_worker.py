from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import re
import subprocess
from typing import Protocol

from .render_scheduler import RenderAttempt, RenderPlan, RenderTask
from .render_validation import (
    DeterministicNativeRenderValidator,
    PRESENTATION_FAILURES,
)


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
    presentation_variant_by_task: dict[str, int] = field(default_factory=dict)
    attempted_presentation_variants: dict[str, set[int]] = field(default_factory=dict)


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
        validator: DeterministicNativeRenderValidator | None = None,
    ) -> None:
        self.powershell = powershell
        self.batch_script = batch_script
        self.models_root = models_root
        self.render_plan_json = render_plan_json
        self.runner = runner or SubprocessCommandRunner()
        self.validator = validator or DeterministicNativeRenderValidator()

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
        if task.payload.get("execution_mode") != "formal":
            return RenderAttempt.failed("TASK_NOT_FORMAL")
        if task.payload.get("arrow_renderer") != "creo_display_list/v1":
            return RenderAttempt.failed("ARROW_RENDERER_NOT_FORMAL")
        plan_index = task.payload.get("plan_index")
        if not isinstance(plan_index, int) or plan_index < 0:
            return RenderAttempt.failed("INVALID_RENDER_TASK")
        variant_index = session.presentation_variant_by_task.get(task.task_id, 0)
        session.attempted_presentation_variants.setdefault(task.task_id, set()).add(
            variant_index
        )
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
            "-VariantIndex",
            str(variant_index),
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
        report = self.validator.validate(
            image_path,
            audit_path,
            task.payload,
            variant_index=variant_index,
        )
        if not report.passed:
            error = report.failures[0]
            next_variant = _next_presentation_variant(
                task.payload,
                current_index=variant_index,
                attempted=session.attempted_presentation_variants[task.task_id],
                failures=report.failures,
            )
            if next_variant is not None and attempt < 3:
                session.presentation_variant_by_task[task.task_id] = next_variant
                return RenderAttempt.retryable(error)
            return RenderAttempt.failed(error)
        return RenderAttempt.passed(f"sha256:{sha256(image_path.read_bytes()).hexdigest()}")

    def close_session(self, session: CreoSession) -> None:
        del session


def _next_presentation_variant(
    payload: dict,
    *,
    current_index: int,
    attempted: set[int],
    failures: tuple[str, ...],
) -> int | None:
    if not any(item in PRESENTATION_FAILURES for item in failures):
        return None
    variants = payload.get("presentation", {}).get("variants", [])
    if not isinstance(variants, list) or not (0 <= current_index < len(variants)):
        return None
    try:
        current_zoom = float(variants[current_index]["zoom"])
    except (KeyError, TypeError, ValueError):
        return None
    candidates: list[tuple[float, int]] = []
    failure_set = set(failures)
    grow = bool({"SUBJECT_TOO_SMALL", "ARROW_TOO_SMALL"} & failure_set)
    shrink = bool(
        {
            "SUBJECT_NOT_DETECTED",
            "SUBJECT_TOO_LARGE",
            "SUBJECT_CLIPPED",
            "EXCESSIVE_CONTEXT_CLIPPING",
            "ARROW_NOT_VISIBLE",
            "ARROW_CLIPPED",
        }
        & failure_set
    ) and not grow
    for index, variant in enumerate(variants):
        if index in attempted or not isinstance(variant, dict):
            continue
        try:
            zoom = float(variant["zoom"])
        except (KeyError, TypeError, ValueError):
            continue
        if shrink and zoom < current_zoom:
            candidates.append((-zoom, index))
        elif not shrink and grow and zoom > current_zoom:
            candidates.append((zoom, index))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]
