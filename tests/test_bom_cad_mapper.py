from __future__ import annotations

import unittest

from sop_pipeline.agent.bom_cad_mapper import map_bom_to_occurrences
from sop_pipeline.agent.bom_normalizer import NormalizedBom, NormalizedBomRow
from sop_pipeline.agent.model_inventory import ModelFile, ModelInventory
from sop_pipeline.agent.draft_planner import DraftInstallationStep, DraftPlan


def row(number: int, level: str, drawing: str, quantity: int = 1) -> NormalizedBomRow:
    return NormalizedBomRow(
        row=number,
        level=level,
        material_code="",
        drawing_no=drawing,
        name=drawing,
        model="",
        quantity=quantity,
        unit="件",
        assembly_text="",
        control_points="",
        tools="",
        main_process_number=None,
        process_only=False,
    )


class BomCadMapperTests(unittest.TestCase):
    def test_parent_hierarchy_disambiguates_repeated_fasteners(self) -> None:
        bom = NormalizedBom(
            "normalized-bom/v1",
            "BOM",
            1,
            {},
            (
                row(2, "30", "ROOT"),
                row(3, "30.1", "SUB-A"),
                row(4, "30.1.1", "BOLT", 2),
                row(5, "30.2", "SUB-B"),
                row(6, "30.2.1", "BOLT", 1),
            ),
            ("BOM",),
        )
        inventory = ModelInventory(
            "model-inventory/v1",
            (ModelFile("root.asm.1", "ROOT", "asm", 1),),
            "root.asm.1",
            ("root.asm.1",),
            (),
            (),
            (),
        )
        graph = {
            "schema_version": "creo-cad-graph/v3",
            "assembly_file": "root.asm.1",
            "occurrences": [
                {"occurrence_id": "10", "parent_occurrence": "ROOT", "model_name": "SUB-A"},
                {"occurrence_id": "10/1", "parent_occurrence": "10", "model_name": "BOLT"},
                {"occurrence_id": "10/2", "parent_occurrence": "10", "model_name": "BOLT"},
                {"occurrence_id": "20", "parent_occurrence": "ROOT", "model_name": "SUB-B"},
                {"occurrence_id": "20/1", "parent_occurrence": "20", "model_name": "BOLT"},
            ],
        }

        result = map_bom_to_occurrences(bom, inventory, graph)

        by_row = {item.bom_row: item for item in result.rows}
        self.assertEqual(by_row[4].occurrence_ids, ("10/1", "10/2"))
        self.assertEqual(by_row[6].occurrence_ids, ("20/1",))
        self.assertEqual(result.ambiguous_rows, ())

    def test_extra_occurrences_remain_ambiguous_instead_of_being_sliced(self) -> None:
        bom = NormalizedBom(
            "normalized-bom/v1",
            "BOM",
            1,
            {},
            (row(2, "30", "ROOT"), row(3, "30.1", "BOLT", 1)),
            ("BOM",),
        )
        inventory = ModelInventory(
            "model-inventory/v1",
            (ModelFile("root.asm.1", "ROOT", "asm", 1),),
            "root.asm.1",
            ("root.asm.1",),
            (),
            (),
            (),
        )
        graph = {
            "schema_version": "creo-cad-graph/v3",
            "assembly_file": "root.asm.1",
            "occurrences": [
                {"occurrence_id": "1", "parent_occurrence": "ROOT", "model_name": "BOLT"},
                {"occurrence_id": "2", "parent_occurrence": "ROOT", "model_name": "BOLT"},
            ],
        }

        result = map_bom_to_occurrences(bom, inventory, graph)

        self.assertEqual(result.ambiguous_rows, (3,))
        self.assertEqual(result.rows[1].occurrence_ids, ("1", "2"))

    def test_process_constraint_context_resolves_exact_quantity(self) -> None:
        bom = NormalizedBom(
            "normalized-bom/v1",
            "BOM",
            1,
            {},
            (
                row(2, "30", "ROOT"),
                row(3, "30.1", "BOLT", 2),
                row(4, "30.2", "RECEIVER", 1),
            ),
            ("BOM",),
        )
        inventory = ModelInventory(
            "model-inventory/v1",
            (ModelFile("root.asm.1", "ROOT", "asm", 1),),
            "root.asm.1",
            ("root.asm.1",),
            (),
            (),
            (),
        )
        graph = {
            "schema_version": "creo-cad-graph/v3",
            "assembly_file": "root.asm.1",
            "occurrences": [
                {"occurrence_id": "1", "parent_occurrence": "ROOT", "model_name": "BOLT"},
                {"occurrence_id": "2", "parent_occurrence": "ROOT", "model_name": "BOLT"},
                {"occurrence_id": "3", "parent_occurrence": "ROOT", "model_name": "BOLT"},
                {"occurrence_id": "10", "parent_occurrence": "ROOT", "model_name": "RECEIVER"},
            ],
            "constraints": [
                {"occurrences": ["1", "10"]},
                {"occurrences": ["2", "10"]},
                {"occurrences": ["3", "ROOT"]},
            ],
        }
        draft = DraftPlan(
            "draft-plan/v1",
            "root.asm.1",
            (
                DraftInstallationStep(
                    "step-1",
                    "process-001",
                    "安装",
                    (),
                    (3, 4),
                    (),
                    (),
                    "sha256:test",
                ),
            ),
            20,
        )

        result = map_bom_to_occurrences(bom, inventory, graph, draft)

        self.assertEqual(result.ambiguous_rows, ())
        self.assertEqual(result.rows[1].occurrence_ids, ("1", "2"))
        self.assertIn("原生约束自动消歧", result.rows[1].evidence)


if __name__ == "__main__":
    unittest.main()
