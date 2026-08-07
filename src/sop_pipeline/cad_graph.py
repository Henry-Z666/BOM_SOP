"""Creo-native assembly facts exported by the discovery runner.

This is deliberately geometry/constraint data, not AI interpretation. It is
the only input that may establish an occurrence ID or installation direction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def normalize(value: str) -> str:
    return "".join(char.lower() for char in str(value) if char.isalnum())


@dataclass(frozen=True)
class CadGraph:
    assembly_file: str
    root_occurrence: str
    occurrences: list[dict[str, Any]]
    constraints: list[dict[str, Any]]

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "CadGraph":
        if raw.get("schema_version") not in {"creo-cad-graph/v1", "creo-cad-graph/v2"}:
            raise ValueError("不支持的 CAD 图谱版本")
        for key in ("assembly_file", "root_occurrence", "occurrences", "constraints"):
            if key not in raw: raise ValueError(f"CAD 图谱缺少 {key}")
        return cls(raw["assembly_file"], raw["root_occurrence"], raw["occurrences"], raw["constraints"])

    def match(self, part_no: str) -> list[dict[str, Any]]:
        target = normalize(part_no)
        return [node for node in self.occurrences if any(target in candidate or candidate in target for candidate in
                (normalize(node.get("part_no", "")), normalize(node.get("model_name", ""))) if candidate)]

    def partners(self, occurrence_ids: set[str]) -> list[dict[str, Any]]:
        return [edge for edge in self.constraints if occurrence_ids.intersection(edge.get("occurrences", []))]

    @staticmethod
    def occurrence_path(node: dict[str, Any]) -> list[int]:
        """Return a root-ASM component path for v2 nodes and old flat nodes."""
        if node.get("component_path"):
            return [int(value) for value in node["component_path"]]
        raw = str(node.get("occurrence_id", node.get("id", ""))).removeprefix("C_")
        return [int(value.removeprefix("C_")) for value in raw.split("/") if value]

    @classmethod
    def occurrence_key(cls, node: dict[str, Any]) -> str:
        return "/".join(str(value) for value in cls.occurrence_path(node))
