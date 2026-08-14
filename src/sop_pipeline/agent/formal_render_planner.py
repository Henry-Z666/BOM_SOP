from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import math
from typing import Any

from sop_pipeline.camera_planner import (
    calibrate_camera_basis,
    classify_receiver_face,
    generate_camera_candidates,
)

from .bom_cad_mapper import BomCadMap, BomOccurrenceMapping
from .bom_normalizer import NormalizedBom, NormalizedBomRow
from .draft_planner import DraftPlan


@dataclass(frozen=True)
class PlanningDiagnostic:
    code: str
    message: str
    bom_rows: tuple[int, ...] = ()
    occurrence_ids: tuple[str, ...] = ()
    affected_steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArrowAnchorEvidence:
    occurrence_id: str
    constraint_id: str
    complete_point_root: tuple[float, float, float]


@dataclass(frozen=True)
class FormalRenderStep:
    step_id: str
    main_process_id: str
    title: str
    source_bom_rows: tuple[int, ...]
    stage_scope_occurrence: str
    moving_occurrences: tuple[str, ...]
    receiver_occurrences: tuple[str, ...]
    visible_occurrences: tuple[str, ...]
    constraint_ids: tuple[str, ...]
    receiver_point_root: tuple[float, float, float] | None
    receiver_normal_root: tuple[float, float, float] | None
    translation_vector_root: tuple[float, float, float] | None
    arrow_anchors: tuple[ArrowAnchorEvidence, ...]
    camera_id: str | None
    allowed_camera_ids: tuple[str, str]
    depends_on: tuple[str, ...]
    affected_descendants: tuple[str, ...]
    state_delta: tuple[str, ...]
    complete_state_hash: str
    status: str
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class FormalRenderPlan:
    schema_version: str
    assembly_file: str
    camera_basis: dict[str, Any]
    initial_completed_occurrences: tuple[str, ...]
    scope_base_occurrences: dict[str, tuple[str, ...]]
    steps: tuple[FormalRenderStep, ...]
    diagnostics: tuple[PlanningDiagnostic, ...]
    ready_steps: int
    questioned_steps: int
    checkpoint_interval: int
    scope_decisions: dict[str, str]
    fingerprint: str


@dataclass(frozen=True)
class _OccurrenceEvidence:
    occurrence_id: str
    receiver_id: str
    constraint_id: str
    receiver_point: tuple[float, float, float]
    moving_anchor_point: tuple[float, float, float] | None
    outward_normal: tuple[float, float, float]
    alignment: float
    constraint_rank: int


