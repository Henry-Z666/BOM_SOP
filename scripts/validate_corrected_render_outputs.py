"""Validate the complete corrected-v3 image and same-point arrow package."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageStat

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "data" / "runs" / "corrected-v2-render-jobs.json"
IMAGES = ROOT / "outputs" / "images" / "jlink" / "corrected-v3"


def main() -> None:
    jobs = json.loads(JOBS.read_text(encoding="utf-8"))["jobs"]
    failures: list[str] = []
    for job in jobs:
        job_id = job["job_id"]
        image_path = IMAGES / f"{job_id}.jpg"
        audit_path = IMAGES / f"{job_id}.arrow.json"
        if not image_path.exists():
            failures.append(f"{job_id}: missing image")
            continue
        if not audit_path.exists():
            failures.append(f"{job_id}: missing arrow audit")
            continue

        with Image.open(image_path) as image:
            expected_size = (300, 300) if job_id == "12-30-1-12-2" else (1600, 1600)
            if image.size != expected_size:
                failures.append(f"{job_id}: image size {image.size}, expected {expected_size}")
            # Reject empty/near-solid exports such as a clipped Creo window.
            extrema = ImageStat.Stat(image.convert("RGB").resize((64, 64))).extrema
            if max(hi - lo for lo, hi in extrema) < 20:
                failures.append(f"{job_id}: image is blank or near-solid")

        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if audit.get("status") != "passed":
            failures.append(f"{job_id}: arrow status={audit.get('status')}")
        if audit.get("policy") != "same_cad_point/v1":
            failures.append(f"{job_id}: wrong arrow policy")
        covered = {
            occurrence
            for arrow in audit.get("arrows", [])
            for occurrence in arrow.get("covered_occurrences", [])
        }
        expected = set(job["moving_occurrences"])
        if covered != expected:
            failures.append(
                f"{job_id}: arrow coverage mismatch expected={sorted(expected)} got={sorted(covered)}"
            )

    if failures:
        raise SystemExit("OUTPUT VALIDATION FAILED:\n" + "\n".join(failures))
    print(f"OUTPUT VALIDATION PASSED: {len(jobs)} images, arrows and occurrence coverage")


if __name__ == "__main__":
    main()
