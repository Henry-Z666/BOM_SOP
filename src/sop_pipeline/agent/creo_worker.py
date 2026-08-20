from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Protocol

from ..process_control import owned_process_creation_kwargs, terminate_process_tree
from .framing_recovery import (
    FramingRecoveryError,
    derive_zoom_for_subject_span,
)
from .gate_policy import GateCategory, classify_failures, gate_policy
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
    project_lower_left_anchored_zoom_center,
    solve_with_screen_pan_response,
    update_screen_pan_response,
)


CENTERING_FAILURES = frozenset(
    {
        "ACTIVITY_NOT_CENTERED",
        "ARROW_NOT_CENTERED",
        "ARROW_NOT_VISIBLE",
        "ARROW_CLIPPED",
    }
)
ZOOM_FAILURES = frozenset({"SUBJECT_TOO_SMALL", "SUBJECT_TOO_LARGE"})
FRAMING_FAILURES = CENTERING_FAILURES | ZOOM_FAILURES
CAMERA_FLIP_FAILURES = frozenset(
    {
        "CAMERA_RECEIVER_WRONG_HALF_SPACE",
        "CAMERA_RECEIVER_SILHOUETTE",
    }
)
PLANNING_CAMERA_FLIP_DIAGNOSTICS = frozenset({"DIRECTION_SIGN_WEAK"})
MAX_FRAMING_RASTERS_PER_TASK = 6


class CommandRunner(Protocol):
    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]: ...


