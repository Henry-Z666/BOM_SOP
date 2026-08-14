from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RepairCandidate:
    candidate_id: str
    step_id: str
    factor: str
    changes: dict[str, Any]


class BoundedRepairPlanner:
    """Select one bounded visual factor for a comparable candidate group."""

    STRUCTURAL_FAILURES = {
        "ASSEMBLY_HASH_MISMATCH",
        "MOVING_OCCURRENCE_UNRESOLVED",
        "RECEIVER_OCCURRENCE_UNRESOLVED",
        "BOM_QUANTITY_MISMATCH",
        "VISIBLE_SET_MISMATCH",
        "ROTATION_CHANGED",
        "CAMERA_NOT_FIXED",
        "CAMERA_GEOMETRY_INVALID",
        "CAMERA_RECEIVER_SILHOUETTE",
        "EXPLOSION_NOT_VISIBLE_IN_CAMERA",
        "PRESENTATION_CONTRACT_INVALID",
        "ARROW_AUDIT_INVALID",
        "ARROW_COVERAGE_INVALID",
        "ARROW_SURFACE_ANCHOR_UNAVAILABLE",
        "TRANSLATION_AUDIT_INVALID",
    }

    def propose(
        self,
        *,
        step_id: str,
        failure_codes: tuple[str, ...],
        current: dict[str, Any],
    ) -> tuple[RepairCandidate, ...]:
        del current
        failures = set(failure_codes)
        if failures & self.STRUCTURAL_FAILURES:
            return ()
        if failures & {
            "ARROW_COVERAGE_MISMATCH",
            "ARROW_VECTOR_MISMATCH",
            "ARROW_ANCHOR_MISSING",
            "ARROW_OUT_OF_FRAME",
            "ARROW_OVERLAP",
        }:
            return self._group(
                step_id,
                "arrow_layout",
                (
                    {"arrow_layout": "alternate_anchor_1"},
                    {"arrow_layout": "alternate_anchor_2"},
                    {"arrow_layout": "audited_merge"},
                ),
            )
        if failures & {"SUBJECT_TOO_SMALL", "ARROW_TOO_SMALL"}:
            return self._group(
                step_id,
                "zoom",
                ({"zoom": 1.5}, {"zoom": 2.1}),
            )
        if failures & {
            "SUBJECT_NOT_DETECTED",
            "SUBJECT_TOO_LARGE",
            "SUBJECT_CLIPPED",
            "EXCESSIVE_CONTEXT_CLIPPING",
            "ARROW_NOT_VISIBLE",
            "ARROW_CLIPPED",
        }:
            return self._group(
                step_id,
                "zoom",
                ({"zoom": 0.85}, {"zoom": 0.8}),
            )
        if failures & {"IMAGE_DIMENSIONS_MISMATCH", "IMAGE_INVALID"}:
            return self._group(
                step_id,
                "framing",
                ({"framing": "center"}, {"framing": "fit_square"}),
            )
        return ()

    @staticmethod
    def _group(
        step_id: str,
        factor: str,
        variants: tuple[dict[str, Any], ...],
    ) -> tuple[RepairCandidate, ...]:
        return tuple(
            RepairCandidate(
                candidate_id=f"{step_id}-{factor}-{index}",
                step_id=step_id,
                factor=factor,
                changes=changes,
            )
            for index, changes in enumerate(variants[:4], start=1)
        )
