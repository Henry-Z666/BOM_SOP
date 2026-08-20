from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from enum import Enum
import json
import os
import sys
from typing import Any

from .core import AgentCore
from .excel_verifier import ExcelComVerifier
from .models import StepResolution
from .pipeline_orchestrator import PipelineOrchestrator
from .sop_publisher import OpenpyxlWorkbookVerifier
from pathlib import Path


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    return value


def execute(workspace: Path, action: str, payload: dict[str, Any]) -> Any:
    allowed_actions = {"start-analysis", "confirm", "generate", "resolve", "resume"}
    if action not in allowed_actions:
        raise ValueError(f"unsupported worker action: {action}")
    experience_mode = os.environ.get("CREO_SOP_EXPERIENCE_MODE") == "1"
    experience_step_limit = _experience_step_limit() if experience_mode else None
    adapters: dict[str, Any] = {
        "workbook_verifier": (
            OpenpyxlWorkbookVerifier() if experience_mode else ExcelComVerifier()
        )
    }
    core = AgentCore(
        workspace,
        PipelineOrchestrator(
            adapters=adapters,
            experience_step_limit=experience_step_limit,
        ),
    )
    if action == "start-analysis":
        run_id = core.create_run(
            Path(payload["bom_file"]), Path(payload["cad_directory"])
        )
        packet = core.analyze(run_id)
        return {"run_id": run_id, "packet": packet}
    run_id = str(payload["run_id"])
    if action == "confirm":
        return core.confirm(run_id, dict(payload.get("answers", {})))
    if action == "generate":
        return core.generate(run_id)
    if action == "resolve":
        resolution = payload["resolution"]
        return core.resolve(
            run_id,
            StepResolution(
                step_id=str(resolution["step_id"]),
                candidate_id=resolution.get("candidate_id"),
                instruction=resolution.get("instruction"),
                action=resolution.get("action"),
                metadata=dict(resolution.get("metadata", {})),
            ),
        )
    if action == "resume":
        return core.resume(run_id)
    raise AssertionError("unreachable worker action")


def _experience_step_limit() -> int | None:
    raw = os.environ.get("CREO_SOP_EXPERIENCE_STEP_LIMIT", "").strip()
    if not raw:
        return None
    value = int(raw)
    if value < 1:
        raise ValueError("CREO_SOP_EXPERIENCE_STEP_LIMIT must be positive")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--request-file", type=Path)
    parser.add_argument("--response-file", type=Path)
    args = parser.parse_args(argv)
    response: dict[str, Any]
    response_file_is_safe = False
    try:
        if bool(args.request_file) != bool(args.response_file):
            raise ValueError("request-file and response-file must be provided together")
        if args.request_file:
            _assert_workspace_file(args.workspace, args.request_file)
            _assert_workspace_file(args.workspace, args.response_file)
            response_file_is_safe = True
            payload = json.loads(args.request_file.read_text(encoding="utf-8"))
        else:
            payload = json.loads(sys.stdin.read() or "{}")
        result = execute(args.workspace, args.action, payload)
        response = {"ok": True, "result": _json_value(result)}
        exit_code = 0
    except Exception as error:
        response = {"ok": False, "error": f"{type(error).__name__}: {error}"}
        exit_code = 1
    encoded = json.dumps(response, ensure_ascii=False)
    if args.response_file and response_file_is_safe:
        temporary = args.response_file.with_suffix(".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(args.response_file)
    elif not args.response_file:
        print(encoded)
    return exit_code


def _assert_workspace_file(workspace: Path, path: Path) -> None:
    root = workspace.resolve()
    resolved = path.resolve()
    if root not in resolved.parents:
        raise ValueError("worker IPC file must stay inside the Agent workspace")


if __name__ == "__main__":
    raise SystemExit(main())