class SubprocessCommandRunner:
    def __init__(self, timeout_seconds: int = 900) -> None:
        self.timeout_seconds = timeout_seconds

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        # Pipes are unsafe for the persistent Creo launcher: Creo descendants
        # can inherit a pipe handle after the direct PowerShell process exits,
        # making subprocess.run(..., capture_output=True) wait for the worker's
        # five-minute idle shutdown before it observes EOF. Temporary files let
        # us wait only for the direct process while retaining bounded diagnostics.
        with tempfile.TemporaryFile(
            mode="w+", encoding="utf-8", errors="replace"
        ) as stdout_file, tempfile.TemporaryFile(
            mode="w+", encoding="utf-8", errors="replace"
        ) as stderr_file:
            process = subprocess.Popen(
                command,
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
                **owned_process_creation_kwargs(),
            )
            try:
                return_code = process.wait(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                terminate_process_tree(process)
                raise
            stdout_file.seek(0)
            stderr_file.seek(0)
            return subprocess.CompletedProcess(
                command,
                return_code,
                stdout=stdout_file.read(),
                stderr=stderr_file.read(),
            )


@dataclass
class FrozenFramingProfile:
    camera_id: str
    zoom: float
    pan: tuple[float, float]
    scale_signature: str
    source_task_id: str
    subject_span_fraction: float


@dataclass
class CreoSession:
    output_directory: Path
    internal_directory: Path | None = None
    prepared_models_root: Path | None = None
    native_worker_root: Path | None = None
    native_worker_active: bool = False
    presentation_variant_by_task: dict[str, int] = field(default_factory=dict)
    attempted_presentation_variants: dict[str, set[int]] = field(default_factory=dict)
    screen_pan_responses: dict[str, ScreenPanResponse] = field(default_factory=dict)
    framing_profiles: dict[str, FrozenFramingProfile] = field(default_factory=dict)
    recalibrated_profile_keys: set[str] = field(default_factory=set)
    render_frames_by_task: dict[str, int] = field(default_factory=dict)


class AgentNativeCreoWorker:
    """Product-neutral adapter for Creo-native DisplayList arrow rendering."""

    _PREPARED_PATTERN = re.compile(
        r"^\[AGENT_RENDER\]\s+prepared_models\s+(.+?)\s*$",
        re.MULTILINE,
    )
    _WORKER_PATTERN = re.compile(
        r"^\[AGENT_RENDER\]\s+worker_generation\s+(.+?)\s*$",
        re.MULTILINE,
    )

    def __init__(
        self,
        *,
        powershell: str,
        batch_script: Path,
        models_root: Path,
        render_plan_json: Path,
        runtime_config: Path | None = None,
        stop_script: Path | None = None,
        runner: CommandRunner | None = None,
        validator: DeterministicNativeRenderValidator | None = None,
    ) -> None:
        self.powershell = powershell
        self.batch_script = batch_script
        self.models_root = models_root
        self.render_plan_json = render_plan_json
        self.runtime_config = runtime_config
        self.stop_script = stop_script or batch_script.with_name(
            "stop_agent_native_worker.ps1"
        )
        self.runner = runner or SubprocessCommandRunner()
        self.validator = validator or DeterministicNativeRenderValidator()
        self._diagnostics_by_task: dict[str, dict[str, object]] = {}

    def diagnostic_for(self, task_id: str) -> dict[str, object] | None:
        value = self._diagnostics_by_task.get(task_id)
        return dict(value) if value is not None else None

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
            native_worker_root=run_workspace / "internal" / "native-worker",
        )
        session.screen_pan_responses.update(_load_screen_pan_responses(internal_directory))
        session.framing_profiles.update(_load_framing_profiles(internal_directory))
        session.recalibrated_profile_keys.update(
            _load_recalibrated_profile_keys(internal_directory)
        )
        return session

    def render(
        self,
        session: CreoSession,
        task: RenderTask,
        attempt: int,
    ) -> RenderAttempt:
        if task.payload.get("execution_mode") not in {
            "formal",
            "diagnostic_preview",
        }:
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
        try:
            variant = task.payload["presentation"]["variants"][variant_index]
            camera_id = str(variant["camera_id"])
            profile_contract = task.payload["presentation"].get(
                "framing_profile", {}
            )
            profile_policy = str(profile_contract.get("policy", "freeze_per_camera/v1"))
            default_refit = profile_policy == "default_refit/v1"
            if profile_policy == "manual_refit/v1":
                raise ScreenCenteringError(
                    "manual framing is frozen in the production worker"
                )
            profile_key = (
                None
                if default_refit
                else _framing_profile_key(task.payload, camera_id=camera_id)
            )
            if default_refit and (
                variant_index != 0
                or not math.isclose(float(variant["zoom"]), 1.0)
                or tuple(float(value) for value in variant["pan"]) != (0.0, 0.0)
            ):
                raise ScreenCenteringError(
                    "default fixed-camera view must use Zoom=1 and PAN=0"
                )
        except (KeyError, IndexError, TypeError, ValueError, ScreenCenteringError):
            return RenderAttempt.failed("FRAMING_PROFILE_CONTRACT_INVALID")
        frozen = session.framing_profiles.get(profile_key) if profile_key else None
        if frozen is not None:
            rendered = self._render_centered_variant(
                session,
                task,
                camera_id=frozen.camera_id,
                zoom=frozen.zoom,
                pan=frozen.pan,
                label="frozen-profile",
            )
            if isinstance(rendered, RenderAttempt):
                return rendered
            frozen_report, _ = rendered
            if frozen_report.passed:
                return _passed_image(session.output_directory / f"{task.task_id}.jpg")
            if FRAMING_FAILURES.intersection(frozen_report.failures):
                assert profile_key is not None
                if profile_key in session.recalibrated_profile_keys:
                    return _gate_attempt(
                        session.output_directory / f"{task.task_id}.jpg",
                        frozen_report.failures,
                    )
                session.recalibrated_profile_keys.add(profile_key)
                _save_recalibrated_profile_keys(
                    session.internal_directory,
                    session.recalibrated_profile_keys,
                )
                session.framing_profiles.pop(profile_key, None)
                _save_framing_profiles(
                    session.internal_directory, session.framing_profiles
                )
                return self._recover_screen_centering(
                    session,
                    task,
                    variant_index=variant_index,
                    base_report=frozen_report,
                    variant_override={
                        "camera_id": frozen.camera_id,
                        "zoom": frozen.zoom,
                        "pan": [frozen.pan[0], frozen.pan[1]],
                    },
                )
            return _gate_attempt(
                session.output_directory / f"{task.task_id}.jpg",
                frozen_report.failures,
            )
        execution_error = self._run_batch(
            session,
            plan_path=self.render_plan_json,
            output_directory=session.output_directory,
            start_index=plan_index,
            count=1,
            variant_index=variant_index,
            budget_task_id=task.task_id,
        )
        if execution_error is not None:
            return _batch_failure(execution_error)
        image_path = session.output_directory / f"{task.task_id}.jpg"
        audit_path = session.output_directory / f"{task.task_id}.arrow.json"
        report = self.validator.validate(
            image_path,
            audit_path,
            task.payload,
            variant_index=variant_index,
        )
        self._record_gate_diagnostic(
            session,
            task,
            attempt=attempt,
            variant_index=variant_index,
            report=report,
        )
        if (
            profile_policy == "freeze_per_scale_bucket/v1"
            and _outside_scale_probe_safe_boundary(task.payload)
        ):
            # Never replace a real, visible base raster with a known-unsafe
            # high-Zoom blank.  Extreme scale buckets are explicitly reviewed
            # after one frame until a new boundary is proven in real Creo.
            return _gate_attempt(image_path, report.failures)
        if (
            not default_refit
            and profile_policy == "freeze_per_scale_bucket/v1"
            and _scale_bucket_zoom(task.payload, report, float(variant["zoom"]))
            is not None
        ):
            return self._recover_screen_centering(
                session,
                task,
                variant_index=variant_index,
                base_report=report,
            )
        if not report.passed:
            decision = classify_failures(report.failures)
            error = decision.primary_code
            if default_refit:
                return _gate_attempt(image_path, report.failures)
            if CAMERA_FLIP_FAILURES.intersection(report.failures) or (
                _planning_needs_camera_candidates(task.payload)
            ):
                next_variant = _flipped_camera_variant_index(
                    task.payload,
                    current_index=variant_index,
                    attempted=session.attempted_presentation_variants[task.task_id],
                )
                if next_variant is None:
                    next_variant = _next_presentation_variant(
                        task.payload,
                        current_index=variant_index,
                        attempted=session.attempted_presentation_variants[task.task_id],
                        failures=report.failures,
                    )
                if next_variant is not None and attempt < 3:
                    _retain_original_camera_candidate(
                        session,
                        task,
                        image_path=image_path,
                        variant_index=variant_index,
                    )
                    session.presentation_variant_by_task[task.task_id] = next_variant
                    return RenderAttempt.retryable(error)
            recovery_failures = (
                FRAMING_FAILURES
                if profile_policy == "freeze_per_scale_bucket/v1"
                else CENTERING_FAILURES
            )
            if (
                recovery_failures.intersection(report.failures)
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
            return _gate_attempt(image_path, report.failures)
        if not default_refit:
            _freeze_framing_profile(
                session,
                task,
                camera_id=camera_id,
                zoom=float(variant["zoom"]),
                pan=tuple(float(value) for value in variant["pan"]),
                report=report,
            )
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
            zoom = float(variant["zoom"])
            max_abs_pan = _effective_pan_bound(
                float(contract["max_abs_pan"]), zoom
            )
            max_rounds = int(contract["max_probe_rounds"])
            camera_id = str(variant["camera_id"])
            base_pan = tuple(float(value) for value in variant["pan"])
            subject_only = not _has_arrow_center(base_report)
            if _native_focus_refit_enabled(task.payload) and _has_arrow_center(
                base_report
            ):
                base_center = base_report.arrow_raster.center_pixel
            else:
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
            combined = self._render_combined_zoom(
                session,
                task,
                report=base_report,
                camera_id=camera_id,
                zoom=zoom,
                pan=base_pan,
                focus_center=base_center,
                response=response,
                target=target,
                zoom_round=zoom_round,
            )
            if isinstance(combined, RenderAttempt):
                return combined
            if combined is not None:
                combined_report, combined_pan, combined_zoom, target_pending = combined
                if combined_report.passed:
                    if target_pending:
                        return self._recover_screen_centering(
                            session,
                            task,
                            variant_index=variant_index,
                            base_report=combined_report,
                            variant_override={
                                "camera_id": camera_id,
                                "zoom": combined_zoom,
                                "pan": [combined_pan[0], combined_pan[1]],
                            },
                            zoom_round=zoom_round + 1,
                        )
                    return _passed_image(
                        session.output_directory / f"{task.task_id}.jpg"
                    )
                if CENTERING_FAILURES.intersection(combined_report.failures):
                    return self._recover_screen_centering(
                        session,
                        task,
                        variant_index=variant_index,
                        base_report=combined_report,
                        variant_override={
                            "camera_id": camera_id,
                            "zoom": combined_zoom,
                            "pan": [combined_pan[0], combined_pan[1]],
                        },
                        zoom_round=zoom_round + 1,
                    )
                return self._recover_centered_zoom(
                    session,
                    task,
                    report=combined_report,
                    camera_id=camera_id,
                    zoom=combined_zoom,
                    pan=combined_pan,
                    zoom_round=zoom_round + 1,
                )
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
            session.screen_pan_responses[cache_key] = response
            _save_screen_pan_responses(
                session.internal_directory, session.screen_pan_responses
            )
            combined = self._render_combined_zoom(
                session,
                task,
                report=base_report,
                camera_id=camera_id,
                zoom=zoom,
                pan=base_pan,
                focus_center=base_center,
                response=response,
                target=target,
                zoom_round=zoom_round,
            )
            if isinstance(combined, RenderAttempt):
                return combined
            if combined is not None:
                combined_report, combined_pan, combined_zoom, target_pending = combined
                if combined_report.passed:
                    if target_pending:
                        return self._recover_screen_centering(
                            session,
                            task,
                            variant_index=variant_index,
                            base_report=combined_report,
                            variant_override={
                                "camera_id": camera_id,
                                "zoom": combined_zoom,
                                "pan": [combined_pan[0], combined_pan[1]],
                            },
                            zoom_round=zoom_round + 1,
                        )
                    return _passed_image(
                        session.output_directory / f"{task.task_id}.jpg"
                    )
                if CENTERING_FAILURES.intersection(combined_report.failures):
                    return self._recover_screen_centering(
                        session,
                        task,
                        variant_index=variant_index,
                        base_report=combined_report,
                        variant_override={
                            "camera_id": camera_id,
                            "zoom": combined_zoom,
                            "pan": [combined_pan[0], combined_pan[1]],
                        },
                        zoom_round=zoom_round + 1,
                    )
                return self._recover_centered_zoom(
                    session,
                    task,
                    report=combined_report,
                    camera_id=camera_id,
                    zoom=combined_zoom,
                    pan=combined_pan,
                    zoom_round=zoom_round + 1,
                )
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

    def _render_combined_zoom(
        self,
        session: CreoSession,
        task: RenderTask,
        *,
        report: NativeRenderGateReport,
        camera_id: str,
        zoom: float,
        pan: tuple[float, float],
        focus_center: tuple[float, float],
        response: ScreenPanResponse,
        target: tuple[float, float],
        zoom_round: int,
    ):
        try:
            contract = task.payload["presentation"]["zoom_recovery"]
            if zoom_round >= int(contract["max_rounds"]):
                return None
            if report.composition is None:
                raise FramingRecoveryError("subject metrics are unavailable")
            requested_zoom = _scale_bucket_zoom(task.payload, report, zoom)
            if requested_zoom is None:
                if not ZOOM_FAILURES.intersection(report.failures):
                    return None
                requested_zoom = derive_zoom_for_subject_span(
                    current_zoom=zoom,
                    observed_span=report.composition.max_span_fraction,
                    target_span=float(contract["target_subject_span"]),
                    min_zoom=float(contract["min_zoom"]),
                    max_zoom=float(contract["max_zoom"]),
                )
            derived_zoom = _bounded_zoom_step(zoom, requested_zoom)
            target_pending = not math.isclose(
                derived_zoom, requested_zoom, rel_tol=0.01, abs_tol=1.0e-6
            )
            if math.isclose(derived_zoom, zoom, abs_tol=1.0e-6):
                raise FramingRecoveryError("derived Zoom does not change")
            if _native_focus_refit_enabled(task.payload):
                ratio = derived_zoom / zoom
                projected_center = (
                    target[0] + ratio * (focus_center[0] - target[0]),
                    target[1] + ratio * (focus_center[1] - target[1]),
                )
            else:
                projected_center = project_lower_left_anchored_zoom_center(
                    current_center=focus_center,
                    current_zoom=zoom,
                    target_zoom=derived_zoom,
                    frame_pixels=(1600, 1600),
                )
            derived_pan = _solve_pan(
                target,
                pan,
                projected_center,
                response,
                _effective_pan_bound(
                    float(task.payload["presentation"]["centering"]["max_abs_pan"]),
                    derived_zoom,
                ),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            FramingRecoveryError,
            ScreenCenteringError,
        ):
            return None
        rendered = self._render_centered_variant(
            session,
            task,
            camera_id=camera_id,
            zoom=derived_zoom,
            pan=derived_pan,
            label=f"combined-zoom-{zoom_round + 1}",
        )
        if isinstance(rendered, RenderAttempt):
            return rendered
        rendered_report, rendered_pan = rendered
        return rendered_report, rendered_pan, derived_zoom, target_pending

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
        failure_set = set(report.failures)
        if not (failure_set & ZOOM_FAILURES) or (failure_set - ZOOM_FAILURES):
            return RenderAttempt.failed(report.failures[0])
        try:
            contract = task.payload["presentation"]["zoom_recovery"]
            max_rounds = int(contract["max_rounds"])
            if zoom_round >= max_rounds:
                return RenderAttempt.failed("SUBJECT_TOO_SMALL")
            if report.composition is None:
                raise FramingRecoveryError("subject metrics are unavailable")
            cache_key = _screen_pan_response_key(
                task.payload, camera_id=camera_id, zoom=zoom
            )
            if cache_key not in session.screen_pan_responses:
                return self._recover_screen_centering(
                    session,
                    task,
                    variant_index=0,
                    base_report=report,
                    variant_override={
                        "camera_id": camera_id,
                        "zoom": zoom,
                        "pan": [pan[0], pan[1]],
                    },
                    zoom_round=zoom_round,
                )
            derived_zoom = derive_zoom_for_subject_span(
                current_zoom=zoom,
                observed_span=report.composition.max_span_fraction,
                target_span=float(contract["target_subject_span"]),
                min_zoom=float(contract["min_zoom"]),
                max_zoom=float(contract["max_zoom"]),
            )
            if math.isclose(derived_zoom, zoom, abs_tol=1.0e-6):
                raise FramingRecoveryError("derived Zoom does not change")
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
            budget_task_id=task.task_id,
        )
        if error is not None:
            return _batch_failure(error)
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
            budget_task_id=task.task_id,
        )
        if error is not None:
            return _batch_failure(error)
        report = self.validator.validate(
            session.output_directory / f"{task.task_id}.jpg",
            session.output_directory / f"{task.task_id}.arrow.json",
            payload,
            variant_index=0,
        )
        if report.passed:
            _freeze_framing_profile(
                session,
                task,
                camera_id=camera_id,
                zoom=zoom,
                pan=pan,
                report=report,
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
        budget_task_id: str,
    ) -> str | None:
        used = session.render_frames_by_task.get(budget_task_id, 0)
        if used + count > MAX_FRAMING_RASTERS_PER_TASK:
            return "FRAMING_FRAME_BUDGET_EXCEEDED"
        session.render_frames_by_task[budget_task_id] = used + count
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
        if self.runtime_config is not None:
            command.extend(["-RuntimeConfig", str(self.runtime_config)])
        if session.native_worker_root is not None:
            command.extend(["-WorkerRoot", str(session.native_worker_root)])
        if session.prepared_models_root is not None:
            command.extend(["-PreparedModelsRoot", str(session.prepared_models_root)])
        try:
            result = self.runner.run(command)
        except subprocess.TimeoutExpired as error:
            self._record_batch_diagnostic(
                session,
                budget_task_id,
                "CREO_TIMEOUT",
                message=str(error),
            )
            return "CREO_TIMEOUT"
        except OSError as error:
            self._record_batch_diagnostic(
                session,
                budget_task_id,
                "CREO_PROCESS_ERROR",
                message=str(error),
            )
            return "CREO_PROCESS_ERROR"
        prepared_match = self._PREPARED_PATTERN.search(result.stdout or "")
        if prepared_match is not None:
            session.prepared_models_root = Path(prepared_match.group(1).strip())
        if self._WORKER_PATTERN.search(result.stdout or "") is not None:
            session.native_worker_active = True
        if result.returncode != 0:
            self._record_batch_diagnostic(
                session,
                budget_task_id,
                "CREO_RENDER_FAILED",
                returncode=result.returncode,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
            )
            return "CREO_RENDER_FAILED"
        return None

    def _record_batch_diagnostic(
        self,
        session: CreoSession,
        task_id: str,
        error_code: str,
        *,
        message: str = "",
        returncode: int | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        def tail(value: str) -> str:
            return value.replace("\x00", "")[-4000:]

        payload: dict[str, object] = {
            "schema_version": "creo-render-diagnostic/v1",
            "task_id": task_id,
            "error_code": error_code,
            "message": tail(message),
            "returncode": returncode,
            "stdout_tail": tail(stdout),
            "stderr_tail": tail(stderr),
        }
        self._diagnostics_by_task[task_id] = payload
        root = session.output_directory.parent / "internal" / "render-diagnostics"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{_safe_name(task_id)}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _record_gate_diagnostic(
        self,
        session: CreoSession,
        task: RenderTask,
        *,
        attempt: int,
        variant_index: int,
        report: NativeRenderGateReport,
    ) -> None:
        decision = classify_failures(report.failures)
        policies = [gate_policy(code) for code in report.failures]
        presentation = task.payload.get("presentation", {})
        frame_gate = (
            presentation.get("frame_gate", {})
            if isinstance(presentation, dict)
            else {}
        )
        camera_measurements = _camera_gate_measurements(
            task.payload,
            variant_index=variant_index,
        )
        attempted_actions = [
            str(value)
            for value in task.payload.get("attempted_actions", [])
            if str(value).strip()
        ]
        attempted_actions.append(
            f"已渲染视角变体 {variant_index}（第 {attempt} 次尝试）"
        )
        payload: dict[str, object] = {
            "schema_version": "creo-render-diagnostic/v3",
            "task_id": task.task_id,
            "step_id": task.step_id,
            "phase": "deterministic_render_gate",
            "attempt": attempt,
            "variant_index": variant_index,
            "framing_policy": task.payload.get("presentation", {})
            .get("framing_profile", {})
            .get("policy", "freeze_per_camera/v1"),
            "error_code": decision.primary_code or None,
            "primary_code": decision.primary_code or None,
            "message": (
                "确定性渲染检查通过"
                if report.passed
                else "；".join(policy.user_message for policy in policies)
            ),
            "failures": list(report.failures),
            "category": None if report.passed else decision.category.value,
            "expected": {
                "subject_span": [
                    frame_gate.get("min_subject_span"),
                    frame_gate.get("max_subject_span"),
                ],
                "max_clipped_edges": frame_gate.get("max_clipped_edges"),
                "camera_receiver_dot_min": 0.35,
                "projected_explosion_min": 1.0e-6,
            },
            "actual": {
                "composition": (
                    asdict(report.composition)
                    if report.composition is not None
                    else None
                ),
                "arrow_raster": (
                    asdict(report.arrow_raster)
                    if report.arrow_raster is not None
                    else None
                ),
                **camera_measurements,
            },
            "attempted_actions": attempted_actions,
            "suggested_actions": [
                policy.suggested_action for policy in policies
            ],
            "retained_image": (
                f"rendered/{task.task_id}.jpg"
                if not report.passed and decision.retain_real_image
                else None
            ),
            "composition": (
                asdict(report.composition) if report.composition is not None else None
            ),
            "arrow_raster": (
                asdict(report.arrow_raster) if report.arrow_raster is not None else None
            ),
            "expected_arrow_count": len(task.payload.get("arrow_anchors", [])),
            "image_path": f"rendered/{task.task_id}.jpg",
            "audit_path": f"rendered/{task.task_id}.arrow.json",
        }
        self._diagnostics_by_task[task.task_id] = payload
        root = session.output_directory.parent / "internal" / "render-diagnostics"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{_safe_name(task.task_id)}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def close_session(self, session: CreoSession) -> None:
        if not session.native_worker_active or session.native_worker_root is None:
            return
        command = [
            self.powershell,
            "-NoProfile",
            "-File",
            str(self.stop_script),
            "-RunWorkspaceRoot",
            str(session.output_directory.parent),
            "-WorkerRoot",
            str(session.native_worker_root),
        ]
        try:
            self.runner.run(command)
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            session.native_worker_active = False


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


def _scale_bucket_zoom(
    payload: dict,
    report: NativeRenderGateReport,
    current_zoom: float,
) -> float | None:
    """Use the free base raster to calibrate CAD activity against stage context."""

    if report.composition is None:
        return None
    presentation = payload.get("presentation", {})
    profile = presentation.get("framing_profile", {})
    evidence = profile.get("scale_evidence", {}) if isinstance(profile, dict) else {}
    if (
        not isinstance(evidence, dict)
        or evidence.get("status") != "available"
        or profile.get("policy") != "freeze_per_scale_bucket/v1"
    ):
        return None
    try:
        activity_size = tuple(
            float(value) for value in evidence["activity_projected_size_root"]
        )
        context_size = tuple(
            float(value) for value in evidence["context_projected_size_root"]
        )
        contract = presentation["zoom_recovery"]
        activity_span = max(activity_size)
        context_span = max(context_size)
        observed_activity_span = (
            float(report.composition.max_span_fraction)
            * activity_span
            / context_span
        )
        derived = derive_zoom_for_subject_span(
            current_zoom=current_zoom,
            observed_span=observed_activity_span,
            target_span=float(contract["target_subject_span"]),
            min_zoom=float(contract["min_zoom"]),
            max_zoom=float(contract["max_zoom"]),
        )
    except (KeyError, TypeError, ValueError, FramingRecoveryError):
        return None
    if math.isclose(derived, current_zoom, rel_tol=0.05, abs_tol=1.0e-6):
        return None
    return derived


def _native_focus_refit_enabled(payload: dict) -> bool:
    presentation = payload.get("presentation", {})
    contract = presentation.get("native_refit", {})
    profile = presentation.get("framing_profile", {})
    return (
        isinstance(contract, dict)
        and isinstance(profile, dict)
        and profile.get("probe_interface_status")
        == "enabled_real_cad_bounds/v1"
        and contract.get("schema_version") == "native-focus-refit/v1"
        and contract.get("fit_occurrences") == "moving_only/v1"
        and contract.get("restore_stage_context_without_refit") is True
    )


def _outside_scale_probe_safe_boundary(payload: dict) -> bool:
    profile = payload.get("presentation", {}).get("framing_profile", {})
    evidence = profile.get("scale_evidence", {}) if isinstance(profile, dict) else {}
    try:
        boundary = int(profile["safe_context_activity_ratio_bucket_max"])
        ratio_bucket = int(evidence["context_activity_ratio_bucket"])
    except (KeyError, TypeError, ValueError):
        return False
    return (
        profile.get("outside_safe_boundary") == "question_without_zoom/v1"
        and ratio_bucket > boundary
    )


def _bounded_zoom_step(
    current_zoom: float,
    requested_zoom: float,
    *,
    max_ratio: float = 3.0,
) -> float:
    """Keep the activity observable while traversing a large scale change."""

    if current_zoom <= 0.0 or requested_zoom <= 0.0 or max_ratio <= 1.0:
        raise FramingRecoveryError("bounded Zoom step inputs are invalid")
    return min(max(requested_zoom, current_zoom / max_ratio), current_zoom * max_ratio)


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
    del zoom
    value = {
        "schema_version": "screen-pan-response-key/v3",
        "camera_id": camera_id,
        "position_direction_root": camera.get("position_direction_root"),
        "up_reference_root": camera.get("up_reference_root"),
        "frame_pixels": [1600, 1600],
        "export_contract": "creo-native-jpeg/v1",
        "scale_signature": _pan_response_scale_signature(payload),
    }
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _pan_response_scale_signature(payload: dict) -> str:
    profile = payload.get("presentation", {}).get("framing_profile", {})
    if not isinstance(profile, dict):
        return "default/v1"
    if profile.get("policy") != "freeze_per_scale_bucket/v1":
        return "default/v1"
    signature = str(profile.get("scale_signature", "")).strip()
    if not signature:
        raise ScreenCenteringError("framing scale signature is missing")
    return signature


def _framing_profile_key(payload: dict, *, camera_id: str) -> str:
    camera = payload.get("camera_catalog", {}).get(camera_id)
    if not isinstance(camera, dict):
        raise ScreenCenteringError("camera basis is unavailable")
    contract = payload.get("presentation", {}).get("framing_profile", {})
    if not isinstance(contract, dict):
        raise ScreenCenteringError("framing profile contract is invalid")
    policy = str(contract.get("policy", "freeze_per_camera/v1"))
    if policy not in {"freeze_per_camera/v1", "freeze_per_scale_bucket/v1"}:
        raise ScreenCenteringError("framing profile policy is unsupported")
    scale_signature = str(contract.get("scale_signature", "default/v1"))
    if not scale_signature:
        raise ScreenCenteringError("framing scale signature is missing")
    value = {
        "schema_version": "frozen-framing-profile-key/v1",
        "camera_id": camera_id,
        "position_direction_root": camera.get("position_direction_root"),
        "up_reference_root": camera.get("up_reference_root"),
        "frame_pixels": [1600, 1600],
        "export_contract": "creo-native-jpeg/v1",
        "scale_signature": scale_signature,
    }
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _freeze_framing_profile(
    session: CreoSession,
    task: RenderTask,
    *,
    camera_id: str,
    zoom: float,
    pan: tuple[float, float],
    report: NativeRenderGateReport,
) -> None:
    if session.internal_directory is None or report.composition is None:
        return
    key = _framing_profile_key(task.payload, camera_id=camera_id)
    if key in session.framing_profiles:
        return
    contract = task.payload.get("presentation", {}).get("framing_profile", {})
    session.framing_profiles[key] = FrozenFramingProfile(
        camera_id=camera_id,
        zoom=float(zoom),
        pan=(float(pan[0]), float(pan[1])),
        scale_signature=str(contract.get("scale_signature", "default/v1")),
        source_task_id=task.task_id,
        subject_span_fraction=float(report.composition.max_span_fraction),
    )
    _save_framing_profiles(session.internal_directory, session.framing_profiles)


def _effective_pan_bound(base_bound: float, zoom: float) -> float:
    """Scale the normalized PAN envelope with Creo's native Zoom.

    ``ScreenTransform`` Zoom is anchored at a screen corner, so preserving the
    same visible centre can require proportionally more PAN as Zoom increases.
    The render contract supplies the Zoom=1 envelope; this derivation keeps the
    bound product-neutral while remaining limited by the contract's bounded
    Zoom range and the final image hard gates.
    """

    if not all(math.isfinite(value) for value in (base_bound, zoom)):
        raise ScreenCenteringError("PAN bound inputs must be finite")
    if base_bound <= 0.0 or zoom <= 0.0:
        raise ScreenCenteringError("PAN bound inputs must be positive")
    return base_bound * max(1.0, zoom)


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


def _framing_profile_file(internal_directory: Path) -> Path:
    return internal_directory / "frozen-framing-profiles.json"


def _recalibrated_profile_keys_file(internal_directory: Path) -> Path:
    return internal_directory / "recalibrated-framing-profile-keys.json"


def _load_framing_profiles(
    internal_directory: Path,
) -> dict[str, FrozenFramingProfile]:
    path = _framing_profile_file(internal_directory)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "frozen-framing-profile-cache/v1":
            return {}
        profiles: dict[str, FrozenFramingProfile] = {}
        for key, value in payload.get("profiles", {}).items():
            profile = FrozenFramingProfile(
                camera_id=str(value["camera_id"]),
                zoom=float(value["zoom"]),
                pan=tuple(float(item) for item in value["pan"]),
                scale_signature=str(value["scale_signature"]),
                source_task_id=str(value["source_task_id"]),
                subject_span_fraction=float(value["subject_span_fraction"]),
            )
            if (
                len(profile.pan) != 2
                or not profile.camera_id
                or not profile.scale_signature
                or not all(
                    math.isfinite(item)
                    for item in (
                        profile.zoom,
                        *profile.pan,
                        profile.subject_span_fraction,
                    )
                )
                or profile.zoom <= 0.0
            ):
                return {}
            profiles[str(key)] = profile
        return profiles
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def _save_framing_profiles(
    internal_directory: Path,
    profiles: dict[str, FrozenFramingProfile],
) -> None:
    path = _framing_profile_file(internal_directory)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": "frozen-framing-profile-cache/v1",
                "profiles": {
                    key: asdict(value) for key, value in sorted(profiles.items())
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_recalibrated_profile_keys(internal_directory: Path) -> set[str]:
    path = _recalibrated_profile_keys_file(internal_directory)
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "framing-profile-recalibration/v1":
            return set()
        return {
            str(value)
            for value in payload.get("profile_keys", [])
            if str(value).startswith("sha256:")
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return set()


def _save_recalibrated_profile_keys(
    internal_directory: Path,
    profile_keys: set[str],
) -> None:
    path = _recalibrated_profile_keys_file(internal_directory)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": "framing-profile-recalibration/v1",
                "profile_keys": sorted(profile_keys),
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


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


def _retain_original_camera_candidate(
    session: CreoSession,
    task: RenderTask,
    *,
    image_path: Path,
    variant_index: int,
) -> None:
    if not image_path.is_file() or image_path.stat().st_size == 0:
        return
    for existing in session.output_directory.glob(
        f"{_safe_name(task.step_id)}-candidate-*.jpg"
    ):
        existing.unlink(missing_ok=True)
    camera_id = _presentation_camera_id(task.payload, variant_index)
    candidate = session.output_directory / (
        f"{_safe_name(task.step_id)}-candidate-1-original-"
        f"{_safe_name(camera_id)}.jpg"
    )
    shutil.copy2(image_path, candidate)


def _complete_camera_flip_candidates(
    session: CreoSession,
    task: RenderTask,
    *,
    image_path: Path,
    variant_index: int,
    failures: tuple[str, ...],
    error_code: str | None = None,
) -> RenderAttempt | None:
    if variant_index == 0 or not image_path.is_file() or image_path.stat().st_size == 0:
        return None
    originals = tuple(
        sorted(
            session.output_directory.glob(
                f"{_safe_name(task.step_id)}-candidate-1-original-*.jpg"
            )
        )
    )
    if len(originals) != 1:
        return None
    decision = classify_failures(failures)
    if decision.category not in {
        GateCategory.AUTO_REPAIR,
        GateCategory.HUMAN_REVIEW,
    }:
        return None
    camera_id = _presentation_camera_id(task.payload, variant_index)
    flipped = session.output_directory / (
        f"{_safe_name(task.step_id)}-candidate-2-flipped-"
        f"{_safe_name(camera_id)}.jpg"
    )
    shutil.copy2(image_path, flipped)
    return RenderAttempt.questioned(
        (
            f"sha256:{sha256(originals[0].read_bytes()).hexdigest()}",
            f"sha256:{sha256(flipped.read_bytes()).hexdigest()}",
        ),
        error_code or decision.primary_code or "DIRECTION_SIGN_WEAK",
    )


def _planning_needs_camera_candidates(payload: dict) -> bool:
    diagnostics = payload.get("diagnostics") or ()
    return any(
        str(code).strip().upper() in PLANNING_CAMERA_FLIP_DIAGNOSTICS
        for code in diagnostics
    )


def _flipped_camera_variant_index(
    payload: dict,
    *,
    current_index: int,
    attempted: set[int],
) -> int | None:
    variants = payload.get("presentation", {}).get("variants", [])
    if not isinstance(variants, list) or not (0 <= current_index < len(variants)):
        return None
    try:
        current_camera = str(variants[current_index]["camera_id"])
        current_zoom = float(variants[current_index]["zoom"])
    except (KeyError, TypeError, ValueError):
        return None
    named: int | None = None
    same_zoom: int | None = None
    any_other: int | None = None
    for index, variant in enumerate(variants):
        if index in attempted or not isinstance(variant, dict):
            continue
        camera_id = str(variant.get("camera_id") or "")
        if not camera_id or camera_id == current_camera:
            continue
        if str(variant.get("variant_id") or "") == "flipped-camera":
            named = index
            break
        try:
            zoom = float(variant["zoom"])
        except (KeyError, TypeError, ValueError):
            continue
        if same_zoom is None and math.isclose(zoom, current_zoom):
            same_zoom = index
        if any_other is None:
            any_other = index
    return named if named is not None else (
        same_zoom if same_zoom is not None else any_other
    )


def _presentation_camera_id(payload: dict, variant_index: int) -> str:
    try:
        return str(payload["presentation"]["variants"][variant_index]["camera_id"])
    except (KeyError, IndexError, TypeError):
        return "unknown-camera"


def _camera_gate_measurements(
    payload: dict,
    *,
    variant_index: int,
) -> dict[str, object]:
    try:
        variant = payload["presentation"]["variants"][variant_index]
        camera_id = str(variant["camera_id"])
        camera = payload["camera_catalog"][camera_id]
        view = _unit_tuple(camera["position_direction_root"])
        normal = _unit_tuple(payload["receiver_normal_root"])
        translation = tuple(float(value) for value in payload["translation_vector_root"])
        if len(translation) != 3:
            raise ValueError
        receiver_dot = sum(normal[index] * view[index] for index in range(3))
        along_view = sum(translation[index] * view[index] for index in range(3))
        projected = tuple(
            translation[index] - along_view * view[index] for index in range(3)
        )
        projected_length = math.sqrt(sum(value * value for value in projected))
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError):
        return {
            "camera_id": None,
            "camera_receiver_dot": None,
            "projected_explosion_length": None,
        }
    return {
        "camera_id": camera_id,
        "camera_receiver_dot": receiver_dot,
        "projected_explosion_length": projected_length,
    }


def _unit_tuple(value: object) -> tuple[float, float, float]:
    vector = tuple(float(item) for item in value)  # type: ignore[arg-type]
    if len(vector) != 3:
        raise ValueError
    length = math.sqrt(sum(item * item for item in vector))
    if length <= 1.0e-12:
        raise ValueError
    return tuple(item / length for item in vector)


def _passed_image(path: Path) -> RenderAttempt:
    if not path.is_file() or path.stat().st_size == 0:
        return RenderAttempt.retryable("RENDER_OUTPUT_MISSING")
    return RenderAttempt.passed(f"sha256:{sha256(path.read_bytes()).hexdigest()}")


def _reviewable_image(path: Path, error_code: str) -> RenderAttempt:
    if not path.is_file() or path.stat().st_size == 0:
        return RenderAttempt.retryable("RENDER_OUTPUT_MISSING")
    return RenderAttempt.reviewable(
        f"sha256:{sha256(path.read_bytes()).hexdigest()}", error_code
    )


def _gate_attempt(path: Path, failures: tuple[str, ...]) -> RenderAttempt:
    decision = classify_failures(failures)
    if decision.category in {
        GateCategory.AUTO_REPAIR,
        GateCategory.HUMAN_REVIEW,
    }:
        return _reviewable_image(path, decision.primary_code)
    if decision.category is GateCategory.SYSTEM_RETRY:
        return RenderAttempt.retryable(decision.primary_code)
    return RenderAttempt.failed(decision.primary_code)


def _batch_failure(error_code: str) -> RenderAttempt:
    if error_code == "FRAMING_FRAME_BUDGET_EXCEEDED":
        return RenderAttempt.failed(error_code)
    return RenderAttempt.retryable(error_code)


def _next_presentation_variant(
    payload: dict,
    *,
    current_index: int,
    attempted: set[int],
    failures: tuple[str, ...],
) -> int | None:
    decision = classify_failures(failures)
    if decision.category not in {
        GateCategory.AUTO_REPAIR,
        GateCategory.HUMAN_REVIEW,
        GateCategory.SYSTEM_RETRY,
    }:
        return None
    variants = payload.get("presentation", {}).get("variants", [])
    if not isinstance(variants, list) or not (0 <= current_index < len(variants)):
        return None
    try:
        current_zoom = float(variants[current_index]["zoom"])
    except (KeyError, TypeError, ValueError):
        return None
    try:
        current_camera = str(variants[current_index]["camera_id"])
    except (KeyError, TypeError):
        current_camera = ""
    candidates: list[tuple[float, int]] = []
    failure_set = set(failures)
    camera_repair = bool(CAMERA_FLIP_FAILURES & failure_set) or (
        _planning_needs_camera_candidates(payload)
    )
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
        camera_id = str(variant.get("camera_id") or "")
        if camera_repair and camera_id and camera_id != current_camera:
            candidates.append((abs(zoom - current_zoom), index))
        elif shrink and zoom < current_zoom:
            candidates.append((-zoom, index))
        elif not shrink and grow and zoom > current_zoom:
            candidates.append((zoom, index))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]
