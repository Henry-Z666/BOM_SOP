from __future__ import annotations

import unittest

from sop_pipeline.agent.sop_layout import (
    PixelSize,
    balanced_page_sizes,
    plan_page_layout,
)


class SopLayoutTests(unittest.TestCase):
    def test_balanced_pages_avoid_single_image_continuations(self) -> None:
        expected = {
            1: (1,),
            6: (6,),
            7: (4, 3),
            8: (4, 4),
            9: (5, 4),
            10: (5, 5),
            11: (6, 5),
            12: (6, 6),
        }

        for count, page_sizes in expected.items():
            with self.subTest(count=count):
                self.assertEqual(balanced_page_sizes(count), page_sizes)

    def test_square_images_use_compact_centered_patterns(self) -> None:
        expected_rows = {
            1: (1,),
            2: (2,),
            3: (3,),
            4: (2, 2),
            5: (3, 2),
            6: (3, 3),
        }

        for count, row_counts in expected_rows.items():
            with self.subTest(count=count):
                placements = plan_page_layout(
                    (PixelSize(1600, 1600),) * count,
                    zone_width=980,
                    zone_height=566,
                )
                self.assertEqual(len(placements), count)
                self.assertEqual(
                    tuple(len(row) for row in _rows(placements)), row_counts
                )
                self.assertEqual(
                    {(item.width, item.height) for item in placements},
                    {(placements[0].width, placements[0].width)},
                )
                self.assertLessEqual(
                    abs((min(item.x for item in placements) + max(item.right for item in placements)) - 980),
                    1,
                )
                self.assertLessEqual(
                    abs((min(item.y for item in placements) + max(item.bottom for item in placements)) - 566),
                    1,
                )
                _assert_tight_non_overlapping(
                    self, placements, zone_width=980, zone_height=566
                )

    def test_layout_preserves_non_square_aspect_ratios(self) -> None:
        placements = plan_page_layout(
            (PixelSize(1600, 1200), PixelSize(1600, 1200), PixelSize(1600, 1200))
        )

        for placement in placements:
            self.assertAlmostEqual(placement.width / placement.height, 4 / 3, places=2)
        _assert_tight_non_overlapping(self, placements)


def _rows(placements):
    grouped: dict[int, list] = {}
    for item in placements:
        grouped.setdefault(item.y, []).append(item)
    return tuple(grouped[key] for key in sorted(grouped))


def _assert_tight_non_overlapping(
    test: unittest.TestCase,
    placements,
    *,
    zone_width: int = 720,
    zone_height: int = 500,
) -> None:
    rows = _rows(placements)
    for row in rows:
        ordered = sorted(row, key=lambda item: item.x)
        for left, right in zip(ordered, ordered[1:]):
            test.assertEqual(right.x - left.right, 8)
    for upper, lower in zip(rows, rows[1:]):
        test.assertEqual(
            min(item.y for item in lower) - max(item.bottom for item in upper),
            8,
        )
    for index, left in enumerate(placements):
        test.assertGreaterEqual(left.x, 0)
        test.assertGreaterEqual(left.y, 0)
        test.assertLessEqual(left.right, zone_width)
        test.assertLessEqual(left.bottom, zone_height)
        for right in placements[index + 1 :]:
            test.assertTrue(
                left.right <= right.x
                or right.right <= left.x
                or left.bottom <= right.y
                or right.bottom <= left.y
            )


if __name__ == "__main__":
    unittest.main()
