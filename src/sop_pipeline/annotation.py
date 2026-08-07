"""Deterministic SVG annotation, separated from the native Creo image."""
from __future__ import annotations

import html
from pathlib import Path

from .io import read_json, write_json
from .paths import OUTPUTS
from .validation import validate_contract


def annotate(contract_path: Path) -> Path:
    contract = read_json(contract_path)
    errors = validate_contract(contract, require_render=True)
    if errors:
        raise ValueError("标注被阻断：" + "；".join(errors))
    render = contract["render"]
    start = render["projection"]["moving_point_exploded"]
    end = render["projection"]["moving_point_complete"]
    label = html.escape(contract["method"].get("text") or contract["title"])
    base = html.escape(str(render["exploded_image"]))
    output = OUTPUTS / "annotations" / f"{contract['step_id']}.svg"
    output.parent.mkdir(parents=True, exist_ok=True)
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1600" height="900" viewBox="0 0 1600 900">
<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#D92D20"/></marker></defs>
<image xlink:href="{base}" width="1600" height="900" preserveAspectRatio="xMidYMid meet"/>
<line x1="{start[0]}" y1="{start[1]}" x2="{end[0]}" y2="{end[1]}" stroke="#D92D20" stroke-width="6" marker-end="url(#arrow)"/>
<rect x="50" y="50" width="520" height="120" rx="12" fill="white" stroke="#D92D20" stroke-width="3"/><text x="80" y="105" font-family="Microsoft YaHei, sans-serif" font-size="30" fill="#1D2939">{label}</text>
</svg>'''
    output.write_text(svg, encoding="utf-8")
    contract["annotation"] = {"file": str(output), "arrow": {"from": start, "to": end}, "status": "passed"}
    contract["automation"]["phase"] = "verified"
    write_json(contract_path, contract)
    return output
