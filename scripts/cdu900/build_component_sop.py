"""Build the existing component-card edition with ordinary Python packages."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from sop_pipeline.component_workbook import build_component_workbook


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--update-pages', help='Comma-separated existing page numbers to replace')
    parser.add_argument('--preview', help='Render selected pages of an existing XLSX using local Excel or LibreOffice')
    parser.add_argument('--preview-backend', choices=['auto', 'excel', 'libreoffice'], default='auto')
    args = parser.parse_args()
    if args.preview:
        from render_component_preview import render_preview
        render_preview(args.output.resolve(), [int(n) for n in args.preview.split(',')], args.preview_backend)
        return
    path = build_component_workbook(args.output.resolve(), ROOT / 'assets/publication',
        [int(n) for n in args.update_pages.split(',')] if args.update_pages else None)
    print('FINAL_WORKBOOK_SAVED ' + str(path))


if __name__ == '__main__':
    main()
