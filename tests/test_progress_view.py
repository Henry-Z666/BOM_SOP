from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from sop_pipeline.agent.bundle_paths import (
    bundled_creo_script,
    materialized_creo_script,
)
from sop_pipeline.agent.progress import write_progress
from sop_pipeline.desktop.run_view import progress_snapshot, review_packet


class ProgressViewTests(unittest.TestCase):
    def test_progress_publish_retries_transient_windows_replace_denial(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            run_workspace = workspace / "runs" / "run-1"
            original_replace = Path.replace
            attempts = 0

            def transient_denial(path: Path, target: Path):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError(5, "Access is denied", str(target))
                return original_replace(path, target)

            with patch.object(Path, "replace", transient_denial):
                write_progress(
                    run_workspace,
                    run_id="run-1",
                    skill="normalize-bom",
                    state="RUNNING",
                )

            snapshot = progress_snapshot(workspace, "run-1")
            temporary_files = list(
                (run_workspace / "internal").glob("progress.json.*.tmp")
            )

        self.assertEqual(attempts, 2)
        self.assertEqual(snapshot["skill"], "normalize-bom")
        self.assertEqual(temporary_files, [])

    def test_progress_snapshot_reports_skill_stage(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            run_workspace = workspace / "runs" / "run-1"
            write_progress(
                run_workspace,
                run_id="run-1",
                skill="map-bom-cad",
                state="RUNNING",
            )

            snapshot = progress_snapshot(workspace, "run-1")

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["percent"], 30)
        self.assertEqual(snapshot["stage"], "映射 BOM 与 CAD 零部件")

    def test_render_progress_uses_atomic_checkpoint_counts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            run_workspace = workspace / "runs" / "run-1"
            (run_workspace / "plans").mkdir(parents=True)
            (run_workspace / "internal").mkdir(parents=True)
            tasks = [
                {"step_id": f"step-{index}", "payload": {"execution_mode": "formal"}}
                for index in range(10)
            ]
            (run_workspace / "plans" / "locked-render-jobs-0001.json").write_text(
                json.dumps({"tasks": tasks}), encoding="utf-8"
            )
            (run_workspace / "internal" / "render-checkpoint-0001.json").write_text(
                json.dumps({"steps": [{"step_id": f"step-{index}"} for index in range(4)]}),
                encoding="utf-8",
            )
            write_progress(
                run_workspace,
                run_id="run-1",
                skill="render-batch",
                state="RUNNING",
            )

            snapshot = progress_snapshot(workspace, "run-1")

        self.assertEqual(snapshot["completed_tasks"], 4)
        self.assertEqual(snapshot["total_tasks"], 10)
        self.assertEqual(snapshot["percent"], 68)
        self.assertIn("4 / 10", snapshot["detail"])

    def test_frozen_creo_scripts_resolve_inside_meipass(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(sys, "_MEIPASS", folder, create=True):
                path = bundled_creo_script("run_agent_native_batch.ps1")

        self.assertEqual(
            path,
            Path(folder) / "creo_java" / "run_agent_native_batch.ps1",
        )

    def test_frozen_creo_runtime_is_materialized_outside_transient_meipass(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            transient = root / "_MEI-old"
            run_workspace = root / "run"
            required = (
                "creo_java/run_input_discovery.ps1",
                "creo_java/run_agent_native_batch.ps1",
                "creo_java/stop_agent_native_worker.ps1",
                "creo_java/build/AutoCadDiscovery.class",
                "creo_java/build/NativeArrowWorker.class",
                "scripts/fit_creo_image.ps1",
            )
            for relative in required:
                path = transient / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode("utf-8"))
            with patch.object(sys, "_MEIPASS", str(transient), create=True):
                script = materialized_creo_script(
                    run_workspace, "run_agent_native_batch.ps1"
                )

            self.assertTrue(script.is_file())
            self.assertIn(run_workspace / "internal" / "bundled-runtime", script.parents)
            self.assertNotIn(transient, script.parents)
            self.assertEqual(script.read_text(encoding="utf-8"), "creo_java/run_agent_native_batch.ps1")

    def test_review_packet_exposes_candidates_and_placeholder_paths(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            run_workspace = workspace / "runs" / "run-1"
            (run_workspace / "results").mkdir(parents=True)
            (run_workspace / "rendered").mkdir()
            (run_workspace / "internal" / "validation").mkdir(parents=True)
            candidate = run_workspace / "rendered" / "step-1-candidate-1.png"
            placeholder = run_workspace / "internal" / "validation" / "step-2-placeholder.png"
            candidate.write_bytes(b"candidate")
            placeholder.write_bytes(b"placeholder")
            (run_workspace / "results" / "validation-0001.json").write_text(
                json.dumps(
                    {
                        "steps": [
                            {"step_id": "step-1", "status": "QUESTIONED", "image_path": "rendered/step-1-candidate-1.png", "issues": ["箭头待确认"]},
                            {"step_id": "step-2", "status": "FAILED", "image_path": "internal/validation/step-2-placeholder.png", "issues": []},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (run_workspace / "results" / "candidate-set-0001.json").write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "step_id": "step-1",
                                "candidates": [
                                    {"candidate_id": "candidate-1", "image_path": "rendered/step-1-candidate-1.png", "recommended": True}
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            packet = review_packet(workspace, "run-1")

        self.assertEqual(packet["candidate_count"], 1)
        self.assertEqual(packet["items"][0]["kind"], "candidate")
        self.assertTrue(packet["items"][0]["image_path"].endswith("step-1-candidate-1.png"))
        self.assertEqual(packet["items"][1]["kind"], "placeholder")
        self.assertIn("基础几何硬门", packet["items"][1]["issues"][0])

    def test_review_packet_explains_when_no_candidates_passed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            results = workspace / "runs" / "run-1" / "results"
            results.mkdir(parents=True)
            (results / "validation-0001.json").write_text(
                json.dumps({"steps": [{"step_id": "step-9", "status": "FAILED", "image_path": ""}]}),
                encoding="utf-8",
            )
            (results / "candidate-set-0001.json").write_text(
                json.dumps({"groups": []}), encoding="utf-8"
            )

            packet = review_packet(workspace, "run-1")

        self.assertEqual(packet["candidate_count"], 0)
        self.assertIn("没有候选图通过", packet["message"])


if __name__ == "__main__":
    unittest.main()
