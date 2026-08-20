from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from sop_pipeline.agent import (
    AgentCore,
    PipelineOrchestrator,
    RenderAttempt,
    RunStatus,
    SkillPipelineError,
    StepResolution,
    StepResult,
    StepStatus,
)
from sop_pipeline.agent.creo_discovery import StaticCreoDiscovery
from tests.test_agent_analysis import _xlsx


class ImageWorker:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def open_session(self, run_workspace, plan):
        output = run_workspace / "rendered"
        output.mkdir(parents=True, exist_ok=True)
        return output

    def render(self, session, task, attempt):
        del attempt
        self.calls.append(task.step_id)
        image = session / f"{task.task_id}.jpg"
        Image.new("RGB", (1600, 1600), "white").save(image)
        return RenderAttempt.passed("sha256:" + sha256(image.read_bytes()).hexdigest())

    def close_session(self, session):
        del session


class CandidateWorker(ImageWorker):
    def render(self, session, task, attempt):
        del attempt
        self.calls.append(task.step_id)
        hashes = []
        for index, shade in enumerate((230, 200), start=1):
            image = session / f"{task.task_id}-candidate-{index}.jpg"
            Image.new("RGB", (1600, 1600), (shade, shade, shade)).save(image)
            hashes.append("sha256:" + sha256(image.read_bytes()).hexdigest())
        return RenderAttempt.reviewable(hashes[0], "SUBJECT_TOO_SMALL")


class PresentationWarningWorker(ImageWorker):
    def render(self, session, task, attempt):
        del attempt
        self.calls.append(task.step_id)
        image = session / f"{task.task_id}.jpg"
        Image.new("RGB", (1600, 1600), "white").save(image)
        return RenderAttempt.reviewable(
            "sha256:" + sha256(image.read_bytes()).hexdigest(),
            "SUBJECT_TOO_SMALL",
        )


class RecoveringWorker(ImageWorker):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True
        self.failed_step: str | None = None

    def render(self, session, task, attempt):
        self.calls.append(task.step_id)
        if self.failed_step is None:
            self.failed_step = task.step_id
        if self.fail and task.step_id == self.failed_step:
            hashes = []
            for index, shade in enumerate((225, 205), start=1):
                image = session / f"{task.task_id}-candidate-{index}.jpg"
                Image.new("RGB", (1600, 1600), (shade, shade, shade)).save(image)
                hashes.append("sha256:" + sha256(image.read_bytes()).hexdigest())
            return RenderAttempt.reviewable(hashes[0], "SUBJECT_TOO_SMALL")
        return super().render(session, task, attempt)


class AlwaysFailingWorker(ImageWorker):
    def render(self, session, task, attempt):
        del session
        self.calls.append(task.step_id)
        return RenderAttempt.retryable("CREO_RUNTIME_CONFIG_MISSING")


