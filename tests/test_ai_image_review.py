from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sop_pipeline.agent.ai_image_review import AIImageReviewer


class FakeVisionTransport:
    model = "test-vision"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[Path, str, int]] = []

    def call(self, image_file: Path, prompt: str, *, seed: int) -> str:
        self.calls.append((image_file, prompt, seed))
        return self.responses.pop(0)


class AIImageReviewerTests(unittest.TestCase):
    def test_passed_decision_is_accepted_without_confidence_threshold(self) -> None:
        transport = FakeVisionTransport(['{"passed":true,"issues":[]}'])
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "step.png"
            image.write_bytes(b"image")
            decision = AIImageReviewer(transport).review_render(
                image, {"step_id": "step-1", "camera_id": "fixed_123"}
            )

        self.assertTrue(decision.passed)
        self.assertEqual(decision.issues, ())
        self.assertEqual(decision.model, "test-vision")
        self.assertEqual(len(transport.calls), 1)

    def test_invalid_schema_retries_then_routes_a_question_to_review(self) -> None:
        transport = FakeVisionTransport(
            [
                '{"result":"unknown"}',
                '{"passed":false,"issues":["接收位置被遮挡"]}',
            ]
        )
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "step.png"
            image.write_bytes(b"image")
            decision = AIImageReviewer(transport).review_render(
                image, {"step_id": "step-2"}
            )

        self.assertFalse(decision.passed)
        self.assertEqual(decision.issues, ("接收位置被遮挡",))
        self.assertEqual(len(transport.calls), 2)

    def test_passed_decision_cannot_hide_reported_issues(self) -> None:
        transport = FakeVisionTransport(
            [
                '{"passed":true,"issues":["箭头不清楚"]}',
                '{"passed":true,"issues":["箭头不清楚"]}',
            ]
        )
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "step.png"
            image.write_bytes(b"image")
            with self.assertRaisesRegex(ValueError, "未返回有效结果"):
                AIImageReviewer(transport).review_render(image, {})


if __name__ == "__main__":
    unittest.main()
