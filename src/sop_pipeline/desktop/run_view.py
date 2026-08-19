from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sop_pipeline.agent.step_revision import CURRENT_IMAGE_CANDIDATE_ID
from sop_pipeline.agent.review import (
    HUMAN_OVERRIDE_IMAGE_ID,
    prepare_review_step,
)


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
    locked_plan = _latest_json(
        run_workspace / "plans", "locked-render-plan-*.json"
    ) or {}
    plan_steps = {
        str(step.get("step_id")): {
            "step_number": index,
            "title": str(step.get("title", "")).strip(),
            "source_bom_rows": [
                int(row)
                for row in step.get("source_bom_rows", [])
                if isinstance(row, int) or str(row).isdigit()
            ],
        }
        for index, step in enumerate(locked_plan.get("steps", []), start=1)
        if isinstance(step, dict)
    }
    plan_contracts = {
        str(step.get("step_id")): step
        for step in locked_plan.get("steps", [])
        if isinstance(step, dict)
    }
    groups = {
        str(group.get("step_id")): group
        for group in candidate_set.get("groups", [])
        if isinstance(group, dict)
    }
    published_status = {
        str(step.get("step_id")): str(step.get("status"))
        for step in publication.get("steps", [])
        if isinstance(step, dict)
    }
    items: list[dict[str, Any]] = []
    candidate_count = 0
    for validation_index, step in enumerate(validation.get("steps", []), start=1):
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("step_id", ""))
        metadata = plan_steps.get(
            step_id,
            {"step_number": validation_index, "title": "", "source_bom_rows": []},
        )
        step_number = int(metadata["step_number"])
        title = str(metadata["title"])
        source_bom_rows = list(metadata["source_bom_rows"])
        display_name = _review_step_display_name(
            step_number, title, source_bom_rows, step_id
        )
        error_code = str(step.get("error_code", "")).strip()
        error_message = str(step.get("error_message", "")).strip()
        effective_status = published_status.get(
            step_id, str(step.get("status", ""))
        )
        if effective_status == "PASSED":
            continue
        issues = [str(issue) for issue in step.get("issues", []) if str(issue).strip()]
        for failure in step.get("failures", []):
            if not isinstance(failure, dict):
                continue
            message = str(failure.get("message") or failure.get("code") or "").strip()
            action = str(failure.get("suggested_action") or "").strip()
            detail = f"{message} 建议：{action}" if message and action else message
            if detail and detail not in issues:
                issues.append(detail)
        diagnostics = _review_diagnostics(step)
        review_contract = prepare_review_step(
            run_workspace,
            step,
            plan_contracts.get(step_id, {}),
        )
        review_fields = {
            "machine_status": review_contract["machine_status"],
            "machine_category": review_contract["machine_category"],
            "machine_failures": review_contract["machine_failures"],
            "has_real_image": review_contract["has_real_image"],
            "override_allowed": review_contract["override_allowed"],
            "available_actions": review_contract["available_actions"],
            "guided_form": review_contract["guided_form"],
            "attempt_history": review_contract["attempt_history"],
        }
        selected_group = groups.get(step_id, {})
        group_candidates = (
            selected_group.get("candidates", [])
            if selected_group.get("selection_allowed", False)
            else []
        )
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
                    "step_number": step_number,
                    "step_title": title,
                    "source_bom_rows": source_bom_rows,
                    "error_code": error_code,
                    "error_message": error_message,
                    **diagnostics,
                    "candidate_id": candidate_id,
                    "recommended": bool(candidate.get("recommended", False)),
                    "image_path": str(image_path) if image_path else "",
                    **review_fields,
                    "issues": issues,
                    "label": (
                        f"{display_name} · {candidate_id}"
                        + ("（推荐）" if candidate.get("recommended") else "")
                    ),
                }
            )
            candidate_count += 1
        if group_candidates:
            continue
        relative_path = str(step.get("image_path", ""))
        image_path = run_workspace / relative_path if relative_path else None
        acceptance_allowed = bool(review_contract["normal_acceptance_allowed"])
        if acceptance_allowed:
            items.append(
                {
                    "kind": "current",
                    "step_id": step_id,
                    "step_number": step_number,
                    "step_title": title,
                    "source_bom_rows": source_bom_rows,
                    "error_code": error_code,
                    "error_message": error_message,
                    **diagnostics,
                    "candidate_id": CURRENT_IMAGE_CANDIDATE_ID,
                    "recommended": True,
                    "image_path": str(image_path) if image_path else "",
                    **review_fields,
                    "issues": issues or ["图片已通过基础几何硬门，等待人工确认。"],
                    "label": f"{display_name} · 当前图片（可直接采用）",
                }
            )
            candidate_count += 1
            continue
        if bool(review_contract["override_allowed"]):
            override_path = str(review_contract["image_path"])
            items.append(
                {
                    "kind": "failed_image",
                    "step_id": step_id,
                    "step_number": step_number,
                    "step_title": title,
                    "source_bom_rows": source_bom_rows,
                    "error_code": error_code,
                    "error_message": error_message,
                    **diagnostics,
                    "candidate_id": HUMAN_OVERRIDE_IMAGE_ID,
                    "recommended": False,
                    "image_path": override_path,
                    **review_fields,
                    "issues": issues
                    or ["机器校验未通过；可修正重生成，或在确认风险后采用此原图。"],
                    "label": f"{display_name} · 机器未通过（可人工审核原图）",
                }
            )
            candidate_count += 1
            continue
        retained_relative = str(step.get("retained_image") or "")
        retained_path = (
            run_workspace / retained_relative if retained_relative else image_path
        )
        if (
            retained_path is not None
            and retained_path.is_file()
            and str(step.get("category") or "") == "system_retry"
        ):
            items.append(
                {
                    "kind": "retained",
                    "step_id": step_id,
                    "step_number": step_number,
                    "step_title": title,
                    "source_bom_rows": source_bom_rows,
                    "error_code": error_code,
                    "error_message": error_message,
                    **diagnostics,
                    "candidate_id": "",
                    "recommended": False,
                    "image_path": str(retained_path),
                    "issues": issues
                    or ["本次重渲染失败，已保留上一张有效图片并等待重试。"],
                    "label": f"{display_name} · 已回退并保留上一张图片",
                }
            )
            continue
        items.append(
            {
                "kind": "placeholder",
                "step_id": step_id,
                "step_number": step_number,
                "step_title": title,
                "source_bom_rows": source_bom_rows,
                "error_code": error_code,
                "error_message": error_message,
                **diagnostics,
                "candidate_id": "",
                "recommended": False,
                "image_path": str(image_path) if image_path else "",
                **review_fields,
                "issues": issues or ["本步骤没有可交付图片，需要按说明修正后重新生成。"],
                "label": f"{display_name} · 待重新生成（占位图）",
            }
        )
    delivery = str(publication.get("delivery_directory", run_workspace / "delivery"))
    if not items:
        message = "没有待处理步骤。"
    elif candidate_count:
        message = (
            "请选择步骤查看完整证据。合格候选图可直接采用；机器未通过但已生成的"
            "原图可在确认风险后人工采用，或填写关键信息重新生成。"
        )
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


