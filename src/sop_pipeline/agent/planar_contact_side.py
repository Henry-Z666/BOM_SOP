"""Resolve a thin part's free side from a local native contact, not a camera guess."""
from copy import deepcopy
import math

def resolve_planar_contact_side(payload,graph,*,bound_travel=False):
    if payload.get('image_coverage',{}).get('role')!='detail':raise ValueError('Contact-side repair requires a detail')
    p=deepcopy(payload);nodes={n['occurrence_id']:n for n in graph['occurrences']};evidence=[];directions=[];receivers=[];surfaces=[]
    for occurrence,box in zip(p['moving_occurrences'],p['stage_geometry_root']['moving_bounds'],strict=True):
        center=[(a+b)/2 for a,b in zip(box['min'],box['max'])];span=max(b-a for a,b in zip(box['min'],box['max']));candidates=[]
        for constraint in graph['constraints']:
            if constraint['occurrences'][0]!=occurrence or constraint.get('type') not in ('INSERT','MATE_OFF','ALIGN_OFF'):continue
            a=constraint.get('assembly_reference',{});c=constraint.get('component_reference',{});ag=a.get('geometry',{});cg=c.get('geometry',{})
            if ag.get('status')!='available' or cg.get('status')!='available' or a.get('item_type')!='SURFACE':continue
            normal=ag.get('direction_root');ap=ag.get('point_root');cp=cg.get('point_root')
            if not normal or not ap or not cp:continue
            length=math.sqrt(sum(x*x for x in normal))
            if length<1e-9:continue
            normal=[x/length for x in normal]
            thickness=sum(abs(normal[k])*(box['max'][k]-box['min'][k]) for k in range(3))
            if thickness<=1e-6 or thickness>.35*span:continue
            distance=math.dist(ap,cp)
            # Creo returns a representative point on each planar face, not
            # necessarily the same point of contact. A MATE can therefore
            # have a large tangential separation while the planes coincide.
            # INSERT cylinder origins still require the stricter local pair.
            if constraint['type'] in ('MATE_OFF','ALIGN_OFF'):
                receiver_box=nodes.get(a.get('occurrence_id'),{}).get('bounds_root',{})
                component_normal=cg.get('direction_root')
                if not component_normal or receiver_box.get('status')!='available':continue
                cl=math.sqrt(sum(x*x for x in component_normal))
                if cl<1e-9 or abs(sum(normal[k]*component_normal[k]/cl for k in range(3)))<.9999:continue
                if any(ap[k]<receiver_box['min'][k]-1e-4 or ap[k]>receiver_box['max'][k]+1e-4 or cp[k]<receiver_box['min'][k]-3 or cp[k]>receiver_box['max'][k]+3 for k in range(3)):continue
                distance=abs(sum((ap[k]-cp[k])*normal[k] for k in range(3)))
                if constraint['type']=='ALIGN_OFF' and abs(distance-abs(float(constraint.get('offset',0))))>1.e-4:continue
            if distance>max(3.,thickness*.25):continue # A remote height datum is not the local contact.
            if any(cp[k]<box['min'][k]-1e-4 or cp[k]>box['max'][k]+1e-4 for k in range(3)):continue
            side=sum((center[k]-cp[k])*normal[k] for k in range(3))
            if abs(side)<.25*thickness:continue # A mid-plane or cylinder origin cannot prove the free side.
            direction=[x*(1 if side>0 else -1) for x in normal]
            candidates.append((distance,constraint,a,direction,thickness,side))
        if not candidates:raise ValueError('No local native thin-part contact proves the free side')
        _,constraint,receiver,direction,thickness,side=min(candidates,key=lambda c:(c[0],c[1]['id']))
        directions.append(direction);receivers.append(receiver['occurrence_id']);surfaces.append({'occurrence_id':receiver['occurrence_id'],'item_id':receiver['item_id'],'item_type':'SURFACE'})
        evidence.append({'occurrence':occurrence,'constraint_id':constraint['id'],'assembly_reference':constraint['assembly_reference'],'component_reference':constraint['component_reference'],'normal_thickness':thickness,'body_center_signed_distance':side,'free_side_root':direction})
    direction=directions[0]
    if any(sum(a*b for a,b in zip(direction,n))<.9999 for n in directions):raise ValueError('Distinct free sides need separate views')
    travel=math.sqrt(sum(x*x for x in p['translation_vector_root']))
    if travel<=0:raise ValueError('Missing proven presentation travel')
    if bound_travel:
        travel=max(max(2*r['normal_thickness'] for r in evidence),.35*max(max(b['max'][k]-b['min'][k] for k in range(3)) for b in p['stage_geometry_root']['moving_bounds']))
    vector=[x*travel for x in direction]
    p['receiver_normal_root']=direction;p['translation_vector_root']=vector;p['occurrence_translation_vectors_root']={a:vector[:] for a in p['moving_occurrences']}
    by_occ={r['occurrence']:r for r in evidence}
    for anchor in p['arrow_anchors']:
        anchor['constraint_id']=by_occ[anchor['occurrence_id']]['constraint_id'];anchor['expected_exploded_point_root']=[a+b for a,b in zip(anchor['complete_point_root'],vector)]
    p['constraint_ids']=[r['constraint_id'] for r in evidence];p['receiver_occurrences']=sorted(set(receivers));p['receiver_interface_selections']=surfaces
    first=p['receiver_occurrences'][0]
    p['connection_camera']={'schema_version':'contact-interior-oblique/v1','foundation_occurrence':first,'foundation_bounds':{k:nodes[first]['bounds_root'][k] for k in ('min','max')}}
    p['contact_side_evidence']={'schema_version':'native-planar-contact-side/v1','assembly_sha256':graph['assembly_sha256'],'basis':'Local paired contact points near a boundary face, native axis and thin-body side; no inferred CAD pose change','contacts':evidence,'prior_translation_vector_root':payload['translation_vector_root']}
    from .installation_view import interface_view
    from .material_annotations import attach_material_numbers
    p=interface_view(p)
    annotation=p.get('material_annotations')
    if annotation:p=attach_material_numbers(p,page_id=annotation['page_id'],material_rows=annotation['material_rows'],label_mode='name',group_repeated_names=annotation.get('label_grouping')=='same-material-one-callout/v1')
    return p
