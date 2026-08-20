from __future__ import annotations

from .bom_normalizer import normalize_bom
from .draft_planner import create_draft_plan
from .model_inventory import inventory_models
from .models import (
    AnalysisResult,
    ClarificationItem,
    ClarificationPacket,
    GenerationResult,
    PlanRevision,
    ProducedArtifact,
    RunRecord,
    StepResolution,
)


class LocalAnalysisWorkflow:
    """Deterministic two-input analysis used before Creo discovery."""

    def analyze(self, run: RunRecord) -> AnalysisResult:
        bom = normalize_bom(run.bom_file)
        inventory = inventory_models(run.cad_directory, bom)
        draft_plan = create_draft_plan(bom, inventory)
        questions: list[ClarificationItem] = []
        if len(bom.sheet_candidates) > 1:
            questions.append(
                ClarificationItem(
                    item_id="select-bom-sheet",
                    category="CONFIRMATION",
                    question="检测到多个结构相同的 BOM 工作表，请确认本次使用哪一个。",
                    options=bom.sheet_candidates,
                    recommended_option=bom.sheet_name,
                    evidence=("候选工作表的有效表头和物料行评分相同。",),
                )
            )
        if len(inventory.assembly_candidates) > 1:
            questions.append(
                ClarificationItem(
                    item_id="select-final-assembly",
                    category="CONFIRMATION",
                    question="检测到多个同等可信的最终总装，请确认本次使用哪一个。",
                    options=inventory.assembly_candidates,
                    recommended_option=inventory.final_assembly,
                    evidence=("候选 ASM 与 BOM 根物料的匹配分数和文件版本相同。",),
                )
            )
        if inventory.missing_bom_rows:
            rows = ", ".join(str(row) for row in inventory.missing_bom_rows[:20])
            questions.append(
                ClarificationItem(
                    item_id="unmatched-bom-rows",
                    category="CONFIRMATION",
                    question=f"BOM 第 {rows} 行暂未匹配到同名 Creo 模型，是否按推荐方案继续分析？",
                    options=("按推荐方案继续", "返回检查 CAD 文件夹"),
                    recommended_option="按推荐方案继续",
                    evidence=(f"共有 {len(inventory.missing_bom_rows)} 行未完成文件级匹配；后续仍会使用 Creo occurrence 图谱复核。",),
                )
            )
        if inventory.ambiguous_bom_rows:
            rows = ", ".join(str(row) for row in inventory.ambiguous_bom_rows[:20])
            questions.append(
                ClarificationItem(
                    item_id="ambiguous-bom-models",
                    category="CONFIRMATION",
                    question=f"BOM 第 {rows} 行对应多个 Creo 模型版本或类型，请按推荐版本继续或返回检查文件。",
                    options=("使用每个模型的最高版本", "返回检查 CAD 文件夹"),
                    recommended_option="使用每个模型的最高版本",
                    evidence=("正式运行只会锁定每个模型的最高文件版本。",),
                )
            )
        facts = {
            "bom_sheet": bom.sheet_name,
            "bom_rows": len(bom.rows),
            "main_processes": len(bom.main_process_numbers),
            "renderable_main_processes": len(bom.renderable_process_numbers),
            "final_assembly": inventory.final_assembly,
            "model_files": len(inventory.files),
            "unmatched_bom_rows": len(inventory.missing_bom_rows),
            "non_modeled_bom_rows": len(inventory.non_modeled_bom_rows),
            "draft_installation_steps": len(draft_plan.steps),
        }
        packet = ClarificationPacket(
            schema_version="clarification-packet/v1",
            summary=(
                f"已从 {bom.sheet_name} 识别 {len(bom.rows)} 行物料、"
                f"{len(bom.main_process_numbers)} 个主工序，其中 "
                f"{len(bom.renderable_process_numbers)} 个需要生成安装图；"
                f"推荐最终总装为 {inventory.final_assembly}。"
            ),
            items=tuple(questions),
            facts=facts,
        )
        return AnalysisResult(
            packet=packet,
            artifacts=(
                ProducedArtifact("normalized-bom", "analysis/normalized-bom.json", bom),
                ProducedArtifact("model-inventory", "analysis/model-inventory.json", inventory),
                ProducedArtifact("draft-plan", "analysis/draft-plan.json", draft_plan),
            ),
        )

    def generate(self, run: RunRecord, plan: PlanRevision) -> GenerationResult:
        raise RuntimeError("正式生成将在阶段三接入 Creo worker")

    def resolve(self, run: RunRecord, resolution: StepResolution) -> GenerationResult:
        raise RuntimeError("步骤释疑将在阶段四接入局部再生成")