def _review_diagnostics(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "primary_code": str(
            step.get("primary_code") or step.get("error_code") or ""
        ),
        "failures": [
            dict(value)
            for value in step.get("failures", [])
            if isinstance(value, dict)
        ],
        "category": str(step.get("category") or ""),
        "expected": step.get("expected"),
        "actual": step.get("actual"),
        "attempted_actions": [
            str(value)
            for value in step.get("attempted_actions", [])
            if str(value).strip()
        ],
        "suggested_actions": [
            str(value)
            for value in step.get("suggested_actions", [])
            if str(value).strip()
        ],
        "retained_image": str(step.get("retained_image") or ""),
    }


def _review_step_display_name(
    step_number: int,
    title: str,
    source_bom_rows: list[int],
    step_id: str,
) -> str:
    parts = [f"第 {step_number} 步"]
    if source_bom_rows:
        rows = "、".join(str(row) for row in source_bom_rows)
        parts.append(f"BOM 第 {rows} 行")
    if title:
        parts.append(title)
    elif step_id:
        parts.append(step_id)
    return " · ".join(parts)


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
    _path, payload = _latest_json_artifact(directory, pattern)
    return payload


def _latest_json_artifact(
    directory: Path,
    pattern: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    paths = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime_ns)
    if not paths:
        return None, None
    path = paths[-1]
    return path, _read_json(path)
