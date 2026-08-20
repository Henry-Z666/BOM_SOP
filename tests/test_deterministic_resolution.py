from __future__ import annotations

import unittest

from sop_pipeline.agent.deterministic_resolution import structured_step_revision
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
