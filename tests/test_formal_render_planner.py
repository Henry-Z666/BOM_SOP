from __future__ import annotations

from dataclasses import asdict, replace
import math
import unittest

from sop_pipeline.agent.bom_cad_mapper import BomCadMap, BomOccurrenceMapping
from sop_pipeline.agent.bom_normalizer import NormalizedBom, NormalizedBomRow
from sop_pipeline.agent.draft_planner import DraftInstallationStep, DraftPlan
from sop_pipeline.agent.formal_render_planner import (
    compile_formal_render_plan,
    formal_render_plan_from_dict,
    lock_formal_render_plan,
)
from sop_pipeline.agent.render_job_compiler import (
    _native_selected_fit_contract,
    compile_locked_render_jobs,
)


def bom_row(number: int, level: str, drawing: str, name: str) -> NormalizedBomRow:
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
        process_only=False,
    )


def mapped(
    number: int,
    level: str,
    drawing: str,
    parent: int | None,
    occurrences: tuple[str, ...],
) -> BomOccurrenceMapping:
    return BomOccurrenceMapping(
        number,
        level,
        drawing,
        drawing,
        len(occurrences),
        parent,
        occurrences,
        "matched",
        "test",
    )


def transform(x: float, y: float, z: float) -> dict:
    return {
        "x_axis": [1.0, 0.0, 0.0],
        "y_axis": [0.0, 1.0, 0.0],
        "z_axis": [0.0, 0.0, 1.0],
        "origin": [x, y, z],
    }


def constraint(
    edge_id: str,
    moving: str,
    receiver: str,
    direction: list[float] | None,
    point: list[float] | None,
    kind: str = "INSERT",
) -> dict:
    geometry = (
        {"status": "available", "direction_root": direction, "point_root": point}
        if direction is not None and point is not None
        else {"status": "unavailable"}
    )
    return {
        "id": edge_id,
        "type": kind,
        "occurrences": [moving, receiver],
        "assembly_reference": {"occurrence_id": receiver, "geometry": geometry},
        "component_reference": {"occurrence_id": moving, "geometry": geometry},
    }


def fixture() -> tuple[NormalizedBom, DraftPlan, BomCadMap, dict]:
    rows = (
        bom_row(2, "30", "ROOT", "总装"),
        bom_row(3, "30.1", "BASE-ASM", "基础合件"),
        bom_row(4, "30.1.1", "BASE", "底板"),
        bom_row(5, "30.1.2", "BOLT", "螺钉"),
        bom_row(6, "30.2", "SUB-ASM", "功能合件"),
        bom_row(7, "30.2.1", "SUB-BASE", "功能底板"),
        bom_row(8, "30.2.2", "PART", "功能件"),
    )
    bom = NormalizedBom("normalized-bom/v1", "BOM", 1, {}, rows, ("BOM",))
    draft = DraftPlan(
        "draft-plan/v1",
        "root.asm.1",
        (
            DraftInstallationStep(
                "draft-1", "process-001", "基础", (), (3, 4, 5), (), (), "sha256:1"
            ),
            DraftInstallationStep(
                "draft-2", "process-002", "功能", (), (6, 7, 8), (), (), "sha256:2"
            ),
        ),
        20,
    )
    mappings = (
        mapped(2, "30", "ROOT", None, ("ROOT",)),
        mapped(3, "30.1", "BASE-ASM", 2, ("10",)),
        mapped(4, "30.1.1", "BASE", 3, ("10/1",)),
        mapped(5, "30.1.2", "BOLT", 3, ("10/2",)),
        mapped(6, "30.2", "SUB-ASM", 2, ("20",)),
        mapped(7, "30.2.1", "SUB-BASE", 6, ("20/1",)),
        mapped(8, "30.2.2", "PART", 6, ("20/2",)),
    )
    mapping = BomCadMap("bom-cad-map/v1", "root.asm.1", mappings, 7, (), (), ())
    graph = {
        "schema_version": "creo-cad-graph/v3",
        "assembly_file": "root.asm.1",
        "default_view_matrix": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "authoritative_assembly": {"sha256": "sha256:test"},
        "occurrences": [
            {"occurrence_id": "10", "parent_occurrence": "ROOT", "part_no": "base.asm", "transform": transform(0, 0, 0)},
            {"occurrence_id": "10/1", "parent_occurrence": "10", "part_no": "base.prt", "transform": transform(0, 0, 0)},
            {"occurrence_id": "10/2", "parent_occurrence": "10", "part_no": "bolt.prt", "transform": transform(0, 0, 10)},
            {"occurrence_id": "20", "parent_occurrence": "ROOT", "part_no": "sub.asm", "transform": transform(120, 0, 0)},
            {"occurrence_id": "20/1", "parent_occurrence": "20", "part_no": "sub-base.prt", "transform": transform(120, 0, 0)},
            {"occurrence_id": "20/2", "parent_occurrence": "20", "part_no": "part.prt", "transform": transform(120, 0, 20)},
        ],
        "constraints": [
            constraint("10-fix", "10", "ROOT", None, None, "FIX"),
            constraint("10-1-fix", "10/1", "10", None, None, "FIX"),
            constraint("10-2-insert", "10/2", "10/1", [0, 0, 1], [0, 0, 0]),
            constraint("20-mate", "20", "10/1", [1, 0, 0], [100, 0, 0], "MATE"),
            constraint("20-1-fix", "20/1", "20", None, None, "FIX"),
            constraint("20-2-insert", "20/2", "20/1", [0, 0, 1], [120, 0, 0]),
        ],
    }
    return bom, draft, mapping, graph


