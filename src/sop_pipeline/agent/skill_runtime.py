from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .artifacts import ArtifactStore
from .models import ArtifactRef, RunRecord, SkillStatus
from .skill_contract import Diagnostic, RetryScope, SkillResult
from .skill_registry import SkillInvocation, SkillRegistry
from .store import RunStore


@dataclass(frozen=True)
class SkillArtifactValue:
    kind: str
    value: Any


@dataclass(frozen=True)
class SkillHandlerOutput:
    status: SkillStatus
    artifacts: tuple[SkillArtifactValue, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    retry_scope: RetryScope | None = None
    allowed_next: tuple[str, ...] | None = None


@dataclass(frozen=True)
class SkillContext:
    run: RunRecord
    artifact_store: ArtifactStore
    run_store: RunStore
    adapters: Mapping[str, Any] = field(default_factory=dict)

    def read_json(self, reference: str) -> dict[str, Any]:
        artifact = self.run_store.resolve_artifact(self.run.run_id, reference)
        return self.artifact_store.read_json(
            self.run.workspace, artifact.relative_path
        )

    def artifact(self, reference: str) -> ArtifactRef:
        return self.run_store.resolve_artifact(self.run.run_id, reference)


class SkillHandler(Protocol):
    def __call__(
        self, context: SkillContext, invocation: SkillInvocation
    ) -> SkillHandlerOutput: ...


_OUTPUT_PATHS: dict[str, dict[str, str]] = {
    "intake-preflight": {
        "preflight-report": "analysis/preflight-report.json",
        "input-manifest": "analysis/input-manifest.json",
    },
    "normalize-bom": {"normalized-bom": "analysis/normalized-bom.json"},
    "lock-assembly": {
        "model-inventory": "analysis/model-inventory.json",
        "assembly-lock": "analysis/assembly-lock.json",
    },
    "discover-cad": {"creo-cad-graph": "analysis/creo-cad-graph.json"},
    "map-bom-cad": {"bom-cad-map": "analysis/bom-cad-map.json"},
    "plan-assembly": {
        "draft-plan": "analysis/draft-plan.json",
        "formal-render-plan": "analysis/formal-render-plan.json",
    },
    "clarify-plan": {
        "clarification-packet": "analysis/clarification-packet.json",
        "plan-recommendations": "analysis/plan-recommendations.json",
    },
    "compile-render-jobs": {
        "locked-render-jobs": "plans/locked-render-jobs-{revision:04d}.json",
    },
    "render-batch": {
        "render-batch-result": "results/render-batch-{revision:04d}.json",
    },
    "validate-repair": {
        "validation-result": "results/validation-{revision:04d}.json",
        "candidate-set": "results/candidate-set-{revision:04d}.json",
    },
    "publish-delivery": {
        "publication-result": "results/publication-{revision:04d}.json",
    },
    "resolve-step": {
        "step-revision": "revisions/step-revision-{revision:04d}.json",
        "invalidation-set": "revisions/invalidation-set-{revision:04d}.json",
    },
}


class SkillRuntime:
    """Execute versioned Agent skills through one durable, testable interface."""

    def __init__(
        self,
        store: RunStore,
        artifacts: ArtifactStore,
        handlers: Mapping[str, SkillHandler],
        *,
        adapters: Mapping[str, Any] | None = None,
        registry: SkillRegistry | None = None,
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self.handlers = dict(handlers)
        self.adapters = dict(adapters or {})
        self.registry = registry or SkillRegistry()

    def execute(
        self,
        run_id: str,
        skill_name: str,
        input_refs: tuple[str, ...] = (),
        parameters: dict[str, Any] | None = None,
    ) -> SkillResult:
        run = self.store.get(run_id)
        invocation = SkillInvocation(
            schema_version="skill-invocation/v1",
            run_id=run_id,
            skill_name=skill_name,
            input_refs=tuple(input_refs),
            parameters=dict(parameters or {}),
        )
        input_artifacts = tuple(
            self.store.resolve_artifact(run_id, reference) for reference in input_refs
        )
        for artifact in input_artifacts:
            self._verify_artifact(run, artifact)
        fingerprint = self._fingerprint(run, invocation, input_artifacts)
        cached = self.store.find_skill_result(run_id, skill_name, fingerprint)
        if cached is not None and cached.status is SkillStatus.PASSED:
            if all(self._artifact_is_current(run, item) for item in cached.artifacts):
                return cached

        handler = self.handlers.get(skill_name)
        if handler is None:
            raise ValueError(f"Agent skill has no executable handler: {skill_name}")

        def invoke(_: SkillInvocation) -> SkillResult:
            output = handler(
                SkillContext(run, self.artifacts, self.store, self.adapters),
                invocation,
            )
            references = tuple(
                self.artifacts.write_json(
                    run_id=run_id,
                    run_workspace=run.workspace,
                    kind=item.kind,
                    relative_path=self._output_path(
                        skill_name,
                        item.kind,
                        int(invocation.parameters.get("revision", run.plan_revision)),
                    ),
                    value=item.value,
                )
                for item in output.artifacts
            )
            definition = self.registry.definitions[skill_name]
            return SkillResult(
                schema_version="agent-skill-result/v1",
                skill=skill_name,
                run_id=run_id,
                status=output.status,
                input_fingerprint=fingerprint,
                artifacts=references,
                diagnostics=output.diagnostics,
                retry_scope=output.retry_scope,
                allowed_next=(
                    definition.allowed_next
                    if output.allowed_next is None
                    else output.allowed_next
                ),
            )

        result = self.registry.execute(invocation, run.status, invoke)
        self.store.save_skill_result(result, datetime.now(timezone.utc).isoformat())
        return result

    def tool_definitions(self) -> tuple[dict[str, Any], ...]:
        """Return provider-neutral JSON Schema descriptions for Qwen tools."""

        return tuple(
            {
                "name": definition.name,
                "contract_version": definition.contract_version,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string"},
                        "input_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "parameters": {"type": "object"},
                    },
                    "required": ["run_id", "input_refs"],
                    "additionalProperties": False,
                },
            }
            for definition in self.registry.definitions.values()
        )

    @staticmethod
    def _fingerprint(
        run: RunRecord,
        invocation: SkillInvocation,
        artifacts: tuple[ArtifactRef, ...],
    ) -> str:
        payload = {
            "schema_version": "skill-execution-fingerprint/v1",
            "skill": invocation.skill_name,
            "contract": "agent-skill/v1",
            "run_input": run.input_fingerprint,
            "inputs": [
                {
                    "kind": item.kind,
                    "relative_path": item.relative_path,
                    "sha256": item.sha256,
                }
                for item in artifacts
            ],
            "parameters": invocation.parameters,
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "sha256:" + sha256(encoded).hexdigest()

    @staticmethod
    def _output_path(skill: str, kind: str, revision: int) -> str:
        paths = _OUTPUT_PATHS.get(skill, {})
        template = paths.get(kind)
        if template is None:
            raise ValueError(f"{skill} cannot publish undeclared artifact kind: {kind}")
        return template.format(revision=revision)

    @staticmethod
    def _artifact_is_current(run: RunRecord, artifact: ArtifactRef) -> bool:
        path = run.workspace / artifact.relative_path
        if not path.is_file():
            return False
        digest = "sha256:" + sha256(path.read_bytes()).hexdigest()
        return digest == artifact.sha256

    def _verify_artifact(self, run: RunRecord, artifact: ArtifactRef) -> None:
        if artifact.run_id != run.run_id:
            raise ValueError("Skill input cannot reference another run")
        if not self._artifact_is_current(run, artifact):
            raise ValueError(f"Skill input artifact hash mismatch: {artifact.relative_path}")
