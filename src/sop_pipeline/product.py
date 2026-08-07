"""Load one portable assembly-SOP product package.

The package is the seam between generic pipeline code and product-specific
inputs.  It deliberately contains paths and identifiers only; generated runs,
Creo runtime settings, and source CAD remain outside it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


SCHEMA_VERSION = "assembly-sop-product/v1"


@dataclass(frozen=True)
class Product:
    """Resolved, validated paths required by the generic pipeline."""

    config_path: Path
    product_id: str
    bom_file: Path
    models_dir: Path
    sop_template: Path
    final_assembly: str
    bom_sheet: str | None = None

    @property
    def final_assembly_path(self) -> Path:
        return self.models_dir / self.final_assembly


def _resolve(base: Path, value: object, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"产品配置缺少 {name}")
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _project_root(config_path: Path) -> Path:
    """Prefer the checkout root so product files can live at any depth."""
    for candidate in config_path.parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return config_path.parent


def load_product(config_path: Path) -> Product:
    """Read a product package without relying on the checkout's filenames."""
    config_path = config_path.resolve()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise FileNotFoundError(f"找不到产品配置：{config_path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"产品配置不是合法 JSON：{config_path}") from error
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"不支持的产品配置版本：{data.get('schema_version')!r}")

    product_id = data.get("product_id")
    if not isinstance(product_id, str) or not product_id.strip():
        raise ValueError("产品配置缺少 product_id")
    base = _project_root(config_path)
    product = Product(
        config_path=config_path,
        product_id=product_id.strip(),
        bom_file=_resolve(base, data.get("bom_file"), "bom_file"),
        models_dir=_resolve(base, data.get("models_dir"), "models_dir"),
        sop_template=_resolve(base, data.get("sop_template"), "sop_template"),
        final_assembly=str(data.get("final_assembly", "")).strip(),
        bom_sheet=data.get("bom_sheet"),
    )
    if not product.final_assembly:
        raise ValueError("产品配置缺少 final_assembly")
    if product.bom_sheet is not None and (not isinstance(product.bom_sheet, str) or not product.bom_sheet.strip()):
        raise ValueError("bom_sheet 必须是非空字符串")
    for name, path in (("bom_file", product.bom_file), ("models_dir", product.models_dir), ("sop_template", product.sop_template)):
        if not path.exists():
            raise FileNotFoundError(f"产品配置 {name} 不存在：{path}")
    if not product.final_assembly_path.exists():
        raise FileNotFoundError(f"产品配置 final_assembly 不存在：{product.final_assembly_path}")
    return product
