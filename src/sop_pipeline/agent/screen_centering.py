from __future__ import annotations

from dataclasses import dataclass
import math


class ScreenCenteringError(ValueError):
    """Raised when measured PAN probes cannot produce a safe correction."""


@dataclass(frozen=True)
class ScreenCenteringSolution:
    pan: tuple[float, float]
    correction: tuple[float, float]
    determinant: float


@dataclass(frozen=True)
class ScreenCenteringProbePlan:
    base_pan: tuple[float, float]
    x_probe_pan: tuple[float, float]
    y_probe_pan: tuple[float, float]


@dataclass(frozen=True)
class ScreenPanResponse:
    pixels_per_pan_x: tuple[float, float]
    pixels_per_pan_y: tuple[float, float]
    determinant: float


def activity_focus_center(
    subject_center: tuple[float, float],
    arrow_center: tuple[float, float],
) -> tuple[float, float]:
    """Balance the installed context and arrow instead of favoring either one."""

    return (
        (subject_center[0] + arrow_center[0]) / 2.0,
        (subject_center[1] + arrow_center[1]) / 2.0,
    )


def project_lower_left_anchored_zoom_center(
    *,
    current_center: tuple[float, float],
    current_zoom: float,
    target_zoom: float,
    frame_pixels: tuple[int, int],
) -> tuple[float, float]:
    """Predict the same activity point after Creo's lower-left Zoom anchor.

    PAN is applied in exported screen coordinates after the native Zoom.  The
    measured PAN Jacobian can therefore be reused across Zoom values, while
    the anchor displacement itself is an affine projection around the lower
    left corner of the exported frame.
    """

    values = (*current_center, current_zoom, target_zoom, *frame_pixels)
    if not all(math.isfinite(float(value)) for value in values):
        raise ScreenCenteringError("Zoom projection inputs must be finite")
    width, height = frame_pixels
    if current_zoom <= 0.0 or target_zoom <= 0.0 or width <= 0 or height <= 0:
        raise ScreenCenteringError("Zoom projection inputs must be positive")
    ratio = target_zoom / current_zoom
    return (
        ratio * current_center[0],
        height - ratio * (height - current_center[1]),
    )


def plan_screen_center_probes(
    *,
    base_pan: tuple[float, float],
    probe_delta: float,
    max_abs_pan: float,
    target_pixel: tuple[float, float] | None = None,
    base_center: tuple[float, float] | None = None,
) -> ScreenCenteringProbePlan:
    """Create two same-Zoom orthogonal probes without crossing PAN bounds."""

    if not all(math.isfinite(value) for value in (*base_pan, probe_delta, max_abs_pan)):
        raise ScreenCenteringError("probe inputs must be finite")
    if probe_delta <= 0.0 or max_abs_pan <= 0.0:
        raise ScreenCenteringError("probe delta and PAN bound must be positive")
    if max(abs(base_pan[0]), abs(base_pan[1])) > max_abs_pan:
        raise ScreenCenteringError("base PAN exceeds the presentation contract")

    if (target_pixel is None) != (base_center is None):
        raise ScreenCenteringError(
            "probe direction requires both target_pixel and base_center"
        )
    x_direction = 1.0
    y_direction = 1.0
    if target_pixel is not None and base_center is not None:
        x_direction = 1.0 if target_pixel[0] >= base_center[0] else -1.0
        # Creo positive PAN Y moves the raster toward smaller pixel Y.
        y_direction = -1.0 if target_pixel[1] >= base_center[1] else 1.0

    def probed(value: float, direction: float) -> float:
        preferred = value + direction * probe_delta
        if -max_abs_pan <= preferred <= max_abs_pan:
            return preferred
        fallback = value - direction * probe_delta
        if -max_abs_pan <= fallback <= max_abs_pan:
            return fallback
        raise ScreenCenteringError("PAN bound is too narrow for a probe")

    return ScreenCenteringProbePlan(
        base_pan=base_pan,
        x_probe_pan=(probed(base_pan[0], x_direction), base_pan[1]),
        y_probe_pan=(base_pan[0], probed(base_pan[1], y_direction)),
    )


def measure_screen_pan_response(
    *,
    base_pan: tuple[float, float],
    base_center: tuple[float, float],
    x_probe_pan: tuple[float, float],
    x_probe_center: tuple[float, float],
    y_probe_pan: tuple[float, float],
    y_probe_center: tuple[float, float],
) -> ScreenPanResponse:
    """Measure the local Creo-export PAN Jacobian from same-Zoom rasters."""

    dx_pan = x_probe_pan[0] - base_pan[0]
    dy_pan = y_probe_pan[1] - base_pan[1]
    if abs(dx_pan) <= 1.0e-9 or abs(dy_pan) <= 1.0e-9:
        raise ScreenCenteringError("PAN probes must move independent axes")
    if abs(x_probe_pan[1] - base_pan[1]) > 1.0e-9:
        raise ScreenCenteringError("x probe must change only PAN X")
    if abs(y_probe_pan[0] - base_pan[0]) > 1.0e-9:
        raise ScreenCenteringError("y probe must change only PAN Y")
    jx = (
        (x_probe_center[0] - base_center[0]) / dx_pan,
        (x_probe_center[1] - base_center[1]) / dx_pan,
    )
    jy = (
        (y_probe_center[0] - base_center[0]) / dy_pan,
        (y_probe_center[1] - base_center[1]) / dy_pan,
    )
    determinant = jx[0] * jy[1] - jy[0] * jx[1]
    if not all(math.isfinite(value) for value in (*jx, *jy, determinant)):
        raise ScreenCenteringError("PAN response must be finite")
    if abs(determinant) <= 1.0e-6:
        raise ScreenCenteringError("PAN response matrix is singular")
    return ScreenPanResponse(jx, jy, determinant)


