from __future__ import annotations

import unittest

from sop_pipeline.agent.framing_recovery import (
    FramingRecoveryError,
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


if __name__ == "__main__":
    unittest.main()
