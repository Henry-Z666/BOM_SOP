from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image

from sop_pipeline.camera_visibility import CAMERA_VISIBILITY_AUDIT_ENABLED
from .torque_validation import validate_torque_audit
from .installation_view import interface_view


def validate_source_visibility_reset(image_file: Path, payload: dict) -> None:
    validate_receiver_ghosting(image_file,payload)
    if payload.get('source_visibility_normalization') is None:return
    import hashlib
    if payload['source_visibility_normalization']!='native-source-visibility-reset/v1':raise ValueError('Unknown source visibility normalization')
    evidence=json.loads(Path(str(image_file)+'.visibility-reset.json').read_text(encoding='utf8'))
    if evidence.get('schema_version')!='native-source-visibility-reset/v1' or evidence.get('command')!='ProCmdViewShowAll':raise ValueError('Source manual hiding was not cleared natively')
    if evidence.get('auxiliary_layer_retained') is not True or not isinstance(evidence.get('stage_exclusion_count'),int) or evidence['stage_exclusion_count']<0:raise ValueError('Stage and auxiliary evidence missing')
    if evidence.get('image_sha256')!=hashlib.sha256(image_file.read_bytes()).hexdigest():raise ValueError('Source visibility evidence belongs to different image bytes')


def validate_receiver_ghosting(image_file: Path, payload: dict) -> None:
    contract=payload.get('receiver_ghosting')
    if contract is None:return
    import hashlib
    paths=set(contract.get('occurrence_ids',[]))
    if contract.get('schema_version')!='native-receiver-ghost/v1' or contract.get('style')!='native_transparent' or not paths or not paths<=set(payload['receiver_occurrences']):raise ValueError('Invalid native transparent receiver scope')
    if any(o==r or o.startswith(r+'/') for o in payload['moving_occurrences'] for r in paths):raise ValueError('Moving geometry cannot be transparent')
    proof=json.loads(Path(str(image_file)+'.ghost.json').read_text(encoding='utf8'))
    if proof.get('reset_strategy')=='discarded_assembly_display_models/v1':
        expected=payload['authoritative_assembly']['sha256'].removeprefix('sha256:')
        models=proof.get('discarded_models',[])
        if proof.get('schema_version')!='native-receiver-ghost/v1' or set(proof.get('occurrence_ids',[]))!=paths or proof.get('command')!='ProCmdViewTranspShaded' or proof.get('display_state_discarded') is not True or proof.get('poses_restored') is not True or proof.get('assembly_count_after')!=0 or not models or not all(isinstance(m,str) and m.lower().endswith('.asm') for m in models):raise ValueError('Transient assembly display state was not discarded')
        if proof.get('source_assembly_sha256_before')!=expected or proof.get('source_assembly_sha256_after')!=expected or hashlib.sha256(Path(proof['source_assembly_file']).read_bytes()).hexdigest()!=expected:raise ValueError('Disposable appearance rendering changed source assembly')
        if proof.get('image_sha256')!=hashlib.sha256(image_file.read_bytes()).hexdigest():raise ValueError('Disposable appearance proof belongs to different image bytes')
        return
    if proof.get('schema_version')!='native-receiver-ghost/v1' or set(proof.get('occurrence_ids',[]))!=paths or proof.get('command')!='ProCmdViewTranspShaded' or proof.get('restore_command') not in ('ProCmdViewShaded','ProCmdViewShadedEdges','ProCmdViewTranspShaded','ProCmdViewSetDefStyle') or proof.get('appearance_restored') is not True or proof.get('restored_pixels_identical') is not True:raise ValueError('Native appearance restoration proof missing')
    for suffix,key in (('', 'image_sha256'),('.appearance-before.bmp','before_sha256'),('.appearance-restored.bmp','restored_sha256')):
        if hashlib.sha256(Path(str(image_file)+suffix).read_bytes()).hexdigest()!=proof.get(key):raise ValueError('Native appearance proof belongs to different raster bytes')
    with Image.open(str(image_file)+'.appearance-before.bmp') as before,Image.open(str(image_file)+'.appearance-restored.bmp') as after:
        if before.size!=after.size or not np.array_equal(np.asarray(before.convert('RGB')),np.asarray(after.convert('RGB'))):raise ValueError('Native appearance was not restored to original pixels')


def validate_detail_roi_fraction(payload: dict, roi: dict) -> None:
    """Native enlargement stays bounded and must match recorded CAD evidence."""
    requested = payload.get('installation_view', {}).get('roi_frame_fraction')
    if requested is None:
        return  # Legacy renders retain their native 0.65 default.
    value = float(requested)
    recorded = float(roi['frame_fraction'])
    if payload.get('image_coverage', {}).get('role') != 'detail':
        raise ValueError('Enlargement is only supported for detail views')
    if not math.isfinite(value) or not .5 <= value <= .9 or not math.isclose(value, recorded, abs_tol=1.e-9):
        raise ValueError('Native detail ROI fraction differs from contract')


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


@dataclass(frozen=True)
class RasterCompositionMetrics:
    background_rgb: tuple[int, int, int]
    subject_bbox: tuple[int, int, int, int] | None
    significant_components: int
    foreground_pixels: int
    width_fraction: float
    height_fraction: float
    max_span_fraction: float
    border_margin_pixels: int | None
    clipped_edges: tuple[str, ...]
    center_pixel: tuple[float, float] | None
    center_offset_pixels: float | None


@dataclass(frozen=True)
class ArrowRasterMetrics:
    bbox: tuple[int, int, int, int] | None
    pixels: int
    significant_components: int
    max_span_pixels: int
    border_margin_pixels: int | None
    center_pixel: tuple[float, float] | None
    center_offset_pixels: float | None


@dataclass(frozen=True)
class NativeRenderGateReport:
    schema_version: str
    passed: bool
    failures: tuple[str, ...]
    composition: RasterCompositionMetrics | None
    arrow_raster: ArrowRasterMetrics | None
    moving_visibility: tuple[dict[str, Any], ...] = ()


