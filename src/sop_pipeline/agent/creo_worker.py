from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Protocol

from sop_pipeline.camera_visibility import (
    CAMERA_VISIBILITY_AUDIT_ENABLED,
    VisibilityThresholds,
    apply_camera_selection,
    audit_camera_visibility_files,
    select_camera_from_visibility_audits,
)

from ..process_control import owned_process_creation_kwargs, terminate_process_tree
from .gate_policy import GateCategory, classify_failures, gate_policy
from .render_scheduler import RenderAttempt, RenderPlan, RenderTask
from .render_validation import (
    DeterministicNativeRenderValidator,
    NativeRenderGateReport,
)


MAX_RENDER_RASTERS_PER_ATTEMPT = 1


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
class CreoSession:
    output_directory: Path
    prepared_models_root: Path | None = None
    native_worker_root: Path | None = None
    native_worker_active: bool = False
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
        camera_visibility_enabled: bool = CAMERA_VISIBILITY_AUDIT_ENABLED,
    ) -> None:
        self.powershell = powershell
        self.batch_script = batch_script
        self.models_root = models_root
        self.render_plan_json = render_plan_json
        self.runtime_config = runtime_config
        self.stop_script = stop_script or batch_script.with_name(
            "stop_agent_native_worker.ps1"
        )
        self.camera_visibility_enabled = bool(camera_visibility_enabled)
        self.runner = runner or SubprocessCommandRunner()
        self.validator = validator or DeterministicNativeRenderValidator(
            camera_visibility_enabled=self.camera_visibility_enabled
        )
        self._diagnostics_by_task: dict[str, dict[str, object]] = {}

    def diagnostic_for(self, task_id: str) -> dict[str, object] | None:
        value = self._diagnostics_by_task.get(task_id)
        return dict(value) if value is not None else None

    def open_session(self, run_workspace: Path, plan: RenderPlan) -> CreoSession:
        if plan.schema_version != "render-plan/v2":
            raise ValueError("Agent native worker requires render-plan/v2")
        output_directory = run_workspace / "rendered"
        output_directory.mkdir(parents=True, exist_ok=True)
        return CreoSession(
            output_directory=output_directory,
            native_worker_root=run_workspace / "internal" / "native-worker",
        )

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
        visibility_execution_error = self._ensure_camera_visibility(
            session, task, plan_index=plan_index
        )
        if visibility_execution_error is not None:
            return _batch_failure(visibility_execution_error)
        task, visibility_plan, visibility_error = self._lock_camera_visibility(
            session, task
        )
        if visibility_error is not None:
            if visibility_error == "CAMERA_VISIBILITY_AUDIT_MISSING":
                return RenderAttempt.retryable(visibility_error)
            return RenderAttempt.failed(visibility_error)
        variant_index = 0
        try:
            variant = task.payload["presentation"]["variants"][variant_index]
            profile_contract = task.payload["presentation"].get(
                "framing_profile", {}
            )
            if profile_contract.get("policy") != "native_zoom_to_selected/v1":
                raise ValueError("only native selected framing is supported")
            if (
                not math.isclose(float(variant["zoom"]), 1.0)
                or tuple(float(value) for value in variant["pan"]) != (0.0, 0.0)
            ):
                raise ValueError("native selected framing requires Zoom=1 and PAN=0")
        except (KeyError, IndexError, TypeError, ValueError):
            return RenderAttempt.failed("FRAMING_PROFILE_CONTRACT_INVALID")
        execution_error = self._run_batch(
            session,
            plan_path=visibility_plan or self.render_plan_json,
            output_directory=session.output_directory,
            start_index=plan_index,
            count=1,
            variant_index=variant_index,
            budget_task_id=f"{task.task_id}:attempt-{attempt}",
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
        if report.passed:
            return RenderAttempt.passed(
                f"sha256:{sha256(image_path.read_bytes()).hexdigest()}"
            )
        return _gate_attempt(image_path, report.failures)

    def _ensure_camera_visibility(
        self,
        session: CreoSession,
        task: RenderTask,
        *,
        plan_index: int,
    ) -> str | None:
        if not self.camera_visibility_enabled:
            return None
        contract = task.payload.get("camera_visibility")
        if not isinstance(contract, dict) or contract.get("status") != "ready":
            return None
        audit_root = session.output_directory.parent / "internal" / "camera-visibility"
        safe_task_id = _safe_name(task.task_id)
        expected = tuple(
            audit_root / f"{safe_task_id}.{camera_id}.{kind}.png"
            for camera_id in ("fixed_123", "fixed_456")
            for kind in ("isolated", "staged")
        )
        if all(path.is_file() for path in expected):
            return None
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
            str(audit_root),
            "-StartIndex",
            str(plan_index),
            "-Count",
            "1",
            "-Operation",
            "Visibility",
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
            self._record_batch_diagnostic(session, task.task_id, "CREO_TIMEOUT", message=str(error))
            return "CREO_TIMEOUT"
        except OSError as error:
            self._record_batch_diagnostic(session, task.task_id, "CREO_PROCESS_ERROR", message=str(error))
            return "CREO_PROCESS_ERROR"
        prepared_match = self._PREPARED_PATTERN.search(result.stdout or "")
        if prepared_match is not None:
            session.prepared_models_root = Path(prepared_match.group(1).strip())
        if self._WORKER_PATTERN.search(result.stdout or "") is not None:
            session.native_worker_active = True
        if result.returncode != 0:
            self._record_batch_diagnostic(
                session, task.task_id, "CREO_RENDER_FAILED",
                returncode=result.returncode, stdout=result.stdout or "", stderr=result.stderr or "",
            )
            return "CREO_RENDER_FAILED"
        if not all(path.is_file() for path in expected):
            return "CAMERA_VISIBILITY_AUDIT_MISSING"
        return None

    def _lock_camera_visibility(
        self,
        session: CreoSession,
        task: RenderTask,
    ) -> tuple[RenderTask, Path | None, str | None]:
        if not self.camera_visibility_enabled:
            return task, None, None
        contract = task.payload.get("camera_visibility")
        if not isinstance(contract, dict) or contract.get("status") != "ready":
            return task, None, None
        audit_root = session.output_directory.parent / "internal" / "camera-visibility"
        safe_task_id = _safe_name(task.task_id)
        raster_paths = {
            (camera_id, kind): audit_root
            / f"{safe_task_id}.{camera_id}.{kind}.png"
            for camera_id in ("fixed_123", "fixed_456")
            for kind in ("isolated", "staged")
        }
        if not all(path.is_file() for path in raster_paths.values()):
            return task, None, "CAMERA_VISIBILITY_AUDIT_MISSING"
        try:
            thresholds_payload = contract.get("thresholds")
            if not isinstance(thresholds_payload, dict) or (
                thresholds_payload.get("schema_version")
                != "camera-visibility-thresholds/v1"
            ):
                raise ValueError("camera visibility thresholds are invalid")
            thresholds = VisibilityThresholds(
                **{
                    key: value
                    for key, value in thresholds_payload.items()
                    if key != "schema_version"
                }
            )
            moving_labels = contract.get("moving_labels")
            receiver_labels = contract.get("receiver_interface_labels")
            if not isinstance(moving_labels, dict) or not isinstance(
                receiver_labels, dict
            ):
                raise ValueError("camera visibility labels are invalid")
            audits = tuple(
                audit_camera_visibility_files(
                    camera_id=camera_id,
                    isolated_raster=raster_paths[(camera_id, "isolated")],
                    staged_raster=raster_paths[(camera_id, "staged")],
                    moving_labels=tuple(int(value) for value in moving_labels.values()),
                    receiver_labels=tuple(
                        int(value) for value in receiver_labels.values()
                    ),
                    thresholds=thresholds,
                )
                for camera_id in ("fixed_123", "fixed_456")
            )
            decision = select_camera_from_visibility_audits(audits)
            decision_path = audit_root / f"{safe_task_id}.decision.json"
            decision_temporary = decision_path.with_suffix(".json.tmp")
            decision_temporary.write_text(
                json.dumps(
                    decision.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                ),
                encoding="utf-8",
            )
            decision_temporary.replace(decision_path)
            if decision.status != "selected":
                self._diagnostics_by_task[task.task_id] = {
                    "schema_version": "camera-resolution-request/v1",
                    "task_id": task.task_id,
                    "gate_code": "NO_ELIGIBLE_FIXED_CAMERA",
                    "camera_selection": decision.to_dict(),
                    "resolution_options": [
                        dict(option) for option in decision.options
                    ],
                }
                return task, None, "NO_ELIGIBLE_FIXED_CAMERA"
            locked_payload = apply_camera_selection(task.payload, decision)
            locked_task = replace(task, payload=locked_payload)
            plan_payload = json.loads(self.render_plan_json.read_text(encoding="utf-8"))
            tasks = plan_payload.get("tasks")
            if not isinstance(tasks, list):
                raise ValueError("render plan tasks are invalid")
            plan_index = int(task.payload["plan_index"])
            if (
                plan_index < 0
                or plan_index >= len(tasks)
                or str(tasks[plan_index].get("task_id")) != task.task_id
            ):
                raise ValueError("camera decision does not match its render task")
            tasks[plan_index]["payload"] = locked_payload
            audit_root.mkdir(parents=True, exist_ok=True)
            locked_plan = audit_root / f"{safe_task_id}.locked-plan.json"
            temporary = locked_plan.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(
                    plan_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                ),
                encoding="utf-8",
            )
            temporary.replace(locked_plan)
            return locked_task, locked_plan, None
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return task, None, "CAMERA_VISIBILITY_AUDIT_INVALID"

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
        if used + count > MAX_RENDER_RASTERS_PER_ATTEMPT:
            return "RENDER_FRAME_BUDGET_EXCEEDED"
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


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "render-task"


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
        receiver_signed_dot = sum(
            normal[index] * view[index] for index in range(3)
        )
        receiver_dot = abs(receiver_signed_dot)
        along_view = sum(translation[index] * view[index] for index in range(3))
        projected = tuple(
            translation[index] - along_view * view[index] for index in range(3)
        )
        projected_length = math.sqrt(sum(value * value for value in projected))
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError):
        return {
            "camera_id": None,
            "camera_receiver_dot": None,
            "camera_receiver_signed_dot": None,
            "projected_explosion_length": None,
        }
    return {
        "camera_id": camera_id,
        "camera_receiver_dot": receiver_dot,
        "camera_receiver_signed_dot": receiver_signed_dot,
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
    if error_code == "RENDER_FRAME_BUDGET_EXCEEDED":
        return RenderAttempt.failed(error_code)
    return RenderAttempt.retryable(error_code)