def compile_formal_render_plan(
    bom: NormalizedBom,
    draft_plan: DraftPlan,
    mapping: BomCadMap,
    graph: dict[str, Any],
) -> FormalRenderPlan:
    """Compile BOM semantics and Creo evidence through one deterministic seam.

    The result is a geometry-backed plan, not a render pass.  Missing receiver
    geometry remains questioned and is never replaced by a centre-vector guess.
    """

    _validate_inputs(bom, draft_plan, mapping, graph)
    nodes = {str(node["occurrence_id"]): node for node in graph["occurrences"]}
    constraints = tuple(graph.get("constraints", ()))
    bom_rows = {row.row: row for row in bom.rows}
    root_mapping = next(
        (row for row in mapping.rows if row.occurrence_ids == ("ROOT",)), None
    )
    if root_mapping is None:
        raise ValueError("正式规划需要唯一 BOM 根物料映射到 ROOT")

    camera_basis = _camera_basis(graph)
    process_by_row = _process_index(draft_plan)
    descendant_rows = _descendant_rows(mapping.rows)
    diagnostics: list[PlanningDiagnostic] = []
    if camera_basis.get("calibration", {}).get("fallback"):
        diagnostics.append(
            PlanningDiagnostic(
                "CAMERA_BASIS_AUTO_COMPLETED",
                "Creo 打开总装后的视图不是三轴八分体；Agent 已保留方向符号与 Up，自动补全 fixed_123/fixed_456，并要求真实预览硬门复核。",
            )
        )
    scope_bases: dict[str, set[str]] = {}
    initial_completed: set[str] = set()
    raw_steps: list[tuple[tuple[int, int, int], FormalRenderStep]] = []

    root_children = [
        row
        for row in mapping.rows
        if row.parent_bom_row == root_mapping.bom_row and row.status == "matched"
    ]
    root_foundation = min(root_children, key=lambda row: row.bom_row) if root_children else None

    for mapped in sorted(mapping.rows, key=lambda row: row.bom_row):
        row = bom_rows[mapped.bom_row]
        if mapped.bom_row == root_mapping.bom_row or mapped.status == "non_renderable":
            continue
        if mapped.status != "matched":
            diagnostics.append(
                PlanningDiagnostic(
                    "BOM_OCCURRENCE_UNRESOLVED",
                    f"BOM 第 {mapped.bom_row} 行没有唯一、足量的 occurrence 映射。",
                    (mapped.bom_row,),
                    mapped.occurrence_ids,
                )
            )
            continue

        if root_foundation is not None and mapped.bom_row == root_foundation.bom_row:
            initial_completed.update(mapped.occurrence_ids)
            if not _fixed_to_parent(mapped, root_mapping, constraints):
                diagnostics.append(
                    PlanningDiagnostic(
                        "FOUNDATION_ASSUMED_FROM_BOM_ORDER",
                        "首个根级物料被作为总装基体，但 Creo 未提供 FIX 证据。",
                        (mapped.bom_row,),
                        mapped.occurrence_ids,
                    )
                )
            continue

        fixed_scope = _fixed_scope_parent(mapped, constraints)
        if fixed_scope is not None and fixed_scope != "ROOT":
            for occurrence_id in mapped.occurrence_ids:
                scope_bases.setdefault(fixed_scope, set()).add(occurrence_id)
            continue

        moving_set = set(mapped.occurrence_ids)
        evidence = {
            occurrence_id: _select_evidence(
                occurrence_id, moving_set, nodes, constraints
            )
            for occurrence_id in mapped.occurrence_ids
        }
        proven = [item for item in evidence.values() if item is not None]
        missing = tuple(
            occurrence_id
            for occurrence_id, item in evidence.items()
            if item is None
        )
        grouped: dict[tuple[int, int], list[_OccurrenceEvidence]] = {}
        for item in proven:
            face = classify_receiver_face(item.outward_normal, camera_basis)
            # Repeated identical BOM items belong in one image when their
            # proven installation directions use the same receiver face.  The
            # contract retains every distinct receiver occurrence, so this is
            # a presentation grouping rather than a loss of geometry evidence.
            key = (int(face["face_id"]), int(face["sign"]))
            grouped.setdefault(key, []).append(item)

        group_count = len(grouped) + (1 if missing else 0)
        group_index = 0
        for key in sorted(grouped):
            group_index += 1
            members = sorted(grouped[key], key=lambda item: _path_key(item.occurrence_id))
            step = _proven_step(
                row=row,
                mapped=mapped,
                members=members,
                group_index=group_index,
                group_count=group_count,
                process_id=process_by_row.get(mapped.bom_row, "process-unassigned"),
                camera_basis=camera_basis,
                nodes=nodes,
                display_distance=_display_distance(nodes),
            )
            rank = max(descendant_rows.get(mapped.bom_row, (mapped.bom_row,)))
            raw_steps.append(((rank, 1 if _mapped_assembly(mapped, nodes) else 0, mapped.bom_row), step))

        if missing:
            group_index += 1
            step_id = _step_id(
                process_by_row.get(mapped.bom_row, "process-unassigned"),
                mapped.bom_row,
                group_index,
            )
            scope = _common_ancestor(
                tuple(missing)
                + tuple(_parent_occurrence(item, nodes) for item in missing)
            )
            step = FormalRenderStep(
                step_id=step_id,
                main_process_id=process_by_row.get(mapped.bom_row, "process-unassigned"),
                title=_title(row, group_index, group_count),
                source_bom_rows=(mapped.bom_row,),
                stage_scope_occurrence=scope,
                moving_occurrences=tuple(sorted(missing, key=_path_key)),
                receiver_occurrences=(),
                visible_occurrences=(),
                constraint_ids=(),
                receiver_point_root=None,
                receiver_normal_root=None,
                translation_vector_root=None,
                arrow_anchors=(),
                camera_id=None,
                allowed_camera_ids=("fixed_123", "fixed_456"),
                depends_on=(),
                affected_descendants=(),
                state_delta=tuple(sorted(missing, key=_path_key)),
                complete_state_hash="",
                status="questioned",
                diagnostics=("NO_NATIVE_RECEIVER_GEOMETRY",),
            )
            rank = max(descendant_rows.get(mapped.bom_row, (mapped.bom_row,)))
            raw_steps.append(((rank, 1 if _mapped_assembly(mapped, nodes) else 0, mapped.bom_row), step))
            diagnostics.append(
                PlanningDiagnostic(
                    "NO_NATIVE_RECEIVER_GEOMETRY",
                    f"BOM 第 {mapped.bom_row} 行的部分 occurrence 无法证明接收件或离开方向。",
                    (mapped.bom_row,),
                    missing,
                    (step_id,),
                )
            )

    ordered = [item[1] for item in sorted(raw_steps, key=lambda item: (item[0], item[1].step_id))]
    ordered = _attach_dependencies(ordered)
    ordered = _topologically_order(ordered)
    ordered = _complete_plan_state(ordered, scope_bases, initial_completed)
    ordered = _attach_affected_descendants(ordered)
    diagnostics.extend(_subassembly_scope_diagnostics(ordered, nodes, bom_rows))
    ready_steps = sum(step.status == "ready" for step in ordered)
    questioned_steps = sum(step.status == "questioned" for step in ordered)
    payload = {
        "schema_version": "formal-render-plan/v2",
        "assembly_file": mapping.assembly_file,
        "camera_basis": camera_basis,
        "initial_completed_occurrences": sorted(initial_completed, key=_path_key),
        "scope_base_occurrences": {
            scope: sorted(values, key=_path_key)
            for scope, values in sorted(scope_bases.items())
        },
        "steps": [asdict(step) for step in ordered],
        "diagnostics": [asdict(item) for item in diagnostics],
        "ready_steps": ready_steps,
        "questioned_steps": questioned_steps,
        "checkpoint_interval": draft_plan.checkpoint_interval,
        "scope_decisions": {},
    }
    return FormalRenderPlan(
        schema_version="formal-render-plan/v2",
        assembly_file=mapping.assembly_file,
        camera_basis=camera_basis,
        initial_completed_occurrences=tuple(sorted(initial_completed, key=_path_key)),
        scope_base_occurrences={
            scope: tuple(sorted(values, key=_path_key))
            for scope, values in sorted(scope_bases.items())
        },
        steps=tuple(ordered),
        diagnostics=tuple(diagnostics),
        ready_steps=ready_steps,
        questioned_steps=questioned_steps,
        checkpoint_interval=draft_plan.checkpoint_interval,
        scope_decisions={},
        fingerprint=_canonical_hash(payload),
    )


