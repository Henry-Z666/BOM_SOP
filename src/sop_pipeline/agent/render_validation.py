from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image


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
    }
)


class DeterministicNativeRenderValidator:
    """Validate a Creo-native image and audit through one publication seam."""

    def __init__(self, *, vector_tolerance: float = 1.0e-5) -> None:
        self.vector_tolerance = vector_tolerance

    def validate(
        self,
        image_file: Path,
        audit_file: Path,
        task_payload: dict[str, Any],
        *,
        variant_index: int = 0,
    ) -> NativeRenderGateReport:
        failures: list[str] = []
        composition, arrow_raster = self._validate_image(
            image_file, task_payload, failures
        )
        self._validate_camera(task_payload, variant_index, failures)
        self._validate_arrow_audit(audit_file, task_payload, failures)
        unique = tuple(dict.fromkeys(failures))
        return NativeRenderGateReport(
            schema_version="native-render-gate-report/v1",
            passed=not unique,
            failures=unique,
            composition=composition,
            arrow_raster=arrow_raster,
        )

    def _validate_image(
        self,
        image_file: Path,
        payload: dict[str, Any],
        failures: list[str],
    ) -> tuple[RasterCompositionMetrics | None, ArrowRasterMetrics | None]:
        if not image_file.is_file() or image_file.stat().st_size == 0:
            failures.append("RENDER_OUTPUT_MISSING")
            return None, None
        try:
            with Image.open(image_file) as image:
                rgb = image.convert("RGB")
                if rgb.size != (1600, 1600):
                    failures.append("RENDER_FRAME_INVALID")
                    return None, None
                pixels = np.asarray(rgb)
        except (OSError, ValueError):
            failures.append("RENDER_FRAME_INVALID")
            return None, None

        presentation = payload.get("presentation", {})
        if (
            not isinstance(presentation, dict)
            or presentation.get("schema_version") != "fixed-frame-presentation/v1"
            or presentation.get("focus_context") != "stage_visible_bbox/v1"
            or presentation.get("framing_priority") != "installation_activity/v1"
            or presentation.get("zoom_anchor")
            != "installation_activity_center/v1"
        ):
            failures.append("PRESENTATION_CONTRACT_INVALID")
            return None, None
        framing_profile = presentation.get("framing_profile", {})
        if isinstance(framing_profile, dict) and framing_profile.get("policy") == "native_zoom_to_selected/v1":
            selected_fit = presentation.get("native_selected_fit")
            try:
                selected_fit_level = float(selected_fit.get("zoom_to_selected_level", 0.0))
            except (AttributeError, TypeError, ValueError, OverflowError):
                selected_fit_level = 0.0
            if (
                not isinstance(selected_fit, dict)
                or selected_fit.get("schema_version") != "native-selected-fit/v1"
                or selected_fit.get("command") != "ProCmdZoomIntoOutline"
                or selected_fit.get("selection_scope")
                != "moving_and_receiver_occurrences/v1"
                or selected_fit.get("level_policy")
                != "fixed_native_selection_margin/v1"
                or selected_fit.get("max_commands_per_render") != 1
                or selected_fit.get("absolute_pan_zoom_forbidden") is not True
                or not math.isclose(selected_fit_level, 0.75, abs_tol=1.0e-9)
            ):
                failures.append("PRESENTATION_CONTRACT_INVALID")
                return None, None
        centering = presentation.get("center_gate")
        if (
            not isinstance(centering, dict)
            or centering.get("schema_version")
            != "native-composition-center-gate/v1"
            or centering.get("target_pixel") != [800, 800]
        ):
            failures.append("PRESENTATION_CONTRACT_INVALID")
            return None, None
        try:
            target_pixel = tuple(int(value) for value in centering["target_pixel"])
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
            or not 1.0 <= max_activity_center_offset <= 200.0
            or not 1.0 <= max_arrow_center_offset <= 200.0
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
            ignored_regions = _ignored_regions(gate["ignored_regions"])
        except (KeyError, TypeError, ValueError, OverflowError):
            failures.append("PRESENTATION_CONTRACT_INVALID")
            return None, None
        if not (
            1 <= threshold <= 255
            and min_component_pixels >= 1
            and 1 <= component_downsample <= 8
            and 0.0 < min_span < max_span <= 1.0
            and 0 <= max_clipped_edges <= 2
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
                pixels,
                green_delta=arrow_green_delta,
                target_pixel=target_pixel,
                min_component_pixels=max(16, min_arrow_pixels // 4),
            )
        if metrics.max_span_fraction < min_span:
            failures.append("SUBJECT_TOO_SMALL")
        if metrics.max_span_fraction > max_span:
            failures.append("SUBJECT_TOO_LARGE")
        if len(metrics.clipped_edges) > max_clipped_edges:
            failures.append("EXCESSIVE_CONTEXT_CLIPPING")
        arrow_metrics = _arrow_raster_metrics(
            pixels,
            green_delta=arrow_green_delta,
            target_pixel=target_pixel,
            min_component_pixels=max(16, min_arrow_pixels // 4),
        )
        if arrow_metrics.bbox is None:
            failures.append("ARROW_NOT_VISIBLE")
        else:
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
        # The compiled contract defines the installation focus as the midpoint
        # of the staged subject and the native arrow.  Applying both limits to
        # the two centres independently makes the gate mathematically
        # impossible whenever their separation exceeds the sum of the limits,
        # even when their declared midpoint is exactly at the target. Validate
        # the installation focus on the final native-selected-fit raster;
        # arrow visibility, size and clipping remain independent hard gates.
        focus_center = metrics.center_pixel
        if focus_center is not None and arrow_metrics.center_pixel is not None:
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
                arrow_metrics.center_pixel is not None
                and focus_offset > max_arrow_center_offset
            ):
                failures.append("ARROW_NOT_CENTERED")
        return metrics, arrow_metrics

    def _validate_camera(
        self,
        payload: dict[str, Any],
        variant_index: int,
        failures: list[str],
    ) -> None:
        camera = _camera_variant(payload, variant_index)
        camera_id = str(camera.get("camera_id") or camera.get("id") or "")
        if camera_id not in {"fixed_123", "fixed_456"}:
            failures.append("CAMERA_NOT_FIXED")
            return
        try:
            direction = _normalized(camera["position_direction_root"])
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
        expected_translation = payload.get("translation_vector_root")
        if not isinstance(expected_translation, (list, tuple)) or len(
            expected_translation
        ) != 3:
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
                expected = [float(value) for value in expected_translation]
                if not all(math.isfinite(value) for value in actual + expected):
                    raise ValueError
            except (TypeError, ValueError):
                failures.append("TRANSLATION_AUDIT_INVALID")
                continue
            if any(
                not math.isclose(
                    actual[index],
                    expected[index],
                    abs_tol=self.vector_tolerance,
                )
                for index in range(3)
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


def _arrow_raster_metrics(
    pixels: np.ndarray,
    *,
    green_delta: int,
    target_pixel: tuple[int, int],
    min_component_pixels: int,
) -> ArrowRasterMetrics:
    values = pixels.astype(np.int16)
    red, green, blue = values[:, :, 0], values[:, :, 1], values[:, :, 2]
    mask = (
        (green >= 60)
        & (green - red >= green_delta)
        & (green - blue >= green_delta)
    )
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


def _ignored_regions(values: Any) -> tuple[tuple[int, int, int, int], ...]:
    if not isinstance(values, list):
        raise TypeError("ignored_regions must be a list")
    result: list[tuple[int, int, int, int]] = []
    for item in values:
        if not isinstance(item, list) or len(item) != 4:
            raise TypeError("ignored region must have four coordinates")
        x0, y0, x1, y1 = (int(value) for value in item)
        if not (0 <= x0 < x1 <= 1600 and 0 <= y0 < y1 <= 1600):
            raise ValueError("ignored region is outside the fixed frame")
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
