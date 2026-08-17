from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

from sop_pipeline.agent import AgentCore, PipelineOrchestrator, SkillStatus
from sop_pipeline.agent.qwen_adapter import SemanticReview


class _SmokeAdvisor:
    """Offline semantic boundary for the real Creo/Agent structure smoke."""

    def recommend_plan_choices(self, items):
        del items
        return ()

    def review_render(self, image_file: Path, contract):
        del image_file, contract
        return SemanticReview(passed=True, issues=())


def _hash_files(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one real Creo task through AgentCore and SkillRuntime."
    )
    parser.add_argument("--bom", type=Path, required=True)
    parser.add_argument("--cad", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--runtime-config", type=Path)
    args = parser.parse_args()

    if args.runtime_config is not None:
        runtime_config = args.runtime_config.resolve()
        if not runtime_config.is_file():
            raise FileNotFoundError(runtime_config)
        os.environ["QWEN_CREO_RUNTIME_CONFIG"] = str(runtime_config)

    workspace = args.workspace or Path(
        tempfile.mkdtemp(prefix="qwen-creo-agent-smoke-")
    )
    before = _hash_files(args.cad)
    workflow = PipelineOrchestrator(adapters={"qwen_advisor": _SmokeAdvisor()})
    core = AgentCore(workspace, workflow)
    run_id = core.create_run(args.bom, args.cad)
    packet = core.analyze(run_id)
    answers = {
        item.item_id: item.recommended_option
        for item in packet.items
        if item.category == "CONFIRMATION"
    }
    revision = core.confirm(run_id, answers)
    runtime = workflow.runtime
    if runtime is None:
        raise RuntimeError("Agent did not bind SkillRuntime")
    compiled = runtime.execute(
        run_id,
        "compile-render-jobs",
        (f"plans/locked-render-plan-{revision.revision:04d}.json",),
    )
    if compiled.status is not SkillStatus.PASSED:
        raise RuntimeError(f"render compilation failed: {compiled.diagnostics}")
    jobs_path = compiled.artifacts[0].relative_path
    jobs = runtime.artifacts.read_json(core.get_run(run_id).workspace, jobs_path)
    target = next(
        task
        for task in jobs["tasks"]
        if task.get("payload", {}).get("execution_mode") == "formal"
    )
    rendered = runtime.execute(
        run_id,
        "render-batch",
        (jobs_path,),
        {"step_ids": [target["step_id"]]},
    )
    if rendered.status is not SkillStatus.PASSED:
        raise RuntimeError(f"render skill failed: {rendered.diagnostics}")
    validated = runtime.execute(
        run_id,
        "validate-repair",
        (jobs_path, rendered.artifacts[0].relative_path),
    )
    if validated.status is not SkillStatus.PASSED:
        raise RuntimeError(f"validation skill failed: {validated.diagnostics}")
    validation_path = next(
        artifact.relative_path
        for artifact in validated.artifacts
        if artifact.kind == "validation-result"
    )
    candidates_path = next(
        artifact.relative_path
        for artifact in validated.artifacts
        if artifact.kind == "candidate-set"
    )
    published = runtime.execute(
        run_id,
        "publish-delivery",
        (
            "analysis/normalized-bom.json",
            f"plans/locked-render-plan-{revision.revision:04d}.json",
            validation_path,
            candidates_path,
        ),
    )
    if published.status is not SkillStatus.PASSED:
        raise RuntimeError(f"publication skill failed: {published.diagnostics}")
    publication = runtime.artifacts.read_json(
        core.get_run(run_id).workspace, published.artifacts[0].relative_path
    )
    delivery = Path(publication["delivery_directory"])
    sop = delivery / str(publication["workbook"])
    if not sop.is_file():
        raise RuntimeError(f"published SOP is missing: {sop}")
    result = runtime.artifacts.read_json(
        core.get_run(run_id).workspace, rendered.artifacts[0].relative_path
    )
    target_result = next(
        item for item in result["steps"] if item["step_id"] == target["step_id"]
    )
    after = _hash_files(args.cad)
    if before != after:
        raise RuntimeError("source CAD hashes changed during Agent smoke run")
    image = (
        core.get_run(run_id).workspace / str(target_result.get("image_path", ""))
    )
    if target_result["status"] != "PASSED" or not image.is_file():
        raise RuntimeError(f"real target did not pass: {target_result}")
    print(
        json.dumps(
            {
                "run_id": run_id,
                "workspace": str(workspace),
                "step_id": target["step_id"],
                "image": str(image),
                "output_hash": target_result["output_hash"],
                "sop": str(sop),
                "publication_pending": publication["pending"],
                "cad_files": len(before),
                "cad_unchanged": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
