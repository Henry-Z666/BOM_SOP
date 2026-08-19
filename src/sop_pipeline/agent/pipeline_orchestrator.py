from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .artifacts import ArtifactStore
from .formal_render_planner import (
    compile_formal_render_plan,
    formal_render_plan_from_dict,
    lock_formal_render_plan,
)
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
from .skill_handlers import _draft, _mapping, _normalized, default_skill_handlers
from .skill_runtime import SkillRuntime
from .step_revision import (
    RevisionKind,
    StepDependencyGraph,
    StepRevision,
    validate_revision,
)
from .store import RunStore


class SkillPipelineError(RuntimeError):
    def __init__(self, skill: str, status: SkillStatus, message: str) -> None:
        super().__init__(f"{skill} [{status.value}]: {message}")
        self.skill = skill
        self.status = status


class PipelineOrchestrator:
    """Deterministic Agent pipeline; models advise but never choose transitions."""

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
        recovered_refs = self._recover_stale_native_geometry(
            run,
            resolution,
            revision_number,
        )
        if recovered_refs is None:
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
                    "revision": revision_number,
                },
            )
            revision_ref = _artifact_path(result, "step-revision")
            invalidation_ref = _artifact_path(result, "invalidation-set")
        else:
            revision_ref, invalidation_ref = recovered_refs
        revision = self._runtime().artifacts.read_json(run.workspace, revision_ref)
        if revision.get("changes", {}).get("candidate_id"):
            self._run(
                run.run_id,
                "publish-delivery",
                (
                    "analysis/normalized-bom.json",
                    f"plans/locked-render-plan-{run.plan_revision:04d}.json",
                    f"results/validation-{run.plan_revision:04d}.json",
                    f"results/candidate-set-{run.plan_revision:04d}.json",
                    prior_publication_ref,
                    revision_ref,
                    invalidation_ref,
                ),
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

    def _recover_stale_native_geometry(
        self,
        run: RunRecord,
        resolution: StepResolution,
        revision_number: int,
    ) -> tuple[str, str] | None:
        """Upgrade a persisted pre-fix plan without restarting its run."""

        if not resolution.instruction:
            return None
        runtime = self._runtime()
        locked_ref = f"plans/locked-render-plan-{run.plan_revision:04d}.json"
        locked = formal_render_plan_from_dict(
            runtime.artifacts.read_json(run.workspace, locked_ref)
        )
        stale_step = next(
            (step for step in locked.steps if step.step_id == resolution.step_id),
            None,
        )
        validation = runtime.artifacts.read_json(
            run.workspace, f"results/validation-{run.plan_revision:04d}.json"
        )
        validation_step = next(
            (
                item
                for item in validation.get("steps", [])
                if str(item.get("step_id")) == resolution.step_id
            ),
            {},
        )
        plan_is_stale = (
            stale_step is not None
            and "NO_NATIVE_RECEIVER_GEOMETRY" in stale_step.diagnostics
        )
        validation_is_stale = (
            str(validation_step.get("error_code") or "")
            == "NO_NATIVE_RECEIVER_GEOMETRY"
        )
        if stale_step is None or not (plan_is_stale or validation_is_stale):
            return None

        if plan_is_stale:
            compiled = compile_formal_render_plan(
                _normalized(
                    runtime.artifacts.read_json(
                        run.workspace, "analysis/normalized-bom.json"
                    )
                ),
                _draft(
                    runtime.artifacts.read_json(
                        run.workspace, "analysis/draft-plan.json"
                    )
                ),
                _mapping(
                    runtime.artifacts.read_json(
                        run.workspace, "analysis/bom-cad-map.json"
                    )
                ),
                runtime.artifacts.read_json(
                    run.workspace, "analysis/creo-cad-graph.json"
                ),
            )
            answers = {
                item_id: (
                    "按BOM在本工位展开内部构造"
                    if decision == "expand"
                    else "作为已完成整体安装"
                )
                for item_id, decision in locked.scope_decisions.items()
            }
            repaired = lock_formal_render_plan(compiled, answers)
        else:
            repaired = locked
        repaired_step = next(
            (step for step in repaired.steps if step.step_id == resolution.step_id),
            None,
        )
        if (
            repaired_step is None
            or repaired_step.status != "ready"
            or not repaired_step.receiver_occurrences
            or repaired_step.receiver_normal_root is None
            or "NO_NATIVE_RECEIVER_GEOMETRY" in repaired_step.diagnostics
        ):
            return None

        runtime.artifacts.write_json(
            run_id=run.run_id,
            run_workspace=run.workspace,
            kind="locked-render-plan",
            relative_path=locked_ref,
            value=repaired,
        )
        revision = validate_revision(
            StepRevision(
                revision=revision_number,
                step_id=resolution.step_id,
                kind=RevisionKind.COMPLETE_STATE,
                changes={
                    "moving_occurrences": list(repaired_step.moving_occurrences),
                    "receiver_occurrences": list(repaired_step.receiver_occurrences),
                    "direction": list(repaired_step.receiver_normal_root),
                },
            )
        )
        graph = StepDependencyGraph(
            {
                step.step_id: step.depends_on
                for step in repaired.steps
            }
        )
        invalidated = tuple(sorted(graph.invalidated_by(revision)))
        revision_ref = f"revisions/step-revision-{revision_number:04d}.json"
        invalidation_ref = f"revisions/invalidation-set-{revision_number:04d}.json"
        runtime.artifacts.write_json(
            run_id=run.run_id,
            run_workspace=run.workspace,
            kind="step-revision",
            relative_path=revision_ref,
            value={
                "schema_version": "step-revision/v1",
                "revision": revision.revision,
                "step_id": revision.step_id,
                "kind": revision.kind.value,
                "changes": revision.changes,
                "source": "persisted-plan-native-geometry-recovery/v1",
                "instruction": resolution.instruction,
            },
        )
        runtime.artifacts.write_json(
            run_id=run.run_id,
            run_workspace=run.workspace,
            kind="invalidation-set",
            relative_path=invalidation_ref,
            value={
                "schema_version": "invalidation-set/v1",
                "step_revision": revision.revision,
                "steps": invalidated,
            },
        )
        return revision_ref, invalidation_ref

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
