"""Compile component work cards; atomic CAD jobs remain traceable, not publishable units."""
import copy,json,re,math,hashlib
from collections import Counter
from decimal import Decimal
from pathlib import Path
from publication_inputs import load_input,output_directory
ROOT=Path(__file__).resolve().parents[2]
OUT=output_directory()
OUT.mkdir(parents=True,exist_ok=True)
d=load_input('出版排序内容.json')
if d.get('status') == 'draft_not_rendered':
 raise ValueError('重建草案尚未出图，不能套用历史工序号配方出版；请先完成范围、顺序与渲染绑定。')
ops={o['number']:o for o in d['operations']}; images={i['task_id']:i for i in d['images']}
refs={r['operation']:r for r in load_input('工艺图像索引.json')}
tools=load_input('工艺工具来源.json')
process=load_input('process-package.json')
process_by_id={o['operation_id']:o for o in process['operations']}
packages=[]; assigned=set()
def add(title,nums,actions,hero=None,chapter=None):
 nums=list(nums)
 assert not assigned.intersection(nums),(title,assigned.intersection(nums))
 assigned.update(nums)
 packages.append(dict(title=title,operations=nums,actions=actions,hero=hero,chapter=chapter or ops[nums[0]].get('chapter','装配')))
def seq(a,b):return list(range(a,b+1))

