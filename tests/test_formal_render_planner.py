from __future__ import annotations

from dataclasses import asdict, replace
import unittest

from sop_pipeline.agent.bom_cad_mapper import BomCadMap, BomOccurrenceMapping
from sop_pipeline.agent.bom_normalizer import NormalizedBom, NormalizedBomRow
from sop_pipeline.agent.draft_planner import DraftInstallationStep, DraftPlan
from sop_pipeline.agent.formal_render_planner import (
    compile_formal_render_plan,
    formal_render_plan_from_dict,
    lock_formal_render_plan,
)
from sop_pipeline.agent.render_job_compiler import compile_locked_render_jobs


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

    def test_uncertain_scope_uses_bounded_recommendation_and_round_trips(self) -> None:
        bom, draft, mapping, graph = fixture()
        plan = compile_formal_render_plan(bom, draft, mapping, graph)

        locked = lock_formal_render_plan(
            plan,
            {"subassembly-scope-0006": "不确定，按推荐方案生成"},
            {"subassembly-scope-0006": "whole"},
        )
        restored = formal_render_plan_from_dict(asdict(locked))

        self.assertEqual(locked.scope_decisions["subassembly-scope-0006"], "whole")
        self.assertEqual(restored, locked)

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
        self.assertEqual(
            [item["variant_id"] for item in task.payload["presentation"]["variants"]],
            ["base", "zoom-in-50", "zoom-in-110", "zoom-out-15"],
        )
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
            task.payload["presentation"]["centering"]["schema_version"],
            "adaptive-screen-center/v1",
        )
        self.assertEqual(
            task.payload["presentation"]["zoom_recovery"],
            {
                "schema_version": "centered-span-zoom/v1",
                "target_subject_span": 0.55,
                "min_zoom": 0.4,
                "max_zoom": 3.2,
                "max_rounds": 2,
            },
        )
        self.assertNotIn("output_path", str(task.payload))

    def test_unconfirmed_plan_cannot_compile_worker_tasks(self) -> None:
        bom, draft, mapping, graph = fixture()
        plan = compile_formal_render_plan(bom, draft, mapping, graph)

        with self.assertRaisesRegex(ValueError, "locked"):
            compile_locked_render_jobs(plan)


if __name__ == "__main__":
    unittest.main()
