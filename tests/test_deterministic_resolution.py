from __future__ import annotations

import unittest

from sop_pipeline.agent.deterministic_resolution import structured_step_revision
from sop_pipeline.agent.render_scheduler import RenderPlan, RenderTask
from sop_pipeline.agent.skill_handlers import _apply_step_revision
from sop_pipeline.agent.step_revision import RevisionKind


class DeterministicResolutionTests(unittest.TestCase):
    def test_structured_axis_form_creates_exact_direction_revision(self) -> None:
        revision = structured_step_revision(
            "step-1",
            "该零件沿设备总装Y轴负方向装入",
            2,
            structured_inputs={"axis": "Y", "sign": "负"},
        )

        self.assertEqual(revision.kind, RevisionKind.INSTALLATION_GEOMETRY)
        self.assertEqual(revision.changes, {"direction": [0.0, -1.0, 0.0]})

    def test_free_text_axis_sentence_cannot_generate_coordinates(self) -> None:
        with self.assertRaisesRegex(ValueError, "不从自由文本生成坐标"):
            structured_step_revision(
                "step-1",
                "该零件沿设备总装 Z 轴正方向装入",
                1,
            )

    def test_camera_override_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "固定视角已由根坐标系"):
            structured_step_revision(
                "step-1",
                "使用另一视角",
                1,
                structured_inputs={"camera_id": "fixed_456"},
            )

    def test_camera_resolution_choice_becomes_a_bounded_internal_revision(self) -> None:
        explosion = structured_step_revision(
            "step-1",
            "按所选方式重新生成",
            3,
            structured_inputs={
                "camera_resolution_option": "增加一级爆炸距离后重新比较"
            },
        )
        focus = structured_step_revision(
            "step-1",
            "按所选方式重新生成",
            4,
            structured_inputs={
                "camera_resolution_option": "聚焦移动件与安装接口后重新比较"
            },
        )

        self.assertEqual(explosion.kind, RevisionKind.INSTALLATION_GEOMETRY)
        self.assertEqual(
            explosion.changes,
            {"camera_resolution_option": "increase_bounded_explosion_distance"},
        )
        self.assertEqual(focus.kind, RevisionKind.PRESENTATION)
        self.assertEqual(
            focus.changes,
            {"camera_resolution_option": "focus_receiver_interface"},
        )

    def test_unknown_camera_resolution_choice_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "不属于当前有界选项集"):
            structured_step_revision(
                "step-1",
                "自由修改",
                1,
                structured_inputs={"camera_resolution_option": "随便换个相机"},
            )

    def test_manual_review_choices_become_stable_rerender_revisions(self) -> None:
        expected = {
            "normal_explosion": RevisionKind.INSTALLATION_GEOMETRY,
            "reverse_explosion": RevisionKind.INSTALLATION_GEOMETRY,
            "switch_fixed_camera": RevisionKind.PRESENTATION,
            "rebuild_exact_visibility": RevisionKind.PRESENTATION,
            "increase_explosion_distance": RevisionKind.INSTALLATION_GEOMETRY,
            "decrease_explosion_distance": RevisionKind.INSTALLATION_GEOMETRY,
            "focus_installation_region": RevisionKind.PRESENTATION,
        }

        for option_id, kind in expected.items():
            with self.subTest(option_id=option_id):
                revision = structured_step_revision(
                    "step-1",
                    "人工选择二次生成",
                    5,
                    structured_inputs={"rerender_option": option_id},
                )
                self.assertEqual(revision.kind, kind)
                self.assertEqual(revision.changes, {"rerender_option": option_id})

    def test_unknown_manual_rerender_choice_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "二次生成选项"):
            structured_step_revision(
                "step-1",
                "人工选择二次生成",
                1,
                structured_inputs={"rerender_option": "invent_script"},
            )

    def test_camera_resolution_revision_changes_only_the_bounded_contract(self) -> None:
        base = RenderTask(
            task_id="step-1",
            step_id="step-1",
            main_process_id="main-1",
            depends_on=(),
            complete_state_hash="sha256:state",
            blocks_dependents_on_failure=False,
            payload={
                "execution_mode": "formal",
                "translation_vector_root": [0.0, 0.0, 100.0],
                "receiver_point_root": [0.0, 0.0, 0.0],
                "receiver_normal_root": [0.0, 0.0, 1.0],
                "camera_id": "fixed_123",
                "allowed_camera_ids": ["fixed_123", "fixed_456"],
                "visible_occurrences": ["1/1", "1/2", "2/1"],
                "receiver_occurrences": ["1/1"],
                "constraint_ids": ["constraint:1"],
                "arrow_anchors": [
                    {
                        "complete_point_root": [1.0, 2.0, 3.0],
                        "expected_exploded_point_root": [1.0, 2.0, 103.0],
                    }
                ],
                "camera_selection": {"selected_camera_id": "fixed_123"},
                "camera_catalog": {
                    "fixed_123": {
                        "position_direction_root": [1.0, 1.0, 1.0],
                        "up_reference_root": [0.0, 0.0, 1.0],
                    },
                    "fixed_456": {
                        "position_direction_root": [-1.0, -1.0, -1.0],
                        "up_reference_root": [0.0, 0.0, 1.0],
                    },
                },
                "presentation": {
                    "variants": [
                        {
                            "variant_id": "base",
                            "camera_id": "fixed_123",
                            "zoom": 1.0,
                            "pan": [0.0, 0.0],
                        }
                    ],
                    "native_selected_fit": {"zoom_to_selected_level": 0.85},
                },
            },
        )
        plan = RenderPlan("render-plan/v2", (base,))

        exploded = _apply_step_revision(
            plan,
            {
                "revision": 2,
                "step_id": "step-1",
                "kind": "installation_geometry",
                "changes": {
                    "camera_resolution_option": "increase_bounded_explosion_distance"
                },
            },
        ).tasks[0]
        focused = _apply_step_revision(
            plan,
            {
                "revision": 3,
                "step_id": "step-1",
                "kind": "presentation",
                "changes": {"camera_resolution_option": "focus_receiver_interface"},
            },
        ).tasks[0]

        self.assertEqual(exploded.payload["translation_vector_root"], [0.0, 0.0, 115.0])
        self.assertEqual(
            exploded.payload["arrow_anchors"][0]["expected_exploded_point_root"],
            [1.0, 2.0, 118.0],
        )
        self.assertNotIn("camera_selection", exploded.payload)
        self.assertEqual(
            focused.payload["presentation"]["native_selected_fit"][
                "zoom_to_selected_level"
            ],
            0.95,
        )
        self.assertNotIn("camera_selection", focused.payload)

    def test_manual_rerender_options_rewrite_real_render_contract_fields(self) -> None:
        base = RenderTask(
            task_id="step-1",
            step_id="step-1",
            main_process_id="main-1",
            depends_on=(),
            complete_state_hash="sha256:state",
            blocks_dependents_on_failure=False,
            payload={
                "execution_mode": "formal",
                "translation_vector_root": [30.0, 40.0, 0.0],
                "receiver_normal_root": [1.0, 0.0, 0.0],
                "camera_id": "fixed_123",
                "allowed_camera_ids": ["fixed_123", "fixed_456"],
                "camera": {"position_direction_root": [1.0, 1.0, 1.0]},
                "camera_catalog": {
                    "fixed_123": {"position_direction_root": [1.0, 1.0, 1.0]},
                    "fixed_456": {"position_direction_root": [-1.0, -1.0, -1.0]},
                },
                "moving_occurrences": ["2/1"],
                "receiver_occurrences": ["1/1"],
                "visible_occurrences": ["1/1", "1/2", "2/1"],
                "constraint_ids": ["constraint:1"],
                "arrow_anchors": [
                    {
                        "occurrence_id": "2/1",
                        "complete_point_root": [1.0, 2.0, 3.0],
                        "expected_exploded_point_root": [31.0, 42.0, 3.0],
                    }
                ],
                "presentation": {
                    "variants": [
                        {
                            "variant_id": "base",
                            "camera_id": "fixed_123",
                            "zoom": 1.0,
                            "pan": [0.0, 0.0],
                        }
                    ],
                    "native_selected_fit": {"zoom_to_selected_level": 0.85},
                },
            },
        )
        plan = RenderPlan("render-plan/v2", (base,))

        def revised(option_id: str, revision: int = 2) -> RenderTask:
            return _apply_step_revision(
                plan,
                {
                    "revision": revision,
                    "step_id": "step-1",
                    "kind": (
                        "installation_geometry"
                        if option_id
                        in {
                            "normal_explosion",
                            "reverse_explosion",
                            "increase_explosion_distance",
                            "decrease_explosion_distance",
                        }
                        else "presentation"
                    ),
                    "changes": {"rerender_option": option_id},
                },
            ).tasks[0]

        normal = revised("normal_explosion")
        self.assertEqual(normal.payload["translation_vector_root"], [50.0, 0.0, 0.0])
        self.assertEqual(
            normal.payload["arrow_anchors"][0]["expected_exploded_point_root"],
            [51.0, 2.0, 3.0],
        )

        reversed_task = revised("reverse_explosion")
        self.assertEqual(reversed_task.payload["translation_vector_root"], [-30.0, -40.0, 0.0])
        self.assertEqual(
            reversed_task.payload["arrow_anchors"][0]["expected_exploded_point_root"],
            [-29.0, -38.0, 3.0],
        )

        switched = revised("switch_fixed_camera")
        self.assertEqual(switched.payload["camera_id"], "fixed_456")
        self.assertEqual(
            switched.payload["presentation"]["variants"][0]["camera_id"],
            "fixed_456",
        )
        self.assertEqual(switched.payload["camera"], switched.payload["camera_catalog"]["fixed_456"])

        visibility = revised("rebuild_exact_visibility")
        self.assertEqual(
            visibility.payload["visibility_enforcement"],
            {
                "schema_version": "visibility-enforcement/v1",
                "mode": "rebuild_exact_exclusions/v1",
                "exact_visible_occurrences": ["1/1", "1/2", "2/1"],
                "revision": 2,
            },
        )

        increased = revised("increase_explosion_distance")
        self.assertEqual(increased.payload["translation_vector_root"], [34.5, 46.0, 0.0])
        decreased = revised("decrease_explosion_distance")
        self.assertEqual(decreased.payload["translation_vector_root"], [25.5, 34.0, 0.0])

        focused = revised("focus_installation_region")
        self.assertEqual(
            focused.payload["presentation"]["native_selected_fit"]["zoom_to_selected_level"],
            0.95,
        )

    def test_free_text_cannot_invent_occurrence_mapping(self) -> None:
        with self.assertRaisesRegex(ValueError, "BOM/Creo 唯一映射"):
            structured_step_revision(
                "step-1",
                "将螺栓安装到底座",
                1,
                structured_inputs={"moving_name": "螺栓", "receiver_name": "底座"},
            )


if __name__ == "__main__":
    unittest.main()
