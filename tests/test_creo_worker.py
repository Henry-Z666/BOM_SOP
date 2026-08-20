from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image, ImageDraw

from sop_pipeline.agent.creo_worker import AgentNativeCreoWorker
from sop_pipeline.agent.render_scheduler import RenderPlan, RenderTask


class NativeRecordingRunner:
    def __init__(self, prepared_models: Path, *, tiny: bool = False) -> None:
        self.prepared_models = prepared_models
        self.tiny = tiny
        self.commands: list[list[str]] = []

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if "-OutputFolder" not in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        output = Path(command[command.index("-OutputFolder") + 1])
        output.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (1600, 1600), "white")
        draw = ImageDraw.Draw(image)
        bbox = (740, 740, 840, 840) if self.tiny else (250, 300, 1200, 1150)
        draw.rectangle(bbox, fill=(80, 100, 120))
        draw.line((675, 800, 925, 800), fill=(0, 150, 0), width=8)
        image.save(output / "formal-step.jpg")
        (output / "formal-step.arrow.json").write_text(
            json.dumps(
                {
                    "schema_version": "arrow-projection/v1",
                    "policy": "same_cad_point/v1",
                    "status": "passed",
                    "arrows": [
                        {
                            "covered_occurrences": ["10/2"],
                            "anchor_source": "model_surface",
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


def native_task() -> RenderTask:
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
            "arrow_anchors": [{"occurrence_id": "10/2"}],
            "camera_catalog": {
                "fixed_123": {
                    "id": "fixed_123",
                    "position_direction_root": [1.0, 0.0, 0.0],
                    "up_reference_root": [0.0, 1.0, 0.0],
                }
            },
            "presentation": {
                "schema_version": "fixed-frame-presentation/v1",
                "focus_context": "stage_visible_bbox/v1",
                "framing_priority": "installation_activity/v1",
                "zoom_anchor": "installation_activity_center/v1",
                "native_refit": {
                    "schema_version": "native-focus-refit/v1",
                    "fit_occurrences": "moving_only/v1",
                    "restore_stage_context_without_refit": True,
                },
                "native_selected_fit": {
                    "schema_version": "native-selected-fit/v1",
                    "command": "ProCmdZoomIntoOutline",
                    "selection_scope": "moving_occurrences/v1",
                    "zoom_to_selected_level": 0.28,
                    "level_policy": "cad_installation_envelope/v3",
                    "max_commands_per_render": 1,
                    "absolute_pan_zoom_forbidden": True,
                },
                "framing_profile": {
                    "schema_version": "native-selected-framing-policy/v1",
                    "policy": "native_zoom_to_selected/v1",
                    "scale_signature": "creo_selected_object_bbox/v1",
                    "on_failure": "question_single_frame/v1",
                },
                "center_gate": {
                    "schema_version": "native-composition-center-gate/v1",
                    "target_pixel": [800, 800],
                    "max_activity_center_offset_pixels": 120,
                    "max_arrow_center_offset_pixels": 120,
                },
                "variants": [
                    {
                        "variant_id": "base",
                        "camera_id": "fixed_123",
                        "zoom": 1.0,
                        "pan": [0.0, 0.0],
                    }
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
    def test_native_selected_fit_is_the_only_framing_path(self) -> None:
        root = Path(__file__).resolve().parents[1]
        worker = (root / "src/sop_pipeline/agent/creo_worker.py").read_text(
            encoding="utf-8"
        )
        batch = (root / "creo_java/run_agent_native_batch.ps1").read_text(
            encoding="utf-8"
        )
        renderer = (root / "creo_java/src/RenderAssemblyImage.java").read_text(
            encoding="utf-8"
        )

        self.assertIn('UIGetCommand("ProCmdZoomIntoOutline")', renderer)
        self.assertIn("MAX_RENDER_RASTERS_PER_TASK = 1", worker)
        self.assertIn("$zoom -ne 1.0", batch)

    def test_worker_renders_exactly_one_native_frame(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = native_task()
            plan = RenderPlan("render-plan/v2", (task,))
            plan_path = workspace / "locked-render-jobs.json"
            plan_path.write_text(
                json.dumps(
                    {"schema_version": plan.schema_version, "tasks": [asdict(task)]}
                ),
                encoding="utf-8",
            )
            runner = NativeRecordingRunner(workspace / "internal/prepared-models")
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=plan_path,
                runner=runner,
            )
            session = worker.open_session(workspace, plan)
            result = worker.render(session, task, 1)
            denied = worker._run_batch(
                session,
                plan_path=plan_path,
                output_directory=workspace / "rendered",
                start_index=0,
                count=1,
                variant_index=0,
                budget_task_id=task.task_id,
            )

        self.assertEqual(result.disposition, "passed")
        self.assertEqual(denied, "RENDER_FRAME_BUDGET_EXCEEDED")
        self.assertEqual(len(runner.commands), 1)

    def test_failed_gate_keeps_single_real_frame_for_review(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = native_task()
            plan = RenderPlan("render-plan/v2", (task,))
            runner = NativeRecordingRunner(
                workspace / "internal/prepared-models", tiny=True
            )
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=Path("plan.json"),
                runner=runner,
            )

            result = worker.render(worker.open_session(workspace, plan), task, 1)

        self.assertEqual(result.disposition, "questioned")
        self.assertEqual(result.error_code, "SUBJECT_TOO_SMALL")
        self.assertEqual(len(runner.commands), 1)

    def test_non_native_framing_is_rejected_before_render(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = native_task()
            task.payload["presentation"]["framing_profile"]["policy"] = (
                "manual_refit/v1"
            )
            plan = RenderPlan("render-plan/v2", (task,))
            runner = NativeRecordingRunner(workspace / "internal/prepared-models")
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=Path("plan.json"),
                runner=runner,
            )

            result = worker.render(worker.open_session(workspace, plan), task, 1)

        self.assertEqual(result.disposition, "failed")
        self.assertEqual(result.error_code, "FRAMING_PROFILE_CONTRACT_INVALID")
        self.assertEqual(runner.commands, [])


if __name__ == "__main__":
    unittest.main()
