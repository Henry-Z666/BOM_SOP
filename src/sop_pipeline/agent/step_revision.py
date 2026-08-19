from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Any


CURRENT_IMAGE_CANDIDATE_ID = "current-image"


class RevisionKind(StrEnum):
    PRESENTATION = "presentation"
    INSTALLATION_GEOMETRY = "installation_geometry"
    COMPLETE_STATE = "complete_state"


@dataclass(frozen=True)
class StepRevision:
    revision: int
    step_id: str
    kind: RevisionKind
    changes: dict[str, Any]


class StepDependencyGraph:
    def __init__(self, dependencies: dict[str, tuple[str, ...]]) -> None:
        self.dependencies = dict(dependencies)
        self.children: dict[str, list[str]] = {
            step_id: [] for step_id in dependencies
        }
        for step_id, parents in dependencies.items():
            for parent in parents:
                if parent not in self.children:
                    raise ValueError(f"unknown dependency: {parent}")
                self.children[parent].append(step_id)
        self._assert_acyclic()

    def invalidated_by(self, revision: StepRevision) -> frozenset[str]:
        validate_revision(revision)
        if revision.step_id not in self.dependencies:
            raise ValueError(f"unknown revised step: {revision.step_id}")
        invalidated = {revision.step_id}
        if revision.kind is not RevisionKind.COMPLETE_STATE:
            affected = revision.changes.get("affected_steps", ())
            invalidated.update(
                step_id for step_id in affected if step_id in self.dependencies
            )
            return frozenset(invalidated)
        pending = list(self.children[revision.step_id])
        while pending:
            step_id = pending.pop()
            if step_id in invalidated:
                continue
            invalidated.add(step_id)
            pending.extend(self.children[step_id])
        return frozenset(invalidated)

    def _assert_acyclic(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("step dependency graph contains a cycle")
            if step_id in visited:
                return
            visiting.add(step_id)
            for parent in self.dependencies[step_id]:
                visit(parent)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in self.dependencies:
            visit(step_id)


_ALLOWED_CHANGES = {
    RevisionKind.PRESENTATION: {
        "camera_id",
        "zoom",
        "pan",
        "explosion_distance",
        "arrow_anchor",
        "arrow_layout",
        "candidate_id",
        "affected_steps",
    },
    RevisionKind.INSTALLATION_GEOMETRY: {
        "moving_occurrences",
        "receiver_occurrences",
        "direction",
        "affected_steps",
    },
    RevisionKind.COMPLETE_STATE: {
        "moving_occurrences",
        "receiver_occurrences",
        "direction",
        "depends_on",
        "order",
    },
}


def validate_revision(revision: StepRevision) -> StepRevision:
    if revision.revision < 1:
        raise ValueError("step revision must be positive")
    if not revision.step_id:
        raise ValueError("step revision requires step_id")
    unsupported = set(revision.changes) - _ALLOWED_CHANGES[revision.kind]
    if unsupported:
        raise ValueError(f"unsupported revision fields: {sorted(unsupported)}")
    camera = revision.changes.get("camera_id")
    if camera is not None and camera not in {"fixed_123", "fixed_456"}:
        raise ValueError("camera_id must be fixed_123 or fixed_456")
    zoom = revision.changes.get("zoom")
    if zoom is not None:
        try:
            zoom_value = float(zoom)
        except (TypeError, ValueError) as error:
            raise ValueError("zoom must be a number") from error
        if not math.isfinite(zoom_value) or not 0.5 <= zoom_value <= 2.0:
            raise ValueError("zoom is outside the bounded repair range")
    pan = revision.changes.get("pan")
    if pan is not None:
        if not isinstance(pan, (list, tuple)) or len(pan) != 2:
            raise ValueError("pan must be a two-number array")
        try:
            pan_values = tuple(float(value) for value in pan)
        except (TypeError, ValueError) as error:
            raise ValueError("pan must be a two-number array") from error
        if any(not math.isfinite(value) or abs(value) > 1.0 for value in pan_values):
            raise ValueError("pan is outside the bounded repair range")
    distance = revision.changes.get("explosion_distance")
    if distance is not None:
        try:
            distance_value = float(distance)
        except (TypeError, ValueError) as error:
            raise ValueError("explosion distance must be a number") from error
        if (
            not math.isfinite(distance_value)
            or not 0.0 <= distance_value <= 1000.0
        ):
            raise ValueError("explosion distance is outside the bounded repair range")
    direction = revision.changes.get("direction")
    if direction is not None:
        if not isinstance(direction, (list, tuple)) or len(direction) != 3:
            raise ValueError("direction must be a three-number array")
        try:
            direction_values = tuple(float(value) for value in direction)
        except (TypeError, ValueError) as error:
            raise ValueError("direction must be a three-number array") from error
        if (
            any(not math.isfinite(value) for value in direction_values)
            or sum(value * value for value in direction_values) <= 1.0e-18
        ):
            raise ValueError("direction must be a non-zero finite vector")
    return revision
