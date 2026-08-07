"""Deterministic final-pixel installation arrows calibrated from Creo geometry."""
from __future__ import annotations

from collections import deque
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

FRAME = (100, 400, 1700, 2000)
FINAL_SIZE = (1600, 1600)
MIN_ARROW_PIXELS = 24.0
MAX_ARROW_PIXELS = 420.0


def crop_creo(path: Path) -> Image.Image:
    image = Image.open(path).convert("RGB")
    if image.size != (1800, 2400):
        raise ValueError(f"unexpected Creo raster {image.size}: {path}")
    return image.crop(FRAME)


def green_components(image: Image.Image) -> list[np.ndarray]:
    rgb = np.asarray(image)
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    mask = (green > 90) & (green > red * 1.22 + 10) & (green > blue * 1.12 + 10)
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    found: list[np.ndarray] = []
    for y, x in zip(*np.nonzero(mask & ~visited)):
        if visited[y, x]:
            continue
        queue = deque([(int(y), int(x))]); visited[y, x] = True; points = []
        while queue:
            py, px = queue.popleft(); points.append((px, py))
            for ny in range(max(0, py - 1), min(height, py + 2)):
                for nx in range(max(0, px - 1), min(width, px + 2)):
                    if mask[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True; queue.append((ny, nx))
        if len(points) >= 12:
            found.append(np.asarray(points, dtype=float))
    return found


def _endpoints(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    delta = points[:, None, :] - points[None, :, :]
    a, b = np.unravel_index(np.argmax(np.sum(delta * delta, axis=2)), (len(points), len(points)))
    return points[a], points[b]


def _start_and_head(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a, b = _endpoints(points)
    count_a = int(np.sum(np.sum((points - a) ** 2, axis=1) <= 14 ** 2))
    count_b = int(np.sum(np.sum((points - b) ** 2, axis=1) <= 14 ** 2))
    return (b, a) if count_a >= count_b else (a, b)


def _draw_arrow(draw: ImageDraw.ImageDraw, start: np.ndarray, head: np.ndarray) -> None:
    delta = head - start; length = float(np.linalg.norm(delta))
    if not MIN_ARROW_PIXELS <= length <= MAX_ARROW_PIXELS:
        raise ValueError(f"unreadable calibrated arrow length {length:.1f}px")
    direction = delta / length; perpendicular = np.array([-direction[1], direction[0]])
    size, wing, green = 13.0, 6.24, (0, 176, 80)
    draw.line([tuple(start), tuple(head)], fill=green, width=3)
    draw.line([tuple(head), tuple(head - direction * size + perpendicular * wing)], fill=green, width=3)
    draw.line([tuple(head), tuple(head - direction * size - perpendicular * wing)], fill=green, width=3)


def compose(base_path: Path, calibration_path: Path, output_path: Path, expected_count: int) -> None:
    base, calibration = crop_creo(base_path), crop_creo(calibration_path)
    components = green_components(calibration)
    if len(components) != expected_count:
        raise ValueError(f"calibration components={len(components)} expected={expected_count}")
    drawing = ImageDraw.Draw(base)
    for component in components:
        start, head = _start_and_head(component)
        _draw_arrow(drawing, start, head)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    base.save(output_path, quality=95)
