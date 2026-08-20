from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from PIL import Image, ImageDraw

from sop_pipeline.agent.render_validation import (
    ArrowEvidence,
    DeterministicNativeRenderValidator,
    DeterministicRenderValidator,
    RenderEvidence,
)


def _native_payload(**changes) -> dict:
    payload = {
        "moving_occurrences": ["1/2"],
        "receiver_normal_root": [1.0, 0.0, 0.0],
        "translation_vector_root": [0.0, 0.0, 10.0],
        "camera_catalog": {
            "fixed_123": {
                "position_direction_root": [1.0, 0.0, 0.0],
                "up_reference_root": [0.0, 1.0, 0.0],
            }
        },
        "presentation": {
            "schema_version": "fixed-frame-presentation/v1",
            "focus_context": "stage_visible_bbox/v1",
            "framing_priority": "installation_activity/v1",
            "zoom_anchor": "installation_activity_center/v1",
            "native_refit": {
                "schema_version": "native-focus-refit/v1",
                "fit_occurrences": "moving_only/v1",
                "restore_stage_context_without_refit": True,
            },
            "center_gate": {
                "schema_version": "native-composition-center-gate/v1",
                "target_pixel": [800, 800],
                "max_activity_center_offset_pixels": 120,
                "max_arrow_center_offset_pixels": 120,
            },
            "variants": [
                {"variant_id": "base", "camera_id": "fixed_123", "zoom": 1.0, "pan": [0.0, 0.0]}
            ],
            "frame_gate": {
                "schema_version": "raster-composition-gate/v2",
                "foreground_delta": 30,
                "min_component_pixels": 32,
                "component_downsample": 4,
                "min_subject_span": 0.54,
                "max_subject_span": 1.0,
                "max_clipped_edges": 2,
                "arrow_green_delta": 20,
                "min_arrow_pixels": 120,
                "min_arrow_span_pixels": 24,
                "min_arrow_border_margin_pixels": 40,
                "ignored_regions": [[0, 1250, 500, 1600]],
            },
        },
    }
    payload.update(changes)
    return payload


