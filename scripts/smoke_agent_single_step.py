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


def _camera_id(task: dict) -> str:
    variants = task.get("payload", {}).get("presentation", {}).get("variants", [])
    if not variants:
        return ""
    return str(variants[0].get("camera_id", ""))


def _select_targets(tasks: list[dict], step_count: int) -> list[dict]:
    if step_count < 1:
        raise ValueError("step count must be positive")
    formal = [
        task
        for task in tasks
        if task.get("payload", {}).get("execution_mode") == "formal"
    ]
    if len(formal) < step_count:
        raise ValueError(
            f"requested {step_count} formal steps but only {len(formal)} exist"
        )
    if step_count == 1:
        return formal[:1]

    by_camera: dict[str, list[dict]] = {}
    for task in formal:
        by_camera.setdefault(_camera_id(task), []).append(task)
    primary_camera, primary_tasks = max(
        by_camera.items(), key=lambda item: (len(item[1]), item[0])
    )
    primary_tasks = sorted(
        primary_tasks, key=lambda task: (bool(task.get("depends_on")), formal.index(task))
    )
    selected = primary_tasks[: min(2, step_count)]
    if len(selected) < step_count:
        secondary = [
            task
            for task in formal
            if _camera_id(task) != primary_camera and task not in selected
        ]
        secondary.sort(
            key=lambda task: (bool(task.get("depends_on")), formal.index(task))
        )
        if secondary:
            selected.append(secondary[0])
    for task in formal:
        if len(selected) >= step_count:
            break
        if task not in selected:
            selected.append(task)
    return selected


def _scale_evidence(task: dict) -> dict:
    profile = (
        task.get("payload", {})
        .get("presentation", {})
        .get("framing_profile", {})
    )
    evidence = profile.get("scale_evidence", {}) if isinstance(profile, dict) else {}
    return evidence if isinstance(evidence, dict) else {}


def _activity_scale(task: dict) -> float:
    evidence = _scale_evidence(task)
    values = evidence.get("activity_projected_size_root", [])
    if evidence.get("status") != "available" or not isinstance(values, list):
        raise ValueError(f"task has no real CAD scale evidence: {task.get('step_id')}")
    return max(float(value) for value in values)


def _spread(values: list[dict], count: int) -> list[dict]:
    if count == 1:
        return values[:1]
    indexes = [round(index * (len(values) - 1) / (count - 1)) for index in range(count)]
    return [values[index] for index in indexes]


def _select_scale_spread(tasks: list[dict], step_count: int) -> list[dict]:
    formal = [
        task
        for task in tasks
        if task.get("payload", {}).get("execution_mode") == "formal"
        and _scale_evidence(task).get("status") == "available"
    ]
    by_camera: dict[str, list[dict]] = {}
    for task in formal:
        by_camera.setdefault(_camera_id(task), []).append(task)
    eligible = {
        camera_id: camera_tasks
        for camera_id, camera_tasks in by_camera.items()
        if len(camera_tasks) >= step_count
    }
    if not eligible:
        raise ValueError(
            f"no fixed camera has {step_count} formal steps with real CAD bounds"
        )
    _, candidates = max(
        eligible.items(),
        key=lambda item: (
            len(
                {
                    _scale_evidence(task).get("activity_bucket")
                    for task in item[1]
                }
            ),
            len(item[1]),
            item[0],
        ),
    )
    representatives: dict[int, dict] = {}
    for task in sorted(candidates, key=_activity_scale):
        bucket = int(_scale_evidence(task)["activity_bucket"])
        representatives.setdefault(bucket, task)
    pool = [representatives[key] for key in sorted(representatives)]
    if len(pool) < step_count:
        pool = sorted(candidates, key=_activity_scale)
    return _spread(pool, step_count)