def lock_formal_render_plan(
    plan: FormalRenderPlan,
    answers: dict[str, str],
    recommended_scopes: dict[str, str] | None = None,
) -> FormalRenderPlan:
    """Apply confirmed semantic choices and recompute all derived plan state."""

    if plan.schema_version not in {"formal-render-plan/v1", "formal-render-plan/v2"}:
        raise ValueError("不支持的正式规划版本")
    recommendations = recommended_scopes or {}
    removed: set[str] = set()
    decisions: dict[str, str] = {}
    by_step = {step.step_id: step for step in plan.steps}
    for diagnostic in plan.diagnostics:
        if diagnostic.code != "SUBASSEMBLY_SCOPE_UNCONFIRMED":
            continue
        row_number = diagnostic.bom_rows[0]
        item_id = f"subassembly-scope-{row_number:04d}"
        if item_id not in answers:
            raise ValueError(f"缺少子装配范围确认：{item_id}")
        answer = answers[item_id]
        if answer == "按BOM在本工位展开内部构造":
            decision = "expand"
        elif answer == "作为已完成整体安装":
            decision = "whole"
        elif answer == "不确定，按推荐方案生成":
            decision = recommendations.get(item_id, "expand")
        else:
            raise ValueError(f"无法识别子装配范围答案：{item_id}")
        if decision not in {"expand", "whole"}:
            raise ValueError(f"子装配推荐超出允许范围：{item_id}")
        decisions[item_id] = decision
        if decision == "whole":
            scopes = set(diagnostic.occurrence_ids)
            removed.update(
                step_id
                for step_id in diagnostic.affected_steps
                if (step := by_step.get(step_id)) is not None
                and step.stage_scope_occurrence in scopes
            )

    retained = [step for step in plan.steps if step.step_id not in removed]
    retained = _attach_dependencies(retained)
    retained = _topologically_order(retained)
    retained = _complete_plan_state(
        retained,
        {scope: set(values) for scope, values in plan.scope_base_occurrences.items()},
        set(plan.initial_completed_occurrences),
    )
    retained = _attach_affected_descendants(retained)
    ready_steps = sum(step.status == "ready" for step in retained)
    questioned_steps = sum(step.status == "questioned" for step in retained)
    payload = {
        "schema_version": plan.schema_version,
        "assembly_file": plan.assembly_file,
        "camera_basis": plan.camera_basis,
        "initial_completed_occurrences": plan.initial_completed_occurrences,
        "scope_base_occurrences": plan.scope_base_occurrences,
        "steps": [asdict(step) for step in retained],
        "diagnostics": [asdict(item) for item in plan.diagnostics],
        "ready_steps": ready_steps,
        "questioned_steps": questioned_steps,
        "checkpoint_interval": plan.checkpoint_interval,
        "scope_decisions": decisions,
    }
    return FormalRenderPlan(
        schema_version=plan.schema_version,
        assembly_file=plan.assembly_file,
        camera_basis=plan.camera_basis,
        initial_completed_occurrences=plan.initial_completed_occurrences,
        scope_base_occurrences=plan.scope_base_occurrences,
        steps=tuple(retained),
        diagnostics=plan.diagnostics,
        ready_steps=ready_steps,
        questioned_steps=questioned_steps,
        checkpoint_interval=plan.checkpoint_interval,
        scope_decisions=decisions,
        fingerprint=_canonical_hash(payload),
    )


