"""Independent scope, color and image-byte review for the component edition."""
import json,hashlib,math,re
from pathlib import Path
from collections import Counter
from PIL import Image,ImageDraw,ImageFont
from publication_inputs import output_directory
ROOT=Path(__file__).resolve().parents[2];OUT=output_directory()
d=json.loads((OUT/'出版内容.json').read_text(encoding='utf8'))
assert hashlib.sha256(Path(d['source_bom']).read_bytes()).hexdigest()==d['source_bom_sha256'].removeprefix('sha256:')
colors=d['color_boundaries']['rows'];used=[m for p in d['pages'] for m in p['materials']]
assert not [(m['row'],m['name']) for m in used if colors.get(str(m['row']),{}).get('role')=='internal'],'Supplied-unit internals leaked into cards'
for m in used:
 assert bool(m.get('assembly_boundary'))==(colors.get(str(m['row']),{}).get('role')=='issued_unit'),(m['row'],m['name'])
assert not any(p.get('reference_images') for p in d['pages'])
assert not any(re.search('检漏|检验|氦检|保压|打包|贴标|安规调试|现场照片',p['component_title']) for p in d['pages'])
assert not any(re.match('安装.*(?:螺钉|螺母|垫圈)$',p['component_title']) for p in d['pages'])
gates={g['task_id']:g for g in json.loads((OUT/'出图检查.json').read_text(encoding='utf8'))}
idx={e['task_id']:e for e in d['images']}
for k,e in idx.items():
 assert gates[k]['passed']
 actual=hashlib.sha256(Path(e['image_path']).read_bytes()).hexdigest()
 assert actual==gates[k]['image_sha256'].removeprefix('sha256:'),(k,'image changed')
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',18)
for start in range(0,len(d['pages']),12):
 subset=d['pages'][start:start+12];canvas=Image.new('RGB',(1260,math.ceil(len(subset)/3)*450),'white');draw=ImageDraw.Draw(canvas)
 for j,p in enumerate(subset):
  x=j%3*420;y=j//3*450
  if p['images']:
   im=Image.open(idx[p['images'][0]]['image_path']).convert('RGB');im.thumbnail((410,400));canvas.paste(im,(x,y))
  draw.text((x+4,y+405),f"{p['number']} {p['component_title']}",font=font,fill='black')
 canvas.save(OUT/f'主图总览-{start//12+1}.jpg',quality=93)
audit={'pages':len(d['pages']),'component_packages':len(d['component_packages']),'published_native_images':len(idx),'source_images_unchanged':True,'supplier_internal_material_rows_published':0,'yellow_material_entries':sum(bool(m.get('assembly_boundary')) for m in used),'photo_label_inspection_pages_published':0}
(OUT/'颜色与范围自检.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(audit,ensure_ascii=False))