def _fixture(root: Path):
    bom = root / "BOM.xlsx"
    _xlsx(
        bom,
        [(
            "BOM",
            [
                ["层级", "物料编码", "图号", "名称", "数量", "单位", "装配步骤"],
                ["30", "ROOT", "ROOT-ASM", "设备总装", "1", "件", "第3步：检查"],
                ["30.1", "A", "PART-A", "底座", "1", "件", "第1步：固定底座"],
                ["30.2", "B", "PART-B", "支架", "1", "件", "第2步：安装支架"],
            ],
        )],
    )
    cad = root / "cad"
    cad.mkdir()
    assembly = cad / "root-asm.asm.1"
    assembly.write_bytes(b"root")
    (cad / "part-a.prt.1").write_bytes(b"a")
    (cad / "part-b.prt.1").write_bytes(b"b")
    graph = {
        "schema_version": "creo-cad-graph/v3",
        "assembly_file": assembly.name,
        "default_view_matrix": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "authoritative_assembly": {
            "sha256": "sha256:" + sha256(assembly.read_bytes()).hexdigest(),
            "coordinate_system": "root_asm",
        },
        "occurrences": [
            {
                "occurrence_id": "10",
                "parent_occurrence": "ROOT",
                "model_name": "PART-A",
                "part_no": "part-a.prt",
                "transform": {
                    "x_axis": [1.0, 0.0, 0.0],
                    "y_axis": [0.0, 1.0, 0.0],
                    "z_axis": [0.0, 0.0, 1.0],
                    "origin": [0.0, 0.0, 0.0],
                },
            },
            {
                "occurrence_id": "20",
                "parent_occurrence": "ROOT",
                "model_name": "PART-B",
                "part_no": "part-b.prt",
                "transform": {
                    "x_axis": [1.0, 0.0, 0.0],
                    "y_axis": [0.0, 1.0, 0.0],
                    "z_axis": [0.0, 0.0, 1.0],
                    "origin": [0.0, 0.0, 20.0],
                },
            },
        ],
        "constraints": [
            {"occurrences": ["10", "ROOT"], "type": "FIX"},
            {
                "id": "20-mate",
                "occurrences": ["20", "10"],
                "type": "MATE",
                "assembly_reference": {
                    "occurrence_id": "10",
                    "geometry": {
                        "status": "available",
                        "direction_root": [0.0, 0.0, 1.0],
                        "point_root": [0.0, 0.0, 0.0],
                    },
                },
                "component_reference": {
                    "occurrence_id": "20",
                    "geometry": {
                        "status": "available",
                        "direction_root": [0.0, 0.0, 1.0],
                        "point_root": [0.0, 0.0, 20.0],
                    },
                },
            },
        ],
    }
    return bom, cad, graph


def _weak_direction_fixture(root: Path):
    """Return a plan with enough geometry to preview but a failed direction gate."""

    bom, cad, graph = _fixture(root)
    weak_constraint = next(
        item for item in graph["constraints"] if item.get("id") == "20-mate"
    )
    weak_constraint["assembly_reference"]["geometry"]["direction_root"] = [
        1.0,
        0.0,
        0.0,
    ]
    return bom, cad, graph


