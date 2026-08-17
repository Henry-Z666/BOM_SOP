from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sop_pipeline.agent import StepResult, StepStatus
from sop_pipeline.agent.render_scheduler import (
    FileCheckpointStore,
    MemoryCheckpointStore,
    RenderAttempt,
    RenderPlan,
    RenderScheduler,
    RenderTask,
)


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


class ReviewableWorker(PassingWorker):
    def render(self, session, task, attempt):
        self.calls.append((task.task_id, attempt))
        return RenderAttempt.reviewable(
            f"sha256:{task.task_id}", "SUBJECT_TOO_SMALL"
        )


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
    def test_single_real_image_can_be_kept_for_review_without_fake_candidates(self) -> None:
        plan = RenderPlan("render-plan/v2", (_task(1),))
        worker = ReviewableWorker()
        with tempfile.TemporaryDirectory() as folder:
            result = RenderScheduler().execute(
                plan, worker, Path(folder), MemoryCheckpointStore()
            )

        self.assertEqual(result.steps[0].status, StepStatus.QUESTIONED)
        self.assertEqual(result.steps[0].output_hash, "sha256:task-001")
        self.assertEqual(result.final_attempts["step-001"].candidate_hashes, ())

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

    def test_failed_checkpoint_is_retried_after_environment_recovery(self) -> None:
        plan = RenderPlan("render-plan/v1", (_task(1),))
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            checkpoints = FileCheckpointStore(workspace / "checkpoint.json")
            # Persist a failed checkpoint to model a broken Creo environment.
            checkpoints.save(
                plan.fingerprint,
                {"step-001": StepResult(
                    step_id="step-001",
                    main_process_id="process-001",
                    status=StepStatus.FAILED,
                    depends_on=(),
                    complete_state_hash="sha256:state-001",
                    output_hash=None,
                )},
            )
            recovered_worker = PassingWorker()
            recovered = RenderScheduler(max_attempts=1).execute(
                plan, recovered_worker, workspace, checkpoints
            )

        self.assertEqual(recovered.steps[0].status, StepStatus.PASSED)
        self.assertEqual(recovered.metrics.restored_steps, 0)
        self.assertEqual(recovered_worker.calls, [("task-001", 1)])

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
        self.assertEqual(checkpoints.save_count, 500)
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
        self.assertEqual(
            result.final_attempts["step-002"].error_code,
            "RENDER_FAILED",
        )

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
