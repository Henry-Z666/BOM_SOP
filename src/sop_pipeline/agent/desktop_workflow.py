from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont

from .bom_cad_mapper import map_bom_to_occurrences
from .creo_discovery import (
    CreoDiscoveryPort,
    PowerShellCreoDiscovery,
    bundled_discovery_script,
    powershell_command,
    resolve_runtime_config,
)
from .formal_render_planner import (
    FormalRenderPlan,
    compile_formal_render_plan,
    formal_render_plan_from_dict,
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
from .qwen_adapter import DashScopeTransport, PlanChoiceRecommendation, QwenAdvisor


class DesktopWorkflow:
    """Desktop composition with optional real Creo facts and safe generation gates."""

    def __init__(
        self,
        discovery: CreoDiscoveryPort | None = None,
        advisor: QwenAdvisor | None = None,
    ) -> None:
        self.analysis = LocalAnalysisWorkflow()
        self.discovery = discovery
        self.advisor = advisor

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
        formal_plan = compile_formal_render_plan(bom, draft_plan, mapping, graph)
        recommendations, qwen_scope_status = _scope_recommendations(
            self.advisor,
            bom,
            draft_plan,
            formal_plan,
            cache_directory=run.workspace.parent.parent / "semantic-cache",
        )
        questions = list(base.packet.items)
        questions.extend(_mapping_questions(mapping))
        questions.extend(_planning_questions(formal_plan, recommendations))
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
                "formal_render_steps": len(formal_plan.steps),
                "formal_ready_steps": formal_plan.ready_steps,
                "formal_questioned_steps": formal_plan.questioned_steps,
                "formal_plan_fingerprint": formal_plan.fingerprint,
                "qwen_scope_status": qwen_scope_status,
                "qwen_scope_recommendations": len(recommendations),
            }
        )
        packet = ClarificationPacket(
            schema_version=base.packet.schema_version,
            summary=(
                base.packet.summary
                + f" Creo 已验证 {len(graph['occurrences'])} 个 occurrence 和 "
                f"{len(graph['constraints'])} 条原生约束，并编译出 "
                f"{len(formal_plan.steps)} 个几何安装候选。"
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
                ProducedArtifact(
                    "formal-render-plan",
                    "analysis/formal-render-plan.json",
                    formal_plan,
                ),
            )
            + (
                (
                    ProducedArtifact(
                        "plan-recommendations",
                        "analysis/plan-recommendations.json",
                        {
                            "schema_version": "plan-recommendations/v1",
                            "items": recommendations,
                        },
                    ),
                )
                if recommendations
                else ()
            ),
        )

    def generate(self, run: RunRecord, plan: PlanRevision) -> GenerationResult:
        locked_path = run.workspace / "plans" / f"locked-render-plan-{plan.revision:04d}.json"
        if locked_path.is_file():
            locked = formal_render_plan_from_dict(_read_json(locked_path))
            source_steps = locked.steps
        else:
            draft = _read_json(run.workspace / "analysis" / "draft-plan.json")
            source_steps = tuple(draft.get("steps", []))
        placeholder_directory = run.workspace / "internal" / "placeholders"
        placeholder_directory.mkdir(parents=True, exist_ok=True)
        step_results: list[StepResult] = []
        sop_steps: list[SopStep] = []
        for item in source_steps:
            if isinstance(item, dict):
                step_id = str(item["step_id"])
                main_process_id = str(item["main_process_id"])
                depends_on = tuple(item.get("depends_on", []))
                complete_state_hash = str(item["complete_state_hash"])
                title = str(item.get("title", step_id))
                step_status = StepStatus.FAILED
            else:
                step_id = item.step_id
                main_process_id = item.main_process_id
                depends_on = item.depends_on
                complete_state_hash = item.complete_state_hash
                title = item.title
                step_status = (
                    StepStatus.QUESTIONED if item.status == "questioned" else StepStatus.FAILED
                )
            if step_status is StepStatus.QUESTIONED:
                process_text = "CAD 几何合同仍有疑问，本图仅为待重新生成占位。"
                control_points = "需要释疑接收面、安装方向或子装配范围。"
            else:
                process_text = "正式 Creo 渲染尚未执行，本图仅为待重新生成占位。"
                control_points = "几何计划已锁定；需要渲染、箭头和图片硬门全部通过。"
            image_path = placeholder_directory / f"{_safe_id(step_id)}.png"
            _write_placeholder(image_path, step_id)
            output_hash = "sha256:" + sha256(image_path.read_bytes()).hexdigest()
            step_results.append(
                StepResult(
                    step_id=step_id,
                    main_process_id=main_process_id,
                    status=step_status,
                    depends_on=depends_on,
                    complete_state_hash=complete_state_hash,
                    output_hash=output_hash,
                )
            )
            sop_steps.append(
                SopStep(
                    step_id=step_id,
                    main_process_id=main_process_id,
                    main_process_name=main_process_id,
                    title=title,
                    image=SopImage(f"placeholder-{step_id}", image_path, placeholder=True),
                    materials=(),
                    process_text=process_text,
                    control_points=control_points,
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


def _planning_questions(
    plan: FormalRenderPlan,
    recommendations: tuple[PlanChoiceRecommendation, ...] = (),
) -> tuple[ClarificationItem, ...]:
    result: list[ClarificationItem] = []
    recommendation_by_id = {item.decision_id: item for item in recommendations}
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
        recommendation = recommendation_by_id.get(item_id)
        recommended_text = (
            "按BOM在本工位展开内部构造"
            if recommendation is not None and recommendation.recommended == "expand"
            else "作为已完成整体安装"
            if recommendation is not None and recommendation.recommended == "whole"
            else "尚无 Qwen 工艺推荐，选择“不确定”时按 BOM 内部结构展开。"
        )
        recommendation_reason = (
            recommendation.reason if recommendation is not None else ""
        )
        result.append(
            ClarificationItem(
                item_id=item_id,
                category="CONFIRMATION",
                question=item.message,
                options=(
                    "按BOM在本工位展开内部构造",
                    "作为已完成整体安装",
                    "不确定，按推荐方案生成",
                ),
                recommended_option="不确定，按推荐方案生成",
                evidence=(
                    "CAD 同时证明了子装配内部约束和该子装配对外安装约束。",
                    "此选择会改变步骤数量和后续完整安装状态，因此必须在生成前锁定。",
                    "Qwen 推荐：" + recommended_text,
                    *(("依据：" + recommendation_reason,) if recommendation_reason else ()),
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


def _scope_recommendations(
    advisor: QwenAdvisor | None,
    bom,
    draft_plan,
    plan: FormalRenderPlan,
    *,
    cache_directory: Path | None = None,
) -> tuple[tuple[PlanChoiceRecommendation, ...], str]:
    diagnostics = [
        item for item in plan.diagnostics if item.code == "SUBASSEMBLY_SCOPE_UNCONFIRMED"
    ]
    if not diagnostics:
        return (), "not_needed"
    rows = {row.row: row for row in bom.rows}
    step_by_id = {step.step_id: step for step in plan.steps}
    requests: list[dict] = []
    for diagnostic in diagnostics:
        row_number = diagnostic.bom_rows[0]
        row = rows[row_number]
        child_rows = sorted(
            {
                source_row
                for step_id in diagnostic.affected_steps
                if (step := step_by_id.get(step_id)) is not None
                for source_row in step.source_bom_rows
                if source_row != row_number and source_row in rows
            }
        )
        process_text = "；".join(
            step.title
            for step in draft_plan.steps
            if row_number in step.source_bom_rows
        )
        requests.append(
            {
                "decision_id": f"subassembly-scope-{row_number:04d}",
                "assembly_name": row.name or row.drawing_no,
                "assembly_text": row.assembly_text,
                "process_text": process_text,
                "child_items": [
                    {
                        "name": rows[child].name,
                        "drawing_no": rows[child].drawing_no,
                        "quantity": rows[child].quantity,
                    }
                    for child in child_rows
                ],
            }
        )
    active = advisor
    if active is None:
        api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        if api_key:
            active = QwenAdvisor(DashScopeTransport(api_key))
    model = str(
        getattr(getattr(active, "transport", None), "text_model", "qwen-plus")
    )
    cache_file = _scope_cache_file(cache_directory, requests, model)
    cached = _read_scope_cache(cache_file, requests)
    if cached is not None:
        return cached, "cached"
    if active is None:
        return (), "not_configured"
    try:
        recommendations = active.recommend_plan_choices(requests)
    except (RuntimeError, ValueError):
        return (), "retryable"
    try:
        _write_scope_cache(cache_file, requests, model, recommendations)
    except OSError:
        # Recommendation caching improves repeatability but must not turn a
        # valid analysis into a system-level failure.
        pass
    return recommendations, "passed"


def _scope_cache_file(
    cache_directory: Path | None,
    requests: list[dict],
    model: str,
) -> Path | None:
    if cache_directory is None:
        return None
    payload = json.dumps(
        {
            "schema_version": "qwen-scope-advice-cache/v1",
            "advisor_contract": "subassembly-scope-request/v1",
            "model": model,
            "requests": requests,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return cache_directory / f"{sha256(payload).hexdigest()}.json"


def _read_scope_cache(
    cache_file: Path | None,
    requests: list[dict],
) -> tuple[PlanChoiceRecommendation, ...] | None:
    if cache_file is None or not cache_file.is_file():
        return None
    try:
        payload = _read_json(cache_file)
        if payload.get("schema_version") != "qwen-scope-advice-cache/v1":
            return None
        expected = {str(item["decision_id"]) for item in requests}
        items = tuple(
            PlanChoiceRecommendation(
                decision_id=str(item["decision_id"]),
                recommended=str(item["recommended"]),
                reason=str(item["reason"]),
            )
            for item in payload["items"]
        )
        actual = [item.decision_id for item in items]
        if (
            len(actual) != len(set(actual))
            or set(actual) != expected
            or any(item.recommended not in {"expand", "whole"} for item in items)
            or any(not item.reason or len(item.reason) > 120 for item in items)
        ):
            return None
        return tuple(sorted(items, key=lambda item: item.decision_id))
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_scope_cache(
    cache_file: Path | None,
    requests: list[dict],
    model: str,
    recommendations: tuple[PlanChoiceRecommendation, ...],
) -> None:
    if cache_file is None:
        return
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            {
                "schema_version": "qwen-scope-advice-cache/v1",
                "advisor_contract": "subassembly-scope-request/v1",
                "model": model,
                "request_ids": [str(item["decision_id"]) for item in requests],
                "items": [
                    {
                        "decision_id": item.decision_id,
                        "recommended": item.recommended,
                        "reason": item.reason,
                    }
                    for item in recommendations
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    temporary = cache_file.with_name(f".{cache_file.name}.tmp-{uuid4().hex}")
    try:
        with temporary.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, cache_file)
    finally:
        if temporary.exists():
            temporary.unlink()
