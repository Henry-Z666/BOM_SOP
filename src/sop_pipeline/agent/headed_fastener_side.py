"""Prove a standard headed fastener's withdrawal side at its native seat."""
import math,re
from copy import deepcopy


def validate_proven_headed_directions(payload):
    proof=payload.get('headed_fastener_side_evidence')
    if not proof:return
    if proof.get('schema_version')!='native-headed-fastener-seat/v1':raise ValueError('Unknown headed fastener evidence')
    for contact in proof['contacts']:
        occurrence=contact['occurrence']
        if occurrence not in payload['moving_occurrences']:raise ValueError('Headed evidence refers to an absent moving occurrence')
        v=payload['occurrence_translation_vectors_root'][occurrence];n=contact['head_side_root']
        length=math.sqrt(sum(x*x for x in v))*math.sqrt(sum(x*x for x in n))
        if not math.isfinite(length) or length<=0 or sum(a*b for a,b in zip(v,n))/length<.999999:
            raise ValueError('Presentation movement contradicts the proven fastener head side')


def headed_fastener_side(payload,graph):
    if payload.get('image_coverage',{}).get('role')!='detail':raise ValueError('Requires a detail view')
    nodes={n['occurrence_id']:n for n in graph['occurrences']};proof=[]
    for occurrence,box in zip(payload['moving_occurrences'],payload['stage_geometry_root']['moving_bounds'],strict=True):
        labels=[r.get('name','') for r in payload.get('material_annotations',{}).get('material_rows',[]) if occurrence in r.get('occurrence_ids',[])]
        if not any(('螺钉' in n or '螺栓' in n) and '头' in n and not any(x in n for x in ('沉头','肩','定位','焊接')) for n in labels):raise ValueError('Not a proven ordinary headed screw')
        node=nodes[occurrence]
        if not node.get('part_no','').lower().endswith('.prt'):raise ValueError('Requires a rigid fastener part')
        spec=' '.join([node.get('part_no',''),*map(str,node.get('parameters',{}).values())]);size=re.search(r'M\s*(\d+(?:\.\d+)?)\s*[xX×*]\s*(\d+(?:\.\d+)?)',spec)
        if not size:raise ValueError('No native nominal thread size')
        diameter,length=map(float,size.groups());candidates=[]
        for c in graph['constraints']:
            if c['occurrences'][0]!=occurrence or c.get('type') not in ('MATE','MATE_OFF'):continue
            a=c.get('assembly_reference',{});b=c.get('component_reference',{});ag=a.get('geometry',{});bg=b.get('geometry',{})
            if a.get('item_type')!='SURFACE' or b.get('item_type')!='SURFACE' or ag.get('status')!='available' or bg.get('status')!='available':continue
            cp=bg.get('point_root');ap=ag.get('point_root');normal=bg.get('direction_root');an=ag.get('direction_root')
            if not cp or not ap or not normal or not an:continue
            norm=math.sqrt(sum(v*v for v in normal));al=math.sqrt(sum(v*v for v in an))
            if norm<1e-8 or al<1e-8:continue
            n=[v/norm for v in normal]
            if max(map(abs,n))<.995 or abs(sum(x*y/al for x,y in zip(n,an)))<.999:continue
            rb=nodes.get(a.get('occurrence_id'),{}).get('bounds_root',{})
            if rb.get('status')!='available' or any(cp[k]<rb['min'][k]-3 or cp[k]>rb['max'][k]+3 for k in range(3)):continue
            gap=abs(sum((ap[k]-cp[k])*n[k] for k in range(3)))
            if abs(gap-abs(float(c.get('offset',0))))>max(.5,diameter*.1):continue
            low=sum((box['min'][k] if n[k]>=0 else box['max'][k])*n[k] for k in range(3));high=sum((box['max'][k] if n[k]>=0 else box['min'][k])*n[k] for k in range(3));seat=sum(cp[k]*n[k] for k in range(3));minus,plus=seat-low,high-seat
            short,long=sorted((minus,plus))
            # Projecting a root AABB onto a slightly rotated screw axis also
            # includes its transverse widths. Account for that bounded excess
            # rather than rejecting a nominal head exactly at the size limit.
            major=max(range(3),key=lambda k:abs(n[k]))
            aabb_excess=sum(abs(n[k])*(box['max'][k]-box['min'][k]) for k in range(3) if k!=major)
            if not .12*diameter<=short<=1.25*diameter+aabb_excess or long<1.4*short or not .5*length<=long<=1.6*length:continue
            direction=[v*(-1 if minus<plus else 1) for v in n]
            candidates.append({'occurrence':occurrence,'constraint_id':c['id'],'assembly_reference':a,'component_reference':b,'head_side_root':direction,'head_extent_mm':short,'shaft_extent_mm':long,'nominal_diameter_mm':diameter,'nominal_length_mm':length,'normal_gap_mm':gap})
        if not candidates:raise ValueError('Native seating plane does not distinguish head and shaft')
        if any(sum(x*y for x,y in zip(candidates[0]['head_side_root'],c['head_side_root']))<.999 for c in candidates):raise ValueError('Conflicting seating planes')
        proof.append(candidates[0])
    if any(sum(x*y for x,y in zip(proof[0]['head_side_root'],p['head_side_root']))<.999 for p in proof):raise ValueError('Opposite heads require separate views')
    return {'schema_version':'native-headed-fastener-seat/v1','assembly_sha256':graph['assembly_sha256'],'contacts':proof,'head_side_root':proof[0]['head_side_root']}


