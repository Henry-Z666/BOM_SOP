from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any
from uuid import uuid4


SKILL_PROGRESS: dict[str, tuple[int, int, str]] = {
    "intake-preflight": (1, 5, "检查输入与运行环境"),
    "normalize-bom": (5, 12, "解析 BOM 表格、层级与数量"),
    "lock-assembly": (12, 18, "锁定最终总装与版本"),
    "discover-cad": (18, 30, "扫描 CAD 装配关系与约束"),
    "map-bom-cad": (30, 38, "映射 BOM 与 CAD 零部件"),
    "plan-assembly": (38, 45, "规划装配顺序与依赖"),
    "clarify-plan": (45, 50, "整理确认项与确定性默认值"),
    "compile-render-jobs": (50, 55, "编译稳定的出图任务"),
    "render-batch": (55, 88, "Creo 正在生成步骤图片"),
    "validate-repair": (88, 95, "检查结构化硬门与待确认事实"),
    "resolve-step": (50, 55, "应用结构化事实确认并计算重做范围"),
    "publish-delivery": (95, 100, "生成 SOP 与交付目录"),
}


def write_progress(
    run_workspace: Path,
    *,
    run_id: str,
    skill: str,
    state: str,
    skill_status: str | None = None,
    message: str | None = None,
) -> None:
    """Atomically publish a small UI read model without coupling Qt to skills."""

    start, end, label = SKILL_PROGRESS.get(skill, (0, 0, skill))
    target = Path(run_workspace) / "internal" / "progress.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": "pipeline-progress/v1",
        "run_id": run_id,
        "skill": skill,
        "stage": label,
        "state": state,
        "percent": end if state == "COMPLETED" else start,
        "stage_start_percent": start,
        "stage_end_percent": end,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if skill_status:
        payload["skill_status"] = skill_status
    if message:
        payload["message"] = message
    # GUI polling can hold progress.json open without FILE_SHARE_DELETE for a
    # few milliseconds on Windows.  Give every writer its own temporary file
    # and retry only the atomic publish boundary; never rewrite a partial JSON
    # document in place and never retry the surrounding Skill.
    temporary = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    _replace_with_transient_retry(temporary, target)


def _replace_with_transient_retry(
    temporary: Path,
    target: Path,
    *,
    max_attempts: int = 20,
) -> None:
    for attempt in range(max_attempts):
        try:
            temporary.replace(target)
            return
        except PermissionError:
            if attempt + 1 == max_attempts:
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(min(0.01 * (2**attempt), 0.1))
