from __future__ import annotations

import argparse
from itertools import product
import json
import math
from pathlib import Path

from sop_pipeline.camera_planner import select_fixed_camera_for_stage


def _translated(bounds: dict, vector: list[float]) -> dict:
    return {
        key: [float(bounds[key][index]) + vector[index] for index in range(3)]
        for key in ("min", "max")
    }


def _centre(bounds: dict) -> list[float]:
    return [
        (float(bounds["min"][index]) + float(bounds["max"][index])) / 2.0
        for index in range(3)
    ]


def _union(items: list[dict]) -> dict:
    return {
        "min": [min(item["min"][index] for item in items) for index in range(3)],
        "max": [max(item["max"][index] for item in items) for index in range(3)],
    }


def _overlap_volume(left: dict, right: dict) -> float:
    spans = [
        max(
            0.0,
            min(left["max"][index], right["max"][index])
            - max(left["min"][index], right["min"][index]),
        )
        for index in range(3)
    ]
    return math.prod(spans)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("locked_plan", type=Path)
    parser.add_argument("occurrences", nargs="+")
    args = parser.parse_args()
    plan = json.loads(args.locked_plan.read_text(encoding="utf-8"))
    bounds = plan["occurrence_bounds_root"]
    distance = max(
        math.sqrt(sum(float(value) ** 2 for value in step["translation_vector_root"]))
        for step in plan["steps"]
        if step.get("translation_vector_root")
    )
    for occurrence in args.occurrences:
        step = next(
            item
            for item in plan["steps"]
            if occurrence in item["moving_occurrences"]
        )
        moving = bounds[occurrence]
        context = [
            bounds[item]
            for item in step["visible_occurrences"]
            if item not in step["moving_occurrences"] and item in bounds
        ]
        context_union = _union(context)
        moving_centre = _centre(moving)
        context_centre = _centre(context_union)
        outward = [
            moving_centre[index] - context_centre[index] for index in range(3)
        ]
        print(f"occurrence={occurrence} step={step['step_id']} outward={outward}")
        for axis, sign in product(range(3), (-1.0, 1.0)):
            vector = [0.0, 0.0, 0.0]
            vector[axis] = sign * distance
            exploded = _translated(moving, vector)
            overlaps = [
                _overlap_volume(exploded, item) for item in context
            ]
            camera = select_fixed_camera_for_stage(
                plan["camera_basis"],
                vector,
                vector,
                [moving],
                context,
            )
            print(
                json.dumps(
                    {
                        "axis": "XYZ"[axis],
                        "sign": int(sign),
                        "overlap_count": sum(value > 1.0e-6 for value in overlaps),
                        "overlap_volume": round(sum(overlaps), 3),
                        "outward_score": round(sign * outward[axis], 3),
                        "camera": camera["id"],
                        "camera_occlusion": camera["metrics"][
                            "analytic_activity_occlusion"
                        ],
                    },
                    ensure_ascii=False,
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
