from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sop_pipeline.agent.review import (
    ACCEPT_WITH_OVERRIDE,
    create_human_override_decision,
    prepare_review_step,
)


class ReviewModuleTests(unittest.TestCase):
    def test_failed_real_image_remains_reviewable_with_guided_direction(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "rendered" / "step-1.jpg"
            image.parent.mkdir()
            image.write_bytes(b"original-creo-image")
            step = {
                "step_id": "step-1",
                "status": "FAILED",
                "category": "hard_block",
                "error_code": "DIRECTION_SIGN_WEAK",
                "image_path": "rendered/step-1.jpg",
                "attempted_actions": ["首次生成", "有界重试"],
            }
            package = prepare_review_step(
                root,
                step,
                {"receiver_normal_root": [0.0, 0.0, -1.0]},
            )

        self.assertTrue(package["has_real_image"])
        self.assertTrue(package["override_allowed"])
        self.assertFalse(package["normal_acceptance_allowed"])
        self.assertEqual(package["available_actions"][0], ACCEPT_WITH_OVERRIDE)
        fields = {item["name"]: item for item in package["guided_form"]["fields"]}
        self.assertEqual(fields["axis"]["default"], "Z")
        self.assertEqual(fields["sign"]["default"], "负")
        self.assertEqual(len(package["attempt_history"]), 2)

    def test_placeholder_cannot_be_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            placeholder = root / "internal" / "step-placeholder.png"
            placeholder.parent.mkdir()
            placeholder.write_bytes(b"placeholder")
            package = prepare_review_step(
                root,
                {
                    "status": "FAILED",
                    "category": "hard_block",
                    "error_code": "MOVING_OCCURRENCE_UNRESOLVED",
                    "image_path": "internal/step-placeholder.png",
                },
            )

        self.assertFalse(package["has_real_image"])
        self.assertFalse(package["override_allowed"])
        self.assertNotIn(ACCEPT_WITH_OVERRIDE, package["available_actions"])

    def test_override_audits_original_hash_and_forbids_watermark(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "rendered" / "step-1.jpg"
            image.parent.mkdir()
            image.write_bytes(b"byte-identical-original")
            decision = create_human_override_decision(
                root,
                {
                    "status": "FAILED",
                    "category": "hard_block",
                    "error_code": "ARROW_AUDIT_INVALID",
                    "image_path": "rendered/step-1.jpg",
                },
                step_id="step-1",
                revision=2,
                reason="现场确认箭头表达可以采用",
            )

        self.assertEqual(decision["decision"], ACCEPT_WITH_OVERRIDE)
        self.assertEqual(decision["publication_transform"], "none")
        self.assertFalse(decision["watermark"])
        self.assertEqual(decision["selected_image_path"], "rendered/step-1.jpg")
        self.assertTrue(decision["selected_image_sha256"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
