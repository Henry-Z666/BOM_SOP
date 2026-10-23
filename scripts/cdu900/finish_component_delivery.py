import json,html,hashlib,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/部件式装配SOP-20261015'
d=json.loads((OUT/'出版内容.json').read_text(encoding='utf8'))
check=json.loads((OUT/'SOP交付检查.json').read_text(encoding='utf8'))
scope=json.loads((OUT/'颜色与范围自检.json').read_text(encoding='utf8'))
file=OUT/d['publication_file'];digest=hashlib.sha256(file.read_bytes()).hexdigest()
assert digest==check['workbook_sha256']
check.update(publication_scope=d['publication_scope'],component_work_packages=len(d['component_packages']),atomic_operations_in_this_edition=len({n for p in d['pages'] for n in p['operations']}),supplier_internal_material_rows_published=scope['supplier_internal_material_rows_published'],main_cad_assembly=True)
check['source_atomic_operations']=d['operation_count']
check['operations']=check['atomic_operations_in_this_edition']
if '--reviewed' in sys.argv:
 check['visual_review']={'all_page_layouts_reviewed':True,'pages':len(d['pages']),'method':'All seven page contact sheets plus full-size representative component cards','representative_pages':[1,9,10,11,13,15,19,25,28,30,35,39,40,44,50]}
 check['status']='published'
else:check['status']='exported_and_structurally_checked'
(OUT/'SOP交付检查.json').write_text(json.dumps(check,ensure_ascii=False,indent=2),encoding='utf8')
cards=''.join(f'<section><h2>{p["number"]}　{html.escape(p["component_title"])}</h2><img loading="lazy" src="SOP页预览/第{p["number"]:02d}页.png"></section>' for p in d['pages'])
page=f'''<!doctype html><meta charset="utf-8"><title>CDU900左机部件装配工艺卡</title>
<style>body{{margin:24px;background:#edf0f3;font:16px Microsoft YaHei}}header,section{{max-width:1400px;margin:20px auto;background:white;padding:20px}}img{{width:100%;height:auto}}h2{{font-size:20px}}a{{color:#135b9b}}</style>
<header><h1>CDU900左机部件装配工艺卡</h1><p>40个部件工作包 · 51页 · 主要CAD装配工艺</p><a href="{html.escape(d['publication_file'])}">下载Excel工艺卡</a></header>{cards}'''
(OUT/'部件工艺卡预览.html').write_text(page,encoding='utf8')
print(json.dumps({'status':check['status'],'pages':len(d['pages']),'workbook':str(file),'bytes':file.stat().st_size,'sha256':digest},ensure_ascii=False))
