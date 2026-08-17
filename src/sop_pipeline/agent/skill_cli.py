from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from .artifacts import ArtifactStore
from .excel_verifier import ExcelComVerifier
from .skill_handlers import default_skill_handlers
from .skill_runtime import SkillRuntime
from .store import RunStore


def execute(
    workspace: Path,
    run_id: str,
    skill: str,
    input_refs: tuple[str, ...],
    parameters: dict[str, Any],
    *,
    use_excel_com: bool = False,
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    store = RunStore(root / "agent.sqlite3")
    adapters = (
        {"workbook_verifier": ExcelComVerifier()} if use_excel_com else {}
    )
    runtime = SkillRuntime(
        store,
        ArtifactStore(store),
        default_skill_handlers(),
        adapters=adapters,
    )
    result = runtime.execute(run_id, skill, input_refs, parameters)
    payload = asdict(result)
    payload["status"] = result.status.value
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one registered SOP Agent skill inside an existing run."
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--skill", required=True)
    parser.add_argument("--input-ref", action="append", default=[])
    parser.add_argument("--parameters", default="{}")
    parser.add_argument(
        "--excel-com",
        action="store_true",
        help="Enable the Windows Excel COM verifier for publication skills.",
    )
    args = parser.parse_args(argv)
    try:
        parameters = json.loads(args.parameters)
        if not isinstance(parameters, dict):
            raise ValueError("--parameters must be a JSON object")
        payload = execute(
            args.workspace,
            args.run_id,
            args.skill,
            tuple(args.input_ref),
            parameters,
            use_excel_com=args.excel_com,
        )
        print(json.dumps(payload, ensure_ascii=False, default=str))
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
