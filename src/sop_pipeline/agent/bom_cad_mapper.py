from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .bom_normalizer import NormalizedBom, NormalizedBomRow
from .model_inventory import ModelInventory, normalize_identifier
from .draft_planner import DraftPlan


@dataclass(frozen=True)
class BomOccurrenceMapping:
    bom_row: int
    level: str
    drawing_no: str
    name: str
    expected_quantity: int | None
    parent_bom_row: int | None
    occurrence_ids: tuple[str, ...]
    status: str
    evidence: str


@dataclass(frozen=True)
class BomCadMap:
    schema_version: str
    assembly_file: str
    rows: tuple[BomOccurrenceMapping, ...]
    matched_rows: int
    ambiguous_rows: tuple[int, ...]
    missing_rows: tuple[int, ...]
    quantity_mismatch_rows: tuple[int, ...]


def map_bom_to_occurrences(
    bom: NormalizedBom,
    inventory: ModelInventory,
    graph: dict[str, Any],
    draft_plan: DraftPlan | None = None,
) -> BomCadMap:
    if graph.get("schema_version") != "creo-cad-graph/v3":
        raise ValueError("BOM/CAD 映射需要 creo-cad-graph/v3")
    occurrences = graph.get("occurrences")
    if not isinstance(occurrences, list) or not occurrences:
        raise ValueError("CAD 图谱没有 occurrence")
    root_row = min(bom.rows, key=lambda row: (_level_depth(row.level), row.row))
    parent_rows = _parent_rows(bom.rows)
    by_row: dict[int, BomOccurrenceMapping] = {}
    non_modeled = set(inventory.non_modeled_bom_rows)

    for row in sorted(bom.rows, key=lambda item: (_level_depth(item.level), item.row)):
        parent = parent_rows.get(row.row)
        expected = _expected_quantity(row.quantity)
        if row.row == root_row.row:
            mapping = BomOccurrenceMapping(
                bom_row=row.row,
                level=row.level,
                drawing_no=row.drawing_no,
                name=row.name,
                expected_quantity=expected,
                parent_bom_row=None,
                occurrence_ids=("ROOT",),
                status="matched",
                evidence="BOM 根物料锁定为最终总装 ROOT",
            )
            by_row[row.row] = mapping
            continue
        if row.process_only or row.row in non_modeled:
            by_row[row.row] = BomOccurrenceMapping(
                bom_row=row.row,
                level=row.level,
                drawing_no=row.drawing_no,
                name=row.name,
                expected_quantity=expected,
                parent_bom_row=parent.row if parent else None,
                occurrence_ids=(),
                status="non_renderable",
                evidence="BOM/CAD 预检已确认该行不要求独立实体 occurrence",
            )
            continue

        identifiers = {
            normalize_identifier(value)
            for value in (row.drawing_no, row.model, row.material_code)
            if value.strip()
        }
        candidates = [
            node for node in occurrences if identifiers.intersection(_node_identifiers(node))
        ]
        evidence = "图号/型号/物料编码与 Creo 模型名精确匹配"
        if parent is not None:
            parent_mapping = by_row.get(parent.row)
            parent_occurrences = (
                set(parent_mapping.occurrence_ids) if parent_mapping is not None else set()
            )
            if parent_occurrences:
                direct = [
                    node
                    for node in candidates
                    if str(node.get("parent_occurrence", "")) in parent_occurrences
                ]
                descendants = [
                    node
                    for node in candidates
                    if any(_is_descendant(_node_id(node), root) for root in parent_occurrences)
                ]
                if direct:
                    candidates = direct
                    evidence += "；由 BOM 父项限定为直接子 occurrence"
                elif descendants:
                    candidates = descendants
                    evidence += "；由 BOM 父项限定为后代 occurrence，需复核中间层级"

        occurrence_ids = tuple(sorted({_node_id(node) for node in candidates}, key=_path_key))
        if not occurrence_ids:
            status = "missing"
        elif expected is None:
            status = "matched" if len(occurrence_ids) == 1 else "ambiguous"
        elif len(occurrence_ids) == expected:
            status = "matched"
        elif len(occurrence_ids) > expected:
            status = "ambiguous"
        else:
            status = "quantity_mismatch"
        by_row[row.row] = BomOccurrenceMapping(
            bom_row=row.row,
            level=row.level,
            drawing_no=row.drawing_no,
            name=row.name,
            expected_quantity=expected,
            parent_bom_row=parent.row if parent else None,
            occurrence_ids=occurrence_ids,
            status=status,
            evidence=evidence,
        )

    rows = tuple(by_row[row.row] for row in bom.rows)
    if draft_plan is not None:
        rows = _refine_by_process_constraints(rows, graph, draft_plan)
    return BomCadMap(
        schema_version="bom-cad-map/v1",
        assembly_file=str(graph["assembly_file"]),
        rows=rows,
        matched_rows=sum(row.status == "matched" for row in rows),
        ambiguous_rows=tuple(row.bom_row for row in rows if row.status == "ambiguous"),
        missing_rows=tuple(row.bom_row for row in rows if row.status == "missing"),
        quantity_mismatch_rows=tuple(
            row.bom_row for row in rows if row.status == "quantity_mismatch"
        ),
    )


