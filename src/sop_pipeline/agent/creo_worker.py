from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import subprocess
from typing import Protocol

from .framing_recovery import FramingRecoveryError, derive_zoom_for_subject_span
from .render_scheduler import RenderAttempt, RenderPlan, RenderTask
from .render_validation import (
    DeterministicNativeRenderValidator,
    NativeRenderGateReport,
    PRESENTATION_FAILURES,
)
from .screen_centering import (
    ScreenCenteringError,
    ScreenPanResponse,
    activity_focus_center,
    measure_screen_pan_response,
    plan_screen_center_probes,
    solve_with_screen_pan_response,
    update_screen_pan_response,
)


CENTERING_FAILURES = frozenset({"ACTIVITY_NOT_CENTERED", "ARROW_NOT_CENTERED"})


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
    internal_directory: Path | None = None
    prepared_models_root: Path | None = None
    presentation_variant_by_task: dict[str, int] = field(default_factory=dict)
    attempted_presentation_variants: dict[str, set[int]] = field(default_factory=dict)
    screen_pan_responses: dict[str, ScreenPanResponse] = field(default_factory=dict)


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
        internal_directory = run_workspace / "internal" / "screen-centering"
        internal_directory.mkdir(parents=True, exist_ok=True)
        session = CreoSession(
            output_directory=output_directory,
            internal_directory=internal_directory,
        )
        session.screen_pan_responses.update(_load_screen_pan_responses(internal_directory))
        return session

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
        execution_error = self._run_batch(
            session,
            plan_path=self.render_plan_json,
            output_directory=session.output_directory,
            start_index=plan_index,
            count=1,
            variant_index=variant_index,
        )
        if execution_error is not None:
            return RenderAttempt.retryable(execution_error)
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
            if (
                CENTERING_FAILURES.intersection(report.failures)
                and report.composition is not None
                and report.composition.center_pixel is not None
            ):
                return self._recover_screen_centering(
                    session,
                    task,
                    variant_index=variant_index,
                    base_report=report,
                )
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

    def _recover_screen_centering(
        self,
        session: CreoSession,
        task: RenderTask,
        *,
        variant_index: int,
        base_report: NativeRenderGateReport,
        variant_override: dict | None = None,
        zoom_round: int = 0,
    ) -> RenderAttempt:
        if session.internal_directory is None:
            return RenderAttempt.failed("SCREEN_CENTERING_STATE_UNAVAILABLE")
        try:
            presentation = task.payload["presentation"]
            contract = presentation["centering"]
            variant = variant_override or presentation["variants"][variant_index]
            target = tuple(float(value) for value in contract["target_pixel"])
            probe_delta = float(contract["probe_delta"])
            max_abs_pan = float(contract["max_abs_pan"])
            max_rounds = int(contract["max_probe_rounds"])
            zoom = float(variant["zoom"])
            camera_id = str(variant["camera_id"])
            base_pan = tuple(float(value) for value in variant["pan"])
            subject_only = not _has_arrow_center(base_report)
            base_center = _report_focus_center(
                base_report, subject_only=subject_only
            )
            cache_key = _screen_pan_response_key(
                task.payload,
                camera_id=camera_id,
                zoom=zoom,
            )
        except (KeyError, IndexError, TypeError, ValueError, ScreenCenteringError):
            return RenderAttempt.failed("SCREEN_CENTERING_EVIDENCE_MISSING")

        response = session.screen_pan_responses.get(cache_key)
        if response is not None:
            try:
                cached_pan = _solve_pan(
                    target, base_pan, base_center, response, max_abs_pan
                )
            except ScreenCenteringError:
                session.screen_pan_responses.pop(cache_key, None)
                _save_screen_pan_responses(
                    session.internal_directory, session.screen_pan_responses
                )
                response = None
        if response is not None:
            corrected = self._render_centered_variant(
                session,
                task,
                camera_id=camera_id,
                zoom=zoom,
                pan=cached_pan,
                label="cached",
            )
            if isinstance(corrected, RenderAttempt):
                return corrected
            corrected_report, corrected_pan = corrected
            if corrected_report.passed:
                return _passed_image(session.output_directory / f"{task.task_id}.jpg")
            if not CENTERING_FAILURES.intersection(corrected_report.failures):
                return self._recover_centered_zoom(
                    session,
                    task,
                    report=corrected_report,
                    camera_id=camera_id,
                    zoom=zoom,
                    pan=corrected_pan,
                    zoom_round=zoom_round,
                )
            session.screen_pan_responses.pop(cache_key, None)
            _save_screen_pan_responses(
                session.internal_directory, session.screen_pan_responses
            )
            base_report = corrected_report
            base_pan = corrected_pan
            subject_only = not _has_arrow_center(corrected_report)
            base_center = _report_focus_center(
                corrected_report, subject_only=subject_only
            )

        for round_index in range(1, 2):
            try:
                probe_plan = plan_screen_center_probes(
                    base_pan=base_pan,
                    probe_delta=probe_delta,
                    max_abs_pan=max_abs_pan,
                    target_pixel=(target[0], target[1]),
                    base_center=base_center,
                )
            except ScreenCenteringError:
                return RenderAttempt.failed("SCREEN_CENTERING_UNSOLVABLE")
            probes = self._render_centering_probes(
                session,
                task,
                camera_id=camera_id,
                zoom=zoom,
                round_index=round_index,
                x_pan=probe_plan.x_probe_pan,
                y_pan=probe_plan.y_probe_pan,
            )
            if isinstance(probes, RenderAttempt):
                return probes
            x_report, y_report = probes
            try:
                response = measure_screen_pan_response(
                    base_pan=base_pan,
                    base_center=base_center,
                    x_probe_pan=probe_plan.x_probe_pan,
                    x_probe_center=_report_focus_center(
                        x_report, subject_only=subject_only
                    ),
                    y_probe_pan=probe_plan.y_probe_pan,
                    y_probe_center=_report_focus_center(
                        y_report, subject_only=subject_only
                    ),
                )
                solved_pan = _solve_pan(
                    target, base_pan, base_center, response, max_abs_pan
                )
            except ScreenCenteringError:
                return RenderAttempt.failed("SCREEN_CENTERING_UNSOLVABLE")
            corrected = self._render_centered_variant(
                session,
                task,
                camera_id=camera_id,
                zoom=zoom,
                pan=solved_pan,
                label=f"round-{round_index}",
            )
            if isinstance(corrected, RenderAttempt):
                return corrected
            corrected_report, corrected_pan = corrected
            if (
                not CENTERING_FAILURES.intersection(corrected_report.failures)
                and not subject_only
            ):
                session.screen_pan_responses[cache_key] = response
                _save_screen_pan_responses(
                    session.internal_directory, session.screen_pan_responses
                )
            if corrected_report.passed:
                return _passed_image(session.output_directory / f"{task.task_id}.jpg")
            if not CENTERING_FAILURES.intersection(corrected_report.failures):
                return self._recover_centered_zoom(
                    session,
                    task,
                    report=corrected_report,
                    camera_id=camera_id,
                    zoom=zoom,
                    pan=corrected_pan,
                    zoom_round=zoom_round,
                )
            if max_rounds < 2:
                return RenderAttempt.failed(corrected_report.failures[0])
            try:
                corrected_center = _report_focus_center(
                    corrected_report, subject_only=subject_only
                )
                response = update_screen_pan_response(
                    response=response,
                    prior_pan=base_pan,
                    prior_center=base_center,
                    observed_pan=corrected_pan,
                    observed_center=corrected_center,
                )
                secant_pan = _solve_pan(
                    target,
                    corrected_pan,
                    corrected_center,
                    response,
                    max_abs_pan,
                )
            except ScreenCenteringError:
                return RenderAttempt.failed("SCREEN_CENTERING_UNSOLVABLE")
            secant = self._render_centered_variant(
                session,
                task,
                camera_id=camera_id,
                zoom=zoom,
                pan=secant_pan,
                label="secant-2",
            )
            if isinstance(secant, RenderAttempt):
                return secant
            secant_report, secant_pan = secant
            if not CENTERING_FAILURES.intersection(secant_report.failures):
                session.screen_pan_responses[cache_key] = response
                _save_screen_pan_responses(
                    session.internal_directory, session.screen_pan_responses
                )
            if secant_report.passed:
                return _passed_image(session.output_directory / f"{task.task_id}.jpg")
            if not CENTERING_FAILURES.intersection(secant_report.failures):
                return self._recover_centered_zoom(
                    session,
                    task,
                    report=secant_report,
                    camera_id=camera_id,
                    zoom=zoom,
                    pan=secant_pan,
                    zoom_round=zoom_round,
                )
            return RenderAttempt.failed(secant_report.failures[0])
        return RenderAttempt.failed(base_report.failures[0])

    def _recover_centered_zoom(
        self,
        session: CreoSession,
        task: RenderTask,
        *,
        report: NativeRenderGateReport,
        camera_id: str,
        zoom: float,
        pan: tuple[float, float],
        zoom_round: int,
    ) -> RenderAttempt:
        if set(report.failures) != {"SUBJECT_TOO_SMALL"}:
            return RenderAttempt.failed(report.failures[0])
        try:
            contract = task.payload["presentation"]["zoom_recovery"]
            max_rounds = int(contract["max_rounds"])
            if zoom_round >= max_rounds:
                return RenderAttempt.failed("SUBJECT_TOO_SMALL")
            if report.composition is None:
                raise FramingRecoveryError("subject metrics are unavailable")
            derived_zoom = derive_zoom_for_subject_span(
                current_zoom=zoom,
                observed_span=report.composition.max_span_fraction,
                target_span=float(contract["target_subject_span"]),
                min_zoom=float(contract["min_zoom"]),
                max_zoom=float(contract["max_zoom"]),
            )
            if derived_zoom <= zoom + 1.0e-6:
                raise FramingRecoveryError("derived Zoom does not increase")
        except (KeyError, TypeError, ValueError, FramingRecoveryError):
            return RenderAttempt.failed("ZOOM_RECOVERY_UNSOLVABLE")

        zoomed = self._render_centered_variant(
            session,
            task,
            camera_id=camera_id,
            zoom=derived_zoom,
            pan=pan,
            label=f"zoom-{zoom_round + 1}",
        )
        if isinstance(zoomed, RenderAttempt):
            return zoomed
        zoomed_report, zoomed_pan = zoomed
        if zoomed_report.passed:
            return _passed_image(session.output_directory / f"{task.task_id}.jpg")
        if CENTERING_FAILURES.intersection(zoomed_report.failures):
            return self._recover_screen_centering(
                session,
                task,
                variant_index=0,
                base_report=zoomed_report,
                variant_override={
                    "camera_id": camera_id,
                    "zoom": derived_zoom,
                    "pan": [zoomed_pan[0], zoomed_pan[1]],
                },
                zoom_round=zoom_round + 1,
            )
        return self._recover_centered_zoom(
            session,
            task,
            report=zoomed_report,
            camera_id=camera_id,
            zoom=derived_zoom,
            pan=zoomed_pan,
            zoom_round=zoom_round + 1,
        )

    def _render_centering_probes(
        self,
        session: CreoSession,
        task: RenderTask,
        *,
        camera_id: str,
        zoom: float,
        round_index: int,
        x_pan: tuple[float, float],
        y_pan: tuple[float, float],
    ) -> tuple[NativeRenderGateReport, NativeRenderGateReport] | RenderAttempt:
        assert session.internal_directory is not None
        safe_task_id = _safe_name(task.task_id)
        probe_directory = (
            session.internal_directory / safe_task_id / f"round-{round_index}-probes"
        )
        probe_directory.mkdir(parents=True, exist_ok=True)
        plan_path = probe_directory / "render-plan.json"
        probe_specs = (
            (f"{safe_task_id}__center_x", x_pan),
            (f"{safe_task_id}__center_y", y_pan),
        )
        payloads = _write_transient_render_plan(
            plan_path,
            task,
            camera_id=camera_id,
            zoom=zoom,
            task_specs=probe_specs,
        )
        error = self._run_batch(
            session,
            plan_path=plan_path,
            output_directory=probe_directory,
            start_index=0,
            count=2,
            variant_index=0,
        )
        if error is not None:
            return RenderAttempt.retryable(error)
        reports = tuple(
            self.validator.validate(
                probe_directory / f"{task_id}.jpg",
                probe_directory / f"{task_id}.arrow.json",
                payload,
                variant_index=0,
            )
            for (task_id, _), payload in zip(probe_specs, payloads, strict=True)
        )
        if any(
            report.composition is None
            or report.composition.center_pixel is None
            for report in reports
        ):
            return RenderAttempt.failed("SCREEN_CENTERING_PROBE_INVALID")
        return reports[0], reports[1]

    def _render_centered_variant(
        self,
        session: CreoSession,
        task: RenderTask,
        *,
        camera_id: str,
        zoom: float,
        pan: tuple[float, float],
        label: str,
    ) -> tuple[NativeRenderGateReport, tuple[float, float]] | RenderAttempt:
        assert session.internal_directory is not None
        plan_directory = session.internal_directory / _safe_name(task.task_id)
        plan_directory.mkdir(parents=True, exist_ok=True)
        plan_path = plan_directory / f"render-plan-{_safe_name(label)}.json"
        payload = _write_transient_render_plan(
            plan_path,
            task,
            camera_id=camera_id,
            zoom=zoom,
            task_specs=((task.task_id, pan),),
        )[0]
        error = self._run_batch(
            session,
            plan_path=plan_path,
            output_directory=session.output_directory,
            start_index=0,
            count=1,
            variant_index=0,
        )
        if error is not None:
            return RenderAttempt.retryable(error)
        report = self.validator.validate(
            session.output_directory / f"{task.task_id}.jpg",
            session.output_directory / f"{task.task_id}.arrow.json",
            payload,
            variant_index=0,
        )
        return report, pan

    def _run_batch(
        self,
        session: CreoSession,
        *,
        plan_path: Path,
        output_directory: Path,
        start_index: int,
        count: int,
        variant_index: int,
    ) -> str | None:
        command = [
            self.powershell,
            "-NoProfile",
            "-File",
            str(self.batch_script),
            "-ModelsRoot",
            str(self.models_root),
            "-RenderPlanJson",
            str(plan_path),
            "-OutputFolder",
            str(output_directory),
            "-StartIndex",
            str(start_index),
            "-Count",
            str(count),
            "-VariantIndex",
            str(variant_index),
            "-RunWorkspaceRoot",
            str(session.output_directory.parent),
        ]
        if session.prepared_models_root is not None:
            command.extend(["-PreparedModelsRoot", str(session.prepared_models_root)])
        try:
            result = self.runner.run(command)
        except subprocess.TimeoutExpired:
            return "CREO_TIMEOUT"
        except OSError:
            return "CREO_PROCESS_ERROR"
        prepared_match = self._PREPARED_PATTERN.search(result.stdout or "")
        if prepared_match is not None:
            session.prepared_models_root = Path(prepared_match.group(1).strip())
        if result.returncode != 0:
            return "CREO_RENDER_FAILED"
        return None

    def close_session(self, session: CreoSession) -> None:
        del session


