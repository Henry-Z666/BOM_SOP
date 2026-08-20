from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from sop_pipeline.agent.qwen_adapter import QwenAdvisor
from sop_pipeline.agent.step_revision import RevisionKind


class FakeTransport:
    def __init__(self, response: str) -> None:
        self.response = response
        self.text_calls = []
        self.vision_calls = []

    def call_text(self, messages, *, seed):
        self.text_calls.append((messages, seed))
        return self.response

    def call_vision(self, image_file, prompt, *, seed):
        self.vision_calls.append((image_file, prompt, seed))
        return self.response


class QwenAdvisorTests(unittest.TestCase):
    def test_invalid_schema_is_corrected_with_a_bounded_retry(self) -> None:
        class RetryTransport(FakeTransport):
            def __init__(self):
                super().__init__('{"type":"presentation"}')
                self.responses = [
                    '{"type":"presentation"}',
                    '{"kind":"presentation","changes":{"camera_id":"fixed_456"}}',
                ]

            def call_text(self, messages, *, seed):
                self.text_calls.append((messages, seed))
                return self.responses.pop(0)

        transport = RetryTransport()
        revision = QwenAdvisor(transport).interpret_resolution(
            "step-1", "换到 fixed_456 固定视角", 1
        )

        self.assertEqual(revision.changes, {"camera_id": "fixed_456"})
        self.assertEqual(len(transport.text_calls), 2)

    def test_pan_revision_is_rejected_by_native_selected_policy(self) -> None:
        transport = FakeTransport(
            '{"kind":"presentation","changes":{"pan":[0.5,0.0]}}'
        )

        with self.assertRaisesRegex(ValueError, "frozen"):
            QwenAdvisor(transport).interpret_resolution(
                "step-1", "向右移动构图", 1
            )

        self.assertEqual(len(transport.text_calls), 3)

    def test_pan_component_aliases_cannot_bypass_frozen_policy(self) -> None:
        transport = FakeTransport(
            '{"kind":"presentation","changes":{"pan_x":0.25,"pan_y":-0.1}}'
        )

        with self.assertRaisesRegex(ValueError, "frozen"):
            QwenAdvisor(transport).interpret_resolution(
                "step-1", "把零件补全并向左调整", 1
            )

    def test_natural_language_resolution_becomes_validated_step_revision(self) -> None:
        transport = FakeTransport(
            json.dumps(
                {
                    "kind": "presentation",
                    "changes": {"camera_id": "fixed_456"},
                }
            )
        )
        revision = QwenAdvisor(transport).interpret_resolution(
            step_id="step-7",
            instruction="换到 fixed_456 固定视角",
            revision=2,
            current_context={"current_camera_id": "fixed_123"},
        )

        self.assertEqual(revision.kind, RevisionKind.PRESENTATION)
        self.assertEqual(revision.changes["camera_id"], "fixed_456")
        self.assertEqual(transport.text_calls[0][1], 7)

    def test_resolution_receives_minimized_current_gate_context(self) -> None:
        transport = FakeTransport(
            '{"kind":"installation_geometry","changes":{"direction":[0,0,1]}}'
        )
        QwenAdvisor(transport).interpret_resolution(
            "step-7",
            "沿Z轴正方向装入",
            3,
            current_context={
                "status": "QUESTIONED",
                "error_code": "DIRECTION_SIGN_WEAK",
                "error_message": "安装方向证据不足",
                "image_kind": "placeholder",
                "moving_occurrences": ["10180/39"],
                "receiver_occurrences": ["12871"],
                "source_bom_items": [
                    {
                        "bom_row": 20,
                        "name": "接头",
                        "drawing_no": "DKBA83165952",
                        "material_code": "M-20",
                        "local_path": "C:/secret/part.prt",
                    }
                ],
                "local_path": "C:/secret/cad",
            },
        )

        sent = json.loads(transport.text_calls[0][0][1]["content"])
        self.assertEqual(
            sent["current_step"]["error_code"], "DIRECTION_SIGN_WEAK"
        )
        self.assertEqual(
            sent["correction_contract"]["installation_geometry"]["direction"]["type"],
            "xyz_vector",
        )
        self.assertIn(
            "direction",
            sent["current_step"]["required_correction_fields"],
        )
        self.assertEqual(
            sent["current_step"]["source_bom_items"],
            [
                {
                    "bom_row": 20,
                    "drawing_no": "DKBA83165952",
                    "material_code": "M-20",
                    "name": "接头",
                }
            ],
        )
        self.assertNotIn("moving_occurrences", sent["current_step"])
        self.assertNotIn("receiver_occurrences", sent["current_step"])
        self.assertIn(
            "user is never required to provide an internal occurrence ID",
            transport.text_calls[0][0][0]["content"],
        )
        self.assertNotIn("local_path", sent["current_step"])
        self.assertNotIn("C:/secret", transport.text_calls[0][0][1]["content"])

    def test_insufficient_placeholder_resolution_returns_actionable_guidance(self) -> None:
        transport = FakeTransport('{"kind":"installation_geometry","changes":{}}')

        with self.assertRaisesRegex(
            ValueError,
            "安装方向.*例如.*沿 Z 轴正方向",
        ):
            QwenAdvisor(transport).interpret_resolution(
                "step-7",
                "调整到正视图重新生成",
                3,
                current_context={
                    "status": "QUESTIONED",
                    "error_code": "DIRECTION_SIGN_WEAK",
                    "error_message": "安装方向证据不足",
                    "image_kind": "placeholder",
                },
            )

        self.assertEqual(len(transport.text_calls), 3)
        retry_prompt = transport.text_calls[1][0][-1]["content"]
        self.assertIn("changes must be a non-empty object", retry_prompt)
        self.assertIn("direction", retry_prompt)

    def test_view_change_cannot_replace_required_geometry_fact(self) -> None:
        transport = FakeTransport(
            '{"kind":"presentation","changes":{"camera_id":"fixed_123"}}'
        )

        with self.assertRaisesRegex(ValueError, "安装方向"):
            QwenAdvisor(transport).interpret_resolution(
                "step-7",
                "调整到正视图重新生成",
                3,
                current_context={
                    "error_code": "DIRECTION_SIGN_WEAK",
                    "image_kind": "placeholder",
                },
            )

        retry_prompt = transport.text_calls[1][0][-1]["content"]
        self.assertIn("missing required correction fields: direction", retry_prompt)

    def test_render_review_sends_only_image_and_minimized_context(self) -> None:
        transport = FakeTransport('{"passed": true, "issues": []}')
        advisor = QwenAdvisor(transport)
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "step.jpg"
            image.write_bytes(b"image")
            decision = advisor.review_render(
                image,
                {"step_title": "安装阀门", "bom_quantity": 1},
            )

        self.assertTrue(decision.passed)
        sent_image, prompt, _ = transport.vision_calls[0]
        self.assertEqual(sent_image, image)
        self.assertIn("安装阀门", prompt)
        self.assertNotIn("CAD", prompt.upper())

    def test_plan_recommendation_sends_only_minimized_bom_semantics(self) -> None:
        transport = FakeTransport(
            '{"decisions":[{"decision_id":"scope-1","recommended":"whole",'
            '"reason":"该总成为外购成品"}]}'
        )
        recommendations = QwenAdvisor(transport).recommend_plan_choices(
            [
                {
                    "decision_id": "scope-1",
                    "assembly_name": "阀体合件",
                    "assembly_text": "安装阀体",
                    "process_text": "整体装入",
                    "child_items": [
                        {"name": "阀芯", "drawing_no": "A-1", "quantity": 1}
                    ],
                    "occurrence_paths": ["51/123"],
                    "local_path": "C:/secret",
                }
            ]
        )

        self.assertEqual(recommendations[0].recommended, "whole")
        sent = transport.text_calls[0][0][1]["content"]
        self.assertIn("阀体合件", sent)
        self.assertNotIn("51/123", sent)
        self.assertNotIn("C:/secret", sent)

    def test_plan_recommendation_rejects_unknown_or_missing_decisions(self) -> None:
        transport = FakeTransport(
            '{"decisions":[{"decision_id":"wrong","recommended":"expand",'
            '"reason":"x"}]}'
        )
        with self.assertRaisesRegex(ValueError, "valid subassembly"):
            QwenAdvisor(transport, max_schema_attempts=1).recommend_plan_choices(
                [{"decision_id": "scope-1", "assembly_name": "合件"}]
            )

    def test_invalid_vision_schema_is_retried_without_adding_more_input_data(self) -> None:
        class RetryVisionTransport(FakeTransport):
            def __init__(self):
                super().__init__('{"result":"bad"}')
                self.responses = [
                    '{"result":"bad"}',
                    '{"passed":false,"issues":["receiver hidden"]}',
                ]

            def call_vision(self, image_file, prompt, *, seed):
                self.vision_calls.append((image_file, prompt, seed))
                return self.responses.pop(0)

        transport = RetryVisionTransport()
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "step.jpg"
            image.write_bytes(b"image")
            review = QwenAdvisor(transport).review_render(
                image, {"step_title": "安装阀门"}
            )

        self.assertFalse(review.passed)
        self.assertEqual(len(transport.vision_calls), 2)
        self.assertEqual(
            transport.vision_calls[0][0], transport.vision_calls[1][0]
        )

    def test_qwen_cannot_return_arbitrary_output_path(self) -> None:
        transport = FakeTransport(
            '{"kind":"presentation","changes":{"output_path":"C:/escape"}}'
        )
        with self.assertRaises(ValueError):
            QwenAdvisor(transport).interpret_resolution("step-1", "保存到这里", 1)

    def test_clear_zoom_instruction_cannot_override_native_selected_fit(self) -> None:
        transport = FakeTransport("这一步应该把安装位置放大显示")

        with self.assertRaises(ValueError):
            QwenAdvisor(transport).interpret_resolution(
                "step-1", "以安装部位为中心放大", 2
            )
        self.assertEqual(len(transport.text_calls), 3)

    def test_qwen_cannot_invent_direction_without_explicit_axis_text(self) -> None:
        transport = FakeTransport(
            '{"kind":"installation_geometry","changes":{"direction":[0,0,1]}}'
        )

        with self.assertRaisesRegex(ValueError, "明确的安装方向"):
            QwenAdvisor(transport).interpret_resolution(
                "step-1",
                "请重新生成",
                2,
                current_context={"error_code": "DIRECTION_SIGN_WEAK"},
            )

    def test_explicit_axis_direction_has_a_bounded_fallback_when_schema_fails(self) -> None:
        transport = FakeTransport(
            '{"kind":"installation_geometry","changes":{}}'
        )

        revision = QwenAdvisor(transport).interpret_resolution(
            "step-1",
            "该零件沿设备 Z 轴正方向装入",
            2,
            current_context={"error_code": "DIRECTION_SIGN_WEAK"},
        )

        self.assertEqual(revision.kind, RevisionKind.INSTALLATION_GEOMETRY)
        self.assertEqual(revision.changes, {"direction": [0.0, 0.0, 1.0]})
        self.assertEqual(len(transport.text_calls), 3)


if __name__ == "__main__":
    unittest.main()
