from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .local_workflow import LocalAnalysisWorkflow
from .models import (
    AnalysisResult,
    GenerationResult,
    PlanRevision,
    RunRecord,
    StepResolution,
    StepResult,
    StepStatus,
)
from .sop_publisher import SopImage, SopPublisher, SopStep


class DesktopWorkflow:
    """Safe desktop composition until a product-neutral Creo plan is provable."""

    def __init__(self) -> None:
        self.analysis = LocalAnalysisWorkflow()

    def analyze(self, run: RunRecord) -> AnalysisResult:
        return self.analysis.analyze(run)

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