add('框架底盘与四根立柱',seq(1,8),['核对底盘方向及四根立柱图号，将完整立柱分别就位。','按柱脚连接图装入组合螺栓及沉头螺钉，完成四处固定。'],7,'框架安装')
add('框架前部横梁',[9,10],['按图号及孔位区分两根横梁，装到立柱对应位置。','横梁与立柱连接处使用本页配套组合螺栓固定。'],10)
add('框架底部加强支撑',seq(11,14),['将加强件焊件作为完整来料，按图示位置装入框架。','核对各加强件图号及连接方向，配套紧固件随部件一并安装。'],14)
add('框架水盘与下部支撑',seq(15,18),['将水盘焊件、加强件和固定件按整体位置图就位。','使用对应规格组合螺栓完成各连接处固定。'],15)
add('框架中部横梁',seq(19,22),['依图号区分各横梁，按对应层位装入框架。','横梁端部连接使用本页组合螺栓，沉头连接按局部图安装。'],22)
add('框架上部横梁',seq(23,27),['按图示层位安装横梁合件及上横梁焊件。','核对连接孔位和方向，配套组合螺栓与沉头螺钉一并固定。'],27)
add('框架后部支撑与加强件',[28,29,39,40,46,47,48],['将后部横梁、固定件合件及加强件按图号分别就位。','按各部件连接形式配套螺栓、垫圈并固定。'],48)
add('框架门锁与门板安装支座',[30,31,38,41,42,43,44,45],['按图安装门锁固定件、门板固定件及对应支座。','配套螺栓、沉头螺钉和锁紧薄螺母随支座完成固定。'],31)
add('离心泵与减振安装',seq(95,100),['按整体位置图将离心泵和橡胶减振垫就位。','装入平垫、弹垫和M12螺母；按管路连接顺序完成最终锁紧。'],100,'水泵安装')
add('板式换热器与安装支撑',seq(101,107)+[114,116,117,118,119,120,121,216],['板换、橡塑海绵及减振垫按对应位置配套，接口方向核对原工艺卡。','将完成配套的板换部件装入框架，并完成横梁和两侧固定连接。'],107,'板换安装')
add('设备支撑与框架横梁',seq(108,113)+[217],['设备就位后安装图示横梁，保持前道设备安装空间。','横梁及对应固定件使用本页紧固件完成连接。'],111,'框架安装')
add('进水管部件 SX0402307477',seq(49,70)+[290,291,292,293],['以完整管路焊件为基体，按接口位置装配阀件、密封件及传感器。','完成卡箍连接后，将进水管部件整体装入框架对应位置。'],293,'管路安装')
add('进水管部件 SX0402307479',seq(71,81),['以完整管路焊件为基体，按图装配阀件、转接头和密封件。','核对排气阀及压力传感器位置，完成卡箍和密封连接。'],81,'管路分装')
add('出水管部件 SX0402307821',seq(122,133)+[247,248,249,250,251,294,295,296,297],['按管路接口关系装配球阀、流量计、安全阀和压力传感器。','各密封圈及卡箍垫圈随对应接口安装，按连接要求完成固定。'],127,'管路分装')
add('出水管部件 SX0402307487',seq(134,143),['以完整管路焊件为基础，装配阀件、压力传感器和转接头。','端盖、垫圈与卡箍按对应接口成组安装。'],139,'管路分装')
add('进水管部件 SX0402307822',seq(82,90)+seq(144,152),['以完整管路焊件为基体，安装电动二通阀及各接口阀件。','按图示接口装入密封件、端盖和卡箍，核对压力传感器位置。'],82,'管路分装')
add('水质检测部件',seq(153,157),['按图装配PH探头、传感器及球阀，配套对应密封圈。','保持接口对应关系，完成水质检测部件分装。'],157,'管路分装')
add('进水管部件 SX0402307151',seq(158,160)+seq(252,256),['以完整管路焊件为基体，装配球阀和转接头。','按接口装入密封圈、卡箍垫圈及卡箍，完成部件分装。'],254,'管路分装')
add('水系统入框与法兰连接',seq(257,271)+seq(298,303)+[354,355,356],['将完成分装的管路按总装位置装入框架，核对管口及法兰内无异物。','密封垫片与法兰螺栓成组安装，按对应孔位完成连接。','温度传感器及电缆防水接头随对应接口安装。'],356,'管路安装')
add('管路支撑固定组件',[309,310,311,312],['按每组组成配套22组管路固定螺栓组合。','将固定组合用于总装图示管路支撑处；按本卡各页数量配套。'],312,'管路安装')
add('弱电控箱箱体与安装基座',[161,162,163,164,165,166,167,168,169,185,189,190,191,192],['使用完整电控箱体焊件和门板部件，安装内部固定件、导轨及线槽。','按图示位置固定箱体内各支撑件，紧固件随对应部件安装。'],169,'弱电控箱装配')
add('弱电控箱控制器与端子',seq(170,182),['按导轨位置装配控制器、模块、端子、交换机及水浸检测模块。','核对器件型号和相邻端子布置，护线环及端子固定座随器件安装。'],182,'弱电控箱装配')
add('弱电控箱通风组件',[183,184,186,187,188],['按图将散热风扇及风机网罩安装到箱体对应开口。','网罩和风扇按图示连接使用锁紧薄螺母固定。'],183,'弱电控箱装配')
add('弱电控箱整体安装',[273,197,318],['弱电控箱分装及箱内器件完成后，将总成整体装入框架。','箱体按总装位置固定，箱内已完成部件保持完整。'],273,'电控箱安装')
add('强电控箱箱体与门板',[193,194,195,196,274],['使用完整电控箱部件，按图安装电控箱门板部件。','门板与箱体连接处的螺钉、锁紧螺母随部件配套安装。'],193,'强电控箱装配')
add('强电箱电器部件',seq(275,280),['按器件布置装配端子固定座、开关电源、断路器和变频器。','电器部件分装完成后整体安装到强电箱体内。'],280,'强电控箱装配')
add('强电控箱整体安装',[282,316,283],['强电箱内部装配与接线完成后，将总成整体装入框架。','按图示位置固定箱体，保持内部总成完整。'],316,'电控箱安装')
add('温湿度与水质控制器',[198,199,201,202,203],['按总装位置安装温湿度传感器、水质控制器及电缆防水接头。','传感器固定螺钉随本体安装，接口方向按图核对。'],198,'检测器件安装')
add('操作面板与按钮器件',seq(304,308),['按面板孔位安装触摸屏、指示灯、蜂鸣器及按钮。','按钮保护罩随按钮配套，按器件对应连接完成固定。'],304,'操作面板安装')
add('照明与限位开关',[200,349,350],['按图安装限位开关及LED照明器件。','固定螺钉、锁紧螺母随器件安装，完成对应电气连接。'],350,'照明与限位')
add('膨胀罐与软管连接',[204,205,206,207,347,348],['按整体位置图安装膨胀罐、支撑固定件及组合螺栓。','连接膨胀罐软管，注意随管密封垫圈；完成阀帽及喉箍配套。'],204,'膨胀罐安装')
add('补液泵与安装支架',[208,209,210,211,212,213,214,215,218,219],['按图安装补液泵支架，将完整隔膜泵固定到支架上。','配套宝塔接头、软管夹箍及对应固定紧固件。'],212,'补液泵安装')
add('进水管部件 SX0402307454',seq(220,226),['以完整管路焊件为基础，安装电磁阀、转接头及对应密封件。','部件分装完成后按总装位置整体安装。'],226,'补液管路安装')
add('水箱部件与总装安装',seq(227,234)+[115],['水箱焊件作为完整来料，安装球阀、快接弯头、软管及压力传感器。','完成接口密封后将水箱部件整体装到总装位置。'],234,'水箱安装')
add('补水管部件 SX0402307450',seq(235,244),['以完整管路焊件为基础，安装端盖、电磁阀、转接头和密封件。','卡箍连接完成后，将补水管部件整体装入对应位置。'],244,'补水管路安装')
add('软管布置与喉箍固定',[284,313,314,345,346],['按原工艺卡连接软管及塑料管，核对规格和走向。','喉箍随各软管接口安装，按对应位置图完成固定。'],346,'软管安装')
add('侧面板组件',[32,33,34,35],['侧面板焊件作为完整来料使用，按对应图号及侧别就位。','完成两侧面板安装，保持已完成焊件整体。'],35,'门板安装')
add('前面板与挡水组件',[91,92,93,94],['以完整前面板部件为基础，安装门弹垫及挡水板。','对应螺栓随挡水板安装，完成后将前面板装到框架。'],94,'门板安装')
add('门锁拉杆与前门组件',[36,37,246,285,286,287,288,289],['将门锁、拉杆及门垫装到完整前面板部件上。','核对门锁和拉杆安装关系，完成前门组件及对应锁紧连接。'],288,'门板安装')
add('封板与顶板收口',[245,357,358,359,360],['内部装配及检查完成后，按图安装封板与顶板。','使用本页自攻锁紧螺钉完成外部收口固定。'],359,'门板安装')