def _node_identifiers(node: dict[str, Any]) -> set[str]:
    values = {
        normalize_identifier(str(node.get("model_name", ""))),
        normalize_identifier(_remove_creo_suffix(str(node.get("part_no", "")))),
    }
    return {value for value in values if value}


def _remove_creo_suffix(value: str) -> str:
    lowered = value.casefold()
    for marker in (".asm", ".prt"):
        position = lowered.rfind(marker)
        if position >= 0:
            return value[:position]
    return value


def _node_id(node: dict[str, Any]) -> str:
    value = str(node.get("occurrence_id", ""))
    if not value:
        raise ValueError("CAD occurrence 缺少 occurrence_id")
    return value


def _path_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("/"))


def _is_descendant(value: str, parent: str) -> bool:
    return parent == "ROOT" or value.startswith(parent + "/")


def _level_depth(level: str) -> int:
    return len([part for part in level.split(".") if part]) if level else 10_000


def _parent_rows(rows: tuple[NormalizedBomRow, ...]) -> dict[int, NormalizedBomRow]:
    result: dict[int, NormalizedBomRow] = {}
    prior: list[NormalizedBomRow] = []
    for row in rows:
        candidates = [
            candidate
            for candidate in prior
            if _is_parent_level(candidate.level, row.level)
        ]
        if candidates:
            result[row.row] = max(
                candidates, key=lambda candidate: (_level_depth(candidate.level), candidate.row)
            )
        prior.append(row)
    return result


def _is_parent_level(parent: str, child: str) -> bool:
    parent_parts = [part for part in parent.split(".") if part]
    child_parts = [part for part in child.split(".") if part]
    return len(parent_parts) < len(child_parts) and child_parts[: len(parent_parts)] == parent_parts


def _expected_quantity(value: float | int | str) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer() and value > 0:
        return int(value)
    return None


def _refine_by_process_constraints(
    rows: tuple[BomOccurrenceMapping, ...],
    graph: dict[str, Any],
    draft_plan: DraftPlan,
) -> tuple[BomOccurrenceMapping, ...]:
    scopes: dict[int, set[int]] = {}
    for step in draft_plan.steps:
        scope = {int(value) for value in step.source_bom_rows}
        for row_number in scope:
            scopes.setdefault(row_number, set()).update(scope)
    by_row = {row.bom_row: row for row in rows}
    constraints = graph.get("constraints", [])
    result: list[BomOccurrenceMapping] = []
    for row in rows:
        if row.status != "ambiguous" or row.expected_quantity is None:
            result.append(row)
            continue
        scope_rows = scopes.get(row.bom_row, set())
        context: set[str] = set()
        for row_number in scope_rows:
            mapped = by_row.get(row_number)
            if (
                row_number == row.bom_row
                or mapped is None
                or mapped.status != "matched"
            ):
                continue
            context.update(
                occurrence
                for occurrence in mapped.occurrence_ids
                if occurrence != "ROOT"
            )
        scores = {
            candidate: sum(
                1
                for edge in constraints
                if candidate in edge.get("occurrences", [])
                and context.intersection(str(value) for value in edge.get("occurrences", []))
            )
            for candidate in row.occurrence_ids
        }
        ranked = sorted(
            ((score, candidate) for candidate, score in scores.items() if score > 0),
            key=lambda item: (-item[0], _path_key(item[1])),
        )
        expected = row.expected_quantity
        selected: tuple[str, ...] = ()
        if len(ranked) == expected:
            selected = tuple(sorted((item[1] for item in ranked), key=_path_key))
        elif len(ranked) > expected and ranked[expected - 1][0] > ranked[expected][0]:
            selected = tuple(
                sorted((item[1] for item in ranked[:expected]), key=_path_key)
            )
        if selected:
            result.append(
                replace(
                    row,
                    occurrence_ids=selected,
                    status="matched",
                    evidence=row.evidence + "；由同工序唯一 occurrence 的 Creo 原生约束自动消歧",
                )
            )
        else:
            result.append(row)
    return tuple(result)
