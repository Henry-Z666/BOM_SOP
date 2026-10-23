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
            "focus_context": "installation_activity_bbox/v1",
            "framing_priority": "installation_activity/v1",
            "zoom_anchor": "installation_activity_center/v1",
            "native_selected_fit": {
                "schema_version": "native-selected-fit/v2",
                "command": "ProCmdZoomIntoOutline",
                "selection_scope": "moving_occurrences_and_receiver_surfaces/v1",
                "coordinate_adjustment": "none_native_surface_fit/v1",
                "zoom_to_selected_level": 0.70,
                "level_policy": "fixed_native_selection_margin/v1",
                "max_commands_per_render": 1,
                "manual_pan_zoom_forbidden": True,
            },
            "framing_profile": {
                "schema_version": "native-selected-framing-policy/v1",
                "policy": "native_receiver_surface_fit/v1",
                "selection_scope": "moving_occurrences_and_receiver_surfaces/v1",
                "on_failure": "question_single_frame/v1",
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

    def test_native_gate_rejects_legacy_native_viewport_contract(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image, audit = _write_native_files(root, None)
            frame = Image.new("RGB", (1600, 1600), "white")
            draw = ImageDraw.Draw(frame)
            draw.rectangle((650, 260, 1550, 1340), fill=(80, 100, 120))
            draw.line((675, 800, 925, 800), fill=(0, 150, 0), width=8)
            frame.save(image)

            report = DeterministicNativeRenderValidator().validate(
                image,
                audit,
                _native_payload(
                    presentation={
                        **_native_payload()["presentation"],
                        "schema_version": "native-viewport-presentation/v1",
                    }
                ),
            )

        self.assertIn("PRESENTATION_CONTRACT_INVALID", report.failures)
        self.assertIsNone(report.composition)

    def test_native_gate_rejects_rectangular_frame(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image, audit = _write_native_files(root, None)
            frame = Image.new("RGB", (1600, 1000), "white")
            draw = ImageDraw.Draw(frame)
            draw.rectangle((250, 150, 1350, 850), fill=(80, 100, 120))
            draw.line((675, 500, 925, 500), fill=(0, 150, 0), width=8)
            frame.save(image)
            presentation = {
                **_native_payload()["presentation"],
                "schema_version": "square-crop-presentation/v1",
                "final_frame": [1600, 1600],
                "source_policy": "native_viewport_overscan/v1",
                "crop_policy": "fixed_center_square/v1",
                "padding_forbidden": True,
                "stretching_forbidden": True,
            }

            report = DeterministicNativeRenderValidator().validate(
                image,
                audit,
                _native_payload(presentation=presentation),
            )

        self.assertIn("RENDER_FRAME_INVALID", report.failures)

    def test_native_gate_audits_each_grouped_occurrence_translation(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "native.jpg"
            frame = Image.new("RGB", (1600, 1600), "white")
            draw = ImageDraw.Draw(frame)
            draw.rectangle((250, 300, 1350, 1300), fill=(80, 100, 120))
            draw.line((550, 500, 550, 1100), fill=(0, 150, 0), width=8)
            draw.line((1050, 1100, 1050, 500), fill=(0, 150, 0), width=8)
            frame.save(image)
            audit = root / "native.arrow.json"
            audit.write_text(
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
                            },
                            {
                                "covered_occurrences": ["1/3"],
                                "anchor_source": "model_surface",
                                "complete_root": [20.0, 0.0, 0.0],
                                "exploded_root": [20.0, 0.0, -10.0],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            presentation = {
                **_native_payload()["presentation"],
                "schema_version": "square-crop-presentation/v1",
                "final_frame": [1600, 1600],
                "source_policy": "native_viewport_overscan/v1",
                "crop_policy": "fixed_center_square/v1",
                "padding_forbidden": True,
                "stretching_forbidden": True,
            }
            payload = _native_payload(
                moving_occurrences=["1/2", "1/3"],
                occurrence_translation_vectors_root={
                    "1/2": [0.0, 0.0, 10.0],
                    "1/3": [0.0, 0.0, -10.0],
                },
                presentation=presentation,
            )

            report = DeterministicNativeRenderValidator().validate(
                image, audit, payload
            )

        self.assertTrue(report.passed, report.failures)
        self.assertNotIn("TRANSLATION_AUDIT_INVALID", report.failures)

    def test_native_gate_rejects_missing_raster_arrow_for_one_moving_instance(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image, audit = _write_native_files(root, (250, 300, 1200, 1150))
            audit.write_text(
                json.dumps(
                    {
                        "schema_version": "arrow-projection/v1",
                        "policy": "same_cad_point/v1",
                        "status": "passed",
                        "arrows": [
                            {
                                "covered_occurrences": [occurrence],
                                "anchor_source": "model_surface",
                                "complete_root": [0.0, 0.0, 0.0],
                                "exploded_root": [0.0, 0.0, 10.0],
                            }
                            for occurrence in ("1/2", "1/3")
                        ],
                    }
                ),
                encoding="utf-8",
            )
            payload = _native_payload(moving_occurrences=["1/2", "1/3"])
            report = DeterministicNativeRenderValidator().validate(
                image, audit, payload
            )

        self.assertFalse(report.passed)
        self.assertIn("ARROW_RASTER_COUNT_MISMATCH", report.failures)

    def test_ready_visibility_contract_requires_a_matching_hashed_decision(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image, audit = _write_native_files(Path(folder), (250, 300, 1200, 1150))
            payload = _native_payload(
                camera_id="fixed_123",
                camera_visibility={
                    "schema_version": "camera-visibility-contract/v1",
                    "status": "ready",
                },
            )
            validator = DeterministicNativeRenderValidator(
                camera_visibility_enabled=True
            )
            missing = validator.validate(
                image, audit, payload
            )
            payload["camera_selection"] = _camera_selection("fixed_123")
            passed = validator.validate(
                image, audit, payload
            )
            payload["camera_selection"]["selected_camera_id"] = "fixed_456"
            mismatched = validator.validate(
                image, audit, payload
            )

        self.assertIn("CAMERA_VISIBILITY_AUDIT_INVALID", missing.failures)
        self.assertNotIn("CAMERA_VISIBILITY_AUDIT_INVALID", passed.failures)
        self.assertIn("CAMERA_VISIBILITY_AUDIT_INVALID", mismatched.failures)

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

    def test_receiver_surface_fit_still_enforces_final_raster_center(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image, audit = _write_native_files(root, None)
            frame = Image.new("RGB", (1600, 1600), "white")
            draw = ImageDraw.Draw(frame)
            draw.rectangle((850, 350, 1599, 1250), fill=(80, 100, 120))
            draw.line((1200, 800, 1500, 800), fill=(0, 150, 0), width=8)
            frame.save(image)
            payload = _native_payload()
            report = DeterministicNativeRenderValidator().validate(
                image, audit, payload
            )

        self.assertFalse(report.passed)
        self.assertIn("ACTIVITY_NOT_CENTERED", report.failures)
        self.assertIn("ARROW_NOT_CENTERED", report.failures)

    def test_offset_activity_passes_when_receiver_interface_has_safe_margin(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image, audit = _write_native_files(root, None)
            frame = Image.new("RGB", (1600, 1600), "white")
            draw = ImageDraw.Draw(frame)
            # The complete receiver is intentionally clipped on the right;
            # the moving-to-receiver arrow and its endpoint remain well inside.
            draw.rectangle((680, 220, 1599, 1379), fill=(80, 100, 120))
            draw.line((794, 788, 1130, 1371), fill=(0, 150, 0), width=8)
            frame.save(image)
            payload = _native_payload()
            payload["presentation"]["center_gate"].update(
                {
                    "max_activity_center_offset_pixels": 400,
                    "max_arrow_center_offset_pixels": 400,
                }
            )
            report = DeterministicNativeRenderValidator().validate(
                image, audit, payload
            )

        self.assertTrue(report.passed, report.failures)
        self.assertGreaterEqual(report.arrow_raster.border_margin_pixels, 40)
        self.assertEqual(report.composition.clipped_edges, ("right",))

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


def _camera_selection(selected_id: str) -> dict:
    digest = "sha256:" + "0" * 64
    return {
        "schema_version": "camera-selection-decision/v1",
        "status": "selected",
        "selected_camera_id": selected_id,
        "selection_policy": "eligible_worst_visibility_then_stable_id/v1",
        "options": [],
        "audits": [
            {
                "schema_version": "camera-visibility-audit/v1",
                "source": "creo-lossless-component-label-raster/v1",
                "camera_id": camera_id,
                "eligible": camera_id == selected_id,
                "failures": [] if camera_id == selected_id else ["MOVING_SET_OCCLUDED"],
                "isolated_sha256": digest,
                "staged_sha256": digest,
            }
            for camera_id in ("fixed_123", "fixed_456")
        ],
    }


if __name__ == "__main__":
    unittest.main()


class BackgroundArrowEvidenceTests(unittest.TestCase):
    def test_red_cad_background_is_not_counted_as_an_arrow(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);image,audit=_write_native_files(folder,(250,250,1350,1350))
            base=Image.open(image).convert('RGB');ImageDraw.Draw(base).rectangle((0,200,350,1400),fill=(255,0,0))
            background=Path(str(image)+'.background.jpg');base.save(background,quality=100)
            ImageDraw.Draw(base).line((675,800,925,800),fill=(230,10,10),width=12);base.save(image,quality=100)
            evidence=Path(str(image)+'.raster-evidence.json');evidence.write_text(json.dumps({'schema_version':'native-background-difference/v1','camera_and_stage_unchanged':True,'background_sha256':hashlib.sha256(background.read_bytes()).hexdigest(),'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest()}),encoding='utf-8')
            payload=_native_payload(arrow_color='red',arrow_evidence='native-background-difference/v1')
            payload['presentation']['center_gate']['max_activity_center_offset_pixels']=400
            payload['presentation']['center_gate']['max_arrow_center_offset_pixels']=400
            report=DeterministicNativeRenderValidator().validate(image,audit,payload)
            self.assertNotIn('ARROW_CLIPPED',report.failures)
            self.assertEqual(report.arrow_raster.significant_components,1)
            self.assertGreater(report.arrow_raster.border_margin_pixels,500)
            background.write_bytes(b'changed')
            report=DeterministicNativeRenderValidator().validate(image,audit,payload)
            self.assertIn('ARROW_BACKGROUND_EVIDENCE_INVALID',report.failures)


class CadRoiEvidenceTests(unittest.TestCase):
    def test_native_roi_rejects_missing_or_modified_calibration(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);image,audit=_write_native_files(folder,(250,250,1350,1350))
            payload=_native_payload(installation_view={'coordinate_adapter':'cad_roi_screen_transform/v1','roi_feature_span':10},stage_geometry_root={'moving_bounds':[{'min':[0,0,0],'max':[10,10,10]}]})
            report=DeterministicNativeRenderValidator().validate(image,audit,payload)
            self.assertIn('CAD_ROI_EVIDENCE_INVALID',report.failures)
            snapshots=[]
            for name in ['a','pan']:
                file=folder/(name+'.jpg');Image.new('RGB',(10,10),'white').save(file)
                snapshots.append({'file':str(file),'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
            calibration_file=Path(str(image)+'.calibration.json')
            calibration_file.write_text(json.dumps({'schema_version':'native-viewport-calibration/v1','profile':[1,-1,100,-100,0,0,1600,1600],'residual':[0,0],'snapshots':snapshots}),encoding='utf-8')
            roi_file=Path(str(image)+'.roi.json')
            roi_file.write_text(json.dumps({'schema_version':'cad-roi-screen-transform/v1','temporary_markers_removed':True,'zoom':2,'pan':[0,0],'feature_span':10,'calibration_sha256':hashlib.sha256(calibration_file.read_bytes()).hexdigest()}),encoding='utf-8')
            report=DeterministicNativeRenderValidator().validate(image,audit,payload)
            self.assertNotIn('CAD_ROI_EVIDENCE_INVALID',report.failures)
            (folder/'pan.jpg').write_bytes(b'changed')
            report=DeterministicNativeRenderValidator().validate(image,audit,payload)
            self.assertIn('CAD_ROI_EVIDENCE_INVALID',report.failures)


class LocatorEvidenceTests(unittest.TestCase):
    def test_group_cover_and_native_text_frame_are_required(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            image=Path(directory)/'locator.jpg'
            notes={'purpose':'spatial_group_indices_from_cad','labels':[{'text':'1','occurrence_ids':['a']},{'text':'2','occurrence_ids':['b']}]}
            notes['contract_hash']='sha256:'+hashlib.sha256(json.dumps(notes,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()
            coverage={'schema_version':'installation-view-coverage/v1','role':'location','detail_groups':[['a'],['b']],
                      'operation_occurrences':['a','b'],'display_occurrences':['a','b'],'positioning_context_status':'available'}
            payload={'image_coverage':coverage,'installation_notes':notes}
            labels=[{'text':str(i),'connection_id':'spatial-group-'+str(i),'occurrence_id':o,'text_box_camera':[i*100,100,40,60]} for i,o in enumerate(['a','b'],1)]
            audit={'schema_version':'installation-note-audit/v1','renderer':'creo_display_list/v1','contract_hash':notes['contract_hash'],'overlap_check':'passed','labels':labels}
            note_file=Path(str(image)+'.note.json');note_file.write_text(json.dumps(audit),encoding='utf-8')
            Path(str(image)+'.roi.json').write_text(json.dumps({'camera_plane_to_final_pixels':[1,-1,400,900]}),encoding='utf-8')
            failures=[];validator=DeterministicNativeRenderValidator();validator._validate_locator_numbers(image,payload,failures)
            self.assertEqual(failures,[])
            notes.update(anchor_state='authoritative_complete',layout_policy='ordered-perimeter-lane/v1')
            notes.pop('contract_hash')
            notes['contract_hash']='sha256:'+hashlib.sha256(json.dumps(notes,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()
            audit['contract_hash']=notes['contract_hash']
            payload['installation_view']={'composition_policy':'single-image-balanced-fit/v15'}
            payload['arrow_anchors']=[{'occurrence_id':o,'complete_point_root':[i,2,3]} for i,o in enumerate(['a','b'])]
            for i,label in enumerate(labels):label.update(anchor_source='native_complete_installation_anchor',target_root=[i,2,3])
            note_file.write_text(json.dumps(audit),encoding='utf-8')
            failures=[];validator._validate_locator_numbers(image,payload,failures);self.assertEqual(failures,[])
            labels[0]['target_root']=[9,9,9];note_file.write_text(json.dumps(audit),encoding='utf-8')
            failures=[];validator._validate_locator_numbers(image,payload,failures);self.assertIn('LOCATOR_NOTE_EVIDENCE_INVALID',failures)
            labels[0]['target_root']=[0,2,3]
            labels[0]['text_box_camera'][0]=-500;note_file.write_text(json.dumps(audit),encoding='utf-8')
            failures=[];validator._validate_locator_numbers(image,payload,failures);self.assertIn('LOCATOR_LABEL_CLIPPED',failures)
            coverage['detail_groups']=[['a'],['a']]
            failures=[];validator._validate_locator_numbers(image,payload,failures);self.assertIn('LOCATOR_NOTE_EVIDENCE_INVALID',failures)


class MovingOccurrenceVisibilityTests(unittest.TestCase):
    def test_hidden_moving_body_cannot_pass_on_receiver_and_arrow_pixels(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            image=Path(directory)/'moving.jpg';reference_file=Path(str(image)+'.background.jpg');without_file=Path(directory)/'without.jpg'
            reference=Image.new('RGB',(1600,1600),(230,230,230));ImageDraw.Draw(reference).rectangle((500,500,1100,1100),fill=(100,100,100));reference.save(without_file,quality=100)
            ImageDraw.Draw(reference).rectangle((700,700,900,900),fill=(0,100,200));reference.save(reference_file,quality=100)
            def write_evidence():
                Path(str(image)+'.moving-visibility.json').write_text(json.dumps({'schema_version':'native-occurrence-difference/v1','camera_and_other_occurrences_unchanged':True,'poses_restored':True,'reference_sha256':hashlib.sha256(reference_file.read_bytes()).hexdigest(),'entries':[{'occurrence_id':'m','file':str(without_file),'sha256':hashlib.sha256(without_file.read_bytes()).hexdigest()}]}),encoding='utf-8')
            write_evidence();validator=DeterministicNativeRenderValidator();failures=[]
            result=validator._validate_moving_visibility(image,{'moving_occurrences':['m']},failures)
            self.assertEqual(failures,[]);self.assertGreaterEqual(result[0]['largest_visible_span_pixels'],200)
            without_file.write_bytes(reference_file.read_bytes());write_evidence();failures=[]
            result=validator._validate_moving_visibility(image,{'moving_occurrences':['m']},failures)
            self.assertIn('MOVING_OCCURRENCE_HIDDEN',failures);self.assertEqual(result[0]['visible_pixels_estimate'],0)
            without_file.write_bytes(b'changed');failures=[]
            validator._validate_moving_visibility(image,{'moving_occurrences':['m']},failures)
            self.assertIn('MOVING_VISIBILITY_EVIDENCE_INVALID',failures)