# User narrowed this edition to the principal CAD work. Labels, on-site photos,
# wiring photo cards, inspections and packing remain in the full source package.
excluded=sorted(set(ops)-assigned)
assert all(ops[n].get('process_only') for n in excluded)
excluded_occurrences=[a for n in excluded for m in ops[n]['materials'] for a in m['occurrence_ids']]

fastener=re.compile('螺[钉栓母]|垫圈|垫片|卡箍|喉箍|密封圈|门弹垫|门胶垫')
generic=re.compile(r'^(核对 .+，按图安装.+本步骤共.+。|核对图号 .+，整体安装 .+。|对应连接使用：.+。|局部图按连接位置分组|核对定位图中的|以完整.+为分装基础|以.+作为本分装基体|物料按本步骤各页|将完成分装的.+安装至框架内图示位置)')
def meaningful(o):
 return [t for t in o['instructions'] if not generic.match(t) and not re.match(r'^JH\d+ 柱脚：',t) and not t.startswith(('核对 XS','核对图号','局部连接示意中隐藏','局部图将接收件透明','局部图按连接','局部图中','局部连接示意隐藏'))]
def merge_materials(numbers):
 result={}
 for n in numbers:
  for m in ops[n]['materials']:
   key=(m['row'],m['material_code'],m.get('unit',''))
   if key not in result:result[key]=copy.deepcopy(m)
   else:
    v=result[key];v['quantity']=float(Decimal(str(v['quantity']))+Decimal(str(m['quantity'])))
    if v['quantity'].is_integer():v['quantity']=int(v['quantity'])
    v['occurrence_ids']+=m['occurrence_ids']
 return list(result.values())
