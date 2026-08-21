from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw

from sop_pipeline.camera_visibility import (
    VisibilityThresholds,
    audit_camera_visibility,
    select_camera_from_visibility_audits,
    visibility_contract,
)
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


class SameSessionVisibilityRunner(NativeRecordingRunner):
    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        if "-Operation" in command and command[command.index("-Operation") + 1] == "Visibility":
            self.commands.append(command)
            workspace = Path(command[command.index("-RunWorkspaceRoot") + 1])
            _write_visibility_rasters(workspace)
            worker_root = Path(command[command.index("-WorkerRoot") + 1])
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    f"[AGENT_RENDER] prepared_models {self.prepared_models}\n"
                    f"[AGENT_RENDER] worker_generation {worker_root / 'generation-test'}\n"
                ),
                stderr="",
            )
        return super().run(command)


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
            "receiver_occurrences": ["10/1"],
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
                "native_selected_fit": {
                    "schema_version": "native-selected-fit/v1",
                    "command": "ProCmdZoomIntoOutline",
                    "selection_scope": "moving_and_receiver_occurrences/v1",
                    "zoom_to_selected_level": 0.85,
                    "level_policy": "fixed_native_selection_margin/v1",
                    "max_commands_per_render": 1,
                    "absolute_pan_zoom_forbidden": True,
                },
                "framing_profile": {
                    "schema_version": "native-selected-framing-policy/v1",
                    "policy": "native_zoom_to_selected/v1",
                    "selection_scope": "moving_and_receiver_occurrences/v1",
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
    def test_optional_visibility_revision_is_safe_for_initial_render_jobs(self) -> None:
        root = Path(__file__).resolve().parents[1]
        batch = (root / "creo_java/run_agent_native_batch.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "$visibilityProperty = "
            "$payload.PSObject.Properties['visibility_enforcement']",
            batch,
        )
        self.assertNotIn(
            "$visibilityEnforcement = $payload.visibility_enforcement",
            batch,
        )

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
        self.assertIn("MAX_RENDER_RASTERS_PER_ATTEMPT = 1", worker)
        self.assertIn("native-framing-audit/v1", batch)
        self.assertIn("$zoom -ne 1.0", batch)

    def test_visibility_masks_use_geometry_exclusion_not_ui_highlight(self) -> None:
        root = Path(__file__).resolve().parents[1]
        renderer = (root / "creo_java/src/RenderAssemblyImage.java").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("selection.Highlight(", renderer)
        self.assertIn("activateVisibilityRep", renderer)
        self.assertIn("restoreDynamicPoses", renderer)
        self.assertIn("ScreenTransform_Create", renderer)
        self.assertIn("applyCamera(assembly, cameraSpec)", renderer)
        self.assertIn("session.FlushCurrentWindow();\n        window.SetScreenTransform", renderer)
        self.assertIn("window.SetScreenTransform", renderer)
        self.assertIn("assignVisibilityGroup", renderer)
        self.assertIn("bestDelta - Math.max(0, secondDelta)", renderer)

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
                budget_task_id=f"{task.task_id}:attempt-1",
            )

        self.assertEqual(result.disposition, "passed")
        self.assertEqual(denied, "RENDER_FRAME_BUDGET_EXCEEDED")
        self.assertEqual(len(runner.commands), 1)

    def test_system_retry_gets_one_new_native_frame(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = native_task()
            plan = RenderPlan("render-plan/v2", (task,))
            runner = NativeRecordingRunner(workspace / "internal/prepared-models")
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=Path("plan.json"),
                runner=runner,
            )
            session = worker.open_session(workspace, plan)

            first = worker.render(session, task, 1)
            second = worker.render(session, task, 2)

        self.assertEqual(first.disposition, "passed")
        self.assertEqual(second.disposition, "passed")
        self.assertEqual(len(runner.commands), 2)

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

    def test_frozen_visibility_audit_does_not_block_formal_render(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = _visibility_task()
            plan = RenderPlan("render-plan/v2", (task,))
            plan_path = _write_plan(workspace, plan, task)
            runner = NativeRecordingRunner(workspace / "internal/prepared-models")
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=plan_path,
                runner=runner,
            )

            result = worker.render(worker.open_session(workspace, plan), task, 1)

        self.assertEqual(result.disposition, "passed")
        self.assertEqual(len(runner.commands), 1)
        self.assertNotIn("-Operation", runner.commands[0])

    def test_visibility_and_formal_render_use_the_same_worker_seam(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = _visibility_task()
            plan = RenderPlan("render-plan/v2", (task,))
            plan_path = _write_plan(workspace, plan, task)
            runner = SameSessionVisibilityRunner(
                workspace / "internal/prepared-models"
            )
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=plan_path,
                runner=runner,
                camera_visibility_enabled=True,
            )
            session = worker.open_session(workspace, plan)

            result = worker.render(session, task, 1)

        self.assertEqual(result.disposition, "passed")
        self.assertEqual(len(runner.commands), 2)
        self.assertEqual(
            [command[command.index("-File") + 1] for command in runner.commands],
            ["native.ps1", "native.ps1"],
        )
        worker_roots = [
            command[command.index("-WorkerRoot") + 1] for command in runner.commands
        ]
        self.assertEqual(worker_roots[0], worker_roots[1])
        self.assertTrue(session.native_worker_active)

    def test_passed_visibility_decision_rewrites_only_the_internal_plan(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = _visibility_task()
            plan = RenderPlan("render-plan/v2", (task,))
            plan_path = _write_plan(workspace, plan, task)
            decision_root = _write_visibility_rasters(workspace)
            runner = NativeRecordingRunner(workspace / "internal/prepared-models")
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=plan_path,
                runner=runner,
                camera_visibility_enabled=True,
            )

            result = worker.render(worker.open_session(workspace, plan), task, 1)
            locked = json.loads(
                (decision_root / "formal-step.locked-plan.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(result.disposition, "passed")
        self.assertEqual(locked["tasks"][0]["payload"]["camera_id"], "fixed_456")
        self.assertEqual(
            locked["tasks"][0]["payload"]["camera_selection"][
                "selected_camera_id"
            ],
            "fixed_456",
        )
        self.assertEqual(len(runner.commands), 1)

    def test_no_eligible_fixed_camera_stops_before_formal_render(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = _visibility_task()
            plan = RenderPlan("render-plan/v2", (task,))
            plan_path = _write_plan(workspace, plan, task)
            decision_root = _write_visibility_rasters(workspace, both_fail=True)
            runner = NativeRecordingRunner(workspace / "internal/prepared-models")
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=plan_path,
                runner=runner,
                camera_visibility_enabled=True,
            )

            result = worker.render(worker.open_session(workspace, plan), task, 1)
            decision = json.loads(
                (decision_root / "formal-step.decision.json").read_text(
                    encoding="utf-8"
                )
            )
            diagnostic = worker.diagnostic_for(task.task_id)

        self.assertEqual(result.disposition, "failed")
        self.assertEqual(result.error_code, "NO_ELIGIBLE_FIXED_CAMERA")
        self.assertEqual(decision["status"], "needs_resolution")
        self.assertTrue(decision["options"])
        self.assertEqual(
            diagnostic["schema_version"], "camera-resolution-request/v1"
        )
        self.assertEqual(
            [item["option_id"] for item in diagnostic["resolution_options"]],
            [
                "increase_bounded_explosion_distance",
                "focus_receiver_interface",
                "defer_product_camera_calibration",
            ],
        )
        self.assertEqual(runner.commands, [])

    def test_existing_decision_file_is_overwritten_from_raster_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = _visibility_task()
            plan = RenderPlan("render-plan/v2", (task,))
            plan_path = _write_plan(workspace, plan, task)
            decision_root = _write_visibility_rasters(workspace)
            decision_path = decision_root / "formal-step.decision.json"
            decision_path.write_text(
                json.dumps(
                    {
                        "schema_version": "camera-selection-decision/v1",
                        "status": "selected",
                        "selected_camera_id": "fixed_123",
                        "audits": [],
                    }
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
                camera_visibility_enabled=True,
            )

            result = worker.render(worker.open_session(workspace, plan), task, 1)
            decision = json.loads(decision_path.read_text(encoding="utf-8"))

        self.assertEqual(result.disposition, "passed")
        self.assertEqual(decision["selected_camera_id"], "fixed_456")
        self.assertEqual(len(decision["audits"]), 2)
        self.assertEqual(len(runner.commands), 1)


def _visibility_task() -> RenderTask:
    task = native_task()
    task.payload["camera_catalog"]["fixed_456"] = {
        "id": "fixed_456",
        "position_direction_root": [-1.0, 0.0, 0.0],
        "up_reference_root": [0.0, 1.0, 0.0],
    }
    task.payload["camera_visibility"] = visibility_contract(
        {"10/2": 1}, {"10/1": 2}
    )
    task.payload["camera_visibility"]["status"] = "ready"
    return task


def _write_plan(workspace: Path, plan: RenderPlan, task: RenderTask) -> Path:
    path = workspace / "locked-render-jobs.json"
    path.write_text(
        json.dumps({"schema_version": plan.schema_version, "tasks": [asdict(task)]}),
        encoding="utf-8",
    )
    return path


def _visibility_decision():
    isolated = np.zeros((20, 20), dtype=np.uint32)
    isolated[:10, :10] = 1
    isolated[10:, :10] = 2

    def audit(camera_id: str, moving: int, receiver: int):
        staged = np.zeros((20, 20), dtype=np.uint32)
        staged.flat[:moving] = 1
        staged.flat[200 : 200 + receiver] = 2
        return audit_camera_visibility(
            camera_id=camera_id,
            isolated_labels=isolated,
            staged_labels=staged,
            moving_labels=(1,),
            receiver_labels=(2,),
            thresholds=VisibilityThresholds(),
        )

    return select_camera_from_visibility_audits(
        (audit("fixed_123", 90, 20), audit("fixed_456", 80, 80))
    )


def _write_visibility_rasters(workspace: Path, *, both_fail: bool = False) -> Path:
    root = workspace / "internal" / "camera-visibility"
    root.mkdir(parents=True)
    decision = (
        select_camera_from_visibility_audits(
            (
                _audit_for_raster("fixed_123", 20, 20),
                _audit_for_raster("fixed_456", 20, 20),
            )
        )
        if both_fail
        else _visibility_decision()
    )
    for audit in decision.audits:
        isolated = np.zeros((20, 20), dtype=np.uint32)
        isolated[:10, :10] = 1
        isolated[10:, :10] = 2
        staged = np.zeros((20, 20), dtype=np.uint32)
        for item in (*audit.moving, *audit.receivers):
            if item.label == 1:
                staged.flat[: item.visible_pixels] = 1
            else:
                staged.flat[200 : 200 + item.visible_pixels] = 2
        for kind, labels in (("isolated", isolated), ("staged", staged)):
            rgb = np.stack(
                (
                    (labels >> 16) & 255,
                    (labels >> 8) & 255,
                    labels & 255,
                ),
                axis=2,
            ).astype(np.uint8)
            Image.fromarray(rgb, "RGB").save(
                root / f"formal-step.{audit.camera_id}.{kind}.png"
            )
    return root


def _audit_for_raster(camera_id: str, moving: int, receiver: int):
    isolated = np.zeros((20, 20), dtype=np.uint32)
    isolated[:10, :10] = 1
    isolated[10:, :10] = 2
    staged = np.zeros((20, 20), dtype=np.uint32)
    staged.flat[:moving] = 1
    staged.flat[200 : 200 + receiver] = 2
    return audit_camera_visibility(
        camera_id=camera_id,
        isolated_labels=isolated,
        staged_labels=staged,
        moving_labels=(1,),
        receiver_labels=(2,),
        thresholds=VisibilityThresholds(),
    )


if __name__ == "__main__":
    unittest.main()