def _report_focus_center(
    report: NativeRenderGateReport,
    *,
    subject_only: bool = False,
) -> tuple[float, float]:
    if report.composition is None or report.composition.center_pixel is None:
        raise ScreenCenteringError("render report has no activity centers")
    if subject_only or not _has_arrow_center(report):
        return report.composition.center_pixel
    return activity_focus_center(
        report.composition.center_pixel,
        report.arrow_raster.center_pixel,
    )


def _has_arrow_center(report: NativeRenderGateReport) -> bool:
    return (
        report.arrow_raster is not None
        and report.arrow_raster.center_pixel is not None
    )


def _solve_pan(
    target: tuple[float, ...],
    base_pan: tuple[float, ...],
    base_center: tuple[float, float],
    response: ScreenPanResponse,
    max_abs_pan: float,
) -> tuple[float, float]:
    if len(target) != 2 or len(base_pan) != 2:
        raise ScreenCenteringError("centering target and PAN must have two values")
    return solve_with_screen_pan_response(
        target_pixel=(target[0], target[1]),
        base_pan=(base_pan[0], base_pan[1]),
        base_center=base_center,
        response=response,
        max_abs_pan=max_abs_pan,
    ).pan


def _screen_pan_response_key(
    payload: dict,
    *,
    camera_id: str,
    zoom: float,
) -> str:
    camera = payload.get("camera_catalog", {}).get(camera_id)
    if not isinstance(camera, dict):
        raise ScreenCenteringError("camera basis is unavailable")
    value = {
        "schema_version": "screen-pan-response-key/v1",
        "camera_id": camera_id,
        "position_direction_root": camera.get("position_direction_root"),
        "up_reference_root": camera.get("up_reference_root"),
        "zoom": zoom,
        "frame_pixels": [1600, 1600],
        "export_contract": "creo-native-jpeg/v1",
    }
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _write_transient_render_plan(
    path: Path,
    task: RenderTask,
    *,
    camera_id: str,
    zoom: float,
    task_specs: tuple[tuple[str, tuple[float, float]], ...],
) -> tuple[dict, ...]:
    payloads: list[dict] = []
    tasks: list[dict] = []
    for index, (task_id, pan) in enumerate(task_specs):
        payload = deepcopy(task.payload)
        payload["plan_index"] = index
        presentation = payload["presentation"]
        presentation["variants"] = [
            {
                "variant_id": f"adaptive-center-{index}",
                "camera_id": camera_id,
                "zoom": zoom,
                "pan": [pan[0], pan[1]],
            }
        ]
        payloads.append(payload)
        tasks.append(
            {
                "task_id": task_id,
                "step_id": task.step_id,
                "main_process_id": task.main_process_id,
                "depends_on": [],
                "complete_state_hash": task.complete_state_hash,
                "blocks_dependents_on_failure": False,
                "payload": payload,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {"schema_version": "render-plan/v2", "tasks": tasks},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)
    return tuple(payloads)


def _screen_pan_response_file(internal_directory: Path) -> Path:
    return internal_directory / "screen-pan-responses.json"


def _load_screen_pan_responses(
    internal_directory: Path,
) -> dict[str, ScreenPanResponse]:
    path = _screen_pan_response_file(internal_directory)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "screen-pan-response-cache/v1":
            return {}
        responses: dict[str, ScreenPanResponse] = {}
        for key, value in payload.get("responses", {}).items():
            response = ScreenPanResponse(
                pixels_per_pan_x=tuple(float(item) for item in value["pixels_per_pan_x"]),
                pixels_per_pan_y=tuple(float(item) for item in value["pixels_per_pan_y"]),
                determinant=float(value["determinant"]),
            )
            if (
                len(response.pixels_per_pan_x) != 2
                or len(response.pixels_per_pan_y) != 2
                or not all(
                    math.isfinite(item)
                    for item in (
                        *response.pixels_per_pan_x,
                        *response.pixels_per_pan_y,
                        response.determinant,
                    )
                )
                or abs(response.determinant) <= 1.0e-6
            ):
                return {}
            responses[str(key)] = response
        return responses
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def _save_screen_pan_responses(
    internal_directory: Path,
    responses: dict[str, ScreenPanResponse],
) -> None:
    path = _screen_pan_response_file(internal_directory)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": "screen-pan-response-cache/v1",
                "responses": {
                    key: asdict(value) for key, value in sorted(responses.items())
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "render-task"


def _passed_image(path: Path) -> RenderAttempt:
    if not path.is_file() or path.stat().st_size == 0:
        return RenderAttempt.retryable("RENDER_OUTPUT_MISSING")
    return RenderAttempt.passed(f"sha256:{sha256(path.read_bytes()).hexdigest()}")


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
