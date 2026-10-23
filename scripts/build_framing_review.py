"""Build an operation-grouped review from real small-batch evidence."""
import argparse
import html
import json
import os
from collections import OrderedDict
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batches',nargs='+',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--material-table',type=Path,help='Numbered material table for a callout demonstration page')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    groups=OrderedDict();records=[]
    for batch in args.batches:
        samples=json.loads((batch/'样本清单.json').read_text(encoding='utf-8'))
        reports=json.loads((batch/'检查记录.json').read_text(encoding='utf-8'))
        for sample,report in zip(samples,reports):
            ident=sample['task_id'];picture=batch/'图片'/(ident+'.jpg')
            if ident!=report['task_id']:raise ValueError('Review evidence order mismatch')
            key=(str(batch),sample['source_index']);groups.setdefault(key,[]).append((sample,report,picture))
            records.append({'batch':str(batch),'source_index':sample['source_index'],**report})
    panels=[]
    for entries in groups.values():
        cards=[];title=html.escape(entries[0][0]['title'])
        for sample,report,picture in entries:
            role=sample.get('image_coverage',{}).get('role','installation')
            caption={'location':'整体定位','detail':'局部安装','installation':'安装单图'}[role]
            coverage=sample.get('image_coverage',{})
            if role=='detail':caption+=f" {coverage['detail_group_index']}/{coverage['detail_group_count']}"
            status='自动检查通过' if report['passed'] else ('等待力矩标注视觉确认' if report['failures']==['TORQUE_VISUAL_REVIEW_REQUIRED'] else '未通过：'+', '.join(report['failures']))
            url=Path(os.path.relpath(picture,args.output)).as_posix()
            img=f'<a href="{html.escape(url)}"><img loading="lazy" src="{html.escape(url)}"></a>' if picture.exists() else '<p>未生成</p>'
            cards.append(f'<article><h3>{caption} · 层级 {sample["assembly_depth"]}</h3>{img}<p class="{"ok" if report["passed"] else "bad"}">{html.escape(status)}</p></article>')
        panels.append(f'<section><h2>{title}</h2><div class="grid">'+''.join(cards)+'</div></section>')
    passed=sum(r['passed'] for r in records);generated=sum(r['generated'] for r in records)
    table=''
    if args.material_table:
        data=json.loads(args.material_table.read_text(encoding='utf-8'))
        rows=''.join('<tr><td>物料 '+str(r['number'])+'</td><td>'+html.escape(r['material_code'])+'</td><td>'+html.escape(r['name'])+'</td></tr>' for r in data['rows'])
        table='<section><h2>数字气泡对应表</h2><p>位置编号关联局部组；物料编号关联下表。两种气泡与力矩共用实心字体、填充底色及引线。示例页编号不替代历史 SOP 分页。</p><table border="1" cellpadding="8"><tr><th>编号</th><th>物料编码</th><th>名称</th></tr>'+rows+'</table></section>'
    heading='两类数字气泡与力矩样式融合' if args.material_table else '通用取景规则 v15：冻结规则小批量回归'
    scope='本批只选择部分局部组验证标注样式，未完成全部定位/局部组出版验收。' if args.material_table else '定位图对应同组全部局部图。缺定位背景的失败仍保留；逐件遮挡负对照另存于验证记录。'
    page=f'''<!doctype html><meta charset="utf-8"><title>{heading}</title>
<style>body{{font-family:Microsoft YaHei;margin:24px;background:#f1f4f7;color:#203040}}section{{background:white;margin:24px 0;padding:18px;border-radius:12px}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}}img{{width:100%}}.ok{{color:#176c44}}.bad{{color:#a62626}}@media(max-width:1000px){{.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}</style>
<h1>{heading}</h1><p>{len(groups)} 个工序样本；原生图 {generated}/{len(records)}，自动检查通过 {passed}。全部使用同一套 CAD 尺寸与锚点规则，无样本倍率特例。</p>
<p>{scope}这里是隔离几何验证，不是正式连续工序 SOP。点击图片看原图。</p>'''+table+''.join(panels)
    (args.output/'规则验证预览.html').write_text(page,encoding='utf-8')
    (args.output/'汇总检查记录.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'operations':len(groups),'generated':generated,'passed':passed,'images':len(records)},ensure_ascii=False))

if __name__=='__main__':main()
