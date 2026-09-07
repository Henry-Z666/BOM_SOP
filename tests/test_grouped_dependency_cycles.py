from dataclasses import replace
import unittest

from test_formal_render_planner import fixture, bom_row, mapped, constraint, transform
from sop_pipeline.agent.formal_render_planner import compile_formal_render_plan


def grouped_fixture(cyclic_group=True):
    bom, draft, mapping, graph = fixture()
    rows = (bom_row(2, "1", "ROOT", "总装"), bom_row(3, "1.1", "BASE", "基体"),
            replace(bom_row(4, "1.2", "A", "传感器"), quantity=2),
            replace(bom_row(5, "1.3", "B", "接头"), quantity=2))
    bom = replace(bom, rows=rows)
    mapping = replace(mapping, rows=(mapped(2, "1", "ROOT", None, ("ROOT",)),
        mapped(3, "1.1", "BASE", 2, ("10",)), mapped(4, "1.2", "A", 2, ("20", "21")),
        mapped(5, "1.3", "B", 2, ("30", "31"))), matched_rows=4)
    graph["occurrences"] = [{"occurrence_id": n, "parent_occurrence": "ROOT", "part_no": n + ".prt", "transform": transform(0, 0, i * 10)} for i, n in enumerate(("10", "20", "21", "30", "31"))]
    receivers = {"20": "10", "21": "31" if cyclic_group else "10", "30": "20", "31": "10"}
    graph["constraints"] = [constraint("base", "10", "ROOT", None, None, "FIX")] + [constraint(n, n, r, [0, 0, 1], [0, 0, 0]) for n, r in receivers.items()]
    return bom, draft, mapping, graph


class GroupedDependencyCycleTests(unittest.TestCase):
    def test_grouping_cycle_is_split_without_losing_receiver_precedence(self):
        inputs = grouped_fixture()
        plan = compile_formal_render_plan(*inputs)
        producers = {o: s for s in plan.steps for o in s.moving_occurrences}
        self.assertEqual(set(producers), {"20", "21", "30", "31"})
        self.assertEqual(sum(len(s.moving_occurrences) for s in plan.steps), 4)
        order = {s.step_id: i for i, s in enumerate(plan.steps)}
        for moving, receiver in (("21", "31"), ("30", "20")):
            step = producers[moving]
            self.assertIn(producers[receiver].step_id, step.depends_on)
            self.assertLess(order[producers[receiver].step_id], order[step.step_id])
            self.assertIn(receiver, step.receiver_occurrences)
        self.assertTrue(any(d.code == "GROUP_DEPENDENCY_SPLIT" for d in plan.diagnostics))
        self.assertEqual(plan.fingerprint, compile_formal_render_plan(*inputs).fingerprint)

    def test_acyclic_groups_remain_grouped(self):
        plan = compile_formal_render_plan(*grouped_fixture(False))
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(sorted(len(s.moving_occurrences) for s in plan.steps), [2, 2])

    def test_real_occurrence_cycle_remains_blocked(self):
        inputs = grouped_fixture()
        graph = inputs[3]
        graph["constraints"] = [constraint("base", "10", "ROOT", None, None, "FIX")] + [constraint(n, n, r, [0, 0, 1], [0, 0, 0]) for n, r in {"20":"30", "21":"10", "30":"20", "31":"10"}.items()]
        with self.assertRaisesRegex(ValueError, "循环"):
            compile_formal_render_plan(*inputs)


if __name__ == "__main__":
    unittest.main()
