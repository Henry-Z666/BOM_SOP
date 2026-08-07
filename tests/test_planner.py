from pathlib import Path
import unittest
import tempfile

from sop_pipeline.io import read_json
from sop_pipeline.auto_planner import plan
from sop_pipeline.cad_graph import CadGraph
from sop_pipeline.planner import create_pilots
from sop_pipeline.validation import validate_contract, validate_render


class PlannerTests(unittest.TestCase):
    def test_pilots_await_automatic_cad_discovery(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = create_pilots(contracts_dir=Path(folder))
        self.assertEqual(len(paths), 2)
        # The files remain valid outside the temporary context only in memory;
        # verify category behaviour from the generated paths before cleanup.
        with tempfile.TemporaryDirectory() as folder:
            contracts = [read_json(path) for path in create_pilots(contracts_dir=Path(folder))]
            self.assertEqual({item["scope"] for item in contracts}, {"build_subassembly", "attach_to_parent"})
            self.assertTrue(all(item["automation"]["phase"] == "awaiting_cad_discovery" for item in contracts))
            self.assertTrue(all(validate_contract(item) for item in contracts))

    def test_auto_planner_uses_constraint_graph(self):
        with tempfile.TemporaryDirectory() as folder:
            contract = read_json(create_pilots(contracts_dir=Path(folder))[1])
        graph = CadGraph.from_json({"schema_version": "creo-cad-graph/v1", "assembly_file": contract["assembly"]["file"],
                                    "root_occurrence": "ROOT", "occurrences": [{"id": "WATER_TANK", "part_no": "JH9919000534"}, {"id": "ROOT", "part_no": "JB9918900337"}],
                                    "constraints": [{"id": "mate-01", "occurrences": ["WATER_TANK", "ROOT"], "assembly_axis": [0, 0, 1], "display_distance": 100}]})
        result = plan(contract, graph)
        self.assertEqual(result["automation"]["phase"], "planned")
        self.assertEqual(result["moving_occurrences"], ["WATER_TANK"])
        self.assertEqual(result["receiver_occurrences"], ["ROOT"])
        self.assertEqual(result["translation"]["vectors"][0]["vector"], [0.0, 0.0, -100.0])

    def test_contract_rejects_rotation_change(self):
        contract = {"schema_version": "step-contract/v1", "automation": {"phase": "planned"}, "expected_bom_items": [{}],
                "moving_occurrences": ["m"], "receiver_occurrences": ["r"], "retained_occurrences": [],
                "translation": {"type": "translation_only", "vectors": [[1, 0, 0]], "evidence": "constraint"},
                "camera": {"selected": "oblique"}, "method": {"text": "", "source": None},
                "render": {"complete_image": "a.png", "exploded_image": "b.png", "projection": {"moving_point_complete": [1, 1], "moving_point_exploded": [0, 0]},
                           "occurrences": [{"id": "m", "complete_matrix": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]], "exploded_matrix": [[0,1,0,0],[1,0,0,0],[0,0,1,0],[0,0,0,1]]}, {"id": "r", "complete_matrix": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]], "exploded_matrix": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]}]}}
        self.assertTrue(any("发生旋转" in error for error in validate_contract(contract, require_render=True)))

    def test_recursive_graph_occurrence_path_is_stable(self):
        graph = CadGraph.from_json({"schema_version": "creo-cad-graph/v2", "assembly_file": "root.asm.2",
                                    "root_occurrence": "ROOT", "occurrences": [{"id": "51/4888", "component_path": [51, 4888], "part_no": "A"}],
                                    "constraints": []})
        node = graph.occurrences[0]
        self.assertEqual(CadGraph.occurrence_path(node), [51, 4888])
        self.assertEqual(CadGraph.occurrence_key(node), "51/4888")

    def test_same_point_arrow_requires_exact_occurrence_coverage(self):
        matrix = [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
        contract = {"moving_occurrences": ["51/1", "51/2"], "receiver_occurrences": [], "retained_occurrences": [],
                    "render": {"complete_image": "a.jpg", "exploded_image": "b.jpg",
                               "occurrences": [{"id": "51/1", "complete_matrix": matrix, "exploded_matrix": matrix},
                                               {"id": "51/2", "complete_matrix": matrix, "exploded_matrix": matrix}],
                               "projection": {"policy": "same_cad_point/v1", "status": "passed", "arrows": [
                                   {"covered_occurrences": ["51/1", "51/2"], "anchor_local": [0,0,0], "anchor_source": "model_surface",
                                    "complete_root": [0,0,0], "exploded_root": [0,0,10],
                                    "complete_screen_plane": [0,0], "exploded_screen_plane": [0,10], "merged": True}]}}}
        self.assertEqual(validate_render(contract), [])
        contract["render"]["projection"]["arrows"][0]["covered_occurrences"] = ["51/1"]
        self.assertTrue(any("覆盖" in error for error in validate_render(contract)))

    def test_forward_stage_rejects_broad_parent_occurrence(self):
        contract = {"schema_version": "step-contract/v2", "assembly": {"authoritative_manifest": "a.json"},
                    "automation": {"phase": "planned"}, "expected_bom_items": [{}],
                    "moving_occurrences": ["51/5025/79"], "receiver_occurrences": ["51/5025/47"],
                    "retained_occurrences": [], "visible_occurrences": ["51", "51/5025/79", "51/5025/47"],
                    "stage_visibility": {"policy": "forward_exact/v1", "completed_occurrences": ["51"],
                                         "required_context_occurrences": []},
                    "translation": {"type": "translation_only", "vectors": [[0,45,0]], "evidence": "constraint"},
                    "camera": {"selected": "fixed_456"}, "method": {"text": "", "source": None}}
        errors = validate_contract(contract)
        self.assertTrue(any("宽泛父 occurrence" in error for error in errors))