def formal_render_plan_from_dict(payload: dict[str, Any]) -> FormalRenderPlan:
    if payload.get("schema_version") not in {"formal-render-plan/v1", "formal-render-plan/v2"}:
        raise ValueError("不支持的正式规划版本")
    return FormalRenderPlan(
        schema_version=str(payload["schema_version"]),
        assembly_file=str(payload["assembly_file"]),
        camera_basis=dict(payload["camera_basis"]),
        initial_completed_occurrences=tuple(payload["initial_completed_occurrences"]),
        scope_base_occurrences={
            str(scope): tuple(values)
            for scope, values in payload.get("scope_base_occurrences", {}).items()
        },
        steps=tuple(
            FormalRenderStep(
                **{
                    **item,
                    "source_bom_rows": tuple(item["source_bom_rows"]),
                    "moving_occurrences": tuple(item["moving_occurrences"]),
                    "receiver_occurrences": tuple(item["receiver_occurrences"]),
                    "visible_occurrences": tuple(item["visible_occurrences"]),
                    "constraint_ids": tuple(item["constraint_ids"]),
                    "receiver_point_root": tuple(item["receiver_point_root"])
                    if item.get("receiver_point_root") is not None
                    else None,
                    "receiver_normal_root": tuple(item["receiver_normal_root"])
                    if item.get("receiver_normal_root") is not None
                    else None,
                    "translation_vector_root": tuple(item["translation_vector_root"])
                    if item.get("translation_vector_root") is not None
                    else None,
                    "arrow_anchors": tuple(
                        ArrowAnchorEvidence(
                            occurrence_id=str(anchor["occurrence_id"]),
                            constraint_id=str(anchor["constraint_id"]),
                            complete_point_root=tuple(anchor["complete_point_root"]),
                        )
                        for anchor in item.get("arrow_anchors", ())
                    ),
                    "allowed_camera_ids": tuple(item["allowed_camera_ids"]),
                    "depends_on": tuple(item["depends_on"]),
                    "affected_descendants": tuple(item["affected_descendants"]),
                    "state_delta": tuple(item["state_delta"]),
                    "diagnostics": tuple(item.get("diagnostics", ())),
                }
            )
            for item in payload["steps"]
        ),
        diagnostics=tuple(
            PlanningDiagnostic(
                **{
                    **item,
                    "bom_rows": tuple(item.get("bom_rows", ())),
                    "occurrence_ids": tuple(item.get("occurrence_ids", ())),
                    "affected_steps": tuple(item.get("affected_steps", ())),
                }
            )
            for item in payload.get("diagnostics", ())
        ),
        ready_steps=int(payload["ready_steps"]),
        questioned_steps=int(payload["questioned_steps"]),
        checkpoint_interval=int(payload["checkpoint_interval"]),
        scope_decisions={
            str(key): str(value) for key, value in payload.get("scope_decisions", {}).items()
        },
        fingerprint=str(payload["fingerprint"]),
    )


