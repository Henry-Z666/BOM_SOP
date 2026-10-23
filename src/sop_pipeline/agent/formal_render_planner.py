from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
from graphlib import CycleError, TopologicalSorter
import json
import math
from typing import Any

from sop_pipeline.camera_planner import (
    calibrate_camera_basis,
    classify_receiver_face,
    generate_camera_candidates,
    select_fixed_camera_for_stage,
)
from sop_pipeline.explosion_planner import select_display_translation

from .bom_cad_mapper import BomCadMap, BomOccurrenceMapping
from .bom_normalizer import NormalizedBom, NormalizedBomRow, assembly_scope_frontier, color_boundary_contract
from .draft_planner import DraftPlan
from .physical_sequence import (
    PHYSICAL_SEQUENCE_POLICY,
    infer_physical_precedence,
    physical_role,
)


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
    receiver_occurrence_id: str | None = None
    receiver_item_id: int | None = None
    receiver_item_type: str | None = None


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
    occurrence_translation_vectors_root: dict[
        str, tuple[float, float, float]
    ] = field(default_factory=dict)


class PlanningDependencyCycle(ValueError):
    """A reviewable blocked draft, never a publishable render plan."""

    def __init__(self, steps: list[FormalRenderStep], blocked_ids: list[str]) -> None:
        self.steps = tuple(steps)
        self.blocked_ids = tuple(blocked_ids)
        self.scope_requests: tuple[PlanningDiagnostic, ...] = ()
        super().__init__("正式规划依赖图存在循环：" + ", ".join(blocked_ids[:20]))

    def diagnostic_payload(self) -> dict[str, Any]:
        return {"schema_version": "planning-blockers/v1", "status": "blocked",
            "production_eligible": False, "code": "DEPENDENCY_CYCLE",
            "blocked_step_ids": list(self.blocked_ids),
            "scope_requests": [asdict(item) for item in self.scope_requests],
            "steps": [asdict(step) for step in self.steps]}


@dataclass(frozen=True)
class FormalRenderPlan:
    schema_version: str
    assembly_file: str
    camera_basis: dict[str, Any]
    occurrence_bounds_root: dict[str, dict[str, list[float]]]
    initial_completed_occurrences: tuple[str, ...]
    scope_base_occurrences: dict[str, tuple[str, ...]]
    steps: tuple[FormalRenderStep, ...]
    diagnostics: tuple[PlanningDiagnostic, ...]
    ready_steps: int
    questioned_steps: int
    checkpoint_interval: int
    scope_decisions: dict[str, str]
    fingerprint: str
    color_boundaries: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FormalRenderBatch:
    batch_id: str
    root_bom_row: int
    source_bom_rows: tuple[int, ...]
    plan: FormalRenderPlan


@dataclass(frozen=True)
class FormalRenderForest:
    schema_version: str
    assembly_file: str
    batches: tuple[FormalRenderBatch, ...]
    fingerprint: str

    @property
    def steps(self) -> tuple[FormalRenderStep, ...]:
        return tuple(step for batch in self.batches for step in batch.plan.steps)

    @property
    def diagnostics(self) -> tuple[PlanningDiagnostic, ...]:
        return tuple(item for batch in self.batches for item in batch.plan.diagnostics)

    @property
    def ready_steps(self) -> int:
        return sum(batch.plan.ready_steps for batch in self.batches)

    @property
    def questioned_steps(self) -> int:
        return sum(batch.plan.questioned_steps for batch in self.batches)

    @property
    def checkpoint_interval(self) -> int:
        return max((batch.plan.checkpoint_interval for batch in self.batches), default=20)


