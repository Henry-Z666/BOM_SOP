from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import ctypes
import json
import os
import subprocess
import sys
import tempfile
from time import perf_counter
import unittest

from PIL import Image, ImageDraw

from sop_pipeline.agent.creo_worker import (
    AgentNativeCreoWorker,
    _effective_pan_bound,
    _screen_pan_response_key,
)
from sop_pipeline.agent.creo_worker import SubprocessCommandRunner
from sop_pipeline.agent.render_scheduler import RenderPlan, RenderTask


def _windows_pid_is_running(pid: int) -> bool:
    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


class CommandRunnerTests(unittest.TestCase):
    def test_command_runner_does_not_wait_for_grandchild_stdout_eof(self) -> None:
        child_code = (
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-c','import time;time.sleep(1.5)'],"
            "stdout=sys.stdout,stderr=sys.stderr,close_fds=False); "
            "print('direct-process-complete')"
        )
        runner = SubprocessCommandRunner(timeout_seconds=1)

        started = perf_counter()
        result = runner.run([sys.executable, "-c", child_code])
        elapsed = perf_counter() - started

        self.assertEqual(result.returncode, 0)
        self.assertIn("direct-process-complete", result.stdout)
        self.assertLess(elapsed, 1.0)

    @unittest.skipUnless(os.name == "nt", "Windows process-tree contract")
    def test_command_timeout_terminates_inherited_grandchild(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            pid_file = Path(folder) / "grandchild.pid"
            grandchild_code = (
                "import os,time; "
                f"open({str(pid_file)!r},'w').write(str(os.getpid())); "
                "time.sleep(30)"
            )
            parent_code = (
                "import subprocess,sys,time; "
                f"subprocess.Popen([sys.executable,'-c',{grandchild_code!r}]); "
                "time.sleep(30)"
            )
            runner = SubprocessCommandRunner(timeout_seconds=1)

            with self.assertRaises(subprocess.TimeoutExpired):
                runner.run([sys.executable, "-c", parent_code])

            self.assertTrue(pid_file.is_file())
            grandchild_pid = int(pid_file.read_text(encoding="utf-8"))
            self.assertFalse(_windows_pid_is_running(grandchild_pid))

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


class PersistentProtocolRunner(NativeRecordingRunner):
    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        if "-OutputFolder" not in command:
            self.commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        result = super().run(command)
        generation = self.prepared_models.parent / "native-worker" / "generation-test"
        return subprocess.CompletedProcess(
            command,
            result.returncode,
            stdout=(result.stdout or "")
            + f"[AGENT_RENDER] worker_generation {generation}\n",
            stderr=result.stderr,
        )


class AdaptiveCenteringRunner:
    def __init__(
        self,
        prepared_models: Path,
        *,
        zoom_sensitive: bool = False,
        hide_arrow_when_off_center: bool = False,
        lower_left_zoom_anchor: bool = False,
        subject_half_size: float = 400.0,
    ) -> None:
        self.prepared_models = prepared_models
        self.zoom_sensitive = zoom_sensitive
        self.hide_arrow_when_off_center = hide_arrow_when_off_center
        self.lower_left_zoom_anchor = lower_left_zoom_anchor
        self.subject_half_size = subject_half_size
        self.commands: list[list[str]] = []

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        output = Path(command[command.index("-OutputFolder") + 1])
        output.mkdir(parents=True, exist_ok=True)
        plan_path = Path(command[command.index("-RenderPlanJson") + 1])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        start = int(command[command.index("-StartIndex") + 1])
        count = int(command[command.index("-Count") + 1])
        variant_index = int(command[command.index("-VariantIndex") + 1])
        for task in plan["tasks"][start : start + count]:
            variant = task["payload"]["presentation"]["variants"][variant_index]
            pan_x, pan_y = (float(value) for value in variant["pan"])
            zoom = float(variant["zoom"])
            if self.lower_left_zoom_anchor:
                # Creo applies PAN in exported screen coordinates after Zoom;
                # its response is therefore reusable across Zoom values.
                center_x = zoom * 1100.0 + 1000.0 * pan_x + 100.0 * pan_y
                center_y = (
                    1600.0
                    - zoom * (1600.0 - 800.0)
                    + 50.0 * pan_x
                    - 900.0 * pan_y
                )
            else:
                center_x = 1100.0 + 1000.0 * pan_x + 100.0 * pan_y
                center_y = 800.0 + 50.0 * pan_x - 900.0 * pan_y
            task_id = task["task_id"]
            image = Image.new("RGB", (1600, 1600), "white")
            draw = ImageDraw.Draw(image)
            half_width = self.subject_half_size * zoom if self.zoom_sensitive else 440.0
            half_height = self.subject_half_size * 0.95 * zoom if self.zoom_sensitive else 425.0
            draw.rectangle(
                (
                    round(center_x - half_width),
                    round(center_y - half_height),
                    round(center_x + half_width),
                    round(center_y + half_height),
                ),
                fill=(80, 100, 120),
            )
            if not self.hide_arrow_when_off_center or center_x <= 1000.0:
                draw.line(
                    (
                        round(center_x - 125),
                        round(center_y),
                        round(center_x + 125),
                        round(center_y),
                    ),
                    fill=(0, 150, 0),
                    width=8,
                )
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
                "native_refit": {
                    "schema_version": "native-focus-refit/v1",
                    "fit_occurrences": "moving_only/v1",
                    "restore_stage_context_without_refit": True,
                },
                "centering": {
                    "schema_version": "adaptive-screen-center/v1",
                    "activity_bbox": "subject_plus_native_arrow/v1",
                    "initial_estimate": "cad_activity_origin/v1",
                    "focus_center": "midpoint_subject_arrow/v1",
                    "probe_policy": "on_gate_failure/v1",
                    "response_cache_scope": "camera_frame_environment/v2",
                    "max_probe_rounds": 2,
                    "target_pixel": [800, 800],
                    "probe_delta": 0.1,
                    "max_abs_pan": 1.0,
                    "max_activity_center_offset_pixels": 120,
                    "max_arrow_center_offset_pixels": 120,
                },
                "zoom_recovery": {
                    "schema_version": "centered-span-zoom/v1",
                    "target_subject_span": 0.55,
                    "min_zoom": 0.4,
                    "max_zoom": 32.0,
                    "max_rounds": 3,
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


def _scale_bucket_task(
    *,
    task_id: str,
    plan_index: int,
    signature: str,
    activity_size: float,
    context_size: float,
) -> RenderTask:
    base = _native_task()
    payload = deepcopy(base.payload)
    payload["plan_index"] = plan_index
    payload["presentation"]["variants"] = [
        {
            "variant_id": "base",
            "camera_id": "fixed_123",
            "zoom": 1.0,
            "pan": [0.0, 0.0],
        }
    ]
    payload["presentation"]["framing_profile"] = {
        "schema_version": "frozen-framing-profile-policy/v2",
        "policy": "freeze_per_scale_bucket/v1",
        "scale_signature": signature,
        "scale_evidence": {
            "schema_version": "cad-framing-scale/v1",
            "status": "available",
            "scale_signature": signature,
            "activity_projected_size_root": [activity_size, activity_size],
            "context_projected_size_root": [context_size, context_size],
        },
        "probe_interface_status": "enabled_test_cad_bounds/v1",
        "on_mismatch": "invalidate_and_recalibrate_once/v1",
        "max_bucket_recalibrations": 1,
    }
    return replace(
        base,
        task_id=task_id,
        step_id=task_id,
        payload=payload,
    )


class AgentNativeCreoWorkerTests(unittest.TestCase):
    def test_native_arrow_display_is_unique_and_cleanup_failure_is_not_ignored(self) -> None:
        root = Path(__file__).resolve().parents[1] / "creo_java" / "src"
        projection = (root / "ArrowProjection.java").read_text(encoding="utf-8")
        renderer = (root / "RenderAssemblyImage.java").read_text(encoding="utf-8")

        self.assertNotIn("CreateDisplayList3D(73101", projection)
        self.assertIn("AtomicInteger", projection)
        self.assertNotIn(
            "if (arrowDisplay != null) try { arrowDisplay.Delete(); } catch (Throwable ignored) {}",
            renderer,
        )
        self.assertIn("ARROW_DISPLAY_CLEANUP_FAILED", renderer)

    def test_native_powershell_chain_propagates_runtime_config(self) -> None:
        root = Path(__file__).resolve().parents[1] / "creo_java"
        batch = (root / "run_agent_native_batch.ps1").read_text(encoding="utf-8")
        worker = (root / "invoke_agent_native_worker.ps1").read_text(encoding="utf-8")
        legacy = (root / "invoke_agent_native_jlink.ps1").read_text(encoding="utf-8")

        self.assertIn("Get-CreoRuntime -ProjectRoot $projectRoot -ConfigPath $RuntimeConfig", batch)
        self.assertIn("build.ps1') -RuntimeConfig $runtime.ConfigPath", batch)
        self.assertEqual(batch.count("-RuntimeConfig \"' + $runtime.ConfigPath + '\"'"), 2)
        self.assertIn("Get-CreoRuntime -ProjectRoot $ProjectRoot -ConfigPath $RuntimeConfig", worker)
        self.assertIn("Get-CreoRuntime -ProjectRoot $ProjectRoot -ConfigPath $RuntimeConfig", legacy)
        self.assertIn("@('formal','diagnostic_preview')", batch)
        self.assertNotIn("candidate_search", batch)
        self.assertIn("max_zoom -ne 32.0", batch)
        self.assertIn("$zoom -gt 32.0", batch)
        self.assertIn("max_rounds -ne 3", batch)

    def test_native_framing_has_a_six_raster_hard_budget(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = _native_task()
            plan = RenderPlan("render-plan/v2", (task,))
            plan_path = workspace / "locked-render-jobs.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "schema_version": plan.schema_version,
                        "tasks": [asdict(task)],
                    }
                ),
                encoding="utf-8",
            )
            runner = AdaptiveCenteringRunner(
                workspace / "internal" / "prepared-models"
            )
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=plan_path,
                runner=runner,
            )
            session = worker.open_session(workspace, plan)
            for _ in range(3):
                self.assertIsNone(
                    worker._run_batch(
                        session,
                        plan_path=plan_path,
                        output_directory=workspace / "rendered",
                        start_index=0,
                        count=2,
                        variant_index=0,
                        budget_task_id=task.task_id,
                    )
                )
            denied = worker._run_batch(
                session,
                plan_path=plan_path,
                output_directory=workspace / "rendered",
                start_index=0,
                count=1,
                variant_index=0,
                budget_task_id=task.task_id,
            )

        self.assertEqual(denied, "FRAMING_FRAME_BUDGET_EXCEEDED")
        self.assertEqual(sum(int(command[command.index("-Count") + 1]) for command in runner.commands), 6)

    def test_pan_response_cache_is_reused_across_zoom_values(self) -> None:
        payload = _native_task().payload
        self.assertEqual(
            _screen_pan_response_key(payload, camera_id="fixed_123", zoom=1.0),
            _screen_pan_response_key(payload, camera_id="fixed_123", zoom=2.75),
        )

    def test_pan_response_cache_is_not_reused_across_scale_buckets(self) -> None:
        first = _scale_bucket_task(
            task_id="first",
            plan_index=0,
            signature="cad-framing-scale/v1:depth=1:activity=10:context=11:ratio=0",
            activity_size=80.0,
            context_size=100.0,
        )
        second = _scale_bucket_task(
            task_id="second",
            plan_index=1,
            signature="cad-framing-scale/v1:depth=2:activity=9:context=11:ratio=1",
            activity_size=70.0,
            context_size=100.0,
        )

        self.assertNotEqual(
            _screen_pan_response_key(first.payload, camera_id="fixed_123", zoom=1.0),
            _screen_pan_response_key(second.payload, camera_id="fixed_123", zoom=1.0),
        )

    def test_pan_bound_scales_from_contract_at_native_zoom(self) -> None:
        self.assertEqual(_effective_pan_bound(1.0, 0.8), 1.0)
        self.assertAlmostEqual(_effective_pan_bound(1.0, 2.65), 2.65)

    def test_native_worker_uses_bounded_session_protocol_and_stops_on_close(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            runner = PersistentProtocolRunner(
                workspace / "internal" / "prepared-models"
            )
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("creo_java/run_agent_native_batch.ps1"),
                models_root=Path("cad"),
                render_plan_json=Path("locked-render-jobs.json"),
                runner=runner,
            )
            plan = RenderPlan("render-plan/v2", (_native_task(),))
            session = worker.open_session(workspace, plan)

            attempt = worker.render(session, plan.tasks[0], 1)
            worker.close_session(session)

        self.assertEqual(attempt.disposition, "passed")
        self.assertEqual(len(runner.commands), 2)
        render_command, stop_command = runner.commands
        self.assertIn("-WorkerRoot", render_command)
        self.assertEqual(
            Path(render_command[render_command.index("-WorkerRoot") + 1]),
            workspace / "internal" / "native-worker",
        )
        self.assertIn("stop_agent_native_worker.ps1", str(stop_command))
        self.assertFalse(session.native_worker_active)

    def test_native_worker_rejects_candidate_search_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            base = _native_task()
            payload = deepcopy(base.payload)
            payload["execution_mode"] = "candidate_search"
            payload["diagnostics"] = ["DIRECTION_SIGN_WEAK"]
            task = replace(base, payload=payload)
            plan = RenderPlan("render-plan/v2", (task,))
            runner = PersistentProtocolRunner(
                workspace / "internal" / "prepared-models"
            )
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("creo_java/run_agent_native_batch.ps1"),
                models_root=Path("cad"),
                render_plan_json=Path("locked-render-jobs.json"),
                runner=runner,
            )
            session = worker.open_session(workspace, plan)

            attempt = worker.render(session, task, 1)

        self.assertEqual(attempt.disposition, "failed")
        self.assertEqual(attempt.error_code, "TASK_NOT_FORMAL")
        self.assertEqual(runner.commands, [])

    def test_weak_direction_cannot_produce_camera_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = _native_task()
            task.payload["execution_mode"] = "candidate_search"
            task.payload["diagnostics"] = ["DIRECTION_SIGN_WEAK"]
            task.payload["presentation"]["framing_profile"] = {
                "policy": "default_refit/v1"
            }
            task.payload["presentation"]["variants"] = [
                {
                    "variant_id": "base",
                    "camera_id": "fixed_123",
                    "zoom": 1.0,
                    "pan": [0.0, 0.0],
                },
                {
                    "variant_id": "flipped-camera",
                    "camera_id": "fixed_456",
                    "zoom": 1.0,
                    "pan": [0.0, 0.0],
                },
            ]
            plan = RenderPlan("render-plan/v2", (task,))
            runner = NativeRecordingRunner(
                workspace / "internal" / "prepared-models"
            )
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=Path("plan.json"),
                runner=runner,
            )
            session = worker.open_session(workspace, plan)

            first = worker.render(session, task, 1)
            candidates = sorted(
                (workspace / "rendered").glob("formal-step-candidate-*.jpg")
            )

        self.assertEqual(first.disposition, "failed")
        self.assertEqual(first.error_code, "TASK_NOT_FORMAL")
        self.assertEqual(len(candidates), 0)
        self.assertEqual(runner.commands, [])

    def test_native_worker_passes_durable_runtime_config_to_render_batch(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            runtime_config = workspace / "internal" / "creo-runtime.json"
            runner = PersistentProtocolRunner(
                workspace / "internal" / "prepared-models"
            )
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("creo_java/run_agent_native_batch.ps1"),
                models_root=Path("cad"),
                render_plan_json=Path("locked-render-jobs.json"),
                runtime_config=runtime_config,
                runner=runner,
            )
            plan = RenderPlan("render-plan/v2", (_native_task(),))

            attempt = worker.render(worker.open_session(workspace, plan), plan.tasks[0], 1)

        self.assertEqual(attempt.disposition, "passed")
        command = runner.commands[0]
        self.assertIn("-RuntimeConfig", command)
        self.assertEqual(
            Path(command[command.index("-RuntimeConfig") + 1]), runtime_config
        )

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

    def test_default_refit_camera_gate_does_not_flip_camera(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = _native_task()
            task.payload["presentation"]["framing_profile"] = {
                "policy": "default_refit/v1"
            }
            task.payload["presentation"]["variants"] = [
                {
                    "variant_id": "base",
                    "camera_id": "fixed_456",
                    "zoom": 1.0,
                    "pan": [0.0, 0.0],
                },
                {
                    "variant_id": "flipped-camera",
                    "camera_id": "fixed_123",
                    "zoom": 1.0,
                    "pan": [0.0, 0.0],
                },
            ]
            plan = RenderPlan("render-plan/v2", (task,))
            runner = NativeRecordingRunner(
                workspace / "internal" / "prepared-models"
            )
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=Path("plan.json"),
                runner=runner,
            )
            session = worker.open_session(workspace, plan)

            first = worker.render(session, task, 1)
        self.assertEqual(first.disposition, "failed")
        self.assertEqual(first.error_code, "CAMERA_RECEIVER_SILHOUETTE")
        self.assertEqual(
            [
                command[command.index("-VariantIndex") + 1]
                for command in runner.commands
            ],
            ["0"],
        )

    def test_default_refit_does_not_create_flipped_camera_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = _native_task()
            task.payload["presentation"]["framing_profile"] = {
                "policy": "default_refit/v1"
            }
            task.payload["camera_catalog"]["fixed_123"][
                "position_direction_root"
            ] = [-1.0, 0.0, 0.0]
            task.payload["presentation"]["variants"] = [
                {
                    "variant_id": "base",
                    "camera_id": "fixed_456",
                    "zoom": 1.0,
                    "pan": [0.0, 0.0],
                },
                {
                    "variant_id": "flipped-camera",
                    "camera_id": "fixed_123",
                    "zoom": 1.0,
                    "pan": [0.0, 0.0],
                },
            ]
            plan = RenderPlan("render-plan/v2", (task,))
            runner = NativeRecordingRunner(
                workspace / "internal" / "prepared-models"
            )
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=Path("plan.json"),
                runner=runner,
            )
            session = worker.open_session(workspace, plan)

            first = worker.render(session, task, 1)
            candidates = sorted(
                (workspace / "rendered").glob(
                    "formal-step-candidate-*.jpg"
                )
            )

        self.assertEqual(first.disposition, "failed")
        self.assertEqual(first.error_code, "CAMERA_RECEIVER_SILHOUETTE")
        self.assertEqual(len(candidates), 0)

    def test_default_refit_policy_never_probes_zooms_or_writes_framing_cache(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = _native_task()
            payload = deepcopy(task.payload)
            payload["presentation"]["framing_profile"] = {
                "schema_version": "frozen-framing-profile-policy/v1",
                "policy": "default_refit/v1",
                "scale_signature": "default/v1",
                "probe_interface_status": "frozen_pending_scale_derivation/v1",
            }
            payload["presentation"]["variants"] = [
                {
                    "variant_id": "base",
                    "camera_id": "fixed_123",
                    "zoom": 1.0,
                    "pan": [0.0, 0.0],
                }
            ]
            task = replace(task, payload=payload)
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

            result = worker.render(
                worker.open_session(workspace, RenderPlan("render-plan/v2", (task,))),
                task,
                1,
            )
            profile_written = (
                workspace
                / "internal"
                / "screen-centering"
                / "frozen-framing-profiles.json"
            ).exists()

        self.assertEqual(result.disposition, "questioned")
        self.assertEqual(result.error_code, "SUBJECT_TOO_SMALL")
        self.assertTrue(result.output_hash)
        self.assertEqual(len(runner.commands), 1)
        self.assertFalse(profile_written)

    def test_manual_refit_is_rejected_while_probe_policy_is_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = _native_task()
            payload = deepcopy(task.payload)
            payload["presentation"]["framing_profile"] = {
                "schema_version": "frozen-framing-profile-policy/v1",
                "policy": "manual_refit/v1",
                "scale_signature": "user-revision/v1",
                "probe_interface_status": "disabled_user_revision/v1",
            }
            payload["presentation"]["variants"] = [
                {
                    "variant_id": "user-revision",
                    "camera_id": "fixed_123",
                    "zoom": 1.25,
                    "pan": [0.0, 0.0],
                }
            ]
            task = replace(task, payload=payload)
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

            result = worker.render(
                worker.open_session(workspace, RenderPlan("render-plan/v2", (task,))),
                task,
                1,
            )
            cache_written = (
                workspace
                / "internal"
                / "screen-centering"
                / "frozen-framing-profiles.json"
            ).exists()

        self.assertEqual(result.disposition, "failed")
        self.assertEqual(result.error_code, "FRAMING_PROFILE_CONTRACT_INVALID")
        self.assertEqual(len(runner.commands), 0)
        self.assertFalse(cache_written)

    def test_first_camera_calibration_freezes_profile_for_later_steps(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            first_task = _native_task()
            second_payload = deepcopy(first_task.payload)
            second_payload["plan_index"] = 1
            second_task = replace(
                first_task,
                task_id="formal-step-2",
                step_id="formal-step-2",
                payload=second_payload,
            )
            plan = RenderPlan("render-plan/v2", (first_task, second_task))
            plan_path = workspace / "locked-render-jobs.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "schema_version": plan.schema_version,
                        "tasks": [asdict(task) for task in plan.tasks],
                    }
                ),
                encoding="utf-8",
            )
            runner = AdaptiveCenteringRunner(
                workspace / "internal" / "prepared-models"
            )
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=plan_path,
                runner=runner,
            )
            session = worker.open_session(workspace, plan)

            first = worker.render(session, first_task, 1)
            cache_file = (
                workspace
                / "internal"
                / "screen-centering"
                / "screen-pan-responses.json"
            )
            cache_written = cache_file.is_file()
            profile_file = (
                workspace
                / "internal"
                / "screen-centering"
                / "frozen-framing-profiles.json"
            )
            profile_written = profile_file.is_file()
            reopened = worker.open_session(workspace, plan)
            second = worker.render(reopened, second_task, 1)

        self.assertEqual(first.disposition, "passed")
        self.assertEqual(second.disposition, "passed")
        self.assertTrue(cache_written)
        self.assertTrue(profile_written)
        counts = [
            command[command.index("-Count") + 1] for command in runner.commands
        ]
        self.assertEqual(counts, ["1", "2", "1", "1"])
        self.assertEqual(len(reopened.screen_pan_responses), 1)
        self.assertEqual(len(reopened.framing_profiles), 1)

    def test_offscreen_audited_arrow_enters_bounded_center_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = _native_task()
            plan = RenderPlan("render-plan/v2", (task,))
            plan_path = workspace / "locked-render-jobs.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "schema_version": plan.schema_version,
                        "tasks": [asdict(task)],
                    }
                ),
                encoding="utf-8",
            )
            runner = AdaptiveCenteringRunner(
                workspace / "internal" / "prepared-models",
                hide_arrow_when_off_center=True,
            )
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=plan_path,
                runner=runner,
            )

            result = worker.render(worker.open_session(workspace, plan), task, 1)

        self.assertEqual(result.disposition, "passed")
        counts = [command[command.index("-Count") + 1] for command in runner.commands]
        self.assertEqual(counts, ["1", "2", "1"])

    def test_centered_small_subject_derives_zoom_without_fixed_device_value(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = _native_task()
            plan = RenderPlan("render-plan/v2", (task,))
            plan_path = workspace / "locked-render-jobs.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "schema_version": plan.schema_version,
                        "tasks": [asdict(task)],
                    }
                ),
                encoding="utf-8",
            )
            runner = AdaptiveCenteringRunner(
                workspace / "internal" / "prepared-models",
                zoom_sensitive=True,
            )
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=plan_path,
                runner=runner,
            )

            result = worker.render(worker.open_session(workspace, plan), task, 1)
            final_plan_path = Path(
                runner.commands[-1][
                    runner.commands[-1].index("-RenderPlanJson") + 1
                ]
            )
            final_payload = json.loads(final_plan_path.read_text(encoding="utf-8"))
            final_zoom = final_payload["tasks"][0]["payload"]["presentation"][
                "variants"
            ][0]["zoom"]

        self.assertEqual(result.disposition, "passed")
        self.assertGreater(final_zoom, 1.0)
        self.assertLess(final_zoom, 1.2)

    def test_large_zoom_ratio_stays_observable_and_recenters_each_round(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            task = _native_task()
            plan = RenderPlan("render-plan/v2", (task,))
            plan_path = workspace / "locked-render-jobs.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "schema_version": plan.schema_version,
                        "tasks": [asdict(task)],
                    }
                ),
                encoding="utf-8",
            )
            runner = AdaptiveCenteringRunner(
                workspace / "internal" / "prepared-models",
                zoom_sensitive=True,
                lower_left_zoom_anchor=True,
                subject_half_size=160.0,
            )
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=plan_path,
                runner=runner,
            )

            result = worker.render(worker.open_session(workspace, plan), task, 1)
            zooms = []
            rendered_frames = 0
            for command in runner.commands:
                rendered_frames += int(command[command.index("-Count") + 1])
                command_plan = Path(
                    command[command.index("-RenderPlanJson") + 1]
                )
                payload = json.loads(command_plan.read_text(encoding="utf-8"))
                zooms.append(
                    payload["tasks"][0]["payload"]["presentation"]["variants"][0]["zoom"]
                )

        self.assertEqual(result.disposition, "passed")
        self.assertEqual(
            rendered_frames,
            4,
            "a cold framing recovery is base + two probes + one solved raster",
        )
        distinct_zooms = sorted({round(float(value), 6) for value in zooms})
        self.assertEqual(len(distinct_zooms), 2)
        self.assertGreater(distinct_zooms[-1], 1.0)

    def test_scale_bucket_probe_is_bounded_reused_and_invalidated_by_signature(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            first = _scale_bucket_task(
                task_id="scale-first",
                plan_index=0,
                signature="cad-framing-scale/v1:depth=1:activity=10:context=11:ratio=0",
                activity_size=80.0,
                context_size=100.0,
            )
            reused = _scale_bucket_task(
                task_id="scale-reused",
                plan_index=1,
                signature="cad-framing-scale/v1:depth=1:activity=10:context=11:ratio=0",
                activity_size=82.0,
                context_size=100.0,
            )
            changed = _scale_bucket_task(
                task_id="scale-changed",
                plan_index=2,
                signature="cad-framing-scale/v1:depth=2:activity=9:context=11:ratio=1",
                activity_size=70.0,
                context_size=100.0,
            )
            plan = RenderPlan("render-plan/v2", (first, reused, changed))
            plan_path = workspace / "locked-render-jobs.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "schema_version": plan.schema_version,
                        "tasks": [asdict(task) for task in plan.tasks],
                    }
                ),
                encoding="utf-8",
            )
            runner = AdaptiveCenteringRunner(
                workspace / "internal" / "prepared-models",
                zoom_sensitive=True,
                lower_left_zoom_anchor=True,
                subject_half_size=400.0,
            )
            worker = AgentNativeCreoWorker(
                powershell="pwsh",
                batch_script=Path("native.ps1"),
                models_root=Path("cad"),
                render_plan_json=plan_path,
                runner=runner,
            )
            session = worker.open_session(workspace, plan)

            first_result = worker.render(session, first, 1)
            first_frames = sum(
                int(command[command.index("-Count") + 1])
                for command in runner.commands
            )
            reused_result = worker.render(session, reused, 1)
            reused_frames = sum(
                int(command[command.index("-Count") + 1])
                for command in runner.commands
            ) - first_frames
            changed_result = worker.render(session, changed, 1)
            total_frames = sum(
                int(command[command.index("-Count") + 1])
                for command in runner.commands
            )

        self.assertEqual(first_result.disposition, "passed")
        self.assertEqual(reused_result.disposition, "passed")
        self.assertEqual(changed_result.disposition, "passed")
        self.assertEqual(first_frames, 4, "cold bucket must be base + 2 probes + solved")
        self.assertEqual(reused_frames, 1, "same bucket must use one formal raster")
        self.assertEqual(
            total_frames - first_frames - reused_frames,
            4,
            "new bucket must calibrate its own PAN response and scale",
        )
        self.assertEqual(len(session.framing_profiles), 2)
        self.assertEqual(len(session.screen_pan_responses), 2)
        self.assertTrue(all(profile.zoom > 1.0 for profile in session.framing_profiles.values()))


if __name__ == "__main__":
    unittest.main()