def _validate_inputs(
    bom: NormalizedBom,
    draft_plan: DraftPlan,
    mapping: BomCadMap,
    graph: dict[str, Any],
) -> None:
    expected = (
        (bom.schema_version, "normalized-bom/v1"),
        (draft_plan.schema_version, "draft-plan/v1"),
        (mapping.schema_version, "bom-cad-map/v1"),
        (graph.get("schema_version"), "creo-cad-graph/v3"),
    )
    if any(actual != required for actual, required in expected):
        raise ValueError("正式规划输入 Schema 不兼容")
    if mapping.assembly_file.casefold() != str(graph.get("assembly_file", "")).casefold():
        raise ValueError("BOM/CAD 映射与 Creo 图谱不属于同一总装")
    if draft_plan.final_assembly.casefold() != mapping.assembly_file.casefold():
        raise ValueError("草案计划与锁定总装不一致")


def _camera_basis(graph: dict[str, Any]) -> dict[str, Any]:
    manifest = graph.get("authoritative_assembly", {})
    return calibrate_camera_basis(
        str(graph["assembly_file"]),
        str(manifest.get("sha256", "unavailable")),
        graph["default_view_matrix"],
    )


def _process_index(draft_plan: DraftPlan) -> dict[int, str]:
    result: dict[int, str] = {}
    for step in draft_plan.steps:
        for row in step.source_bom_rows:
            result.setdefault(int(row), step.main_process_id)
    return result


def _children_by_parent(rows: tuple[BomOccurrenceMapping, ...]) -> dict[int, list[BomOccurrenceMapping]]:
    result: dict[int, list[BomOccurrenceMapping]] = {}
    for row in sorted(rows, key=lambda item: item.bom_row):
        if row.parent_bom_row is not None and row.status == "matched":
            result.setdefault(row.parent_bom_row, []).append(row)
    return result


def _descendant_rows(rows: tuple[BomOccurrenceMapping, ...]) -> dict[int, tuple[int, ...]]:
    by_parent = _children_by_parent(rows)

    def collect(row_number: int) -> tuple[int, ...]:
        result = [row_number]
        for child in by_parent.get(row_number, ()):
            result.extend(collect(child.bom_row))
        return tuple(result)

    return {row.bom_row: collect(row.bom_row) for row in rows}


def _fixed_to_parent(
    mapped: BomOccurrenceMapping,
    parent: BomOccurrenceMapping,
    constraints: tuple[dict[str, Any], ...],
) -> bool:
    parent_occurrences = set(parent.occurrence_ids)
    for occurrence in mapped.occurrence_ids:
        if not any(
            edge.get("type") == "FIX"
            and edge.get("occurrences", [None, None])[0] == occurrence
            and edge.get("occurrences", [None, None])[1] in parent_occurrences
            for edge in constraints
            if len(edge.get("occurrences", ())) == 2
        ):
            return False
    return True


def _fixed_scope_parent(
    mapped: BomOccurrenceMapping,
    constraints: tuple[dict[str, Any], ...],
) -> str | None:
    parents: set[str] = set()
    for occurrence in mapped.occurrence_ids:
        matches = [
            str(edge["occurrences"][1])
            for edge in constraints
            if edge.get("type") == "FIX"
            and len(edge.get("occurrences", ())) == 2
            and str(edge["occurrences"][0]) == occurrence
        ]
        if len(matches) != 1:
            return None
        parents.add(matches[0])
    return next(iter(parents)) if len(parents) == 1 else None


