"""Prepare bounded diagnostic samples using the same reusable framing rule."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from sop_pipeline.agent.installation_view import expand_installation_views


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--indices',required=True,help='Comma-separated zero-based source indices')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--policy',choices=['v10','v11','v12','v13','v14','v15'],default='v15')
    parser.add_argument('--max-images',type=int,default=24)
    parser.add_argument('--material-map',type=Path,help='Fixed SOP page tables with page_id, step_ids and explicitly occurrence-bound rows')
    args=parser.parse_args()
    if not 1<=args.max_images<=32:parser.error('Small-batch limit must be between 1 and 32')
    source=json.loads(args.plan.read_text(encoding='utf-8'))
    indices=list(dict.fromkeys(int(x) for x in args.indices.split(',')))
    policy='single-image-balanced-fit/'+args.policy
    page_map={}
    if args.material_map:
        for page in json.loads(args.material_map.read_text(encoding='utf-8'))['pages']:
            for step_id in page['step_ids']:
                if step_id in page_map:parser.error('A step cannot belong to multiple material-number pages')
                page_map[step_id]=page
    tasks=[];samples=[]
    for index in indices:
        if not 0<=index<len(source['tasks']):parser.error('Source index outside render plan')
        original=source['tasks'][index]
        if args.material_map and original['step_id'] not in page_map:parser.error('Selected step has no fixed material-number page')
        if original['payload'].get('execution_mode')!='diagnostic_preview':
            parser.error('This tool accepts isolated diagnostic plans only; use the formal stage planner for production')
        for view_index,payload in enumerate(expand_installation_views(original['payload'],policy=policy)):
            if args.material_map and payload.get('image_coverage',{}).get('role')!='location':
                from sop_pipeline.agent.material_annotations import attach_material_numbers
                page=page_map[original['step_id']]
                payload=attach_material_numbers(payload,page_id=page['page_id'],material_rows=page['rows'])
            task=deepcopy(original);task['task_id']+=f'-{args.policy}sample{view_index}'
            payload['production_eligible']=False;task['payload']=payload;tasks.append(task)
            samples.append({'task_id':task['task_id'],'source_index':index,'title':payload.get('title',task['step_id']),
                'assembly_depth':max(o.count('/')+1 for o in payload['moving_occurrences']),
                'moving_count':len(payload['moving_occurrences']),'image_coverage':payload.get('image_coverage',{}),
                'composition':payload['installation_view']})
    if len(tasks)>args.max_images:parser.error(f'{len(tasks)} images exceed small-batch limit; select fewer operations')
    result=deepcopy(source);result['tasks']=tasks
    args.output.mkdir(parents=True,exist_ok=True)
    for name,value in [('jobs.json',result),('样本清单.json',samples),('source_selection.json',
        {'schema_version':'framing-sample-selection/v1','policy':policy,'source_plan':str(args.plan.resolve()),
         'source_sha256':hashlib.sha256(args.plan.read_bytes()).hexdigest(),'source_indices':indices,'image_count':len(tasks),
         'production_eligible':False,'sample_specific_scale_overrides':False})]:
        (args.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'operations':len(indices),'images':len(tasks),'policy':policy},ensure_ascii=False))


if __name__=='__main__':main()
