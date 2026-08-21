from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from sop_pipeline.camera_visibility import (
    CameraVisibilityAudit,
    VisibilityThresholds,
    apply_camera_selection,
    audit_camera_visibility,
    audit_camera_visibility_files,
    label_from_rgb,
    camera_selection_decision_from_dict,
    select_camera_from_visibility_audits,
    visibility_contract,
)


MOVING = label_from_rgb(10, 20, 30)
RECEIVER = label_from_rgb(40, 50, 60)
CONTEXT = label_from_rgb(70, 80, 90)


def _labels(*, moving_visible: int, receiver_visible: int) -> tuple[np.ndarray, np.ndarray]:
    isolated = np.zeros((20, 20), dtype=np.uint32)
    isolated[0:10, 0:10] = MOVING
    isolated[10:20, 0:10] = RECEIVER
    staged = np.full((20, 20), CONTEXT, dtype=np.uint32)
    staged.flat[:moving_visible] = MOVING
    receiver_indices = np.arange(200, 200 + receiver_visible)
    staged.flat[receiver_indices] = RECEIVER
    return isolated, staged


def _audit(camera_id: str, moving_visible: int, receiver_visible: int) -> CameraVisibilityAudit:
    isolated, staged = _labels(
        moving_visible=moving_visible,
        receiver_visible=receiver_visible,
    )
    return audit_camera_visibility(
        camera_id=camera_id,
        isolated_labels=isolated,
        staged_labels=staged,
        moving_labels=(MOVING,),
        receiver_labels=(RECEIVER,),
        thresholds=VisibilityThresholds(),
    )


