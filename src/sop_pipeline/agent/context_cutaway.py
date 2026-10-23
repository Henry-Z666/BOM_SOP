"""CAD-ray candidates for explicit local cutaway diagrams, never installation edits."""
import math
from copy import deepcopy

def ray_box_entry(origin,direction,box):
    low,high=0.,math.inf
    for k in range(3):
        if abs(direction[k])<1e-10:
            if not box['min'][k]<=origin[k]<=box['max'][k]:return None
            continue
        a=(box['min'][k]-origin[k])/direction[k];b=(box['max'][k]-origin[k])/direction[k]
        low=max(low,min(a,b));high=min(high,max(a,b))
        if low>high:return None
    return low if high>1e-6 else None

def context_cutaway(payload,graph,*,limit=2,minimum_context_span=80):
    if payload.get('image_coverage',{}).get('role')!='detail':raise ValueError('Cutaway is restricted to local detail diagrams')
    p=deepcopy(payload);moving=set(p['moving_occurrences']);protected=moving|set(p['receiver_occurrences'])
    direction=p['camera']['position_direction_root'];visible=set(p['visible_occurrences']);existing=set(p.get('bom_visibility_exclusions',[]))
    geometry=p['stage_geometry_root']['moving_bounds'];vectors=p.get('occurrence_translation_vectors_root',{})
    centres=[[.5*(box['min'][k]+box['max'][k])+vectors.get(o,p['translation_vector_root'])[k] for k in range(3)] for o,box in zip(p['moving_occurrences'],geometry)]
    candidates=[]
    for node in graph['occurrences']:
        o=node['occurrence_id'];box=node.get('bounds_root',{})
        if not str(node.get('part_no','')).lower().endswith('.prt') or box.get('status')!='available':continue
        if node.get('feature_status_code') in (2,3,4,5):continue
        if any(o==q or o.startswith(q+'/') or q.startswith(o+'/') for q in protected):continue
        pieces=o.split('/');ancestors={'/'.join(pieces[:i]) for i in range(1,len(pieces)+1)}
        if not ancestors & visible:continue
        if ancestors & existing:continue
        # Keep small connectors, receivers, and surrounding installation clues.
        if max(box['max'][k]-box['min'][k] for k in range(3))<minimum_context_span:continue
        entries=[ray_box_entry(c,direction,box) for c in centres]
        entries=[v for v in entries if v is not None]
        if entries:candidates.append((min(entries),o,box))
    chosen=sorted(candidates,key=lambda x:(x[0],x[1]))[:limit]
    if not chosen:raise ValueError('No CAD-derived context cutaway candidate')
    p['bom_visibility_exclusions']=sorted(set(existing)|{o for _,o,_ in chosen})
    p['context_cutaway']={'schema_version':'source-native-context-cutaway/v1','hidden_context_occurrences':[o for _,o,_ in chosen],'native_bounds':[b for _,_,b in chosen],'basis':'Native CAD ray/bounds candidates; final visibility must be verified','moving_and_receiver_geometry_unchanged':True,'publication_caption':'局部连接示意（遮挡外壁隐藏）','requires_intact_locator':True}
    return p
