from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Protocol

from .models import StepResult, StepStatus


@dataclass(frozen=True)
class RenderTask:
    task_id: str
    step_id: str
    main_process_id: str
    depends_on: tuple[str, ...]
    complete_state_hash: str
    blocks_dependents_on_failure: bool = False
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RenderPlan:
    schema_version: str
    tasks: tuple[RenderTask, ...]

    @property
    def fingerprint(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "tasks": [asdict(task) for task in self.tasks],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RenderAttempt:
    disposition: str
    output_hash: str | None = None
    error_code: str | None = None
    candidate_hashes: tuple[str, ...] = ()

    @classmethod
    def passed(cls, output_hash: str) -> RenderAttempt:
        return cls("passed", output_hash=output_hash)

    @classmethod
    def retryable(cls, error_code: str) -> RenderAttempt:
        return cls("retryable", error_code=error_code)

    @classmethod
    def questioned(
        cls,
        candidate_hashes: tuple[str, ...],
        error_code: str = "NEEDS_REVIEW",
    ) -> RenderAttempt:
        if not 2 <= len(candidate_hashes) <= 4:
            raise ValueError("questioned attempts require 2 to 4 candidates")
        return cls(
            "questioned",
            output_hash=candidate_hashes[0],
            error_code=error_code,
            candidate_hashes=candidate_hashes,
        )

    @classmethod
    def reviewable(
        cls,
        output_hash: str,
        error_code: str = "PRESENTATION_REVIEW_REQUIRED",
    ) -> RenderAttempt:
        """Keep one structurally valid image for semantic/manual review.

        Candidate sets still require 2--4 alternatives.  This disposition is
        for a real Creo image which passed geometry/audit gates but triggered
        one or more presentation warnings under a frozen camera policy.
        """

        if not output_hash:
            raise ValueError("reviewable attempts require output_hash")
        return cls("questioned", output_hash=output_hash, error_code=error_code)

    @classmethod
    def failed(cls, error_code: str) -> RenderAttempt:
        return cls("failed", error_code=error_code)


class RenderWorker(Protocol):
    def open_session(self, run_workspace: Path, plan: RenderPlan) -> Any: ...

    def render(
        self,
        session: Any,
        task: RenderTask,
        attempt: int,
    ) -> RenderAttempt: ...

    def close_session(self, session: Any) -> None: ...


class CheckpointStore(Protocol):
    def load(self, plan_fingerprint: str) -> dict[str, StepResult]: ...

    def save(
        self,
        plan_fingerprint: str,
        completed: dict[str, StepResult],
    ) -> None: ...


class MemoryCheckpointStore:
    def __init__(self) -> None:
        self._plans: dict[str, dict[str, StepResult]] = {}
        self.save_count = 0

    def load(self, plan_fingerprint: str) -> dict[str, StepResult]:
        return dict(self._plans.get(plan_fingerprint, {}))

    def save(
        self,
        plan_fingerprint: str,
        completed: dict[str, StepResult],
    ) -> None:
        self._plans[plan_fingerprint] = dict(completed)
        self.save_count += 1


class FileCheckpointStore:
    """Atomic, plan-scoped checkpoint storage for crash recovery."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, plan_fingerprint: str) -> dict[str, StepResult]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("plan_fingerprint") != plan_fingerprint:
            return {}
        results: dict[str, StepResult] = {}
        for item in payload.get("steps", []):
            step = StepResult(
                step_id=item["step_id"],
                main_process_id=item["main_process_id"],
                status=StepStatus(item["status"]),
                depends_on=tuple(item["depends_on"]),
                complete_state_hash=item["complete_state_hash"],
                output_hash=item.get("output_hash"),
            )
            results[step.step_id] = step
        return results

    def save(
        self,
        plan_fingerprint: str,
        completed: dict[str, StepResult],
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "render-checkpoint/v1",
            "plan_fingerprint": plan_fingerprint,
            "steps": [
                {
                    **asdict(completed[step_id]),
                    "status": completed[step_id].status.value,
                }
                for step_id in sorted(completed)
            ],
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)


@dataclass(frozen=True)
class RenderMetrics:
    total_tasks: int
    rendered_tasks: int
    render_attempts: int
    worker_sessions: int
    restored_steps: int


@dataclass(frozen=True)
class RenderScheduleResult:
    steps: tuple[StepResult, ...]
    metrics: RenderMetrics
    final_attempts: dict[str, RenderAttempt] = field(default_factory=dict)


class RenderScheduler:
    """Deterministic DAG executor; worker behavior stays behind an adapter."""

    def __init__(self, *, max_attempts: int = 3, tasks_per_session: int = 20) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if tasks_per_session < 1:
            raise ValueError("tasks_per_session must be positive")
        self.max_attempts = max_attempts
        self.tasks_per_session = tasks_per_session

    def execute(
        self,
        plan: RenderPlan,
        worker: RenderWorker,
        run_workspace: Path,
        checkpoints: CheckpointStore,
    ) -> RenderScheduleResult:
        ordered, by_step = _validate_and_order(plan)
        completed = checkpoints.load(plan.fingerprint)
        completed = {
            step_id: result
            for step_id, result in completed.items()
            if step_id in by_step
            and result.complete_state_hash == by_step[step_id].complete_state_hash
            and result.status in {StepStatus.PASSED, StepStatus.QUESTIONED}
        }
        restored_steps = len(completed)
        session: Any | None = None
        rendered_in_session = 0
        rendered_tasks = 0
        render_attempts = 0
        worker_sessions = 0
        final_attempts: dict[str, RenderAttempt] = {}

        try:
            for task in ordered:
                if task.step_id in completed:
                    continue
                if _must_wait_for_dependency(task, completed, by_step):
                    completed[task.step_id] = _step_result(
                        task,
                        StepStatus.DEPENDENCY_WAIT,
                    )
                    checkpoints.save(plan.fingerprint, completed)
                    continue

                if session is None:
                    session = worker.open_session(run_workspace, plan)
                    worker_sessions += 1
                    rendered_in_session = 0

                final_attempt: RenderAttempt | None = None
                for attempt_number in range(1, self.max_attempts + 1):
                    render_attempts += 1
                    try:
                        final_attempt = worker.render(session, task, attempt_number)
                    except Exception as error:
                        final_attempt = RenderAttempt.retryable(type(error).__name__)
                    if final_attempt.disposition != "retryable":
                        break

                assert final_attempt is not None
                final_attempts[task.step_id] = final_attempt
                completed[task.step_id] = _result_from_attempt(task, final_attempt)
                rendered_tasks += 1
                rendered_in_session += 1
                checkpoints.save(plan.fingerprint, completed)

                if rendered_in_session == self.tasks_per_session:
                    worker.close_session(session)
                    session = None
        finally:
            if session is not None:
                worker.close_session(session)

        if not plan.tasks and not completed:
            checkpoints.save(plan.fingerprint, completed)

        return RenderScheduleResult(
            steps=tuple(completed[task.step_id] for task in ordered),
            metrics=RenderMetrics(
                total_tasks=len(plan.tasks),
                rendered_tasks=rendered_tasks,
                render_attempts=render_attempts,
                worker_sessions=worker_sessions,
                restored_steps=restored_steps,
            ),
            final_attempts=final_attempts,
        )


def _step_result(
    task: RenderTask,
    status: StepStatus,
    output_hash: str | None = None,
) -> StepResult:
    return StepResult(
        step_id=task.step_id,
        main_process_id=task.main_process_id,
        status=status,
        depends_on=task.depends_on,
        complete_state_hash=task.complete_state_hash,
        output_hash=output_hash,
    )


def _result_from_attempt(task: RenderTask, attempt: RenderAttempt) -> StepResult:
    if attempt.disposition == "passed":
        if not attempt.output_hash:
            raise ValueError("passed render attempt requires output_hash")
        return _step_result(task, StepStatus.PASSED, attempt.output_hash)
    if attempt.disposition == "questioned" or attempt.candidate_hashes:
        output_hash = attempt.output_hash or attempt.candidate_hashes[0]
        return _step_result(task, StepStatus.QUESTIONED, output_hash)
    if attempt.disposition in {"failed", "retryable"}:
        return _step_result(task, StepStatus.FAILED)
    raise ValueError(f"unsupported render disposition: {attempt.disposition}")


def _must_wait_for_dependency(
    task: RenderTask,
    completed: dict[str, StepResult],
    by_step: dict[str, RenderTask],
) -> bool:
    for dependency_id in task.depends_on:
        dependency_result = completed[dependency_id]
        dependency_task = by_step[dependency_id]
        if dependency_result.status is StepStatus.DEPENDENCY_WAIT:
            return True
        if (
            dependency_result.status in {StepStatus.FAILED, StepStatus.QUESTIONED}
            and dependency_task.blocks_dependents_on_failure
        ):
            return True
    return False


def _validate_and_order(
    plan: RenderPlan,
) -> tuple[tuple[RenderTask, ...], dict[str, RenderTask]]:
    by_step: dict[str, RenderTask] = {}
    task_ids: set[str] = set()
    position: dict[str, int] = {}
    for index, task in enumerate(plan.tasks):
        if task.step_id in by_step:
            raise ValueError(f"duplicate step_id: {task.step_id}")
        if task.task_id in task_ids:
            raise ValueError(f"duplicate task_id: {task.task_id}")
        by_step[task.step_id] = task
        task_ids.add(task.task_id)
        position[task.step_id] = index

    indegree = {step_id: 0 for step_id in by_step}
    children: dict[str, list[str]] = {step_id: [] for step_id in by_step}
    for task in plan.tasks:
        for dependency_id in task.depends_on:
            if dependency_id not in by_step:
                raise ValueError(
                    f"unknown dependency {dependency_id} for {task.step_id}"
                )
            indegree[task.step_id] += 1
            children[dependency_id].append(task.step_id)

    ready = sorted(
        (step_id for step_id, count in indegree.items() if count == 0),
        key=position.__getitem__,
        reverse=True,
    )
    ordered: list[RenderTask] = []
    while ready:
        step_id = ready.pop()
        ordered.append(by_step[step_id])
        for child_id in children[step_id]:
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                ready.append(child_id)
                ready.sort(key=position.__getitem__, reverse=True)

    if len(ordered) != len(plan.tasks):
        raise ValueError("render plan contains a dependency cycle")
    return tuple(ordered), by_step
