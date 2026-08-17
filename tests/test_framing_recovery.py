from __future__ import annotations

import unittest

from sop_pipeline.agent.framing_recovery import (
    FramingRecoveryError,
    derive_progressive_zoom_for_subject_span,
    derive_zoom_for_subject_span,
)


class FramingRecoveryTests(unittest.TestCase):
    def test_derives_zoom_from_centered_raster_span(self) -> None:
        zoom = derive_zoom_for_subject_span(
            current_zoom=2.3,
            observed_span=0.4975,
            target_span=0.55,
            min_zoom=0.4,
            max_zoom=3.2,
        )
        self.assertAlmostEqual(zoom, 2.5427135678)

    def test_clamps_to_contract_and_rejects_invalid_span(self) -> None:
        self.assertEqual(
            derive_zoom_for_subject_span(
                current_zoom=2.3,
                observed_span=0.2,
                target_span=0.55,
                min_zoom=0.4,
                max_zoom=3.2,
            ),
            3.2,
        )
        with self.assertRaises(FramingRecoveryError):
            derive_zoom_for_subject_span(
                current_zoom=1.0,
                observed_span=0.0,
                target_span=0.55,
                min_zoom=0.4,
                max_zoom=3.2,
            )

    def test_progressive_zoom_distributes_measured_ratio_over_remaining_rounds(self) -> None:
        first = derive_progressive_zoom_for_subject_span(
            current_zoom=1.0,
            observed_span=0.2,
            target_span=0.55,
            min_zoom=0.4,
            max_zoom=3.2,
            remaining_rounds=2,
        )
        second = derive_progressive_zoom_for_subject_span(
            current_zoom=first,
            observed_span=0.2 * first,
            target_span=0.55,
            min_zoom=0.4,
            max_zoom=3.2,
            remaining_rounds=1,
        )

        self.assertAlmostEqual(first, 2.75**0.5)
        self.assertAlmostEqual(second, 2.75)

        with self.assertRaises(FramingRecoveryError):
            derive_progressive_zoom_for_subject_span(
                current_zoom=1.0,
                observed_span=0.2,
                target_span=0.55,
                min_zoom=0.4,
                max_zoom=3.2,
                remaining_rounds=0,
            )


if __name__ == "__main__":
    unittest.main()
