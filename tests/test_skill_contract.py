from __future__ import annotations

import unittest

from sop_pipeline.agent import Diagnostic, RetryScope, SkillResult, SkillStatus


class SkillContractTests(unittest.TestCase):
    def test_retryable_result_requires_machine_executable_retry_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "retry_scope"):
            SkillResult(
                schema_version="agent-skill-result/v1",
                skill="render-batch",
                run_id="run-001",
                status=SkillStatus.RETRYABLE,
                input_fingerprint="sha256:input",
                diagnostics=(Diagnostic("CREO_CRASH", "Creo 会话异常退出"),),
                retry_scope=None,
                allowed_next=("render-batch",),
            )

        result = SkillResult(
            schema_version="agent-skill-result/v1",
            skill="render-batch",
            run_id="run-001",
            status=SkillStatus.RETRYABLE,
            input_fingerprint="sha256:input",
            diagnostics=(Diagnostic("CREO_CRASH", "Creo 会话异常退出"),),
            retry_scope=RetryScope("render_job", ("job-017",), 2),
            allowed_next=("render-batch",),
        )

        self.assertEqual(result.retry_scope.selectors, ("job-017",))

    def test_blocked_result_requires_a_stable_diagnostic_code(self) -> None:
        with self.assertRaisesRegex(ValueError, "diagnostic"):
            SkillResult(
                schema_version="agent-skill-result/v1",
                skill="lock-assembly",
                run_id="run-001",
                status=SkillStatus.BLOCKED,
                input_fingerprint="sha256:input",
                diagnostics=(),
                retry_scope=None,
                allowed_next=(),
            )


if __name__ == "__main__":
    unittest.main()
