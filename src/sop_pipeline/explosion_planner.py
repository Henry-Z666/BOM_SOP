"""Deterministic choice between interface-normal and lateral display explosion."""

from __future__ import annotations

import math
from typing import Any, Iterable


def select_display_translation(
    interface_normal_root: Iterable[float],
    display_distance: float,
    moving_bounds: list[dict[str, list[float]]],
    context_bounds: list[dict[str, list[float]]],
    contact_points_root: list[Iterable[float]],
) -> dict[str, Any]:
    """Return a CAD-only display vector without changing the installation axis.

    The interface normal remains the installation truth.  A lateral display
    vector is permitted only when the normal explosion still overlaps staged
    context substantially and one orthogonal root axis removes that overlap.
    This covers long bridges/hoses that telescope along themselves and clamps
    whose locating constraint is a poor exploded-view path.
    """

    normal = _unit(interface_normal_root)
    if display_distance <= 1.0e-9 or not moving_bounds or not context_bounds:
        return _result(normal, display_distance, "interface_normal", 0.0, 0.0)
    moving = _union(moving_bounds)
    context = _union(context_bounds)
    extents = [moving["max"][index] - moving["min"][index] for index in range(3)]
    positive_extents = [value for value in extents if value > 1.0e-9]
    if not positive_extents:
        return _result(normal, display_distance, "interface_normal", 0.0, 0.0)
    moving_volume = max(1.0e-9, math.prod(positive_extents))
    normal_vector = [display_distance * value for value in normal]
    normal_overlap = _overlap_sum(
        _translate(moving, normal_vector), context_bounds
    )
    normal_overlap_ratio = normal_overlap / moving_volume
    normal_span = sum(abs(normal[index]) * extents[index] for index in range(3))
    minimum_span = min(positive_extents)
    axial_ratio = normal_span / minimum_span
    shape_aspect_ratio = max(positive_extents) / minimum_span
    contact_lateral_ratio = _contact_lateral_ratio(
        normal,
        moving,
        contact_points_root,
    )
    ambiguous_normal = axial_ratio >= 8.0 or (
        normal_overlap_ratio >= 0.50
        and shape_aspect_ratio >= 3.0
        and contact_lateral_ratio >= 0.03
    )
    if normal_overlap_ratio <= 0.05 or not ambiguous_normal:
        return _result(
            normal,
            display_distance,
            "interface_normal",
            normal_overlap_ratio,
            normal_overlap_ratio,
        )

    evidence = _lateral_direction_evidence(
        normal,
        moving,
        context,
        contact_points_root,
    )
    candidates: list[tuple[tuple[float, float, int, int], list[float], float]] = []
    for axis in range(3):
        axis_vector = [0.0, 0.0, 0.0]
        axis_vector[axis] = 1.0
        if abs(_dot(axis_vector, normal)) > 0.25:
            continue
        for sign in (-1, 1):
            direction = [sign * value for value in axis_vector]
            vector = [display_distance * value for value in direction]
            overlap = _overlap_sum(_translate(moving, vector), context_bounds)
            ratio = overlap / moving_volume
            evidence_score = _dot(direction, evidence)
            candidates.append(
                (
                    (
                        round(ratio, 12),
                        -round(evidence_score, 12),
                        axis,
                        0 if sign > 0 else 1,
                    ),
                    direction,
                    ratio,
                )
            )
    if not candidates:
        return _result(
            normal,
            display_distance,
            "interface_normal",
            normal_overlap_ratio,
            normal_overlap_ratio,
        )
    _, direction, lateral_overlap_ratio = min(candidates, key=lambda item: item[0])
    if (
        lateral_overlap_ratio > 0.05
        or lateral_overlap_ratio >= normal_overlap_ratio * 0.25
    ):
        return _result(
            normal,
            display_distance,
            "interface_normal",
            normal_overlap_ratio,
            normal_overlap_ratio,
        )
    return _result(
        direction,
        display_distance,
        "lateral_clearance",
        normal_overlap_ratio,
        lateral_overlap_ratio,
    )


