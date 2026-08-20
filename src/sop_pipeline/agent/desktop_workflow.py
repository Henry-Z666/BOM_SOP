from __future__ import annotations

from .creo_discovery import CreoDiscoveryPort
from .formal_render_planner import FormalRenderPlan
from .models import (
    AnalysisResult,
    ClarificationItem,
    ClarificationPacket,
    GenerationResult,
    PlanRevision,
    RunRecord,
    StepResolution,
)


class DesktopWorkflow:
    """Desktop composition with optional real Creo facts and safe generation gates."""

    def __init__(
        self,
        discovery: CreoDiscoveryPort | None = None,
    ) -> None:
        # Import lazily because skill handlers reuse the clarification helpers
        # in this module. DesktopWorkflow is now only a compatibility facade;
        # the executable path is the same PipelineOrchestrator used by the GUI.
        from .pipeline_orchestrator import PipelineOrchestrator

        adapters = {}
        if discovery is not None:
            adapters["creo_discovery"] = discovery
        self._delegate = PipelineOrchestrator(adapters=adapters)

    def bind(self, workspace, store, artifacts) -> None:
        self._delegate.bind(workspace, store, artifacts)

    def analyze(self, run: RunRecord) -> AnalysisResult:
        return self._delegate.analyze(run)

    def generate(self, run: RunRecord, plan: PlanRevision) -> GenerationResult:
        return self._delegate.generate(run, plan)

    def resolve(self, run: RunRecord, resolution: StepResolution) -> GenerationResult:
        return self._delegate.resolve(run, resolution)


def _mapping_questions(mapping) -> tuple[ClarificationItem, ...]:
    result: list[ClarificationItem] = []
    if mapping.ambiguous_rows:
        rows = ", ".join(str(value) for value in mapping.ambiguous_rows[:20])
        result.append(
            ClarificationItem(
                item_id="ambiguous-creo-occurrences",
                category="CONFIRMATION",
                question=f"BOM 第 {rows} 行在同一父装配下仍有多组 occurrence 候选，是否生成候选方案供后续选图？",
                options=("保留候选并继续", "返回检查 BOM/CAD"),
                recommended_option="保留候选并继续",
                evidence=("Agent 未按文件顺序截取 occurrence，也未猜测重复件身份。",),
            )
        )
    unresolved = tuple(sorted(set(mapping.missing_rows + mapping.quantity_mismatch_rows)))
    if unresolved:
        rows = ", ".join(str(value) for value in unresolved[:20])
        result.append(
            ClarificationItem(
                item_id="creo-quantity-mismatch",
                category="CONFIRMATION",
                question=f"BOM 第 {rows} 行的 Creo occurrence 数量不足或缺失，是否保留为待确认步骤并继续？",
                options=("保留待确认步骤并继续", "返回检查 BOM/CAD"),
                recommended_option="保留待确认步骤并继续",
                evidence=("缺失实体不会由脚本虚构。",),
            )
        )
    return tuple(result)


def _planning_questions(
    plan: FormalRenderPlan,
) -> tuple[ClarificationItem, ...]:
    result: list[ClarificationItem] = []
    missing = [
        item for item in plan.diagnostics if item.code == "NO_NATIVE_RECEIVER_GEOMETRY"
    ]
    if missing:
        rows = tuple(sorted({row for item in missing for row in item.bom_rows}))
        result.append(
            ClarificationItem(
                item_id="unproven-installation-geometry",
                category="CONFIRMATION",
                question=(
                    "部分安装对象只有最终位置或 FIX 约束，无法从 CAD 证明接收面和离开方向。"
                    "是否先保留为待确认候选并继续生成其他步骤？"
                ),
                options=("保留待确认候选并继续", "返回检查 CAD 约束"),
                recommended_option="保留待确认候选并继续",
                evidence=(
                    "涉及 BOM 行：" + ", ".join(str(value) for value in rows[:20]),
                    "Agent 没有使用零件中心到总装中心的猜测向量。",
                ),
                affected_steps=tuple(
                    sorted({step for item in missing for step in item.affected_steps})
                ),
            )
        )
    for item in plan.diagnostics:
        if item.code != "SUBASSEMBLY_SCOPE_UNCONFIRMED":
            continue
        row_id = item.bom_rows[0] if item.bom_rows else 0
        item_id = f"subassembly-scope-{row_id:04d}"
        result.append(
            ClarificationItem(
                item_id=item_id,
                category="CONFIRMATION",
                question=item.message,
                options=(
                    "按BOM在本工位展开内部构造",
                    "作为已完成整体安装",
                ),
                recommended_option="按BOM在本工位展开内部构造",
                evidence=(
                    "CAD 同时证明了子装配内部约束和该子装配对外安装约束。",
                    "此选择会改变步骤数量和后续完整安装状态，因此必须在生成前锁定。",
                    "缺少明确的自制/外购或工位范围字段时，确定性默认按 BOM 层级展开。",
                ),
                affected_steps=item.affected_steps,
            )
        )
    foundation = [
        item for item in plan.diagnostics if item.code == "FOUNDATION_ASSUMED_FROM_BOM_ORDER"
    ]
    if foundation:
        result.append(
            ClarificationItem(
                item_id="root-foundation-selection",
                category="CONFIRMATION",
                question="BOM 首个根级物料缺少 Creo FIX 证据，是否按 BOM 顺序作为总装基体？",
                options=("按BOM首项作为基体", "返回检查最终总装"),
                recommended_option="按BOM首项作为基体",
                evidence=tuple(item.message for item in foundation),
            )
        )
    return tuple(result)