def resolve_headed_fastener_side(payload,graph,*,bound_travel=False):
    proof=headed_fastener_side(payload,graph);p=deepcopy(payload)
    travel=math.sqrt(sum(v*v for v in p['translation_vector_root']))
    if travel<=0:raise ValueError('Missing presentation travel')
    if bound_travel:
        minimum=max(c['shaft_extent_mm']+.5*c['nominal_diameter_mm'] for c in proof['contacts'])
        maximum=max(minimum,*(max(1.5*c['nominal_length_mm'],3*c['nominal_diameter_mm']) for c in proof['contacts']))
        prior=travel;travel=max(minimum,min(travel,maximum))
        p['headed_fastener_travel_evidence']={'schema_version':'native-fastener-clearance-fit/v1','prior_travel_mm':prior,'travel_mm':travel,'minimum_withdrawal_mm':minimum,'maximum_presentation_mm':maximum,'basis':'Native seat-to-tip extent clears the joint; nominal thread size limits unnecessary presentation gap'}
    direction=proof['head_side_root'];vector=[v*travel for v in direction]
    contacts={c['occurrence']:c for c in proof['contacts']}
    p['translation_vector_root']=vector
    p['occurrence_translation_vectors_root']={o:vector[:] for o in p['moving_occurrences']}
    p['receiver_normal_root']=direction
    for a in p['arrow_anchors']:
        a['constraint_id']=contacts[a['occurrence_id']]['constraint_id']
        a['expected_exploded_point_root']=[x+y for x,y in zip(a['complete_point_root'],vector)]
    p['constraint_ids']=[c['constraint_id'] for c in proof['contacts']]
    seat_receivers=sorted({c['assembly_reference']['occurrence_id'] for c in proof['contacts']})
    seat_visible=all(any(r==a or r.startswith(a+'/') for a in p['visible_occurrences']) and not any(r==a or r.startswith(a+'/') for a in p.get('bom_visibility_exclusions',[])) for r in seat_receivers)
    if seat_visible:
        p['receiver_occurrences']=seat_receivers
        p['receiver_interface_selections']=[{k:c['assembly_reference'][k] for k in ('occurrence_id','item_id','item_type')} for c in proof['contacts']]
    nodes={n['occurrence_id']:n for n in graph['occurrences']};first=p['receiver_occurrences'][0]
    for receiver in p['receiver_occurrences']:
        if receiver not in p['visible_occurrences']:
            if not any(receiver.startswith(a+'/') for a in p['visible_occurrences']):raise ValueError('Proven seat receiver outside current stage')
            p['visible_occurrences'].append(receiver)
            p['stage_geometry_root']['context_bounds'].append({k:nodes[receiver]['bounds_root'][k] for k in ('min','max')})
    context_ids=[o for o in p['visible_occurrences'] if o not in p['moving_occurrences']]
    p['connection_camera']={'schema_version':'contact-interior-oblique/v1','foundation_occurrence':first,'foundation_bounds':deepcopy(p['stage_geometry_root']['context_bounds'][context_ids.index(first)])}
    p['headed_fastener_side_evidence']=proof
    from .installation_view import interface_view
    from .material_annotations import attach_material_numbers
    p=interface_view(p);a=p.get('material_annotations')
    if a:p=attach_material_numbers(p,page_id=a['page_id'],material_rows=a['material_rows'],label_mode='name',group_repeated_names=a.get('label_grouping')=='same-material-one-callout/v1')
    return p
