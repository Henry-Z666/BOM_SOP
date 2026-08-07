"""The stable, auditable contract between planning and Creo rendering."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .bom import BomItem
from .io import write_json

SCHEMA_VERSION = "step-contract/v2"


def _item(item: BomItem) -> dict[str, Any]:
    return {"bom_row": item.row, "level": item.level, "part_no": item.drawing_no,
            "name": item.name, "quantity": item.quantity, "unit": item.unit}


def make_contract(*, step_id: str, title: str, scope: str, assembly_file: str,
                  assembly_level: str, expected: list[BomItem], source: BomItem | None,
                  authoritative_manifest: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "step_id": step_id,
        "title": title,
        "scope": scope,
        "assembly": {"file": assembly_file, "bom_level": assembly_level,
                     "source_of_truth": "Creo native final ASM",
                     "authoritative_manifest": authoritative_manifest},
        "expected_bom_items": [_item(item) for item in expected],
        "moving_occurrences": [],
        "receiver_occurrences": [],
        "retained_occurrences": [],
        "visible_occurrences": [],
        "stage_visibility": {"policy": "forward_exact/v1", "completed_occurrences": [],
                             "required_context_occurrences": [], "rigid_completed_subassemblies": []},
        "translation": {"type": "translation_only", "vectors": [], "evidence": None},
        "camera": {"allowed": ["fixed_123", "fixed_456"], "selected": None},
        "method": {"text": source.assembly_text if source else "", "source": "BOM 组装步骤" if source and source.assembly_text else None},
        "control_points": source.control_points if source else "",
        "tools": source.tools if source else "",
        "automation": {"phase": "awaiting_cad_discovery", "confidence": 0.0,
                       "reasons": ["等待 Creo 原生装配图谱抽取。"], "planner": "constraint-graph/v1"},
        "render": None,
        "provenance": {"planner": "bom-hierarchy/v1", "source_bom_rows": [item.row for item in expected]},
    }


def save_contract(path: Path, contract: dict[str, Any]) -> None:
    write_json(path, contract)
