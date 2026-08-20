from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from sop_pipeline.agent import AgentCore, DesktopWorkflow, RunStatus
from sop_pipeline.agent.creo_discovery import StaticCreoDiscovery
from tests.test_agent_analysis import _xlsx
from tests.test_pipeline_orchestrator import _fixture as pipeline_fixture


class DesktopWorkflowTests(unittest.TestCase):
    def test_real_discovery_facts_are_registered_before_confirmation(self) -> None:
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
                        ["30.2", "B", "PART-B", "支架", "1", "件", "第2步：安装支架"],
                    ],
                )],
            )
            cad = root / "cad"
            cad.mkdir()
            (cad / "root-asm.asm.1").write_bytes(b"root")
            (cad / "part-a.prt.1").write_bytes(b"part")
            (cad / "part-b.prt.1").write_bytes(b"part-b")
            graph = {
                "schema_version": "creo-cad-graph/v3",
                "assembly_file": "root-asm.asm.1",
                "default_view_matrix": [
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
                "authoritative_assembly": {"sha256": "sha256:test"},
                "occurrences": [
                    {
                        "occurrence_id": "10",
                        "parent_occurrence": "ROOT",
                        "model_name": "PART-A",
                        "part_no": "part-a.prt",
                        "transform": {
                            "x_axis": [1.0, 0.0, 0.0],
                            "y_axis": [0.0, 1.0, 0.0],
                            "z_axis": [0.0, 0.0, 1.0],
                            "origin": [0.0, 0.0, 0.0],
                        },
                    },
                    {
                        "occurrence_id": "20",
                        "parent_occurrence": "ROOT",
                        "model_name": "PART-B",
                        "part_no": "part-b.prt",
                        "transform": {
                            "x_axis": [1.0, 0.0, 0.0],
                            "y_axis": [0.0, 1.0, 0.0],
                            "z_axis": [0.0, 0.0, 1.0],
                            "origin": [0.0, 0.0, 20.0],
                        },
                    },
                ],
                "constraints": [
                    {"occurrences": ["10", "ROOT"], "type": "FIX"},
                    {
                        "id": "20-mate",
                        "occurrences": ["20", "10"],
                        "type": "MATE",
                        "assembly_reference": {
                            "occurrence_id": "10",
                            "geometry": {
                                "status": "available",
                                "direction_root": [0.0, 0.0, 1.0],
                                "point_root": [0.0, 0.0, 0.0],
                            },
                        },
                        "component_reference": {
                            "occurrence_id": "20",
                            "geometry": {
                                "status": "available",
                                "direction_root": [0.0, 0.0, 1.0],
                                "point_root": [0.0, 0.0, 20.0],
                            },
                        },
                    },
                ],
            }
            core = AgentCore(
                root / "workspace", DesktopWorkflow(StaticCreoDiscovery(graph))
            )

            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)

            self.assertEqual(packet.facts["creo_discovery"], "passed")
            self.assertEqual(packet.facts["cad_occurrences"], 2)
            self.assertEqual(packet.facts["mapped_bom_rows"], 3)
            run = core.get_run(run_id)
            self.assertTrue((run.workspace / "analysis" / "creo-cad-graph.json").is_file())
            self.assertTrue((run.workspace / "analysis" / "bom-cad-map.json").is_file())
            self.assertTrue(
                (run.workspace / "analysis" / "formal-render-plan.json").is_file()
            )
            answers = {
                item.item_id: item.recommended_option
                for item in packet.items
                if item.category == "CONFIRMATION"
            }
            revision = core.confirm(run_id, answers)
            self.assertTrue(
                (
                    run.workspace
                    / "plans"
                    / f"locked-render-plan-{revision.revision:04d}.json"
                ).is_file()
            )
            self.assertFalse(
                (
                    run.workspace
                    / "plans"
                    / f"locked-render-jobs-{revision.revision:04d}.json"
                ).is_file()
            )

    def test_unproven_geometry_delivers_pending_sop_instead_of_crashing_or_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad, graph = pipeline_fixture(root)
            graph["constraints"][1]["assembly_reference"]["geometry"] = {
                "status": "unavailable"
            }
            graph["constraints"][1]["component_reference"]["geometry"] = {
                "status": "unavailable"
            }
            before = {path.name: sha256(path.read_bytes()).hexdigest() for path in cad.iterdir()}
            core = AgentCore(
                root / "workspace",
                DesktopWorkflow(StaticCreoDiscovery(graph)),
            )

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
            self.assertIn("待确认", workbook.active["AN5"].value)
            self.assertTrue(all(step.output_hash for step in outcome.steps))


if __name__ == "__main__":
    unittest.main()
