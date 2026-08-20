from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .artifacts import ArtifactStore
from .models import (
    AnalysisResult,
    ClarificationItem,
    ClarificationPacket,
    GenerationResult,
    PlanRevision,
    RunRecord,
    SkillStatus,
    StepResolution,
    StepResult,
    StepStatus,
)
from .progress import write_progress
from .skill_handlers import default_skill_handlers
from .skill_runtime import SkillRuntime
from .store import RunStore


class SkillPipelineError(RuntimeError):
    def __init__(self, skill: str, status: SkillStatus, message: str) -> None:
        super().__init__(f"{skill} [{status.value}]: {message}")
        self.skill = skill
        self.status = status


class PipelineOrchestrator:
    """Deterministic BOM-and-Creo pipeline with durable skill transitions."""

    def __init__(
        self,
        *,
        adapters: Mapping[str, Any] | None = None,
        experience_step_limit: int | None = None,
    ) -> None:
        self.adapters = dict(adapters or {})
        self.experience_step_limit = experience_step_limit
        self.runtime: SkillRuntime | None = None

    def bind(
        self,
        workspace: Path,
        store: RunStore,
        artifacts: ArtifactStore,
    ) -> None:
        del workspace
        self.runtime = SkillRuntime(
            store,
            artifacts,
            default_skill_handlers(),
            adapters=self.adapters,
        )

    def analyze(self, run: RunRecord) -> AnalysisResult:
        runtime = self._runtime()
        self._run(run.run_id, "intake-preflight")
        self._run(
            run.run_id,
            "normalize-bom",
            ("analysis/input-manifest.json",),
        )
        self._run(
            run.run_id,
            "lock-assembly",
            ("analysis/normalized-bom.json",),
        )
        self._run(
            run.run_id,
            "discover-cad",
            ("analysis/assembly-lock.json", "analysis/input-manifest.json"),
        )
        self._run(
            run.run_id,
            "map-bom-cad",
            (
                "analysis/normalized-bom.json",
                "analysis/model-inventory.json",
                "analysis/creo-cad-graph.json",
            ),
        )
        self._run(
            run.run_id,
            "plan-assembly",
            (
                "analysis/normalized-bom.json",
                "analysis/model-inventory.json",
                "analysis/bom-cad-map.json",
                "analysis/creo-cad-graph.json",
            ),
        )
        self._run(
            run.run_id,
            "clarify-plan",
            (
                "analysis/normalized-bom.json",
                "analysis/model-inventory.json",
                "analysis/bom-cad-map.json",
                "analysis/draft-plan.json",
                "analysis/formal-render-plan.json",
                "analysis/creo-cad-graph.json",
            ),
        )
        payload = runtime.artifacts.read_json(
            run.workspace, "analysis/clarification-packet.json"
        )
        return AnalysisResult(packet=_clarification_packet(payload), artifacts=())

    def generate(self, run: RunRecord, plan: PlanRevision) -> GenerationResult:
        self._run(
            run.run_id,
            "compile-render-jobs",
            (f"plans/locked-render-plan-{plan.revision:04d}.json",),
        )
        render_parameters: dict[str, Any] | None = None
        if self.experience_step_limit is not None:
            jobs = self._runtime().artifacts.read_json(
                run.workspace,
                f"plans/locked-render-jobs-{plan.revision:04d}.json",
            )
            render_parameters = {
                "step_ids": _representative_formal_step_ids(
                    jobs, self.experience_step_limit
                ),
                "result_scope_contract": "requested/v1",
            }
        self._run(
            run.run_id,
            "render-batch",
            (f"plans/locked-render-jobs-{plan.revision:04d}.json",),
            render_parameters,
        )
        self._run(
            run.run_id,
            "validate-repair",
            (
                f"plans/locked-render-jobs-{plan.revision:04d}.json",
                f"results/render-batch-{plan.revision:04d}.json",
            ),
        )
        self._run(
            run.run_id,
            "publish-delivery",
            (
                "analysis/normalized-bom.json",
                f"plans/locked-render-plan-{plan.revision:04d}.json",
                f"results/validation-{plan.revision:04d}.json",
                f"results/candidate-set-{plan.revision:04d}.json",
            ),
        )
        return self._generation_result(run)

    def resolve(
        self, run: RunRecord, resolution: StepResolution
    ) -> GenerationResult:
        current_steps = self._runtime().store.list_steps(run.run_id)
        prior_hashes = {step.step_id: step.output_hash for step in current_steps}
        prior_publication_ref = (
            f"results/publication-{run.plan_revision:04d}.json"
        )
        revision_number = self._next_step_revision(run)
        result = self._run(
            run.run_id,
            "resolve-step",
            (
                f"results/candidate-set-{run.plan_revision:04d}.json",
                f"results/validation-{run.plan_revision:04d}.json",
                f"plans/locked-render-plan-{run.plan_revision:04d}.json",
                "analysis/normalized-bom.json",
            ),
            {
                "step_id": resolution.step_id,
                "candidate_id": resolution.candidate_id,
                "instruction": resolution.instruction,
                "action": resolution.action,
                "metadata": dict(resolution.metadata),
                "revision": revision_number,
            },
        )
        revision_ref = _artifact_path(result, "step-revision")
        invalidation_ref = _artifact_path(result, "invalidation-set")
        review_decision_ref = _optional_artifact_path(
            result, "human-review-decision"
        )
        revision = self._runtime().artifacts.read_json(run.workspace, revision_ref)
        if revision.get("changes", {}).get("candidate_id"):
            publication_refs = [
                "analysis/normalized-bom.json",
                f"plans/locked-render-plan-{run.plan_revision:04d}.json",
                f"results/validation-{run.plan_revision:04d}.json",
                f"results/candidate-set-{run.plan_revision:04d}.json",
                prior_publication_ref,
                revision_ref,
                invalidation_ref,
            ]
            if review_decision_ref is not None:
                publication_refs.append(review_decision_ref)
            self._run(
                run.run_id,
                "publish-delivery",
                tuple(publication_refs),
            )
        else:
            self._run(
                run.run_id,
                "compile-render-jobs",
                (
                    f"plans/locked-render-plan-{run.plan_revision:04d}.json",
                    revision_ref,
                ),
            )
            self._run(
                run.run_id,
                "render-batch",
                (
                    f"plans/locked-render-jobs-{run.plan_revision:04d}.json",
                    invalidation_ref,
                    f"results/validation-{run.plan_revision:04d}.json",
                ),
            )
            self._run(
                run.run_id,
                "validate-repair",
                (
                    f"plans/locked-render-jobs-{run.plan_revision:04d}.json",
                    f"results/render-batch-{run.plan_revision:04d}.json",
                ),
            )
            self._run(
                run.run_id,
                "publish-delivery",
                (
                    "analysis/normalized-bom.json",
                    f"plans/locked-render-plan-{run.plan_revision:04d}.json",
                    f"results/validation-{run.plan_revision:04d}.json",
                    f"results/candidate-set-{run.plan_revision:04d}.json",
                    prior_publication_ref,
                    invalidation_ref,
                ),
            )
        generated = self._generation_result(run)
        affected = set(
            self._runtime().artifacts.read_json(run.workspace, invalidation_ref)["steps"]
        )
        for step in generated.steps:
            if step.step_id not in affected and prior_hashes.get(step.step_id) != step.output_hash:
                raise ValueError(f"局部再生成修改了无关步骤：{step.step_id}")
        return generated

    def _run(
        self,
        run_id: str,
        skill: str,
        refs: tuple[str, ...] = (),
        parameters: dict[str, Any] | None = None,
    ):
        runtime = self._runtime()
        run = runtime.store.get(run_id)
        write_progress(
            run.workspace,
            run_id=run_id,
            skill=skill,
            state="RUNNING",
        )
        try:
            result = runtime.execute(run_id, skill, refs, parameters)
        except Exception as error:
            write_progress(
                run.workspace,
                run_id=run_id,
                skill=skill,
                state="ERROR",
                message=str(error),
            )
            raise
        write_progress(
            run.workspace,
            run_id=run_id,
            skill=skill,
            state="COMPLETED",
            skill_status=result.status.value,
        )
        if result.status in {SkillStatus.BLOCKED, SkillStatus.RETRYABLE}:
            message = "; ".join(item.message for item in result.diagnostics)
            raise SkillPipelineError(skill, result.status, message or "Skill执行失败")
        return result

    def _generation_result(self, run: RunRecord) -> GenerationResult:
        payload = self._runtime().artifacts.read_json(
            run.workspace, f"results/publication-{run.plan_revision:04d}.json"
        )
        return GenerationResult(
            steps=tuple(
                StepResult(
                    step_id=str(item["step_id"]),
                    main_process_id=str(item["main_process_id"]),
                    status=StepStatus(item["status"]),
                    depends_on=tuple(item.get("depends_on", [])),
                    complete_state_hash=str(item["complete_state_hash"]),
                    output_hash=item.get("output_hash"),
                )
                for item in payload["steps"]
            ),
            delivery_directory=Path(payload["delivery_directory"]),
        )

    def _next_step_revision(self, run: RunRecord) -> int:
        root = run.workspace / "revisions"
        if not root.is_dir():
            return 1
        revisions = []
        for path in root.glob("step-revision-*.json"):
            try:
                revisions.append(int(path.stem.rsplit("-", 1)[-1]))
            except ValueError:
                continue
        return max(revisions, default=0) + 1

    def _runtime(self) -> SkillRuntime:
        if self.runtime is None:
            raise RuntimeError("PipelineOrchestrator尚未绑定Agent运行存储")
        return self.runtime


