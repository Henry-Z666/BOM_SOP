"""Set print-only OOXML metadata, inspect the saved artifact-authored workbook."""
import json,re,zipfile,xml.etree.ElementTree as E,hashlib,math,sys
from pathlib import Path
import openpyxl
from PIL import Image,ImageDraw,ImageFont
root=Path(__file__).resolve().parents[2];out=Path(sys.argv[1]) if len(sys.argv)>1 else root/'outputs/部件式装配SOP-20261015'
content=json.loads((out/'出版内容.json').read_text(encoding='utf8'));pages=content['pages']
p=out/content.get('publication_file','100步阶段装配SOP.xlsx')
gates=json.loads((out/'出图检查.json').read_text(encoding='utf8'))
assert len(gates)==len(content['images']) and all(g['passed'] for g in gates),'Native images must all pass'
ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main';E.register_namespace('',ns);E.register_namespace('r','http://schemas.openxmlformats.org/officeDocument/2006/relationships');tag=lambda s:f'{{{ns}}}{s}'
with zipfile.ZipFile(p) as z:data={n:z.read(n) for n in z.namelist()}
b=E.fromstring(data['xl/workbook.xml']);names=b.find(tag('definedNames'))
if names is None:
 names=E.Element(tag('definedNames'));at=next((i for i,e in enumerate(b) if e.tag in {tag('calcPr'),tag('extLst')}),len(b));b.insert(at,names)
for e in list(names):
 if e.get('name') in {'_xlnm.Print_Area','_xlnm.Print_Titles'}:names.remove(e)
for i,s in enumerate(b.find(tag('sheets'))):E.SubElement(names,tag('definedName'),{'name':'_xlnm.Print_Area','localSheetId':str(i)}).text=f"'{s.get('name')}'!$B$1:$AR$35"
data['xl/workbook.xml']=E.tostring(b,encoding='utf-8',xml_declaration=True)
for name in data:
 if not re.fullmatch(r'xl/worksheets/sheet\d+\.xml',name):continue
 s=E.fromstring(data[name]);props=s.find(tag('sheetPr'))
 if props is None:props=E.Element(tag('sheetPr'));s.insert(0,props)
 fit=props.find(tag('pageSetUpPr'))
 if fit is None:fit=E.SubElement(props,tag('pageSetUpPr'))
 fit.set('fitToPage','1')
 for key in ('printOptions','pageMargins','pageSetup'):
  prior=s.find(tag(key))
  if prior is not None:s.remove(prior)
 at=next((i for i,e in enumerate(s) if e.tag in {tag('headerFooter'),tag('drawing'),tag('legacyDrawing'),tag('extLst')}),len(s))
 settings=[E.Element(tag('printOptions'),{'horizontalCentered':'1','headings':'0','gridLines':'0'}),E.Element(tag('pageMargins'),{'left':'0.15','right':'0.15','top':'0.2','bottom':'0.2','header':'0','footer':'0'}),E.Element(tag('pageSetup'),{'paperSize':'9','orientation':'landscape','fitToWidth':'1','fitToHeight':'1'})]
 for i,e in enumerate(settings):s.insert(at+i,e)
 data[name]=E.tostring(s,encoding='utf-8',xml_declaration=True)
temp=p.with_suffix('.tmp')
with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED) as z:
 for n,v in data.items():z.writestr(n,v)
temp.replace(p)
w=openpyxl.load_workbook(p)
assert len(w.worksheets)==len(pages)
branding_count=1 if content.get('branding') else 0
assert sum(len(s._images) for s in w)==sum(len(pg['images'])+len(pg.get('reference_images',[]))+branding_count for pg in pages)
image_index={e['task_id']:e for e in content['images']}
for s,page in zip(w,pages,strict=True):
 assert len(s._images)==len(page['images'])+len(page.get('reference_images',[]))+branding_count and s.page_setup.orientation=='landscape' and s.page_setup.fitToHeight==1
 from collections import Counter
 expected_files=[Path(image_index[n]['image_path']) for n in page['images']]+[Path(e['image_path']) for e in page.get('reference_images',[])]
 if branding_count:expected_files.append(Path(content['branding']['image_path']))
 assert Counter(hashlib.sha256(im._data()).hexdigest() for im in s._images)==Counter(hashlib.sha256(f.read_bytes()).hexdigest() for f in expected_files),'Embedded image bytes changed'
 assert 'B1:AR35' in str(s.print_area).replace('$','')
 for n,m in enumerate(page['materials'],21):
  assert str(s[f'AJ{n}'].value)==m['material_code'];assert s[f'AR{n}'].value==m['quantity'];assert s[f'AM{n}'].value.startswith(m['name'])
 for r in s:
  for c in r:assert c.data_type!='e',(s.title,c.coordinate,c.value)
 for image in s._images:
  a=image.anchor
  if branding_count and a._from.row==0:continue
  assert a._from.col>=1 and a._from.row>=7
  assert a._from.col<34 and a._from.row<35
record={'operations':content['operation_count'],'pages':len(pages),'native_images':len(content['images']),'source_diagram_pages':sum(len(pg.get('reference_images',[])) for pg in pages),'images_in_workbook':sum(len(s._images) for s in w),'material_occurrences':sum(len(m['occurrence_ids']) for pg in pages for m in pg['materials']),'printing':'A4 landscape, one worksheet per printed page','source_bom':content['source_bom'],'source_assembly':content['source_assembly'],'source_assembly_sha256':hashlib.sha256(Path(content['source_assembly']).read_bytes()).hexdigest(),'workbook_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
assert record['source_assembly_sha256']=='bf68d45a1813a5c802ddcbe71fdccdfbc8eb0e5bfdbe561f3b29f6ba5323b7c1'
(out/'SOP交付检查.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',20)
previews=sorted((out/'SOP页预览').glob('第*页.png'),key=lambda f:int(re.search(r'第(\d+)页',f.name).group(1)))
assert len(previews)==len(pages)
for start in range(0,len(previews),8):
 subset=previews[start:start+8];canvas=Image.new('RGB',(1700,math.ceil(len(subset)/2)*630),'#edf0f3');draw=ImageDraw.Draw(canvas)
 for i,f in enumerate(subset):
  im=Image.open(f).convert('RGB');im.thumbnail((840,595));x=(i%2)*850;y=(i//2)*630;canvas.paste(im,(x,y));draw.text((x+8,y+599),f'第{start+i+1}页',font=font,fill='black')
 canvas.save(out/f'SOP页面总览-{start//8+1}.jpg',quality=95)
print(json.dumps(record,ensure_ascii=False))