class CameraVisibilityTests(unittest.TestCase):
    def test_exact_label_visibility_selects_the_only_eligible_camera(self) -> None:
        fixed_123 = _audit("fixed_123", 90, 20)
        fixed_456 = _audit("fixed_456", 85, 80)

        decision = select_camera_from_visibility_audits((fixed_123, fixed_456))

        self.assertFalse(fixed_123.eligible)
        self.assertTrue(fixed_456.eligible)
        self.assertEqual(decision.status, "selected")
        self.assertEqual(decision.selected_camera_id, "fixed_456")
        self.assertEqual(decision.options, ())

    def test_two_eligible_cameras_rank_worst_target_visibility_first(self) -> None:
        fixed_123 = _audit("fixed_123", 95, 60)
        fixed_456 = _audit("fixed_456", 80, 80)

        decision = select_camera_from_visibility_audits((fixed_456, fixed_123))

        self.assertEqual(decision.selected_camera_id, "fixed_456")

    def test_exact_tie_stably_prefers_fixed_123(self) -> None:
        decision = select_camera_from_visibility_audits(
            (_audit("fixed_456", 80, 80), _audit("fixed_123", 80, 80))
        )

        self.assertEqual(decision.selected_camera_id, "fixed_123")

    def test_no_eligible_camera_returns_bounded_user_options(self) -> None:
        decision = select_camera_from_visibility_audits(
            (_audit("fixed_123", 30, 80), _audit("fixed_456", 80, 20))
        )

        self.assertEqual(decision.status, "needs_resolution")
        self.assertIsNone(decision.selected_camera_id)
        option_ids = [str(item["option_id"]) for item in decision.options]
        self.assertEqual(
            option_ids,
            [
                "increase_bounded_explosion_distance",
                "focus_receiver_interface",
                "defer_product_camera_calibration",
            ],
        )

    def test_lossless_png_files_are_hashed_and_audited(self) -> None:
        isolated, staged = _labels(moving_visible=80, receiver_visible=80)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            isolated_path = root / "isolated.png"
            staged_path = root / "staged.png"
            Image.fromarray(_rgb(isolated), "RGB").save(isolated_path)
            Image.fromarray(_rgb(staged), "RGB").save(staged_path)

            audit = audit_camera_visibility_files(
                camera_id="fixed_123",
                isolated_raster=isolated_path,
                staged_raster=staged_path,
                moving_labels=(MOVING,),
                receiver_labels=(RECEIVER,),
                thresholds=VisibilityThresholds(),
            )

        self.assertTrue(audit.eligible)
        self.assertTrue(audit.isolated_sha256.startswith("sha256:"))
        self.assertTrue(audit.staged_sha256.startswith("sha256:"))

    def test_contract_requires_both_target_kinds_and_both_fixed_cameras(self) -> None:
        contract = visibility_contract(
            {"51/1": MOVING},
            {"51/2#surface-7": RECEIVER},
        )

        self.assertEqual(
            contract["candidate_camera_ids"], ["fixed_123", "fixed_456"]
        )
        self.assertTrue(contract["formal_render_requires_selected_audit"])
        with self.assertRaisesRegex(ValueError, "moving and receiver"):
            visibility_contract({}, {"receiver": RECEIVER})

    def test_passed_decision_locks_camera_without_exposing_script_fields(self) -> None:
        decision = select_camera_from_visibility_audits(
            (_audit("fixed_123", 90, 50), _audit("fixed_456", 80, 80))
        )
        payload = {
            "camera_id": "fixed_123",
            "camera_catalog": {
                "fixed_123": {"position_direction_root": [1, 1, 1]},
                "fixed_456": {"position_direction_root": [-1, -1, -1]},
            },
            "presentation": {"variants": []},
        }

        locked = apply_camera_selection(payload, decision)

        self.assertEqual(locked["camera_id"], "fixed_456")
        self.assertEqual(
            locked["presentation"]["variants"][0]["variant_id"],
            "visibility-audited",
        )
        self.assertEqual(
            locked["camera_selection"]["selected_camera_id"], "fixed_456"
        )
        self.assertNotIn("script", locked)

    def test_unresolved_decision_cannot_be_compiled_as_a_formal_camera(self) -> None:
        decision = select_camera_from_visibility_audits(
            (_audit("fixed_123", 20, 20), _audit("fixed_456", 20, 20))
        )
        with self.assertRaisesRegex(ValueError, "not eligible"):
            apply_camera_selection({}, decision)

    def test_serialized_decision_is_recomputed_before_it_can_lock_a_camera(self) -> None:
        decision = select_camera_from_visibility_audits(
            (_audit("fixed_123", 90, 60), _audit("fixed_456", 80, 80))
        )
        restored = camera_selection_decision_from_dict(decision.to_dict())
        self.assertEqual(restored.selected_camera_id, "fixed_456")

        tampered = decision.to_dict()
        tampered["selected_camera_id"] = "fixed_123"
        with self.assertRaisesRegex(ValueError, "does not match"):
            camera_selection_decision_from_dict(tampered)

    def test_misaligned_or_non_fixed_evidence_fails_closed(self) -> None:
        isolated, staged = _labels(moving_visible=80, receiver_visible=80)
        with self.assertRaisesRegex(ValueError, "fixed camera"):
            audit_camera_visibility(
                camera_id="invented",
                isolated_labels=isolated,
                staged_labels=staged,
                moving_labels=(MOVING,),
                receiver_labels=(RECEIVER,),
                thresholds=VisibilityThresholds(),
            )
        with self.assertRaisesRegex(ValueError, "aligned"):
            audit_camera_visibility(
                camera_id="fixed_123",
                isolated_labels=isolated,
                staged_labels=staged[:, :-1],
                moving_labels=(MOVING,),
                receiver_labels=(RECEIVER,),
                thresholds=VisibilityThresholds(),
            )

    def test_label_contamination_cannot_be_clamped_to_a_false_pass(self) -> None:
        isolated, staged = _labels(moving_visible=80, receiver_visible=80)
        staged[0:20, 0:20] = MOVING

        with self.assertRaisesRegex(ValueError, "exceeds its aligned isolated"):
            audit_camera_visibility(
                camera_id="fixed_123",
                isolated_labels=isolated,
                staged_labels=staged,
                moving_labels=(MOVING,),
                receiver_labels=(RECEIVER,),
                thresholds=VisibilityThresholds(),
            )


def _rgb(labels: np.ndarray) -> np.ndarray:
    values = np.asarray(labels, dtype=np.uint32)
    return np.stack(
        (
            (values >> 16) & 255,
            (values >> 8) & 255,
            values & 255,
        ),
        axis=2,
    ).astype(np.uint8)


if __name__ == "__main__":
    unittest.main()
