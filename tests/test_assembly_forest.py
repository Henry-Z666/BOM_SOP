from __future__ import annotations

import unittest

from sop_pipeline.agent.assembly_forest import build_assembly_forest
from sop_pipeline.agent.bom_normalizer import NormalizedBom, NormalizedBomRow
from sop_pipeline.agent.model_inventory import ModelFile, ModelInventory


def row(
    number: int,
    level: str,
    drawing: str,
    name: str,
    *,
    assembly_text: str = "",
) -> NormalizedBomRow:
    return NormalizedBomRow(
        row=number,
        level=level,
        material_code="",
        drawing_no=drawing,
        name=name,
        model="",
        quantity=1,
        unit="件",
        assembly_text=assembly_text,
        control_points="",
        tools="",
        main_process_number=None,
        process_only=False,
    )


def inventory() -> ModelInventory:
    files = (
        ModelFile("asm-a.asm.2", "ASM-A", "asm", 2),
        ModelFile("part-a.prt.1", "PART-A", "prt", 1),
        ModelFile("loose.prt.1", "LOOSE", "prt", 1),
        ModelFile("asm-b.asm.3", "ASM-B", "asm", 3),
        ModelFile("part-b.prt.1", "PART-B", "prt", 1),
    )
    return ModelInventory(
        "model-inventory/v1",
        files,
        "asm-a.asm.2",
        ("asm-a.asm.2",),
        (),
        (),
        (),
    )


class AssemblyForestTests(unittest.TestCase):
    def test_product_title_requires_all_three_evidence_rules(self) -> None:
        bom = NormalizedBom(
            "normalized-bom/v1",
            "BOM",
            1,
            {},
            (
                row(2, "", "PRODUCT", "产品标题"),
                row(3, "1", "ASM-A", "总装A"),
                row(4, "1.1", "PART-A", "A零件"),
                row(5, "2", "LOOSE", "顶层散件"),
                row(6, "7", "ASM-B", "总装B"),
                row(7, "7.1", "PART-B", "B零件"),
            ),
            ("BOM",),
        )

        forest = build_assembly_forest(bom, inventory())

        self.assertEqual(forest.product_title_rows, (2,))
        self.assertEqual([scope.root_bom_row for scope in forest.roots], [3, 6])
        self.assertEqual(forest.roots[0].source_bom_rows, (3, 4))
        self.assertEqual(forest.roots[1].source_bom_rows, (6, 7))
        self.assertEqual(forest.unscoped_bom_rows, (5,))

    def test_blank_level_row_with_a_step_is_not_silently_treated_as_title(self) -> None:
        bom = NormalizedBom(
            "normalized-bom/v1",
            "BOM",
            1,
            {},
            (
                row(2, "", "PRODUCT", "产品", assembly_text="安装顶层附件"),
                row(3, "1", "ASM-A", "总装A"),
            ),
            ("BOM",),
        )

        forest = build_assembly_forest(bom, inventory())

        self.assertEqual(forest.product_title_rows, ())
        self.assertIn(2, forest.unscoped_bom_rows)


if __name__ == "__main__":
    unittest.main()
