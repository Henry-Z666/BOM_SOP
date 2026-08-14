from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from PIL import Image

from sop_pipeline.agent.render_validation import (
    ArrowEvidence,
    DeterministicRenderValidator,
    RenderEvidence,
)


def _evidence(image: Path, **changes) -> RenderEvidence:
    values = dict(
        image_file=image,
        expected_assembly_hash="abc",
        actual_assembly_hash="abc",
        moving_occurrences=("1/2",),
        resolved_moving_occurrences=("1/2",),
        receiver_occurrences=("1/1",),
        resolved_receiver_occurrences=("1/1",),
        expected_visible_occurrences=("1/1", "1/2"),
        actual_visible_occurrences=("1/1", "1/2"),
        bom_quantity=1,
        rotation_unchanged=True,
        camera_id="fixed_123",
        arrows=(
            ArrowEvidence(
                covered_occurrences=("1/2",),
                local_anchor_id="surface:7/u:0.5/v:0.5",
                exploded_root=(10.0, 0.0, 0.0),
                complete_root=(0.0, 0.0, 0.0),
                expected_installation_vector=(-10.0, 0.0, 0.0),
                in_frame=True,
                overlaps=False,
            ),
        ),
        forbidden_content_detected=False,
        expected_dimensions=(1600, 1600),
    )
    values.update(changes)
    return RenderEvidence(**values)


class RenderValidationTests(unittest.TestCase):
    def test_valid_image_passes_all_deterministic_hard_gates(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "step.jpg"
            Image.new("RGB", (1600, 1600), "white").save(image)
            report = DeterministicRenderValidator().validate(_evidence(image))

        self.assertTrue(report.passed)
        self.assertEqual(report.failures, ())

    def test_future_occurrence_and_unapproved_camera_fail_hard_gate(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "step.jpg"
            Image.new("RGB", (1600, 1600), "white").save(image)
            report = DeterministicRenderValidator().validate(
                _evidence(
                    image,
                    actual_visible_occurrences=("1/1", "1/2", "1/99"),
                    camera_id="custom_view",
                )
            )

        self.assertFalse(report.passed)
        self.assertIn("VISIBLE_SET_MISMATCH", report.failures)
        self.assertIn("CAMERA_NOT_FIXED", report.failures)

    def test_arrow_must_use_complete_occurrence_coverage_and_expected_vector(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "step.jpg"
            Image.new("RGB", (1600, 1600), "white").save(image)
            bad_arrow = ArrowEvidence(
                covered_occurrences=(),
                local_anchor_id="surface:7",
                exploded_root=(10.0, 0.0, 0.0),
                complete_root=(5.0, 0.0, 0.0),
                expected_installation_vector=(-10.0, 0.0, 0.0),
                in_frame=True,
                overlaps=False,
            )
            report = DeterministicRenderValidator().validate(
                _evidence(image, arrows=(bad_arrow,))
            )

        self.assertIn("ARROW_COVERAGE_MISMATCH", report.failures)
        self.assertIn("ARROW_VECTOR_MISMATCH", report.failures)


if __name__ == "__main__":
    unittest.main()
