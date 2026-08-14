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

    def test_qwen_cannot_return_arbitrary_output_path(self) -> None:
        transport = FakeTransport(
            '{"kind":"presentation","changes":{"output_path":"C:/escape"}}'
        )
        with self.assertRaises(ValueError):
            QwenAdvisor(transport).interpret_resolution("step-1", "保存到这里", 1)


if __name__ == "__main__":
    unittest.main()
