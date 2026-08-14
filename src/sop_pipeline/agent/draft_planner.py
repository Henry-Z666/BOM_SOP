from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from .bom_normalizer import NormalizedBom, NormalizedBomRow
from .model_inventory import ModelInventory, normalize_identifier


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
    identifiers = {
        normalize_identifier(value)
        for value in (row.drawing_no, row.model, row.material_code)
        if value.strip()
    }
    return tuple(sorted(
        model.relative_path
        for model in inventory.files
        if normalize_identifier(model.base_name) in identifiers
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


def create_draft_plan(bom: NormalizedBom, inventory: ModelInventory) -> DraftPlan:
    process_rows = [row for row in bom.rows if row.main_process_number is not None]
    rows_by_position = list(bom.rows)
    position_by_row = {row.row: index for index, row in enumerate(rows_by_position)}
    process_boundaries = sorted(position_by_row[row.row] for row in process_rows)
    scope_end_by_start = {
        start: process_boundaries[index + 1] if index + 1 < len(process_boundaries) else len(rows_by_position)
        for index, start in enumerate(process_boundaries)
    }
    process_rows.sort(key=lambda row: (row.main_process_number or 0, row.row))
    steps: list[DraftInstallationStep] = []
    previous_step_id: str | None = None
    previous_state_hash = "sha256:" + hashlib.sha256(b"draft-state/v1").hexdigest()

    for process_row in process_rows:
        if process_row.process_only:
            continue
        start = position_by_row[process_row.row]
        end = scope_end_by_start[start]
        scope_rows = tuple(rows_by_position[start:end])
        model_files = tuple(sorted({
            path
            for row in scope_rows
            for path in _model_files_for_row(row, inventory)
        }))
        instructions = _instructions(process_row.assembly_text) or (process_row.name,)
        main_process_id = f"process-{process_row.main_process_number:03d}"
        for local_index, instruction in enumerate(instructions, 1):
            step_id = f"{main_process_id}-step-{local_index:03d}"
            delta = model_files if local_index == 1 else ()
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
    return DraftPlan(
        schema_version="draft-plan/v1",
        final_assembly=inventory.final_assembly,
        steps=tuple(steps),
        checkpoint_interval=20,
    )
