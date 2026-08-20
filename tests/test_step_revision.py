from __future__ import annotations

import unittest

from sop_pipeline.agent.step_revision import (
    RevisionKind,
    StepDependencyGraph,
    StepRevision,
    validate_revision,
)


class StepRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = StepDependencyGraph(
            {
                "a": (),
                "b": ("a",),
                "c": ("b",),
                "d": ("a",),
            }
        )

    def test_presentation_change_invalidates_only_current_step(self) -> None:
        revision = StepRevision(
            1, "b", RevisionKind.PRESENTATION, {"candidate_id": "candidate-a"}
        )
        self.assertEqual(self.graph.invalidated_by(revision), frozenset({"b"}))

    def test_complete_state_change_invalidates_only_dependency_descendants(self) -> None:
        revision = StepRevision(
            1, "b", RevisionKind.COMPLETE_STATE, {"direction": [0, 0, 1]}
        )
        self.assertEqual(self.graph.invalidated_by(revision), frozenset({"b", "c"}))

    def test_unrelated_branch_remains_valid(self) -> None:
        revision = StepRevision(1, "b", RevisionKind.COMPLETE_STATE, {})
        self.assertNotIn("d", self.graph.invalidated_by(revision))

    def test_revision_cannot_override_locked_camera_or_arbitrary_path(self) -> None:
        invalid = StepRevision(
            1,
            "b",
            RevisionKind.PRESENTATION,
            {"camera_id": "invented", "output_path": "C:/anywhere"},
        )
        with self.assertRaises(ValueError):
            validate_revision(invalid)


if __name__ == "__main__":
    unittest.main()
