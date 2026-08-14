from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sop_pipeline.desktop.service import DesktopAgentService


class FakeBackend:
    def __init__(self) -> None:
        self.calls = []

    def start_analysis(self, bom_file, cad_directory):
        self.calls.append(("analyze", bom_file, cad_directory))
        return {
            "run_id": "run-1",
            "packet": {
                "items": [
                    {
                        "item_id": "q1",
                        "category": "CONFIRMATION",
                        "recommended_option": "推荐方案",
                    }
                ]
            },
        }

    def confirm(self, run_id, answers):
        self.calls.append(("confirm", run_id, answers))
        return {"revision": 1}

    def generate(self, run_id):
        self.calls.append(("generate", run_id))
        return {"run_id": run_id, "status": "COMPLETED"}

    def resolve(self, run_id, resolution):
        self.calls.append(("resolve", run_id, resolution))
        return {"run_id": run_id, "status": "COMPLETED"}

    def resume(self, run_id):
        self.calls.append(("resume", run_id))
        return {"run_id": run_id, "status": "GENERATING"}

    def pause(self):
        self.calls.append(("pause",))
        return True


class DesktopAgentServiceTests(unittest.TestCase):
    def test_analysis_and_confirm_generate_run_off_the_ui_call_surface(self) -> None:
        backend = FakeBackend()
        service = DesktopAgentService(backend)
        with tempfile.TemporaryDirectory() as folder:
            result = service.start_analysis(
                Path(folder) / "bom.xlsx", Path(folder) / "cad"
            ).result(timeout=2)
            answers = service.recommended_answers(result["packet"])
            outcome = service.confirm_and_generate("run-1", answers).result(timeout=2)
        service.close()

        self.assertEqual(answers, {"q1": "推荐方案"})
        self.assertEqual(outcome["status"], "COMPLETED")
        self.assertEqual([call[0] for call in backend.calls], ["analyze", "confirm", "generate"])

    def test_candidate_and_natural_language_resolution_share_one_interface(self) -> None:
        backend = FakeBackend()
        service = DesktopAgentService(backend)
        selected = service.resolve_candidate("run-1", "step-2", "candidate-b").result(timeout=2)
        instructed = service.resolve_instruction("run-1", "step-3", "箭头再短一些").result(timeout=2)
        service.close()

        self.assertEqual(selected["status"], "COMPLETED")
        self.assertEqual(instructed["status"], "COMPLETED")
        self.assertEqual(backend.calls[0][2]["candidate_id"], "candidate-b")
        self.assertEqual(backend.calls[1][2]["instruction"], "箭头再短一些")

    def test_pause_is_forwarded_to_the_independent_backend_process(self) -> None:
        backend = FakeBackend()
        service = DesktopAgentService(backend)
        self.assertTrue(service.pause())
        service.close()
        self.assertEqual(backend.calls, [("pause",)])


if __name__ == "__main__":
    unittest.main()
