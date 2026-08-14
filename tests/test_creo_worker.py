from __future__ import annotations

from pathlib import Path
import json
import subprocess
import tempfile
import unittest

from PIL import Image, ImageDraw

from sop_pipeline.agent.creo_worker import AgentNativeCreoWorker, PowerShellCreoWorker
from sop_pipeline.agent.render_scheduler import RenderPlan, RenderTask


class RecordingRunner:
    def __init__(self, prepared_models: Path) -> None:
        self.prepared_models = prepared_models
        self.commands: list[list[str]] = []

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        output_folder = Path(command[command.index("-OutputFolder") + 1])
        output_folder.mkdir(parents=True, exist_ok=True)
        index = int(command[command.index("-StartIndex") + 1])
        (output_folder / f"job-{index}.jpg").write_bytes(f"image-{index}".encode())
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"[BATCH] prepared_isolated_models {self.prepared_models}\n",
            stderr="",
        )


def _task(index: int) -> RenderTask:
    return RenderTask(
        task_id=f"job-{index}",
        step_id=f"job-{index}",
        main_process_id="30",
        depends_on=(),
        complete_state_hash=f"state-{index}",
        payload={"contract_index": index},
    )


class PowerShellCreoWorkerTests(unittest.TestCase):
    def test_one_model_copy_is_reused_for_all_tasks_in_a_session(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            prepared = workspace / "prepared-models"
            runner = RecordingRunner(prepared)
            worker = PowerShellCreoWorker(
                powershell="pwsh",
                stage_script=Path("creo_java/run_stage_batch.ps1"),
                product_config=Path("products/water-tank/product.json"),
                jobs_json=Path("data/runs/corrected-v2-render-jobs.json"),
                runner=runner,
            )
            plan = RenderPlan("render-plan/v1", (_task(0), _task(1)))
            session = worker.open_session(workspace, plan)

            first = worker.render(session, plan.tasks[0], 1)
            second = worker.render(session, plan.tasks[1], 1)
            worker.close_session(session)

        self.assertEqual(first.disposition, "passed")
        self.assertEqual(second.disposition, "passed")
        self.assertNotIn("-PreparedModelsRoot", runner.commands[0])
        self.assertIn("-PreparedModelsRoot", runner.commands[1])
        prepared_arg = runner.commands[1].index("-PreparedModelsRoot") + 1
        self.assertEqual(Path(runner.commands[1][prepared_arg]), prepared)

    def test_task_without_contract_index_is_rejected_before_creo(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            runner = RecordingRunner(workspace / "prepared-models")
            worker = PowerShellCreoWorker(
                powershell="pwsh",
                stage_script=Path("stage.ps1"),
                product_config=Path("product.json"),
                jobs_json=Path("jobs.json"),
                runner=runner,
            )
            task = RenderTask("job", "step", "30", (), "state")
            session = worker.open_session(
                workspace, RenderPlan("render-plan/v1", (task,))
            )
            attempt = worker.render(session, task, 1)

        self.assertEqual(attempt.disposition, "failed")
        self.assertEqual(attempt.error_code, "INVALID_RENDER_TASK")
        self.assertEqual(runner.commands, [])


class NativeRecordingRunner:
    def __init__(
        self,
        prepared_models: Path,
        *,
        fallback: bool = False,
        first_variant_tiny: bool = False,
    ) -> None:
        self.prepared_models = prepared_models
        self.fallback = fallback
        self.first_variant_tiny = first_variant_tiny
        self.commands: list[list[str]] = []

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        output = Path(command[command.index("-OutputFolder") + 1])
        output.mkdir(parents=True, exist_ok=True)
        task_id = "formal-step"
        variant_index = int(command[command.index("-VariantIndex") + 1])
        image = Image.new("RGB", (1600, 1600), "white")
        draw = ImageDraw.Draw(image)
        bbox = (
            (740, 740, 840, 840)
            if self.first_variant_tiny and variant_index == 0
            else (250, 300, 1200, 1150)
        )
        draw.rectangle(bbox, fill=(80, 100, 120))
        draw.line((675, 800, 925, 800), fill=(0, 150, 0), width=8)
        image.save(output / f"{task_id}.jpg")
        (output / f"{task_id}.arrow.json").write_text(
            json.dumps(
                {
                    "schema_version": "arrow-projection/v1",
                    "policy": "same_cad_point/v1",
                    "status": "passed",
                    "arrows": [
                        {
                            "covered_occurrences": ["10/2"],
                            "anchor_source": (
                                "occurrence_origin_fallback" if self.fallback else "model_surface"
                            ),
                            "complete_root": [1.0, 2.0, 3.0],
                            "exploded_root": [1.0, 2.0, 13.0],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"[AGENT_RENDER] prepared_models {self.prepared_models}\n",
            stderr="",
        )


def _native_task() -> RenderTask:
    return RenderTask(
        task_id="formal-step",
        step_id="formal-step",
        main_process_id="process-001",
        depends_on=(),
        complete_state_hash="sha256:state",
        payload={
            "execution_mode": "formal",
            "arrow_renderer": "creo_display_list/v1",
            "plan_index": 0,
            "moving_occurrences": ["10/2"],
            "receiver_normal_root": [1.0, 0.0, 0.0],
            "translation_vector_root": [0.0, 0.0, 10.0],
            "camera": {
                "id": "fixed_123",
                "position_direction_root": [1.0, 0.0, 0.0],
                "up_reference_root": [0.0, 1.0, 0.0],
            },
            "camera_catalog": {
                "fixed_123": {
                    "id": "fixed_123",
                    "position_direction_root": [1.0, 0.0, 0.0],
                    "up_reference_root": [0.0, 1.0, 0.0],
                },
                "fixed_456": {
                    "id": "fixed_456",
                    "position_direction_root": [-1.0, 0.0, 0.0],
                    "up_reference_root": [0.0, 1.0, 0.0],
                },
            },
            "presentation": {
                "schema_version": "fixed-frame-presentation/v1",
                "focus_context": "stage_visible_bbox/v1",
                "framing_priority": "installation_activity/v1",
                "zoom_anchor": "installation_activity_center/v1",
                "centering": {
                    "schema_version": "adaptive-screen-center/v1",
                    "activity_bbox": "subject_plus_native_arrow/v1",
                    "initial_estimate": "cad_activity_origin/v1",
                    "focus_center": "midpoint_subject_arrow/v1",
                    "probe_policy": "on_gate_failure/v1",
                    "response_cache_scope": "camera_zoom_frame_environment/v1",
                    "max_probe_rounds": 2,
                    "target_pixel": [800, 800],
                    "probe_delta": 0.1,
                    "max_abs_pan": 1.0,
                    "max_activity_center_offset_pixels": 120,
                    "max_arrow_center_offset_pixels": 120,
                },
                "variants": [
                    {"variant_id": "base", "camera_id": "fixed_123", "zoom": 1.0, "pan": [0.0, 0.0]},
                    {"variant_id": "zoom-in-50", "camera_id": "fixed_123", "zoom": 1.5, "pan": [0.0, 0.0]},
                    {"variant_id": "zoom-in-110", "camera_id": "fixed_123", "zoom": 2.1, "pan": [0.0, 0.0]},
                    {"variant_id": "zoom-out-15", "camera_id": "fixed_123", "zoom": 0.85, "pan": [0.0, 0.0]},
                ],
                "frame_gate": {
                    "schema_version": "raster-composition-gate/v2",
                    "foreground_delta": 30,
                    "min_component_pixels": 32,
                    "component_downsample": 4,
                    "min_subject_span": 0.54,
                    "max_subject_span": 1.0,
                    "max_clipped_edges": 2,
                    "arrow_green_delta": 20,
                    "min_arrow_pixels": 120,
                    "min_arrow_span_pixels": 24,
                    "min_arrow_border_margin_pixels": 40,
                    "ignored_regions": [[0, 1250, 500, 1600]],
                },
            },
        },
    )


class AgentNativeCreoWorkerTests(unittest.TestCase):
    def test_native_worker_reuses_model_copy_and_validates_arrow_audit(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            prepared = workspace / "internal" / "prepared-models"
            runner = NativeRecordingRunner(prepared)
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("creo_java/run_agent_native_batch.ps1"),
                models_root=Path("cad"),
                render_plan_json=Path("locked-render-jobs.json"),
                runner=runner,
            )
            plan = RenderPlan("render-plan/v2", (_native_task(),))
            session = worker.open_session(workspace, plan)

            first = worker.render(session, plan.tasks[0], 1)
            second = worker.render(session, plan.tasks[0], 1)

        self.assertEqual(first.disposition, "passed")
        self.assertEqual(second.disposition, "passed")
        self.assertNotIn("-ProductConfig", runner.commands[0])
        self.assertNotIn("-PreparedModelsRoot", runner.commands[0])
        self.assertIn("-PreparedModelsRoot", runner.commands[1])
        self.assertEqual(runner.commands[0][runner.commands[0].index("-VariantIndex") + 1], "0")

    def test_occurrence_origin_arrow_cannot_pass_formal_gate(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            runner = NativeRecordingRunner(
                workspace / "internal" / "prepared-models", fallback=True
            )
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=Path("plan.json"),
                runner=runner,
            )
            plan = RenderPlan("render-plan/v2", (_native_task(),))
            attempt = worker.render(worker.open_session(workspace, plan), plan.tasks[0], 1)

        self.assertEqual(attempt.disposition, "failed")
        self.assertEqual(attempt.error_code, "ARROW_SURFACE_ANCHOR_UNAVAILABLE")

    def test_small_subject_retries_only_with_compiled_zoom_in_variant(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            runner = NativeRecordingRunner(
                workspace / "internal" / "prepared-models",
                first_variant_tiny=True,
            )
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=Path("plan.json"),
                runner=runner,
            )
            plan = RenderPlan("render-plan/v2", (_native_task(),))
            session = worker.open_session(workspace, plan)

            first = worker.render(session, plan.tasks[0], 1)
            second = worker.render(session, plan.tasks[0], 2)

        self.assertEqual(first.disposition, "retryable")
        self.assertEqual(first.error_code, "SUBJECT_TOO_SMALL")
        self.assertEqual(second.disposition, "passed")
        indexes = [
            command[command.index("-VariantIndex") + 1]
            for command in runner.commands
        ]
        self.assertEqual(indexes, ["0", "1"])


if __name__ == "__main__":
    unittest.main()