PRESENTATION_FAILURES = frozenset(
    {
        "SUBJECT_NOT_DETECTED",
        "SUBJECT_TOO_SMALL",
        "SUBJECT_TOO_LARGE",
        "SUBJECT_CLIPPED",
        "EXCESSIVE_CONTEXT_CLIPPING",
        "ARROW_NOT_VISIBLE",
        "ARROW_TOO_SMALL",
        "ARROW_CLIPPED",
        "ACTIVITY_NOT_CENTERED",
        "ARROW_NOT_CENTERED",
        "LETTERBOXED_VIEWPORT",
        "MOVING_OCCURRENCE_HIDDEN",
        "MOVING_OCCURRENCE_TOO_SMALL",
    }
)


class DeterministicNativeRenderValidator:
    """Validate a Creo-native image and audit through one publication seam."""

    def __init__(
        self,
        *,
        vector_tolerance: float = 1.0e-5,
        camera_visibility_enabled: bool = CAMERA_VISIBILITY_AUDIT_ENABLED,
    ) -> None:
        self.vector_tolerance = vector_tolerance
        self.camera_visibility_enabled = bool(camera_visibility_enabled)

    def validate(
        self,
        image_file: Path,
        audit_file: Path,
        task_payload: dict[str, Any],
        *,
        variant_index: int = 0,
    ) -> NativeRenderGateReport:
        failures: list[str] = []
        if task_payload.get('terminal_stage_visibility'):
            import hashlib
            try:
                stage = json.loads(image_file.with_suffix('.framing.json').read_text(encoding='utf8'))
                if (stage.get('terminal_stage_visibility') != task_payload['terminal_stage_visibility']
                    or set(stage['visible_occurrences']) != set(task_payload['visible_occurrences'])
                    or set(stage['excluded_occurrences']) != set(task_payload.get('bom_visibility_exclusions', []))
                    or stage['rendered_image_sha256'] != hashlib.sha256(image_file.read_bytes()).hexdigest()):
                    raise ValueError('Changed terminal visibility or image')
            except (OSError, KeyError, ValueError, TypeError):
                failures.append('TERMINAL_STAGE_RENDER_EVIDENCE_INVALID')
        expected_arrow_components = self._validate_arrow_audit(
            audit_file, task_payload, failures
        )
        composition, arrow_raster = self._validate_image(
            image_file,
            task_payload,
            failures,
            expected_arrow_components=expected_arrow_components,
        )
        moving_visibility=[]
        if task_payload.get('installation_view',{}).get('composition_policy') in ('single-image-balanced-fit/v14','single-image-balanced-fit/v15') and task_payload.get('image_coverage',{}).get('role')!='location' and task_payload.get('moving_visibility_evidence')!='native-occurrence-difference/v1':
            failures.append('MOVING_VISIBILITY_EVIDENCE_INVALID')
        if task_payload.get('moving_visibility_evidence')=='native-occurrence-difference/v1':
            moving_visibility=self._validate_moving_visibility(image_file,task_payload,failures)
        self._validate_camera(task_payload, variant_index, failures)
        self._validate_camera_visibility(task_payload, variant_index, failures)
        failures.extend(validate_torque_audit(image_file, task_payload))
        from .material_annotations import validate_material_audit
        failures.extend(validate_material_audit(image_file,task_payload))
        unique = tuple(dict.fromkeys(failures))
        return NativeRenderGateReport(
            schema_version="native-render-gate-report/v1",
            passed=not unique,
            failures=unique,
            composition=composition,
            arrow_raster=arrow_raster,
            moving_visibility=tuple(moving_visibility),
        )

    def _validate_image(
        self,
        image_file: Path,
        payload: dict[str, Any],
        failures: list[str],
        *,
        expected_arrow_components: int | None,
    ) -> tuple[RasterCompositionMetrics | None, ArrowRasterMetrics | None]:
        if not image_file.is_file() or image_file.stat().st_size == 0:
            failures.append("RENDER_OUTPUT_MISSING")
            return None, None
        try:validate_source_visibility_reset(image_file,payload)
        except (OSError,ValueError,KeyError,TypeError):
            failures.append('NATIVE_SOURCE_VISIBILITY_RESET_INVALID')
            return None,None
        try:
            with Image.open(image_file) as image:
                rgb = image.convert("RGB")
                width, height = rgb.size
                if width != 1600 or height != 1600:
                    failures.append("RENDER_FRAME_INVALID")
                    return None, None
                pixels = np.asarray(rgb)
        except (OSError, ValueError):
            failures.append("RENDER_FRAME_INVALID")
            return None, None

        adapter=payload.get('installation_view',{}).get('coordinate_adapter')
        if adapter in ('cad_roi_screen_transform/v1','cad_location_screen_transform/v1'):
            import hashlib
            try:
                roi=json.loads(Path(str(image_file)+'.roi.json').read_text(encoding='utf-8'))
                calibration_file=Path(str(image_file)+'.calibration.json')
                calibration=json.loads(calibration_file.read_text(encoding='utf-8'))
                if roi.get('schema_version')!='cad-roi-screen-transform/v1' or roi.get('temporary_markers_removed') is not True:raise ValueError('Invalid native ROI evidence')
                if hashlib.sha256(calibration_file.read_bytes()).hexdigest()!=roi['calibration_sha256']:raise ValueError('Changed viewport calibration')
                calibration_version=calibration.get('schema_version')
                expected_profile={'native-viewport-calibration/v1':8,'native-viewport-calibration/v2':10}.get(calibration_version)
                if expected_profile is None or len(calibration['profile'])!=expected_profile:raise ValueError('Invalid viewport profile')
                if not all(math.isfinite(float(x)) for x in calibration['profile']) or not all(math.isfinite(float(x)) and abs(float(x))<=8 for x in calibration['residual']):raise ValueError('Invalid calibration measurements')
                if len(calibration['snapshots'])!=(3 if calibration_version=='native-viewport-calibration/v2' else 2):raise ValueError('Missing calibration snapshots')
                for snapshot in calibration['snapshots']:
                    if hashlib.sha256(Path(snapshot['file']).read_bytes()).hexdigest()!=snapshot['sha256']:raise ValueError('Changed diagnostic snapshot')
                if not math.isfinite(float(roi['zoom'])) or float(roi['zoom'])<=0 or len(roi['pan'])!=2 or not all(math.isfinite(float(x)) for x in roi['pan']):raise ValueError('Invalid ROI transform')
                if abs(float(roi['feature_span'])-float(payload['installation_view']['roi_feature_span']))>1.e-7:raise ValueError('Changed ROI feature scale')
                validate_detail_roi_fraction(payload, roi)
                from .installation_view import installation_feature_span
                if not math.isclose(float(roi['feature_span']),installation_feature_span(payload),rel_tol=1.e-8,abs_tol=1.e-9):raise ValueError('ROI span does not match CAD bounds')
            except (OSError,ValueError,KeyError,TypeError,OverflowError):
                failures.append('CAD_ROI_EVIDENCE_INVALID')
                return None,None

        arrow_pixels=pixels
        if payload.get('arrow_evidence')=='native-background-difference/v1':
            import hashlib
            try:
                background_file=Path(str(image_file)+'.background.jpg')
                evidence=json.loads(Path(str(image_file)+'.raster-evidence.json').read_text(encoding='utf-8'))
                if evidence.get('schema_version')!='native-background-difference/v1' or evidence.get('camera_and_stage_unchanged') is not True:raise ValueError('Invalid native raster evidence')
                if hashlib.sha256(background_file.read_bytes()).hexdigest()!=evidence['background_sha256'] or hashlib.sha256(image_file.read_bytes()).hexdigest()!=evidence['image_sha256']:raise ValueError('Changed native raster evidence')
                with Image.open(background_file) as background:base=np.asarray(background.convert('RGB'))
                if base.shape!=pixels.shape:raise ValueError('Mismatched background frame')
                arrow_pixels=_native_arrow_background_difference(pixels,base,color=str(payload.get('arrow_color','green')))
            except (OSError,ValueError,KeyError,TypeError):
                failures.append('ARROW_BACKGROUND_EVIDENCE_INVALID')
                return None,None
        presentation = payload.get("presentation", {})
        presentation_schema = (
            str(presentation.get("schema_version", ""))
            if isinstance(presentation, dict)
            else ""
        )
        if (
            not isinstance(presentation, dict)
            or presentation_schema
            not in {
                "fixed-frame-presentation/v1",
                "square-crop-presentation/v1",
            }
            or presentation.get("focus_context") != "installation_activity_bbox/v1"
            or presentation.get("framing_priority") != "installation_activity/v1"
            or presentation.get("zoom_anchor")
            != "installation_activity_center/v1"
        ):
            failures.append("PRESENTATION_CONTRACT_INVALID")
            return None, None
        framing_profile = presentation.get("framing_profile", {})
        selected_fit = presentation.get("native_selected_fit")
        try:
            selected_fit_level = float(selected_fit.get("zoom_to_selected_level", 0.0))
        except (AttributeError, TypeError, ValueError, OverflowError):
            selected_fit_level = 0.0
        if (
            not isinstance(framing_profile, dict)
            or framing_profile.get("schema_version")
            != "native-selected-framing-policy/v1"
            or framing_profile.get("policy")
            != "native_receiver_surface_fit/v1"
            or framing_profile.get("selection_scope")
            != "moving_occurrences_and_receiver_surfaces/v1"
            or not isinstance(selected_fit, dict)
            or selected_fit.get("schema_version") != "native-selected-fit/v2"
            or selected_fit.get("command") != "ProCmdZoomIntoOutline"
            or selected_fit.get("selection_scope")
            != "moving_occurrences_and_receiver_surfaces/v1"
            or selected_fit.get("coordinate_adjustment")
            != "none_native_surface_fit/v1"
            or selected_fit.get("level_policy")
            != "fixed_native_selection_margin/v1"
            or selected_fit.get("max_commands_per_render") != 1
            or selected_fit.get("manual_pan_zoom_forbidden") is not True
            or not (any(
                math.isclose(selected_fit_level, allowed, abs_tol=1.0e-9)
                for allowed in (0.70, 0.80, 0.85, 0.95, 1.45, 1.50)
            ) or (payload.get('installation_view',{}).get('composition_policy') in ('single-image-context-margin/v1','single-image-anchor-fit/v2','single-image-balanced-fit/v3','single-image-balanced-fit/v4','single-image-balanced-fit/v5','single-image-balanced-fit/v6','single-image-balanced-fit/v7','single-image-balanced-fit/v8','single-image-balanced-fit/v9','single-image-balanced-fit/v10','single-image-balanced-fit/v11','single-image-balanced-fit/v12','single-image-balanced-fit/v13','single-image-balanced-fit/v14','single-image-balanced-fit/v15')
                  and math.isclose(selected_fit_level, interface_view(payload)['installation_view']['selected_fit_level'],abs_tol=1e-6)))
        ):
            failures.append("PRESENTATION_CONTRACT_INVALID")
            return None, None
        if presentation_schema == "square-crop-presentation/v1" and (
            presentation.get("final_frame") != [1600, 1600]
            or presentation.get("source_policy") != "native_viewport_overscan/v1"
            or presentation.get("crop_policy") != "fixed_center_square/v1"
            or presentation.get("padding_forbidden") is not True
            or presentation.get("stretching_forbidden") is not True
        ):
            failures.append("PRESENTATION_CONTRACT_INVALID")
            return None, None
        centering = presentation.get("center_gate")
        if (
            not isinstance(centering, dict)
            or centering.get("schema_version")
            != "native-composition-center-gate/v1"
            or (
                centering.get("target_policy") != "native_frame_center/v1"
                and centering.get("target_pixel") != [800, 800]
            )
        ):
            failures.append("PRESENTATION_CONTRACT_INVALID")
            return None, None
        try:
            target_pixel = (width // 2, height // 2)
            max_activity_center_offset = float(
                centering["max_activity_center_offset_pixels"]
            )
            max_arrow_center_offset = float(
                centering["max_arrow_center_offset_pixels"]
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            failures.append("PRESENTATION_CONTRACT_INVALID")
            return None, None
        if (
            len(target_pixel) != 2
            or not 1.0 <= max_activity_center_offset <= 400.0
            or not 1.0 <= max_arrow_center_offset <= 400.0
        ):
            failures.append("PRESENTATION_CONTRACT_INVALID")
            return None, None
        gate = presentation.get("frame_gate", {})
        if not isinstance(gate, dict):
            failures.append("PRESENTATION_CONTRACT_INVALID")
            return None, None
        if gate.get("schema_version") != "raster-composition-gate/v2":
            failures.append("PRESENTATION_CONTRACT_INVALID")
            return None, None
        try:
            threshold = int(gate["foreground_delta"])
            min_component_pixels = int(gate["min_component_pixels"])
            component_downsample = int(gate["component_downsample"])
            min_span = float(gate["min_subject_span"])
            max_span = float(gate["max_subject_span"])
            max_clipped_edges = int(gate["max_clipped_edges"])
            arrow_green_delta = int(gate["arrow_green_delta"])
            min_arrow_pixels = int(gate["min_arrow_pixels"])
            min_arrow_span = int(gate["min_arrow_span_pixels"])
            min_arrow_margin = int(gate["min_arrow_border_margin_pixels"])
            ignored_regions = _ignored_regions(
                gate["ignored_regions"], width=width, height=height
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            failures.append("PRESENTATION_CONTRACT_INVALID")
            return None, None
        if not (
            1 <= threshold <= 255
            and min_component_pixels >= 1
            and 1 <= component_downsample <= 8
            and 0.0 < min_span < max_span <= 1.0
            and 0 <= max_clipped_edges <= (4 if payload.get('installation_view', {}).get('allow_remote_body_clipping') else 2)
            and 1 <= arrow_green_delta <= 255
            and min_arrow_pixels >= 1
            and min_arrow_span >= 1
            and min_arrow_margin >= 0
        ):
            failures.append("PRESENTATION_CONTRACT_INVALID")
            return None, None
        metrics = _composition_metrics(
            pixels,
            foreground_delta=threshold,
            min_component_pixels=min_component_pixels,
            component_downsample=component_downsample,
            ignored_regions=ignored_regions,
            target_pixel=target_pixel,
        )
        if metrics.subject_bbox is None:
            failures.append("SUBJECT_NOT_DETECTED")
            return metrics, _arrow_raster_metrics(
                arrow_pixels,
                green_delta=arrow_green_delta,
                target_pixel=target_pixel,
                min_component_pixels=max(16, min_arrow_pixels // 4),
                color=str(payload.get('arrow_color', 'green')),
            )
        if metrics.max_span_fraction < min_span:
            failures.append("SUBJECT_TOO_SMALL")
        if metrics.max_span_fraction > max_span:
            failures.append("SUBJECT_TOO_LARGE")
        if len(metrics.clipped_edges) > max_clipped_edges:
            failures.append("EXCESSIVE_CONTEXT_CLIPPING")
        arrow_metrics = _arrow_raster_metrics(
            arrow_pixels,
            green_delta=arrow_green_delta,
            target_pixel=target_pixel,
            min_component_pixels=max(16, min_arrow_pixels // 4),
            color=str(payload.get('arrow_color', 'green')),
        )
        locator=adapter=='cad_location_screen_transform/v1'
        if locator:self._validate_locator_numbers(image_file,payload,failures)
        if not locator and arrow_metrics.bbox is None:
            failures.append("ARROW_NOT_VISIBLE")
        elif not locator:
            if (
                arrow_metrics.pixels < min_arrow_pixels
                or arrow_metrics.max_span_pixels < min_arrow_span
            ):
                failures.append("ARROW_TOO_SMALL")
            if (
                arrow_metrics.border_margin_pixels is not None
                and arrow_metrics.border_margin_pixels < min_arrow_margin
            ):
                failures.append("ARROW_CLIPPED")
            # One Creo display-list arrow is a connected raster component.
            # Extra components can be harmless anti-alias/head fragments, but
            # fewer components than the validated arrow audit means at least
            # one moving instance or receiver endpoint disappeared from the
            # delivered frame.
            if (
                expected_arrow_components is not None
                and arrow_metrics.significant_components
                < expected_arrow_components
            ):
                failures.append("ARROW_RASTER_COUNT_MISMATCH")
        # The compiled contract defines the installation focus as the midpoint
        # of the staged subject and the native arrow.  Applying both limits to
        # the two centres independently makes the gate mathematically
        # impossible whenever their separation exceeds the sum of the limits,
        # even when their declared midpoint is exactly at the target. Validate
        # the installation focus on the final native-selected-fit raster;
        # arrow visibility, size and clipping remain independent hard gates.
        focus_center = metrics.center_pixel
        if not locator and focus_center is not None and arrow_metrics.center_pixel is not None:
            focus_center = (
                (focus_center[0] + arrow_metrics.center_pixel[0]) / 2.0,
                (focus_center[1] + arrow_metrics.center_pixel[1]) / 2.0,
            )
        if focus_center is not None:
            focus_offset = math.hypot(
                focus_center[0] - target_pixel[0],
                focus_center[1] - target_pixel[1],
            )
            if focus_offset > max_activity_center_offset:
                failures.append("ACTIVITY_NOT_CENTERED")
            if (
                not locator and arrow_metrics.center_pixel is not None
                and focus_offset > max_arrow_center_offset
            ):
                failures.append("ARROW_NOT_CENTERED")
        return metrics, arrow_metrics

    def _validate_moving_visibility(self,image_file: Path,payload: dict,failures: list[str]) -> list[dict]:
        import hashlib
        result=[]
        try:
            evidence=json.loads(Path(str(image_file)+'.moving-visibility.json').read_text(encoding='utf-8'))
            if evidence.get('schema_version')!='native-occurrence-difference/v1' or evidence.get('camera_and_other_occurrences_unchanged') is not True or evidence.get('poses_restored') is not True:raise ValueError('Invalid occurrence visibility evidence')
            reference_file=Path(str(image_file)+'.background.jpg')
            if hashlib.sha256(reference_file.read_bytes()).hexdigest()!=evidence['reference_sha256']:raise ValueError('Changed occurrence reference')
            with Image.open(reference_file) as image:reference=np.asarray(image.convert('RGB'))
            identities=[e['occurrence_id'] for e in evidence['entries']]
            if len(identities)!=len(set(identities)) or sorted(identities)!=sorted(payload['moving_occurrences']):raise ValueError('Incomplete occurrence visibility cover')
            for entry in evidence['entries']:
                file=Path(entry['file'])
                if hashlib.sha256(file.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('Changed occurrence snapshot')
                with Image.open(file) as image:without=np.asarray(image.convert('RGB'))
                if without.shape!=reference.shape or reference.shape!=(1600,1600,3):raise ValueError('Invalid occurrence snapshot frame')
                difference=np.max(np.abs(reference.astype(np.int16)-without.astype(np.int16)),axis=2)>24
                components=_significant_components(difference[::4,::4],8)
                pixels=sum(c[0]*16 for c in components)
                span=max((max(c[3]-c[1]+1,c[4]-c[2]+1)*4 for c in components),default=0)
                result.append({'occurrence_id':entry['occurrence_id'],'visible_pixels_estimate':pixels,'largest_visible_span_pixels':span,'min_pixels':256,'min_span_pixels':160})
                if pixels<256:failures.append('MOVING_OCCURRENCE_HIDDEN')
                elif span<160:failures.append('MOVING_OCCURRENCE_TOO_SMALL')
        except (OSError,ValueError,KeyError,TypeError,OverflowError):
            failures.append('MOVING_VISIBILITY_EVIDENCE_INVALID')
        return result

    def _validate_locator_numbers(self,image_file: Path,payload: dict,failures: list[str]) -> None:
        """A locator proves numbered group positions; detail images prove installation arrows."""
        import hashlib
        try:
            coverage=payload['image_coverage'];notes=payload['installation_notes']
            if coverage.get('schema_version')!='installation-view-coverage/v1' or coverage.get('role')!='location':raise ValueError('Invalid locator role')
            if coverage.get('positioning_context_status')!='available':
                failures.append('LOCATOR_CONTEXT_UNAVAILABLE')
            groups=coverage['detail_groups'];operation=coverage['operation_occurrences']
            flattened=[o for group in groups for o in group]
            if not groups or any(not group for group in groups) or len(flattened)!=len(set(flattened)) or sorted(flattened)!=sorted(operation) or sorted(coverage['display_occurrences'])!=sorted(operation):raise ValueError('Incomplete group cover')
            if notes.get('purpose')!='spatial_group_indices_from_cad':raise ValueError('Locator requires native group numbers')
            canonical=dict(notes);canonical.pop('contract_hash',None)
            if 'sha256:'+hashlib.sha256(json.dumps(canonical,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()!=notes['contract_hash']:raise ValueError('Changed note contract')
            audit=json.loads(Path(str(image_file)+'.note.json').read_text(encoding='utf-8'))
            if audit.get('schema_version')!='installation-note-audit/v1' or audit.get('renderer')!='creo_display_list/v1' or audit.get('contract_hash')!=notes['contract_hash'] or audit.get('overlap_check')!='passed':raise ValueError('Invalid native note evidence')
            if len(audit['labels'])!=len(groups) or len(notes['labels'])!=len(groups):raise ValueError('Missing group number')
            roi=json.loads(Path(str(image_file)+'.roi.json').read_text(encoding='utf-8'))
            transform=roi['camera_plane_to_final_pixels']
            if len(transform)!=4 or not all(math.isfinite(float(x)) for x in transform):raise ValueError('Invalid final pixel mapping')
            sx,sy,ox,oy=transform
            if sx<=0 or sy>=0:raise ValueError('Invalid camera pixel axes')
            for index,(group,label,expected) in enumerate(zip(groups,audit['labels'],notes['labels']),1):
                if label['text']!=('位置 '+str(index) if notes.get('number_kind')=='position' else str(index)) or label['connection_id']!='spatial-group-'+str(index) or label['occurrence_id']!=group[0] or expected['occurrence_ids']!=[group[0]] or expected['text']!=label['text']:raise ValueError('Group number identity mismatch')
                if payload.get('installation_view',{}).get('composition_policy')=='single-image-balanced-fit/v15':
                    anchors={a['occurrence_id']:a['complete_point_root'] for a in payload['arrow_anchors']}
                    if notes.get('anchor_state')!='authoritative_complete' or notes.get('layout_policy')!='ordered-perimeter-lane/v1' or label.get('anchor_source')!='native_complete_installation_anchor' or not np.allclose(label['target_root'],anchors[group[0]],atol=1e-6,rtol=0):raise ValueError('Locator anchor differs from complete CAD position')
                x,y,w,h=label['text_box_camera']
                if not all(math.isfinite(float(v)) for v in (x,y,w,h)) or w<=0 or h<=0:raise ValueError('Invalid native text rectangle')
                bounds=sorted([sx*x+ox,sx*(x+w)+ox])+sorted([sy*y+oy,sy*(y+h)+oy])
                if min(bounds)<16 or max(bounds)>1584:failures.append('LOCATOR_LABEL_CLIPPED')
                if abs(sx*w)<24 or abs(sy*h)<24:failures.append('LOCATOR_LABEL_TOO_SMALL')
        except (OSError,ValueError,KeyError,TypeError,OverflowError):
            failures.append('LOCATOR_NOTE_EVIDENCE_INVALID')

    def _validate_camera(
        self,
        payload: dict[str, Any],
        variant_index: int,
        failures: list[str],
    ) -> None:
        camera = _camera_variant(payload, variant_index)
        camera_id = str(camera.get("camera_id") or camera.get("id") or "")
        local_view = payload.get('installation_view', {})
        local_camera = local_view.get('schema_version') == 'installation-interface-view/v1' and camera_id == 'interface_oblique'
        if camera_id not in {"fixed_123", "fixed_456"} and not local_camera:
            failures.append("CAMERA_NOT_FIXED")
            return
        try:
            direction = _normalized(camera["position_direction_root"])
            if local_camera:
                from .installation_view import interface_view
                expected = interface_view(payload)['camera']
                if not np.allclose(direction, expected['position_direction_root'], atol=1e-6, rtol=0):
                    raise ValueError('Interface camera differs from locked native normal')
            normal = _normalized(payload["receiver_normal_root"])
            translation = np.asarray(
                payload["translation_vector_root"], dtype=np.float64
            )
            if translation.shape != (3,) or not np.isfinite(translation).all():
                raise ValueError
        except (KeyError, TypeError, ValueError):
            failures.append("CAMERA_GEOMETRY_INVALID")
            return
        # Creo surface direction signs do not establish a physical front side;
        # reject only a true silhouette, not the opposite locked octant.
        if abs(float(np.dot(normal, direction))) < 0.35:
            failures.append("CAMERA_RECEIVER_SILHOUETTE")
        projected = translation - float(np.dot(translation, direction)) * direction
        if float(np.linalg.norm(projected)) <= 1.0e-6:
            failures.append("EXPLOSION_NOT_VISIBLE_IN_CAMERA")

    def _validate_camera_visibility(
        self,
        payload: dict[str, Any],
        variant_index: int,
        failures: list[str],
    ) -> None:
        if not self.camera_visibility_enabled:
            return
        contract = payload.get("camera_visibility")
        if not isinstance(contract, dict) or contract.get("status") != "ready":
            return
        decision = payload.get("camera_selection")
        if not isinstance(decision, dict) or (
            decision.get("schema_version") != "camera-selection-decision/v1"
            or decision.get("status") != "selected"
            or decision.get("selection_policy")
            != "eligible_worst_visibility_then_stable_id/v1"
        ):
            failures.append("CAMERA_VISIBILITY_AUDIT_INVALID")
            return
        selected_id = str(decision.get("selected_camera_id") or "")
        variant = _camera_variant(payload, variant_index)
        variant_camera_id = str(
            variant.get("camera_id") or variant.get("id") or ""
        )
        if (
            selected_id not in {"fixed_123", "fixed_456"}
            or selected_id != str(payload.get("camera_id") or "")
            or selected_id != variant_camera_id
        ):
            failures.append("CAMERA_VISIBILITY_AUDIT_INVALID")
            return
        audits = decision.get("audits")
        if not isinstance(audits, list) or len(audits) != 2:
            failures.append("CAMERA_VISIBILITY_AUDIT_INVALID")
            return
        by_id = {
            str(item.get("camera_id") or ""): item
            for item in audits
            if isinstance(item, dict)
        }
        selected = by_id.get(selected_id)
        if set(by_id) != {"fixed_123", "fixed_456"} or not isinstance(
            selected, dict
        ):
            failures.append("CAMERA_VISIBILITY_AUDIT_INVALID")
            return
        hashes = (
            selected.get("isolated_sha256"),
            selected.get("staged_sha256"),
        )
        if (
            selected.get("schema_version") != "camera-visibility-audit/v1"
            or selected.get("source")
            != "creo-lossless-component-label-raster/v1"
            or selected.get("eligible") is not True
            or selected.get("failures") != []
            or any(
                not isinstance(value, str)
                or not value.startswith("sha256:")
                or len(value) != 71
                for value in hashes
            )
        ):
            failures.append("CAMERA_VISIBILITY_AUDIT_INVALID")

    def _validate_arrow_audit(
        self,
        audit_file: Path,
        payload: dict[str, Any],
        failures: list[str],
    ) -> int | None:
        try:
            audit = json.loads(audit_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            failures.append("ARROW_AUDIT_INVALID")
            return None
        if not isinstance(audit, dict) or (
            audit.get("schema_version") != "arrow-projection/v1"
            or audit.get("policy") != "same_cad_point/v1"
            or audit.get("status") != "passed"
        ):
            failures.append("ARROW_AUDIT_INVALID")
            return None
        arrows = audit.get("arrows")
        if not isinstance(arrows, list) or not arrows:
            failures.append("ARROW_COVERAGE_INVALID")
            return None
        moving_occurrences = [
            str(value) for value in payload.get("moving_occurrences", [])
        ]
        raw_translations = payload.get("occurrence_translation_vectors_root")
        if not isinstance(raw_translations, dict):
            uniform = payload.get("translation_vector_root")
            raw_translations = {
                occurrence: uniform for occurrence in moving_occurrences
            }
        if set(str(value) for value in raw_translations) != set(moving_occurrences):
            failures.append("TRANSLATION_AUDIT_INVALID")
            return None
        expected_translations: dict[str, list[float]] = {}
        try:
            for occurrence, vector in raw_translations.items():
                if not isinstance(vector, (list, tuple)) or len(vector) != 3:
                    raise ValueError
                converted = [float(value) for value in vector]
                if not all(math.isfinite(value) for value in converted):
                    raise ValueError
                expected_translations[str(occurrence)] = converted
        except (TypeError, ValueError):
            failures.append("TRANSLATION_AUDIT_INVALID")
            return None
        covered: list[str] = []
        for arrow in arrows:
            if not isinstance(arrow, dict):
                failures.append("ARROW_AUDIT_INVALID")
                continue
            if arrow.get("anchor_source") == "occurrence_origin_fallback":
                failures.append("ARROW_SURFACE_ANCHOR_UNAVAILABLE")
            arrow_coverage = arrow.get("covered_occurrences")
            if not isinstance(arrow_coverage, list):
                failures.append("ARROW_COVERAGE_INVALID")
                continue
            covered.extend(str(value) for value in arrow_coverage)
            complete = arrow.get("complete_root")
            exploded = arrow.get("exploded_root")
            if (
                not isinstance(complete, list)
                or not isinstance(exploded, list)
                or len(complete) != 3
                or len(exploded) != 3
            ):
                failures.append("TRANSLATION_AUDIT_INVALID")
                continue
            try:
                actual = [
                    float(exploded[index]) - float(complete[index])
                    for index in range(3)
                ]
                if not all(math.isfinite(value) for value in actual):
                    raise ValueError
            except (TypeError, ValueError):
                failures.append("TRANSLATION_AUDIT_INVALID")
                continue
            expected_for_arrow = [
                expected_translations.get(str(occurrence))
                for occurrence in arrow_coverage
            ]
            if any(expected is None for expected in expected_for_arrow) or any(
                any(
                    not math.isclose(
                        actual[index],
                        expected[index],
                        abs_tol=self.vector_tolerance,
                    )
                    for index in range(3)
                )
                for expected in expected_for_arrow
                if expected is not None
            ):
                failures.append("TRANSLATION_AUDIT_INVALID")
        if sorted(covered) != sorted(
            str(value) for value in payload.get("moving_occurrences", [])
        ):
            failures.append("ARROW_COVERAGE_INVALID")
        return len(arrows)


class DeterministicRenderValidator:
    """Hard publication gates which no caller may waive."""

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


def _camera_variant(payload: dict[str, Any], variant_index: int) -> dict[str, Any]:
    presentation = payload.get("presentation", {})
    variants = presentation.get("variants", [])
    catalog = payload.get("camera_catalog", {})
    if isinstance(variants, list) and 0 <= variant_index < len(variants):
        variant = variants[variant_index]
        if not isinstance(variant, dict):
            return {}
        camera_id = str(variant.get("camera_id", ""))
        camera = catalog.get(camera_id)
        if isinstance(camera, dict):
            return {
                "id": camera_id,
                "camera_id": camera_id,
                "position_direction_root": camera.get("position_direction_root"),
                "up_reference_root": camera.get("up_reference_root"),
                "zoom": variant.get("zoom"),
                "pan": variant.get("pan"),
            }
    camera = payload.get("camera")
    return camera if isinstance(camera, dict) else {}


def _normalized(values: Any) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float64)
    if vector.shape != (3,) or not np.isfinite(vector).all():
        raise ValueError("invalid vector")
    length = float(np.linalg.norm(vector))
    if length <= 1.0e-10:
        raise ValueError("zero vector")
    return vector / length


def _composition_metrics(
    pixels: np.ndarray,
    *,
    foreground_delta: int,
    min_component_pixels: int,
    component_downsample: int,
    ignored_regions: tuple[tuple[int, int, int, int], ...],
    target_pixel: tuple[int, int],
) -> RasterCompositionMetrics:
    height, width, _ = pixels.shape
    border_width = max(4, min(20, width // 20, height // 20))
    border = np.concatenate(
        (
            pixels[:border_width].reshape(-1, 3),
            pixels[-border_width:].reshape(-1, 3),
            pixels[:, :border_width].reshape(-1, 3),
            pixels[:, -border_width:].reshape(-1, 3),
        )
    )
    background = np.median(border, axis=0).astype(np.int16)
    delta = np.max(
        np.abs(pixels.astype(np.int16) - background.reshape(1, 1, 3)),
        axis=2,
    )
    foreground = delta >= foreground_delta
    for x0, y0, x1, y1 in ignored_regions:
        foreground[y0:y1, x0:x1] = False
    factor = max(1, min(component_downsample, 8))
    if factor > 1:
        cropped_height = height - height % factor
        cropped_width = width - width % factor
        sampled = foreground[:cropped_height, :cropped_width].reshape(
            cropped_height // factor,
            factor,
            cropped_width // factor,
            factor,
        ).any(axis=(1, 3))
        sampled_minimum = max(1, math.ceil(min_component_pixels / factor))
    else:
        sampled = foreground
        sampled_minimum = min_component_pixels
    sampled_components = _significant_components(sampled, sampled_minimum)
    significant = [
        (
            count * factor * factor,
            x0 * factor,
            y0 * factor,
            min(width - 1, (x1 + 1) * factor - 1),
            min(height - 1, (y1 + 1) * factor - 1),
        )
        for count, x0, y0, x1, y1 in sampled_components
    ]
    if not significant:
        return RasterCompositionMetrics(
            background_rgb=tuple(int(value) for value in background),
            subject_bbox=None,
            significant_components=0,
            foreground_pixels=0,
            width_fraction=0.0,
            height_fraction=0.0,
            max_span_fraction=0.0,
            border_margin_pixels=None,
            clipped_edges=(),
            center_pixel=None,
            center_offset_pixels=None,
        )
    x0 = min(item[1] for item in significant)
    y0 = min(item[2] for item in significant)
    x1 = max(item[3] for item in significant)
    y1 = max(item[4] for item in significant)
    width_fraction = (x1 - x0 + 1) / width
    height_fraction = (y1 - y0 + 1) / height
    clipped_edges = tuple(
        edge
        for edge, active in (
            ("left", x0 == 0),
            ("top", y0 == 0),
            ("right", x1 == width - 1),
            ("bottom", y1 == height - 1),
        )
        if active
    )
    center_pixel = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    center_offset = float(
        np.hypot(center_pixel[0] - target_pixel[0], center_pixel[1] - target_pixel[1])
    )
    return RasterCompositionMetrics(
        background_rgb=tuple(int(value) for value in background),
        subject_bbox=(x0, y0, x1, y1),
        significant_components=len(significant),
        foreground_pixels=sum(item[0] for item in significant),
        width_fraction=width_fraction,
        height_fraction=height_fraction,
        max_span_fraction=max(width_fraction, height_fraction),
        border_margin_pixels=min(x0, y0, width - 1 - x1, height - 1 - y1),
        clipped_edges=clipped_edges,
        center_pixel=center_pixel,
        center_offset_pixels=center_offset,
    )


def _native_arrow_background_difference(pixels: np.ndarray, base: np.ndarray, *, color: str) -> np.ndarray:
    """Remove native CAD red-body outline changes without guessing arrow pixels."""
    difference=np.max(np.abs(pixels.astype(np.int16)-base.astype(np.int16)),axis=2)>8
    if color=='red':
        values=base.astype(np.int16)
        body=(values[:,:,0]>=180)&(values[:,:,1]<=80)&(values[:,:,2]<=80)
        # Native JPEG/edge antialias residuals stay within a few pixels of the
        # unchanged CAD body. An arrow of identical color over that body is
        # not readable there; retain the visible arrow on contrasting surfaces.
        for _ in range(max(1,round(min(body.shape)/1600*3))):
            padded=np.pad(body,1);expanded=np.zeros_like(body)
            for dy in range(3):
                for dx in range(3):expanded|=padded[dy:dy+body.shape[0],dx:dx+body.shape[1]]
            body=expanded
        difference &= ~body
    return np.where(difference[:,:,None],pixels,255).astype(np.uint8)


def _arrow_raster_metrics(
    pixels: np.ndarray,
    *,
    green_delta: int,
    target_pixel: tuple[int, int],
    min_component_pixels: int,
    color: str = 'green',
) -> ArrowRasterMetrics:
    values = pixels.astype(np.int16)
    red, green, blue = values[:, :, 0], values[:, :, 1], values[:, :, 2]
    mask = (
        (green >= 60)
        & (green - red >= green_delta)
        & (green - blue >= green_delta)
    )
    if color == 'red':
        # The native arrow is pure red. Orange seal bodies are not arrows.
        mask = (red >= 180) & (green <= 80) & (blue <= 80) & (red - green >= green_delta) & (red - blue >= green_delta)
    y_values, x_values = np.nonzero(mask)
    if not len(x_values):
        return ArrowRasterMetrics(None, 0, 0, 0, None, None, None)
    significant_components = len(
        _significant_components(mask, min_component_pixels)
    )
    x0, x1 = int(x_values.min()), int(x_values.max())
    y0, y1 = int(y_values.min()), int(y_values.max())
    height, width, _ = pixels.shape
    center_pixel = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    center_offset = float(
        np.hypot(center_pixel[0] - target_pixel[0], center_pixel[1] - target_pixel[1])
    )
    return ArrowRasterMetrics(
        bbox=(x0, y0, x1, y1),
        pixels=int(len(x_values)),
        significant_components=significant_components,
        max_span_pixels=max(x1 - x0 + 1, y1 - y0 + 1),
        border_margin_pixels=min(x0, y0, width - 1 - x1, height - 1 - y1),
        center_pixel=center_pixel,
        center_offset_pixels=center_offset,
    )


def _ignored_regions(
    values: Any,
    *,
    width: int,
    height: int,
) -> tuple[tuple[int, int, int, int], ...]:
    if not isinstance(values, list):
        raise TypeError("ignored_regions must be a list")
    result: list[tuple[int, int, int, int]] = []
    for item in values:
        if not isinstance(item, list) or len(item) != 4:
            raise TypeError("ignored region must have four coordinates")
        base_x0, base_y0, base_x1, base_y1 = (int(value) for value in item)
        if not (0 <= base_x0 < base_x1 <= 1600 and 0 <= base_y0 < base_y1 <= 1600):
            raise ValueError("ignored region is outside the fixed frame")
        x0 = round(base_x0 * width / 1600)
        x1 = round(base_x1 * width / 1600)
        y0 = round(base_y0 * height / 1600)
        y1 = round(base_y1 * height / 1600)
        result.append((x0, y0, x1, y1))
    return tuple(result)




def _significant_components(
    mask: np.ndarray,
    min_component_pixels: int,
) -> list[tuple[int, int, int, int, int]]:
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    result: list[tuple[int, int, int, int, int]] = []
    for start_y, start_x in zip(*np.nonzero(mask), strict=True):
        if seen[start_y, start_x]:
            continue
        pending = [(int(start_y), int(start_x))]
        seen[start_y, start_x] = True
        count = 0
        x0 = x1 = int(start_x)
        y0 = y1 = int(start_y)
        while pending:
            y, x = pending.pop()
            count += 1
            x0, x1 = min(x0, x), max(x1, x)
            y0, y1 = min(y0, y), max(y1, y)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    candidate_y, candidate_x = y + dy, x + dx
                    if (
                        0 <= candidate_y < height
                        and 0 <= candidate_x < width
                        and mask[candidate_y, candidate_x]
                        and not seen[candidate_y, candidate_x]
                    ):
                        seen[candidate_y, candidate_x] = True
                        pending.append((candidate_y, candidate_x))
        if count >= min_component_pixels:
            result.append((count, x0, y0, x1, y1))
    return result


def _vectors_close(
    left: Iterable[float],
    right: Iterable[float],
    tolerance: float,
) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(left, right, strict=True))
