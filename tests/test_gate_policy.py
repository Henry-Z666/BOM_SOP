from __future__ import annotations

import unittest

from sop_pipeline.agent.gate_policy import (
    GateCategory,
    classify_failures,
    gate_policy,
)
from sop_pipeline.agent.skill_handlers import _link_revision_cameras


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
        ):
            with self.subTest(code=code):
                policy = gate_policy(code)
                self.assertEqual(policy.category, GateCategory.HARD_BLOCK)
                self.assertFalse(policy.retain_real_image)

    def test_camera_compatibility_codes_are_automatic_repairs(self) -> None:
        for code in (
            "CAMERA_RECEIVER_WRONG_HALF_SPACE",
            "CAMERA_RECEIVER_SILHOUETTE",
            "EXPLOSION_NOT_VISIBLE_IN_CAMERA",
        ):
            with self.subTest(code=code):
                policy = gate_policy(code)
                self.assertEqual(policy.category, GateCategory.AUTO_REPAIR)
                self.assertTrue(policy.retain_real_image)

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

    def test_strictest_failure_class_wins_without_losing_all_codes(self) -> None:
        decision = classify_failures(
            (
                "SUBJECT_TOO_SMALL",
                "CAMERA_RECEIVER_SILHOUETTE",
                "ARROW_AUDIT_INVALID",
            )
        )

        self.assertEqual(decision.category, GateCategory.HARD_BLOCK)
        self.assertEqual(decision.primary_code, "ARROW_AUDIT_INVALID")
        self.assertEqual(
            decision.failures,
            (
                "SUBJECT_TOO_SMALL",
                "CAMERA_RECEIVER_SILHOUETTE",
                "ARROW_AUDIT_INVALID",
            ),
        )

    def test_unknown_code_fails_closed_with_actionable_policy(self) -> None:
        policy = gate_policy("NEW_UNCLASSIFIED_GATE")

        self.assertEqual(policy.category, GateCategory.HARD_BLOCK)
        self.assertIn("未分类", policy.user_message)
        self.assertTrue(policy.suggested_action)

    def test_direction_revision_relinks_both_fixed_cameras(self) -> None:
        contract = {
            "receiver_normal_root": [-1.0, 0.0, 0.0],
            "translation_vector_root": [-80.0, 0.0, 0.0],
            "camera_catalog": {
                "fixed_123": {
                    "id": "fixed_123",
                    "position_direction_root": [0.8, 0.0, 0.6],
                },
                "fixed_456": {
                    "id": "fixed_456",
                    "position_direction_root": [-0.8, 0.0, 0.6],
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
            preferred_camera_id="fixed_123",
        )

        self.assertEqual(contract["camera_id"], "fixed_456")
        self.assertEqual(
            [item["camera_id"] for item in presentation["variants"]],
            ["fixed_456", "fixed_123"],
        )
        self.assertEqual(presentation["variants"][0]["zoom"], 1.2)
        self.assertEqual(presentation["variants"][0]["pan"], [0.1, -0.1])


if __name__ == "__main__":
    unittest.main()
