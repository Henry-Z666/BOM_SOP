from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from sop_pipeline.agent import (
    AgentCore,
    PipelineOrchestrator,
    RenderAttempt,
    RevisionKind,
    RunStatus,
    SemanticReview,
    SkillPipelineError,
    StepResolution,
    StepRevision,
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
        return RenderAttempt.questioned(tuple(hashes))


class RecoveringWorker(ImageWorker):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True

    def render(self, session, task, attempt):
        self.calls.append(task.step_id)
        if self.fail:
            return RenderAttempt.retryable("CREO_TEST_FAILURE")
        return super().render(session, task, attempt)


class FakeAdvisor:
    def review_render(self, image_file, minimized_context):
        del image_file, minimized_context
        return SemanticReview(True, ())

    def interpret_resolution(self, step_id, instruction, revision):
        del instruction
        return StepRevision(
            revision,
            step_id,
            RevisionKind.PRESENTATION,
            {"zoom": 1.1},
        )


class RecoverableAdvisor(FakeAdvisor):
    def __init__(self) -> None:
        self.fail = True

    def review_render(self, image_file, minimized_context):
        if self.fail:
            raise RuntimeError("temporary DashScope timeout")
        return super().review_render(image_file, minimized_context)


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


class PipelineOrchestratorTests(unittest.TestCase):
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
                        "qwen_advisor": FakeAdvisor(),
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
                        "qwen_advisor": FakeAdvisor(),
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

    def test_natural_language_resolution_reruns_only_invalidated_step(self) -> None:
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
                        "qwen_advisor": FakeAdvisor(),
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
                StepResolution(step_id=target.step_id, instruction="主体稍微放大"),
            )

            self.assertEqual(pending.status, RunStatus.NEEDS_REVIEW)
            self.assertEqual(completed.status, RunStatus.COMPLETED)
            self.assertEqual(worker.calls.count(target.step_id), 5)

    def test_qwen_review_outage_is_retryable_and_reuses_render(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad, graph = _fixture(root)
            worker = ImageWorker()
            advisor = RecoverableAdvisor()
            core = AgentCore(
                root / "workspace",
                PipelineOrchestrator(
                    adapters={
                        "creo_discovery": StaticCreoDiscovery(graph),
                        "render_worker": worker,
                        "qwen_advisor": advisor,
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

            with self.assertRaises(SkillPipelineError):
                core.generate(run_id)
            calls_after_render = list(worker.calls)
            advisor.fail = False
            completed = core.resume(run_id)

            self.assertEqual(completed.status, RunStatus.COMPLETED)
            self.assertEqual(worker.calls, calls_after_render)


if __name__ == "__main__":
    unittest.main()
