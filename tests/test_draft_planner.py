from __future__ import annotations

import unittest

from sop_pipeline.agent.bom_normalizer import NormalizedBom, NormalizedBomRow
from sop_pipeline.agent.draft_planner import create_draft_plan
from sop_pipeline.agent.model_inventory import ModelFile, ModelInventory


def row(
    number: int,
    level: str,
    drawing: str,
    name: str,
    *,
    process_only: bool = False,
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
        assembly_text="",
        control_points="",
        tools="",
        main_process_number=None,
        process_only=process_only,
    )


def inventory(*files: ModelFile, non_modeled: tuple[int, ...] = ()) -> ModelInventory:
    return ModelInventory(
        "model-inventory/v1",
        tuple(files),
        "root.asm.1",
        ("root.asm.1",),
        (),
        (),
        non_modeled,
    )


class DraftPlannerHierarchyTests(unittest.TestCase):
    def test_hierarchy_only_bom_uses_each_asm_as_a_process_scope(self) -> None:
        bom = NormalizedBom(
            "normalized-bom/v1",
            "BOM",
            1,
            {},
            (
                row(2, "1", "ROOT", "温控单元"),
                row(3, "1.1", "FRAME", "框架合件"),
                row(4, "1.1.1", "PLATE", "安装板"),
                row(5, "1.1.2", "BRACKET", "支架合件"),
                row(6, "1.1.2.1", "SCREW", "螺钉"),
            ),
            ("BOM",),
        )
        models = inventory(
            ModelFile("root.asm.1", "ROOT", "asm", 1),
            ModelFile("frame.asm.1", "FRAME", "asm", 1),
            ModelFile("plate.prt.1", "PLATE", "prt", 1),
            ModelFile("bracket.asm.1", "BRACKET", "asm", 1),
            ModelFile("screw.prt.1", "SCREW", "prt", 1),
        )

        plan = create_draft_plan(bom, models)

        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[0].source_bom_rows, (3, 4))
        self.assertEqual(plan.steps[1].source_bom_rows, (5, 6))
        self.assertNotIn(2, {value for step in plan.steps for value in step.source_bom_rows})

    def test_hierarchy_fallback_covers_rows_even_without_a_detected_asm(self) -> None:
        bom = NormalizedBom(
            "normalized-bom/v1",
            "BOM",
            1,
            {},
            (
                row(2, "1", "ROOT", "温控单元"),
                row(3, "1.1", "PLATE", "安装板"),
                row(4, "1.2", "SCREW", "螺钉"),
                row(5, "1.3", "GREASE", "润滑脂"),
            ),
            ("BOM",),
        )
        models = inventory(
            ModelFile("root.asm.1", "ROOT", "asm", 1),
            ModelFile("plate.prt.1", "PLATE", "prt", 1),
            ModelFile("screw.prt.1", "SCREW", "prt", 1),
            non_modeled=(5,),
        )

        plan = create_draft_plan(bom, models)

        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].source_bom_rows, (3, 4))
        self.assertIn("产品级", plan.steps[0].title)


if __name__ == "__main__":
    unittest.main()
