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
            "step-1", "换另一侧固定视角", 1
        )

        self.assertEqual(revision.changes, {"camera_id": "fixed_456"})
        self.assertEqual(len(transport.text_calls), 2)

    def test_natural_language_resolution_becomes_validated_step_revision(self) -> None:
        transport = FakeTransport(
            json.dumps(
                {
                    "kind": "presentation",
                    "changes": {"camera_id": "fixed_456", "zoom": 1.1},
                }
            )
        )
        revision = QwenAdvisor(transport).interpret_resolution(
            step_id="step-7",
            instruction="换到另一侧固定视角并稍微放大",
            revision=2,
        )

        self.assertEqual(revision.kind, RevisionKind.PRESENTATION)
        self.assertEqual(revision.changes["camera_id"], "fixed_456")
        self.assertEqual(transport.text_calls[0][1], 7)

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


if __name__ == "__main__":
    unittest.main()
