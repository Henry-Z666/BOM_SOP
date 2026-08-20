"""Deterministic root-ASM camera planning for Creo staged renders.

Camera directions are vectors from the staged model centre toward the camera.
They are absolute in the root assembly coordinate system; no value in this
module represents a relative Euler rotation.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Iterable

Vector = list[float]
Matrix = list[list[float]]
AXES = ("X", "Y", "Z")


def _vector(values: Iterable[float]) -> Vector:
    result = [float(value) for value in values]
    if len(result) != 3:
        raise ValueError("向量必须包含三个分量")
    return result


def dot(left: Iterable[float], right: Iterable[float]) -> float:
    a, b = _vector(left), _vector(right)
    return sum(a[i] * b[i] for i in range(3))


def cross(left: Iterable[float], right: Iterable[float]) -> Vector:
    a, b = _vector(left), _vector(right)
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def norm(values: Iterable[float]) -> float:
    return math.sqrt(dot(values, values))


def normalize(values: Iterable[float]) -> Vector:
    value = _vector(values)
    length = norm(value)
    if length < 1.0e-10:
        raise ValueError("相机向量不能为零")
    return [item / length for item in value]


def opposite(values: Iterable[float]) -> Vector:
    return [-item for item in normalize(values)]


def absolute_view_matrix(position_direction: Iterable[float],
                         up_reference: Iterable[float] = (0.0, 0.0, 1.0)) -> Matrix:
    """Build the right-handed world-to-view rotation used by J-Link.

    Creo uses a row-vector transform (translation is in row four), therefore
    screen-right, screen-up and camera-back are the first three *columns*.
    Translation is zero because this matrix locks orientation only;
    `ProCmdZoomIntoOutline` performs native selected-object framing separately.
    """
    back = normalize(position_direction)
    up_ref = normalize(up_reference)
    right_raw = cross(up_ref, back)
    if norm(right_raw) < 1.0e-8:
        raise ValueError("UP 与 ABS 平行，无法确定画面滚转")
    right = normalize(right_raw)
    up = normalize(cross(back, right))
    return [[right[0], up[0], back[0], 0.0],
            [right[1], up[1], back[1], 0.0],
            [right[2], up[2], back[2], 0.0],
            [0.0, 0.0, 0.0, 1.0]]


def determinant3(matrix: Matrix) -> float:
    a = matrix
    return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
            - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
            + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))


def _sign(value: float) -> int:
    return 1 if value >= 0.0 else -1


def _axis_direction(axis_index: int, sign: int) -> Vector:
    result = [0.0, 0.0, 0.0]
    result[axis_index] = float(sign)
    return result


def assembly_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def calibrate_camera_basis(assembly_file: str, assembly_hash: str,
                           default_view_matrix: Matrix,
                           up_reference: Iterable[float] = (0.0, 0.0, 1.0)) -> dict[str, Any]:
    if len(default_view_matrix) < 3 or any(len(row) < 3 for row in default_view_matrix[:3]):
        raise ValueError("默认视图矩阵至少为 3x3")
    # Creo's view axes are columns, not rows.  Rebuild an orthonormal rotation
    # from the saved default back/up columns so replay preserves its standard
    # roll instead of producing a tilted/diamond view.
    saved_direction = normalize(
        [float(default_view_matrix[row][2]) for row in range(3)]
    )
    saved_up = normalize([float(default_view_matrix[row][1]) for row in range(3)])
    signs = [_sign(value) for value in saved_direction]
    weak_axes = [
        AXES[index]
        for index, value in enumerate(saved_direction)
        if abs(value) < 0.15
    ]
    fallback = None
    direction = saved_direction
    if weak_axes:
        # Creo OpenFile commonly returns a front/side orthographic transform,
        # even when the product has no reusable named default view.  Such a
        # direction collapses two receiver-face families into silhouettes.
        # Complete only the missing octant weights while preserving the saved
        # visible signs and saved up reference.  The resulting camera remains
        # deterministic and still requires the normal preview hard gates.
        direction = normalize([float(value) for value in signs])
        fallback = "equal_octant_completion/v1"
    fixed_123_matrix = absolute_view_matrix(direction, saved_up)
    fixed_456_matrix = absolute_view_matrix(opposite(direction), saved_up)
    faces: dict[str, Any] = {}
    for axis_index, axis in enumerate(AXES):
        positive_face, negative_face = axis_index + 1, axis_index + 4
        sign = signs[axis_index]
        faces[str(positive_face)] = {
            "axis": axis, "sign": sign,
            "axis_label": ("+" if sign > 0 else "-") + axis,
            "normal_root": _axis_direction(axis_index, sign),
        }
        faces[str(negative_face)] = {
            "axis": axis, "sign": -sign,
            "axis_label": ("+" if sign < 0 else "-") + axis,
            "normal_root": _axis_direction(axis_index, -sign),
        }
    return {
        "schema_version": "assembly-camera-basis/v4",
        "assembly_file": assembly_file,
        "assembly_sha256": assembly_hash,
        "coordinate_system": "root_asm",
        "default_view_matrix": default_view_matrix,
        "saved_default_position_direction_root": saved_direction,
        "default_position_direction_root": direction,
        "opposite_position_direction_root": opposite(direction),
        "fixed_123_position_direction_root": direction,
        "fixed_456_position_direction_root": opposite(direction),
        "fixed_123_view_matrix": fixed_123_matrix,
        "fixed_456_view_matrix": fixed_456_matrix,
        "default_octant_signs": signs,
        "up_reference_root": saved_up,
        "faces": faces,
        "calibration": {
            "source": "Creo GetCurrentViewTransform immediately after OpenFile",
            "source_trihedral": not weak_axes,
            "trihedral": True,
            "weak_axes": weak_axes,
            "formal_view_policy": (
                "fixed_equal_octant_and_centre_opposite"
                if fallback
                else "fixed_saved_default_and_centre_opposite"
            ),
            "fallback": fallback,
            "preview_required": True,
        },
    }


def classify_receiver_face(normal_root: Iterable[float], basis: dict[str, Any],
                           max_axis_deviation_degrees: float = 25.0) -> dict[str, Any]:
    normal = normalize(normal_root)
    axis_index = max(range(3), key=lambda index: abs(normal[index]))
    sign = _sign(normal[axis_index])
    alignment = abs(normal[axis_index])
    deviation = math.degrees(math.acos(max(-1.0, min(1.0, alignment))))
    face_id = next(int(face_id) for face_id, face in basis["faces"].items()
                   if AXES[axis_index] == face["axis"] and sign == int(face["sign"]))
    return {
        "face_id": face_id,
        "axis": AXES[axis_index],
        "sign": sign,
        "axis_label": ("+" if sign > 0 else "-") + AXES[axis_index],
        "normal_root": normal,
        "axis_alignment": alignment,
        "axis_deviation_degrees": deviation,
        "confidence": "high" if deviation <= max_axis_deviation_degrees else "low",
    }


def projected_length(vector: Iterable[float], position_direction: Iterable[float]) -> float:
    value, back = _vector(vector), normalize(position_direction)
    along = dot(value, back)
    projected = [value[i] - along * back[i] for i in range(3)]
    return norm(projected)


def _candidate(candidate_id: str, direction: Vector, receiver_normal: Vector,
               explode_vector: Vector, kind: str, up: Vector) -> dict[str, Any]:
    direction = normalize(direction)
    visibility = dot(receiver_normal, direction)
    separation = projected_length(explode_vector, direction)
    matrix = absolute_view_matrix(direction, up)
    return {
        "id": candidate_id,
        "kind": kind,
        "position_direction_root": direction,
        "up_reference_root": up,
        "view_matrix": matrix,
        "metrics": {
            "receiver_normal_alignment": visibility,
            "projected_explosion_length": separation,
            "receiver_boundary_visible": None,
            "hole_min_pixel_gap": None,
            "occlusion_score": None,
            "frame_coverage": None,
        },
        "hard_gate": {
            "receiver_outside_half_space": visibility > 0.0,
            "receiver_face_not_silhouette": visibility >= 0.35,
            "projected_explosion_nonzero": separation > 1.0e-6,
            "preview_required": True,
        },
    }


def generate_camera_candidates(basis: dict[str, Any], receiver_face: dict[str, Any],
                               explosion_vector_root: Iterable[float]) -> list[dict[str, Any]]:
    """Return the single formal view allowed for this receiver face.

    Faces 1/2/3 always use the fixed equal-weight default octant; faces 4/5/6
    always use its exact centre-symmetric opposite.  There are deliberately no
    pitch, receiver-emphasis, or adjacent-octant fallbacks.
    """
    normal = normalize(receiver_face["normal_root"])
    explode = _vector(explosion_vector_root)
    up = normalize(basis.get("up_reference_root", [0.0, 0.0, 1.0]))
    fixed_123 = normalize(basis["fixed_123_position_direction_root"])
    use_default = int(receiver_face["face_id"]) <= 3
    direction = fixed_123 if use_default else opposite(fixed_123)
    candidate_id = "fixed_123" if use_default else "fixed_456"
    return [_candidate(candidate_id, direction, normal, explode,
                       "fixed_saved_default_or_centre_opposite", up)]


def score_camera_candidate(candidate: dict[str, Any], *, receiver_boundary_visible: bool,
                           hole_min_pixel_gap: float, occlusion_score: float,
                           frame_coverage: float, required_hole_gap: float = 12.0,
                           minimum_frame_coverage: float = 0.55,
                           maximum_frame_coverage: float = 1.0) -> dict[str, Any]:
    result = {**candidate, "metrics": dict(candidate["metrics"]), "hard_gate": dict(candidate["hard_gate"])}
    metrics, gate = result["metrics"], result["hard_gate"]
    metrics.update({"receiver_boundary_visible": bool(receiver_boundary_visible),
                    "hole_min_pixel_gap": float(hole_min_pixel_gap),
                    "occlusion_score": float(occlusion_score),
                    "frame_coverage": float(frame_coverage)})
    gate.update({"receiver_boundary_visible": bool(receiver_boundary_visible),
                 "holes_distinguishable": hole_min_pixel_gap >= required_hole_gap,
                 "activity_and_receiver_in_frame": minimum_frame_coverage <= frame_coverage <= maximum_frame_coverage})
    result["eligible"] = all(bool(value) for value in gate.values() if value is not None)
    visibility = max(0.0, float(metrics["receiver_normal_alignment"]))
    separation = float(metrics["projected_explosion_length"])
    result["score"] = round((1000.0 if result["eligible"] else 0.0)
                            + 100.0 * visibility + 10.0 * min(separation, 100.0)
                            + 5.0 * min(hole_min_pixel_gap, 100.0)
                            + 100.0 * max(0.0, 1.0 - occlusion_score)
                            + 50.0 * frame_coverage, 6)
    return result


def select_camera(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [candidate for candidate in candidates if candidate.get("eligible")]
    pool = eligible or candidates
    if not pool:
        raise ValueError("没有相机候选")
    # max() is stable for equal keys, so declared candidate order remains the
    # deterministic tie-breaker and keeps the primary view ahead of fallbacks.
    return max(pool, key=lambda candidate: float(candidate.get("score", 0.0)))
