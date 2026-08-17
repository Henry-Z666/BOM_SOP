from __future__ import annotations

from dataclasses import dataclass
import unittest

from sop_pipeline.agent.physical_sequence import (
    infer_physical_precedence,
    physical_role,
)


@dataclass(frozen=True)
class Step:
    step_id: str
    title: str
    receiver_occurrences: tuple[str, ...]
    main_process_id: str = "process-001"
    stage_scope_occurrence: str = "ROOT"
    depends_on: tuple[str, ...] = ()


class PhysicalSequenceTests(unittest.TestCase):
    def test_seal_cover_and_clamp_follow_interface_physics(self) -> None:
        steps = (
            Step("cover", "卡式端盖", ("receiver",)),
            Step("gasket", "卡箍垫圈", ("receiver",)),
            Step("clamp", "不锈钢卡箍", ("receiver",)),
        )

        edges = {
            (edge.before_step_id, edge.after_step_id)
            for edge in infer_physical_precedence(steps)
        }

        self.assertIn(("gasket", "cover"), edges)
        self.assertIn(("cover", "clamp"), edges)
        self.assertIn(("gasket", "clamp"), edges)

    def test_seal_does_not_reverse_native_receiver_dependency(self) -> None:
        steps = (
            Step("valve", "球阀", ("tank-port",)),
            Step(
                "outer-seal",
                "O形密封圈",
                ("valve",),
                depends_on=("valve",),
            ),
        )

        edges = infer_physical_precedence(steps)

        self.assertNotIn(
            ("outer-seal", "valve"),
            {(edge.before_step_id, edge.after_step_id) for edge in edges},
        )

    def test_clamp_connector_is_not_misclassified_as_retainer(self) -> None:
        self.assertEqual(physical_role("不锈钢卡箍接头"), "component")
        self.assertEqual(physical_role("顶盖防水密封条"), "seal")

    def test_unrelated_same_process_seal_is_not_pulled_before_closure(self) -> None:
        steps = (
            Step("cover", "卡式端盖", ("port-a",)),
            Step("gasket-a", "卡箍垫圈（1/2）", ("port-a",)),
            Step("gasket-b", "卡箍垫圈（2/2）", ("port-b",)),
            Step("sensor-seal", "压力传感器密封圈", ("sensor-port",)),
        )

        edges = {
            (edge.before_step_id, edge.after_step_id)
            for edge in infer_physical_precedence(steps)
        }

        self.assertIn(("gasket-a", "cover"), edges)
        self.assertIn(("gasket-b", "cover"), edges)
        self.assertNotIn(("sensor-seal", "cover"), edges)


if __name__ == "__main__":
    unittest.main()
