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


def plan_screen_center_probes(
    *,
    base_pan: tuple[float, float],
    probe_delta: float,
    max_abs_pan: float,
) -> ScreenCenteringProbePlan:
    """Create two same-Zoom orthogonal probes without crossing PAN bounds."""

    if not all(math.isfinite(value) for value in (*base_pan, probe_delta, max_abs_pan)):
        raise ScreenCenteringError("probe inputs must be finite")
    if probe_delta <= 0.0 or max_abs_pan <= 0.0:
        raise ScreenCenteringError("probe delta and PAN bound must be positive")
    if max(abs(base_pan[0]), abs(base_pan[1])) > max_abs_pan:
        raise ScreenCenteringError("base PAN exceeds the presentation contract")

    def probed(value: float) -> float:
        if value + probe_delta <= max_abs_pan:
            return value + probe_delta
        if value - probe_delta >= -max_abs_pan:
            return value - probe_delta
        raise ScreenCenteringError("PAN bound is too narrow for a probe")

    return ScreenCenteringProbePlan(
        base_pan=base_pan,
        x_probe_pan=(probed(base_pan[0]), base_pan[1]),
        y_probe_pan=(base_pan[0], probed(base_pan[1])),
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
