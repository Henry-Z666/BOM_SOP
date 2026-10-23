"""A local review artifact for source-backed, explicitly unapproved phase results."""
from __future__ import annotations
from collections import Counter
from hashlib import sha256
import html
import json
from pathlib import Path

def write_phase_report(workspace: Path, output: Path) -> Path:
    def read(name):
        return json.loads((workspace / name).read_text(encoding="utf-8-sig"))
    package, graph, mapping = (read(n) for n in
        ("process-package.json", "cad-graph.json", "cad-mapping.json"))
    blockers = read("planning-blockers.json") if (workspace / "planning-blockers.json").exists() else {
        "blocked_step_ids": [], "steps": read("geometry-plan.json")["steps"]}
    for source in package["sources"]:
        if "sha256:" + sha256(Path(source["file"]).read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("phase report source changed")
    bom = {r["row"]: r for r in package["bom"]["rows"]}
    data = {"product": package["product_id"], "production_eligible": False,
        "source_package_fingerprint": package["source_package_fingerprint"],
        "sources": package["sources"], "occurrences": len(graph["occurrences"]),
        "constraints": len(graph["constraints"]), "mapping_counts": dict(Counter(r["status"] for r in mapping["rows"])),
        "materials": [{**bom[r["bom_row"]], "mapping_status": r["status"],
            "occurrence_ids": r.get("occurrence_ids", [])} for r in mapping["rows"]],
        "operations": package["operations"], "parameters": package["parameter_catalog"],
        "blocked_step_ids": blockers["blocked_step_ids"],
        "scope_requests": read("scope-requests.json")["requests"],
        "binding_requests": read("operation-binding-requests.json")["steps"],
        "steps": blockers["steps"]}
    output.mkdir(parents=True, exist_ok=True)
    (output / "phase-data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    encoded = json.dumps(data, ensure_ascii=False).replace("</", r"<\/")
    page = TEMPLATE.replace("__DATA__", encoded)
    target = output / "阶段评审.html"
    if (output / "原生安装图-技术验证.jpg").exists() and (output / "原生图验证.json").exists():
        audit = json.loads((output / "原生图验证.json").read_text(encoding="utf-8"))
        failures = html.escape("、".join(audit.get("failures", [])) or "无结构性错误，仍需视觉审核")
        preview = '<section class="panel"><h2>原生出图技术验证</h2><p>此图仅验证显示链路，不是批准的 SOP。当前验收结果：' + failures + '</p><img src="原生安装图-技术验证.jpg" style="max-width:100%;width:620px" alt="原生模型技术验证图"></section>'
        page = page.replace('<section class="panel"><nav>', preview + '<section class="panel"><nav>')
    target.write_text(page, encoding="utf-8")
    summary = "# 重构后的阶段成果\n\n资料：新版 AI生成SOP外发资料。原始文件只读，旧资料不进入运行输入。\n\n"
    summary += f"原生扫描：{len(graph['occurrences'])} 个实例，{len(graph['constraints'])} 条约束。\n\n"
    summary += "BOM 映射：" + "；".join(f"{k} {v}" for k,v in data["mapping_counts"].items()) + "。\n\n"
    summary += f"操作候选 {len(data['operations'])}；步骤草案 {len(data['steps'])}；依赖阻断 {len(data['blocked_step_ids'])}。\n\n"
    summary += "本阶段成果用于评审，不是批准的生产 SOP。未确认型号适用性、分装范围、力矩和工艺顺序不进入正式发布。\n\n"
    summary += "入口：阶段评审.html。可搜索物料、工艺操作、力矩原文及实例；完整结构化证据位于 phase-data.json。\n"
    (output / "阶段说明.md").write_text(summary,encoding="utf-8")
    return target

TEMPLATE = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CDU900 · 重构阶段评审</title>
<style>body{margin:0;background:#f4f6fa;color:#243349;font:15px/1.65 "Microsoft YaHei",sans-serif}main{max-width:1280px;margin:auto;padding:38px}h1{margin:0;font-size:32px}small{color:#657589}.banner{padding:15px 22px;margin:22px 0;background:#fff5d9;border-left:4px solid #cb982d}.stats,.flow{display:flex;gap:14px;flex-wrap:wrap}.stat,.flow span{padding:18px 24px;background:white;border:1px solid #dce2eb;border-radius:9px;flex:1}.stat b{display:block;font-size:28px;color:#20589c}.panel{background:white;border:1px solid #dce2eb;border-radius:10px;padding:22px;margin-top:24px}nav{display:flex;gap:8px;margin:20px 0}button{cursor:pointer;border:1px solid #ccd7e5;background:white;padding:10px 17px;border-radius:6px}button.active{background:#20589c;color:white}input,select{padding:10px;border:1px solid #ccd7e5;border-radius:6px;margin-right:9px}input{width:min(480px,70%)}.scroll{overflow:auto;max-height:620px}table{width:100%;border-collapse:collapse;font-size:13px}th{position:sticky;top:0;background:#eaf0f8;text-align:left}td,th{padding:11px 13px;border-bottom:1px solid #e8edf4;vertical-align:top}td{white-space:pre-wrap;word-break:break-word}td:first-child{min-width:80px}.bad{color:#a43e31}details{margin-bottom:10px}code{font-size:12px;word-break:break-all}footer{margin-top:30px;color:#63738a}</style>
<main><small>AI ASSEMBLY / 第一阶段 / 2026-10-08</small><h1>CDU900 · 重构阶段评审</h1><div class="banner"><b>待确认成果</b> · 原生扫描已完成，生产发布尚未批准。表中的 ready 表示几何候选，不表示工艺、力矩或生产步骤已批准。</div><div class="stats" id="stats"></div>
<section class="panel"><h2>从新版资料到可执行工艺</h2><div class="flow"><span>新版资料<br><small>BOM / 工艺卡 / 标准</small></span><span>事实与来源<br><small>文字 / 参数 / 哈希</small></span><span>原生 CAD<br><small>实例 / 曲面 / 约束</small></span><span>工艺决策<br><small>范围 / 顺序 / 力矩</small></span><span>出图与发布<br><small>气泡 / 引线 / 审核</small></span></div><p>工艺与参数决策保留独立证据。CAD 装配参考不自动等同于生产先后关系；冲突不会通过删除原始参考消除。</p><details><summary>当前使用的新版源文件及哈希</summary><div id="sources"></div></details></section>
<section class="panel"><nav><button data-tab="materials" class="active">物料映射</button><button data-tab="operations">工艺与力矩</button><button data-tab="steps">步骤与阻断</button><button data-tab="parameters">参数候选库</button></nav><input id="search" placeholder="搜索物料名称、编码、工艺原文、实例路径"><select id="filter"><option value="all">全部</option><option value="issues">待处理项</option></select><p id="count"></p><div class="scroll"><table><thead id="head"></thead><tbody id="rows"></tbody></table></div></section><footer>全量来源和候选均保留。图片参数尚需核验，重复工艺卡及型号差异仍待确认。结构化记录：phase-data.json。</footer></main>
<script>const data=__DATA__;let tab='materials';const blocked=new Set(data.blocked_step_ids);const labels={matched:'精确对应',ambiguous:'多候选',missing:'未对应',quantity_mismatch:'数量不符',non_renderable:'非出图项'};document.getElementById('stats').innerHTML='';for(const [n,v] of [['原生实例',data.occurrences],['BOM 行',data.materials.length],['操作候选',data.operations.length],['阻断步骤',blocked.size]]){let d=document.createElement('div');d.className='stat';let b=document.createElement('b');b.textContent=v;d.append(b,document.createTextNode(n));document.getElementById('stats').append(d)}for(const s of data.sources){let p=document.createElement('p');p.textContent=s.file+'\n'+s.sha256;document.getElementById('sources').append(p)}function render(){let query=document.getElementById('search').value.toLowerCase(),issues=document.getElementById('filter').value==='issues';let heads,items;if(tab==='materials'){heads=['BOM 行','物料','图号 / 编码','状态','原生实例'];items=data.materials.filter(x=>!issues||!['matched','non_renderable'].includes(x.mapping_status)).map(x=>[x.row,x.name,(x.drawing_no||'')+'\n'+(x.material_code||''),labels[x.mapping_status]||x.mapping_status,x.occurrence_ids.join('\n')])}else if(tab==='operations'){heads=['操作候选','物料 / BOM 行','操作文字','力矩原文与来源','状态'];items=data.operations.map(x=>[x.title,x.materials.join('\n')+'\nBOM '+x.candidate_bom_rows.join(', '),x.instructions.map(i=>i.text+' ['+i.sheet+' / '+i.location+']').join('\n'),x.torque_claims.map(c=>c.evidence.text+' ['+c.evidence.sheet+' / '+c.evidence.location+']').join('\n')||'未提取明确参数',x.applicability])}else if(tab==='parameters'){heads=['参数原文','数值 / 单位','源表 / 位置','适用性'];items=data.parameters.map(x=>[x.evidence.text,x.value+x.tolerance+' '+x.unit,x.evidence.sheet+' / '+x.evidence.location,/BOM/i.test(x.evidence.file)?'型号文本中的参数；需核验': '待核验并绑定具体连接'])}else{heads=['步骤草案','BOM 行 / 范围','实例 / 接收件','前置依赖','状态'];items=data.steps.filter(x=>!issues||blocked.has(x.step_id)||x.status!=='ready').map(x=>[x.title+'\n'+x.step_id,x.source_bom_rows.join(', ')+'\n'+x.stage_scope_occurrence,x.moving_occurrences.join('\n')+'\n→ '+x.receiver_occurrences.join(', '),x.depends_on.join('\n'),blocked.has(x.step_id)?'依赖阻断':x.status])}items=items.filter(x=>x.join(' ').toLowerCase().includes(query));document.getElementById('count').textContent='显示 '+items.length+' 条；所有内容均为候选或草案。';let h=document.getElementById('head'),r=document.getElementById('rows');h.replaceChildren();r.replaceChildren();let tr=document.createElement('tr');for(let name of heads){let th=document.createElement('th');th.textContent=name;tr.append(th)}h.append(tr);for(let item of items){let tr=document.createElement('tr');for(let value of item){let td=document.createElement('td');td.textContent=value;tr.append(td)}r.append(tr)}}document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('active',x===b));render()});document.getElementById('search').oninput=render;document.getElementById('filter').onchange=render;render();</script></html>'''
