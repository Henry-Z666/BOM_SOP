"""Archive the actual CDU900 input evidence without changing source bytes."""
from pathlib import Path
import hashlib,json,shutil

ROOT=Path(__file__).resolve().parents[2]
BUNDLE=ROOT/'project-data/cdu900-left'
OLD=ROOT/'outputs/全量最终SOP-20261013'
SOURCE=ROOT/'AI生成SOP外发资料'

def main():
    BUNDLE.mkdir(parents=True,exist_ok=True)
    paths={};files={}
    def add(source,destination):
        source=Path(source).resolve();target=BUNDLE/destination
        target.parent.mkdir(parents=True,exist_ok=True)
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
            shutil.copy2(source,target)
        relative=target.relative_to(BUNDLE).as_posix()
        paths[str(source)]=relative
        files[relative]={'sha256':digest,'bytes':target.stat().st_size}
    for source in sorted(SOURCE.rglob('*')):
        if source.is_file():add(source,Path('sources')/source.relative_to(SOURCE))
    data=json.loads((OLD/'出版排序内容.json').read_text(encoding='utf8'))
    for image in data['images']:
        source=Path(image['image_path']);digest=hashlib.sha256(source.read_bytes()).hexdigest()
        add(source,Path('native-images')/digest[:2]/(digest+source.suffix.lower()))
    index=json.loads((OLD/'工艺原图/图像索引.json').read_text(encoding='utf8'))
    for reference in index:
        for key in ('image_path','pdf_path'):
            if reference.get(key) and Path(reference[key]).exists():
                source=Path(reference[key]);add(source,Path('process-diagrams')/source.name)
    for name in ['出版排序内容.json','出图检查.json','工艺工具来源.json','新总装图谱.json','confirmed-native-bindings.tsv','BOM完整覆盖检查.json']:
        add(OLD/name,Path('analysis')/name)
    add(OLD/'工艺原图/图像索引.json','analysis/工艺图像索引.json')
    add(ROOT/'work/process-mvp-20261008/rebuild/two-images/analysis/process-package.json','analysis/process-package.json')
    add(ROOT/'outputs/部件式装配SOP-20261015/出版内容.json','analysis/部件式出版内容.json')
    product=json.loads((ROOT/'data/products/cdu900-left/product.json').read_text(encoding='utf8'))
    product['source_root']='sources'
    product_path=BUNDLE/'product.json'
    product_path.write_bytes(json.dumps(product,ensure_ascii=False,indent=2).encode('utf8'))
    paths[str((ROOT/'data/products/cdu900-left/product.json').resolve())]='product.json'
    files['product.json']={'sha256':hashlib.sha256(product_path.read_bytes()).hexdigest(),'bytes':product_path.stat().st_size}
    manifest={'schema_version':'publication-input-bundle/v1','product_id':'cdu900-left',
        'path_map':paths,'files':files,'source_assembly_sha256':'bf68d45a1813a5c802ddcbe71fdccdfbc8eb0e5bfdbe561f3b29f6ba5323b7c1'}
    (BUNDLE/'bundle-manifest.json').write_bytes(json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf8'))
    print(json.dumps({'files':len(files),'bytes':sum(f['bytes'] for f in files.values()),'max_file_bytes':max(f['bytes'] for f in files.values())}))

if __name__=='__main__':main()