class PipelineOrchestratorTests(unittest.TestCase):
    def test_unaffected_step_rejects_state_hash_drift(self) -> None:
        before = (
            StepResult(
                step_id="step-1",
                main_process_id="process-1",
                status=StepStatus.PASSED,
                depends_on=(),
                complete_state_hash="sha256:old",
                output_hash="sha256:image",
            ),
        )
        after = (
            StepResult(
                step_id="step-1",
                main_process_id="process-1",
                status=StepStatus.PASSED,
                depends_on=(),
                complete_state_hash="sha256:new",
                output_hash="sha256:image",
            ),
        )

        with self.assertRaisesRegex(ValueError, "step-1"):
            AgentCore._assert_unaffected_unchanged(before, after, set())

    def test_direction_resolution_rerenders_after_locked_plan_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad, graph = _fixture(root)
            core = AgentCore(
                root / "workspace",
                PipelineOrchestrator(
                    adapters={
                        "creo_discovery": StaticCreoDiscovery(graph),
                        "render_worker": ImageWorker(),
                    }
                ),
            )
            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)
            core.confirm(
                run_id,
                {
                    item.item_id: item.recommended_option
                    for item in packet.items
                    if item.category == "CONFIRMATION"
                },
            )
            run = core.get_run(run_id)
            locked_path = "plans/locked-render-plan-0001.json"
            repaired = core._artifacts.read_json(run.workspace, locked_path)
            stale = json.loads(json.dumps(repaired))
            target = stale["steps"][0]
            target_id = target["step_id"]
            target.update(
                {
                    "receiver_occurrences": [],
                    "constraint_ids": [],
                    "receiver_point_root": None,
                    "receiver_normal_root": None,
                    "translation_vector_root": None,
                    "arrow_anchors": [],
                    "camera_id": None,
                    "status": "questioned",
                    "diagnostics": ["NO_NATIVE_RECEIVER_GEOMETRY"],
                }
            )
            stale["ready_steps"] -= 1
            stale["questioned_steps"] += 1
            core._artifacts.write_json(
                run_id=run_id,
                run_workspace=run.workspace,
                kind="locked-render-plan",
                relative_path=locked_path,
                value=stale,
            )
            pending = core.generate(run_id)
            self.assertEqual(pending.status, RunStatus.NEEDS_REVIEW)
            core._artifacts.write_json(
                run_id=run_id,
                run_workspace=run.workspace,
                kind="locked-render-plan",
                relative_path=locked_path,
                value=repaired,
            )

            completed = core.resolve(
                run_id,
                StepResolution(
                    step_id=target_id,
                    instruction="该零件沿设备Z轴正方向装入",
                ),
            )

            self.assertEqual(completed.status, RunStatus.COMPLETED)
            self.assertTrue(
                (run.workspace / "revisions" / "step-revision-0001.json").exists()
            )

    def test_resolve_does_not_recompile_or_overwrite_stale_locked_plan(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad, graph = _fixture(root)
            worker = ImageWorker()
            core = AgentCore(
                root / "workspace",
                PipelineOrchestrator(
                    adapters={
                        "creo_discovery": StaticCreoDiscovery(graph),
                        "render_worker": worker,
                    }
                ),
            )
            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)
            core.confirm(
                run_id,
                {
                    item.item_id: item.recommended_option
                    for item in packet.items
                    if item.category == "CONFIRMATION"
                },
            )
            run = core.get_run(run_id)
            locked_path = "plans/locked-render-plan-0001.json"
            locked = core._artifacts.read_json(run.workspace, locked_path)
            target = locked["steps"][0]
            target_id = target["step_id"]
            target.update(
                {
                    "receiver_occurrences": [],
                    "constraint_ids": [],
                    "receiver_point_root": None,
                    "receiver_normal_root": None,
                    "translation_vector_root": None,
                    "arrow_anchors": [],
                    "camera_id": None,
                    "status": "questioned",
                    "diagnostics": ["NO_NATIVE_RECEIVER_GEOMETRY"],
                }
            )
            locked["ready_steps"] -= 1
            locked["questioned_steps"] += 1
            core._artifacts.write_json(
                run_id=run_id,
                run_workspace=run.workspace,
                kind="locked-render-plan",
                relative_path=locked_path,
                value=locked,
            )

            pending = core.generate(run_id)
            self.assertEqual(pending.status, RunStatus.NEEDS_REVIEW)
            locked_before = (run.workspace / locked_path).read_bytes()

            with self.assertRaises(SkillPipelineError):
                core.resolve(
                    run_id,
                    StepResolution(
                        step_id=target_id,
                        instruction="按BOM型号重新找文件，把零件安装到孔洞中",
                    ),
                )

            self.assertEqual((run.workspace / locked_path).read_bytes(), locked_before)

    def test_presentation_resolution_cannot_claim_success_without_new_render(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad, graph = _weak_direction_fixture(root)
            worker = ImageWorker()
            core = AgentCore(
                root / "workspace",
                PipelineOrchestrator(
                    adapters={
                        "creo_discovery": StaticCreoDiscovery(graph),
                        "render_worker": worker,
                    }
                ),
            )
            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)
            core.confirm(
                run_id,
                {
                    item.item_id: item.recommended_option
                    for item in packet.items
                    if item.category == "CONFIRMATION"
                },
            )
            pending = core.generate(run_id)
            target = next(
                step
                for step in pending.steps
                if step.status.value == "FAILED"
            )
            calls_before = list(worker.calls)

            with self.assertRaisesRegex(SkillPipelineError, "脚本版本只接受"):
                core.resolve(
                    run_id,
                    StepResolution(
                        step_id=target.step_id,
                        instruction="翻转视角",
                    ),
                )

            self.assertEqual(worker.calls, calls_before)

    def test_explicit_direction_resolution_unlocks_and_renders_blocked_step(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad, graph = _weak_direction_fixture(root)
            worker = ImageWorker()
            core = AgentCore(
                root / "workspace",
                PipelineOrchestrator(
                    adapters={
                        "creo_discovery": StaticCreoDiscovery(graph),
                        "render_worker": worker,
                    }
                ),
            )
            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)
            core.confirm(
                run_id,
                {
                    item.item_id: item.recommended_option
                    for item in packet.items
                    if item.category == "CONFIRMATION"
                },
            )
            pending = core.generate(run_id)
            target = next(
                step for step in pending.steps if step.status.value == "FAILED"
            )
            run = core.get_run(run_id)
            pending_validation = json.loads(
                (run.workspace / "results" / "validation-0001.json").read_text(
                    encoding="utf-8"
                )
            )
            pending_item = next(
                item
                for item in pending_validation["steps"]
                if item["step_id"] == target.step_id
            )
            self.assertEqual(pending_item["image_kind"], "rendered")
            self.assertFalse(pending_item["manual_acceptance_allowed"])
            self.assertTrue(
                pending_item["image_path"].replace("\\", "/").startswith("rendered/")
            )
            calls_before = worker.calls.count(target.step_id)

            outcome = core.resolve(
                run_id,
                StepResolution(
                    step_id=target.step_id,
                    instruction="该零件沿设备Z轴正方向装入",
                ),
            )

            self.assertEqual(outcome.status, RunStatus.COMPLETED)
            self.assertEqual(worker.calls.count(target.step_id), calls_before + 1)
            validation = json.loads(
                (run.workspace / "results" / "validation-0001.json").read_text(
                    encoding="utf-8"
                )
            )
            item = next(
                value
                for value in validation["steps"]
                if value["step_id"] == target.step_id
            )
            self.assertIn("-revision-", item["image_path"])

    def test_human_override_publishes_original_bytes_without_changing_machine_result(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad, graph = _weak_direction_fixture(root)
            worker = ImageWorker()
            core = AgentCore(
                root / "workspace",
                PipelineOrchestrator(
                    adapters={
                        "creo_discovery": StaticCreoDiscovery(graph),
                        "render_worker": worker,
                    }
                ),
            )
            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)
            core.confirm(
                run_id,
                {
                    item.item_id: item.recommended_option
                    for item in packet.items
                    if item.category == "CONFIRMATION"
                },
            )
            pending = core.generate(run_id)
            target = next(step for step in pending.steps if step.status is StepStatus.FAILED)
            run = core.get_run(run_id)
            validation = json.loads(
                (run.workspace / "results" / "validation-0001.json").read_text(
                    encoding="utf-8"
                )
            )
            machine_item = next(
                item for item in validation["steps"] if item["step_id"] == target.step_id
            )
            original = run.workspace / machine_item["image_path"]
            original_bytes = original.read_bytes()

            completed = core.resolve(
                run_id,
                StepResolution(
                    step_id=target.step_id,
                    action="accept_with_override",
                    metadata={
                        "acknowledged": True,
                        "reason": "人工核对装配现场后确认采用",
                    },
                ),
            )

            publication = json.loads(
                (run.workspace / "results" / "publication-0001.json").read_text(
                    encoding="utf-8"
                )
            )
            published = next(
                item for item in publication["steps"] if item["step_id"] == target.step_id
            )
            decision = json.loads(
                (run.workspace / "reviews" / "human-review-decision-0001.json").read_text(
                    encoding="utf-8"
                )
            )
            delivery_image = next(
                path
                for path in (run.workspace / "delivery" / "步骤图片").iterdir()
                if target.step_id in path.name
            )
            delivery_bytes = delivery_image.read_bytes()

        self.assertEqual(completed.status, RunStatus.COMPLETED)
        self.assertEqual(machine_item["status"], "FAILED")
        self.assertEqual(published["status"], "PASSED")
        self.assertEqual(published["machine_status"], "FAILED")
        self.assertEqual(published["human_disposition"], "accept_with_override")
        self.assertEqual(decision["publication_transform"], "none")
        self.assertFalse(decision["watermark"])
        self.assertEqual(delivery_bytes, original_bytes)

    def test_deterministic_gates_keep_presentation_warning_for_review(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad, graph = _fixture(root)
            worker = PresentationWarningWorker()
            core = AgentCore(
                root / "workspace",
                PipelineOrchestrator(
                    adapters={
                        "creo_discovery": StaticCreoDiscovery(graph),
                        "render_worker": worker,
                    }
                ),
            )
            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)
            core.confirm(
                run_id,
                {
                    item.item_id: item.recommended_option
                    for item in packet.items
                    if item.category == "CONFIRMATION"
                },
            )

            outcome = core.generate(run_id)

        self.assertEqual(outcome.status, RunStatus.NEEDS_REVIEW)
        self.assertTrue(worker.calls)

    def test_zero_success_render_batch_blocks_before_placeholder_publication(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad, graph = _fixture(root)
            worker = AlwaysFailingWorker()
            core = AgentCore(
                root / "workspace",
                PipelineOrchestrator(
                    adapters={
                        "creo_discovery": StaticCreoDiscovery(graph),
                        "render_worker": worker,
                    }
                ),
            )
            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)
            core.confirm(
                run_id,
                {
                    item.item_id: item.recommended_option
                    for item in packet.items
                    if item.category == "CONFIRMATION"
                },
            )

            with self.assertRaisesRegex(SkillPipelineError, "零张"):
                core.generate(run_id)

            run = core.get_run(run_id)
            self.assertEqual(run.status, RunStatus.BLOCKED_SYSTEM)
            self.assertFalse((run.workspace / "delivery" / "SOP.xlsx").exists())

    def test_agent_executes_real_skill_chain_through_public_interface(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad, graph = _fixture(root)
            worker = ImageWorker()
            core = AgentCore(
                root / "workspace",
                PipelineOrchestrator(
                    adapters={
                        "creo_discovery": StaticCreoDiscovery(graph),
                        "render_worker": worker,
                    }
                ),
            )

            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)
            answers = {
                item.item_id: item.recommended_option
                for item in packet.items
                if item.category == "CONFIRMATION"
            }
            revision = core.confirm(run_id, answers)
            run = core.get_run(run_id)
            self.assertFalse(
                (run.workspace / "plans" / "locked-render-jobs-0001.json").exists()
            )
            outcome = core.generate(run_id)

            self.assertEqual(revision.revision, 1)
            self.assertEqual(outcome.status, RunStatus.COMPLETED)
            self.assertTrue(worker.calls)
            self.assertTrue((outcome.delivery_directory / "SOP.xlsx").is_file())
            self.assertEqual(
                {path.name for path in outcome.delivery_directory.iterdir()},
                {"SOP.xlsx", "步骤图片"},
            )
            self.assertTrue(
                (run.workspace / "results" / "publication-0001.json").is_file()
            )

    def test_candidate_selection_resolves_step_without_rerendering(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad, graph = _fixture(root)
            worker = CandidateWorker()
            core = AgentCore(
                root / "workspace",
                PipelineOrchestrator(
                    adapters={
                        "creo_discovery": StaticCreoDiscovery(graph),
                        "render_worker": worker,
                    }
                ),
            )
            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)
            core.confirm(
                run_id,
                {
                    item.item_id: item.recommended_option
                    for item in packet.items
                    if item.category == "CONFIRMATION"
                },
            )
            pending = core.generate(run_id)
            calls_before = list(worker.calls)
            target = pending.steps[0]

            completed = core.resolve(
                run_id,
                StepResolution(
                    step_id=target.step_id,
                    candidate_id="candidate-2",
                ),
            )

            self.assertEqual(pending.status, RunStatus.NEEDS_REVIEW)
            self.assertEqual(completed.status, RunStatus.COMPLETED)
            self.assertEqual(worker.calls, calls_before)
            self.assertTrue((completed.delivery_directory / "SOP.xlsx").is_file())

    def test_candidate_selection_cannot_bypass_a_hard_block(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad, graph = _fixture(root)
            worker = CandidateWorker()
            core = AgentCore(
                root / "workspace",
                PipelineOrchestrator(
                    adapters={
                        "creo_discovery": StaticCreoDiscovery(graph),
                        "render_worker": worker,
                    }
                ),
            )
            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)
            core.confirm(
                run_id,
                {
                    item.item_id: item.recommended_option
                    for item in packet.items
                    if item.category == "CONFIRMATION"
                },
            )
            pending = core.generate(run_id)
            run = core.get_run(run_id)
            validation_ref = "results/validation-0001.json"
            validation = core._artifacts.read_json(run.workspace, validation_ref)
            validation["steps"][0]["category"] = "hard_block"
            validation["steps"][0]["manual_acceptance_allowed"] = False
            core._artifacts.write_json(
                run_id=run_id,
                run_workspace=run.workspace,
                kind="validation-result",
                relative_path=validation_ref,
                value=validation,
            )

            with self.assertRaisesRegex(SkillPipelineError, "基础几何硬门"):
                core.resolve(
                    run_id,
                    StepResolution(
                        step_id=pending.steps[0].step_id,
                        candidate_id="candidate-1",
                    ),
                )

    def test_fixed_camera_cannot_be_overridden_during_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad, graph = _fixture(root)
            worker = RecoveringWorker()
            core = AgentCore(
                root / "workspace",
                PipelineOrchestrator(
                    adapters={
                        "creo_discovery": StaticCreoDiscovery(graph),
                        "render_worker": worker,
                    }
                ),
            )
            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)
            core.confirm(
                run_id,
                {
                    item.item_id: item.recommended_option
                    for item in packet.items
                    if item.category == "CONFIRMATION"
                },
            )
            pending = core.generate(run_id)
            worker.fail = False
            calls_before = list(worker.calls)

            with self.assertRaisesRegex(SkillPipelineError, "固定视角"):
                core.resolve(
                    run_id,
                    StepResolution(
                        step_id=pending.steps[0].step_id,
                        instruction="切换固定视角",
                        metadata={
                            "structured_inputs": {"camera_id": "fixed_456"}
                        },
                    ),
                )

            self.assertEqual(worker.calls, calls_before)

    def test_explicit_axis_direction_reruns_only_invalidated_step(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad, graph = _fixture(root)
            worker = RecoveringWorker()
            core = AgentCore(
                root / "workspace",
                PipelineOrchestrator(
                    adapters={
                        "creo_discovery": StaticCreoDiscovery(graph),
                        "render_worker": worker,
                    }
                ),
            )
            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)
            core.confirm(
                run_id,
                {
                    item.item_id: item.recommended_option
                    for item in packet.items
                    if item.category == "CONFIRMATION"
                },
            )
            pending = core.generate(run_id)
            worker.fail = False
            target = pending.steps[0]

            completed = core.resolve(
                run_id,
                StepResolution(
                    step_id=target.step_id,
                    instruction="该零件沿设备Z轴正方向装入",
                ),
            )

            self.assertEqual(pending.status, RunStatus.NEEDS_REVIEW)
            self.assertEqual(completed.status, RunStatus.COMPLETED)
            self.assertEqual(worker.calls.count(target.step_id), 3)

if __name__ == "__main__":
    unittest.main()