def create_formal_render_forest(
    batches: tuple[FormalRenderBatch, ...],
) -> FormalRenderForest:
    payload = {
        "schema_version": "formal-render-forest/v1",
        "assembly_file": "assembly-forest",
        "batches": [asdict(batch) for batch in batches],
    }
    return FormalRenderForest(
        schema_version="formal-render-forest/v1",
        assembly_file="assembly-forest",
        batches=batches,
        fingerprint=_canonical_hash(payload),
    )


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
    receiver_item_id: int | None
    receiver_item_type: str | None


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

    color_boundaries = color_boundary_contract(bom)
    bom = assembly_scope_frontier(bom)
    active_rows = {r.row for r in bom.rows}
    mapping = replace(mapping, rows=tuple(r for r in mapping.rows if r.bom_row in active_rows),
        matched_rows=sum(r.status=='matched' and r.bom_row in active_rows for r in mapping.rows),
        ambiguous_rows=tuple(r for r in mapping.ambiguous_rows if r in active_rows),
        missing_rows=tuple(r for r in mapping.missing_rows if r in active_rows),
        quantity_mismatch_rows=tuple(r for r in mapping.quantity_mismatch_rows if r in active_rows))
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
    occurrence_owner = _occurrence_owner(mapping.rows)
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
    context_only_bom_rows: set[int] = set()
    unresolved_bom_rows: set[int] = set()
    raw_steps: list[tuple[tuple[int, int, int], FormalRenderStep]] = []
    group_evidence: dict[str, list[_OccurrenceEvidence]] = {}

    root_children = [
        row
        for row in mapping.rows
        if row.parent_bom_row == root_mapping.bom_row and row.status == "matched"
    ]
    root_foundation = min(root_children, key=lambda row: row.bom_row) if root_children else None
    foundation_containers: set[int] = set()
    if root_foundation is not None and not bom_rows[root_foundation.bom_row].assembly_boundary:
        prefix=bom_rows[root_foundation.bom_row].level.split('.')
        issued=[m for m in mapping.rows if m.status=='matched' and bom_rows[m.bom_row].assembly_boundary
                and bom_rows[m.bom_row].level.split('.')[:len(prefix)]==prefix]
        if issued:
            # The frame is a construction scope. Starting with its complete
            # ASM would expose future columns/beams before their operations.
            chosen=min(issued,key=lambda m:m.bom_row)
            foundation_containers={m.bom_row for m in mapping.rows if m.bom_row!=root_mapping.bom_row
                and any(o!=p and p.startswith(o+'/') for o in m.occurrence_ids for p in chosen.occurrence_ids)}
            root_foundation=chosen

    for mapped in sorted(mapping.rows, key=lambda row: row.bom_row):
        row = bom_rows[mapped.bom_row]
        if mapped.bom_row == root_mapping.bom_row or mapped.status == "non_renderable":
            continue
        if mapped.bom_row in foundation_containers:
            context_only_bom_rows.add(mapped.bom_row)
            continue
        if mapped.status != "matched":
            unresolved_bom_rows.add(mapped.bom_row)
            continue

        if root_foundation is not None and mapped.bom_row == root_foundation.bom_row:
            context_only_bom_rows.add(mapped.bom_row)
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
        # A FIX constraint proves final placement, not process chronology.
        # Only the first BOM root child is the assembly foundation.  Promoting
        # any later root-fixed component to initial context leaks future parts
        # into every earlier ROOT-scoped image and makes exact visibility
        # rebuilding incapable of hiding them.
        if fixed_scope == "ROOT":
            fixed_scope = None
        if fixed_scope is not None and fixed_scope != "ROOT":
            context_only_bom_rows.add(mapped.bom_row)
            for occurrence_id in mapped.occurrence_ids:
                scope_bases.setdefault(fixed_scope, set()).add(occurrence_id)
            continue

        moving_set = set(mapped.occurrence_ids)
        evidence = {
            occurrence_id: _select_evidence(
                occurrence_id,
                moving_set,
                nodes,
                constraints,
                display_distance=_display_distance(nodes),
            )
            for occurrence_id in mapped.occurrence_ids
        }
        proven = [item for item in evidence.values() if item is not None]
        missing = tuple(
            occurrence_id
            for occurrence_id, item in evidence.items()
            if item is None
        )
        grouped: dict[tuple[int | str, int, str], list[_OccurrenceEvidence]] = {}
        row_role = physical_role(row.name)
        for item in proven:
            face = classify_receiver_face(item.outward_normal, camera_basis)
            # Repeated identical BOM items belong in one image when their
            # proven installation directions use the same receiver face.  The
            # contract retains every distinct receiver occurrence, so this is
            # a presentation grouping rather than a loss of geometry evidence.
            receiver_layer = (
                _nearest_occurrence_owner(item.receiver_id, occurrence_owner)
                if row_role == "seal"
                else "shared-presentation-layer"
            )
            # Repeated parts from one BOM row are one installation action when
            # their insertion axes are parallel.  The group resolves one
            # common signed direction; a single noisy/opposite surface sign
            # must not split the step or reverse one fastener.  Seals keep
            # signed-face grouping because opposite interfaces are distinct
            # seal layers.
            key = (
                int(face["face_id"]) if row_role == "seal" else str(face["axis"]),
                int(face["sign"]) if row_role == "seal" else 0,
                receiver_layer,
            )
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
            rank = mapped.bom_row
            raw_steps.append(((rank, 1 if _mapped_assembly(mapped, nodes) else 0, mapped.bom_row), step))
            group_evidence[step.step_id] = members

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
            rank = mapped.bom_row
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

    planned_bom_rows = {
        bom_row
        for _, step in raw_steps
        for bom_row in step.source_bom_rows
    }
    matched_generation_rows = {
        item.bom_row
        for item in mapping.rows
        if item.bom_row != root_mapping.bom_row and item.status == "matched"
    }
    unaccounted = matched_generation_rows - planned_bom_rows - context_only_bom_rows
    if unaccounted:
        raise ValueError(
            "正式规划静默遗漏已映射BOM行："
            + ", ".join(str(value) for value in sorted(unaccounted)[:20])
        )
    if unresolved_bom_rows:
        diagnostics.append(
            PlanningDiagnostic(
                "BOM_PLANNING_COVERAGE_INCOMPLETE",
                (
                    f"共有 {len(unresolved_bom_rows)} 行可生成BOM物料没有唯一、足量的 occurrence 映射；"
                    "这些行已计入规划覆盖审计，但不会静默生成图片。"
                ),
                tuple(sorted(unresolved_bom_rows)),
            )
        )

    ordered = [item[1] for item in sorted(raw_steps, key=lambda item: (item[0], item[1].step_id))]
    # Contracting independent occurrence chains into BOM-row groups can make
    # an otherwise acyclic graph cyclic. Split only groups on a proven cycle,
    # rebuilding geometry from their original per-occurrence CAD evidence.
    # Recompute all edges; never remove a receiver or precedence constraint.
    mapping_by_row = {item.bom_row: item for item in mapping.rows}
    while True:
        ordered = _attach_dependencies(ordered)
        ordered, physical_diagnostics = _attach_physical_dependencies(ordered)
        try:
            TopologicalSorter({step.step_id: step.depends_on for step in ordered}).prepare()
            break
        except CycleError as error:
            cycle = set(error.args[1])
        split_ids = {
            step.step_id for step in ordered
            if step.step_id in cycle and len(group_evidence.get(step.step_id, ())) > 1
        }
        if not split_ids:
            # A real occurrence/assembly precedence conflict needs repair or
            # a process decision. Keep the existing fail-closed diagnostic.
            try:
                _topologically_order(ordered)
            except PlanningDependencyCycle as blocked:
                blocked.scope_requests = tuple(_subassembly_scope_diagnostics(ordered, nodes, bom_rows))
                raise
            raise AssertionError("cycle detector and ordering disagree")
        rebuilt: list[FormalRenderStep] = []
        for step in ordered:
            if step.step_id not in split_ids:
                rebuilt.append(step)
                continue
            members = group_evidence[step.step_id]
            replacements = []
            for index, member in enumerate(members, 1):
                replacement = _proven_step(
                    row=bom_rows[step.source_bom_rows[0]],
                    mapped=mapping_by_row[step.source_bom_rows[0]],
                    members=[member], group_index=1, group_count=1,
                    process_id=step.main_process_id, camera_basis=camera_basis,
                    nodes=nodes, display_distance=_display_distance(nodes),
                )
                replacements.append(replace(
                    replacement,
                    step_id=f"{step.step_id}-dependency-{index:03d}",
                    title=f"{step.title}（依赖分组 {index}/{len(members)}）",
                ))
            rebuilt.extend(replacements)
            diagnostics.append(PlanningDiagnostic(
                "GROUP_DEPENDENCY_SPLIT",
                "重复件合并引入依赖循环，按原始CAD实例证据拆分；保留全部零件和接收关系。",
                step.source_bom_rows, step.moving_occurrences,
                tuple(item.step_id for item in replacements),
            ))
        ordered = rebuilt
    diagnostics.extend(physical_diagnostics)
    ordered = _topologically_order(ordered)
    ordered = _complete_plan_state(ordered, scope_bases, initial_completed)
    ordered = _lock_final_stage_geometry(
        ordered,
        _occurrence_bounds_root(nodes),
        camera_basis,
    )
    ordered = _attach_affected_descendants(ordered)
    diagnostics.extend(_subassembly_scope_diagnostics(ordered, nodes, bom_rows))
    ready_steps = sum(step.status == "ready" for step in ordered)
    questioned_steps = sum(step.status == "questioned" for step in ordered)
    payload = {
        "schema_version": "formal-render-plan/v2",
        "assembly_file": mapping.assembly_file,
        "camera_basis": camera_basis,
        "occurrence_bounds_root": _occurrence_bounds_root(nodes),
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
        "color_boundaries": color_boundaries,
    }
    return FormalRenderPlan(
        schema_version="formal-render-plan/v2",
        assembly_file=mapping.assembly_file,
        camera_basis=camera_basis,
        occurrence_bounds_root=_occurrence_bounds_root(nodes),
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
        color_boundaries=color_boundaries,
        fingerprint=_canonical_hash(payload),
    )


