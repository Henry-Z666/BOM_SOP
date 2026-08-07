from __future__ import annotations

from pathlib import Path

from .bom import BomItem, direct_children, read_bom
from .contracts import make_contract, save_contract
from .paths import CONTRACTS
from .product import Product


def _find(items: list[BomItem], level: str) -> BomItem:
    return next(item for item in items if item.level == level)


def create_pilots(product: Product, contracts_dir: Path = CONTRACTS) -> list[Path]:
    """Create the two explicitly selected pilot categories; no relationship is guessed."""
    items = read_bom(product.bom_file, product.bom_sheet)
    internal_parent = _find(items, "30.1")
    root = _find(items, "30")
    contracts = [
        make_contract(step_id=f"pilot.internal-{product.product_id}", title=f"{internal_parent.name}：直属子项内部构建",
                      scope="build_subassembly", assembly_file=str(product.final_assembly_path),
                      assembly_level="30.1", expected=direct_children(items, "30.1"), source=internal_parent),
        make_contract(step_id=f"pilot.attach-{product.product_id}", title=f"{internal_parent.name}：整体安装至父装配",
                      scope="attach_to_parent", assembly_file=str(product.final_assembly_path),
                      assembly_level="30", expected=[internal_parent], source=root),
    ]
    results = []
    for contract in contracts:
        path = contracts_dir / f"{contract['step_id']}.json"
        save_contract(path, contract)
        results.append(path)
    return results
