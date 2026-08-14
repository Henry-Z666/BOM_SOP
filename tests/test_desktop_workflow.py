from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from sop_pipeline.agent import AgentCore, DesktopWorkflow, RunStatus
from sop_pipeline.agent.creo_discovery import StaticCreoDiscovery
from sop_pipeline.agent.desktop_workflow import _scope_recommendations
from sop_pipeline.agent.formal_render_planner import compile_formal_render_plan
from sop_pipeline.agent.qwen_adapter import PlanChoiceRecommendation
from tests.test_agent_analysis import _xlsx
from tests.test_formal_render_planner import fixture as planning_fixture


class DesktopWorkflowTests(unittest.TestCase):
    def test_qwen_scope_recommendation_is_stable_from_local_fingerprint_cache(self) -> None:
        class CountingAdvisor:
            def __init__(self) -> None:
                self.calls = 0

            def recommend_plan_choices(self, items):
                self.calls += 1
                return tuple(
                    PlanChoiceRecommendation(
                        str(item["decision_id"]), "whole", "作为已完成合件安装"
                    )
                    for item in items
                )

        bom, draft, mapping, graph = planning_fixture()
        plan = compile_formal_render_plan(bom, draft, mapping, graph)
        advisor = CountingAdvisor()
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder)
            first, first_status = _scope_recommendations(
                advisor, bom, draft, plan, cache_directory=cache
            )
            second, second_status = _scope_recommendations(
                advisor, bom, draft, plan, cache_directory=cache
            )

        self.assertEqual(first, second)
        self.assertEqual(advisor.calls, 1)
        self.assertEqual(first_status, "passed")
        self.assertEqual(second_status, "cached")

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
                    ],
                )],
            )
            cad = root / "cad"
            cad.mkdir()
            (cad / "root-asm.asm.1").write_bytes(b"root")
            (cad / "part-a.prt.1").write_bytes(b"part")
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
                    }
                ],
                "constraints": [
                    {"occurrences": ["10", "ROOT"], "type": "FIX"}
                ],
            }
            core = AgentCore(
                root / "workspace", DesktopWorkflow(StaticCreoDiscovery(graph))
            )

            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)

            self.assertEqual(packet.facts["creo_discovery"], "passed")
            self.assertEqual(packet.facts["cad_occurrences"], 1)
            self.assertEqual(packet.facts["mapped_bom_rows"], 2)
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