def lock_formal_render_plan(
    plan: FormalRenderPlan | FormalRenderForest,
    answers: dict[str, str],
) -> FormalRenderPlan | FormalRenderForest:
    """Apply explicit bounded choices and recompute all derived plan state."""

    if isinstance(plan, FormalRenderForest):
        return create_formal_render_forest(
            tuple(
                replace(batch, plan=lock_formal_render_plan(batch.plan, answers))
                for batch in plan.batches
            )
        )
    if plan.schema_version not in {"formal-render-plan/v1", "formal-render-plan/v2"}:
        raise ValueError("不支持的正式规划版本")
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
        else:
            raise ValueError(f"无法识别子装配范围答案：{item_id}")
        if decision not in {"expand", "whole"}:
            raise ValueError(f"子装配范围超出允许值：{item_id}")
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
    retained, _ = _attach_physical_dependencies(retained)
    retained = _topologically_order(retained)
    retained = _complete_plan_state(
        retained,
        {scope: set(values) for scope, values in plan.scope_base_occurrences.items()},
        set(plan.initial_completed_occurrences),
    )
    retained = _lock_final_stage_geometry(
        retained,
        plan.occurrence_bounds_root,
        plan.camera_basis,
    )
    retained = _attach_affected_descendants(retained)
    ready_steps = sum(step.status == "ready" for step in retained)
    questioned_steps = sum(step.status == "questioned" for step in retained)
    payload = {
        "schema_version": plan.schema_version,
        "assembly_file": plan.assembly_file,
        "camera_basis": plan.camera_basis,
        "occurrence_bounds_root": plan.occurrence_bounds_root,
        "initial_completed_occurrences": plan.initial_completed_occurrences,
        "scope_base_occurrences": plan.scope_base_occurrences,
        "steps": [asdict(step) for step in retained],
        "diagnostics": [asdict(item) for item in plan.diagnostics],
        "ready_steps": ready_steps,
        "questioned_steps": questioned_steps,
        "checkpoint_interval": plan.checkpoint_interval,
        "scope_decisions": decisions,
        "color_boundaries": plan.color_boundaries,
    }
    return FormalRenderPlan(
        schema_version=plan.schema_version,
        assembly_file=plan.assembly_file,
        camera_basis=plan.camera_basis,
        occurrence_bounds_root=plan.occurrence_bounds_root,
        initial_completed_occurrences=plan.initial_completed_occurrences,
        scope_base_occurrences=plan.scope_base_occurrences,
        steps=tuple(retained),
        diagnostics=plan.diagnostics,
        ready_steps=ready_steps,
        questioned_steps=questioned_steps,
        checkpoint_interval=plan.checkpoint_interval,
        scope_decisions=decisions,
        color_boundaries=plan.color_boundaries,
        fingerprint=_canonical_hash(payload),
    )


