from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

from sop_pipeline.agent import AgentCore, PipelineOrchestrator, SkillStatus


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
            f"no fixed camera has {step_count} formal steps"
        )
    _, candidates = max(
        eligible.items(),
        key=lambda item: (len(item[1]), item[0]),
    )
    ordered = sorted(
        candidates,
        key=lambda task: int(task.get("payload", {}).get("plan_index", 0)),
    )
    return _spread(ordered, step_count)


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
        "--full",
        action="store_true",
        help="Run the complete confirmed plan through render, validation, and publication.",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Stop after compiling jobs and print execution-mode counts.",
    )
    parser.add_argument(
        "--step-id",
        help="Render one exact compiled step ID instead of selecting by position.",
    )
    parser.add_argument(
        "--step-ids",
        help="Render a comma-separated set of exact compiled step IDs.",
    )
    parser.add_argument(
        "--main-process",
        help="Limit target selection to one compiled main_process_id.",
    )
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
        os.environ["CREO_SOP_RUNTIME_CONFIG"] = str(runtime_config)

    workspace = args.workspace or Path(
        tempfile.mkdtemp(prefix="creo-sop-agent-smoke-")
    )
    before = _hash_files(args.cad)
    workflow = PipelineOrchestrator()
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
    if args.plan_only:
        compiled = runtime.execute(
            run_id,
            "compile-render-jobs",
            (f"plans/locked-render-plan-{revision.revision:04d}.json",),
        )
        if compiled.status is not SkillStatus.PASSED:
            raise RuntimeError(f"render compilation failed: {compiled.diagnostics}")
        jobs = runtime.artifacts.read_json(
            core.get_run(run_id).workspace, compiled.artifacts[0].relative_path
        )
        mode_counts: dict[str, int] = {}
        diagnostic_counts: dict[str, int] = {}
        for task in jobs["tasks"]:
            mode = str(task.get("payload", {}).get("execution_mode", ""))
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
            for code in task.get("payload", {}).get("diagnostics", []):
                diagnostic_counts[str(code)] = diagnostic_counts.get(str(code), 0) + 1
        after = _hash_files(args.cad)
        if before != after:
            raise RuntimeError("source CAD hashes changed during planning run")
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "workspace": str(workspace),
                    "execution_modes": mode_counts,
                    "diagnostics": diagnostic_counts,
                    "cad_files": len(before),
                    "cad_unchanged": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.full:
        outcome = core.generate(run_id)
        run_workspace = core.get_run(run_id).workspace
        after = _hash_files(args.cad)
        if before != after:
            raise RuntimeError("source CAD hashes changed during full Agent run")
        render_result = runtime.artifacts.read_json(
            run_workspace, f"results/render-batch-{revision.revision:04d}.json"
        )
        publication = runtime.artifacts.read_json(
            run_workspace, f"results/publication-{revision.revision:04d}.json"
        )
        delivery = Path(publication["delivery_directory"])
        delivery_files = sorted(
            path.relative_to(delivery).as_posix()
            for path in delivery.rglob("*")
            if path.is_file()
        )
        status_counts: dict[str, int] = {}
        for step in outcome.steps:
            status_counts[step.status.value] = status_counts.get(step.status.value, 0) + 1
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "workspace": str(workspace),
                    "run_status": outcome.status.value,
                    "step_statuses": status_counts,
                    "render_metrics": render_result.get("metrics", {}),
                    "rendered_images": len(list((run_workspace / "rendered").glob("*.jpg"))),
                    "arrow_audits": len(list((run_workspace / "rendered").glob("*.arrow.json"))),
                    "delivery": str(delivery),
                    "publication_pending": publication["pending"],
                    "delivery_files": delivery_files,
                    "cad_files": len(before),
                    "cad_unchanged": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    compiled = runtime.execute(
        run_id,
        "compile-render-jobs",
        (f"plans/locked-render-plan-{revision.revision:04d}.json",),
    )
    if compiled.status is not SkillStatus.PASSED:
        raise RuntimeError(f"render compilation failed: {compiled.diagnostics}")
    jobs_path = compiled.artifacts[0].relative_path
    jobs = runtime.artifacts.read_json(core.get_run(run_id).workspace, jobs_path)
    selection_pool = list(jobs["tasks"])
    if args.main_process:
        selection_pool = [
            task
            for task in selection_pool
            if str(task.get("main_process_id")) == args.main_process
        ]
        if not selection_pool:
            raise ValueError(f"unknown main process: {args.main_process}")
    if args.step_id and args.step_ids:
        raise ValueError("--step-id and --step-ids are mutually exclusive")
    if args.step_id or args.step_ids:
        requested_ids = (
            [args.step_id]
            if args.step_id
            else [
                value.strip()
                for value in str(args.step_ids).split(",")
                if value.strip()
            ]
        )
        targets = [
            task
            for task in selection_pool
            if str(task.get("step_id")) in requested_ids
        ]
        found_ids = {str(task.get("step_id")) for task in targets}
        missing_ids = [value for value in requested_ids if value not in found_ids]
        if missing_ids or len(targets) != len(requested_ids):
            raise ValueError(
                "unknown or duplicate step IDs: " + ", ".join(missing_ids)
            )
        target_order = {step_id: index for index, step_id in enumerate(requested_ids)}
        targets.sort(key=lambda task: target_order[str(task.get("step_id"))])
    else:
        targets = (
            _select_scale_spread(selection_pool, args.step_count)
            if args.selection == "scale-spread"
            else _select_targets(selection_pool, args.step_count)
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
                        "zoom_to_selected_level": task["payload"]["presentation"]
                        ["native_selected_fit"]["zoom_to_selected_level"],
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
