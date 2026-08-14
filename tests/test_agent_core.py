from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sop_pipeline.agent import (
    AgentCore,
    AnalysisResult,
    ClarificationPacket,
    ClarificationItem,
    GenerationResult,
    RunStatus,
    StepResult,
    StepResolution,
    StepStatus,
)


class SuccessfulWorkflow:
    def analyze(self, run):
        return AnalysisResult(
            packet=ClarificationPacket(
                schema_version="clarification-packet/v1",
                summary="识别到 1 个主工序和 1 个安装步骤。",
                items=(),
            )
        )

    def generate(self, run, plan):
        return GenerationResult(
            steps=(
                StepResult(
                    step_id="process-001-step-001",
                    main_process_id="process-001",
                    status=StepStatus.PASSED,
                    depends_on=(),
                    complete_state_hash="sha256:complete-state",
                    output_hash="sha256:image-001",
                ),
            ),
            delivery_directory=run.workspace / "delivery",
        )

    def resolve(self, run, resolution):
        raise AssertionError("happy path must not require resolution")


class QuestionedWorkflow(SuccessfulWorkflow):
    def generate(self, run, plan):
        return GenerationResult(
            steps=(
                StepResult(
                    step_id="step-questioned",
                    main_process_id="process-001",
                    status=StepStatus.QUESTIONED,
                    depends_on=(),
                    complete_state_hash="sha256:state-a",
                    output_hash="sha256:candidate-a",
                ),
                StepResult(
                    step_id="step-independent",
                    main_process_id="process-002",
                    status=StepStatus.PASSED,
                    depends_on=(),
                    complete_state_hash="sha256:state-b",
                    output_hash="sha256:unchanged-image",
                ),
            ),
            delivery_directory=run.workspace / "delivery",
        )

    def resolve(self, run, resolution):
        if resolution.step_id != "step-questioned" or resolution.candidate_id != "B":
            raise AssertionError("unexpected resolution")
        return GenerationResult(
            steps=(
                StepResult(
                    step_id="step-questioned",
                    main_process_id="process-001",
                    status=StepStatus.PASSED,
                    depends_on=(),
                    complete_state_hash="sha256:state-a",
                    output_hash="sha256:selected-b",
                ),
                StepResult(
                    step_id="step-independent",
                    main_process_id="process-002",
                    status=StepStatus.PASSED,
                    depends_on=(),
                    complete_state_hash="sha256:state-b",
                    output_hash="sha256:unchanged-image",
                ),
            ),
            delivery_directory=run.workspace / "delivery",
        )


class CrashingWorkflow(SuccessfulWorkflow):
    def generate(self, run, plan):
        raise RuntimeError("simulated worker crash")


class ClarifyingWorkflow(SuccessfulWorkflow):
    def analyze(self, run):
        return AnalysisResult(
            packet=ClarificationPacket(
                schema_version="clarification-packet/v1",
                summary="存在一个需要确认的装配语义。",
                items=(
                    ClarificationItem(
                        item_id="install-mode",
                        category="CONFIRMATION",
                        question="左右支架是否同一步安装？",
                        options=("同一步安装", "分成两个步骤"),
                        recommended_option="同一步安装",
                    ),
                ),
            )
        )


class UnsafeDeliveryWorkflow(SuccessfulWorkflow):
    def generate(self, run, plan):
        result = super().generate(run, plan)
        return GenerationResult(
            steps=result.steps,
            delivery_directory=run.workspace.parent / "outside-this-run",
        )


class MutatingIndependentWorkflow(QuestionedWorkflow):
    def resolve(self, run, resolution):
        result = super().resolve(run, resolution)
        changed = list(result.steps)
        independent = changed[1]
        changed[1] = StepResult(
            step_id=independent.step_id,
            main_process_id=independent.main_process_id,
            status=independent.status,
            depends_on=independent.depends_on,
            complete_state_hash=independent.complete_state_hash,
            output_hash="sha256:unexpected-change",
        )
        return GenerationResult(tuple(changed), result.delivery_directory)


