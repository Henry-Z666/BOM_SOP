"""Deterministic component-visibility audits for the two fixed Creo cameras.

The audit images are not presentation images.  They are lossless label rasters
produced from the same staged Creo state and screen transform.  Every target
occurrence or receiver-interface patch has one stable 24-bit label.  Comparing
an isolated target raster with the full staged raster therefore measures
visibility without recognising objects from appearance and without an AI model.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
from PIL import Image


FIXED_CAMERA_IDS = ("fixed_123", "fixed_456")

# Temporarily frozen until the desktop review flow can show the exact failed
# camera evidence beside each repair choice. Keep the implementation and
# contracts intact so the feature can be re-enabled without a migration.
CAMERA_VISIBILITY_AUDIT_ENABLED = False


@dataclass(frozen=True)
class VisibilityThresholds:
    min_target_pixels: int = 32
    min_moving_visible_fraction: float = 0.70
    min_each_moving_visible_fraction: float = 0.55
    min_receiver_visible_fraction: float = 0.55
    min_each_receiver_visible_fraction: float = 0.40

    def __post_init__(self) -> None:
        if self.min_target_pixels < 1:
            raise ValueError("min_target_pixels must be positive")
        for name in (
            "min_moving_visible_fraction",
            "min_each_moving_visible_fraction",
            "min_receiver_visible_fraction",
            "min_each_receiver_visible_fraction",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")

    def to_contract(self) -> dict[str, object]:
        return {
            "schema_version": "camera-visibility-thresholds/v1",
            **asdict(self),
        }


@dataclass(frozen=True)
class LabelVisibility:
    label: int
    expected_pixels: int
    visible_pixels: int
    visible_fraction: float


@dataclass(frozen=True)
class CameraVisibilityAudit:
    camera_id: str
    eligible: bool
    moving: tuple[LabelVisibility, ...]
    receivers: tuple[LabelVisibility, ...]
    moving_visible_fraction: float
    receiver_visible_fraction: float
    worst_target_visible_fraction: float
    failures: tuple[str, ...]
    isolated_sha256: str
    staged_sha256: str

    @property
    def score(self) -> tuple[float, float, float]:
        return (
            self.worst_target_visible_fraction,
            self.moving_visible_fraction,
            self.receiver_visible_fraction,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "camera-visibility-audit/v1",
            "source": "creo-lossless-component-label-raster/v1",
            "camera_id": self.camera_id,
            "eligible": self.eligible,
            "moving": [asdict(item) for item in self.moving],
            "receivers": [asdict(item) for item in self.receivers],
            "moving_visible_fraction": self.moving_visible_fraction,
            "receiver_visible_fraction": self.receiver_visible_fraction,
            "worst_target_visible_fraction": self.worst_target_visible_fraction,
            "failures": list(self.failures),
            "isolated_sha256": self.isolated_sha256,
            "staged_sha256": self.staged_sha256,
        }


@dataclass(frozen=True)
class CameraSelectionDecision:
    status: str
    selected_camera_id: str | None
    audits: tuple[CameraVisibilityAudit, ...]
    options: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "camera-selection-decision/v1",
            "status": self.status,
            "selected_camera_id": self.selected_camera_id,
            "selection_policy": "eligible_worst_visibility_then_stable_id/v1",
            "audits": [item.to_dict() for item in self.audits],
            "options": [dict(item) for item in self.options],
        }


def label_from_rgb(red: int, green: int, blue: int) -> int:
    values = (int(red), int(green), int(blue))
    if any(value < 0 or value > 255 for value in values):
        raise ValueError("RGB labels must be bytes")
    return (values[0] << 16) | (values[1] << 8) | values[2]


def read_label_raster(path: Path) -> np.ndarray:
    """Read a lossless RGB label raster as one integer label per pixel."""

    with Image.open(path) as image:
        if image.format != "PNG":
            raise ValueError("camera visibility evidence must be a lossless PNG")
        pixels = np.asarray(image.convert("RGB"), dtype=np.uint32)
    return (pixels[:, :, 0] << 16) | (pixels[:, :, 1] << 8) | pixels[:, :, 2]


def audit_camera_visibility(
    *,
    camera_id: str,
    isolated_labels: np.ndarray,
    staged_labels: np.ndarray,
    moving_labels: Iterable[int],
    receiver_labels: Iterable[int],
    thresholds: VisibilityThresholds,
    isolated_sha256: str = "",
    staged_sha256: str = "",
) -> CameraVisibilityAudit:
    """Measure exact target visibility from aligned Creo component-ID rasters."""

    if camera_id not in FIXED_CAMERA_IDS:
        raise ValueError("camera visibility audit requires a fixed camera")
    isolated = np.asarray(isolated_labels)
    staged = np.asarray(staged_labels)
    if isolated.ndim != 2 or staged.ndim != 2 or isolated.shape != staged.shape:
        raise ValueError("isolated and staged label rasters must be aligned 2D arrays")
    moving_ids = _unique_labels(moving_labels, "moving")
    receiver_ids = _unique_labels(receiver_labels, "receiver")
    if set(moving_ids) & set(receiver_ids):
        raise ValueError("moving and receiver labels must be disjoint")

    moving = tuple(_label_visibility(isolated, staged, label) for label in moving_ids)
    receivers = tuple(
        _label_visibility(isolated, staged, label) for label in receiver_ids
    )
    failures: list[str] = []
    _check_target_pixels(moving, thresholds.min_target_pixels, "MOVING", failures)
    _check_target_pixels(
        receivers, thresholds.min_target_pixels, "RECEIVER_INTERFACE", failures
    )
    moving_fraction = _weighted_fraction(moving)
    receiver_fraction = _weighted_fraction(receivers)
    if moving_fraction < thresholds.min_moving_visible_fraction:
        failures.append("MOVING_SET_OCCLUDED")
    if min(item.visible_fraction for item in moving) < (
        thresholds.min_each_moving_visible_fraction
    ):
        failures.append("MOVING_OCCURRENCE_OCCLUDED")
    if receiver_fraction < thresholds.min_receiver_visible_fraction:
        failures.append("RECEIVER_INTERFACE_OCCLUDED")
    if min(item.visible_fraction for item in receivers) < (
        thresholds.min_each_receiver_visible_fraction
    ):
        failures.append("RECEIVER_INTERFACE_PATCH_OCCLUDED")
    unique_failures = tuple(dict.fromkeys(failures))
    all_targets = (*moving, *receivers)
    return CameraVisibilityAudit(
        camera_id=camera_id,
        eligible=not unique_failures,
        moving=moving,
        receivers=receivers,
        moving_visible_fraction=round(moving_fraction, 9),
        receiver_visible_fraction=round(receiver_fraction, 9),
        worst_target_visible_fraction=round(
            min(item.visible_fraction for item in all_targets), 9
        ),
        failures=unique_failures,
        isolated_sha256=isolated_sha256,
        staged_sha256=staged_sha256,
    )


def audit_camera_visibility_files(
    *,
    camera_id: str,
    isolated_raster: Path,
    staged_raster: Path,
    moving_labels: Iterable[int],
    receiver_labels: Iterable[int],
    thresholds: VisibilityThresholds,
) -> CameraVisibilityAudit:
    return audit_camera_visibility(
        camera_id=camera_id,
        isolated_labels=read_label_raster(isolated_raster),
        staged_labels=read_label_raster(staged_raster),
        moving_labels=moving_labels,
        receiver_labels=receiver_labels,
        thresholds=thresholds,
        isolated_sha256=_file_sha256(isolated_raster),
        staged_sha256=_file_sha256(staged_raster),
    )


def select_camera_from_visibility_audits(
    audits: Iterable[CameraVisibilityAudit],
) -> CameraSelectionDecision:
    by_id = {audit.camera_id: audit for audit in audits}
    if set(by_id) != set(FIXED_CAMERA_IDS):
        raise ValueError("camera selection requires exactly fixed_123 and fixed_456 audits")
    ordered = tuple(by_id[camera_id] for camera_id in FIXED_CAMERA_IDS)
    eligible = [audit for audit in ordered if audit.eligible]
    if eligible:
        selected = max(
            eligible,
            key=lambda item: (
                item.score,
                1 if item.camera_id == "fixed_123" else 0,
            ),
        )
        return CameraSelectionDecision(
            status="selected",
            selected_camera_id=selected.camera_id,
            audits=ordered,
            options=(),
        )
    return CameraSelectionDecision(
        status="needs_resolution",
        selected_camera_id=None,
        audits=ordered,
        options=_resolution_options(ordered),
    )


def visibility_contract(
    moving_labels: Mapping[str, int],
    receiver_labels: Mapping[str, int],
    thresholds: VisibilityThresholds | None = None,
) -> dict[str, object]:
    configured = thresholds or VisibilityThresholds()
    if not moving_labels or not receiver_labels:
        raise ValueError("camera visibility contract requires moving and receiver labels")
    return {
        "schema_version": "camera-visibility-contract/v1",
        "source": "creo-lossless-component-label-raster/v1",
        "candidate_camera_ids": list(FIXED_CAMERA_IDS),
        "moving_labels": {str(key): int(value) for key, value in moving_labels.items()},
        "receiver_interface_labels": {
            str(key): int(value) for key, value in receiver_labels.items()
        },
        "thresholds": configured.to_contract(),
        "formal_render_requires_selected_audit": True,
        "on_no_eligible_camera": "structured_resolution_options/v1",
    }


def apply_camera_selection(
    task_payload: Mapping[str, object],
    decision: CameraSelectionDecision,
) -> dict[str, object]:
    """Lock a passed audit decision into a render payload without script input."""

    if decision.status != "selected" or decision.selected_camera_id is None:
        raise ValueError("camera selection decision is not eligible for formal rendering")
    selected_id = decision.selected_camera_id
    selected_audit = next(
        (item for item in decision.audits if item.camera_id == selected_id), None
    )
    if selected_audit is None or not selected_audit.eligible:
        raise ValueError("selected camera has no eligible visibility audit")
    payload = deepcopy(dict(task_payload))
    catalog = payload.get("camera_catalog")
    if not isinstance(catalog, Mapping) or not isinstance(
        catalog.get(selected_id), Mapping
    ):
        raise ValueError("render payload lacks the selected fixed camera")
    presentation = payload.get("presentation")
    if not isinstance(presentation, Mapping):
        raise ValueError("render payload lacks its presentation contract")
    locked_presentation = deepcopy(dict(presentation))
    locked_presentation["variants"] = [
        {
            "variant_id": "visibility-audited",
            "camera_id": selected_id,
            "zoom": 1.0,
            "pan": [0.0, 0.0],
        }
    ]
    payload["camera_id"] = selected_id
    payload["camera"] = {
        "id": selected_id,
        **deepcopy(dict(catalog[selected_id])),
        "zoom": 1.0,
        "pan": [0.0, 0.0],
        "frame": "square",
    }
    payload["presentation"] = locked_presentation
    payload["camera_selection"] = decision.to_dict()
    return payload


def camera_selection_decision_from_dict(
    payload: Mapping[str, object],
) -> CameraSelectionDecision:
    if payload.get("schema_version") != "camera-selection-decision/v1":
        raise ValueError("unsupported camera selection decision")
    raw_audits = payload.get("audits")
    if not isinstance(raw_audits, list):
        raise ValueError("camera selection decision has no audits")
    audits: list[CameraVisibilityAudit] = []
    for raw in raw_audits:
        if not isinstance(raw, Mapping):
            raise ValueError("camera selection audit is invalid")
        audits.append(_audit_from_dict(raw))
    raw_options = payload.get("options", [])
    if not isinstance(raw_options, list) or not all(
        isinstance(item, Mapping) for item in raw_options
    ):
        raise ValueError("camera selection options are invalid")
    decision = CameraSelectionDecision(
        status=str(payload.get("status") or ""),
        selected_camera_id=(
            str(payload["selected_camera_id"])
            if payload.get("selected_camera_id") is not None
            else None
        ),
        audits=tuple(audits),
        options=tuple(dict(item) for item in raw_options),
    )
    # Recompute the decision so a hand-edited selected ID cannot bypass the
    # deterministic ranking or make an ineligible camera formal.
    expected = select_camera_from_visibility_audits(decision.audits)
    if (
        decision.status != expected.status
        or decision.selected_camera_id != expected.selected_camera_id
    ):
        raise ValueError("camera selection decision does not match its audits")
    return decision


def _unique_labels(values: Iterable[int], name: str) -> tuple[int, ...]:
    result = tuple(int(value) for value in values)
    if not result or len(result) != len(set(result)):
        raise ValueError(f"{name} labels must be non-empty and unique")
    if any(value <= 0 or value > 0xFFFFFF for value in result):
        raise ValueError(f"{name} labels must be non-background RGB labels")
    return result


def _label_visibility(
    isolated: np.ndarray, staged: np.ndarray, label: int
) -> LabelVisibility:
    expected = int(np.count_nonzero(isolated == label))
    visible = int(np.count_nonzero(staged == label))
    if visible > expected:
        raise ValueError(
            f"staged label {label} exceeds its aligned isolated reference"
        )
    fraction = 0.0 if expected == 0 else visible / expected
    return LabelVisibility(label, expected, visible, round(fraction, 9))


def _check_target_pixels(
    values: tuple[LabelVisibility, ...],
    minimum: int,
    prefix: str,
    failures: list[str],
) -> None:
    if any(item.expected_pixels < minimum for item in values):
        failures.append(f"{prefix}_AUDIT_TARGET_TOO_SMALL")


def _weighted_fraction(values: tuple[LabelVisibility, ...]) -> float:
    expected = sum(item.expected_pixels for item in values)
    if expected == 0:
        return 0.0
    return sum(item.visible_pixels for item in values) / expected


def _resolution_options(
    audits: tuple[CameraVisibilityAudit, ...]
) -> tuple[dict[str, object], ...]:
    failures = {failure for audit in audits for failure in audit.failures}
    options: list[dict[str, object]] = []
    if failures & {"MOVING_SET_OCCLUDED", "MOVING_OCCURRENCE_OCCLUDED"}:
        options.append(
            {
                "option_id": "increase_bounded_explosion_distance",
                "label": "增加一级爆炸距离后重新比较",
                "revision_kind": "installation_geometry",
            }
        )
    if failures & {
        "RECEIVER_INTERFACE_OCCLUDED",
        "RECEIVER_INTERFACE_PATCH_OCCLUDED",
    }:
        options.append(
            {
                "option_id": "focus_receiver_interface",
                "label": "聚焦移动件与安装接口后重新比较",
                "revision_kind": "presentation",
            }
        )
    options.append(
        {
            "option_id": "defer_product_camera_calibration",
            "label": "转入产品相机配置处理",
            "revision_kind": "product_configuration",
        }
    )
    return tuple(options)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _audit_from_dict(payload: Mapping[str, object]) -> CameraVisibilityAudit:
    if payload.get("schema_version") != "camera-visibility-audit/v1":
        raise ValueError("unsupported camera visibility audit")

    def labels(key: str) -> tuple[LabelVisibility, ...]:
        raw_values = payload.get(key)
        if not isinstance(raw_values, list) or not raw_values:
            raise ValueError(f"camera visibility audit has no {key} labels")
        result = []
        for raw in raw_values:
            if not isinstance(raw, Mapping):
                raise ValueError("camera visibility label evidence is invalid")
            result.append(
                LabelVisibility(
                    label=int(raw["label"]),
                    expected_pixels=int(raw["expected_pixels"]),
                    visible_pixels=int(raw["visible_pixels"]),
                    visible_fraction=float(raw["visible_fraction"]),
                )
            )
        return tuple(result)

    failures = payload.get("failures", [])
    if not isinstance(failures, list):
        raise ValueError("camera visibility failures are invalid")
    return CameraVisibilityAudit(
        camera_id=str(payload.get("camera_id") or ""),
        eligible=bool(payload.get("eligible", False)),
        moving=labels("moving"),
        receivers=labels("receivers"),
        moving_visible_fraction=float(payload.get("moving_visible_fraction", 0.0)),
        receiver_visible_fraction=float(
            payload.get("receiver_visible_fraction", 0.0)
        ),
        worst_target_visible_fraction=float(
            payload.get("worst_target_visible_fraction", 0.0)
        ),
        failures=tuple(str(value) for value in failures),
        isolated_sha256=str(payload.get("isolated_sha256") or ""),
        staged_sha256=str(payload.get("staged_sha256") or ""),
    )
