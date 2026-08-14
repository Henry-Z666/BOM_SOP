from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image


@dataclass(frozen=True)
class ArrowEvidence:
    covered_occurrences: tuple[str, ...]
    local_anchor_id: str
    exploded_root: tuple[float, float, float]
    complete_root: tuple[float, float, float]
    expected_installation_vector: tuple[float, float, float]
    in_frame: bool
    overlaps: bool


@dataclass(frozen=True)
class RenderEvidence:
    image_file: Path
    expected_assembly_hash: str
    actual_assembly_hash: str
    moving_occurrences: tuple[str, ...]
    resolved_moving_occurrences: tuple[str, ...]
    receiver_occurrences: tuple[str, ...]
    resolved_receiver_occurrences: tuple[str, ...]
    expected_visible_occurrences: tuple[str, ...]
    actual_visible_occurrences: tuple[str, ...]
    bom_quantity: int
    rotation_unchanged: bool
    camera_id: str
    arrows: tuple[ArrowEvidence, ...]
    forbidden_content_detected: bool
    expected_dimensions: tuple[int, int]


@dataclass(frozen=True)
class RenderGateReport:
    schema_version: str
    passed: bool
    failures: tuple[str, ...]


class DeterministicRenderValidator:
    """Hard publication gates which no language model may waive."""

    def __init__(self, *, vector_tolerance: float = 1e-6) -> None:
        self.vector_tolerance = vector_tolerance

    def validate(self, evidence: RenderEvidence) -> RenderGateReport:
        failures: list[str] = []
        if evidence.actual_assembly_hash != evidence.expected_assembly_hash:
            failures.append("ASSEMBLY_HASH_MISMATCH")
        if sorted(evidence.resolved_moving_occurrences) != sorted(
            evidence.moving_occurrences
        ):
            failures.append("MOVING_OCCURRENCE_UNRESOLVED")
        if sorted(evidence.resolved_receiver_occurrences) != sorted(
            evidence.receiver_occurrences
        ):
            failures.append("RECEIVER_OCCURRENCE_UNRESOLVED")
        if len(evidence.moving_occurrences) != evidence.bom_quantity:
            failures.append("BOM_QUANTITY_MISMATCH")
        if set(evidence.actual_visible_occurrences) != set(
            evidence.expected_visible_occurrences
        ):
            failures.append("VISIBLE_SET_MISMATCH")
        if not evidence.rotation_unchanged:
            failures.append("ROTATION_CHANGED")
        if evidence.camera_id not in {"fixed_123", "fixed_456"}:
            failures.append("CAMERA_NOT_FIXED")
        self._validate_arrows(evidence, failures)
        if evidence.forbidden_content_detected:
            failures.append("FORBIDDEN_CONTENT_VISIBLE")
        try:
            with Image.open(evidence.image_file) as image:
                if image.size != evidence.expected_dimensions:
                    failures.append("IMAGE_DIMENSIONS_MISMATCH")
                image.verify()
        except (FileNotFoundError, OSError):
            failures.append("IMAGE_INVALID")
        return RenderGateReport(
            schema_version="render-gate-report/v1",
            passed=not failures,
            failures=tuple(dict.fromkeys(failures)),
        )

    def _validate_arrows(
        self,
        evidence: RenderEvidence,
        failures: list[str],
    ) -> None:
        covered = {
            occurrence
            for arrow in evidence.arrows
            for occurrence in arrow.covered_occurrences
        }
        if covered != set(evidence.moving_occurrences):
            failures.append("ARROW_COVERAGE_MISMATCH")
        for arrow in evidence.arrows:
            actual_vector = tuple(
                complete - exploded
                for complete, exploded in zip(
                    arrow.complete_root,
                    arrow.exploded_root,
                    strict=True,
                )
            )
            if not _vectors_close(
                actual_vector,
                arrow.expected_installation_vector,
                self.vector_tolerance,
            ):
                failures.append("ARROW_VECTOR_MISMATCH")
            if not arrow.local_anchor_id:
                failures.append("ARROW_ANCHOR_MISSING")
            if not arrow.in_frame:
                failures.append("ARROW_OUT_OF_FRAME")
            if arrow.overlaps:
                failures.append("ARROW_OVERLAP")


def _vectors_close(
    left: Iterable[float],
    right: Iterable[float],
    tolerance: float,
) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(left, right, strict=True))
