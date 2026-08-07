"""Constraint-first automatic planner; no manual occurrence selection exists."""
from __future__ import annotations

from typing import Any

from .cad_graph import CadGraph


def _vector(edge: dict[str, Any]) -> tuple[list[float] | None, str | None]:
    direction = edge.get("assembly_axis") or edge.get("contact_normal")
    if not direction or len(direction) != 3: return None, None
    distance = float(edge.get("display_distance", 80.0))
    return [-float(value) * distance for value in direction], edge.get("id", "unnamed-constraint")


def plan(contract: dict[str, Any], graph: CadGraph) -> dict[str, Any]:
    auto = contract["automation"]
    if graph.assembly_file.lower() != contract["assembly"]["file"].lower():
        auto.update({"phase": "awaiting_cad_discovery", "confidence": 0.0,
                     "reasons": ["CAD 图谱的权威 ASM 与步骤合同不一致。"]})
        return contract
    moving: list[str] = []
    mismatches: list[str] = []
    for item in contract["expected_bom_items"]:
        matches = graph.match(item["part_no"])
        expected = item["quantity"]
        if not isinstance(expected, (int, float)) or len(matches) < expected:
            mismatches.append(f"{item['part_no']}：BOM {expected}，CAD {len(matches)}")
        moving.extend((node.get("occurrence_id") or node["id"]) for node in matches[:int(expected)] if node.get("id"))
    if mismatches or not moving:
        auto.update({"phase": "awaiting_cad_discovery", "confidence": 0.0,
                     "reasons": ["BOM 与 CAD occurrence 未完整匹配：" + "；".join(mismatches) if mismatches else "未找到活动 occurrence。"]})
        return contract
    moving_set = set(moving)
    edges = graph.partners(moving_set)
    receivers = sorted({node for edge in edges for node in edge.get("occurrences", []) if node not in moving_set})
    if not receivers: receivers = [graph.root_occurrence]
    vector, evidence = next((result for result in (_vector(edge) for edge in edges) if result[0]), (None, None))
    if not vector:
        auto.update({"phase": "awaiting_cad_discovery", "confidence": 0.35,
                     "reasons": ["已定位活动件，但原生约束未提供装配轴或接触法向。"]})
        return contract
    camera = ({"allowed": ["fixed_123", "fixed_456"], "selected": "fixed_123"}
              if contract.get("schema_version") == "step-contract/v2"
              else {"candidates": ["front", "back", "bottom", "left", "right", "oblique"], "selected": "oblique"})
    contract.update({"moving_occurrences": moving, "receiver_occurrences": receivers,
                     "retained_occurrences": [graph.root_occurrence],
                     "translation": {"type": "translation_only", "vectors": [{"occurrences": moving, "vector": vector}],
                                     "evidence": f"Creo constraint {evidence}: assembly axis/contact normal"},
                     "camera": camera})
    confidence = 0.70 + (0.15 if receivers != [graph.root_occurrence] else 0) + (0.10 if len(edges) >= 2 else 0)
    auto.update({"phase": "planned", "confidence": min(confidence, 0.95),
                 "reasons": ["活动件由 BOM→CAD occurrence 映射确定。", f"接收件由 {len(edges)} 条 Creo 约束关系确定。", f"爆炸方向来自约束 {evidence}。"]})
    return contract
