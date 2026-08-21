from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from sop_pipeline.camera_visibility import (
    VisibilityThresholds,
    audit_camera_visibility_files,
    select_camera_from_visibility_audits,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select one fixed Creo camera from lossless label audits."
    )
    parser.add_argument("--contract", type=Path, required=True)
    for camera_id in ("fixed-123", "fixed-456"):
        parser.add_argument(f"--{camera_id}-isolated", type=Path, required=True)
        parser.add_argument(f"--{camera_id}-staged", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def execute(arguments: list[str] | None = None) -> dict[str, object]:
    args = _parser().parse_args(arguments)
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("schema_version") != "camera-visibility-contract/v1":
        raise ValueError("unsupported camera visibility contract")
    thresholds_payload = contract.get("thresholds")
    if not isinstance(thresholds_payload, dict) or (
        thresholds_payload.get("schema_version")
        != "camera-visibility-thresholds/v1"
    ):
        raise ValueError("camera visibility contract has invalid thresholds")
    thresholds = VisibilityThresholds(
        **{
            key: value
            for key, value in thresholds_payload.items()
            if key != "schema_version"
        }
    )
    moving = tuple(int(value) for value in contract["moving_labels"].values())
    receivers = tuple(
        int(value) for value in contract["receiver_interface_labels"].values()
    )
    audits = []
    for camera_id, isolated, staged in (
        (
            "fixed_123",
            args.fixed_123_isolated,
            args.fixed_123_staged,
        ),
        (
            "fixed_456",
            args.fixed_456_isolated,
            args.fixed_456_staged,
        ),
    ):
        audits.append(
            audit_camera_visibility_files(
                camera_id=camera_id,
                isolated_raster=isolated,
                staged_raster=staged,
                moving_labels=moving,
                receiver_labels=receivers,
                thresholds=thresholds,
            )
        )
    payload = select_camera_from_visibility_audits(audits).to_dict()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    return payload


def main() -> int:
    try:
        result = execute()
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
