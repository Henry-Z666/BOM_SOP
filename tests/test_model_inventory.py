from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sop_pipeline.agent.bom_normalizer import NormalizedBom, NormalizedBomRow
from sop_pipeline.agent.model_inventory import inventory_models


def row(number: int, level: str, drawing: str, model: str = "") -> NormalizedBomRow:
    return NormalizedBomRow(
        row=number,
        level=level,
        material_code="",
        drawing_no=drawing,
        name=drawing,
        model=model,
        quantity=1,
        unit="件",
        assembly_text="",
        control_points="",
        tools="",
        main_process_number=None,
        process_only=False,
    )


class ModelInventoryPriorityTests(unittest.TestCase):
    def test_file_scan_uses_drawing_first_then_exact_model_fallback(self) -> None:
        bom = NormalizedBom(
            "normalized-bom/v1",
            "BOM",
            1,
            {},
            (
                row(2, "1", "ROOT"),
                row(3, "1.1", "DRAW-A", "MODEL-B"),
                row(4, "1.2", "UNKNOWN", "MODEL-C"),
            ),
            ("BOM",),
        )
        with tempfile.TemporaryDirectory() as folder:
            cad = Path(folder)
            for name in ("root.asm.1", "draw-a.prt.1", "model-b.prt.1", "model-c.prt.1"):
                (cad / name).write_bytes(name.encode("ascii"))

            result = inventory_models(cad, bom)

        self.assertNotIn(3, result.ambiguous_bom_rows)
        self.assertNotIn(4, result.missing_bom_rows)


if __name__ == "__main__":
    unittest.main()
