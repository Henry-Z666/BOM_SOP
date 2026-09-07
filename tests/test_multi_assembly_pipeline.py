from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sop_pipeline.agent import AgentCore, SkillRuntime
from sop_pipeline.agent.artifacts import ArtifactStore
from sop_pipeline.agent.formal_render_planner import (
    formal_render_plan_from_dict,
    lock_formal_render_plan,
)
from sop_pipeline.agent.render_job_compiler import compile_locked_render_jobs
from sop_pipeline.agent.skill_handlers import default_skill_handlers
from sop_pipeline.agent.store import RunStore
from tests.test_agent_analysis import _xlsx


class RecordingMultiAssemblyDiscovery:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def discover(
        self,
        cad_directory: Path,
        final_assembly: str,
        run_workspace: Path,
    ) -> dict:
        del cad_directory, run_workspace
        self.calls.append(final_assembly)
        stem = Path(final_assembly).name.split(".asm", 1)[0].upper()
        child = "PART-A" if stem == "ASM-A" else "PART-B"
        return {
            "schema_version": "creo-cad-graph/v3",
            "assembly_file": final_assembly,
            "assembly_name": Path(final_assembly).name.rsplit(".", 1)[0],
            "assembly_version": int(Path(final_assembly).suffix[1:]),
            "root_coordinate_system": "root_asm",
            "default_view_matrix": [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            "occurrences": [
                {
                    "occurrence_id": "1",
                    "parent_occurrence": "ROOT",
                    "model_name": child,
                    "part_no": child + ".prt",
                    "transform": {
                        "x_axis": [1.0, 0.0, 0.0],
                        "y_axis": [0.0, 1.0, 0.0],
                        "z_axis": [0.0, 0.0, 1.0],
                        "origin": [0.0, 0.0, 0.0],
                    },
                },
                {
                    "occurrence_id": "2",
                    "parent_occurrence": "ROOT",
                    "model_name": child + "2",
                    "part_no": child + "2.prt",
                    "transform": {
                        "x_axis": [1.0, 0.0, 0.0],
                        "y_axis": [0.0, 1.0, 0.0],
                        "z_axis": [0.0, 0.0, 1.0],
                        "origin": [0.0, 0.0, 10.0],
                    },
                },
            ],
            "constraints": [],
        }


class MultiAssemblyPipelineTests(unittest.TestCase):
    def test_lock_and_discovery_process_every_top_level_asm(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom = root / "BOM.xlsx"
            _xlsx(
                bom,
                [(
                    "BOM",
                    [
                        ["层级", "物料编码", "图号", "名称", "数量", "单位", "装配步骤"],
                        ["", "PRODUCT", "PRODUCT", "产品标题", "1", "件", ""],
                        ["1", "A", "ASM-A", "总装A", "1", "件", ""],
                        ["1.1", "A1", "PART-A", "A零件", "1", "件", ""],
                        ["1.2", "A2", "PART-A2", "A零件2", "1", "件", ""],
                        ["7", "B", "ASM-B", "总装B", "1", "件", ""],
                        ["7.1", "B1", "PART-B", "B零件", "1", "件", ""],
                        ["7.2", "B2", "PART-B2", "B零件2", "1", "件", ""],
                    ],
                )],
            )
            cad = root / "cad"
            cad.mkdir()
            for name in (
                "asm-a.asm.2",
                "part-a.prt.1",
                "part-a2.prt.1",
                "asm-b.asm.3",
                "part-b.prt.1",
                "part-b2.prt.1",
            ):
                (cad / name).write_bytes(name.encode("ascii"))
            workspace = root / "workspace"
            run_id = AgentCore(workspace).create_run(bom, cad)
            store = RunStore(workspace / "agent.sqlite3")
            discovery = RecordingMultiAssemblyDiscovery()
            runtime = SkillRuntime(
                store,
                ArtifactStore(store),
                default_skill_handlers(),
                adapters={"creo_discovery": discovery},
            )

            runtime.execute(run_id, "intake-preflight")
            runtime.execute(
                run_id,
                "normalize-bom",
                ("analysis/input-manifest.json",),
            )
            runtime.execute(
                run_id,
                "lock-assembly",
                ("analysis/normalized-bom.json",),
            )
            runtime.execute(
                run_id,
                "discover-cad",
                ("analysis/assembly-lock.json", "analysis/input-manifest.json"),
            )
            runtime.execute(
                run_id,
                "map-bom-cad",
                (
                    "analysis/normalized-bom.json",
                    "analysis/model-inventory.json",
                    "analysis/creo-cad-graph.json",
                ),
            )
            runtime.execute(
                run_id,
                "plan-assembly",
                (
                    "analysis/normalized-bom.json",
                    "analysis/model-inventory.json",
                    "analysis/bom-cad-map.json",
                    "analysis/creo-cad-graph.json",
                ),
            )
            runtime.execute(
                run_id,
                "clarify-plan",
                (
                    "analysis/normalized-bom.json",
                    "analysis/model-inventory.json",
                    "analysis/bom-cad-map.json",
                    "analysis/draft-plan.json",
                    "analysis/formal-render-plan.json",
                    "analysis/creo-cad-graph.json",
                ),
            )
            lock = runtime.artifacts.read_json(
                store.get(run_id).workspace,
                "analysis/assembly-lock.json",
            )
            forest = runtime.artifacts.read_json(
                store.get(run_id).workspace,
                "analysis/creo-cad-graph.json",
            )
            mapping = runtime.artifacts.read_json(
                store.get(run_id).workspace,
                "analysis/bom-cad-map.json",
            )
            formal = runtime.artifacts.read_json(
                store.get(run_id).workspace,
                "analysis/formal-render-plan.json",
            )
            packet = runtime.artifacts.read_json(
                store.get(run_id).workspace,
                "analysis/clarification-packet.json",
            )

        self.assertEqual(lock["schema_version"], "assembly-lock/v2")
        self.assertEqual(
            [(item["root_bom_row"], item["assembly_file"]) for item in lock["assemblies"]],
            [(3, "asm-a.asm.2"), (6, "asm-b.asm.3")],
        )
        self.assertEqual(discovery.calls, ["asm-a.asm.2", "asm-b.asm.3"])
        self.assertEqual(forest["schema_version"], "creo-cad-forest/v1")
        self.assertEqual(len(forest["assemblies"]), 2)
        self.assertEqual(mapping["schema_version"], "bom-cad-forest/v1")
        self.assertEqual(len(mapping["batches"]), 2)
        self.assertEqual(
            [batch["mapping"]["rows"][0]["occurrence_ids"] for batch in mapping["batches"]],
            [["ROOT"], ["ROOT"]],
        )
        title = next(item for item in mapping["rows"] if item["bom_row"] == 2)
        self.assertEqual(title["status"], "non_renderable")
        self.assertIn("产品标题", title["evidence"])
        self.assertEqual(formal["schema_version"], "formal-render-forest/v1")
        self.assertEqual(len(formal["batches"]), 2)
        self.assertEqual(
            [item["plan"]["assembly_file"] for item in formal["batches"]],
            ["asm-a.asm.2", "asm-b.asm.3"],
        )
        self.assertIn("锁定2个顶层ASM批次", packet["summary"])
        self.assertEqual(packet["facts"]["top_level_assembly_batches"], 2)
        self.assertEqual(packet["facts"]["product_title_rows"], [2])
        self.assertEqual(packet["facts"]["cad_occurrences"], 4)
        self.assertNotIn(
            "select-final-assembly",
            [item["item_id"] for item in packet["items"]],
        )
        locked = lock_formal_render_plan(formal_render_plan_from_dict(formal), {})
        jobs = compile_locked_render_jobs(locked)
        self.assertEqual(
            [task.payload["plan_index"] for task in jobs.tasks],
            list(range(len(jobs.tasks))),
            "Merged roots must dispatch global indices, not repeat the first root",
        )
        self.assertEqual(
            [task.payload["assembly_file"] for task in jobs.tasks],
            ["asm-a.asm.2", "asm-b.asm.3"],
        )


if __name__ == "__main__":
    unittest.main()