def _result(
    direction: Iterable[float],
    distance: float,
    mode: str,
    normal_overlap_ratio: float,
    selected_overlap_ratio: float,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "translation_vector_root": [
            round(float(value) * distance, 6) for value in direction
        ],
        "normal_overlap_ratio": round(normal_overlap_ratio, 9),
        "selected_overlap_ratio": round(selected_overlap_ratio, 9),
    }


def _lateral_direction_evidence(
    normal: list[float],
    moving: dict[str, list[float]],
    context: dict[str, list[float]],
    contact_points: list[Iterable[float]],
) -> list[float]:
    moving_centre = _centre(moving)
    diagonal = math.sqrt(
        sum(
            (moving["max"][index] - moving["min"][index]) ** 2
            for index in range(3)
        )
    )
    evidence = [0.0, 0.0, 0.0]
    parsed_contacts = [_vector(value) for value in contact_points]
    parsed_contacts = [value for value in parsed_contacts if value is not None]
    if parsed_contacts:
        contact_centre = [
            sum(point[index] for point in parsed_contacts) / len(parsed_contacts)
            for index in range(3)
        ]
        evidence = [
            moving_centre[index] - contact_centre[index] for index in range(3)
        ]
        evidence = _reject_axis(evidence, normal)
    if _length(evidence) < max(1.0e-6, diagonal * 0.03):
        context_centre = _centre(context)
        evidence = _reject_axis(
            [
                moving_centre[index] - context_centre[index]
                for index in range(3)
            ],
            normal,
        )
    return _unit(evidence) if _length(evidence) > 1.0e-9 else [0.0, 0.0, 0.0]


def _contact_lateral_ratio(
    normal: list[float],
    moving: dict[str, list[float]],
    contact_points: list[Iterable[float]],
) -> float:
    parsed = [_vector(value) for value in contact_points]
    parsed = [value for value in parsed if value is not None]
    if not parsed:
        return 0.0
    centre = _centre(moving)
    contact_centre = [
        sum(point[index] for point in parsed) / len(parsed)
        for index in range(3)
    ]
    lateral = _reject_axis(
        [centre[index] - contact_centre[index] for index in range(3)],
        normal,
    )
    diagonal = math.sqrt(
        sum(
            (moving["max"][index] - moving["min"][index]) ** 2
            for index in range(3)
        )
    )
    return _length(lateral) / diagonal if diagonal > 1.0e-9 else 0.0


def _reject_axis(vector: list[float], axis: list[float]) -> list[float]:
    along = _dot(vector, axis)
    return [vector[index] - along * axis[index] for index in range(3)]


def _overlap_sum(
    moving: dict[str, list[float]], context: list[dict[str, list[float]]]
) -> float:
    return sum(_overlap_volume(moving, item) for item in context)


def _overlap_volume(left: dict[str, list[float]], right: dict[str, list[float]]) -> float:
    return math.prod(
        max(
            0.0,
            min(left["max"][index], right["max"][index])
            - max(left["min"][index], right["min"][index]),
        )
        for index in range(3)
    )


def _translate(
    bounds: dict[str, list[float]], vector: Iterable[float]
) -> dict[str, list[float]]:
    offset = list(vector)
    return {
        key: [bounds[key][index] + offset[index] for index in range(3)]
        for key in ("min", "max")
    }


def _union(items: list[dict[str, list[float]]]) -> dict[str, list[float]]:
    return {
        "min": [min(item["min"][index] for item in items) for index in range(3)],
        "max": [max(item["max"][index] for item in items) for index in range(3)],
    }


def _centre(bounds: dict[str, list[float]]) -> list[float]:
    return [
        (bounds["min"][index] + bounds["max"][index]) / 2.0
        for index in range(3)
    ]


def _vector(value: Iterable[float]) -> list[float] | None:
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    return result if len(result) == 3 and all(math.isfinite(item) for item in result) else None


def _unit(value: Iterable[float]) -> list[float]:
    vector = [float(item) for item in value]
    length = _length(vector)
    if length <= 1.0e-10:
        raise ValueError("direction cannot be zero")
    return [item / length for item in vector]


def _length(value: Iterable[float]) -> float:
    return math.sqrt(sum(float(item) ** 2 for item in value))


def _dot(left: Iterable[float], right: Iterable[float]) -> float:
    return sum(float(a) * float(b) for a, b in zip(left, right, strict=True))
