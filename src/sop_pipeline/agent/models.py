from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class RunStatus(StrEnum):
    ANALYZING = "ANALYZING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    GENERATING = "GENERATING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    COMPLETED = "COMPLETED"
    BLOCKED_SYSTEM = "BLOCKED_SYSTEM"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    QUESTIONED = "QUESTIONED"
    FAILED = "FAILED"
    DEPENDENCY_WAIT = "DEPENDENCY_WAIT"


class SkillStatus(StrEnum):
    PASSED = "passed"
    RETRYABLE = "retryable"
    QUESTIONED = "questioned"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    bom_file: Path
    cad_directory: Path
    workspace: Path
    status: RunStatus
    input_fingerprint: str
    plan_revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ClarificationItem:
    item_id: str
    category: str
    question: str
    options: tuple[str, ...]
    recommended_option: str
    evidence: tuple[str, ...] = ()
    affected_steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClarificationPacket:
    schema_version: str
    summary: str
    items: tuple[ClarificationItem, ...]
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProducedArtifact:
    kind: str
    relative_path: str
    value: Any


@dataclass(frozen=True)
class AnalysisResult:
    packet: ClarificationPacket
    artifacts: tuple[ProducedArtifact, ...] = ()


@dataclass(frozen=True)
class PlanRevision:
    run_id: str
    revision: int
    answers: dict[str, str]
    analysis_fingerprint: str
    fingerprint: str
    created_at: str


@dataclass(frozen=True)
class StepResult:
    step_id: str
    main_process_id: str
    status: StepStatus
    depends_on: tuple[str, ...]
    complete_state_hash: str
    output_hash: str | None = None


@dataclass(frozen=True)
class GenerationResult:
    steps: tuple[StepResult, ...]
    delivery_directory: Path


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    status: RunStatus
    steps: tuple[StepResult, ...] = ()
    delivery_directory: Path | None = None


@dataclass(frozen=True)
class StepResolution:
    step_id: str
    candidate_id: str | None = None
    instruction: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    run_id: str
    kind: str
    relative_path: str
    sha256: str
    created_at: str
