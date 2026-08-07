from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .io import read_json
from .paths import OUTPUTS, ROOT
from .product import Product
from .validation import validate_contract


def publish(contract_path: Path, product: Product) -> Path:
    contract = read_json(contract_path)
    errors = validate_contract(contract, require_render=True)
    if errors:
        raise ValueError("Excel 发布被阻断：" + "；".join(errors))
    if not contract.get("annotation", {}).get("file"):
        raise ValueError("Excel 发布被阻断：缺少已通过的二维标注图")
    output = OUTPUTS / "sop" / f"{contract['step_id']}.xlsx"
    output.parent.mkdir(parents=True, exist_ok=True)
    node = os.environ.get("CODEX_NODE", "node")
    subprocess.run([node, str(ROOT / "scripts" / "export_sop.mjs"), str(contract_path), str(product.sop_template), str(output)], check=True)
    return output
