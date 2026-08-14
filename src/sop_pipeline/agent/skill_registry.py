from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .models import RunStatus
from .skill_contract import SkillResult


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    contract_version: str
    allowed_run_states: frozenset[RunStatus]
    allowed_next: tuple[str, ...]


@dataclass(frozen=True)
class SkillInvocation:
    schema_version: str
    run_id: str
    skill_name: str
    input_refs: tuple[str, ...]
    parameters: dict[str, Any]


def _definition(
    name: str,
    states: tuple[RunStatus, ...],
    next_skills: tuple[str, ...],
) -> SkillDefinition:
    return SkillDefinition(name, "agent-skill/v1", frozenset(states), next_skills)


_ANALYZING = (RunStatus.ANALYZING,)
_GENERATING = (RunStatus.GENERATING,)

AGENT_SKILL_DEFINITIONS: dict[str, SkillDefinition] = {
    "intake-preflight": _definition("intake-preflight", _ANALYZING, ("normalize-bom",)),
    "normalize-bom": _definition("normalize-bom", _ANALYZING, ("lock-assembly", "discover-cad")),
    "lock-assembly": _definition("lock-assembly", _ANALYZING, ("discover-cad",)),
    "discover-cad": _definition("discover-cad", _ANALYZING, ("map-bom-cad",)),
    "map-bom-cad": _definition("map-bom-cad", _ANALYZING, ("plan-assembly",)),
    "plan-assembly": _definition("plan-assembly", _ANALYZING, ("clarify-plan",)),
    "clarify-plan": _definition("clarify-plan", _ANALYZING, ("compile-render-jobs",)),
    "compile-render-jobs": _definition("compile-render-jobs", _GENERATING, ("render-batch",)),
    "render-batch": _definition("render-batch", _GENERATING, ("validate-repair",)),
    "validate-repair": _definition("validate-repair", _GENERATING, ("render-batch", "publish-delivery")),
    "publish-delivery": _definition(
        "publish-delivery",
        (RunStatus.GENERATING, RunStatus.NEEDS_REVIEW),
        ("resolve-step",),
    ),
    "resolve-step": _definition(
        "resolve-step",
        (RunStatus.NEEDS_REVIEW,),
        ("render-batch", "validate-repair", "publish-delivery"),
    ),
}


class SkillRegistry:
    def __init__(
        self,
        definitions: dict[str, SkillDefinition] | None = None,
    ) -> None:
        self.definitions = definitions or AGENT_SKILL_DEFINITIONS

    def execute(
        self,
        invocation: SkillInvocation,
        run_status: RunStatus,
        handler: Callable[[SkillInvocation], SkillResult],
    ) -> SkillResult:
        if invocation.schema_version != "skill-invocation/v1":
            raise ValueError("unsupported skill invocation schema")
        definition = self.definitions.get(invocation.skill_name)
        if definition is None:
            raise ValueError(f"unknown Agent skill: {invocation.skill_name}")
        if run_status not in definition.allowed_run_states:
            raise ValueError(
                f"{invocation.skill_name} is not allowed in {run_status.value}"
            )
        if _contains_forbidden_output_path(invocation.parameters):
            raise ValueError("skill parameters cannot select an arbitrary output path")
        result = handler(invocation)
        if result.skill != invocation.skill_name or result.run_id != invocation.run_id:
            raise ValueError("skill result identity does not match invocation")
        if not set(result.allowed_next).issubset(definition.allowed_next):
            raise ValueError("skill result requests a forbidden next transition")
        return result


def _contains_forbidden_output_path(value: Any, key: str = "") -> bool:
    normalized = key.casefold().replace("-", "_")
    if normalized in {"output_path", "output_directory", "delivery_directory"}:
        return True
    if isinstance(value, dict):
        return any(
            _contains_forbidden_output_path(child, str(child_key))
            for child_key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_output_path(child, key) for child in value)
    return False