def _write_native_files(folder: Path, bbox: tuple[int, int, int, int] | None) -> tuple[Path, Path]:
    image_file = folder / "native.jpg"
    image = Image.new("RGB", (1600, 1600), "white")
    if bbox is not None:
        draw = ImageDraw.Draw(image)
        draw.rectangle(bbox, fill=(80, 100, 120))
        draw.line((675, 800, 925, 800), fill=(0, 150, 0), width=8)
    image.save(image_file)
    audit_file = folder / "native.arrow.json"
    audit_file.write_text(
        json.dumps(
            {
                "schema_version": "arrow-projection/v1",
                "policy": "same_cad_point/v1",
                "status": "passed",
                "arrows": [
                    {
                        "covered_occurrences": ["1/2"],
                        "anchor_source": "model_surface",
                        "complete_root": [0.0, 0.0, 0.0],
                        "exploded_root": [0.0, 0.0, 10.0],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return image_file, audit_file


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

    def test_native_gate_accepts_valid_frame_camera_and_creo_arrow_audit(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image, audit = _write_native_files(Path(folder), (250, 300, 1200, 1150))
            report = DeterministicNativeRenderValidator().validate(
                image, audit, _native_payload()
            )

        self.assertTrue(report.passed)
        self.assertIsNotNone(report.composition)
        self.assertGreater(report.composition.max_span_fraction, 0.54)
        self.assertGreater(report.arrow_raster.pixels, 120)

    def test_native_gate_does_not_treat_arrowhead_fragments_as_extra_arrows(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image, audit = _write_native_files(root, (250, 300, 1200, 1150))
            with Image.open(image) as source:
                frame = source.convert("RGB")
            draw = ImageDraw.Draw(frame)
            draw.line((675, 600, 925, 600), fill=(0, 150, 0), width=8)
            draw.line((675, 1000, 925, 1000), fill=(0, 150, 0), width=8)
            frame.save(image)

            report = DeterministicNativeRenderValidator().validate(
                image, audit, _native_payload()
            )

        self.assertNotIn("ARROW_RASTER_AUDIT_MISMATCH", report.failures)
        self.assertTrue(report.passed, report.failures)
        self.assertEqual(report.arrow_raster.significant_components, 3)

    def test_native_gate_distinguishes_missing_and_small_subjects(self) -> None:
        validator = DeterministicNativeRenderValidator()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            blank_image, audit = _write_native_files(root, None)
            blank = validator.validate(blank_image, audit, _native_payload())
            small_image, audit = _write_native_files(root, (740, 740, 840, 840))
            small = validator.validate(small_image, audit, _native_payload())

        self.assertIn("SUBJECT_NOT_DETECTED", blank.failures)
        self.assertIn("SUBJECT_TOO_SMALL", small.failures)

    def test_native_gate_rejects_receiver_silhouette_even_when_image_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image, audit = _write_native_files(Path(folder), (250, 300, 1200, 1150))
            payload = _native_payload()
            payload["camera_catalog"]["fixed_123"]["position_direction_root"] = [0.0, 1.0, 0.0]
            report = DeterministicNativeRenderValidator().validate(image, audit, payload)

        self.assertIn("CAMERA_RECEIVER_SILHOUETTE", report.failures)

    def test_malformed_presentation_and_arrow_numbers_fail_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image, audit = _write_native_files(root, (250, 300, 1200, 1150))
            payload = _native_payload()
            payload["presentation"]["frame_gate"]["min_subject_span"] = "bad"
            broken_presentation = DeterministicNativeRenderValidator().validate(
                image, audit, payload
            )
            payload = _native_payload()
            audit_payload = json.loads(audit.read_text(encoding="utf-8"))
            audit_payload["arrows"][0]["exploded_root"][2] = "bad"
            audit.write_text(json.dumps(audit_payload), encoding="utf-8")
            broken_audit = DeterministicNativeRenderValidator().validate(
                image, audit, payload
            )

        self.assertIn("PRESENTATION_CONTRACT_INVALID", broken_presentation.failures)
        self.assertIn("TRANSLATION_AUDIT_INVALID", broken_audit.failures)

    def test_noncritical_context_may_clip_one_edge_when_arrow_stays_safe(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image, audit = _write_native_files(Path(folder), (0, 300, 1500, 1200))
            report = DeterministicNativeRenderValidator().validate(
                image, audit, _native_payload()
            )

        self.assertTrue(report.passed)
        self.assertEqual(report.composition.clipped_edges, ("left",))

    def test_large_but_off_center_activity_and_arrow_fail_hard_gates(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image, audit = _write_native_files(root, None)
            frame = Image.new("RGB", (1600, 1600), "white")
            draw = ImageDraw.Draw(frame)
            draw.rectangle((850, 350, 1599, 1250), fill=(80, 100, 120))
            draw.line((1200, 800, 1500, 800), fill=(0, 150, 0), width=8)
            frame.save(image)
            report = DeterministicNativeRenderValidator().validate(
                image, audit, _native_payload()
            )

        self.assertFalse(report.passed)
        self.assertIn("ACTIVITY_NOT_CENTERED", report.failures)
        self.assertIn("ARROW_NOT_CENTERED", report.failures)

    def test_native_selected_fit_uses_creo_selection_center_not_context_midpoint(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image, audit = _write_native_files(root, None)
            frame = Image.new("RGB", (1600, 1600), "white")
            draw = ImageDraw.Draw(frame)
            draw.rectangle((850, 350, 1599, 1250), fill=(80, 100, 120))
            draw.line((1200, 800, 1500, 800), fill=(0, 150, 0), width=8)
            frame.save(image)
            payload = _native_payload()
            payload["presentation"]["framing_profile"] = {
                "policy": "native_zoom_to_selected/v1"
            }
            payload["presentation"]["native_selected_fit"] = {
                "schema_version": "native-selected-fit/v1",
                "command": "ProCmdZoomIntoOutline",
                "selection_scope": "moving_occurrences/v1",
                "zoom_to_selected_level": 0.35,
                "level_policy": "cad_installation_envelope/v3",
                "max_commands_per_render": 1,
                "absolute_pan_zoom_forbidden": True,
            }
            report = DeterministicNativeRenderValidator().validate(
                image, audit, payload
            )

        self.assertTrue(report.passed, report.failures)

    def test_balanced_subject_arrow_midpoint_is_the_centering_hard_gate(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image, audit = _write_native_files(root, None)
            frame = Image.new("RGB", (1600, 1600), "white")
            draw = ImageDraw.Draw(frame)
            # The individual centres are deliberately outside the 120 px
            # limits, while their declared focus midpoint is exactly centred.
            draw.rectangle((360, 500, 1240, 1360), fill=(80, 100, 120))
            draw.line((650, 670, 950, 670), fill=(0, 150, 0), width=12)
            frame.save(image)

            report = DeterministicNativeRenderValidator().validate(
                image, audit, _native_payload()
            )

        self.assertTrue(report.passed, report.failures)

    def test_creo_lower_left_status_text_is_excluded_from_subject_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            clean, audit = _write_native_files(root, (400, 300, 1250, 1150))
            validator = DeterministicNativeRenderValidator()
            clean_report = validator.validate(clean, audit, _native_payload())

            with Image.open(clean) as source:
                noisy_frame = source.convert("RGB")
            draw = ImageDraw.Draw(noisy_frame)
            # Connected blocks mimic Creo's lower-left Simp Rep status label.
            draw.rectangle((8, 1320, 440, 1345), fill=(80, 80, 80))
            noisy = root / "native-with-status.jpg"
            noisy_frame.save(noisy)
            noisy_report = validator.validate(noisy, audit, _native_payload())

        self.assertEqual(
            noisy_report.composition.subject_bbox,
            clean_report.composition.subject_bbox,
        )
        self.assertEqual(
            noisy_report.composition.center_pixel,
            clean_report.composition.center_pixel,
        )


if __name__ == "__main__":
    unittest.main()