def _select_evidence(
    occurrence_id: str,
    moving_set: set[str],
    nodes: dict[str, dict[str, Any]],
    constraints: tuple[dict[str, Any], ...],
) -> _OccurrenceEvidence | None:
    node = nodes.get(occurrence_id)
    if node is None:
        return None
    origin = _vector(node.get("transform", {}).get("origin"))
    if origin is None:
        return None
    candidates: list[_OccurrenceEvidence] = []
    for edge in constraints:
        ends = edge.get("occurrences")
        if not isinstance(ends, list) or len(ends) != 2 or str(ends[0]) != occurrence_id:
            continue
        receiver = str(ends[1])
        if receiver in moving_set or edge.get("type") == "FIX":
            continue
        reference = edge.get("assembly_reference")
        geometry = reference.get("geometry") if isinstance(reference, dict) else None
        if not isinstance(geometry, dict) or geometry.get("status") != "available":
            continue
        point = _vector(geometry.get("point_root"))
        normal = _unit(geometry.get("direction_root"))
        if point is None or normal is None:
            continue
        separation = tuple(origin[index] - point[index] for index in range(3))
        separation_unit = _unit(separation)
        alignment = abs(_dot(normal, separation_unit)) if separation_unit else 0.0
        sign = 1.0 if _dot(normal, separation) >= 0.0 else -1.0
        outward = tuple(sign * value for value in normal)
        component_reference = edge.get("component_reference")
        component_geometry = (
            component_reference.get("geometry")
            if isinstance(component_reference, dict)
            else None
        )
        moving_anchor = (
            _vector(component_geometry.get("point_root"))
            if isinstance(component_geometry, dict)
            and component_geometry.get("status") == "available"
            else None
        )
        candidates.append(
            _OccurrenceEvidence(
                occurrence_id,
                receiver,
                str(edge.get("id", "")),
                point,
                moving_anchor,
                outward,
                alignment,
                _constraint_rank(str(edge.get("type", ""))),
            )
        )
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            item.constraint_rank,
            -item.alignment,
            item.constraint_id,
        ),
    )


def _proven_step(
    *,
    row: NormalizedBomRow,
    mapped: BomOccurrenceMapping,
    members: list[_OccurrenceEvidence],
    group_index: int,
    group_count: int,
    process_id: str,
    camera_basis: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    display_distance: float,
) -> FormalRenderStep:
    normal = _unit(
        tuple(
            sum(item.outward_normal[index] for item in members)
            for index in range(3)
        )
    )
    if normal is None:
        raise ValueError("同组 occurrence 的接收面法向相互抵消")
    point = tuple(
        sum(item.receiver_point[index] for item in members) / len(members)
        for index in range(3)
    )
    translation = tuple(round(value * display_distance, 6) for value in normal)
    face = classify_receiver_face(normal, camera_basis)
    camera = generate_camera_candidates(camera_basis, face, translation)[0]
    moving = tuple(sorted((item.occurrence_id for item in members), key=_path_key))
    receivers = tuple(sorted({item.receiver_id for item in members}, key=_path_key))
    scope = _common_ancestor(moving + receivers)
    low_alignment = min(item.alignment for item in members) < 0.10
    low_face_confidence = face["confidence"] != "high"
    missing_arrow_anchor = any(item.moving_anchor_point is None for item in members)
    camera_gate = camera.get("hard_gate", {})
    step_diagnostics = tuple(
        code
        for code, active in (
            ("DIRECTION_SIGN_WEAK", low_alignment),
            ("RECEIVER_NORMAL_NOT_AXIS_ALIGNED", low_face_confidence),
            ("MOVING_ARROW_ANCHOR_UNAVAILABLE", missing_arrow_anchor),
            (
                "CAMERA_RECEIVER_WRONG_HALF_SPACE",
                not bool(camera_gate.get("receiver_outside_half_space")),
            ),
            (
                "CAMERA_RECEIVER_SILHOUETTE",
                not bool(camera_gate.get("receiver_face_not_silhouette")),
            ),
            (
                "EXPLOSION_NOT_VISIBLE_IN_CAMERA",
                not bool(camera_gate.get("projected_explosion_nonzero")),
            ),
        )
        if active
    )
    return FormalRenderStep(
        step_id=_step_id(process_id, mapped.bom_row, group_index),
        main_process_id=process_id,
        title=_title(row, group_index, group_count),
        source_bom_rows=(mapped.bom_row,),
        stage_scope_occurrence=scope,
        moving_occurrences=moving,
        receiver_occurrences=receivers,
        visible_occurrences=(),
        constraint_ids=tuple(sorted(item.constraint_id for item in members)),
        receiver_point_root=point,
        receiver_normal_root=normal,
        translation_vector_root=translation,
        arrow_anchors=tuple(
            ArrowAnchorEvidence(
                occurrence_id=item.occurrence_id,
                constraint_id=item.constraint_id,
                complete_point_root=item.moving_anchor_point,
            )
            for item in members
            if item.moving_anchor_point is not None
        ),
        camera_id=str(camera["id"]),
        allowed_camera_ids=("fixed_123", "fixed_456"),
        depends_on=(),
        affected_descendants=(),
        state_delta=moving,
        complete_state_hash="",
        status="questioned" if step_diagnostics else "ready",
        diagnostics=step_diagnostics,
    )


