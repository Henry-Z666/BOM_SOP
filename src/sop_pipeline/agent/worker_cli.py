from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from enum import Enum
import json
from pathlib import Path
import sys
from typing import Any

from .core import AgentCore
from .desktop_workflow import DesktopWorkflow
from .models import StepResolution


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
    core = AgentCore(workspace, DesktopWorkflow())
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
            ),
        )
    if action == "resume":
        return core.resume(run_id)
    raise ValueError(f"unsupported worker action: {action}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--action", required=True)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        result = execute(args.workspace, args.action, payload)
        print(json.dumps({"ok": True, "result": _json_value(result)}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {"ok": False, "error": f"{type(error).__name__}: {error}"},
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
