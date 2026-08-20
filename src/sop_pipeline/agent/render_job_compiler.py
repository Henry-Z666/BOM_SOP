from __future__ import annotations

from typing import Any

from .framing_scale import FramingScaleError, build_framing_scale_evidence
from .formal_render_planner import FormalRenderPlan, FormalRenderStep
from .render_scheduler import RenderPlan, RenderTask


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
            "title": step.title,
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
            "presentation": _compile_presentation(plan, step),
            "arrow_anchors": _compile_arrow_anchors(step),
            "arrow_renderer": "creo_display_list/v1",
            "diagnostics": list(step.diagnostics),
            "repair_space": {
                "camera_ids": list(step.allowed_camera_ids),
                "explosion_scales": [0.85, 1.0, 1.15],
                "pan_offsets": [[0, 0]],
                "zoom_scales": [1.0],
                "framing_repairs": (
                    "bounded_scale_bucket_probe/v1"
                    if plan.occurrence_bounds_root
                    else "frozen_pending_scale_derivation/v1"
                ),
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


def _execution_mode(step: FormalRenderStep) -> str:
    if step.status == "ready":
        return "formal"
    renderable_review_codes = {
        "DIRECTION_SIGN_WEAK",
        "RECEIVER_NORMAL_NOT_AXIS_ALIGNED",
        "CAMERA_RECEIVER_WRONG_HALF_SPACE",
        "CAMERA_RECEIVER_SILHOUETTE",
        "EXPLOSION_NOT_VISIBLE_IN_CAMERA",
    }
    if step.diagnostics and set(step.diagnostics).issubset(renderable_review_codes):
        required = (
            step.receiver_point_root,
            step.receiver_normal_root,
            step.translation_vector_root,
            step.camera_id,
            step.receiver_occurrences,
            step.constraint_ids,
            step.arrow_anchors,
        )
        if all(required):
            return "diagnostic_preview"
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


def _compile_presentation(
    plan: FormalRenderPlan, step: FormalRenderStep
) -> dict[str, Any]:
    framing_profile = _framing_profile_contract(plan, step)
    if step.camera_id is None:
        return {
            "schema_version": "fixed-frame-presentation/v1",
            "focus_context": "stage_visible_bbox/v1",
            "framing_priority": "installation_activity/v1",
            "zoom_anchor": "installation_activity_center/v1",
            "native_refit": _native_refit_contract(),
            "native_selected_fit": _native_selected_fit_contract(framing_profile),
            "framing_profile": framing_profile,
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
        "native_refit": _native_refit_contract(),
        "native_selected_fit": _native_selected_fit_contract(framing_profile),
        "framing_profile": framing_profile,
        "centering": _centering_contract(),
        "zoom_recovery": _zoom_recovery_contract(),
        "variants": [
            {
                "variant_id": "base",
                "camera_id": step.camera_id,
                "zoom": 1.0,
                "pan": [0.0, 0.0],
            },
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


def _framing_profile_contract(
    plan: FormalRenderPlan, step: FormalRenderStep
) -> dict[str, Any]:
    frozen = {
        "schema_version": "frozen-framing-profile-policy/v3",
        "policy": "native_zoom_to_selected/v1",
        "scale_signature": "creo_selected_object_bbox/v1",
        "probe_interface_status": "frozen_diagnostic_only/v1",
        "on_failure": "question_without_probe/v1",
    }
    if step.camera_id is None:
        return frozen
    camera = _compile_camera_catalog(plan).get(step.camera_id)
    if camera is None:
        return frozen
    try:
        evidence = build_framing_scale_evidence(
            occurrence_bounds_root=plan.occurrence_bounds_root,
            moving_occurrences=step.moving_occurrences,
            receiver_occurrences=step.receiver_occurrences,
            visible_occurrences=step.visible_occurrences,
            translation_vector_root=step.translation_vector_root,
            stage_scope_occurrence=step.stage_scope_occurrence,
            camera=camera,
        )
    except FramingScaleError:
        return frozen
    return {**frozen, "scale_evidence": evidence}


def _native_selected_fit_contract(framing_profile: dict[str, Any]) -> dict[str, Any]:
    level = 0.3
    evidence = framing_profile.get("scale_evidence", {})
    if isinstance(evidence, dict) and evidence.get("status") == "available":
        moving = evidence.get("moving_projected_size_root", [])
        installation = evidence.get("installation_projected_size_root", [])
        try:
            moving_size = max(float(value) for value in moving)
            installation_size = max(float(value) for value in installation)
            if moving_size > 0.0 and installation_size > 0.0:
                level = round(
                    max(0.15, min(0.45, 0.45 * moving_size / installation_size)),
                    2,
                )
        except (TypeError, ValueError):
            pass
    return {
        "schema_version": "native-selected-fit/v1",
        "command": "ProCmdZoomIntoOutline",
        "selection_scope": "moving_occurrences/v1",
        "zoom_to_selected_level": level,
        "level_policy": "cad_installation_envelope/v2",
        "max_commands_per_render": 1,
        "absolute_pan_zoom_forbidden": True,
    }


def _native_refit_contract() -> dict[str, Any]:
    return {
        "schema_version": "native-focus-refit/v1",
        "fit_occurrences": "moving_only/v1",
        "restore_stage_context_without_refit": True,
    }


def _zoom_recovery_contract() -> dict[str, Any]:
    return {
        "schema_version": "centered-span-zoom/v1",
        "target_subject_span": 0.55,
        "min_zoom": 0.4,
        "max_zoom": 32.0,
        "max_rounds": 3,
    }
