import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {FileBlob,SpreadsheetFile} from '@oai/artifact-tool';
const root=fileURLToPath(new URL('../../',import.meta.url)).replaceAll('\\','/').replace(/\/$/,'');
const out=process.argv[2]||`${root}/outputs/部件式装配SOP-20261015`;
const meta=JSON.parse(await fs.readFile(`${root}/assets/publication/template-meta.json`,'utf8'));
const data=JSON.parse(await fs.readFile(`${out}/出版内容.json`,'utf8'));
const componentMode=data.publication_mode==='component_cards/v1';
const gates=JSON.parse(await fs.readFile(`${out}/出图检查.json`,'utf8'));
const totalPages=data.pages.length;
const previewArg=process.argv.find(a=>a.startsWith('--preview='));
const updateArg=process.argv.find(a=>a.startsWith('--update-pages='));
if(previewArg){const wanted=new Set(previewArg.slice(10).split(',').map(Number));data.pages=data.pages.filter(p=>wanted.has(p.number));}
if(updateArg){const wanted=new Set(updateArg.slice(15).split(',').map(Number));data.pages=data.pages.filter(p=>wanted.has(p.number));}
const usedImages=new Set(data.pages.flatMap(p=>p.images));
const builderHash=createHash('sha256').update(await fs.readFile(new URL(import.meta.url))).digest('hex');
let previewCache={};try{previewCache=JSON.parse(await fs.readFile(`${out}/SOP页预览/渲染索引.json`,'utf8'));}catch(error){if(error.code!=='ENOENT')throw error;}
const gateById=new Map(gates.map(g=>[g.task_id,g]));
if(previewArg){if([...usedImages].some(id=>!gateById.get(id)?.passed))throw Error('Preview native images must pass');}
else if(gates.length!==data.images.length||gates.some(g=>!g.passed))throw Error('All native images must pass before publication');
let heightOverrides={};try{heightOverrides=JSON.parse(await fs.readFile(`${out}/表格行高修正.json`,'utf8'));}catch(error){if(error.code!=='ENOENT')throw error;}
const images=new Map(data.images.map(i=>[i.task_id,i]));
const brandingBytes=data.branding?await fs.readFile(data.branding.image_path):null;
const operations=new Map(data.operations.map(o=>[o.number,o]));
function materialText(m){
 const unit=m.unit&&!['个','件','台','套','只'].includes(m.unit)?`（单位：${m.unit}）`:'';
 return m.name+unit+'\n'+(m.model||m.drawing_no);
}
function estimatedLines(text,capacity=28){return text.split('\n').reduce((n,line)=>n+Math.max(1,Math.ceil([...line].reduce((w,c)=>w+(/[^\u0000-\u00ff]/.test(c)?2:1),0)/capacity)),0);}
function caption(e){
 const p=e.payload,op=operations.get(e.operation),moving=new Set(p.moving_occurrences);
 if(e.method_reference)return `步骤${e.operation}：同型号连接方法示例\n实际安装位置及数量见本工序完整定位图和物料表`;
 if(op.native_coverage_key?.includes('共22组'))return `步骤${e.operation}：${op.title}\n图示1组；整机配套22组，用量见物料表`;
 const material=op.materials.find(m=>m.occurrence_ids.some(o=>moving.has(o)));
 if(!material)return e.caption;
 if(p.completed_stage_only||p.image_coverage?.role==='location')return `步骤${e.operation}：${material.name}\n${material.drawing_no} · 整体定位`;
 const coverage=p.image_coverage||{},count=(coverage.operation_occurrences||p.moving_occurrences).length;
 const index=coverage.detail_group_index||Number(e.task_id.match(/-(\d+)$/)?.[1]||1);
 const spec=(material.model||'').match(/M\s*\d+\s*[Xx×*]\s*\d+/i)?.[0].replace(/\s/g,'').replace(/[Xx*]/,'×')||'';
 return `步骤${e.operation} · 位置${index}${spec?' · '+spec:''}\n${material.name}：共${count}件${count>p.moving_occurrences.length?'，示意1件':''}${p.context_cutaway?'；局部连接示意（遮挡外壁隐藏）':''}`;
}
const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(updateArg?`${out}/${data.publication_file}`:`${root}/assets/publication/component-sop-template.xlsx`));
const template=wb.worksheets.getItemAt(0),sheets=updateArg?data.pages.map(p=>wb.worksheets.getItemAt(p.number-1)):[template];
if(updateArg)for(const s of sheets){s.unmergeCells('A1:AR35');for(const m of meta.merges)s.mergeCells(m);}
for(let i=1;!updateArg&&i<data.pages.length;i++){
 const page=data.pages[i];
 const s=wb.worksheets.add(componentMode?`${String(page.number).padStart(3,'0')} ${page.component_title}`.slice(0,31):`阶段装配${String(i+1).padStart(2,'0')}`);
 s.getRange('A1:AR35').copyFrom(template.getRange('A1:AR35'),'all');s.unmergeCells('A1:AR35');
 for(const m of meta.merges)s.mergeCells(m);sheets.push(s);
}
function col(n){let t='';for(;n;n=Math.floor((n-1)/26))t=String.fromCharCode(65+(n-1)%26)+t;return t;}
const widths=Array(44).fill(meta.defaultColWidth||3.5);
for(const d of Object.values(meta.cols))for(let n=d.min;n<=Math.min(44,d.max);n++)widths[n-1]=d.width;
const px=widths.map(w=>Math.floor(w*7+5));let rh=Array.from({length:35},(_,i)=>(meta.rows[String(i+1)]||meta.defaultRowHeight)*4/3);
const zoneW=px.slice(1,34).reduce((a,b)=>a+b,0);let zoneH=rh.slice(7).reduce((a,b)=>a+b,0);
function anchor(x,y){let c=1,r=7;while(c<33&&x>=px[c]){x-=px[c];c++;}while(r<34&&y>=rh[r]){y-=rh[r];r++;}return {row:r,col:c,rowOffsetPx:y,colOffsetPx:x};}
await fs.mkdir(`${out}/SOP页预览`,{recursive:true});
for(let i=0;i<data.pages.length;i++){
 const page=data.pages[i],s=sheets[i],set=(a,v)=>s.getRange(a).values=[[v]];
 s.showGridLines=false;s.deleteAllDrawings();s.mergeCells('AM21:AQ21');
 if(brandingBytes){const headerW=px.slice(1,9).reduce((a,b)=>a+b,0),headerH=rh.slice(0,3).reduce((a,b)=>a+b,0),width=Math.min(headerW-24,headerH*1043/292-12),height=width*292/1043;s.images.add({dataUrl:`data:image/png;base64,${brandingBytes.toString('base64')}`,anchor:{from:{row:0,col:1,rowOffsetPx:(headerH-height)/2,colOffsetPx:(headerW-width)/2},extent:{widthPx:width,heightPx:height}}});}
 for(let c=1;c<=44;c++)s.getRange(`${col(c)}1:${col(c)}35`).format.columnWidthPx=px[c-1];
 rh=Array.from({length:35},(_,r)=>{
  let points=meta.rows[String(r+1)]||meta.defaultRowHeight;
  if(r>=7&&r<7+page.instructions.length)points=Math.max(points,estimatedLines(page.instructions[r-7],52)*12+6);
  if(r===7||r===21)points=Math.max(points,28);
  if(componentMode&&r===4)points=Math.max(points,36);
  if(componentMode&&r>=30)points=Math.max(points,23);
  if(r>=20&&r<20+page.materials.length)points=Math.max(points,heightOverrides[String(page.number)]?.[String(r+1)]||0,27,estimatedLines(materialText(page.materials[r-20]))*12+6);
  return points*4/3;
 });
 for(let r=1;r<=35;r++)s.getRange(`A${r}:AR${r}`).format.rowHeight=rh[r-1]*3/4;
 zoneH=rh.slice(7).reduce((a,b)=>a+b,0);
 set('J1',data.publication_title||'项目名称：CDU900左机阶段装配');set('B4',`文件编号：${data.publication_code||'SOP-STAGE-100'} / ${page.number}–${totalPages}`);
 set('M4','CDU900-1250L-S-D7L');set('V4','');set('AF1',data.publication_date||'2026-10-14');set('AN1','V5');set('AN3','05');set('AN4',page.operations.map(n=>String(n).padStart(2,'0')).join(' / '));set('AN5',page.chapter);set('AN6','');
 if(componentMode){set('AN1','C1');set('AN3','01');set('AN4',`ZZ.${page.package_number}-${page.part}`);set('AN5',page.component_title);s.getRange('AN5:AR5').format.wrapText=true;s.getRange('AN5:AR5').format.font={name:'Microsoft YaHei',size:9};set('B7',`${page.component_title}${page.section?' · '+page.section:''}${page.part_count>1?`（${page.part}/${page.part_count}）`:''}`);s.getRange('B4:H6').format.font={name:'Microsoft YaHei',size:9};}
 for(const a of ['AF2','AF3','AF4','AF5','AF6'])set(a,'');
 s.getRange('J1:AA3').format.font={name:'Microsoft YaHei',size:14,bold:true};s.getRange('M4:AA6').format.font={name:'Microsoft YaHei',size:9};
 for(let n=8;n<=18;n++){set(`AI${n}`,n-7);set(`AJ${n}`,null);}
 page.instructions.forEach((t,n)=>set(`AJ${8+n}`,t));
 s.getRange('AJ8:AR18').format.font={name:'Microsoft YaHei',size:9,color:'#16202A'};s.getRange('AJ8:AR18').format.wrapText=true;
 for(let n=21;n<=29;n++)for(const c of ['AI','AJ','AM','AR'])set(`${c}${n}`,null);
 page.materials.forEach((m,n)=>{
  const rr=21+n;set(`AI${rr}`,n+1);set(`AJ${rr}`,m.material_code);set(`AM${rr}`,materialText(m));set(`AR${rr}`,m.quantity);
 });
 s.getRange('AJ21:AL29').format.numberFormat='0';s.getRange('AJ21:AQ29').format.font={name:'Microsoft YaHei',size:9};s.getRange('AM21:AQ29').format.wrapText=true;
 if(componentMode)page.materials.forEach((m,n)=>{if(m.assembly_boundary)s.getRange(`AM${21+n}:AQ${21+n}`).format.fill='#FFFF99';});
 for(let n=32;n<=35;n++)for(const c of ['AI','AJ','AM','AR'])set(`${c}${n}`,null);
 (page.tools||[]).forEach((tool,n)=>{if(n>=4)throw Error('Tool table overflow');set(`AI${32+n}`,n+1);set(`AJ${32+n}`,tool.name);set(`AM${32+n}`,tool.spec||'');set(`AR${32+n}`,tool.quantity||'');});
 if(componentMode&&!page.reference_images?.length){
  // The publication unit is a component card, with a dominant position view
  // and subordinate connection views. Atomic CAD operation IDs stay internal.
  const figureH=rh.slice(7,30).reduce((a,b)=>a+b,0);
  const count=page.images.length;
  const textStyle={font:{name:'Microsoft YaHei',size:10,bold:true,color:'#243442'},horizontalAlignment:'center',verticalAlignment:'center',wrapText:true};
  const insert=(id,x,y,w,h)=>{
   const e=images.get(id);const bytesPromise=fs.readFile(e.image_path);
   return bytesPromise.then(bytes=>s.images.add({dataUrl:`data:image/jpeg;base64,${bytes.toString('base64')}`,anchor:{from:anchor(x,y),extent:{widthPx:w,heightPx:h}}}));
  };
  const cap=(range,id)=>{s.mergeCells(range);s.getRange(range).values=[[page.captions[id]]];s.getRange(range).format=textStyle;};
  if(count===3){
   const leftW=px.slice(1,22).reduce((a,b)=>a+b,0),rightW=zoneW-leftW;
   const splitY=rh.slice(7,19).reduce((a,b)=>a+b,0);
   cap('B8:V8',page.images[0]);cap('W8:AH8',page.images[1]);cap('W20:AH20',page.images[2]);
   const mainSize=Math.min(leftW-18,figureH-rh[7]-16);
   await insert(page.images[0],(leftW-mainSize)/2,rh[7]+(figureH-rh[7]-mainSize)/2,mainSize,mainSize);
   for(let j=1;j<3;j++){
    const top=j===1?0:splitY,bandH=j===1?splitY:figureH-splitY,headH=j===1?rh[7]:rh[19];
    const size=Math.min(rightW-18,bandH-headH-10);
    await insert(page.images[j],leftW+(rightW-size)/2,top+headH+(bandH-headH-size)/2,size,size);
   }
  }else if(count){
   const slotW=zoneW/count,size=Math.min(slotW-18,figureH-rh[7]-16);
   for(let j=0;j<count;j++){
    cap(count===1?'B8:AH8':j===0?'B8:Q8':'R8:AH8',page.images[j]);
    await insert(page.images[j],j*slotW+(slotW-size)/2,rh[7]+(figureH-rh[7]-size)/2,size,size);
   }
  }
  const actions=page.actions||[];
  const ranges=actions.length===3?['B31:L35','M31:W35','X31:AH35']:actions.length===2?['B31:Q35','R31:AH35']:['B31:AH35'];
  actions.forEach((t,j)=>{s.mergeCells(ranges[j]);s.getRange(ranges[j]).values=[[`${j+1}　${t}`]];s.getRange(ranges[j]).format={fill:'#FFF0B3',font:{name:'Microsoft YaHei',size:11,bold:true,color:'#382B13'},verticalAlignment:'center',wrapText:true};});
  if(!count&&!actions.length){s.mergeCells('B10:AH27');s.getRange('B10:AH27').values=[[page.instructions.join('\n\n')]];s.getRange('B10:AH27').format={font:{name:'Microsoft YaHei',size:13,color:'#16202A'},verticalAlignment:'center',wrapText:true};}
  continue;
 }
 if(page.images.length===0&&!page.reference_images?.length){s.mergeCells('B10:AH16');s.getRange('B10:AH16').values=[[page.process_only_text||page.instructions.join('\n')]];s.getRange('B10:AH16').format={font:{name:'Microsoft YaHei',size:12,color:'#16202A'},wrapText:true,verticalAlignment:'center'};}
 if(page.reference_images?.length){
  if(page.reference_images.length!==1||page.images.length)throw Error('Source diagram layout is ambiguous');
  const reference=page.reference_images[0],bytes=await fs.readFile(reference.image_path);
  const pngW=bytes.readUInt32BE(16),pngH=bytes.readUInt32BE(20),scale=Math.min((zoneW-16)/pngW,(zoneH-20)/pngH);
  const width=pngW*scale,height=pngH*scale;
  s.images.add({dataUrl:`data:image/png;base64,${bytes.toString('base64')}`,anchor:{from:anchor((zoneW-width)/2,(zoneH-height)/2),extent:{widthPx:width,heightPx:height}}});
 }
 const columns=page.images.length===1?1:2,rows=page.images.length<=2?1:2;
 const slotW=zoneW/columns;
 const topBandHeight=rh.slice(7,21).reduce((a,b)=>a+b,0);
 const bands=rows===1?[{y:0,height:zoneH,captionHeight:rh[7]}]:[{y:0,height:topBandHeight,captionHeight:rh[7]},{y:topBandHeight,height:zoneH-topBandHeight,captionHeight:rh[21]}];
 const size=Math.floor(Math.min(slotW-16,...bands.map(b=>b.height-b.captionHeight-12)));
 for(let n=0;n<page.images.length;n++){
  const e=images.get(page.images[n]),rr=Math.floor(n/columns),cc=n%columns,bytes=await fs.readFile(e.image_path);
  const band=bands[rr];
  const x=cc*slotW+(slotW-size)/2,y=band.y+band.captionHeight+(band.height-band.captionHeight-size)/2;
  s.images.add({dataUrl:`data:image/jpeg;base64,${bytes.toString('base64')}`,anchor:{from:anchor(x,y),extent:{widthPx:size,heightPx:size}}});
  const cr=rr===0?8:22,range=columns===1?`B${cr}:AH${cr}`:cc===0?`B${cr}:Q${cr}`:`R${cr}:AH${cr}`;
  s.mergeCells(range);s.getRange(range).values=[[caption(e)]];
  s.getRange(range).format={font:{name:'Microsoft YaHei',size:9,bold:true,color:'#16202A'},horizontalAlignment:'center',verticalAlignment:'center',wrapText:true};
 }
}
wb.recalculate();
console.log((await wb.inspect({kind:'sheet',maxChars:600})).ndjson);
for(let i=0;i<data.pages.length;i++){
 const pageNumber=data.pages[i].number;
 const pageFingerprint=createHash('sha256').update(JSON.stringify({page:data.pages[i],totalPages,publication:[data.publication_title,data.publication_code,data.publication_date],operations:data.pages[i].operations.map(n=>operations.get(n)),builderHash,images:data.pages[i].images.map(id=>[images.get(id),gateById.get(id)?.image_sha256]),heights:heightOverrides[String(pageNumber)]})).digest('hex');
 if(!previewArg&&previewCache[pageNumber]?.fingerprint===pageFingerprint){try{await fs.access(`${out}/SOP页预览/第${String(pageNumber).padStart(2,'0')}页.png`);continue;}catch{}}
 const b=await wb.render({sheetName:sheets[i].name,range:'B1:AR35',scale:1,format:'png'});
 await fs.writeFile(`${out}/SOP页预览/第${String(pageNumber).padStart(2,'0')}页.png`,new Uint8Array(await b.arrayBuffer()));
 previewCache[pageNumber]={fingerprint:pageFingerprint,builderHash};
 await fs.writeFile(`${out}/SOP页预览/渲染索引.json`,JSON.stringify(previewCache));
 console.log(`PAGE_RENDERED ${i+1}/${data.pages.length}`);
}
if(!previewArg){
 await (await SpreadsheetFile.exportXlsx(wb)).save(`${out}/${data.publication_file||'100步阶段装配SOP.xlsx'}`);
 console.log('FINAL_WORKBOOK_SAVED');
}