def _manifest_frame_counts(run_workspace: Path, step_ids: list[str]) -> dict[str, int]:
    counts = {step_id: 0 for step_id in step_ids}
    worker_root = run_workspace / "internal" / "native-worker"
    if not worker_root.is_dir():
        return counts
    for manifest in worker_root.glob("generation-*/manifests/*.tsv"):
        for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
            output_path = line.split("\t", 1)[0]
            for step_id in step_ids:
                if step_id in output_path:
                    counts[step_id] += 1
                    break
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one real Creo task through AgentCore and SkillRuntime."
    )
    parser.add_argument("--bom", type=Path, required=True)
    parser.add_argument("--cad", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--runtime-config", type=Path)
    parser.add_argument("--step-count", type=int, default=1)
    parser.add_argument(
        "--selection",
        choices=("default", "scale-spread"),
        default="default",
    )
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
    targets = (
        _select_scale_spread(jobs["tasks"], args.step_count)
        if args.selection == "scale-spread"
        else _select_targets(jobs["tasks"], args.step_count)
    )
    target_ids = [str(task["step_id"]) for task in targets]
    render_parameters = {
        "step_ids": target_ids,
        "result_scope_contract": "requested/v1",
    }
    rendered = runtime.execute(
        run_id,
        "render-batch",
        (jobs_path,),
        render_parameters,
    )
    if rendered.status is not SkillStatus.PASSED:
        raise RuntimeError(f"render skill failed: {rendered.diagnostics}")
    run_workspace = core.get_run(run_id).workspace
    frames_after_render = _manifest_frame_counts(run_workspace, target_ids)
    cached_render = runtime.execute(
        run_id,
        "render-batch",
        (jobs_path,),
        render_parameters,
    )
    frames_after_cache = _manifest_frame_counts(run_workspace, target_ids)
    if cached_render != rendered or frames_after_cache != frames_after_render:
        raise RuntimeError("identical render fingerprint was not reused without new frames")
    validated = runtime.execute(
        run_id,
        "validate-repair",
        (jobs_path, rendered.artifacts[0].relative_path),
    )
    if validated.status not in {SkillStatus.PASSED, SkillStatus.QUESTIONED}:
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
    if published.status not in {SkillStatus.PASSED, SkillStatus.QUESTIONED}:
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
    target_results = [
        next(item for item in result["steps"] if item["step_id"] == step_id)
        for step_id in target_ids
    ]
    after = _hash_files(args.cad)
    if before != after:
        raise RuntimeError("source CAD hashes changed during Agent smoke run")
    images = [
        run_workspace / str(target_result.get("image_path", ""))
        for target_result in target_results
    ]
    for target_result, image in zip(target_results, images, strict=True):
        if target_result["status"] == "PASSED" and not image.is_file():
            raise RuntimeError(f"passed target image is missing: {target_result}")
    delivery_files = sorted(
        path.relative_to(delivery).as_posix()
        for path in delivery.rglob("*")
        if path.is_file()
    )
    if not delivery_files or any(
        path.split("/", 1)[0] not in {"SOP.xlsx", "SOP_待确认.xlsx", "步骤图片"}
        for path in delivery_files
    ):
        raise RuntimeError(f"delivery whitelist violation: {delivery_files}")
    print(
        json.dumps(
            {
                "run_id": run_id,
                "workspace": str(workspace),
                "steps": [
                    {
                        "step_id": task["step_id"],
                        "camera_id": _camera_id(task),
                        "scale_signature": (
                            task["payload"]["presentation"]["framing_profile"].get(
                                "scale_signature"
                            )
                        ),
                        "activity_scale_root": _activity_scale(task)
                        if _scale_evidence(task).get("status") == "available"
                        else None,
                        "context_scale_root": max(
                            float(value)
                            for value in _scale_evidence(task)[
                                "context_projected_size_root"
                            ]
                        )
                        if _scale_evidence(task).get("status") == "available"
                        else None,
                        "status": target_result["status"],
                        "image": str(image) if image.is_file() else None,
                        "output_hash": target_result["output_hash"],
                        "rendered_frames": frames_after_render[task["step_id"]],
                    }
                    for task, target_result, image in zip(
                        targets, target_results, images, strict=True
                    )
                ],
                "sop": str(sop),
                "publication_pending": publication["pending"],
                "render_fingerprint_reused": True,
                "delivery_files": delivery_files,
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