def islocation(e):return e['payload'].get('completed_stage_only') or e['payload'].get('image_coverage',{}).get('role')=='location'
def imgname(e):
 o=ops[e['operation']];moving=set(e['payload']['moving_occurrences'])
 if o['title'].startswith('紧固柱脚'):return o['title'].replace('紧固','')
 m=next((m for m in o['materials'] if moving.intersection(m['occurrence_ids'])),None)
 return (m['name']+(' '+m['drawing_no'] if not fastener.search(m['name']) else '')) if m else o['title'].removeprefix('安装')

items=[]
for pn,pkg in enumerate(packages,1):
 # The valve cap is a sourced instruction on the tank installation card, not
 # an independent manufacturing page. Its 0.6 N·m requirement remains explicit.
 native=[n for n in pkg['operations'] if not ops[n].get('process_only') or n==348]
 sources=[n for n in pkg['operations'] if ops[n].get('process_only') and n!=348]
 if pkg['title']=='板式换热器与安装支撑':
  items.append((pn,dict(pkg,operations=seq(101,106),hero=105,section='分装与保温')))
  items.append((pn,dict(pkg,operations=[107,114,116,117,118,119,120,121,216],section='整体安装与固定')))
  continue
 items.append((pn,pkg))
pages=[]
for pn,pkg in items:
 nums=pkg['operations'];materials=merge_materials(nums)
 ref=None  # Source text remains applicable; photo/check/label pages are omitted.
 # Any critical text survives with its original material identity and source linkage.
 controls=[]; seen=set()
 for n in nums:
  source=process_by_id.get(ops[n].get('source_process_operation_id'))
  # Drawing callouts remain on the supplied diagram. Only actual control cells
  # populate the right-hand control column, avoiding duplicate text/blank pages.
  texts=[r['text'] for r in source['instructions'] if re.fullmatch(r'AJ(?:[89]|1[0-8])',r['location']) and r['text']!='互检内容'] if ref and source else meaningful(ops[n])
  for t in texts:
   if t in seen:continue
   seen.add(t)
   if len(nums)>1 and ('力矩' in t or '螺纹胶' in t) and '限位开关' not in t:
    scope='、'.join(dict.fromkeys(m['name']+(' '+(m.get('model') or m.get('drawing_no') or '') if re.search('卡箍|球阀',m['name']) else '') for m in ops[n]['materials']))
    t=scope+'：'+t
   controls.append(t)
 if pkg['title']=='板式换热器与安装支撑':controls.insert(0,'板换接口标识“上”的一端安装在顶部，不可装反。')
 if pkg['title']=='离心泵与减振安装':controls.append('使用塑料封盖或美纹纸密封泵口，防止进入灰尘。')
 if pkg['title']=='照明与限位开关':controls=['限位开关自带螺母按6.0 N·m锁紧；M4×30安装螺钉与M4锁紧螺母按1.2 N·m锁紧。','LED灯使用自带螺钉按1.7 N·m锁紧。']
 if pkg['title']=='膨胀罐与软管连接':controls=['连接软管前放置自带密封圈，按20 N·m锁紧并划线；不得遗漏密封圈。','弃用顶部自带塑料盖；排气阀阀帽使用M10套筒锁紧至顶部充气嘴，力矩0.6 N·m，锁紧后划线。']
 # Native component cards explain the action once. Routine atomic placement text
 # disappears; control requirements and BOM issue quantities remain intact.
 if not ref:
  controls=[t for t in controls if not t.startswith(('图示为一组装配完成状态','每组：M8×30','按物料表配套本机','领料时核对规格'))]
  if any(m.get('assembly_boundary') for m in materials):controls.insert(0,'物料表黄色名称栏为整体来料，直接安装；内部焊接、压铆及制造不展开。')
 if not controls and not ref:controls=['核对本部件图号、安装位置与连接方向；各紧固件随部件配套安装。']
 # Short control cells keep their qualifiers, allowing continuation cards instead
 # of shrinking fonts. No character is dropped in the splitting operation.
 lines=[]
 for t in controls:
  while len(t)>62:
   stop=max(t.rfind('；',0,62),t.rfind('。',0,62),t.rfind('，',0,62))
   cut=stop+1 if stop>=28 else 62;lines.append(t[:cut]);t=t[cut:]
  if t:lines.append(t)
 mparts=[materials[i:i+9] for i in range(0,len(materials),9)] or [[]]
 cparts=[lines[i:i+10] for i in range(0,len(lines),10)]
 count=max(len(mparts),len(cparts))
 choices=[images[k] for n in nums for k in ops[n]['images']]
 hero_candidates=[e for e in choices if e['operation']==pkg['hero']] if pkg['hero'] else []
 hero=next((e for e in hero_candidates if islocation(e)),None)
 if hero is None:hero=next((e for e in reversed(choices) if islocation(e)),None) or next(iter(hero_candidates),None) or next(iter(choices),None)
 if pkg['title']=='弱电控箱控制器与端子':hero=images['full20261013-op-179-view-01']
 if pkg['title']=='进水管部件 SX0402307822':hero=images['full20261013-op-148-view-01']
 frame_install={'进水管部件 SX0402307454':'full20261013-op-239-view-01','水箱部件与总装安装':'full20261013-op-248-view-01','补水管部件 SX0402307450':'full20261013-op-258-view-01'}
 if pkg['title'] in frame_install:hero=images[frame_install[pkg['title']]]
 for part in range(count):
  mats=mparts[part] if part<len(mparts) else []
  wanted={m['row'] for m in mats}
  # Use at most three views: component position plus two pertinent details.
  scored=[]
  for e in choices:
   if hero and e['task_id']==hero['task_id']:continue
   o=ops[e['operation']];names=[m['name'] for m in o['materials']]
   relevance=bool(wanted.intersection(m['row'] for m in o['materials']))
   score=(20 if relevance else 0)+(5 if not islocation(e) else 0)+(4 if any(not fastener.search(n) for n in names) else 0)
   if e.get('method_reference'):score-=30
   scored.append((score,e))
  chosen=[hero] if hero else []
  for _,e in sorted(scored,key=lambda x:x[0],reverse=True):
   if len(chosen)>=3:break
   if any(e['operation']==c['operation'] and islocation(e)==islocation(c) for c in chosen):continue
   chosen.append(e)
  if pkg['title']=='离心泵与减振安装':
   # M12 nut/washer connection is the useful detail, not another tiny overview.
   detail=next((images[k] for k in ops[98]['images'] if not islocation(images[k])),None)
   if detail:chosen=[hero,detail]
  if ref:chosen=[]
  ids=[e['task_id'] for e in chosen]
  captions={e['task_id']:('整体位置｜' if islocation(e) else '连接示意｜')+imgname(e) for e in chosen}
  if hero:captions[hero['task_id']]=('整体位置｜' if islocation(hero) else '部件安装｜')+pkg['title']
  for e in chosen:
   if e.get('method_reference'):captions[e['task_id']]='同型号连接方法示例；实际位置见整体图'
  ts=[]
  for n in nums:
   for tool in tools.get(ops[n].get('source_process_operation_id'),[]):
    if tool not in ts:ts.append(tool)
  assert len(ts)<=4,(pkg['title'],ts)
  page=dict(number=len(pages)+1,package_number=pn,component_title=pkg['title'],section=pkg.get('section',''),chapter=pkg['chapter'],operations=nums,images=ids,captions=captions,materials=mats,instructions=cparts[part] if part<len(cparts) else [],actions=pkg['actions'],tools=ts,reference_images=[ref] if ref else [],part=part+1,part_count=count)
  if ref:page['actions']=[]
  pages.append(page)

