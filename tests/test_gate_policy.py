from __future__ import annotations

import unittest

from sop_pipeline.agent.gate_policy import (
    GateCategory,
    classify_failures,
    gate_policy,
)
from sop_pipeline.agent.skill_handlers import _link_revision_cameras
from sop_pipeline.agent.skill_handlers import _confirmed_receiver_direction
from sop_pipeline.agent.skill_handlers import _revised_display_translation


class GatePolicyTests(unittest.TestCase):
    def test_structural_truth_codes_remain_hard_blocks(self) -> None:
        for code in (
            "ASSEMBLY_HASH_MISMATCH",
            "MOVING_OCCURRENCE_UNRESOLVED",
            "RECEIVER_OCCURRENCE_UNRESOLVED",
            "BOM_QUANTITY_MISMATCH",
            "VISIBLE_SET_MISMATCH",
            "ROTATION_CHANGED",
            "ARROW_AUDIT_INVALID",
            "ARROW_COVERAGE_INVALID",
            "TRANSLATION_AUDIT_INVALID",
            "DIRECTION_SIGN_WEAK",
            "RECEIVER_NORMAL_NOT_AXIS_ALIGNED",
            "CAMERA_VISIBILITY_AUDIT_INVALID",
        ):
            with self.subTest(code=code):
                policy = gate_policy(code)
                self.assertEqual(policy.category, GateCategory.HARD_BLOCK)
                self.assertFalse(policy.retain_real_image)

    def test_camera_compatibility_codes_require_replanning(self) -> None:
        for code in (
            "CAMERA_RECEIVER_WRONG_HALF_SPACE",
            "CAMERA_RECEIVER_SILHOUETTE",
            "EXPLOSION_NOT_VISIBLE_IN_CAMERA",
        ):
            with self.subTest(code=code):
                policy = gate_policy(code)
                self.assertEqual(policy.category, GateCategory.HARD_BLOCK)
                self.assertFalse(policy.retain_real_image)

    def test_presentation_codes_retain_real_images_for_human_review(self) -> None:
        for code in (
            "SUBJECT_TOO_SMALL",
            "SUBJECT_TOO_LARGE",
            "EXCESSIVE_CONTEXT_CLIPPING",
            "ARROW_NOT_VISIBLE",
            "ARROW_TOO_SMALL",
            "ARROW_CLIPPED",
            "ACTIVITY_NOT_CENTERED",
            "ARROW_NOT_CENTERED",
        ):
            with self.subTest(code=code):
                policy = gate_policy(code)
                self.assertEqual(policy.category, GateCategory.HUMAN_REVIEW)
                self.assertTrue(policy.retain_real_image)

    def test_blank_frame_and_runtime_codes_are_system_retries(self) -> None:
        for code in (
            "SUBJECT_NOT_DETECTED",
            "RENDER_OUTPUT_MISSING",
            "RENDER_FRAME_INVALID",
            "CREO_TIMEOUT",
            "CREO_PROCESS_ERROR",
            "CREO_RENDER_FAILED",
            "PRESENTATION_CONTRACT_INVALID",
        ):
            with self.subTest(code=code):
                self.assertEqual(
                    gate_policy(code).category,
                    GateCategory.SYSTEM_RETRY,
                )

    def test_exact_occlusion_codes_enter_bounded_auto_repair(self) -> None:
        for code in (
            "MOVING_SET_OCCLUDED",
            "MOVING_OCCURRENCE_OCCLUDED",
            "RECEIVER_INTERFACE_OCCLUDED",
            "RECEIVER_INTERFACE_PATCH_OCCLUDED",
            "NO_ELIGIBLE_FIXED_CAMERA",
        ):
            with self.subTest(code=code):
                policy = gate_policy(code)
                self.assertEqual(policy.category, GateCategory.AUTO_REPAIR)
                self.assertFalse(policy.retain_real_image)

    def test_missing_or_too_small_camera_audit_is_a_system_retry(self) -> None:
        for code in (
            "CAMERA_VISIBILITY_AUDIT_MISSING",
            "MOVING_AUDIT_TARGET_TOO_SMALL",
            "RECEIVER_INTERFACE_AUDIT_TARGET_TOO_SMALL",
        ):
            with self.subTest(code=code):
                self.assertEqual(
                    gate_policy(code).category,
                    GateCategory.SYSTEM_RETRY,
                )

    def test_strictest_failure_class_wins_without_losing_all_codes(self) -> None:
        decision = classify_failures(
            (
                "SUBJECT_TOO_SMALL",
                "ARROW_AUDIT_INVALID",
                "CAMERA_RECEIVER_SILHOUETTE",
            )
        )

        self.assertEqual(decision.category, GateCategory.HARD_BLOCK)
        self.assertEqual(decision.primary_code, "ARROW_AUDIT_INVALID")
        self.assertEqual(
            decision.failures,
            (
                "SUBJECT_TOO_SMALL",
                "ARROW_AUDIT_INVALID",
                "CAMERA_RECEIVER_SILHOUETTE",
            ),
        )

    def test_unknown_code_fails_closed_with_actionable_policy(self) -> None:
        policy = gate_policy("NEW_UNCLASSIFIED_GATE")

        self.assertEqual(policy.category, GateCategory.HARD_BLOCK)
        self.assertIn("未分类", policy.user_message)
        self.assertTrue(policy.suggested_action)

    def test_direction_revision_uses_absolute_alignment_and_stable_tie(self) -> None:
        contract = {
            "receiver_normal_root": [-1.0, 0.0, 0.0],
            "translation_vector_root": [-80.0, 0.0, 0.0],
            "camera_catalog": {
                "fixed_123": {
                    "id": "fixed_123",
                    "position_direction_root": [0.8, 0.0, 0.6],
                    "up_reference_root": [0.0, 1.0, 0.0],
                },
                "fixed_456": {
                    "id": "fixed_456",
                    "position_direction_root": [-0.8, 0.0, -0.6],
                    "up_reference_root": [0.0, 1.0, 0.0],
                },
            },
        }
        presentation = {
            "variants": [
                {
                    "variant_id": "old",
                    "camera_id": "fixed_123",
                    "zoom": 1.2,
                    "pan": [0.1, -0.1],
                }
            ]
        }

        _link_revision_cameras(
            contract,
            presentation,
            revision_number=2,
        )

        self.assertEqual(contract["camera_id"], "fixed_123")
        self.assertEqual(
            [item["camera_id"] for item in presentation["variants"]],
            ["fixed_123"],
        )
        self.assertEqual(presentation["variants"][0]["zoom"], 1.2)
        self.assertEqual(presentation["variants"][0]["pan"], [0.1, -0.1])

    def test_structured_direction_only_changes_measured_axis_sign(self) -> None:
        measured = [0.0, 0.1, 0.994987437]

        confirmed = _confirmed_receiver_direction(measured, [0.0, 0.0, -1.0])

        self.assertAlmostEqual(confirmed[1], -0.1)
        self.assertAlmostEqual(confirmed[2], -0.994987437)
        with self.assertRaisesRegex(ValueError, "不能改变承接轴"):
            _confirmed_receiver_direction(measured, [1.0, 0.0, 0.0])
        with self.assertRaisesRegex(ValueError, "缺少已测 Creo 承接轴"):
            _confirmed_receiver_direction(None, [0.0, 0.0, 1.0])

    def test_direction_confirmation_recomputes_lateral_display_translation(self) -> None:
        contract = {
            "stage_geometry_root": {
                "moving_bounds": [
                    {"min": [-1.0, -165.0, -1.0], "max": [1.0, 165.0, 1.0]}
                ],
                "context_bounds": [
                    {"min": [-8.0, -220.0, -20.0], "max": [8.0, 220.0, 0.0]}
                ],
            },
            "arrow_anchors": [{"complete_point_root": [0.0, 160.0, 0.0]}],
        }

        selected = _revised_display_translation(
            contract, [0.0, 1.0, 0.0], 100.0
        )

        self.assertEqual(selected["mode"], "lateral_clearance")
        self.assertEqual(selected["translation_vector_root"], [0.0, 0.0, 100.0])


if __name__ == "__main__":
    unittest.main()
