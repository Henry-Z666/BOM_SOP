from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sop_pipeline.agent import StepStatus
from sop_pipeline.agent.render_scheduler import (
    FileCheckpointStore,
    MemoryCheckpointStore,
    RenderAttempt,
    RenderPlan,
    RenderScheduler,
    RenderTask,
)
from sop_pipeline.agent.render_job_compiler import compile_creo_render_jobs


class PassingWorker:
    def __init__(self) -> None:
        self.sessions = 0
        self.calls: list[tuple[str, int]] = []

    def open_session(self, run_workspace, plan):
        self.sessions += 1
        return f"session-{self.sessions}"

    def render(self, session, task, attempt):
        self.calls.append((task.task_id, attempt))
        return RenderAttempt.passed(f"sha256:{task.task_id}")

    def close_session(self, session):
        pass


class OneBrokenWorker(PassingWorker):
    def render(self, session, task, attempt):
        self.calls.append((task.task_id, attempt))
        if task.task_id == "task-002":
            return RenderAttempt.retryable("RENDER_FAILED")
        return RenderAttempt.passed(f"sha256:{task.task_id}")


def _task(index: int, *, depends_on=(), blocks=False) -> RenderTask:
    return RenderTask(
        task_id=f"task-{index:03d}",
        step_id=f"step-{index:03d}",
        main_process_id=f"process-{index:03d}",
        depends_on=tuple(depends_on),
        complete_state_hash=f"sha256:state-{index:03d}",
        blocks_dependents_on_failure=blocks,
    )


class RenderSchedulerTests(unittest.TestCase):
    def test_current_42_job_contract_compiles_deterministically(self) -> None:
        contract = Path("data/runs/corrected-v2-render-jobs.json")
        first = compile_creo_render_jobs(contract)
        second = compile_creo_render_jobs(contract)

        self.assertEqual(len(first.tasks), 42)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.tasks[0].depends_on, ())
        self.assertEqual(first.tasks[-1].depends_on, (first.tasks[-2].step_id,))
        self.assertTrue(all(task.main_process_id == "30" for task in first.tasks))

    def test_disk_checkpoint_resumes_without_rerendering(self) -> None:
        plan = RenderPlan("render-plan/v1", tuple(_task(index) for index in range(1, 8)))
        worker = PassingWorker()
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            checkpoints = FileCheckpointStore(workspace / "checkpoint.json")
            first = RenderScheduler(tasks_per_session=3).execute(
                plan, worker, workspace, checkpoints
            )
            resumed_worker = PassingWorker()
            resumed = RenderScheduler(tasks_per_session=3).execute(
                plan, resumed_worker, workspace, checkpoints
            )

        self.assertEqual(first.steps, resumed.steps)
        self.assertEqual(resumed.metrics.restored_steps, 7)
        self.assertEqual(resumed.metrics.worker_sessions, 0)
        self.assertEqual(resumed_worker.calls, [])

    def test_500_tasks_use_linear_checkpoints_and_25_bounded_sessions(self) -> None:
        plan = RenderPlan("render-plan/v1", tuple(_task(index) for index in range(1, 501)))
        worker = PassingWorker()
        checkpoints = MemoryCheckpointStore()
        with tempfile.TemporaryDirectory() as folder:
            result = RenderScheduler(max_attempts=3, tasks_per_session=20).execute(
                plan,
                worker,
                Path(folder),
                checkpoints,
            )

        self.assertEqual(result.metrics.total_tasks, 500)
        self.assertEqual(result.metrics.worker_sessions, 25)
        self.assertEqual(result.metrics.render_attempts, 500)
        self.assertEqual(checkpoints.save_count, 25)
        self.assertTrue(all(step.status is StepStatus.PASSED for step in result.steps))

    def test_visual_failure_does_not_block_later_steps(self) -> None:
        plan = RenderPlan(
            "render-plan/v1",
            (
                _task(1),
                _task(2, depends_on=("step-001",)),
                _task(3, depends_on=("step-002",)),
            ),
        )
        worker = OneBrokenWorker()
        with tempfile.TemporaryDirectory() as folder:
            result = RenderScheduler(max_attempts=3, tasks_per_session=20).execute(
                plan,
                worker,
                Path(folder),
                MemoryCheckpointStore(),
            )

        statuses = {step.step_id: step.status for step in result.steps}
        self.assertEqual(statuses["step-002"], StepStatus.FAILED)
        self.assertEqual(statuses["step-003"], StepStatus.PASSED)
        self.assertEqual([attempt for task, attempt in worker.calls if task == "task-002"], [1, 2, 3])

    def test_structural_failure_waits_only_its_dependency_descendants(self) -> None:
        plan = RenderPlan(
            "render-plan/v1",
            (
                _task(1),
                _task(2, blocks=True),
                _task(3, depends_on=("step-002",)),
                _task(4),
            ),
        )
        worker = OneBrokenWorker()
        with tempfile.TemporaryDirectory() as folder:
            result = RenderScheduler(max_attempts=2, tasks_per_session=20).execute(
                plan,
                worker,
                Path(folder),
                MemoryCheckpointStore(),
            )

        statuses = {step.step_id: step.status for step in result.steps}
        self.assertEqual(statuses["step-002"], StepStatus.FAILED)
        self.assertEqual(statuses["step-003"], StepStatus.DEPENDENCY_WAIT)
        self.assertEqual(statuses["step-004"], StepStatus.PASSED)


if __name__ == "__main__":
    unittest.main()
