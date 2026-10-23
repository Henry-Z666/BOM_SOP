"""Withdraw a thin cover from an externally bounded native receiver face."""
from copy import deepcopy
import math


def resolve_thin_cover_side(payload, graph):
    p=deepcopy(payload)
    if len(p['moving_occurrences'])!=1:raise ValueError('Opposite cover faces require separate details')
    o=p['moving_occurrences'][0];b=p['stage_geometry_root']['moving_bounds'][0]
    nodes={n['occurrence_id']:n for n in graph['occurrences']};choices=[]
    for c in graph['constraints']:
        if c['occurrences'][0]!=o or c.get('type') not in ('INSERT','ALIGN_OFF','MATE_OFF','MATE'):continue
        ref=c.get('assembly_reference',{});g=ref.get('geometry',{});n=g.get('direction_root');rb=nodes.get(ref.get('occurrence_id'),{}).get('bounds_root',{})
        if ref.get('item_type')!='SURFACE' or not n or rb.get('status')!='available':continue
        length=math.sqrt(sum(x*x for x in n))
        if length<=0:continue
        n=[x/length for x in n];axis=max(range(3),key=lambda k:abs(n[k]))
        if abs(n[axis])<.99999:continue
        thickness=b['max'][axis]-b['min'][axis];span=max(b['max'][k]-b['min'][k] for k in range(3))
        if thickness<=0 or thickness>.35*span:continue
        # A disjoint interval at a receiver boundary proves the outside;
        # a mid-plane, remote datum, or an embedded plate does not.
        clearance=max(2.,.5*thickness)
        if -.5<=b['min'][axis]-rb['max'][axis]<=clearance:sign=1
        elif -.5<=rb['min'][axis]-b['max'][axis]<=clearance:sign=-1
        else:continue
        if any(min(b['max'][k],rb['max'][k])-max(b['min'][k],rb['min'][k])<.25*(b['max'][k]-b['min'][k]) for k in range(3) if k!=axis):continue
        direction=[x*(sign if n[axis]>0 else -sign) for x in n]
        choices.append((c,ref,rb,direction,thickness,span))
    if not choices:raise ValueError('No touching external receiver boundary proves cover side')
    if any(sum(a*b for a,b in zip(choices[0][3],q[3]))<.9999 for q in choices):raise ValueError('Conflicting native cover boundaries')
    c,ref,rb,n,t,span=choices[0];distance=max(2*t,.35*span);v=[x*distance for x in n]
    p['thin_cover_side_evidence']={'schema_version':'native-external-thin-cover/v1','assembly_sha256':graph['assembly_sha256'],'occurrence':o,'constraint_id':c['id'],'assembly_reference':c['assembly_reference'],'moving_bounds':b,'receiver_bounds':rb,'direction_root':n,'travel_mm':distance}
    p['receiver_normal_root']=n;p['translation_vector_root']=v;p['occurrence_translation_vectors_root']={o:v}
    p['constraint_ids']=[c['id']];p['receiver_occurrences']=[ref['occurrence_id']]
    p['receiver_interface_selections']=[{'occurrence_id':ref['occurrence_id'],'item_id':ref['item_id'],'item_type':'SURFACE'}]
    for a in p['arrow_anchors']:
        a['constraint_id']=c['id'];a['expected_exploded_point_root']=[a['complete_point_root'][k]+v[k] for k in range(3)]
    p['connection_camera']={'schema_version':'contact-interior-oblique/v1','foundation_occurrence':ref['occurrence_id'],'foundation_bounds':{k:rb[k] for k in ('min','max')}}
    return p