def formal_render_plan_from_dict(
    payload: dict[str, Any],
) -> FormalRenderPlan | FormalRenderForest:
    if payload.get("schema_version") == "formal-render-forest/v1":
        batches = tuple(
            FormalRenderBatch(
                batch_id=str(item["batch_id"]),
                root_bom_row=int(item["root_bom_row"]),
                source_bom_rows=tuple(int(value) for value in item["source_bom_rows"]),
                plan=formal_render_plan_from_dict(dict(item["plan"])),
            )
            for item in payload["batches"]
        )
        if any(not isinstance(batch.plan, FormalRenderPlan) for batch in batches):
            raise ValueError("正式规划森林不能嵌套")
        return FormalRenderForest(
            schema_version="formal-render-forest/v1",
            assembly_file=str(payload["assembly_file"]),
            batches=batches,
            fingerprint=str(payload["fingerprint"]),
        )
    if payload.get("schema_version") not in {"formal-render-plan/v1", "formal-render-plan/v2"}:
        raise ValueError("不支持的正式规划版本")
    return FormalRenderPlan(
        schema_version=str(payload["schema_version"]),
        assembly_file=str(payload["assembly_file"]),
        camera_basis=dict(payload["camera_basis"]),
        occurrence_bounds_root={
            str(occurrence): {
                "min": [float(value) for value in bounds["min"]],
                "max": [float(value) for value in bounds["max"]],
            }
            for occurrence, bounds in payload.get("occurrence_bounds_root", {}).items()
        },
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
                            receiver_occurrence_id=(
                                str(anchor["receiver_occurrence_id"])
                                if anchor.get("receiver_occurrence_id") is not None
                                else None
                            ),
                            receiver_item_id=(
                                int(anchor["receiver_item_id"])
                                if anchor.get("receiver_item_id") is not None
                                else None
                            ),
                            receiver_item_type=(
                                str(anchor["receiver_item_type"])
                                if anchor.get("receiver_item_type") is not None
                                else None
                            ),
                        )
                        for anchor in item.get("arrow_anchors", ())
                    ),
                    "allowed_camera_ids": tuple(item["allowed_camera_ids"]),
                    "depends_on": tuple(item["depends_on"]),
                    "affected_descendants": tuple(item["affected_descendants"]),
                    "state_delta": tuple(item["state_delta"]),
                    "diagnostics": tuple(item.get("diagnostics", ())),
                    "occurrence_translation_vectors_root": {
                        str(occurrence): tuple(float(value) for value in vector)
                        for occurrence, vector in item.get(
                            "occurrence_translation_vectors_root",
                            {
                                occurrence: item.get("translation_vector_root")
                                for occurrence in item.get("moving_occurrences", ())
                                if item.get("translation_vector_root") is not None
                            },
                        ).items()
                    },
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
        color_boundaries=dict(payload.get("color_boundaries", {})),
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