class AgentCoreTests(unittest.TestCase):
    def test_created_run_survives_a_new_core_process(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom = root / "BOM.xlsx"
            bom.write_bytes(b"bom-v1")
            cad = root / "cad"
            cad.mkdir()
            (cad / "root.asm.1").write_bytes(b"assembly-v1")

            first = AgentCore(root / "agent-workspace")
            run_id = first.create_run(bom, cad)

            second = AgentCore(root / "agent-workspace")
            run = second.get_run(run_id)

        self.assertEqual(run.run_id, run_id)
        self.assertEqual(run.status, RunStatus.ANALYZING)
        self.assertTrue(run.input_fingerprint.startswith("sha256:"))

    def test_confirmed_run_completes_through_the_public_interface(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom = root / "BOM.xlsx"
            bom.write_bytes(b"bom-v1")
            cad = root / "cad"
            cad.mkdir()
            (cad / "root.asm.1").write_bytes(b"assembly-v1")
            core = AgentCore(root / "agent-workspace", workflow=SuccessfulWorkflow())

            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)
            plan = core.confirm(run_id, answers={})
            outcome = core.generate(run_id)

        self.assertEqual(packet.summary, "识别到 1 个主工序和 1 个安装步骤。")
        self.assertEqual(plan.revision, 1)
        self.assertEqual(outcome.status, RunStatus.COMPLETED)
        self.assertEqual(outcome.steps[0].status, StepStatus.PASSED)

    def test_questioned_step_can_be_resolved_without_changing_independent_output(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom = root / "BOM.xlsx"
            bom.write_bytes(b"bom-v1")
            cad = root / "cad"
            cad.mkdir()
            (cad / "root.asm.1").write_bytes(b"assembly-v1")
            core = AgentCore(root / "agent-workspace", workflow=QuestionedWorkflow())
            run_id = core.create_run(bom, cad)
            core.analyze(run_id)
            core.confirm(run_id, answers={})

            first = core.generate(run_id)
            resolved = core.resolve(
                run_id,
                StepResolution(step_id="step-questioned", candidate_id="B"),
            )

        self.assertEqual(first.status, RunStatus.NEEDS_REVIEW)
        self.assertEqual(first.steps[1].status, StepStatus.PASSED)
        self.assertEqual(resolved.status, RunStatus.COMPLETED)
        self.assertEqual(resolved.steps[1].output_hash, "sha256:unchanged-image")

    def test_resume_continues_a_confirmed_run_after_process_restart(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom = root / "BOM.xlsx"
            bom.write_bytes(b"bom-v1")
            cad = root / "cad"
            cad.mkdir()
            (cad / "root.asm.1").write_bytes(b"assembly-v1")
            first = AgentCore(root / "agent-workspace", workflow=SuccessfulWorkflow())
            run_id = first.create_run(bom, cad)
            first.analyze(run_id)
            first.confirm(run_id, answers={})

            restarted = AgentCore(root / "agent-workspace", workflow=SuccessfulWorkflow())
            outcome = restarted.resume(run_id)

        self.assertEqual(outcome.status, RunStatus.COMPLETED)
        self.assertEqual(outcome.steps[0].output_hash, "sha256:image-001")

    def test_generation_crash_remains_resumable_without_reanalysis(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom = root / "BOM.xlsx"
            bom.write_bytes(b"bom-v1")
            cad = root / "cad"
            cad.mkdir()
            (cad / "root.asm.1").write_bytes(b"assembly-v1")
            first = AgentCore(root / "agent-workspace", workflow=CrashingWorkflow())
            run_id = first.create_run(bom, cad)
            first.analyze(run_id)
            first.confirm(run_id, answers={})

            with self.assertRaisesRegex(RuntimeError, "simulated worker crash"):
                first.generate(run_id)

            restarted = AgentCore(root / "agent-workspace", workflow=SuccessfulWorkflow())
            before_resume = restarted.get_run(run_id)
            outcome = restarted.resume(run_id)

        self.assertEqual(before_resume.status, RunStatus.GENERATING)
        self.assertEqual(before_resume.plan_revision, 1)
        self.assertEqual(outcome.status, RunStatus.COMPLETED)

    def test_generation_cannot_write_delivery_outside_its_run(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom = root / "BOM.xlsx"
            bom.write_bytes(b"bom-v1")
            cad = root / "cad"
            cad.mkdir()
            (cad / "root.asm.1").write_bytes(b"assembly-v1")
            core = AgentCore(root / "agent-workspace", workflow=UnsafeDeliveryWorkflow())
            run_id = core.create_run(bom, cad)
            core.analyze(run_id)
            core.confirm(run_id, answers={})

            with self.assertRaisesRegex(ValueError, "当前运行批次"):
                core.generate(run_id)

    def test_resolution_cannot_change_an_independent_step(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom = root / "BOM.xlsx"
            bom.write_bytes(b"bom-v1")
            cad = root / "cad"
            cad.mkdir()
            (cad / "root.asm.1").write_bytes(b"assembly-v1")
            core = AgentCore(root / "agent-workspace", workflow=MutatingIndependentWorkflow())
            run_id = core.create_run(bom, cad)
            core.analyze(run_id)
            core.confirm(run_id, answers={})
            core.generate(run_id)

            with self.assertRaisesRegex(ValueError, "无关步骤"):
                core.resolve(
                    run_id,
                    StepResolution(step_id="step-questioned", candidate_id="B"),
                )

    def test_confirmation_requires_one_valid_answer_per_question(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom = root / "BOM.xlsx"
            bom.write_bytes(b"bom-v1")
            cad = root / "cad"
            cad.mkdir()
            (cad / "root.asm.1").write_bytes(b"assembly-v1")
            core = AgentCore(root / "agent-workspace", workflow=ClarifyingWorkflow())
            run_id = core.create_run(bom, cad)
            core.analyze(run_id)

            with self.assertRaisesRegex(ValueError, "install-mode"):
                core.confirm(run_id, answers={})
            with self.assertRaisesRegex(ValueError, "不属于确认卡选项"):
                core.confirm(run_id, answers={"install-mode": "随便处理"})
            plan = core.confirm(run_id, answers={"install-mode": "同一步安装"})

        self.assertEqual(plan.answers, {"install-mode": "同一步安装"})


if __name__ == "__main__":
    unittest.main()
