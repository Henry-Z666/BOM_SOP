from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .bom_cad_mapper import map_bom_to_occurrences
from .creo_discovery import (
    CreoDiscoveryPort,
    PowerShellCreoDiscovery,
    bundled_discovery_script,
    powershell_command,
    resolve_runtime_config,
)
from .local_workflow import LocalAnalysisWorkflow
from .models import (
    AnalysisResult,
    ClarificationItem,
    ClarificationPacket,
    GenerationResult,
    PlanRevision,
    ProducedArtifact,
    RunRecord,
    StepResolution,
    StepResult,
    StepStatus,
)
from .sop_publisher import SopImage, SopPublisher, SopStep


class DesktopWorkflow:
    """Desktop composition with optional real Creo facts and safe generation gates."""

    def __init__(self, discovery: CreoDiscoveryPort | None = None) -> None:
        self.analysis = LocalAnalysisWorkflow()
        self.discovery = discovery

    def analyze(self, run: RunRecord) -> AnalysisResult:
        base = self.analysis.analyze(run)
        discovery = self.discovery
        runtime_config = resolve_runtime_config(run.workspace) if discovery is None else None
        if discovery is None and runtime_config is not None:
            discovery = PowerShellCreoDiscovery(
                powershell=powershell_command(),
                script=bundled_discovery_script(),
                runtime_config=runtime_config,
            )
        if discovery is None:
            facts = dict(base.packet.facts)
            facts["creo_discovery"] = "not_configured"
            return AnalysisResult(
                packet=ClarificationPacket(
                    schema_version=base.packet.schema_version,
                    summary=base.packet.summary,
                    items=base.packet.items,
                    facts=facts,
                ),
                artifacts=base.artifacts,
            )

        artifacts = {item.kind: item.value for item in base.artifacts}
        bom = artifacts["normalized-bom"]
        inventory = artifacts["model-inventory"]
        draft_plan = artifacts["draft-plan"]
        graph = discovery.discover(
            run.cad_directory, inventory.final_assembly, run.workspace
        )
        mapping = map_bom_to_occurrences(bom, inventory, graph, draft_plan)
        questions = list(base.packet.items)
        questions.extend(_mapping_questions(mapping))
        facts = dict(base.packet.facts)
        facts.update(
            {
                "creo_discovery": "passed",
                "cad_occurrences": len(graph["occurrences"]),
                "cad_constraints": len(graph["constraints"]),
                "mapped_bom_rows": mapping.matched_rows,
                "ambiguous_occurrence_rows": len(mapping.ambiguous_rows),
                "missing_occurrence_rows": len(mapping.missing_rows),
                "quantity_mismatch_rows": len(mapping.quantity_mismatch_rows),
            }
        )
        packet = ClarificationPacket(
            schema_version=base.packet.schema_version,
            summary=(
                base.packet.summary
                + f" Creo 已验证 {len(graph['occurrences'])} 个 occurrence 和 "
                f"{len(graph['constraints'])} 条原生约束。"
            ),
            items=tuple(questions),
            facts=facts,
        )
        return AnalysisResult(
            packet=packet,
            artifacts=base.artifacts
            + (
                ProducedArtifact("creo-cad-graph", "analysis/creo-cad-graph.json", graph),
                ProducedArtifact("bom-cad-map", "analysis/bom-cad-map.json", mapping),
            ),
        )

    def generate(self, run: RunRecord, plan: PlanRevision) -> GenerationResult:
        del plan
        draft = _read_json(run.workspace / "analysis" / "draft-plan.json")
        placeholder_directory = run.workspace / "internal" / "placeholders"
        placeholder_directory.mkdir(parents=True, exist_ok=True)
        step_results: list[StepResult] = []
        sop_steps: list[SopStep] = []
        for item in draft.get("steps", []):
            step_id = str(item["step_id"])
            image_path = placeholder_directory / f"{_safe_id(step_id)}.png"
            _write_placeholder(image_path, step_id)
            output_hash = "sha256:" + sha256(image_path.read_bytes()).hexdigest()
            step_results.append(
                StepResult(
                    step_id=step_id,
                    main_process_id=str(item["main_process_id"]),
                    status=StepStatus.FAILED,
                    depends_on=tuple(item.get("depends_on", [])),
                    complete_state_hash=str(item["complete_state_hash"]),
                    output_hash=output_hash,
                )
            )
            sop_steps.append(
                SopStep(
                    step_id=step_id,
                    main_process_id=str(item["main_process_id"]),
                    main_process_name=str(item["main_process_id"]),
                    title=str(item.get("title", step_id)),
                    image=SopImage(f"placeholder-{step_id}", image_path, placeholder=True),
                    materials=(),
                    process_text="几何合同尚未通过，禁止作为正式装配指导。",
                    control_points="需要完成 Creo occurrence、接收面和安装方向验证。",
                    tools="",
                    questioned=True,
                )
            )
        if not sop_steps:
            raise RuntimeError("BOM 未产生可出版的安装步骤")
        delivery = run.workspace / "delivery"
        SopPublisher().publish(tuple(sop_steps), delivery, pending=True)
        return GenerationResult(tuple(step_results), delivery)

    def resolve(self, run: RunRecord, resolution: StepResolution) -> GenerationResult:
        del run, resolution
        raise RuntimeError("该步骤缺少可验证的 Creo 几何合同，不能根据文字猜测后转正")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_id(value: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "_" for character in value)


def _write_placeholder(path: Path, step_id: str) -> None:
    image = Image.new("RGB", (1600, 1600), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 1560, 1560), outline="#C00000", width=18)
    draw.line((250, 250, 1350, 1350), fill="#C00000", width=55)
    draw.line((1350, 250, 250, 1350), fill="#C00000", width=55)
    font = _placeholder_font(70)
    draw.text((190, 700), "REGENERATION REQUIRED", fill="#C00000", font=font)
    draw.text((190, 810), step_id, fill="#333333", font=_placeholder_font(44))
    image.save(path)


def _placeholder_font(size: int):
    for candidate in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


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
                evidence=("缺失实体不会由 Qwen 或脚本虚构。",),
            )
        )
    return tuple(result)
