from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from .render_scheduler import RenderPlan, RenderTask


SUPPORTED_SCHEMA = "creo-render-jobs/v3"


def compile_creo_render_jobs(contract_file: Path) -> RenderPlan:
    """Compile the legacy Creo job document into the Agent render seam."""

    payload = json.loads(contract_file.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SUPPORTED_SCHEMA:
        raise ValueError(
            f"unsupported Creo render contract: {payload.get('schema_version')}"
        )
    jobs = payload.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("Creo render contract requires a non-empty jobs array")

    tasks: list[RenderTask] = []
    prior_step_id: str | None = None
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("each Creo render job must be an object")
        job_id = _required_text(job, "job_id")
        bom_level = _required_text(job, "bom_level")
        completed = job.get("stage_visibility", {}).get(
            "completed_occurrences", []
        )
        moving = job.get("moving_occurrences", [])
        state_payload = {
            "completed_occurrences": completed,
            "moving_occurrences": moving,
        }
        tasks.append(
            RenderTask(
                task_id=job_id,
                step_id=job_id,
                main_process_id=_main_process_id(bom_level),
                depends_on=(prior_step_id,) if prior_step_id else (),
                complete_state_hash=_canonical_hash(state_payload),
                blocks_dependents_on_failure=False,
                payload={**job, "contract_index": len(tasks)},
            )
        )
        prior_step_id = job_id

    return RenderPlan(schema_version="render-plan/v1", tasks=tuple(tasks))


def _required_text(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Creo render job requires {field_name}")
    return value.strip()


def _main_process_id(bom_level: str) -> str:
    match = re.match(r"\s*(\d+)", bom_level)
    if match is None:
        raise ValueError(f"cannot determine main process from BOM level: {bom_level}")
    return match.group(1)


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"
