from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def progress_snapshot(workspace: Path, run_id: str | None = None) -> dict[str, Any]:
    """Read the newest durable progress snapshot for the desktop UI."""

    run_workspace = _run_workspace(Path(workspace), run_id)
    if run_workspace is None:
        return {
            "available": False,
            "percent": 0,
            "stage": "正在创建独立运行区",
            "detail": "正在登记本次 BOM 与 CAD 输入。",
        }
    progress_file = run_workspace / "internal" / "progress.json"
    payload = _read_json(progress_file)
    if payload is None:
        return {
            "available": False,
            "run_id": run_workspace.name,
            "percent": 0,
            "stage": "正在准备任务",
            "detail": "独立运行区已创建，正在启动分析。",
        }
    snapshot = dict(payload)
    snapshot["available"] = True
    snapshot["run_id"] = str(payload.get("run_id", run_workspace.name))
    snapshot["detail"] = str(payload.get("message", payload.get("stage", "")))
    if payload.get("skill") == "render-batch" and payload.get("state") == "RUNNING":
        completed, total = _render_counts(run_workspace)
        start = int(payload.get("stage_start_percent", 55))
        end = int(payload.get("stage_end_percent", 88))
        if total:
            snapshot["percent"] = start + round((end - start) * completed / total)
            snapshot["detail"] = f"Creo 步骤图片：已完成 {completed} / {total}"
            snapshot["completed_tasks"] = completed
            snapshot["total_tasks"] = total
    return snapshot


def review_packet(workspace: Path, run_id: str) -> dict[str, Any]:
    """Build a user-facing review list from versioned Agent artifacts."""

    run_workspace = _run_workspace(Path(workspace), run_id)
    if run_workspace is None:
        return {"run_id": run_id, "items": [], "message": "找不到该任务的运行区。"}
    validation = _latest_json(run_workspace / "results", "validation-*.json") or {}
    candidate_set = _latest_json(run_workspace / "results", "candidate-set-*.json") or {}
    publication = _latest_json(run_workspace / "results", "publication-*.json") or {}
    groups = {
        str(group.get("step_id")): group
        for group in candidate_set.get("groups", [])
        if isinstance(group, dict)
    }
    items: list[dict[str, Any]] = []
    candidate_count = 0
    for step in validation.get("steps", []):
        if not isinstance(step, dict) or step.get("status") == "PASSED":
            continue
        step_id = str(step.get("step_id", ""))
        issues = [str(issue) for issue in step.get("issues", []) if str(issue).strip()]
        group_candidates = groups.get(step_id, {}).get("candidates", [])
        for candidate in group_candidates:
            if not isinstance(candidate, dict):
                continue
            relative_path = str(candidate.get("image_path", ""))
            image_path = run_workspace / relative_path if relative_path else None
            candidate_id = str(candidate.get("candidate_id", ""))
            items.append(
                {
                    "kind": "candidate",
                    "step_id": step_id,
                    "candidate_id": candidate_id,
                    "recommended": bool(candidate.get("recommended", False)),
                    "image_path": str(image_path) if image_path else "",
                    "issues": issues,
                    "label": (
                        f"{step_id} · {candidate_id}"
                        + ("（推荐）" if candidate.get("recommended") else "")
                    ),
                }
            )
            candidate_count += 1
        if group_candidates:
            continue
        relative_path = str(step.get("image_path", ""))
        image_path = run_workspace / relative_path if relative_path else None
        items.append(
            {
                "kind": "placeholder",
                "step_id": step_id,
                "candidate_id": "",
                "recommended": False,
                "image_path": str(image_path) if image_path else "",
                "issues": issues or ["本步骤没有图片通过基础几何硬门，需要按说明重新生成。"],
                "label": f"{step_id} · 待重新生成（占位图）",
            }
        )
    delivery = str(publication.get("delivery_directory", run_workspace / "delivery"))
    if not items:
        message = "没有待处理步骤。"
    elif candidate_count:
        message = "请选择一个步骤图片查看大图。候选图可直接采用；占位步骤请填写修正说明。"
    else:
        message = (
            "本次没有候选图通过基础几何硬门。下方显示的是待重新生成步骤和占位图；"
            "请选择步骤，查看原因后输入普通语言修正说明。"
        )
    return {
        "schema_version": "desktop-review-packet/v1",
        "run_id": run_id,
        "delivery_directory": delivery,
        "candidate_count": candidate_count,
        "items": items,
        "message": message,
    }


def _run_workspace(workspace: Path, run_id: str | None) -> Path | None:
    runs_root = workspace / "runs"
    if run_id:
        candidate = runs_root / run_id
        return candidate if candidate.is_dir() else None
    if not runs_root.is_dir():
        return None
    candidates = [path for path in runs_root.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def _render_counts(run_workspace: Path) -> tuple[int, int]:
    plan_files = sorted((run_workspace / "plans").glob("locked-render-jobs-*.json"))
    total = 0
    if plan_files:
        plan = _read_json(plan_files[-1]) or {}
        total = sum(
            1
            for task in plan.get("tasks", [])
            if task.get("payload", {}).get("execution_mode") == "formal"
        )
    checkpoints = sorted(
        (run_workspace / "internal").glob("render-checkpoint-*.json"),
        key=lambda path: path.stat().st_mtime_ns,
    )
    completed = 0
    if checkpoints:
        checkpoint = _read_json(checkpoints[-1]) or {}
        completed = len(checkpoint.get("steps", []))
    return min(completed, total) if total else completed, total


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _latest_json(directory: Path, pattern: str) -> dict[str, Any] | None:
    paths = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime_ns)
    return _read_json(paths[-1]) if paths else None
