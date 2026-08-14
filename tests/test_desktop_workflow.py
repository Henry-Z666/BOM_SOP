from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from sop_pipeline.agent import AgentCore, DesktopWorkflow, RunStatus
from tests.test_agent_analysis import _xlsx


class DesktopWorkflowTests(unittest.TestCase):
    def test_unproven_geometry_delivers_pending_sop_instead_of_crashing_or_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom = root / "BOM.xlsx"
            _xlsx(
                bom,
                [(
                    "BOM",
                    [
                        ["层级", "物料编码", "图号", "名称", "数量", "单位", "装配步骤"],
                        ["30", "ROOT", "ROOT-ASM", "设备总装", "1", "件", "第2步：检查"],
                        ["30.1", "A", "PART-A", "底座", "1", "件", "第1步：固定底座"],
                    ],
                )],
            )
            cad = root / "cad"
            cad.mkdir()
            assembly = cad / "root-asm.asm.1"
            part = cad / "part-a.prt.1"
            assembly.write_bytes(b"root")
            part.write_bytes(b"part")
            before = {path.name: sha256(path.read_bytes()).hexdigest() for path in cad.iterdir()}
            core = AgentCore(root / "workspace", DesktopWorkflow())

            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)
            answers = {
                item.item_id: item.recommended_option
                for item in packet.items
                if item.category == "CONFIRMATION"
            }
            core.confirm(run_id, answers)
            outcome = core.generate(run_id)
            after = {path.name: sha256(path.read_bytes()).hexdigest() for path in cad.iterdir()}

            self.assertEqual(outcome.status, RunStatus.NEEDS_REVIEW)
            self.assertEqual(before, after)
            self.assertEqual(
                {entry.name for entry in outcome.delivery_directory.iterdir()},
                {"SOP_待确认.xlsx", "步骤图片"},
            )
            workbook = load_workbook(
                outcome.delivery_directory / "SOP_待确认.xlsx"
            )
            self.assertIn("待确认", workbook.active["A2"].value)
            self.assertTrue(all(step.output_hash for step in outcome.steps))


if __name__ == "__main__":
    unittest.main()
