"""Static acceptance checks for the corrected water-tank render batch."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "data" / "runs"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    batch = load(RUNS / "corrected-v2-render-jobs.json")
    jobs = batch["jobs"]
    errors: list[str] = []

    if len(jobs) != 42:
        errors.append(f"expected 42 corrected jobs, got {len(jobs)}")

    for index, job in enumerate(jobs, 1):
        label = f"{index:02d}/{job['bom_level']}"
        moving = set(job["moving_occurrences"])
        receiver = set(job["receiver_occurrences"])
        visible = set(job["visible_occurrences"])
        completed = set(job["stage_visibility"]["completed_occurrences"])
        vector = job["translation"]["vector"]
        if not moving or not receiver:
            errors.append(f"{label}: missing moving or receiver occurrence")
        if not moving | receiver <= visible:
            errors.append(f"{label}: moving/receiver is absent from visible set")
        if not completed <= visible:
            errors.append(f"{label}: completed occurrence was hidden")
        if len(job["visible_occurrences"]) != len(visible):
            errors.append(f"{label}: duplicate visible occurrence")
        nonzero_axes = sum(abs(float(value)) > 1e-9 for value in vector)
        if nonzero_axes != 1:
            errors.append(f"{label}: explosion must be one receiver-normal axis, got {vector}")
        if not job["render"].get("draw_install_arrows"):
            errors.append(f"{label}: arrows disabled")

        camera_path = RUNS / job["camera_contract_file"]
        camera = load(camera_path)
        selected_id = camera["selected"]["id"]
        if selected_id not in {"fixed_123", "fixed_456"}:
            errors.append(f"{label}: non-fixed camera {selected_id}")
        pan = camera["framing"].get("pan")
        if not isinstance(pan, list) or len(pan) != 2:
            errors.append(f"{label}: calibrated staged pan is missing")

    levels = [job["bom_level"] for job in jobs]
    ordering = [
        ("30.4+30.5", "30.2", "top-plate sealing must precede rigid top-plate install"),
        ("30.10", "30.9", "gasket must precede end cap"),
        ("30.9", "30.11", "end cap must precede clamp"),
        ("30.20.3", "30.19", "outflow closeup must precede rigid install"),
        ("30.21.1+30.21.2+30.13d", "30.21", "inlet-pipe closeup must precede rigid install"),
        ("30.22", "30.23", "inlet-pipe gasket must precede end cap"),
        ("30.23", "30.25", "inlet-pipe end cap must precede clamp"),
        ("30.27", "30.24", "U-clamp must precede its two screws"),
    ]
    for first, second, message in ordering:
        if levels.index(first) >= levels.index(second):
            errors.append(message)

    if errors:
        print("STATIC VALIDATION FAILED")
        for error in errors:
            print("-", error)
        return 1
    print("STATIC VALIDATION PASSED: 42 jobs, exact visibility, axis-normal explosion, fixed cameras")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
