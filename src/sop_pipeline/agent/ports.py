from __future__ import annotations

from typing import Protocol

from .models import (
    AnalysisResult,
    GenerationResult,
    PlanRevision,
    RunRecord,
    StepResolution,
)


class WorkflowPort(Protocol):
    """External seam implemented by the real pipeline and test adapters."""

    def analyze(self, run: RunRecord) -> AnalysisResult: ...

    def generate(self, run: RunRecord, plan: PlanRevision) -> GenerationResult: ...

    def resolve(self, run: RunRecord, resolution: StepResolution) -> GenerationResult: ...
