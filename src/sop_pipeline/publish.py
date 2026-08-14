from __future__ import annotations

from pathlib import Path
from typing import Any

from .agent.sop_publisher import SopImage, SopPublisher, SopStep
from .io import read_json
from .paths import OUTPUTS
from .product import Product
from .validation import validate_contract


def publish(contract_path: Path, product: Product | None = None) -> Path:
    """Compatibility entry point backed by the Agent's Python XLSX publisher."""

    del product
    contract = read_json(contract_path)
    errors = validate_contract(contract, require_render=True)
    if errors:
        raise ValueError("Excel 发布被阻断：" + "；".join(errors))
    annotation = contract.get("annotation", {}).get("file")
    if not annotation:
        raise ValueError("Excel 发布被阻断：缺少已通过的二维标注图")
    image_path = Path(annotation)
    if not image_path.is_absolute():
        image_path = contract_path.parent / image_path

    step_id = str(contract.get("step_id", contract_path.stem))
    bom = contract.get("bom", {})
    materials = tuple(
        (
            str(item.get("code", item.get("drawing_number", ""))),
            str(item.get("name", "")),
            int(item.get("quantity", 1)),
        )
        for item in _material_rows(bom)
    )
    step = SopStep(
        step_id=step_id,
        main_process_id=str(contract.get("main_process_id", "1")),
        main_process_name=str(contract.get("main_process_name", "装配工序")),
        title=str(contract.get("title", step_id)),
        image=SopImage(step_id, image_path),
        materials=materials,
        process_text=str(contract.get("process_text", "")),
        control_points=str(contract.get("control_points", "")),
        tools=str(contract.get("tools", "")),
    )
    delivery = OUTPUTS / "sop" / step_id / "交付结果"
    return SopPublisher(images_per_page=1).publish((step,), delivery)


def _material_rows(bom: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(bom, dict) and isinstance(bom.get("materials"), list):
        return tuple(item for item in bom["materials"] if isinstance(item, dict))
    if isinstance(bom, list):
        return tuple(item for item in bom if isinstance(item, dict))
    return ()