# Order by component work sequence, with the source process work packages restored
# at the correct construction/inspection milestones rather than appended blindly.
def phase(p):
 title=p['component_title']; ns=p['operations']
 if 272 in ns:return (20.5,0)
 if 281 in ns:return (25.5,0)
 if 315 in ns or 317 in ns or any(318<=n<=328 for n in ns):return (35,p['number'])
 if 351 in ns or 352 in ns:return (35.5,p['number'])
 if 353 in ns:return (36,p['number'])
 if any(329<=n<=344 for n in ns):return (37,p['number'])
 if any(361<=n<=366 for n in ns):return (43,p['number'])
 return (p['package_number'],p['part'])
# Explicit component ranks prevent electrical installation preceding its wiring.
rank={p['title']:i+1 for i,p in enumerate(packages[:39])}
# Use named milestone insertion without changing component-local order.
native_order={p['title']:i*10 for i,p in enumerate(packages) if not all(ops[n].get('process_only') for n in p['operations'])}
def order(p):
 ns=p['operations'];title=p['component_title']
 if 272 in ns:return (native_order['弱电控箱整体安装']-1,p['part'])
 if 281 in ns:return (native_order['强电控箱整体安装']-1,p['part'])
 if any(n in ns for n in [315,317,319,320,321,322,323,324,325,326,327,328,351,352]):return (native_order['侧面板组件']-5,p['package_number']+p['part']/100)
 if 353 in ns:return (native_order['侧面板组件']-4,p['part'])
 if any(329<=n<=344 for n in ns):return (native_order['侧面板组件']-3,p['package_number']+p['part']/100)
 if any(361<=n<=366 for n in ns):return (1000,p['package_number']+p['part']/100)
 return (native_order.get(title,900),p['part'])
