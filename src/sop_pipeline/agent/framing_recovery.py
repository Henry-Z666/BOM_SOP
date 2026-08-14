from __future__ import annotations

import math


class FramingRecoveryError(ValueError):
    """Raised when a raster span cannot produce a bounded Zoom correction."""


def derive_zoom_for_subject_span(
    *,
    current_zoom: float,
    observed_span: float,
    target_span: float,
    min_zoom: float,
    max_zoom: float,
) -> float:
    """Derive orthographic Zoom from the measured centered subject span."""

    values = (current_zoom, observed_span, target_span, min_zoom, max_zoom)
    if not all(math.isfinite(value) for value in values):
        raise FramingRecoveryError("Zoom derivation values must be finite")
    if not 0.0 < observed_span <= 1.0:
        raise FramingRecoveryError("observed span must be in (0, 1]")
    if not 0.0 < target_span <= 1.0:
        raise FramingRecoveryError("target span must be in (0, 1]")
    if not 0.0 < min_zoom <= current_zoom <= max_zoom:
        raise FramingRecoveryError("current Zoom is outside its bounds")
    derived = current_zoom * target_span / observed_span
    return min(max(derived, min_zoom), max_zoom)
