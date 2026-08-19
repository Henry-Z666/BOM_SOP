from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor
from typing import Sequence


@dataclass(frozen=True)
class PixelSize:
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("image dimensions must be positive")


@dataclass(frozen=True)
class ImagePlacement:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


def balanced_page_sizes(item_count: int, max_per_page: int = 6) -> tuple[int, ...]:
    """Use the fewest pages, then distribute items without orphan pages."""

    if item_count < 1:
        raise ValueError("item_count must be positive")
    if max_per_page < 1:
        raise ValueError("max_per_page must be positive")
    page_count = ceil(item_count / max_per_page)
    base, remainder = divmod(item_count, page_count)
    return tuple(base + (1 if index < remainder else 0) for index in range(page_count))


def plan_page_layout(
    image_sizes: Sequence[PixelSize],
    *,
    zone_width: int = 720,
    zone_height: int = 500,
    gap: int = 8,
    padding: int = 6,
) -> tuple[ImagePlacement, ...]:
    """Return a centered, tight, order-preserving layout for one SOP image zone."""

    sizes = tuple(image_sizes)
    if not 1 <= len(sizes) <= 6:
        raise ValueError("a page supports between 1 and 6 images")
    if zone_width <= 0 or zone_height <= 0:
        raise ValueError("image zone dimensions must be positive")
    if gap < 0 or padding < 0:
        raise ValueError("gap and padding cannot be negative")
    inner_width = zone_width - 2 * padding
    inner_height = zone_height - 2 * padding
    if inner_width <= 0 or inner_height <= 0:
        raise ValueError("padding leaves no usable image area")

    candidates = _row_patterns(len(sizes))
    pattern, scale = max(
        (
            (pattern, _scale_for_pattern(sizes, pattern, inner_width, inner_height, gap))
            for pattern in candidates
        ),
        key=lambda item: (item[1], -len(item[0]), -max(item[0]) + min(item[0])),
    )
    if scale <= 0:
        raise ValueError("images cannot fit inside the configured image zone")

    scaled = tuple(
        PixelSize(
            max(1, floor(size.width * scale)),
            max(1, floor(size.height * scale)),
        )
        for size in sizes
    )
    rows: list[tuple[PixelSize, ...]] = []
    cursor = 0
    for row_count in pattern:
        rows.append(scaled[cursor : cursor + row_count])
        cursor += row_count

    row_heights = tuple(max(item.height for item in row) for row in rows)
    block_height = sum(row_heights) + gap * (len(rows) - 1)
    y = padding + (inner_height - block_height) // 2
    placements: list[ImagePlacement] = []
    for row, row_height in zip(rows, row_heights, strict=True):
        row_width = sum(item.width for item in row) + gap * (len(row) - 1)
        x = padding + (inner_width - row_width) // 2
        for item in row:
            placements.append(
                ImagePlacement(
                    x=x,
                    y=y + (row_height - item.height) // 2,
                    width=item.width,
                    height=item.height,
                )
            )
            x += item.width + gap
        y += row_height + gap

    result = tuple(placements)
    _validate_layout(result, zone_width=zone_width, zone_height=zone_height, gap=gap)
    return result


def _row_patterns(count: int) -> tuple[tuple[int, ...], ...]:
    patterns: list[tuple[int, ...]] = [(count,)]
    for first_row in range(count - 1, 0, -1):
        second_row = count - first_row
        if 1 <= second_row <= first_row:
            patterns.append((first_row, second_row))
    return tuple(patterns)


def _scale_for_pattern(
    sizes: tuple[PixelSize, ...],
    pattern: tuple[int, ...],
    inner_width: int,
    inner_height: int,
    gap: int,
) -> float:
    row_width_limits: list[float] = []
    row_max_heights: list[int] = []
    cursor = 0
    for count in pattern:
        row = sizes[cursor : cursor + count]
        available_width = inner_width - gap * (count - 1)
        row_width_limits.append(available_width / sum(item.width for item in row))
        row_max_heights.append(max(item.height for item in row))
        cursor += count
    available_height = inner_height - gap * (len(pattern) - 1)
    height_limit = available_height / sum(row_max_heights)
    return min((*row_width_limits, height_limit))


def _validate_layout(
    placements: tuple[ImagePlacement, ...],
    *,
    zone_width: int,
    zone_height: int,
    gap: int,
) -> None:
    for item in placements:
        if item.x < 0 or item.y < 0 or item.right > zone_width or item.bottom > zone_height:
            raise ValueError("image placement escapes the SOP image zone")
    for index, left in enumerate(placements):
        for right in placements[index + 1 :]:
            horizontal_overlap = min(left.right, right.right) - max(left.x, right.x)
            vertical_overlap = min(left.bottom, right.bottom) - max(left.y, right.y)
            if horizontal_overlap > 0 and vertical_overlap > 0:
                raise ValueError("image placements overlap")
            if horizontal_overlap > 0:
                vertical_gap = max(left.y, right.y) - min(left.bottom, right.bottom)
                if vertical_gap < gap:
                    raise ValueError("vertical image gap is below the layout contract")
            if vertical_overlap > 0:
                horizontal_gap = max(left.x, right.x) - min(left.right, right.right)
                if horizontal_gap < gap:
                    raise ValueError("horizontal image gap is below the layout contract")
