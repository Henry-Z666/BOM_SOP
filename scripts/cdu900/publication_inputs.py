"""Resolve archived source paths against the current checkout, never a user home."""
from pathlib import Path
import json,os

ROOT=Path(__file__).resolve().parents[2]
BUNDLE=Path(os.environ.get('CREO_SOP_PUBLICATION_INPUT',str(ROOT/'project-data/cdu900-left'))).resolve()

def output_directory():
    return Path(os.environ.get('CREO_SOP_PUBLICATION_OUT',str(ROOT/'outputs/部件式装配SOP-20261015'))).resolve()

def relocate(value,path_map,bundle=BUNDLE):
    if isinstance(value,dict):return {k:relocate(v,path_map,bundle) for k,v in value.items()}
    if isinstance(value,list):return [relocate(v,path_map,bundle) for v in value]
    if isinstance(value,str) and value.replace('\\','/') in path_map:
        target=(bundle/path_map[value.replace('\\','/')]).resolve()
        if not target.is_relative_to(bundle.resolve()):raise ValueError('Bundle path escapes input directory')
        return str(target)
    return value

def load_input(name):
    manifest_path=BUNDLE/'bundle-manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf8')) if manifest_path.exists() else {'path_map':{}}
    mapping={k.replace('\\','/'):v for k,v in manifest['path_map'].items()}
    return relocate(json.loads((BUNDLE/'analysis'/name).read_text(encoding='utf8')),mapping)
