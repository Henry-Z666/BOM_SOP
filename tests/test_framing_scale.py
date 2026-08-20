from __future__ import annotations

import unittest

from sop_pipeline.agent.framing_scale import build_framing_scale_evidence


CAMERA = {
    "position_direction_root": [1.0, 1.0, 1.0],
    "up_reference_root": [0.0, 0.0, 1.0],
}


def bounds(low: list[float], high: list[float]) -> dict:
    return {"status": "available", "min": low, "max": high}


def evidence(
    catalog: dict[str, dict],
    *,
    translation: list[float] | None = None,
    scope: str = "10",
) -> dict:
    return build_framing_scale_evidence(
        occurrence_bounds_root=catalog,
        moving_occurrences=["moving"],
        receiver_occurrences=["receiver"],
        visible_occurrences=["moving", "receiver", "context"],
        translation_vector_root=translation or [0.0, 0.0, 0.0],
        stage_scope_occurrence=scope,
        camera=CAMERA,
    )


class FramingScaleTests(unittest.TestCase):
    def test_activity_includes_complete_exploded_and_receiver_bounds(self) -> None:
        result = evidence(
            {
                "moving": bounds([0.0, 0.0, 0.0], [2.0, 2.0, 2.0]),
                "receiver": bounds([10.0, 0.0, 0.0], [12.0, 2.0, 2.0]),
                "context": bounds([-20.0, -5.0, -5.0], [20.0, 5.0, 5.0]),
            },
            translation=[0.0, 15.0, 0.0],
        )

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["activity_bounds_root"]["min"], [0.0, 0.0, 0.0])
        self.assertEqual(result["activity_bounds_root"]["max"], [12.0, 17.0, 2.0])
        self.assertEqual(result["context_bounds_root"]["min"], [-20.0, -5.0, -5.0])
        self.assertEqual(result["context_bounds_root"]["max"], [20.0, 5.0, 5.0])
        self.assertGreater(
            max(result["installation_projected_size_root"]),
            max(result["moving_projected_size_root"]),
        )

    def test_nearby_scales_reuse_bucket_but_clear_change_invalidates(self) -> None:
        base = {
            "moving": bounds([0.0, 0.0, 0.0], [10.0, 10.0, 10.0]),
            "receiver": bounds([10.0, 0.0, 0.0], [20.0, 10.0, 10.0]),
            "context": bounds([-30.0, -30.0, -30.0], [30.0, 30.0, 30.0]),
        }
        nearby = {
            key: bounds(
                [coordinate * 1.05 for coordinate in value["min"]],
                [coordinate * 1.05 for coordinate in value["max"]],
            )
            for key, value in base.items()
        }
        much_larger_activity = dict(base)
        much_larger_activity["moving"] = bounds([0.0, 0.0, 0.0], [25.0, 25.0, 25.0])
        much_larger_activity["receiver"] = bounds([25.0, 0.0, 0.0], [50.0, 25.0, 25.0])

        first = evidence(base)
        second = evidence(nearby)
        changed = evidence(much_larger_activity)

        self.assertEqual(first["scale_signature"], second["scale_signature"])
        self.assertNotEqual(first["scale_signature"], changed["scale_signature"])

    def test_hierarchy_depth_is_part_of_cache_signature(self) -> None:
        catalog = {
            "moving": bounds([0.0, 0.0, 0.0], [10.0, 10.0, 10.0]),
            "receiver": bounds([10.0, 0.0, 0.0], [20.0, 10.0, 10.0]),
            "context": bounds([-30.0, -30.0, -30.0], [30.0, 30.0, 30.0]),
        }

        shallow = evidence(catalog, scope="10")
        deep = evidence(catalog, scope="10/20")

        self.assertNotEqual(shallow["scale_signature"], deep["scale_signature"])
        self.assertEqual(shallow["scope_depth"], 1)
        self.assertEqual(deep["scope_depth"], 2)

    def test_missing_occurrence_bounds_disables_scale_probe(self) -> None:
        result = evidence(
            {
                "moving": bounds([0.0, 0.0, 0.0], [1.0, 1.0, 1.0]),
                "receiver": bounds([2.0, 0.0, 0.0], [3.0, 1.0, 1.0]),
            }
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["missing_occurrences"], ["context"])


if __name__ == "__main__":
    unittest.main()
