from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from .bom_normalizer import NormalizedBom, NormalizedBomRow, assembly_scope_frontier
from .model_inventory import ModelInventory, matching_model_files_for_row


@dataclass(frozen=True)
class DraftInstallationStep:
    step_id: str
    main_process_id: str
    title: str
    depends_on: tuple[str, ...]
    source_bom_rows: tuple[int, ...]
    candidate_model_files: tuple[str, ...]
    state_delta: tuple[str, ...]
    complete_state_hash: str
    provisional: bool = True


@dataclass(frozen=True)
class DraftPlan:
    schema_version: str
    final_assembly: str
    steps: tuple[DraftInstallationStep, ...]
    checkpoint_interval: int


def _model_files_for_row(row: NormalizedBomRow, inventory: ModelInventory) -> tuple[str, ...]:
    return tuple(sorted(
        model.relative_path for model in matching_model_files_for_row(row, inventory.files)
    ))


def _instructions(text: str) -> tuple[str, ...]:
    cleaned = re.sub(r"^\s*第\s*[0-9一二两三四五六七八九十百零]+\s*步\s*[：:]\s*", "", text).strip()
    numbered = [
        match.group(1).strip()
        for match in re.finditer(r"(?:^|\n)\s*\d+[.、]\s*([^\n]+)", cleaned)
        if match.group(1).strip()
    ]
    if numbered:
        return tuple(numbered)
    return (cleaned,) if cleaned else ()


def _level_parts(level: str) -> tuple[str, ...]:
    return tuple(part for part in str(level).split(".") if part)


def _root_row(bom: NormalizedBom) -> NormalizedBomRow:
    return min(bom.rows, key=lambda row: (len(_level_parts(row.level)), row.row))


def _is_descendant_or_self(row: NormalizedBomRow, ancestor: NormalizedBomRow) -> bool:
    child = _level_parts(row.level)
    parent = _level_parts(ancestor.level)
    return len(parent) <= len(child) and child[: len(parent)] == parent


def _row_is_assembly(row: NormalizedBomRow, inventory: ModelInventory) -> bool:
    matches = set(_model_files_for_row(row, inventory))
    return any(model.kind == "asm" and model.relative_path in matches for model in inventory.files)


def create_draft_plan(
    bom: NormalizedBom,
    inventory: ModelInventory,
    *,
    assembly_batch: bool = False,
) -> DraftPlan:
    bom = assembly_scope_frontier(bom)
    root = _root_row(bom)
    root_is_assembly = assembly_batch and _row_is_assembly(root, inventory)
    non_modeled = set(inventory.non_modeled_bom_rows)
    renderable_rows = tuple(
        row
        for row in bom.rows
        if row.row != root.row and not row.process_only and row.row not in non_modeled
    )
    boundary_rows = [
        row
        for row in bom.rows
        if row.row != root.row
        and row.main_process_number is not None
    ]
    process_rows = [row for row in boundary_rows if not row.process_only]
    rows_by_position = list(bom.rows)
    position_by_row = {row.row: index for index, row in enumerate(rows_by_position)}
    process_boundaries = sorted(position_by_row[row.row] for row in boundary_rows)
    scope_end_by_start = {
        start: process_boundaries[index + 1] if index + 1 < len(process_boundaries) else len(rows_by_position)
        for index, start in enumerate(process_boundaries)
    }
    process_rows.sort(key=lambda row: (row.main_process_number or 0, row.row))
    steps: list[DraftInstallationStep] = []
    previous_step_id: str | None = None
    previous_state_hash = "sha256:" + hashlib.sha256(b"draft-state/v1").hexdigest()

    def append_step(
        *,
        main_process_id: str,
        local_index: int,
        instruction: str,
        scope_rows: tuple[NormalizedBomRow, ...],
        include_state_delta: bool = True,
    ) -> None:
        nonlocal previous_step_id, previous_state_hash
        model_files = tuple(sorted({
            path
            for row in scope_rows
            for path in _model_files_for_row(row, inventory)
        }))
        step_id = f"{main_process_id}-step-{local_index:03d}"
        delta = model_files if include_state_delta else ()
        state_payload = json.dumps(
            {
                "previous": previous_state_hash,
                "step_id": step_id,
                "state_delta": delta,
                "source_bom_rows": [row.row for row in scope_rows],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        complete_state_hash = "sha256:" + hashlib.sha256(state_payload).hexdigest()
        steps.append(
            DraftInstallationStep(
                step_id=step_id,
                main_process_id=main_process_id,
                title=instruction,
                depends_on=(previous_step_id,) if previous_step_id else (),
                source_bom_rows=tuple(row.row for row in scope_rows),
                candidate_model_files=delta,
                state_delta=delta,
                complete_state_hash=complete_state_hash,
            )
        )
        previous_step_id = step_id
        previous_state_hash = complete_state_hash

    for process_row in process_rows:
        start = position_by_row[process_row.row]
        end = scope_end_by_start[start]
        scope_rows = tuple(
            row
            for row in rows_by_position[start:end]
            if row.row != root.row
            and not row.process_only
            and row.row not in non_modeled
        )
        instructions = _instructions(process_row.assembly_text) or (process_row.name,)
        main_process_id = f"process-{process_row.main_process_number:03d}"
        for local_index, instruction in enumerate(instructions, 1):
            append_step(
                main_process_id=main_process_id,
                local_index=local_index,
                instruction=instruction,
                scope_rows=scope_rows,
                include_state_delta=local_index == 1,
            )

    covered = {row for step in steps for row in step.source_bom_rows}
    uncovered = tuple(row for row in renderable_rows if row.row not in covered)
    if uncovered:
        assembly_rows = tuple(
            row
            for row in renderable_rows
            if _row_is_assembly(row, inventory)
        )
        grouped: dict[int | None, list[NormalizedBomRow]] = {}
        for row in uncovered:
            owners = [
                candidate
                for candidate in assembly_rows
                if candidate.row <= row.row and _is_descendant_or_self(row, candidate)
            ]
            owner = max(
                owners,
                key=lambda candidate: (len(_level_parts(candidate.level)), candidate.row),
                default=None,
            )
            grouped.setdefault(owner.row if owner else None, []).append(row)

        owner_by_row = {row.row: row for row in assembly_rows}
        group_order = sorted(
            grouped,
            key=lambda owner: min(row.row for row in grouped[owner]),
        )
        for owner_row in group_order:
            scope_rows = tuple(grouped[owner_row])
            if owner_row is None:
                if root_is_assembly:
                    process_id = f"process-asm-{root.row:04d}"
                    title = f"装配{root.name or root.drawing_no}"
                else:
                    process_id = "process-product-level"
                    title = "产品级装配（BOM层级补全）"
            else:
                owner = owner_by_row[owner_row]
                process_id = f"process-asm-{owner.row:04d}"
                title = f"装配{owner.name or owner.drawing_no}"
            append_step(
                main_process_id=process_id,
                local_index=1,
                instruction=title,
                scope_rows=scope_rows,
            )

    planned_rows = {row for step in steps for row in step.source_bom_rows}
    missing_rows = tuple(row.row for row in renderable_rows if row.row not in planned_rows)
    if missing_rows:
        raise ValueError(
            "草案规划未覆盖可渲染BOM行：" + ", ".join(map(str, missing_rows[:20]))
        )
    return DraftPlan(
        schema_version="draft-plan/v1",
        final_assembly=inventory.final_assembly,
        steps=tuple(steps),
        checkpoint_interval=20,
    )
