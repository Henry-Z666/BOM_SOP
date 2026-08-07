from __future__ import annotations

import argparse
from pathlib import Path

from .annotation import annotate
from .auto_planner import plan as auto_plan
from .cad_graph import CadGraph
from .creo_adapter import execute
from .discovery import discover
from .io import read_json
from .io import write_json
from .planner import create_pilots
from .publish import publish
from .render_jobs import create_render_jobs
from .validation import validate_contract


def _path(raw: str) -> Path: return Path(raw).resolve()


def main() -> None:
    parser = argparse.ArgumentParser(prog="creo-sop")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan")
    discover_cmd = sub.add_parser("discover"); discover_cmd.add_argument("assembly_file"); discover_cmd.add_argument("output", type=_path)
    auto_cmd = sub.add_parser("auto-plan"); auto_cmd.add_argument("contract", type=_path); auto_cmd.add_argument("cad_graph", type=_path)
    jobs_cmd = sub.add_parser("render-jobs"); jobs_cmd.add_argument("cad_graph", type=_path); jobs_cmd.add_argument("output", type=_path)
    for name in ("validate", "render", "annotate", "publish"):
        command = sub.add_parser(name); command.add_argument("contract", type=_path)
    args = parser.parse_args()
    if args.command == "plan":
        for path in create_pilots(): print(path)
    elif args.command == "discover": print(discover(args.assembly_file, args.output))
    elif args.command == "auto-plan":
        contract = auto_plan(read_json(args.contract), CadGraph.from_json(read_json(args.cad_graph)))
        write_json(args.contract, contract); print(contract["automation"]["phase"])
    elif args.command == "render-jobs": print(create_render_jobs(CadGraph.from_json(read_json(args.cad_graph)), args.output))
    elif args.command == "validate":
        errors = validate_contract(read_json(args.contract), require_render=False)
        print("PASS" if not errors else "BLOCKED\n- " + "\n- ".join(errors))
        if errors: raise SystemExit(2)
    elif args.command == "render": execute(args.contract); print("PASS")
    elif args.command == "annotate": print(annotate(args.contract))
    elif args.command == "publish": print(publish(args.contract))


if __name__ == "__main__": main()
