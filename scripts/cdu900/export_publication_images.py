"""Create an offline image-only handover ZIP; never uploads it to Git."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from zipfile import ZipFile, ZIP_STORED

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
from sop_pipeline.component_workbook import validate_assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='Directory containing the compiled 出版内容.json')
    parser.add_argument('--zip', required=True, type=Path)
    args = parser.parse_args()
    data = json.loads((args.output/'出版内容.json').read_text(encoding='utf8'))
    gates = json.loads((args.output/'出图检查.json').read_text(encoding='utf8'))
    validate_assets(data, gates)
    files = {}
    for image in data['images']:
        source = Path(image['image_path'])
        digest = sha256(source.read_bytes()).hexdigest()
        # This is the content-addressed path used by bundle-manifest.json.
        files[f'native-images/{digest[:2]}/{digest}.jpg'] = source
    if any(args.zip.resolve() == path.resolve() for path in files.values()):
        raise ValueError('ZIP path must not replace an input image')
    args.zip.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.zip, 'w', compression=ZIP_STORED) as archive:
        for name, source in sorted(files.items()):
            archive.write(source, name)
    print(json.dumps({'zip': str(args.zip.resolve()), 'image_files': len(files),
        'image_tasks': len(data['images']), 'contains_bom_or_cad': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
