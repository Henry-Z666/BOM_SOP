from __future__ import annotations

from pathlib import Path

from .bom import BomItem, direct_children, read_bom
from .contracts import make_contract, save_contract
from .paths import CONTRACTS, INPUT_BOM


def _find(items: list[BomItem], level: str) -> BomItem:
    return next(item for item in items if item.level == level)


def create_pilots(bom_path: Path = INPUT_BOM, contracts_dir: Path = CONTRACTS) -> list[Path]:
    """Create the two explicitly selected pilot categories; no relationship is guessed."""
    items = read_bom(bom_path)
    internal_parent = _find(items, "30.1")
    root = _find(items, "30")
    contracts = [
        make_contract(step_id="pilot.internal-water-tank", title="水箱焊件：直属子项内部构建",
                      scope="build_subassembly", assembly_file="零件图/jh9919000534.asm.1",
                      assembly_level="30.1", expected=direct_children(items, "30.1"), source=internal_parent),
        make_contract(step_id="pilot.attach-water-tank", title="水箱焊件：整体安装至水箱部件",
                      scope="attach_to_parent", assembly_file="零件图/jb9918900337.asm.2",
                      assembly_level="30", expected=[internal_parent], source=root),
    ]
    results = []
    for contract in contracts:
        path = contracts_dir / f"{contract['step_id']}.json"
        save_contract(path, contract)
        results.append(path)
    return results
