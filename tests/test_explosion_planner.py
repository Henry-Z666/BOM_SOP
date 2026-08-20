import unittest

from sop_pipeline.explosion_planner import select_display_translation


class ExplosionPlannerTests(unittest.TestCase):
    def test_clear_normal_path_remains_interface_normal(self) -> None:
        selected = select_display_translation(
            [0.0, 0.0, 1.0],
            100.0,
            [{"min": [-5.0, -5.0, -2.0], "max": [5.0, 5.0, 2.0]}],
            [{"min": [-10.0, -10.0, -20.0], "max": [10.0, 10.0, -5.0]}],
            [[0.0, 0.0, -2.0]],
        )

        self.assertEqual(selected["mode"], "interface_normal")
        self.assertEqual(selected["translation_vector_root"], [0.0, 0.0, 100.0])

    def test_severe_overlap_does_not_lateralize_compact_part(self) -> None:
        selected = select_display_translation(
            [0.0, 0.0, 1.0],
            100.0,
            [{"min": [-10.0, -9.0, -8.0], "max": [10.0, 9.0, 8.0]}],
            [{"min": [-20.0, -20.0, -200.0], "max": [20.0, 20.0, 200.0]}],
            [[0.0, 0.0, -8.0]],
        )

        self.assertEqual(selected["mode"], "interface_normal")

    def test_flat_symmetric_seal_keeps_its_normal_axis(self) -> None:
        selected = select_display_translation(
            [0.0, 0.0, 1.0],
            100.0,
            [{"min": [-12.0, -12.0, -1.0], "max": [12.0, 12.0, 1.0]}],
            [{"min": [-20.0, -20.0, -200.0], "max": [20.0, 20.0, 200.0]}],
            [[0.0, 0.0, -1.0]],
        )

        self.assertEqual(selected["mode"], "interface_normal")


if __name__ == "__main__":
    unittest.main()
