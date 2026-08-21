from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from sop_pipeline.desktop.backend import SubprocessAgentBackend
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

    def progress_snapshot(self, run_id=None):
        return {"available": True, "run_id": run_id, "percent": 62, "stage": "正在出图"}

    def review_packet(self, run_id):
        return {"run_id": run_id, "items": [{"step_id": "step-2"}]}


class DesktopAgentServiceTests(unittest.TestCase):
    def test_backend_persists_bounded_worker_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            backend = SubprocessAgentBackend(Path(folder))
            path = backend._persist_worker_log(
                call_token="abc123",
                action="generate",
                run_id="run-1",
                returncode=1,
                stdout="normal output",
                stderr="failure detail",
            )
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], "desktop-agent-worker-log/v1")
        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(payload["stderr_tail"], "failure detail")
        self.assertIn("解决方案", payload["solution"])

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

    def test_informed_override_is_an_explicit_audited_resolution(self) -> None:
        backend = FakeBackend()
        service = DesktopAgentService(backend)

        outcome = service.accept_with_override(
            "run-1",
            "step-4",
            reason="现场确认采用原图",
        ).result(timeout=2)
        service.close()

        self.assertEqual(outcome["status"], "COMPLETED")
        resolution = backend.calls[0][2]
        self.assertEqual(resolution["action"], "accept_with_override")
        self.assertTrue(resolution["metadata"]["acknowledged"])
        self.assertEqual(resolution["metadata"]["reason"], "现场确认采用原图")

    def test_pause_is_forwarded_to_the_independent_backend_process(self) -> None:
        backend = FakeBackend()
        service = DesktopAgentService(backend)
        self.assertTrue(service.pause())
        service.close()
        self.assertEqual(backend.calls, [("pause",)])

    def test_progress_snapshot_is_a_small_synchronous_read_model(self) -> None:
        service = DesktopAgentService(FakeBackend())
        snapshot = service.progress_snapshot("run-1")
        service.close()
        self.assertEqual(snapshot["percent"], 62)
        self.assertEqual(snapshot["run_id"], "run-1")

    def test_review_packet_is_loaded_without_manual_ids(self) -> None:
        service = DesktopAgentService(FakeBackend())
        packet = service.review_packet("run-1")
        service.close()
        self.assertEqual(packet["items"], [{"step_id": "step-2"}])


if __name__ == "__main__":
    unittest.main()
