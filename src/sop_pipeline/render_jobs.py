"""Turn Creo-native constraints into deterministic activity-group render jobs."""
from __future__ import annotations
import math
from pathlib import Path
from typing import Any
from .cad_graph import CadGraph
from .io import write_json

def _origin(node: dict[str, Any]) -> list[float]:
    value = node.get("transform", {}).get("origin", [0.0, 0.0, 0.0])
    return [float(value[i]) if i < len(value) else 0.0 for i in range(3)]

def _vector(origin: list[float], centre: list[float], distance: float = 160.0) -> list[float]:
    raw = [origin[i] - centre[i] for i in range(3)]
    length = math.sqrt(sum(value * value for value in raw))
    axis = [1.0, 0.0, 0.0] if length < 1e-6 else [value / length for value in raw]
    return [round(value * distance, 3) for value in axis]

def create_render_jobs(graph: CadGraph, output: Path) -> Path:
    if not graph.occurrences: raise ValueError("CAD 图谱没有 occurrence，无法创建渲染任务。")
    node_key = lambda node: str(node.get("occurrence_id") or node["id"])
    known = {node_key(node) for node in graph.occurrences}; parent = {item: item for item in known}
    def find(item: str) -> str:
        while parent[item] != item: parent[item] = parent[parent[item]]; item = parent[item]
        return item
    def union(left: str, right: str) -> None:
        left, right = find(left), find(right)
        if left != right: parent[right] = left
    # A direct constraint linking two occurrence IDs means both travel as the
    # already-built activity group at their next parent-assembly operation.
    for edge in graph.constraints:
        ends = [str(item) for item in edge.get("occurrences", []) if str(item) in known]
        if len(ends) == 2: union(ends[0], ends[1])
    anchors = {str(edge["occurrences"][0]) for edge in graph.constraints
               if graph.root_occurrence in edge.get("occurrences", []) and str(edge["occurrences"][0]) in known}
    groups: dict[str, list[dict[str, Any]]] = {}
    for node in graph.occurrences: groups.setdefault(find(node_key(node)), []).append(node)
    origins = [_origin(node) for node in graph.occurrences]
    centre = [sum(point[i] for point in origins) / len(origins) for i in range(3)]
    assembly_name = Path(graph.assembly_file).name; jobs: list[dict[str, Any]] = []
    for index, nodes in enumerate(groups.values(), 1):
        all_members = sorted(node_key(node) for node in nodes)
        # A root-fixed member is the receiving base of this subassembly.  Its
        # constrained children are the small parts being installed, not an
        # object that should be exploded together with them.
        moving = [item for item in all_members if item not in anchors] or all_members
        edges = graph.partners(set(moving))
        receivers = sorted({item for edge in edges for item in edge.get("occurrences", []) if item not in moving}) or [graph.root_occurrence]
        moving_nodes = [node for node in nodes if node_key(node) in moving]
        group_origin = [sum(_origin(node)[i] for node in moving_nodes) / len(moving_nodes) for i in range(3)]
        jobs.append({"job_id": f"{Path(assembly_name).stem.lower()}-{index:02d}-{'_'.join(item.lower() for item in moving)}",
          "assembly_file": assembly_name, "moving_occurrences": moving, "moving_occurrence_paths": [CadGraph.occurrence_path(node) for node in moving_nodes],
          "receiver_occurrences": receivers,
          "translation": {"type": "translation_only", "vector": _vector(group_origin, centre), "evidence": "Creo constraint-connected activity group"},
          "render": {"exploded": True, "temporary_simplified_rep": "AI_SOP_TEMP"},
          "automation": {"phase": "planned", "confidence": 0.65 if edges else 0.45, "reason": "constraint-connected group; no manual selection"}})
    write_json(output, {"schema_version": "creo-render-jobs/v3", "assembly_file": assembly_name,
                        "legacy_intermediate_assembly": graph.assembly_file, "jobs": jobs})
    return output
