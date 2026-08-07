"""Losslessly re-encode validated SOP JPEG pages as PNG for artifact-tool."""
from pathlib import Path
import json
from PIL import Image

root = Path(__file__).resolve().parents[1]
jobs = json.loads((root / "data/runs/corrected-v2-render-jobs.json").read_text(encoding="utf-8"))["jobs"]
source = root / "outputs/images/jlink/corrected-v3"
target = root / "data/runs/published-sop-build/png"
target.mkdir(parents=True, exist_ok=True)
for job in jobs:
    with Image.open(source / f"{job['job_id']}.jpg") as image:
        image.convert("RGB").save(target / f"{job['job_id']}.png", format="PNG", optimize=True)
print(f"converted {len(jobs)} images to {target}")
