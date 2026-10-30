"""One ordinary-Python entry point for reproducing the existing component SOP."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/复现测试')
    parser.add_argument('--input', type=Path, default=ROOT/'project-data/cdu900-left')
    parser.add_argument('--plan-only', action='store_true', help='Only reproduce the 51-page plan; no images needed')
    args = parser.parse_args()
    env = os.environ.copy()
    env['CREO_SOP_PUBLICATION_OUT'] = str(args.output.resolve())
    env['CREO_SOP_PUBLICATION_INPUT'] = str(args.input.resolve())
    env['PYTHONIOENCODING'] = 'utf-8'
    commands = [('plan_component_publication.py', [])]
    if not args.plan_only:
        commands += [('build_component_sop.py', [str(args.output.resolve())]),
                     ('finish-workbook.py', [str(args.output.resolve())])]
    for script, extra in commands:
        result = subprocess.run([sys.executable, str(ROOT/'scripts/cdu900'/script), *extra], cwd=ROOT, env=env)
        if result.returncode:
            raise SystemExit(result.returncode)
    print('PLAN_COMPLETE' if args.plan_only else 'PUBLICATION_COMPLETE', args.output.resolve())


if __name__ == '__main__':
    main()