def _artifact_path(result, kind: str) -> str:
    for artifact in result.artifacts:
        if artifact.kind == kind:
            return artifact.relative_path
    raise KeyError(f"Skill结果缺少产物：{kind}")


def _optional_artifact_path(result, kind: str) -> str | None:
    for artifact in result.artifacts:
        if artifact.kind == kind:
            return artifact.relative_path
    return None


def _representative_formal_step_ids(payload: dict[str, Any], limit: int) -> list[str]:
    if limit < 1:
        raise ValueError("experience step limit must be positive")
    formal = [
        task
        for task in payload.get("tasks", [])
        if task.get("payload", {}).get("execution_mode") == "formal"
    ]
    if not formal:
        raise ValueError("experience mode found no formal render steps")
    by_camera: dict[str, list[dict[str, Any]]] = {}
    for task in formal:
        camera_id = str(task.get("payload", {}).get("camera_id", ""))
        by_camera.setdefault(camera_id, []).append(task)
    primary_camera, primary = max(
        by_camera.items(), key=lambda item: (len(item[1]), item[0])
    )
    primary.sort(key=lambda task: (bool(task.get("depends_on")), formal.index(task)))
    selected = primary[: min(2, limit)]
    secondary = [
        task
        for task in formal
        if str(task.get("payload", {}).get("camera_id", "")) != primary_camera
    ]
    secondary.sort(key=lambda task: (bool(task.get("depends_on")), formal.index(task)))
    if len(selected) < limit and secondary:
        selected.append(secondary[0])
    for task in formal:
        if len(selected) >= min(limit, len(formal)):
            break
        if task not in selected:
            selected.append(task)
    return [str(task["step_id"]) for task in selected]


def _clarification_packet(payload: dict[str, Any]) -> ClarificationPacket:
    return ClarificationPacket(
        schema_version=str(payload["schema_version"]),
        summary=str(payload["summary"]),
        items=tuple(
            ClarificationItem(
                item_id=str(item["item_id"]),
                category=str(item["category"]),
                question=str(item["question"]),
                options=tuple(str(value) for value in item["options"]),
                recommended_option=str(item["recommended_option"]),
                evidence=tuple(str(value) for value in item.get("evidence", [])),
                affected_steps=tuple(
                    str(value) for value in item.get("affected_steps", [])
                ),
            )
            for item in payload.get("items", [])
        ),
        facts=dict(payload.get("facts", {})),
    )
