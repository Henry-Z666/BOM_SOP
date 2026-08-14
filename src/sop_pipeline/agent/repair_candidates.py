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
    }

    def propose(
        self,
        *,
        step_id: str,
        failure_codes: tuple[str, ...],
        current: dict[str, Any],
    ) -> tuple[RepairCandidate, ...]:
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
        if "CAMERA_NOT_FIXED" in failures:
            return self._group(
                step_id,
                "camera_id",
                ({"camera_id": "fixed_123"}, {"camera_id": "fixed_456"}),
            )
        if failures & {"IMAGE_DIMENSIONS_MISMATCH", "IMAGE_INVALID"}:
            return self._group(
                step_id,
                "framing",
                ({"framing": "center"}, {"framing": "fit_square"}),
            )
        camera = current.get("camera_id", "fixed_123")
        alternate = "fixed_456" if camera == "fixed_123" else "fixed_123"
        return self._group(
            step_id,
            "camera_id",
            ({"camera_id": camera}, {"camera_id": alternate}),
        )

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
