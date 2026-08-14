from __future__ import annotations

from pathlib import Path
import unittest

from sop_pipeline.agent import RunStatus, SkillResult
from sop_pipeline.agent.skill_registry import (
    AGENT_SKILL_DEFINITIONS,
    SkillInvocation,
    SkillRegistry,
)


EXPECTED_SKILLS = {
    "intake-preflight",
    "normalize-bom",
    "lock-assembly",
    "discover-cad",
    "map-bom-cad",
    "plan-assembly",
    "clarify-plan",
    "compile-render-jobs",
    "render-batch",
    "validate-repair",
    "publish-delivery",
    "resolve-step",
}


class AgentSkillRegistryTests(unittest.TestCase):
    def test_all_planned_skills_have_versioned_repository_manifests(self) -> None:
        self.assertEqual(set(AGENT_SKILL_DEFINITIONS), EXPECTED_SKILLS)
        for skill_name in EXPECTED_SKILLS:
            manifest = Path("skills") / skill_name / "SKILL.md"
            text = manifest.read_text(encoding="utf-8")
            self.assertIn(f"name: {skill_name}", text)
            self.assertLess(len(text.splitlines()), 80)

    def test_registry_rejects_arbitrary_output_paths_before_handler(self) -> None:
        registry = SkillRegistry()
        called = False

        def handler(invocation):
            nonlocal called
            called = True
            return SkillResult.passed(
                output_refs=(), input_fingerprint="fingerprint", artifact_hashes=()
            )

        invocation = SkillInvocation(
            schema_version="skill-invocation/v1",
            run_id="run-1",
            skill_name="normalize-bom",
            input_refs=("artifact:bom",),
            parameters={"output_path": "C:/escape"},
        )
        with self.assertRaises(ValueError):
            registry.execute(invocation, RunStatus.ANALYZING, handler)
        self.assertFalse(called)

    def test_registry_rejects_skill_in_wrong_run_state(self) -> None:
        invocation = SkillInvocation(
            "skill-invocation/v1", "run-1", "publish-delivery", (), {}
        )
        with self.assertRaises(ValueError):
            SkillRegistry().execute(
                invocation,
                RunStatus.AWAITING_CONFIRMATION,
                lambda _: SkillResult.passed(
                    output_refs=(), input_fingerprint="f", artifact_hashes=()
                ),
            )


if __name__ == "__main__":
    unittest.main()