pages.sort(key=order)
package_page_counts=Counter(p['package_number'] for p in pages)
package_page_index=Counter()
for i,p in enumerate(pages,1):
 p['number']=i;package_page_index[p['package_number']]+=1
 p['part']=package_page_index[p['package_number']];p['part_count']=package_page_counts[p['package_number']]
charged=[a for p in pages for m in p['materials'] for a in m['occurrence_ids']]
expected=[a for o in d['operations'] if o['number'] in assigned for m in o['materials'] for a in m['occurrence_ids']]
assert len(charged)==len(set(charged)) and Counter(charged)==Counter(expected)
def totals(materials):
 c=Counter()
 for m in materials:c[(m['row'],m['material_code'],m.get('unit',''))]+=Decimal(str(m['quantity']))
 return c
assert totals(m for p in pages for m in p['materials'])==totals(m for o in d['operations'] if o['number'] in assigned for m in o['materials'])
used={k for p in pages for k in p['images']}
d['images']=[e for e in d['images'] if e['task_id'] in used]
d.update(pages=pages,component_packages=packages,publication_mode='component_cards/v1',publication_scope='main_cad_assembly_only',excluded_non_cad_operations=excluded,publication_title='CDU900左机部件装配工艺卡',publication_code='SOP-CDU900-LEFT-C',publication_file='CDU900左机部件式装配SOP.xlsx',publication_date='2026-10-15',branding={'image_path':str(ROOT/'assets/publication/shenling-logo.png'),'source_file':'QSL 10303376877-2026 中金乌兰察布CDU900总装工艺卡（左机）.xlsx','source_media':'xl/media/image6.png'})
(OUT/'出版内容.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf8')
gates=load_input('出图检查.json');gates=[g for g in gates if g['task_id'] in used]
assert len(gates)==len(used) and all(g['passed'] for g in gates)
(OUT/'出图检查.json').write_text(json.dumps(gates,ensure_ascii=False,indent=2),encoding='utf8')
audit=dict(component_packages=len(packages),pages=len(pages),selected_native_images=len(used),atomic_operations_retained_internally=len(ops),excluded_non_cad_operations=excluded,excluded_accessory_packaging_occurrences=len(excluded_occurrences),material_occurrences=len(charged),in_scope_material_quantities_exact=True,all_in_scope_cad_occurrences_preserved=True,color_boundary_rule_preserved=True)
(OUT/'部件编排检查.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(audit,ensure_ascii=False))
for p in pages:print(p['number'],p['component_title'],p['part'],p['part_count'],len(p['materials']),len(p['instructions']))