def solve_with_screen_pan_response(
    *,
    target_pixel: tuple[float, float],
    base_pan: tuple[float, float],
    base_center: tuple[float, float],
    response: ScreenPanResponse,
    max_abs_pan: float,
) -> ScreenCenteringSolution:
    """Apply a measured response; the result still requires a hard-gate render."""

    j00, j10 = response.pixels_per_pan_x
    j01, j11 = response.pixels_per_pan_y
    error_x = target_pixel[0] - base_center[0]
    error_y = target_pixel[1] - base_center[1]
    correction_x = (error_x * j11 - j01 * error_y) / response.determinant
    correction_y = (j00 * error_y - error_x * j10) / response.determinant
    pan = (base_pan[0] + correction_x, base_pan[1] + correction_y)
    if not all(math.isfinite(value) for value in (*pan, *target_pixel, *base_center)):
        raise ScreenCenteringError("solved PAN must be finite")
    if max_abs_pan <= 0.0 or max(abs(pan[0]), abs(pan[1])) > max_abs_pan:
        raise ScreenCenteringError("solved PAN exceeds the presentation contract")
    return ScreenCenteringSolution(
        pan=pan,
        correction=(correction_x, correction_y),
        determinant=response.determinant,
    )


def update_screen_pan_response(
    *,
    response: ScreenPanResponse,
    prior_pan: tuple[float, float],
    prior_center: tuple[float, float],
    observed_pan: tuple[float, float],
    observed_center: tuple[float, float],
) -> ScreenPanResponse:
    """Apply one Broyden rank-one update from an actual correction render."""

    delta_pan = (
        observed_pan[0] - prior_pan[0],
        observed_pan[1] - prior_pan[1],
    )
    denominator = delta_pan[0] ** 2 + delta_pan[1] ** 2
    if denominator <= 1.0e-12:
        raise ScreenCenteringError("response update needs a nonzero PAN change")
    j00, j10 = response.pixels_per_pan_x
    j01, j11 = response.pixels_per_pan_y
    predicted = (
        j00 * delta_pan[0] + j01 * delta_pan[1],
        j10 * delta_pan[0] + j11 * delta_pan[1],
    )
    residual = (
        observed_center[0] - prior_center[0] - predicted[0],
        observed_center[1] - prior_center[1] - predicted[1],
    )
    updated_j00 = j00 + residual[0] * delta_pan[0] / denominator
    updated_j01 = j01 + residual[0] * delta_pan[1] / denominator
    updated_j10 = j10 + residual[1] * delta_pan[0] / denominator
    updated_j11 = j11 + residual[1] * delta_pan[1] / denominator
    determinant = updated_j00 * updated_j11 - updated_j01 * updated_j10
    values = (updated_j00, updated_j01, updated_j10, updated_j11, determinant)
    if not all(math.isfinite(value) for value in values) or abs(determinant) <= 1.0e-6:
        raise ScreenCenteringError("updated PAN response matrix is singular")
    return ScreenPanResponse(
        pixels_per_pan_x=(updated_j00, updated_j10),
        pixels_per_pan_y=(updated_j01, updated_j11),
        determinant=determinant,
    )


def solve_screen_center_pan(
    *,
    target_pixel: tuple[float, float],
    base_pan: tuple[float, float],
    base_center: tuple[float, float],
    x_probe_pan: tuple[float, float],
    x_probe_center: tuple[float, float],
    y_probe_pan: tuple[float, float],
    y_probe_center: tuple[float, float],
    max_abs_pan: float,
) -> ScreenCenteringSolution:
    """Solve a local 2-D PAN correction from three renders at one fixed Zoom.

    The caller must re-render the solution and repeat with fresh same-Zoom probes
    if the raster center is still outside its gate. Cross-Zoom samples are invalid.
    """

    values = (
        *target_pixel,
        *base_pan,
        *base_center,
        *x_probe_pan,
        *x_probe_center,
        *y_probe_pan,
        *y_probe_center,
        max_abs_pan,
    )
    if not all(math.isfinite(value) for value in values):
        raise ScreenCenteringError("centering samples must be finite")
    if max_abs_pan <= 0.0:
        raise ScreenCenteringError("max_abs_pan must be positive")

    response = measure_screen_pan_response(
        base_pan=base_pan,
        base_center=base_center,
        x_probe_pan=x_probe_pan,
        x_probe_center=x_probe_center,
        y_probe_pan=y_probe_pan,
        y_probe_center=y_probe_center,
    )
    return solve_with_screen_pan_response(
        target_pixel=target_pixel,
        base_pan=base_pan,
        base_center=base_center,
        response=response,
        max_abs_pan=max_abs_pan,
    )
