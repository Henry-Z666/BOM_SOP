from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from sop_pipeline.camera_planner import absolute_view_matrix


Bounds = tuple[tuple[float, float, float], tuple[float, float, float]]


class FramingScaleError(ValueError):
    """Raised when CAD bounds cannot produce a deterministic scale bucket."""


def build_framing_scale_evidence(
    *,
    occurrence_bounds_root: Mapping[str, Any],
    moving_occurrences: Iterable[str],
    receiver_occurrences: Iterable[str],
    visible_occurrences: Iterable[str],
    translation_vector_root: Iterable[float] | None,
    stage_scope_occurrence: str,
    camera: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a camera-projected, product-neutral framing cache signature.

    Buckets use half-octaves: adjacent bucket boundaries differ by sqrt(2).
    This is coarse enough to reuse a calibrated profile across compatible
    steps, while a clear size change or hierarchy transition gets a new key.
    """

    moving = tuple(dict.fromkeys(str(value) for value in moving_occurrences))
    receivers = tuple(dict.fromkeys(str(value) for value in receiver_occurrences))
    visible = tuple(dict.fromkeys(str(value) for value in visible_occurrences))
    required = tuple(dict.fromkeys((*moving, *receivers, *visible)))
    missing = tuple(
        occurrence
        for occurrence in required
        if _read_bounds(occurrence_bounds_root.get(occurrence)) is None
    )
    if not moving or not receivers or missing:
        return {
            "schema_version": "cad-framing-scale/v1",
            "status": "unavailable",
            "reason": "missing_occurrence_bounds",
            "missing_occurrences": list(missing),
        }

    translation = _vector(translation_vector_root or (0.0, 0.0, 0.0))
    activity_items: list[Bounds] = []
    for occurrence in moving:
        bounds = _required_bounds(occurrence_bounds_root, occurrence)
        activity_items.extend((bounds, _translate(bounds, translation)))
    activity_items.extend(
        _required_bounds(occurrence_bounds_root, occurrence)
        for occurrence in receivers
    )
    context_items = [
        _required_bounds(occurrence_bounds_root, occurrence)
        for occurrence in visible
    ]
    activity = _union(activity_items)
    context = _union(context_items)

    direction = _vector(camera.get("position_direction_root"))
    up = _vector(camera.get("up_reference_root"))
    matrix = absolute_view_matrix(direction, up)
    right = tuple(float(matrix[index][0]) for index in range(3))
    screen_up = tuple(float(matrix[index][1]) for index in range(3))
    activity_width, activity_height = _projected_size(activity, right, screen_up)
    context_width, context_height = _projected_size(context, right, screen_up)
    activity_span = max(activity_width, activity_height)
    context_span = max(context_width, context_height)
    if activity_span <= 1.0e-9 or context_span <= 1.0e-9:
        raise FramingScaleError("CAD framing bounds are degenerate")

    activity_bucket = _half_octave_bucket(activity_span)
    context_bucket = _half_octave_bucket(context_span)
    ratio_bucket = _half_octave_bucket(context_span / activity_span)
    scope_depth = 0 if stage_scope_occurrence == "ROOT" else len(
        tuple(part for part in stage_scope_occurrence.split("/") if part)
    )
    signature = (
        "cad-framing-scale/v1:"
        f"depth={scope_depth}:activity={activity_bucket}:"
        f"context={context_bucket}:ratio={ratio_bucket}"
    )
    return {
        "schema_version": "cad-framing-scale/v1",
        "status": "available",
        "scale_signature": signature,
        "bucket_policy": "half_octave_sqrt2/v1",
        "scope_depth": scope_depth,
        "activity_bucket": activity_bucket,
        "context_bucket": context_bucket,
        "context_activity_ratio_bucket": ratio_bucket,
        "activity_bounds_root": _bounds_json(activity),
        "context_bounds_root": _bounds_json(context),
        "activity_projected_size_root": [
            round(activity_width, 6),
            round(activity_height, 6),
        ],
        "context_projected_size_root": [
            round(context_width, 6),
            round(context_height, 6),
        ],
        "moving_occurrences": list(moving),
        "receiver_occurrences": list(receivers),
        "visible_occurrence_count": len(visible),
    }


def _read_bounds(value: Any) -> Bounds | None:
    if not isinstance(value, Mapping):
        return None
    try:
        low = _vector(value["min"])
        high = _vector(value["max"])
    except (KeyError, TypeError, FramingScaleError):
        return None
    if any(low[index] > high[index] for index in range(3)):
        return None
    return low, high


def _required_bounds(values: Mapping[str, Any], occurrence: str) -> Bounds:
    result = _read_bounds(values.get(occurrence))
    if result is None:
        raise FramingScaleError(f"missing CAD bounds for {occurrence}")
    return result


def _vector(value: Iterable[float] | Any) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise FramingScaleError("framing scale vector must have three values")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise FramingScaleError("framing scale vector must be finite")
    return result


def _translate(bounds: Bounds, translation: tuple[float, float, float]) -> Bounds:
    return tuple(
        tuple(point[index] + translation[index] for index in range(3))
        for point in bounds
    )  # type: ignore[return-value]


def _union(bounds: Iterable[Bounds]) -> Bounds:
    values = tuple(bounds)
    if not values:
        raise FramingScaleError("framing scale union is empty")
    return (
        tuple(min(value[0][index] for value in values) for index in range(3)),
        tuple(max(value[1][index] for value in values) for index in range(3)),
    )  # type: ignore[return-value]


def _corners(bounds: Bounds) -> tuple[tuple[float, float, float], ...]:
    low, high = bounds
    return tuple(
        (x, y, z)
        for x in (low[0], high[0])
        for y in (low[1], high[1])
        for z in (low[2], high[2])
    )


def _projected_size(
    bounds: Bounds,
    right: tuple[float, float, float],
    screen_up: tuple[float, float, float],
) -> tuple[float, float]:
    points = _corners(bounds)
    horizontal = [sum(point[index] * right[index] for index in range(3)) for point in points]
    vertical = [sum(point[index] * screen_up[index] for index in range(3)) for point in points]
    return max(horizontal) - min(horizontal), max(vertical) - min(vertical)


def _half_octave_bucket(value: float) -> int:
    if not math.isfinite(value) or value <= 0.0:
        raise FramingScaleError("scale bucket input must be positive and finite")
    return math.floor(2.0 * math.log2(value))


def _bounds_json(bounds: Bounds) -> dict[str, list[float]]:
    return {
        "min": [round(value, 6) for value in bounds[0]],
        "max": [round(value, 6) for value in bounds[1]],
    }
