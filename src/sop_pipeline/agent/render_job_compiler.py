from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from .formal_render_planner import FormalRenderPlan, FormalRenderStep
from .render_scheduler import RenderPlan, RenderTask


SUPPORTED_SCHEMA = "creo-render-jobs/v3"


def compile_locked_render_jobs(plan: FormalRenderPlan) -> RenderPlan:
    """Compile one immutable geometry plan into bounded worker tasks."""

    if plan.schema_version != "formal-render-plan/v2":
        raise ValueError("render job compilation requires formal-render-plan/v2")
    required_decisions = {
        f"subassembly-scope-{item.bom_rows[0]:04d}"
        for item in plan.diagnostics
        if item.code == "SUBASSEMBLY_SCOPE_UNCONFIRMED" and item.bom_rows
    }
    if set(plan.scope_decisions) != required_decisions:
        raise ValueError("formal render plan must be locked before job compilation")

    known_steps = {step.step_id for step in plan.steps}
    assembly_sha256 = str(plan.camera_basis.get("assembly_sha256", ""))
    if not assembly_sha256.startswith("sha256:"):
        raise ValueError("formal render plan lacks authoritative assembly SHA-256")
    tasks: list[RenderTask] = []
    for index, step in enumerate(plan.steps):
        unknown_dependencies = set(step.depends_on) - known_steps
        if unknown_dependencies:
            raise ValueError(
                f"render step {step.step_id} has unknown dependencies: "
                + ", ".join(sorted(unknown_dependencies))
            )
        mode = _execution_mode(step)
        _validate_step_contract(step, mode)
        payload = {
            "schema_version": "creo-render-task/v1",
            "source_plan_fingerprint": plan.fingerprint,
            "assembly_file": plan.assembly_file,
            "authoritative_assembly": {
                "assembly_file": plan.assembly_file,
                "sha256": assembly_sha256,
                "coordinate_system": str(
                    plan.camera_basis.get("coordinate_system", "root_asm")
                ),
            },
            "plan_index": index,
            "execution_mode": mode,
            "stage_scope_occurrence": step.stage_scope_occurrence,
            "moving_occurrences": list(step.moving_occurrences),
            "receiver_occurrences": list(step.receiver_occurrences),
            "visible_occurrences": list(step.visible_occurrences),
            "constraint_ids": list(step.constraint_ids),
            "receiver_point_root": step.receiver_point_root,
            "receiver_normal_root": step.receiver_normal_root,
            "translation_vector_root": step.translation_vector_root,
            "camera_id": step.camera_id,
            "allowed_camera_ids": list(step.allowed_camera_ids),
            "camera": _compile_camera(plan, step),
            "camera_catalog": _compile_camera_catalog(plan),
            "presentation": _compile_presentation(step),
            "arrow_anchors": _compile_arrow_anchors(step),
            "arrow_renderer": "creo_display_list/v1",
            "diagnostics": list(step.diagnostics),
            "repair_space": {
                "camera_ids": list(step.allowed_camera_ids),
                "explosion_scales": [0.85, 1.0, 1.15],
                "pan_offsets": [[0, 0], [-80, 0], [80, 0], [0, -80], [0, 80]],
                "zoom_scales": [0.8, 0.85, 1.0, 1.5, 2.1],
            },
        }
        tasks.append(
            RenderTask(
                task_id=step.step_id,
                step_id=step.step_id,
                main_process_id=step.main_process_id,
                depends_on=step.depends_on,
                complete_state_hash=step.complete_state_hash,
                blocks_dependents_on_failure=False,
                payload=payload,
            )
        )
    if not tasks:
        raise ValueError("locked render plan contains no steps")
    return RenderPlan(schema_version="render-plan/v2", tasks=tuple(tasks))


