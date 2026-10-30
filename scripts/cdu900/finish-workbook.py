"""Independently verify an openpyxl-authored component workbook."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from sop_pipeline.component_workbook import validate_assets, verify_workbook


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--verify-sources', action='store_true', help='Also require the original BOM and root ASM')
    args = parser.parse_args()
    output = args.output.resolve()
    data = json.loads((output / '出版内容.json').read_text(encoding='utf8'))
    gates = json.loads((output / '出图检查.json').read_text(encoding='utf8'))
    validate_assets(data, gates)
    report = verify_workbook(output / data['publication_file'], data)
    report['sources_verified'] = False
    if args.verify_sources:
        expected_assembly = data.get('source_assembly_sha256')
        if not expected_assembly and data['images']:
            expected_assembly = data['images'][0]['payload']['authoritative_assembly']['sha256']
        for key, expected in [('source_bom', data['source_bom_sha256']),
                              ('source_assembly', expected_assembly)]:
            if not expected or sha256(Path(data[key]).read_bytes()).hexdigest() != expected.removeprefix('sha256:'):
                raise ValueError('Source hash mismatch: ' + key)
        report['sources_verified'] = True
    (output / 'SOP交付检查.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
