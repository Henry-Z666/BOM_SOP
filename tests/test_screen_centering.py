from __future__ import annotations

import unittest

from sop_pipeline.agent.screen_centering import (
    ScreenCenteringError,
    activity_focus_center,
    measure_screen_pan_response,
    plan_screen_center_probes,
    project_lower_left_anchored_zoom_center,
    solve_screen_center_pan,
    solve_with_screen_pan_response,
    update_screen_pan_response,
)


class ScreenCenteringTests(unittest.TestCase):
    def test_projects_activity_center_around_creo_lower_left_zoom_anchor(self) -> None:
        self.assertEqual(
            project_lower_left_anchored_zoom_center(
                current_center=(600.0, 1000.0),
                current_zoom=1.0,
                target_zoom=2.0,
                frame_pixels=(1600, 1600),
            ),
            (1200.0, 400.0),
        )

    def test_focus_center_balances_subject_and_arrow(self) -> None:
        self.assertEqual(
            activity_focus_center((870.0, 800.0), (800.0, 740.0)),
            (835.0, 770.0),
        )

    def test_probe_plan_reverses_at_pan_boundary(self) -> None:
        plan = plan_screen_center_probes(
            base_pan=(0.95, -0.95), probe_delta=0.1, max_abs_pan=1.0
        )
        self.assertEqual(plan.x_probe_pan, (0.85, -0.95))
        self.assertEqual(plan.y_probe_pan, (0.95, -0.85))

    def test_probe_plan_moves_toward_visible_target_using_creo_pan_signs(self) -> None:
        plan = plan_screen_center_probes(
            base_pan=(0.0, 0.0),
            probe_delta=0.1,
            max_abs_pan=1.0,
            target_pixel=(800.0, 800.0),
            base_center=(1500.0, 700.0),
        )
        self.assertEqual(plan.x_probe_pan, (-0.1, 0.0))
        self.assertEqual(plan.y_probe_pan, (0.0, -0.1))

    def test_solves_coupled_pan_response_from_same_zoom_probes(self) -> None:
        result = solve_screen_center_pan(
            target_pixel=(800.0, 800.0),
            base_pan=(0.0, 0.0),
            base_center=(1200.0, 700.0),
            x_probe_pan=(0.1, 0.0),
            x_probe_center=(1380.0, 710.0),
            y_probe_pan=(0.0, 0.1),
            y_probe_center=(1250.0, 580.0),
            max_abs_pan=1.0,
        )

        self.assertAlmostEqual(result.pan[0], -0.1945701357)
        self.assertAlmostEqual(result.pan[1], -0.0995475113)

    def test_measured_response_can_be_reused_without_new_probes(self) -> None:
        response = measure_screen_pan_response(
            base_pan=(0.0, 0.0),
            base_center=(1200.0, 700.0),
            x_probe_pan=(0.1, 0.0),
            x_probe_center=(1380.0, 710.0),
            y_probe_pan=(0.0, 0.1),
            y_probe_center=(1250.0, 580.0),
        )
        result = solve_with_screen_pan_response(
            target_pixel=(800.0, 800.0),
            base_pan=(-0.1, 0.05),
            base_center=(1045.0, 630.0),
            response=response,
            max_abs_pan=1.0,
        )
        self.assertLess(max(abs(value) for value in result.pan), 1.0)

    def test_actual_correction_updates_response_without_second_probe_pair(self) -> None:
        response = measure_screen_pan_response(
            base_pan=(0.0, 0.0),
            base_center=(1500.0, 700.0),
            x_probe_pan=(-0.1, 0.0),
            x_probe_center=(1420.0, 700.0),
            y_probe_pan=(0.0, -0.1),
            y_probe_center=(1490.0, 840.0),
        )
        updated = update_screen_pan_response(
            response=response,
            prior_pan=(0.0, 0.0),
            prior_center=(1500.0, 700.0),
            observed_pan=(-0.85, -0.05),
            observed_center=(500.0, 800.0),
        )
        result = solve_with_screen_pan_response(
            target_pixel=(800.0, 800.0),
            base_pan=(-0.85, -0.05),
            base_center=(500.0, 800.0),
            response=updated,
            max_abs_pan=1.0,
        )
        self.assertGreater(result.pan[0], -0.85)

    def test_rejects_singular_or_out_of_contract_solution(self) -> None:
        common = dict(
            target_pixel=(800.0, 800.0),
            base_pan=(0.0, 0.0),
            base_center=(1200.0, 700.0),
            x_probe_pan=(0.1, 0.0),
            y_probe_pan=(0.0, 0.1),
            max_abs_pan=1.0,
        )
        with self.assertRaises(ScreenCenteringError):
            solve_screen_center_pan(
                **common,
                x_probe_center=(1300.0, 700.0),
                y_probe_center=(1300.0, 700.0),
            )
        with self.assertRaises(ScreenCenteringError):
            solve_screen_center_pan(
                **common,
                x_probe_center=(1210.0, 700.0),
                y_probe_center=(1200.0, 690.0),
            )


if __name__ == "__main__":
    unittest.main()