def _complete_plan_state(
    steps: list[FormalRenderStep],
    scope_bases: dict[str, set[str]],
    initial_completed: set[str],
) -> list[FormalRenderStep]:
    completed = {scope: set(values) for scope, values in scope_bases.items()}
    completed.setdefault("ROOT", set()).update(initial_completed)
    result: list[FormalRenderStep] = []
    for step in steps:
        state = completed.setdefault(step.stage_scope_occurrence, set())
        visible = state | set(step.moving_occurrences) | set(step.receiver_occurrences)
        state.update(step.state_delta)
        state_payload = {
            "scope": step.stage_scope_occurrence,
            "completed_occurrences": sorted(state, key=_path_key),
        }
        result.append(
            replace(
                step,
                visible_occurrences=tuple(sorted(visible, key=_path_key)),
                complete_state_hash=_canonical_hash(state_payload),
            )
        )
    return result


def _attach_dependencies(steps: list[FormalRenderStep]) -> list[FormalRenderStep]:
    producer = {
        occurrence: step.step_id
        for step in steps
        for occurrence in step.moving_occurrences
    }
    result: list[FormalRenderStep] = []
    by_scope: dict[str, list[str]] = {}
    for step in steps:
        by_scope.setdefault(step.stage_scope_occurrence, []).append(step.step_id)
    for step in steps:
        dependencies: set[str] = set()
        for receiver in step.receiver_occurrences:
            candidate = _nearest_producer(
                receiver,
                producer,
                stop_at=step.stage_scope_occurrence,
            )
            if candidate:
                dependencies.add(candidate)
        for moving in step.moving_occurrences:
            dependencies.update(by_scope.get(moving, ()))
        dependencies.discard(step.step_id)
        updated = replace(step, depends_on=tuple(sorted(dependencies)))
        result.append(updated)
    return result


def _topologically_order(steps: list[FormalRenderStep]) -> list[FormalRenderStep]:
    """Order the full dependency graph without treating BOM row order as truth."""

    by_id = {step.step_id: step for step in steps}
    original_index = {step.step_id: index for index, step in enumerate(steps)}
    remaining = {
        step.step_id: {item for item in step.depends_on if item in by_id}
        for step in steps
    }
    children: dict[str, set[str]] = {step_id: set() for step_id in by_id}
    for step_id, dependencies in remaining.items():
        for dependency in dependencies:
            children[dependency].add(step_id)
    ready = sorted(
        (step_id for step_id, dependencies in remaining.items() if not dependencies),
        key=lambda step_id: (original_index[step_id], step_id),
    )
    ordered: list[FormalRenderStep] = []
    emitted: set[str] = set()
    while ready:
        step_id = ready.pop(0)
        ordered.append(by_id[step_id])
        emitted.add(step_id)
        for child in sorted(children[step_id], key=lambda value: (original_index[value], value)):
            remaining[child].discard(step_id)
            if not remaining[child] and child not in emitted and child not in ready:
                ready.append(child)
        ready.sort(key=lambda value: (original_index[value], value))
    if len(ordered) != len(steps):
        cycle = sorted(step_id for step_id, dependencies in remaining.items() if dependencies)
        raise ValueError("正式规划依赖图存在循环：" + ", ".join(cycle[:20]))
    return ordered


def _attach_affected_descendants(steps: list[FormalRenderStep]) -> list[FormalRenderStep]:
    children: dict[str, set[str]] = {step.step_id: set() for step in steps}
    for step in steps:
        for dependency in step.depends_on:
            children.setdefault(dependency, set()).add(step.step_id)

    def descendants(step_id: str) -> tuple[str, ...]:
        pending = list(children.get(step_id, ()))
        found: set[str] = set()
        while pending:
            child = pending.pop()
            if child in found:
                continue
            found.add(child)
            pending.extend(children.get(child, ()))
        return tuple(sorted(found))

    return [replace(step, affected_descendants=descendants(step.step_id)) for step in steps]