class FormalRenderPlannerTests(unittest.TestCase):
    def test_native_selected_fit_uses_one_fixed_relative_margin(self) -> None:
        contract = _native_selected_fit_contract()

        self.assertEqual(contract["zoom_to_selected_level"], 0.85)
        self.assertEqual(
            contract["selection_scope"],
            "moving_and_receiver_occurrences/v1",
        )
        self.assertEqual(
            contract["level_policy"],
            "fixed_native_selection_margin/v1",
        )

    def test_uses_child_reference_from_parent_level_creo_constraint(self) -> None:
        bom, draft, mapping, graph = fixture()
        parent_constraint = constraint(
            "10-parent-insert",
            "10",
            "20/1",
            [0, 0, 1],
            [0, 0, 0],
        )
        parent_constraint["component_reference"]["occurrence_id"] = "10/2"
        graph["constraints"] = [
            parent_constraint
            if item["id"] == "10-2-insert"
            else item
            for item in graph["constraints"]
        ]

        plan = compile_formal_render_plan(bom, draft, mapping, graph)
        child_step = next(
            step for step in plan.steps if step.source_bom_rows == (5,)
        )

        self.assertEqual(child_step.status, "ready")
        self.assertEqual(child_step.moving_occurrences, ("10/2",))
        self.assertEqual(child_step.receiver_occurrences, ("20/1",))
        self.assertEqual(child_step.constraint_ids, ("10-parent-insert",))
        self.assertNotIn("NO_NATIVE_RECEIVER_GEOMETRY", child_step.diagnostics)

    def test_parent_constraint_cannot_supply_geometry_for_a_different_child(self) -> None:
        bom, draft, mapping, graph = fixture()
        parent_constraint = constraint(
            "10-parent-insert",
            "10",
            "20/1",
            [0, 0, 1],
            [0, 0, 0],
        )
        parent_constraint["component_reference"]["occurrence_id"] = "10/1"
        graph["constraints"] = [
            parent_constraint
            if item["id"] == "10-2-insert"
            else item
            for item in graph["constraints"]
        ]

        plan = compile_formal_render_plan(bom, draft, mapping, graph)
        child_step = next(
            step for step in plan.steps if step.source_bom_rows == (5,)
        )

        self.assertEqual(child_step.status, "questioned")
        self.assertIn("NO_NATIVE_RECEIVER_GEOMETRY", child_step.diagnostics)

    def test_rejects_constraint_anchor_outside_moving_occurrence_bounds(self) -> None:
        bom, draft, mapping, graph = fixture()
        moving = next(
            item for item in graph["occurrences"] if item["occurrence_id"] == "10/2"
        )
        moving["bounds_root"] = {
            "status": "available",
            "source": "solid_geom_outline/v1",
            "min": [-1.0, -1.0, 9.0],
            "max": [1.0, 1.0, 11.0],
        }
        edge = next(
            item for item in graph["constraints"] if item["id"] == "10-2-insert"
        )
        edge["component_reference"]["geometry"]["point_root"] = [0.0, 0.0, -100.0]

        plan = compile_formal_render_plan(bom, draft, mapping, graph)
        child_step = next(step for step in plan.steps if step.source_bom_rows == (5,))

        self.assertEqual(child_step.status, "questioned")
        self.assertEqual(child_step.arrow_anchors, ())
        self.assertIn("MOVING_ARROW_ANCHOR_UNAVAILABLE", child_step.diagnostics)

    def test_uses_creo_physical_anchor_when_constraint_surface_is_not_on_solid(self) -> None:
        bom, draft, mapping, graph = fixture()
        moving = next(
            item for item in graph["occurrences"] if item["occurrence_id"] == "10/2"
        )
        moving["bounds_root"] = {
            "status": "available",
            "source": "solid_geom_outline/v1",
            "min": [-1.0, -1.0, 9.0],
            "max": [1.0, 1.0, 11.0],
        }
        moving["physical_anchor_root"] = [0.0, 0.0, 10.0]
        edge = next(
            item for item in graph["constraints"] if item["id"] == "10-2-insert"
        )
        edge["component_reference"]["geometry"]["point_root"] = [0.0, 0.0, -100.0]

        plan = compile_formal_render_plan(bom, draft, mapping, graph)
        child_step = next(step for step in plan.steps if step.source_bom_rows == (5,))

        self.assertEqual(child_step.status, "ready")
        self.assertEqual(child_step.arrow_anchors[0].complete_point_root, (0.0, 0.0, 10.0))
        self.assertNotIn("MOVING_ARROW_ANCHOR_UNAVAILABLE", child_step.diagnostics)

    def test_uses_solid_half_space_for_direction_when_origin_vector_is_weak(self) -> None:
        bom, draft, mapping, graph = fixture()
        moving = next(
            item for item in graph["occurrences"] if item["occurrence_id"] == "10/2"
        )
        moving["transform"] = transform(100.0, 0.0, 1.0)
        moving["bounds_root"] = {
            "status": "available",
            "source": "solid_geom_outline/v1",
            "min": [99.0, -1.0, 0.5],
            "max": [101.0, 1.0, 1.5],
        }
        moving["physical_anchor_root"] = [100.0, 0.0, 1.0]

        plan = compile_formal_render_plan(bom, draft, mapping, graph)
        child_step = next(step for step in plan.steps if step.source_bom_rows == (5,))

        self.assertEqual(child_step.status, "ready")
        self.assertGreater(child_step.translation_vector_root[2], 0.0)
        self.assertNotIn("DIRECTION_SIGN_WEAK", child_step.diagnostics)

    def test_uses_receiver_solid_clearance_when_surface_axis_origin_has_wrong_side(self) -> None:
        bom, draft, mapping, graph = fixture()
        receiver = next(
            item for item in graph["occurrences"] if item["occurrence_id"] == "10/1"
        )
        moving = next(
            item for item in graph["occurrences"] if item["occurrence_id"] == "10/2"
        )
        receiver["bounds_root"] = {
            "status": "available",
            "min": [-5.0, -5.0, 0.0],
            "max": [5.0, 5.0, 8.0],
        }
        moving["bounds_root"] = {
            "status": "available",
            "min": [-1.0, -1.0, 9.0],
            "max": [1.0, 1.0, 11.0],
        }
        moving["physical_anchor_root"] = [0.0, 0.0, 10.0]
        edge = next(
            item for item in graph["constraints"] if item["id"] == "10-2-insert"
        )
        edge["assembly_reference"]["geometry"]["point_root"] = [100.0, 0.0, 10.0]
        edge["component_reference"]["geometry"]["point_root"] = [0.0, 0.0, 10.0]

        plan = compile_formal_render_plan(bom, draft, mapping, graph)
        child_step = next(step for step in plan.steps if step.source_bom_rows == (5,))

        self.assertGreater(child_step.translation_vector_root[2], 0.0)
        self.assertNotIn("DIRECTION_SIGN_WEAK", child_step.diagnostics)

    def test_same_unsigned_axis_splits_when_occurrences_need_opposite_clearance(self) -> None:
        bom, draft, mapping, graph = fixture()
        rows = list(bom.rows)
        rows[3] = replace(rows[3], quantity=2)
        bom = replace(bom, rows=tuple(rows))
        mappings = list(mapping.rows)
        mappings[3] = replace(
            mappings[3], expected_quantity=2, occurrence_ids=("10/2", "10/3")
        )
        mapping = replace(mapping, rows=tuple(mappings))
        receiver = next(
            item for item in graph["occurrences"] if item["occurrence_id"] == "10/1"
        )
        receiver["bounds_root"] = {
            "status": "available",
            "min": [-5.0, -5.0, -1.0],
            "max": [5.0, 5.0, 1.0],
        }
        first = next(
            item for item in graph["occurrences"] if item["occurrence_id"] == "10/2"
        )
        first["bounds_root"] = {
            "status": "available",
            "min": [-1.0, -1.0, 9.0],
            "max": [1.0, 1.0, 11.0],
        }
        first["physical_anchor_root"] = [0.0, 0.0, 10.0]
        graph["occurrences"].append(
            {
                "occurrence_id": "10/3",
                "parent_occurrence": "10",
                "part_no": "bolt.prt",
                "transform": transform(0, 0, -10),
                "physical_anchor_root": [0.0, 0.0, -10.0],
                "bounds_root": {
                    "status": "available",
                    "min": [-1.0, -1.0, -11.0],
                    "max": [1.0, 1.0, -9.0],
                },
            }
        )
        for edge in graph["constraints"]:
            if edge["id"] == "10-2-insert":
                edge["assembly_reference"]["geometry"]["point_root"] = [0.0, 0.0, 100.0]
                edge["component_reference"]["geometry"]["point_root"] = [0.0, 0.0, 10.0]
        second = constraint(
            "10-3-insert", "10/3", "10/1", [0, 0, 1], [0, 0, 100]
        )
        second["component_reference"]["geometry"]["point_root"] = [0.0, 0.0, -10.0]
        graph["constraints"].append(second)

        plan = compile_formal_render_plan(bom, draft, mapping, graph)
        bolt_steps = [step for step in plan.steps if step.source_bom_rows == (5,)]

        self.assertEqual(len(bolt_steps), 2)
        self.assertEqual(
            {math.copysign(1.0, step.translation_vector_root[2]) for step in bolt_steps},
            {-1.0, 1.0},
        )

    def test_long_bridge_switches_from_axial_to_clear_lateral_translation(self) -> None:
        bom, draft, mapping, graph = fixture()
        receiver = next(
            item for item in graph["occurrences"] if item["occurrence_id"] == "10/1"
        )
        moving = next(
            item for item in graph["occurrences"] if item["occurrence_id"] == "10/2"
        )
        receiver["bounds_root"] = {
            "status": "available",
            "min": [-8.0, -220.0, -20.0],
            "max": [8.0, 220.0, 0.0],
        }
        moving["bounds_root"] = {
            "status": "available",
            "min": [-1.0, -165.0, -1.0],
            "max": [1.0, 165.0, 1.0],
        }
        moving["physical_anchor_root"] = [0.0, 160.0, 0.0]
        edge = next(
            item for item in graph["constraints"] if item["id"] == "10-2-insert"
        )
        edge["assembly_reference"]["geometry"] = {
            "status": "available",
            "direction_root": [0.0, 1.0, 0.0],
            "point_root": [0.0, 160.0, 0.0],
        }
        edge["component_reference"]["geometry"] = {
            "status": "available",
            "direction_root": [0.0, 1.0, 0.0],
            "point_root": [0.0, 160.0, 0.0],
        }

        plan = compile_formal_render_plan(bom, draft, mapping, graph)
        child_step = next(step for step in plan.steps if step.source_bom_rows == (5,))

        self.assertAlmostEqual(child_step.translation_vector_root[1], 0.0)
        self.assertGreater(child_step.translation_vector_root[2], 0.0)

    def test_severely_overlapping_normal_uses_contact_backed_lateral_axis(self) -> None:
        bom, draft, mapping, graph = fixture()
        far = next(
            item for item in graph["occurrences"] if item["occurrence_id"] == "20"
        )
        far["transform"] = transform(2000.0, 0.0, 0.0)
        receiver = next(
            item for item in graph["occurrences"] if item["occurrence_id"] == "10/1"
        )
        moving = next(
            item for item in graph["occurrences"] if item["occurrence_id"] == "10/2"
        )
        receiver["bounds_root"] = {
            "status": "available",
            "min": [-40.0, -20.0, -220.0],
            "max": [40.0, 20.0, 220.0],
        }
        moving["bounds_root"] = {
            "status": "available",
            "min": [-33.0, -16.5, -7.5],
            "max": [33.0, 16.5, 7.5],
        }
        moving["physical_anchor_root"] = [0.0, -16.0, 0.0]
        edge = next(
            item for item in graph["constraints"] if item["id"] == "10-2-insert"
        )
        edge["assembly_reference"]["geometry"] = {
            "status": "available",
            "direction_root": [0.0, 0.0, 1.0],
            "point_root": [0.0, 0.0, 0.0],
        }
        edge["component_reference"]["geometry"] = {
            "status": "available",
            "direction_root": [0.0, 0.0, 1.0],
            "point_root": [0.0, -16.0, 0.0],
        }

        plan = compile_formal_render_plan(bom, draft, mapping, graph)
        child_step = next(step for step in plan.steps if step.source_bom_rows == (5,))

        self.assertGreater(child_step.translation_vector_root[1], 0.0)
        self.assertAlmostEqual(child_step.translation_vector_root[2], 0.0)

    def test_prefers_constraint_with_physical_anchor_over_higher_rank_datum(self) -> None:
        bom, draft, mapping, graph = fixture()
        moving = next(
            item for item in graph["occurrences"] if item["occurrence_id"] == "10/2"
        )
        moving["bounds_root"] = {
            "status": "available",
            "source": "solid_geom_outline/v1",
            "min": [-1.0, -1.0, 9.0],
            "max": [1.0, 1.0, 11.0],
        }
        insert = next(
            item for item in graph["constraints"] if item["id"] == "10-2-insert"
        )
        insert["component_reference"]["geometry"]["point_root"] = [0.0, 0.0, -100.0]
        mate = constraint("10-2-mate", "10/2", "10/1", [0, 0, 1], [0, 0, 0], "MATE")
        mate["component_reference"]["geometry"] = dict(
            mate["component_reference"]["geometry"]
        )
        mate["component_reference"]["geometry"]["point_root"] = [0.0, 0.0, 10.0]
        graph["constraints"].append(mate)

        plan = compile_formal_render_plan(bom, draft, mapping, graph)
        child_step = next(step for step in plan.steps if step.source_bom_rows == (5,))

        self.assertEqual(child_step.status, "ready")
        self.assertEqual(child_step.constraint_ids, ("10-2-mate",))
        self.assertEqual(child_step.arrow_anchors[0].complete_point_root, (0.0, 0.0, 10.0))

    def test_parent_occurrence_uses_descendant_reference_anchor(self) -> None:
        bom, draft, mapping, graph = fixture()
        assembly_constraint = next(
            item for item in graph["constraints"] if item["id"] == "20-mate"
        )
        assembly_constraint["component_reference"]["occurrence_id"] = "20/2"

        plan = compile_formal_render_plan(bom, draft, mapping, graph)
        assembly_step = next(
            step for step in plan.steps if step.source_bom_rows == (6,)
        )

        self.assertEqual(assembly_step.status, "ready")
        self.assertNotIn("NO_NATIVE_RECEIVER_GEOMETRY", assembly_step.diagnostics)

    def test_parent_occurrence_rejects_unrelated_component_reference(self) -> None:
        bom, draft, mapping, graph = fixture()
        assembly_constraint = next(
            item for item in graph["constraints"] if item["id"] == "20-mate"
        )
        assembly_constraint["component_reference"]["occurrence_id"] = "999/2"

        plan = compile_formal_render_plan(bom, draft, mapping, graph)
        assembly_step = next(
            step for step in plan.steps if step.source_bom_rows == (6,)
        )

        self.assertEqual(assembly_step.status, "questioned")
        self.assertIn("NO_NATIVE_RECEIVER_GEOMETRY", assembly_step.diagnostics)

    def test_builds_bottom_up_scopes_without_centre_vector_guessing(self) -> None:
        bom, draft, mapping, graph = fixture()

        plan = compile_formal_render_plan(bom, draft, mapping, graph)

        self.assertEqual(plan.initial_completed_occurrences, ("10",))
        self.assertEqual(plan.ready_steps, 3)
        self.assertEqual(plan.questioned_steps, 0)
        bolt, internal, assembly = plan.steps
        self.assertEqual(bolt.stage_scope_occurrence, "10")
        self.assertEqual(bolt.moving_occurrences, ("10/2",))
        self.assertEqual(bolt.receiver_occurrences, ("10/1",))
        self.assertGreater(bolt.translation_vector_root[2], 0.0)
        self.assertEqual(bolt.camera_id, "fixed_123")
        self.assertEqual(internal.stage_scope_occurrence, "20")
        self.assertEqual(len(internal.arrow_anchors), 1)
        self.assertEqual(assembly.moving_occurrences, ("20",))
        self.assertIn(internal.step_id, assembly.depends_on)
        self.assertIn(assembly.step_id, internal.affected_descendants)
        self.assertTrue(
            any(item.code == "SUBASSEMBLY_SCOPE_UNCONFIRMED" for item in plan.diagnostics)
        )
        self.assertEqual(plan.camera_basis["schema_version"], "assembly-camera-basis/v4")
        self.assertEqual(
            plan.camera_basis["calibration"]["fallback"],
            "equal_octant_completion/v1",
        )
        self.assertTrue(
            any(item.code == "CAMERA_BASIS_AUTO_COMPLETED" for item in plan.diagnostics)
        )

    def test_later_fixed_root_item_is_questioned_not_silently_made_foundation(self) -> None:
        bom, draft, mapping, graph = fixture()
        rows = bom.rows + (bom_row(9, "30.3", "FIXED", "后续固定件"),)
        bom = replace(bom, rows=rows)
        draft = replace(
            draft,
            steps=draft.steps
            + (
                DraftInstallationStep(
                    "draft-3", "process-003", "固定", (), (9,), (), (), "sha256:3"
                ),
            ),
        )
        fixed_mapping = mapped(9, "30.3", "FIXED", 2, ("30",))
        mapping = replace(
            mapping,
            rows=mapping.rows + (fixed_mapping,),
            matched_rows=mapping.matched_rows + 1,
        )
        graph["occurrences"].append(
            {"occurrence_id": "30", "parent_occurrence": "ROOT", "part_no": "fixed.asm", "transform": transform(0, 50, 0)}
        )
        graph["constraints"].append(constraint("30-fix", "30", "ROOT", None, None, "FIX"))

        plan = compile_formal_render_plan(bom, draft, mapping, graph)

        fixed = next(step for step in plan.steps if step.source_bom_rows == (9,))
        self.assertEqual(fixed.status, "questioned")
        self.assertIsNone(fixed.translation_vector_root)
        self.assertIn("NO_NATIVE_RECEIVER_GEOMETRY", fixed.diagnostics)

    def test_same_bom_row_splits_only_when_native_directions_differ(self) -> None:
        bom, draft, mapping, graph = fixture()
        rows = list(bom.rows)
        rows[3] = replace(rows[3], quantity=2)
        bom = replace(bom, rows=tuple(rows))
        mappings = list(mapping.rows)
        mappings[3] = replace(
            mappings[3], expected_quantity=2, occurrence_ids=("10/2", "10/3")
        )
        mapping = replace(mapping, rows=tuple(mappings))
        graph["occurrences"].append(
            {"occurrence_id": "10/3", "parent_occurrence": "10", "part_no": "bolt.prt", "transform": transform(10, 0, 0)}
        )
        graph["constraints"].append(
            constraint("10-3-insert", "10/3", "10", [1, 0, 0], [0, 0, 0])
        )

        plan = compile_formal_render_plan(bom, draft, mapping, graph)

        bolt_steps = [step for step in plan.steps if step.source_bom_rows == (5,)]
        self.assertEqual(len(bolt_steps), 2)
        self.assertEqual(
            {step.receiver_occurrences for step in bolt_steps},
            {("10/1",), ("10",)},
        )

    def test_fingerprint_and_complete_states_are_deterministic(self) -> None:
        bom, draft, mapping, graph = fixture()

        first = compile_formal_render_plan(bom, draft, mapping, graph)
        second = compile_formal_render_plan(bom, draft, mapping, graph)

        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.steps, second.steps)

    def test_locking_whole_subassembly_removes_only_internal_scope(self) -> None:
        bom, draft, mapping, graph = fixture()
        plan = compile_formal_render_plan(bom, draft, mapping, graph)
        untouched = next(step for step in plan.steps if step.stage_scope_occurrence == "10")

        locked = lock_formal_render_plan(
            plan,
            {"subassembly-scope-0006": "作为已完成整体安装"},
        )

        self.assertEqual(locked.scope_decisions, {"subassembly-scope-0006": "whole"})
        self.assertEqual(len(locked.steps), 2)
        self.assertFalse(any(step.stage_scope_occurrence == "20" for step in locked.steps))
        self.assertTrue(any(step.moving_occurrences == ("20",) for step in locked.steps))
        self.assertEqual(
            next(step for step in locked.steps if step.step_id == untouched.step_id),
            untouched,
        )

    def test_uncertain_scope_requires_an_explicit_bounded_choice(self) -> None:
        bom, draft, mapping, graph = fixture()
        plan = compile_formal_render_plan(bom, draft, mapping, graph)

        with self.assertRaisesRegex(ValueError, "无法识别子装配范围答案"):
            lock_formal_render_plan(
                plan,
                {"subassembly-scope-0006": "不确定"},
            )

    def test_lock_requires_every_scope_answer(self) -> None:
        bom, draft, mapping, graph = fixture()
        plan = compile_formal_render_plan(bom, draft, mapping, graph)

        with self.assertRaisesRegex(ValueError, "subassembly-scope-0006"):
            lock_formal_render_plan(plan, {})

    def test_receiver_installed_later_in_bom_is_moved_before_dependent_step(self) -> None:
        bom, draft, mapping, graph = fixture()
        edge = next(item for item in graph["constraints"] if item["id"] == "10-2-insert")
        edge["occurrences"][1] = "20"
        edge["assembly_reference"]["occurrence_id"] = "20"

        plan = compile_formal_render_plan(bom, draft, mapping, graph)

        dependent = next(step for step in plan.steps if step.source_bom_rows == (5,))
        receiver = next(step for step in plan.steps if step.moving_occurrences == ("20",))
        positions = {step.step_id: index for index, step in enumerate(plan.steps)}
        self.assertIn(receiver.step_id, dependent.depends_on)
        self.assertLess(positions[receiver.step_id], positions[dependent.step_id])

    def test_locked_plan_compiles_same_cad_point_worker_tasks(self) -> None:
        bom, draft, mapping, graph = fixture()
        plan = compile_formal_render_plan(bom, draft, mapping, graph)
        locked = lock_formal_render_plan(
            plan,
            {"subassembly-scope-0006": "作为已完成整体安装"},
        )

        render_plan = compile_locked_render_jobs(locked)

        self.assertEqual(render_plan.schema_version, "render-plan/v2")
        self.assertEqual(len(render_plan.tasks), 2)
        task = next(item for item in render_plan.tasks if item.payload["execution_mode"] == "formal")
        anchor = task.payload["arrow_anchors"][0]
        translation = task.payload["translation_vector_root"]
        self.assertEqual(
            anchor["expected_exploded_point_root"],
            [
                round(anchor["complete_point_root"][index] + translation[index], 6)
                for index in range(3)
            ],
        )
        self.assertEqual(task.payload["arrow_renderer"], "creo_display_list/v1")
        self.assertIn(task.payload["camera"]["id"], {"fixed_123", "fixed_456"})
        self.assertEqual(set(task.payload["camera_catalog"]), {"fixed_123", "fixed_456"})
        visibility = task.payload["camera_visibility"]
        self.assertEqual(
            visibility["schema_version"], "camera-visibility-contract/v1"
        )
        self.assertEqual(visibility["status"], "frozen")
        self.assertEqual(
            visibility["freeze_reason"],
            "preview_backed_review_not_available/v1",
        )
        self.assertFalse(visibility["formal_render_requires_selected_audit"])
        self.assertNotIn("moving_labels", visibility)
        self.assertNotIn("receiver_interface_labels", visibility)
        self.assertIn("moving_bounds", task.payload["stage_geometry_root"])
        self.assertIn("context_bounds", task.payload["stage_geometry_root"])
        self.assertEqual(
            task.payload["presentation"]["framing_profile"]["policy"],
            "native_zoom_to_selected/v1",
        )
        self.assertEqual(
            task.payload["presentation"]["native_selected_fit"],
            {
                "schema_version": "native-selected-fit/v1",
                "command": "ProCmdZoomIntoOutline",
                "selection_scope": "moving_and_receiver_occurrences/v1",
                "zoom_to_selected_level": 0.85,
                "level_policy": "fixed_native_selection_margin/v1",
                "max_commands_per_render": 1,
                "absolute_pan_zoom_forbidden": True,
            },
        )
        self.assertEqual(
            task.payload["presentation"]["variants"],
            [
                {
                    "variant_id": "base",
                    "camera_id": task.payload["camera_id"],
                    "zoom": 1.0,
                    "pan": [0.0, 0.0],
                },
            ],
        )
        expected_step = next(item for item in locked.steps if item.step_id == task.step_id)
        self.assertEqual(task.payload["title"], expected_step.title)
        self.assertEqual(
            task.payload["presentation"]["frame_gate"]["schema_version"],
            "raster-composition-gate/v2",
        )
        self.assertEqual(
            task.payload["presentation"]["focus_context"],
            "stage_visible_bbox/v1",
        )
        self.assertEqual(
            task.payload["presentation"]["framing_priority"],
            "installation_activity/v1",
        )
        self.assertEqual(
            task.payload["presentation"]["zoom_anchor"],
            "installation_activity_center/v1",
        )
        self.assertEqual(
            task.payload["presentation"]["center_gate"]["schema_version"],
            "native-composition-center-gate/v1",
        )

    def test_native_selected_fit_does_not_compile_a_scale_cache(self) -> None:
        bom, draft, mapping, graph = fixture()
        for node in graph["occurrences"]:
            origin = node["transform"]["origin"]
            node["bounds_root"] = {
                "status": "available",
                "source": "solid_geom_outline/v1",
                "min": [origin[index] - 5.0 for index in range(3)],
                "max": [origin[index] + 5.0 for index in range(3)],
            }
        origins = {
            node["occurrence_id"]: node["transform"]["origin"]
            for node in graph["occurrences"]
        }
        for edge in graph["constraints"]:
            reference = edge.get("component_reference")
            occurrence = reference.get("occurrence_id") if reference else None
            if occurrence in origins and reference["geometry"].get("status") == "available":
                reference["geometry"] = dict(reference["geometry"])
                reference["geometry"]["point_root"] = origins[occurrence]
        plan = compile_formal_render_plan(bom, draft, mapping, graph)
        locked = lock_formal_render_plan(
            plan,
            {"subassembly-scope-0006": "作为已完成整体安装"},
        )

        restored = formal_render_plan_from_dict(asdict(locked))
        render_plan = compile_locked_render_jobs(restored)
        task = next(
            item
            for item in render_plan.tasks
            if item.payload["execution_mode"] == "formal"
        )
        profile = task.payload["presentation"]["framing_profile"]

        self.assertEqual(profile["policy"], "native_zoom_to_selected/v1")
        self.assertEqual(profile["schema_version"], "native-selected-framing-policy/v1")
        self.assertEqual(
            profile["selection_scope"],
            "moving_and_receiver_occurrences/v1",
        )
        self.assertNotIn("scale_evidence", profile)
        self.assertEqual(profile["on_failure"], "question_single_frame/v1")
        self.assertNotIn("centering", task.payload["presentation"])
        self.assertNotIn("output_path", str(task.payload))

    def test_unconfirmed_plan_cannot_compile_worker_tasks(self) -> None:
        bom, draft, mapping, graph = fixture()
        plan = compile_formal_render_plan(bom, draft, mapping, graph)

        with self.assertRaisesRegex(ValueError, "locked"):
            compile_locked_render_jobs(plan)


if __name__ == "__main__":
    unittest.main()
