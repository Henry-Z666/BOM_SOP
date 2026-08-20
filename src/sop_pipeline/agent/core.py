from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from .artifacts import ArtifactStore
from .formal_render_planner import formal_render_plan_from_dict, lock_formal_render_plan
from .models import (
    AnalysisResult,
    ClarificationItem,
    ClarificationPacket,
    GenerationResult,
    PlanRevision,
    RunOutcome,
    RunRecord,
    RunStatus,
    StepResolution,
    StepResult,
    StepStatus,
)
from .ports import WorkflowPort
from .pipeline_orchestrator import SkillPipelineError
from .models import SkillStatus
from .store import RunStore


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _input_fingerprint(bom_file: Path, cad_directory: Path) -> str:
    digest = hashlib.sha256()
    digest.update(b"agent-input/v1\0")
    digest.update(_file_digest(bom_file).encode("ascii"))
    for path in sorted(item for item in cad_directory.rglob("*") if item.is_file()):
        relative = path.relative_to(cad_directory).as_posix()
        digest.update(b"\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_file_digest(path).encode("ascii"))
    return "sha256:" + digest.hexdigest()


class AgentCore:
    """Durable interface used by the desktop UI and integration tests."""

    def __init__(self, workspace: Path, workflow: WorkflowPort | None = None) -> None:
        self._workspace = Path(workspace).resolve()
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._runs_root = self._workspace / "runs"
        self._runs_root.mkdir(exist_ok=True)
        self._store = RunStore(self._workspace / "agent.sqlite3")
        self._artifacts = ArtifactStore(self._store)
        self._workflow = workflow
        bind = getattr(workflow, "bind", None)
        if callable(bind):
            bind(self._workspace, self._store, self._artifacts)

    def create_run(self, bom_file: Path, cad_directory: Path) -> str:
        bom_file = Path(bom_file).resolve()
        cad_directory = Path(cad_directory).resolve()
        if not bom_file.is_file():
            raise FileNotFoundError(f"找不到 BOM 文件：{bom_file}")
        if not cad_directory.is_dir():
            raise NotADirectoryError(f"找不到 CAD 文件夹：{cad_directory}")

        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:12]
        run_workspace = self._runs_root / run_id
        run_workspace.mkdir()
        now = datetime.now(timezone.utc).isoformat()
        run = RunRecord(
            run_id=run_id,
            bom_file=bom_file,
            cad_directory=cad_directory,
            workspace=run_workspace,
            status=RunStatus.ANALYZING,
            input_fingerprint=_input_fingerprint(bom_file, cad_directory),
            plan_revision=0,
            created_at=now,
            updated_at=now,
        )
        self._store.add(run)
        return run_id

    def get_run(self, run_id: str) -> RunRecord:
        return self._store.get(run_id)

    def _require_workflow(self) -> WorkflowPort:
        if self._workflow is None:
            raise RuntimeError("AgentCore 尚未配置流水线 adapter")
        return self._workflow

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def analyze(self, run_id: str) -> ClarificationPacket:
        run = self._store.get(run_id)
        if run.status is not RunStatus.ANALYZING:
            raise ValueError("只有 ANALYZING 状态可以执行输入分析")
        try:
            result: AnalysisResult = self._require_workflow().analyze(run)
        except SkillPipelineError as error:
            if error.status is SkillStatus.BLOCKED:
                self._store.transition(
                    run_id,
                    expected={RunStatus.ANALYZING},
                    status=RunStatus.BLOCKED_SYSTEM,
                    updated_at=self._now(),
                )
            raise
        packet = result.packet
        if packet.schema_version != "clarification-packet/v1":
            raise ValueError(f"不支持的释疑包版本：{packet.schema_version}")
        for artifact in result.artifacts:
            self._artifacts.write_json(
                run_id=run_id,
                run_workspace=run.workspace,
                kind=artifact.kind,
                relative_path=artifact.relative_path,
                value=artifact.value,
            )
        self._artifacts.write_json(
            run_id=run_id,
            run_workspace=run.workspace,
            kind="clarification-packet",
            relative_path="analysis/clarification-packet.json",
            value=packet,
        )
        self._store.transition(
            run_id,
            expected={RunStatus.ANALYZING},
            status=RunStatus.AWAITING_CONFIRMATION,
            updated_at=self._now(),
        )
        return packet

    def confirm(self, run_id: str, answers: dict[str, str]) -> PlanRevision:
        run = self._store.get(run_id)
        if run.status is not RunStatus.AWAITING_CONFIRMATION:
            raise ValueError("只有 AWAITING_CONFIRMATION 状态可以锁定生成方案")
        packet = self._load_clarification(run)
        questions = {item.item_id: item for item in packet.items if item.category == "CONFIRMATION"}
        unknown = sorted(set(answers) - set(questions))
        if unknown:
            raise ValueError("确认答案包含未知释疑项：" + ", ".join(unknown))
        for item_id, item in questions.items():
            if item_id not in answers:
                raise ValueError(f"释疑项 {item_id} 尚未确认")
            if answers[item_id] not in item.options:
                raise ValueError(f"释疑项 {item_id} 的答案不属于确认卡选项")
        revision = run.plan_revision + 1
        canonical = json.dumps(answers, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        packet_data = self._artifacts.read_json(run.workspace, "analysis/clarification-packet.json")
        packet_canonical = json.dumps(
            packet_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        analysis_fingerprint = "sha256:" + hashlib.sha256(packet_canonical.encode("utf-8")).hexdigest()
        digest = hashlib.sha256(
            f"plan-revision/v1\0{run.input_fingerprint}\0{analysis_fingerprint}\0{canonical}".encode("utf-8")
        ).hexdigest()
        plan = PlanRevision(
            run_id=run_id,
            revision=revision,
            answers=dict(answers),
            analysis_fingerprint=analysis_fingerprint,
            fingerprint="sha256:" + digest,
            created_at=self._now(),
        )
        formal_plan_path = run.workspace / "analysis" / "formal-render-plan.json"
        locked_render_plan = None
        if formal_plan_path.is_file():
            formal_plan = formal_render_plan_from_dict(
                self._artifacts.read_json(run.workspace, "analysis/formal-render-plan.json")
            )
            locked_render_plan = lock_formal_render_plan(
                formal_plan,
                plan.answers,
            )
        self._artifacts.write_json(
            run_id=run_id,
            run_workspace=run.workspace,
            kind="plan-revision",
            relative_path=f"plans/plan-revision-{revision:04d}.json",
            value=plan,
        )
        if locked_render_plan is not None:
            self._artifacts.write_json(
                run_id=run_id,
                run_workspace=run.workspace,
                kind="locked-render-plan",
                relative_path=f"plans/locked-render-plan-{revision:04d}.json",
                value=locked_render_plan,
            )
        self._store.transition(
            run_id,
            expected={RunStatus.AWAITING_CONFIRMATION},
            status=RunStatus.GENERATING,
            updated_at=self._now(),
            plan_revision=revision,
        )
        return plan

    def _load_clarification(self, run: RunRecord) -> ClarificationPacket:
        data: dict[str, Any] = self._artifacts.read_json(
            run.workspace, "analysis/clarification-packet.json"
        )
        return ClarificationPacket(
            schema_version=str(data["schema_version"]),
            summary=str(data["summary"]),
            items=tuple(
                ClarificationItem(
                    item_id=str(item["item_id"]),
                    category=str(item["category"]),
                    question=str(item["question"]),
                    options=tuple(str(option) for option in item["options"]),
                    recommended_option=str(item["recommended_option"]),
                    evidence=tuple(str(value) for value in item.get("evidence", [])),
                    affected_steps=tuple(str(value) for value in item.get("affected_steps", [])),
                )
                for item in data.get("items", [])
            ),
            facts=dict(data.get("facts", {})),
        )

    def _load_plan(self, run: RunRecord) -> PlanRevision:
        data: dict[str, Any] = self._artifacts.read_json(
            run.workspace, f"plans/plan-revision-{run.plan_revision:04d}.json"
        )
        return PlanRevision(
            run_id=str(data["run_id"]),
            revision=int(data["revision"]),
            answers={str(key): str(value) for key, value in data["answers"].items()},
            analysis_fingerprint=str(data["analysis_fingerprint"]),
            fingerprint=str(data["fingerprint"]),
            created_at=str(data["created_at"]),
        )

    def _finish_generation(
        self,
        run: RunRecord,
        result: GenerationResult,
        *,
        artifact_kind: str = "generation-result",
        artifact_path: str | None = None,
    ) -> RunOutcome:
        delivery = Path(result.delivery_directory).resolve()
        root = run.workspace.resolve()
        if delivery != root and root not in delivery.parents:
            raise ValueError("交付目录必须位于当前运行批次内")
        delivery.mkdir(parents=True, exist_ok=True)
        self._store.replace_steps(run.run_id, result.steps)
        status = (
            RunStatus.COMPLETED
            if result.steps and all(step.status is StepStatus.PASSED for step in result.steps)
            else RunStatus.NEEDS_REVIEW
        )
        self._artifacts.write_json(
            run_id=run.run_id,
            run_workspace=run.workspace,
            kind=artifact_kind,
            relative_path=artifact_path or f"results/generation-{run.plan_revision:04d}.json",
            value=result,
        )
        self._store.transition(
            run.run_id,
            expected={RunStatus.GENERATING, RunStatus.NEEDS_REVIEW},
            status=status,
            updated_at=self._now(),
        )
        return RunOutcome(
            run_id=run.run_id,
            status=status,
            steps=result.steps,
            delivery_directory=delivery,
        )

    def generate(self, run_id: str) -> RunOutcome:
        run = self._store.get(run_id)
        if run.status is not RunStatus.GENERATING:
            raise ValueError("只有 GENERATING 状态可以执行正式生成")
        try:
            result = self._require_workflow().generate(run, self._load_plan(run))
        except SkillPipelineError as error:
            if error.status is SkillStatus.BLOCKED:
                self._store.transition(
                    run_id,
                    expected={RunStatus.GENERATING},
                    status=RunStatus.BLOCKED_SYSTEM,
                    updated_at=self._now(),
                )
            raise
        return self._finish_generation(run, result)

    @staticmethod
    def _affected_steps(
        steps: tuple[StepResult, ...], target_step_id: str
    ) -> set[str]:
        known = {step.step_id for step in steps}
        if target_step_id not in known:
            raise ValueError(f"找不到待释疑步骤：{target_step_id}")
        affected = {target_step_id}
        changed = True
        while changed:
            changed = False
            for step in steps:
                if step.step_id not in affected and affected.intersection(step.depends_on):
                    affected.add(step.step_id)
                    changed = True
        return affected

    @staticmethod
    def _assert_unaffected_unchanged(
        before: tuple[StepResult, ...],
        after: tuple[StepResult, ...],
        affected: set[str],
    ) -> None:
        previous = {step.step_id: step for step in before}
        current = {step.step_id: step for step in after}
        if previous.keys() != current.keys():
            raise ValueError("局部再生成不得增加或删除既有安装步骤")
        for step_id in previous.keys() - affected:
            left, right = previous[step_id], current[step_id]
            if (
                left.output_hash != right.output_hash
                or left.complete_state_hash != right.complete_state_hash
                or left.status != right.status
            ):
                raise ValueError(f"局部再生成修改了无关步骤：{step_id}")

    def resolve(self, run_id: str, resolution: StepResolution) -> RunOutcome:
        run = self._store.get(run_id)
        if run.status is not RunStatus.NEEDS_REVIEW:
            raise ValueError("只有 NEEDS_REVIEW 状态可以提交释疑")
        supplied = sum(
            bool(value)
            for value in (
                resolution.candidate_id,
                resolution.instruction,
                resolution.action,
            )
        )
        if supplied != 1:
            raise ValueError("释疑必须且只能提供候选图、修正说明或人工决定之一")
        before = self._store.list_steps(run_id)
        target = next((step for step in before if step.step_id == resolution.step_id), None)
        if target is None:
            raise ValueError(f"找不到待释疑步骤：{resolution.step_id}")
        if target.status not in {StepStatus.QUESTIONED, StepStatus.FAILED}:
            raise ValueError(f"步骤 {resolution.step_id} 当前不需要释疑")
        affected = self._affected_steps(before, resolution.step_id)
        result = self._require_workflow().resolve(run, resolution)
        self._assert_unaffected_unchanged(before, result.steps, affected)
        suffix = uuid4().hex[:12]
        self._artifacts.write_json(
            run_id=run_id,
            run_workspace=run.workspace,
            kind="step-resolution",
            relative_path=f"resolutions/{resolution.step_id}-{suffix}.json",
            value=resolution,
        )
        return self._finish_generation(
            run,
            result,
            artifact_kind="resolution-result",
            artifact_path=f"results/resolution-{resolution.step_id}-{suffix}.json",
        )

    def resume(self, run_id: str) -> RunOutcome:
        run = self._store.get(run_id)
        if run.status is RunStatus.BLOCKED_SYSTEM and run.plan_revision > 0:
            self._store.transition(
                run_id,
                expected={RunStatus.BLOCKED_SYSTEM},
                status=RunStatus.GENERATING,
                updated_at=self._now(),
            )
            return self.generate(run_id)
        if run.status is RunStatus.GENERATING:
            return self.generate(run_id)
        steps = self._store.list_steps(run_id)
        delivery = run.workspace / "delivery"
        return RunOutcome(
            run_id=run_id,
            status=run.status,
            steps=steps,
            delivery_directory=delivery if delivery.exists() else None,
        )