def _fixed_to_parent(
    mapped: BomOccurrenceMapping,
    parent: BomOccurrenceMapping,
    constraints: tuple[dict[str, Any], ...],
) -> bool:
    parent_occurrences = set(parent.occurrence_ids)
    for occurrence in mapped.occurrence_ids:
        if not any(
            edge.get("type") in {"FIX", "DEFAULT_PLACEMENT"}
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
            if edge.get("type") in {"FIX", "DEFAULT_PLACEMENT"}
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
    *,
    display_distance: float,
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
        if not isinstance(ends, list) or len(ends) != 2:
            continue
        component_reference = edge.get("component_reference")
        referenced_component = (
            str(component_reference.get("occurrence_id") or "")
            if isinstance(component_reference, dict)
            else ""
        )
        direct_constraint = str(ends[0]) == occurrence_id
        targets_occurrence = referenced_component == occurrence_id
        targets_descendant = (
            direct_constraint
            and referenced_component.startswith(f"{occurrence_id}/")
        )
        if not (
            targets_occurrence
            or targets_descendant
            or (direct_constraint and not referenced_component)
        ):
            continue
        reference = edge.get("assembly_reference")
        receiver = (
            str(reference.get("occurrence_id") or ends[1])
            if isinstance(reference, dict)
            else str(ends[1])
        )
        if receiver in moving_set or edge.get("type") == "FIX":
            continue
        geometry = reference.get("geometry") if isinstance(reference, dict) else None
        if not isinstance(geometry, dict) or geometry.get("status") != "available":
            continue
        point = _vector(geometry.get("point_root"))
        normal = _unit(geometry.get("direction_root"))
        if point is None or normal is None:
            continue
        separation = tuple(origin[index] - point[index] for index in range(3))
        separation_unit = _unit(separation)
        bounds_sign = _bounds_half_space_sign(node, point, normal)
        if bounds_sign is not None:
            alignment = 1.0
            sign = bounds_sign
        else:
            alignment = abs(_dot(normal, separation_unit)) if separation_unit else 0.0
            sign = 1.0 if _dot(normal, separation) >= 0.0 else -1.0
        outward = tuple(sign * value for value in normal)
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
        if moving_anchor is not None and not _point_within_occurrence_bounds(
            moving_anchor,
            node,
        ):
            moving_anchor = None
        physical_anchor = _vector(node.get("physical_anchor_root"))
        if (
            moving_anchor is None
            and physical_anchor is not None
            and _point_within_occurrence_bounds(physical_anchor, node)
        ):
            moving_anchor = physical_anchor
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
                int(reference["item_id"])
                if isinstance(reference.get("item_id"), int)
                else None,
                str(reference.get("item_type"))
                if reference.get("item_type") is not None
                else None,
            )
        )
    if not candidates:
        return None
    oriented_candidates: list[_OccurrenceEvidence] = []
    for candidate in candidates:
        oriented_normal, clearance_proven = _clearance_oriented_normal(
            candidate,
            candidates,
            nodes,
            display_distance,
        )
        oriented_candidates.append(
            replace(
                candidate,
                outward_normal=oriented_normal,
                alignment=1.0 if clearance_proven else candidate.alignment,
            )
        )
    candidates = oriented_candidates
    return min(
        candidates,
        key=lambda item: (
            item.moving_anchor_point is None,
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
    # Bound local presentation by actual moving-solid dimensions. A small
    # fastener must not inherit the diagonal of the entire product.
    spans = [max(float(bounds["max"][axis]) - float(bounds["min"][axis])
                 for axis in range(3))
             for member in members
             if isinstance((bounds := nodes.get(member.occurrence_id, {}).get("bounds_root")), dict)
             and bounds.get("status") == "available"]
    local_span = max(spans, default=0.0)
    if local_span > 1e-9:
        display_distance = min(display_distance, 1.5 * local_span)
    normal = _unit(
        tuple(
            sum(item.outward_normal[index] for item in members)
            for index in range(3)
        )
    )
    if normal is None:
        # An exact tie is possible because Creo surface directions are
        # unsigned.  Occurrence ordering is stable, so the first measured axis
        # supplies a deterministic common sign for the whole BOM-row action.
        normal = members[0].outward_normal
    point = tuple(
        sum(item.receiver_point[index] for item in members) / len(members)
        for index in range(3)
    )
    translation = tuple(round(value * display_distance, 6) for value in normal)
    occurrence_translations = {
        item.occurrence_id: translation
        for item in members
    }
    face = classify_receiver_face(normal, camera_basis)
    camera = generate_camera_candidates(camera_basis, face, translation)[0]
    moving = tuple(sorted((item.occurrence_id for item in members), key=_path_key))
    receivers = tuple(sorted({item.receiver_id for item in members}, key=_path_key))
    scope = _common_ancestor(moving + receivers)
    low_alignment = min(item.alignment for item in members) < 0.10
    low_face_confidence = face["confidence"] != "high"
    missing_arrow_anchor = any(item.moving_anchor_point is None for item in members)
    missing_receiver_surface = any(
        item.receiver_item_id is None or item.receiver_item_type != "SURFACE"
        for item in members
    )
    camera_gate = camera.get("hard_gate", {})
    step_diagnostics = tuple(
        code
        for code, active in (
            ("DIRECTION_SIGN_WEAK", low_alignment),
            ("RECEIVER_NORMAL_NOT_AXIS_ALIGNED", low_face_confidence),
            ("MOVING_ARROW_ANCHOR_UNAVAILABLE", missing_arrow_anchor),
            ("RECEIVER_SURFACE_SELECTION_UNAVAILABLE", missing_receiver_surface),
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
                receiver_occurrence_id=item.receiver_id,
                receiver_item_id=item.receiver_item_id,
                receiver_item_type=item.receiver_item_type,
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
        occurrence_translation_vectors_root=occurrence_translations,
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


def _attach_physical_dependencies(
    steps: list[FormalRenderStep],
) -> tuple[list[FormalRenderStep], list[PlanningDiagnostic]]:
    inferred = infer_physical_precedence(steps)
    additions: dict[str, set[str]] = {}
    diagnostics: list[PlanningDiagnostic] = []
    by_id = {step.step_id: step for step in steps}
    for edge in inferred:
        additions.setdefault(edge.after_step_id, set()).add(edge.before_step_id)
        before = by_id[edge.before_step_id]
        after = by_id[edge.after_step_id]
        diagnostics.append(
            PlanningDiagnostic(
                "PHYSICAL_SEQUENCE_AUTO_RESOLVED",
                f"按 {PHYSICAL_SEQUENCE_POLICY} 自动确定：{before.title} 先于 {after.title}（{edge.rule}）。",
                tuple(sorted(set(before.source_bom_rows + after.source_bom_rows))),
                edge.shared_receivers,
                (before.step_id, after.step_id),
            )
        )
    return (
        [
            replace(
                step,
                depends_on=tuple(
                    sorted(set(step.depends_on) | additions.get(step.step_id, set()))
                ),
            )
            for step in steps
        ],
        diagnostics,
    )


def _occurrence_owner(
    rows: tuple[BomOccurrenceMapping, ...],
) -> dict[str, str]:
    return {
        occurrence: f"bom-row-{row.bom_row:04d}"
        for row in rows
        if row.status == "matched"
        for occurrence in row.occurrence_ids
    }


def _nearest_occurrence_owner(
    occurrence_id: str, owners: dict[str, str]
) -> str:
    current = occurrence_id
    while current and current != "ROOT":
        if current in owners:
            return owners[current]
        current = current.rpartition("/")[0]
    return owners.get("ROOT", f"receiver:{occurrence_id}")


def _topologically_order(steps: list[FormalRenderStep]) -> list[FormalRenderStep]:
    """Order the full dependency graph without treating BOM row order as truth."""

    by_id = {step.step_id: step for step in steps}
    original_index = {step.step_id: index for index, step in enumerate(steps)}
    row_cluster_index: dict[tuple[int, ...], int] = {}
    for index, step in enumerate(steps):
        row_cluster_index.setdefault(step.source_bom_rows, index)

    def priority(step_id: str) -> tuple[int, str, int]:
        step = by_id[step_id]
        return (
            row_cluster_index[step.source_bom_rows],
            step.step_id,
            original_index[step_id],
        )

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
        key=priority,
    )
    ordered: list[FormalRenderStep] = []
    emitted: set[str] = set()
    while ready:
        step_id = ready.pop(0)
        ordered.append(by_id[step_id])
        emitted.add(step_id)
        for child in sorted(children[step_id], key=priority):
            remaining[child].discard(step_id)
            if not remaining[child] and child not in emitted and child not in ready:
                ready.append(child)
        ready.sort(key=priority)
    if len(ordered) != len(steps):
        cycle = sorted(step_id for step_id, dependencies in remaining.items() if dependencies)
        raise PlanningDependencyCycle(steps, cycle)
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


def _occurrence_bounds_root(
    nodes: dict[str, dict[str, Any]],
) -> dict[str, dict[str, list[float]]]:
    """Retain only finite Creo-derived root bounds in the locked plan."""

    result: dict[str, dict[str, list[float]]] = {}
    for occurrence_id, node in nodes.items():
        bounds = node.get("bounds_root")
        if not isinstance(bounds, dict) or bounds.get("status") != "available":
            continue
        low = _vector(bounds.get("min"))
        high = _vector(bounds.get("max"))
        if low is None or high is None or any(
            low[index] > high[index] for index in range(3)
        ):
            continue
        result[occurrence_id] = {
            "min": [float(value) for value in low],
            "max": [float(value) for value in high],
        }
    return result


def _node_bounds(
    node: dict[str, Any],
) -> dict[str, list[float]] | None:
    bounds = node.get("bounds_root")
    if not isinstance(bounds, dict) or bounds.get("status") != "available":
        return None
    low = _vector(bounds.get("min"))
    high = _vector(bounds.get("max"))
    if low is None or high is None or any(
        low[index] > high[index] for index in range(3)
    ):
        return None
    return {"min": list(low), "max": list(high)}


def _bounds_clearance_after_translation(
    moving_node: dict[str, Any],
    receiver_node: dict[str, Any],
    translation: tuple[float, float, float],
) -> float:
    moving = _node_bounds(moving_node)
    receiver = _node_bounds(receiver_node)
    if moving is None or receiver is None:
        return 0.0
    gaps = [
        max(
            0.0,
            receiver["min"][index]
            - (moving["max"][index] + translation[index]),
            moving["min"][index]
            + translation[index]
            - receiver["max"][index],
        )
        for index in range(3)
    ]
    return math.sqrt(sum(value * value for value in gaps))


def _lock_stage_cameras(
    steps: list[FormalRenderStep],
    bounds_by_occurrence: dict[str, dict[str, list[float]]],
    camera_basis: dict[str, Any],
) -> list[FormalRenderStep]:
    """Lock the less-occluded fixed view after staged visibility is known."""

    result: list[FormalRenderStep] = []
    for step in steps:
        if (
            step.status not in {"ready", "questioned"}
            or step.receiver_normal_root is None
            or step.translation_vector_root is None
        ):
            result.append(step)
            continue
        moving_bounds = [
            bounds_by_occurrence[occurrence]
            for occurrence in step.moving_occurrences
            if occurrence in bounds_by_occurrence
        ]
        moving = set(step.moving_occurrences)
        context_bounds = [
            bounds_by_occurrence[occurrence]
            for occurrence in step.visible_occurrences
            if occurrence not in moving
            and occurrence in bounds_by_occurrence
        ]
        camera = select_fixed_camera_for_stage(
            camera_basis,
            step.receiver_normal_root,
            step.translation_vector_root,
            moving_bounds,
            context_bounds,
        )
        result.append(replace(step, camera_id=str(camera["id"])))
    return result


def _lock_final_stage_geometry(
    steps: list[FormalRenderStep],
    bounds_by_occurrence: dict[str, dict[str, list[float]]],
    camera_basis: dict[str, Any],
) -> list[FormalRenderStep]:
    """Lock display translation first, then the single fixed camera."""

    exploded = _lock_stage_explosion_modes(steps, bounds_by_occurrence)
    return _lock_stage_cameras(exploded, bounds_by_occurrence, camera_basis)


def _lock_stage_explosion_modes(
    steps: list[FormalRenderStep],
    bounds_by_occurrence: dict[str, dict[str, list[float]]],
) -> list[FormalRenderStep]:
    """Replace an unreadable normal explosion with a bounded lateral one."""

    result: list[FormalRenderStep] = []
    for step in steps:
        if (
            step.receiver_normal_root is None
            or step.translation_vector_root is None
        ):
            result.append(step)
            continue
        moving_bounds = [
            bounds_by_occurrence[occurrence]
            for occurrence in step.moving_occurrences
            if occurrence in bounds_by_occurrence
        ]
        moving = set(step.moving_occurrences)
        context_bounds = [
            bounds_by_occurrence[occurrence]
            for occurrence in step.visible_occurrences
            if occurrence not in moving and occurrence in bounds_by_occurrence
        ]
        distance = math.sqrt(
            sum(value * value for value in step.translation_vector_root)
        )
        translations = step.occurrence_translation_vectors_root or {
            occurrence: step.translation_vector_root
            for occurrence in step.moving_occurrences
        }
        directions = {
            tuple(round(value / magnitude, 9) for value in translation)
            for translation in translations.values()
            if (
                magnitude := math.sqrt(
                    sum(value * value for value in translation)
                )
            )
            > 1e-9
        }

        # A repeated fastener group may legitimately need opposite signed
        # clearance vectors at different interfaces.  It still represents one
        # BOM installation action, so retain those per-occurrence translations
        # instead of replacing the whole group with one presentation vector.
        if len(directions) > 1:
            result.append(step)
            continue
        selection = select_display_translation(
            step.receiver_normal_root,
            distance,
            moving_bounds,
            context_bounds,
            [anchor.complete_point_root for anchor in step.arrow_anchors],
        )
        selected_translation = tuple(
            float(value) for value in selection["translation_vector_root"]
        )
        selected_distance = math.sqrt(
            sum(value * value for value in selected_translation)
        )
        occurrence_translations = {
            occurrence: tuple(
                value
                * (
                    math.sqrt(sum(component * component for component in original))
                    / selected_distance
                    if selected_distance > 1e-9
                    else 1.0
                )
                for value in selected_translation
            )
            for occurrence, original in translations.items()
        }
        result.append(
            replace(
                step,
                translation_vector_root=selected_translation,
                occurrence_translation_vectors_root=occurrence_translations,
            )
        )
    return result


def _point_within_occurrence_bounds(
    point: tuple[float, float, float],
    node: dict[str, Any],
) -> bool:
    """Reject constraint anchors that are not on the occurrence's physical solid.

    Creo occasionally exposes an analytic surface coordinate-system origin as
    though it were a sampled point on the selected face.  That origin can be
    far outside the component.  When native solid bounds are available, keep
    only anchors inside them (with a small numerical tolerance).
    """

    bounds = node.get("bounds_root")
    if not isinstance(bounds, dict) or bounds.get("status") != "available":
        return True
    low = _vector(bounds.get("min"))
    high = _vector(bounds.get("max"))
    if low is None or high is None:
        return False
    diagonal = math.sqrt(
        sum((high[index] - low[index]) ** 2 for index in range(3))
    )
    tolerance = max(1.0e-5, diagonal * 1.0e-6)
    return all(
        low[index] - tolerance <= point[index] <= high[index] + tolerance
        for index in range(3)
    )


def _clearance_oriented_normal(
    candidate: _OccurrenceEvidence,
    candidates: list[_OccurrenceEvidence],
    nodes: dict[str, dict[str, Any]],
    display_distance: float,
) -> tuple[tuple[float, float, float], bool]:
    """Resolve an unsigned Creo axis by maximizing worst receiver clearance.

    INSERT/MATE/ALIGN surface directions are not reliable outward normals.  For
    every same-axis constraint on the moving occurrence, compare both signs
    against the actual moving/receiver solid bounds.  Maximin prevents a
    secondary constraint from pulling the part toward another constrained
    receiver while remaining deterministic for multi-constraint parts.
    """

    moving = nodes.get(candidate.occurrence_id, {})
    if _node_bounds(moving) is None:
        return candidate.outward_normal, False
    axis = candidate.outward_normal
    aligned_receivers = {
        item.receiver_id
        for item in candidates
        if abs(_dot(axis, item.outward_normal)) >= 0.95
        and _node_bounds(nodes.get(item.receiver_id, {})) is not None
    }
    if not aligned_receivers:
        return axis, False

    def worst_clearance(sign: float) -> float:
        vector = tuple(sign * display_distance * value for value in axis)
        values = [
            _bounds_clearance_after_translation(
                moving,
                nodes[receiver_id],
                vector,
            )
            for receiver_id in aligned_receivers
        ]
        return min(values)

    positive = worst_clearance(1.0)
    negative = worst_clearance(-1.0)
    tolerance = max(1.0e-5, display_distance * 1.0e-6)
    if positive > negative + tolerance:
        return axis, True
    if negative > positive + tolerance:
        return tuple(-value for value in axis), True
    return candidate.outward_normal, False


def _bounds_half_space_sign(
    node: dict[str, Any],
    receiver_point: tuple[float, float, float],
    normal: tuple[float, float, float],
) -> float | None:
    """Return the side of a receiver plane containing the whole moving solid."""

    bounds = node.get("bounds_root")
    if not isinstance(bounds, dict) or bounds.get("status") != "available":
        return None
    low = _vector(bounds.get("min"))
    high = _vector(bounds.get("max"))
    if low is None or high is None:
        return None
    minimum = 0.0
    maximum = 0.0
    for index, component in enumerate(normal):
        near = low[index] if component >= 0.0 else high[index]
        far = high[index] if component >= 0.0 else low[index]
        minimum += (near - receiver_point[index]) * component
        maximum += (far - receiver_point[index]) * component
    diagonal = math.sqrt(
        sum((high[index] - low[index]) ** 2 for index in range(3))
    )
    tolerance = max(1.0e-5, diagonal * 1.0e-6)
    if minimum >= -tolerance and maximum > tolerance:
        return 1.0
    if maximum <= tolerance and minimum < -tolerance:
        return -1.0
    return None


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