def compile_creo_render_jobs(contract_file: Path) -> RenderPlan:
    """Compile the legacy Creo job document into the Agent render seam."""

    payload = json.loads(contract_file.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SUPPORTED_SCHEMA:
        raise ValueError(
            f"unsupported Creo render contract: {payload.get('schema_version')}"
        )
    jobs = payload.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("Creo render contract requires a non-empty jobs array")

    tasks: list[RenderTask] = []
    prior_step_id: str | None = None
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("each Creo render job must be an object")
        job_id = _required_text(job, "job_id")
        bom_level = _required_text(job, "bom_level")
        completed = job.get("stage_visibility", {}).get(
            "completed_occurrences", []
        )
        moving = job.get("moving_occurrences", [])
        state_payload = {
            "completed_occurrences": completed,
            "moving_occurrences": moving,
        }
        tasks.append(
            RenderTask(
                task_id=job_id,
                step_id=job_id,
                main_process_id=_main_process_id(bom_level),
                depends_on=(prior_step_id,) if prior_step_id else (),
                complete_state_hash=_canonical_hash(state_payload),
                blocks_dependents_on_failure=False,
                payload={**job, "contract_index": len(tasks)},
            )
        )
        prior_step_id = job_id

    return RenderPlan(schema_version="render-plan/v1", tasks=tuple(tasks))


def _execution_mode(step: FormalRenderStep) -> str:
    if step.status == "ready":
        return "formal"
    if (
        step.translation_vector_root is not None
        and step.camera_id is not None
        and len(step.arrow_anchors) == len(step.moving_occurrences)
    ):
        return "candidate_search"
    return "placeholder"


def _validate_step_contract(step: FormalRenderStep, mode: str) -> None:
    if not step.moving_occurrences:
        raise ValueError(f"render step {step.step_id} has no moving occurrence")
    visible = set(step.visible_occurrences)
    required_visible = set(step.moving_occurrences) | set(step.receiver_occurrences)
    if not required_visible.issubset(visible):
        raise ValueError(f"render step {step.step_id} omits required visible occurrences")
    if step.allowed_camera_ids != ("fixed_123", "fixed_456"):
        raise ValueError(f"render step {step.step_id} has an invalid camera repair set")
    if mode == "placeholder":
        return
    if (
        step.receiver_point_root is None
        or step.receiver_normal_root is None
        or step.translation_vector_root is None
        or step.camera_id not in step.allowed_camera_ids
        or not step.receiver_occurrences
        or not step.constraint_ids
    ):
        raise ValueError(f"render step {step.step_id} lacks required geometry")
    anchored = {item.occurrence_id for item in step.arrow_anchors}
    if anchored != set(step.moving_occurrences) or len(step.arrow_anchors) != len(
        step.moving_occurrences
    ):
        raise ValueError(f"render step {step.step_id} lacks same-CAD-point arrow anchors")


def _compile_arrow_anchors(step: FormalRenderStep) -> list[dict[str, Any]]:
    if step.translation_vector_root is None:
        return []
    translation = step.translation_vector_root
    return [
        {
            "occurrence_id": anchor.occurrence_id,
            "constraint_id": anchor.constraint_id,
            "complete_point_root": list(anchor.complete_point_root),
            "expected_exploded_point_root": [
                round(anchor.complete_point_root[index] + translation[index], 6)
                for index in range(3)
            ],
        }
        for anchor in step.arrow_anchors
    ]


def _compile_camera(plan: FormalRenderPlan, step: FormalRenderStep) -> dict[str, Any] | None:
    if step.camera_id is None:
        return None
    basis = plan.camera_basis
    direction = basis.get(f"{step.camera_id}_position_direction_root")
    up = basis.get("up_reference_root")
    if not isinstance(direction, list) or len(direction) != 3:
        raise ValueError(f"render step {step.step_id} lacks its fixed camera direction")
    if not isinstance(up, list) or len(up) != 3:
        raise ValueError(f"render step {step.step_id} lacks its fixed camera up reference")
    return {
        "id": step.camera_id,
        "position_direction_root": direction,
        "up_reference_root": up,
        "zoom": 1.0,
        "pan": [0.0, 0.0],
        "frame": "square",
    }


def _compile_camera_catalog(plan: FormalRenderPlan) -> dict[str, dict[str, Any]]:
    basis = plan.camera_basis
    up = basis.get("up_reference_root")
    if not isinstance(up, list) or len(up) != 3:
        raise ValueError("formal render plan lacks its camera up reference")
    result: dict[str, dict[str, Any]] = {}
    for camera_id in ("fixed_123", "fixed_456"):
        direction = basis.get(f"{camera_id}_position_direction_root")
        if not isinstance(direction, list) or len(direction) != 3:
            raise ValueError(f"formal render plan lacks {camera_id}")
        result[camera_id] = {
            "id": camera_id,
            "position_direction_root": direction,
            "up_reference_root": up,
        }
    return result


def _compile_presentation(step: FormalRenderStep) -> dict[str, Any]:
    if step.camera_id is None:
        return {
            "schema_version": "fixed-frame-presentation/v1",
            "focus_context": "stage_visible_bbox/v1",
            "framing_priority": "installation_activity/v1",
            "zoom_anchor": "installation_activity_center/v1",
            "centering": _centering_contract(),
            "zoom_recovery": _zoom_recovery_contract(),
            "variants": [],
            "frame_gate": _frame_gate(),
        }
    return {
        "schema_version": "fixed-frame-presentation/v1",
        "focus_context": "stage_visible_bbox/v1",
        "framing_priority": "installation_activity/v1",
        "zoom_anchor": "installation_activity_center/v1",
        "centering": _centering_contract(),
        "zoom_recovery": _zoom_recovery_contract(),
        "variants": [
            {
                "variant_id": variant_id,
                "camera_id": step.camera_id,
                "zoom": zoom,
                "pan": [0.0, 0.0],
            }
            for variant_id, zoom in (
                ("base", 1.0),
                ("zoom-in-50", 1.5),
                ("zoom-in-110", 2.1),
                ("zoom-out-15", 0.85),
            )
        ],
        "frame_gate": _frame_gate(),
    }


def _frame_gate() -> dict[str, Any]:
    return {
        "schema_version": "raster-composition-gate/v2",
        "foreground_delta": 30,
        "min_component_pixels": 32,
        "component_downsample": 4,
        "min_subject_span": 0.54,
        "max_subject_span": 1.0,
        "max_clipped_edges": 2,
        "arrow_green_delta": 20,
        "min_arrow_pixels": 120,
        "min_arrow_span_pixels": 24,
        "min_arrow_border_margin_pixels": 40,
        "ignored_regions": [[0, 1200, 500, 1600]],
    }


def _centering_contract() -> dict[str, Any]:
    return {
        "schema_version": "adaptive-screen-center/v1",
        "activity_bbox": "subject_plus_native_arrow/v1",
        "initial_estimate": "cad_activity_origin/v1",
        "focus_center": "midpoint_subject_arrow/v1",
        "probe_policy": "on_gate_failure/v1",
        "response_cache_scope": "camera_frame_environment/v2",
        "max_probe_rounds": 2,
        "target_pixel": [800, 800],
        "probe_delta": 0.1,
        "max_abs_pan": 1.0,
        "max_activity_center_offset_pixels": 120,
        "max_arrow_center_offset_pixels": 120,
    }


def _zoom_recovery_contract() -> dict[str, Any]:
    return {
        "schema_version": "centered-span-zoom/v1",
        "target_subject_span": 0.55,
        "min_zoom": 0.4,
        "max_zoom": 3.2,
        "max_rounds": 2,
    }


def _required_text(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Creo render job requires {field_name}")
    return value.strip()


def _main_process_id(bom_level: str) -> str:
    match = re.match(r"\s*(\d+)", bom_level)
    if match is None:
        raise ValueError(f"cannot determine main process from BOM level: {bom_level}")
    return match.group(1)


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"
