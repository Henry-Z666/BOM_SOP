"""CLI adapter for the reusable V3 deterministic pixel-arrow compositor."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sop_pipeline.pixel_arrow import compose

parser = argparse.ArgumentParser()
parser.add_argument("--base", type=Path, required=True)
parser.add_argument("--calibration", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--expected", type=int, required=True)
args = parser.parse_args()
compose(args.base, args.calibration, args.output, args.expected)
print(f"[PIXEL_V3] composed={args.output}")
