from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from sop_pipeline.agent.creo_discovery import powershell_command  # noqa: E402
from sop_pipeline.agent.creo_worker import AgentNativeCreoWorker  # noqa: E402
from sop_pipeline.agent.render_scheduler import (  # noqa: E402
    RenderAttempt,
    RenderPlan,
    RenderTask,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate that adaptive PAN/ZOOM recovery uses one bounded Creo/J-Link "
            "worker generation."
        )
    )
    parser.add_argument("--models-root", type=Path, required=True)
    parser.add_argument("--render-plan", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--max-attempts", type=int, default=3)
    return parser.parse_args()


def _load_task(plan_path: Path, task_id: str) -> tuple[RenderPlan, RenderTask]:
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "render-plan/v2":
        raise ValueError("persistent centering proof requires render-plan/v2")
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("render plan has no tasks")
    tasks = tuple(
        RenderTask(
            task_id=str(item["task_id"]),
            step_id=str(item["step_id"]),
            main_process_id=str(item["main_process_id"]),
            depends_on=tuple(str(value) for value in item.get("depends_on", [])),
            complete_state_hash=str(item["complete_state_hash"]),
            blocks_dependents_on_failure=bool(
                item.get("blocks_dependents_on_failure", False)
            ),
            payload=dict(item["payload"]),
        )
        for item in raw_tasks
    )
    matches = [task for task in tasks if task.task_id == task_id]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one task named {task_id!r}")
    task = matches[0]
    variants = task.payload.get("presentation", {}).get("variants", [])
    if not variants or [float(value) for value in variants[0].get("pan", [])] != [
        0.0,
        0.0,
    ]:
        raise ValueError("proof task must start from PAN=[0,0]")
    return RenderPlan(str(payload["schema_version"]), tasks), task


def _directory_fingerprint(root: Path) -> str:
    digest = sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        file_hash = sha256(path.read_bytes()).digest()
        digest.update(file_hash)
    return "sha256:" + digest.hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _worker_evidence(workspace: Path) -> dict[str, Any]:
    worker_root = workspace / "internal" / "native-worker"
    generations = sorted(worker_root.glob("generation-*"))
    result_markers = sorted(
        path
        for generation in generations
        for path in (generation / "results").glob("*.result")
        if not path.name.startswith("shutdown-")
    )
    log_files = [generation / "native-arrow-worker.log.err" for generation in generations]
    log_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in log_files
        if path.is_file()
    )
    return {
        "worker_generations": len(generations),
        "render_commands": len(result_markers),
        "rendered_frames": log_text.count("[NATIVE_ARROW] start="),
        "worker_current_marker_present": (
            worker_root / "current-worker.tsv"
        ).is_file(),
        "generation_directories": [str(path) for path in generations],
        "result_markers": [str(path) for path in result_markers],
    }


def main() -> int:
    args = _parse_args()
    models_root = args.models_root.resolve()
    plan_path = args.render_plan.resolve()
    workspace = args.workspace.resolve()
    if not models_root.is_dir():
        raise FileNotFoundError(models_root)
    if not plan_path.is_file():
        raise FileNotFoundError(plan_path)
    if workspace.exists() and any(workspace.iterdir()):
        raise ValueError("proof workspace must be new or empty")
    if args.max_attempts < 1 or args.max_attempts > 3:
        raise ValueError("max-attempts must stay within 1..3")
    workspace.mkdir(parents=True, exist_ok=True)

    plan, task = _load_task(plan_path, args.task_id)
    source_before = _directory_fingerprint(models_root)
    worker = AgentNativeCreoWorker(
        powershell=powershell_command(),
        batch_script=PROJECT_ROOT / "creo_java" / "run_agent_native_batch.ps1",
        models_root=models_root,
        render_plan_json=plan_path,
    )
    session = worker.open_session(workspace, plan)
    started = perf_counter()
    attempt = RenderAttempt.retryable("NOT_STARTED")
    attempts = 0
    print(
        f"[PROOF] task={task.task_id} workspace={workspace} PAN=[0,0]",
        flush=True,
    )
    try:
        for attempts in range(1, args.max_attempts + 1):
            print(f"[PROOF] attempt={attempts} starting", flush=True)
            attempt = worker.render(session, task, attempts)
            print(
                f"[PROOF] attempt={attempts} disposition={attempt.disposition} "
                f"error_code={attempt.error_code or '-'}",
                flush=True,
            )
            if attempt.disposition != "retryable":
                break
    finally:
        print("[PROOF] closing persistent Creo worker", flush=True)
        worker.close_session(session)
        print("[PROOF] persistent Creo worker closed", flush=True)
    elapsed_seconds = perf_counter() - started
    source_after = _directory_fingerprint(models_root)
    evidence = _worker_evidence(workspace)
    image_path = workspace / "rendered" / f"{task.task_id}.jpg"
    audit_path = workspace / "rendered" / f"{task.task_id}.arrow.json"
    passed = (
        attempt.disposition == "passed"
        and evidence["worker_generations"] == 1
        and evidence["render_commands"] >= 1
        and evidence["rendered_frames"] >= evidence["render_commands"]
        and not evidence["worker_current_marker_present"]
        and source_before == source_after
        and image_path.is_file()
        and image_path.stat().st_size > 10_000
        and audit_path.is_file()
        and audit_path.stat().st_size > 100
    )
    proof = {
        "schema_version": "persistent-creo-centering-proof/v1",
        "status": "passed" if passed else "failed",
        "task_id": task.task_id,
        "initial_pan": [0.0, 0.0],
        "attempts": attempts,
        "final_attempt": asdict(attempt),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "source_cad_fingerprint_before": source_before,
        "source_cad_fingerprint_after": source_after,
        "image_path": str(image_path),
        "audit_path": str(audit_path),
        **evidence,
    }
    proof_path = workspace / "internal" / "persistent-centering-proof.json"
    _atomic_json(proof_path, proof)
    print(json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
