from __future__ import annotations

import unittest

from sop_pipeline.agent.repair_candidates import BoundedRepairPlanner


class RepairCandidateTests(unittest.TestCase):
    def test_candidate_group_has_two_to_four_variants_and_one_factor(self) -> None:
        candidates = BoundedRepairPlanner().propose(
            step_id="step-1",
            failure_codes=("ARROW_OVERLAP", "IMAGE_DIMENSIONS_MISMATCH"),
            current={"camera_id": "fixed_123"},
        )

        self.assertGreaterEqual(len(candidates), 2)
        self.assertLessEqual(len(candidates), 4)
        self.assertEqual({candidate.factor for candidate in candidates}, {"arrow_layout"})
        self.assertTrue(all(len(candidate.changes) == 1 for candidate in candidates))

    def test_structural_hard_gate_does_not_produce_visual_candidates(self) -> None:
        candidates = BoundedRepairPlanner().propose(
            step_id="step-1",
            failure_codes=("ASSEMBLY_HASH_MISMATCH",),
            current={},
        )
        self.assertEqual(candidates, ())


if __name__ == "__main__":
    unittest.main()