def _subassembly_scope_diagnostics(
    steps: list[FormalRenderStep],
    nodes: dict[str, dict[str, Any]],
    bom_rows: dict[int, NormalizedBomRow],
) -> list[PlanningDiagnostic]:
    result: list[PlanningDiagnostic] = []
    for step in steps:
        assembly_roots = [
            occurrence
            for occurrence in step.moving_occurrences
            if _is_assembly(nodes.get(occurrence, {}))
        ]
        internal = [
            candidate.step_id
            for root in assembly_roots
            for candidate in steps
            if candidate.stage_scope_occurrence == root
        ]
        if not internal:
            continue
        row = bom_rows[step.source_bom_rows[0]]
        result.append(
            PlanningDiagnostic(
                "SUBASSEMBLY_SCOPE_UNCONFIRMED",
                f"{row.name or row.drawing_no} 同时存在整体安装和内部构造候选，生成前需确认是否在本工位展开。",
                step.source_bom_rows,
                tuple(assembly_roots),
                tuple(sorted(set(internal + [step.step_id]))),
            )
        )
    return result


def _mapped_assembly(mapped: BomOccurrenceMapping, nodes: dict[str, dict[str, Any]]) -> bool:
    return any(_is_assembly(nodes.get(occurrence, {})) for occurrence in mapped.occurrence_ids)


def _is_assembly(node: dict[str, Any]) -> bool:
    value = str(node.get("part_no") or node.get("model_name") or "").casefold()
    return ".asm" in value


def _parent_occurrence(occurrence_id: str, nodes: dict[str, dict[str, Any]]) -> str:
    node = nodes.get(occurrence_id)
    if node is not None:
        return str(node.get("parent_occurrence", "ROOT"))
    return occurrence_id.rpartition("/")[0] or "ROOT"


def _common_ancestor(occurrences: tuple[str, ...]) -> str:
    paths = [() if value == "ROOT" else tuple(value.split("/")) for value in occurrences]
    if not paths:
        return "ROOT"
    common: list[str] = []
    for values in zip(*paths):
        if len(set(values)) != 1:
            break
        common.append(values[0])
    return "/".join(common) if common else "ROOT"


def _nearest_producer(
    occurrence_id: str,
    producer: dict[str, str],
    *,
    stop_at: str = "ROOT",
) -> str | None:
    current = occurrence_id
    while current != "ROOT" and current != stop_at:
        if current in producer:
            return producer[current]
        current = current.rpartition("/")[0] or "ROOT"
    return None


def _display_distance(nodes: dict[str, dict[str, Any]]) -> float:
    origins = [
        vector
        for node in nodes.values()
        if (vector := _vector(node.get("transform", {}).get("origin"))) is not None
    ]
    if len(origins) < 2:
        return 80.0
    spans = [max(point[index] for point in origins) - min(point[index] for point in origins) for index in range(3)]
    diagonal = math.sqrt(sum(value * value for value in spans))
    return round(diagonal * 0.08, 6) if diagonal > 1.0e-6 else 80.0


def _constraint_rank(constraint_type: str) -> int:
    return {
        "INSERT": 0,
        "MATE": 1,
        "MATE_OFF": 1,
        "ALIGN": 2,
        "ALIGN_OFF": 2,
        "CSYS": 3,
        "ORIENT": 3,
        "TANGENT": 4,
    }.get(constraint_type, 9)


def _title(row: NormalizedBomRow, group_index: int, group_count: int) -> str:
    base = row.name or row.drawing_no or f"BOM 第 {row.row} 行"
    return base if group_count <= 1 else f"{base}（{group_index}/{group_count}）"


def _step_id(process_id: str, bom_row: int, group_index: int) -> str:
    return f"{process_id}-row-{bom_row:04d}-group-{group_index:02d}"


def _path_key(value: str) -> tuple[int, ...]:
    if value == "ROOT":
        return ()
    try:
        return tuple(int(part) for part in value.split("/"))
    except ValueError:
        return tuple(ord(character) for character in value)


def _vector(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    return result if all(math.isfinite(item) for item in result) else None


def _unit(value: Any) -> tuple[float, float, float] | None:
    vector = _vector(value)
    if vector is None:
        return None
    length = math.sqrt(_dot(vector, vector))
    if length < 1.0e-10:
        return None
    return tuple(item / length for item in vector)


def _dot(left: tuple[float, ...], right: tuple[float, ...] | None) -> float:
    if right is None:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()
