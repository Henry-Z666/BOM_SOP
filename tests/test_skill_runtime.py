from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from sop_pipeline.agent import (
    AgentCore,
    RunRecord,
    RunStatus,
    SkillContext,
    SkillInvocation,
    SkillRuntime,
    SkillStatus,
)
from sop_pipeline.agent.artifacts import ArtifactStore
from sop_pipeline.agent.skill_handlers import default_skill_handlers
from sop_pipeline.agent.skill_cli import execute as execute_skill_cli
from sop_pipeline.agent.skill_registry import AGENT_SKILL_DEFINITIONS, SkillRegistry
from sop_pipeline.agent.store import RunStore
from tests.test_agent_analysis import _xlsx


def _inputs(root: Path) -> tuple[Path, Path]:
    bom = root / "BOM.xlsx"
    _xlsx(
        bom,
        [(
            "BOM",
            [
                ["层级", "物料编码", "图号", "名称", "数量", "单位", "装配步骤"],
                ["30", "ROOT", "ROOT-ASM", "设备总装", "1", "件", "第2步：检查"],
                ["30.1", "A", "PART-A", "底座", "1", "件", "第1步：固定底座"],
            ],
        )],
    )
    cad = root / "cad"
    cad.mkdir()
    (cad / "root-asm.asm.1").write_bytes(b"root")
    (cad / "part-a.prt.1").write_bytes(b"part")
    return bom, cad


class SkillRuntimeTests(unittest.TestCase):
    def test_contract_version_changes_execution_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad = _inputs(root)
            workspace = root / "workspace"
            core = AgentCore(workspace)
            run_id = core.create_run(bom, cad)
            store = RunStore(workspace / "agent.sqlite3")
            run = store.get(run_id)
            invocation = SkillInvocation(
                "skill-invocation/v1", run_id, "plan-assembly", (), {}
            )
            current = SkillRuntime(
                store, ArtifactStore(store), default_skill_handlers()
            )
            old_definitions = dict(AGENT_SKILL_DEFINITIONS)
            old_definitions["plan-assembly"] = replace(
                old_definitions["plan-assembly"],
                contract_version="agent-skill/v1",
            )
            old = SkillRuntime(
                store,
                ArtifactStore(store),
                default_skill_handlers(),
                registry=SkillRegistry(old_definitions),
            )

            current_fingerprint = current._fingerprint(run, invocation, ())
            old_fingerprint = old._fingerprint(run, invocation, ())

        self.assertNotEqual(current_fingerprint, old_fingerprint)

    def test_all_twelve_skills_have_executable_handlers(self) -> None:
        self.assertEqual(
            set(default_skill_handlers()),
            {
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
            },
        )

    def test_successful_skill_is_reused_by_input_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad = _inputs(root)
            workspace = root / "workspace"
            core = AgentCore(workspace)
            run_id = core.create_run(bom, cad)
            store = RunStore(workspace / "agent.sqlite3")
            runtime = SkillRuntime(
                store, ArtifactStore(store), default_skill_handlers()
            )

            first = runtime.execute(run_id, "intake-preflight")
            second = runtime.execute(run_id, "intake-preflight")
            normalized = runtime.execute(
                run_id,
                "normalize-bom",
                ("analysis/input-manifest.json",),
            )

        self.assertEqual(first.status, SkillStatus.PASSED)
        self.assertEqual(first, second)
        self.assertEqual(normalized.status, SkillStatus.PASSED)
        self.assertEqual(normalized.artifacts[0].kind, "normalized-bom")

    def test_cross_run_artifact_reference_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad = _inputs(root)
            workspace = root / "workspace"
            core = AgentCore(workspace)
            first_run = core.create_run(bom, cad)
            second_run = core.create_run(bom, cad)
            store = RunStore(workspace / "agent.sqlite3")
            runtime = SkillRuntime(
                store, ArtifactStore(store), default_skill_handlers()
            )
            artifact = runtime.execute(
                first_run, "intake-preflight"
            ).artifacts[0]

            with self.assertRaises(KeyError):
                runtime.execute(
                    second_run,
                    "normalize-bom",
                    (artifact.artifact_id,),
                )

    def test_skill_cli_executes_inside_existing_run(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom, cad = _inputs(root)
            workspace = root / "workspace"
            run_id = AgentCore(workspace).create_run(bom, cad)

            result = execute_skill_cli(
                workspace, run_id, "intake-preflight", (), {}
            )

        self.assertEqual(result["skill"], "intake-preflight")
        self.assertEqual(result["status"], "passed")

    def test_tool_definitions_are_provider_neutral_json_schema(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = RunStore(Path(folder) / "agent.sqlite3")
            runtime = SkillRuntime(
                store, ArtifactStore(store), default_skill_handlers()
            )
            tools = runtime.tool_definitions()

        self.assertEqual(len(tools), 12)
        self.assertTrue(all("input_schema" in item for item in tools))
        self.assertFalse(any("openai" in str(item).casefold() for item in tools))

    def test_all_handlers_return_stable_failure_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            workspace = root / "workspace"
            store = RunStore(workspace / "agent.sqlite3")
            run = RunRecord(
                run_id="failure-contract-run",
                bom_file=root / "missing.xlsx",
                cad_directory=root / "missing-cad",
                workspace=workspace / "runs" / "failure-contract-run",
                status=RunStatus.ANALYZING,
                input_fingerprint="sha256:test",
                plan_revision=0,
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
            run.workspace.mkdir(parents=True)
            store.add(run)
            context = SkillContext(run, ArtifactStore(store), store, {})
            handlers = default_skill_handlers()
            outputs = {}
            for name, handler in handlers.items():
                outputs[name] = handler(
                    context,
                    SkillInvocation(
                        "skill-invocation/v1",
                        run.run_id,
                        name,
                        (),
                        {},
                    ),
                )

        self.assertEqual(set(outputs), set(handlers))
        self.assertTrue(
            all(
                output.status in {SkillStatus.BLOCKED, SkillStatus.RETRYABLE}
                for output in outputs.values()
            )
        )
        self.assertTrue(all(output.diagnostics for output in outputs.values()))


if __name__ == "__main__":
    unittest.main()
