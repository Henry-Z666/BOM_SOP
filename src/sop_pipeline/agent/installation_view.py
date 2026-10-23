"""Native contact-focused composition, independent of complete-part framing."""
from copy import deepcopy
import math
import json
import hashlib

CURRENT_FRAMING_POLICY = 'single-image-balanced-fit/v15'
MIN_FEATURE_FRAME_FRACTION = .15
DETAIL_FRAME_FRACTION = .65
FEATURE_SPREAD_LIMIT = 4.0  # conservative round-down of .65/.15

def with_native_connection_camera(payload: dict) -> dict:
    """Bind contact-camera alternatives to an already visible native receiver."""
    out=deepcopy(payload)
    if out.get('connection_camera'):return out
    context=[o for o in out['visible_occurrences'] if o not in out['moving_occurrences']]
    bounds=out['stage_geometry_root']['context_bounds']
    if len(context)!=len(bounds):raise ValueError('Incomplete native context bounds')
    candidates=[o for o in context if any(r==o or r.startswith(o+'/') for r in out['receiver_occurrences'])]
    if not candidates:raise ValueError('Receiver is not covered by a visible native context')
    foundation=max(candidates,key=lambda o:len(o.split('/')))
    out['connection_camera']={'schema_version':'contact-interior-oblique/v1','foundation_occurrence':foundation,'foundation_bounds':deepcopy(bounds[context.index(foundation)])}
    return out

def _bounds_span(bounds):
    """Union size includes dispersed occurrences, not just one representative."""
    if not bounds:return 0.
    return max(max(float(b['max'][i]) for b in bounds)-min(float(b['min'][i]) for b in bounds) for i in range(3))

def interface_view(payload: dict, *, fit_scope: str = 'auto') -> dict:
    result=deepcopy(payload)
    requested_fraction=result.get('installation_view',{}).get('roi_frame_fraction')
    if requested_fraction is not None and (not math.isfinite(float(requested_fraction)) or not .5<=float(requested_fraction)<=.9):raise ValueError('Invalid detail ROI frame fraction')
    normal=[float(x) for x in result['receiver_normal_root']]
    length=math.sqrt(sum(x*x for x in normal))
    if not math.isfinite(length) or length<1e-8:raise ValueError('Interface normal is invalid')
    normal=[x/length for x in normal]
    # A shallow oblique view reveals the contact face and insertion direction.
    up=[0.,1.,0.] if abs(normal[1])<.90 else [0.,0.,1.]
    tangent=[up[1]*normal[2]-up[2]*normal[1],up[2]*normal[0]-up[0]*normal[2],up[0]*normal[1]-up[1]*normal[0]]
    tl=math.sqrt(sum(x*x for x in tangent));tangent=[x/tl for x in tangent]
    direction=[.70*n+.65*t+.25*u for n,t,u in zip(normal,tangent,up)]
    dl=math.sqrt(sum(x*x for x in direction));direction=[x/dl for x in direction]
    connection_camera=result.get('connection_camera')
    if connection_camera:
        if connection_camera.get('schema_version')!='contact-interior-oblique/v1':raise ValueError('Unknown connection camera evidence')
        foundation=connection_camera['foundation_occurrence']
        context_ids=[o for o in result['visible_occurrences'] if o not in result['moving_occurrences']]
        if foundation not in context_ids:raise ValueError('Connection foundation is not visible')
        box=result['stage_geometry_root']['context_bounds'][context_ids.index(foundation)]
        if box!=connection_camera['foundation_bounds']:raise ValueError('Connection camera bounds differ from CAD context')
        anchors=[a['complete_point_root'] for a in result['arrow_anchors']]
        point=[sum(a[i] for a in anchors)/len(anchors) for i in range(3)]
        toward=[(box['min'][i]+box['max'][i])*.5-point[i] for i in range(3)]
        ts=-1 if sum(a*b for a,b in zip(tangent,toward))<0 else 1
        us=-1 if sum(a*b for a,b in zip(up,toward))<0 else 1
        # A contact-backed lateral clearance move can differ from the receiver
        # normal. Observe that moving side so the receiver cannot hide it.
        travel_vector=result['translation_vector_root']
        lateral_t=sum(a*b for a,b in zip(tangent,travel_vector))
        lateral_u=sum(a*b for a,b in zip(up,travel_vector))
        if abs(lateral_t)>1.e-7:ts=-1 if lateral_t<0 else 1
        if abs(lateral_u)>1.e-7:us=-1 if lateral_u<0 else 1
        quadrant=connection_camera.get('view_quadrant',0)
        if quadrant not in (0,1,2,3):raise ValueError('Invalid contact view quadrant')
        if quadrant&1:ts=-ts
        if quadrant&2:us=-us
        oblique_scale=1.
        normal_weight=float(connection_camera.get('normal_weight',.70))
        if not math.isfinite(normal_weight) or not .3<=normal_weight<=1.5:raise ValueError('Invalid contact camera normal weight')
        scale_policy=connection_camera.get('scale_policy')
        if scale_policy:
            if scale_policy not in ('projected-travel-to-feature/v1','projected-travel-to-feature/v2'):raise ValueError('Unknown connection scale policy')
            travel=math.sqrt(sum(float(v)**2 for v in result['translation_vector_root']))
            feature=installation_feature_span(result)
            lateral=math.hypot(.65,.25)
            default_sine=lateral/math.hypot(normal_weight,lateral)
            feature_multiple=2. if scale_policy=='projected-travel-to-feature/v2' else 4.
            target_sine=min(default_sine,feature_multiple*feature/max(travel,1.e-9))
            oblique_scale=normal_weight*target_sine/(lateral*math.sqrt(max(1.e-9,1-target_sine**2)))
        normal_side=connection_camera.get('normal_side',1)
        if normal_side not in (-1,1):raise ValueError('Invalid contact camera normal side')
        direction=[normal_weight*normal_side*n+oblique_scale*(.65*ts*t+.25*us*u) for n,t,u in zip(normal,tangent,up)]
        dl=math.sqrt(sum(x*x for x in direction));direction=[x/dl for x in direction]
        # Near-vertical insertion cannot use the same axis as camera up.
        # A stable Z up avoids arbitrary roll in washer/cover installation views.
        up=[0.,0.,1.] if abs(direction[1])>.90 else [0.,1.,0.]
    geometry=result.get('stage_geometry_root',{})
    coverage=result.get('image_coverage',{})
    if coverage.get('role')=='location':
        reference_span=max((_bounds_span([b]) for b in geometry.get('context_bounds',[])),default=0.)
        coverage['positioning_context_status']='available' if reference_span>=.5*_bounds_span(geometry.get('moving_bounds',[])) else 'insufficient_continuous_visible_context'
    policy=result.get('installation_view',{}).get('composition_policy')
    generalized=policy in ('single-image-anchor-fit/v2','single-image-balanced-fit/v3','single-image-balanced-fit/v4','single-image-balanced-fit/v5','single-image-balanced-fit/v6','single-image-balanced-fit/v7','single-image-balanced-fit/v8','single-image-balanced-fit/v9','single-image-balanced-fit/v10','single-image-balanced-fit/v11','single-image-balanced-fit/v12','single-image-balanced-fit/v13','single-image-balanced-fit/v14','single-image-balanced-fit/v15')
    if generalized:
        bounds=geometry.get('moving_bounds',[])
        if len(bounds)>1:
            extents=[max(float(b['max'][i]) for b in bounds)-min(float(b['min'][i]) for b in bounds) for i in range(3)]
            axis=max(range(3),key=lambda i:extents[i])
            other=max(extents[i] for i in range(3) if i!=axis)
            if extents[axis]>4*max(other,1.e-9):
                long_axis=[float(i==axis) for i in range(3)]
                projection=sum(a*n for a,n in zip(long_axis,normal))
                along=[a-projection*n for a,n in zip(long_axis,normal)]
                al=math.sqrt(sum(x*x for x in along))
                if al>.5:
                    direction=[.48*n+.86*a/al+.08*u for n,a,u in zip(normal,along,up)]
                    dl=math.sqrt(sum(x*x for x in direction));direction=[x/dl for x in direction]
    span=_bounds_span(geometry.get('moving_bounds',[]))
    context_span=_bounds_span(geometry.get('context_bounds',[]))
    if fit_scope=='auto':
        individual_span=max((_bounds_span([b]) for b in geometry.get('moving_bounds',[])),default=0.)
        travel=math.sqrt(sum(float(x)**2 for x in result['translation_vector_root']))
        fit_scope='moving_interfaces' if individual_span>6*max(travel,1.) else 'moving_only'
    if fit_scope not in ('moving_only','moving_interfaces'):raise ValueError('Invalid interface fit scope')
    # Bounded native margin: more context around small parts, without fitting
    # the complete receiver. Large/dispersed groups retain their union scope.
    ratio=context_span/max(span,1.e-9)
    expansion=min(2.4,max(1.35,1.35+.25*math.log2(max(ratio,1.))))
    subject_scale=float(result.get('installation_view',{}).get('subject_scale',1.))
    if not math.isfinite(subject_scale) or not 1<=subject_scale<=3:raise ValueError('Invalid subject scale')
    level=round(min(1.45,.85/expansion*subject_scale),6)
    if generalized:
        fit_scope='balanced_surfaces' if policy in ('single-image-balanced-fit/v3','single-image-balanced-fit/v4','single-image-balanced-fit/v5','single-image-balanced-fit/v6','single-image-balanced-fit/v7','single-image-balanced-fit/v8','single-image-balanced-fit/v9','single-image-balanced-fit/v10','single-image-balanced-fit/v11','single-image-balanced-fit/v12','single-image-balanced-fit/v13','single-image-balanced-fit/v14','single-image-balanced-fit/v15') else 'anchor_surfaces';level=.45 if result.get('installation_notes',{}).get('labels') else (.65 if result.get('torque_annotations',{}).get('labels') else .95);subject_scale=1.
        if policy=='single-image-balanced-fit/v4' and not result.get('installation_notes',{}).get('labels'):level=.62 if not result.get('torque_annotations',{}).get('labels') else .55
    if policy in ('single-image-balanced-fit/v5','single-image-balanced-fit/v6','single-image-balanced-fit/v7','single-image-balanced-fit/v8','single-image-balanced-fit/v9','single-image-balanced-fit/v10','single-image-balanced-fit/v11','single-image-balanced-fit/v12','single-image-balanced-fit/v13','single-image-balanced-fit/v14','single-image-balanced-fit/v15'):
        # Large receivers need a readable contact detail; comparable parts need more surrounding space.
        level=round(.50+.32*min(1.,max(0.,math.log2(max(ratio,1.))/6.)),6)
        if result.get('installation_notes',{}).get('labels'):level=.35
        elif result.get('torque_annotations',{}).get('labels'):level=min(level,.55)
    if policy in ('single-image-balanced-fit/v6','single-image-balanced-fit/v7','single-image-balanced-fit/v8','single-image-balanced-fit/v9','single-image-balanced-fit/v10','single-image-balanced-fit/v11','single-image-balanced-fit/v12','single-image-balanced-fit/v13','single-image-balanced-fit/v14','single-image-balanced-fit/v15'):fit_scope='protected_surfaces'
    if policy in ('single-image-balanced-fit/v7','single-image-balanced-fit/v8','single-image-balanced-fit/v9','single-image-balanced-fit/v10','single-image-balanced-fit/v11','single-image-balanced-fit/v12','single-image-balanced-fit/v13','single-image-balanced-fit/v14','single-image-balanced-fit/v15') and len(result.get('moving_occurrences',[]))>1:fit_scope='protected_groups'
    if policy in ('single-image-balanced-fit/v10','single-image-balanced-fit/v11','single-image-balanced-fit/v12','single-image-balanced-fit/v13','single-image-balanced-fit/v14','single-image-balanced-fit/v15') and result.get('image_coverage',{}).get('role')=='detail':fit_scope='protected_surfaces'
    if policy in ('single-image-balanced-fit/v9','single-image-balanced-fit/v10','single-image-balanced-fit/v11','single-image-balanced-fit/v12','single-image-balanced-fit/v13','single-image-balanced-fit/v14','single-image-balanced-fit/v15'):
        result['arrow_evidence']='native-background-difference/v1'
        if fit_scope=='protected_groups' and not result.get('installation_notes',{}).get('labels') and not result.get('torque_annotations',{}).get('labels'):level=round(.75+.20*min(1.,max(0.,math.log2(max(ratio,1.))/6.)),6)
    result['installation_view']={'schema_version':'installation-interface-view/v1','fit_scope':fit_scope,'allow_remote_body_clipping':True,'focus':'native_contact_geometry','camera_source':'receiver_normal_root','whole_part_required':False,'overview_when_location_ambiguous':False,'image_count':1,'composition_policy':'single-image-context-margin/v1','moving_union_span_mm':span,'receiver_union_span_mm':context_span,'context_expansion':expansion,'selected_fit_level':level}
    if 'native_selected_fit' in result['presentation']:
        result['presentation']['native_selected_fit']['zoom_to_selected_level']=level
    result['installation_view']['subject_scale']=subject_scale
    if requested_fraction is not None:result['installation_view']['roi_frame_fraction']=float(requested_fraction)
    if generalized:result['installation_view']['composition_policy']=policy
    if policy in ('single-image-balanced-fit/v11','single-image-balanced-fit/v12','single-image-balanced-fit/v13','single-image-balanced-fit/v14','single-image-balanced-fit/v15') and result.get('image_coverage',{}).get('role')=='detail':
        result['installation_view']['coordinate_adapter']='cad_roi_screen_transform/v1'
        result['installation_view']['roi_feature_span']=installation_feature_span(result)
    if policy in ('single-image-balanced-fit/v12','single-image-balanced-fit/v13','single-image-balanced-fit/v14','single-image-balanced-fit/v15') and result.get('image_coverage',{}).get('role')=='location':
        result['installation_view']['coordinate_adapter']='cad_location_screen_transform/v1'
        result['installation_view']['roi_feature_span']=installation_feature_span(result)
    if policy in ('single-image-balanced-fit/v14','single-image-balanced-fit/v15'):
        if result.get('image_coverage',{}).get('role')!='location':result['moving_visibility_evidence']='native-occurrence-difference/v1'
        else:result.pop('moving_visibility_evidence',None)
    camera={'id':'interface_oblique','position_direction_root':direction,'up_reference_root':up,'zoom':1.,'pan':[0.,0.],'frame':'native_graphics_area'}
    result['camera_id']='interface_oblique';result['camera']=camera
    result['allowed_camera_ids']=['fixed_123','fixed_456','interface_oblique']
    result.setdefault('camera_catalog',{})['interface_oblique']=camera
    for variant in result['presentation']['variants']:variant['camera_id']='interface_oblique'
    result['presentation']['frame_gate']['max_clipped_edges']=4
    # Judge the local fitting, not the percentage occupied by a complete beam.
    result['presentation']['frame_gate']['min_subject_span']=.18
    result['arrow_color']='red'
    return result

def generalized_view(payload: dict, *, policy: str = CURRENT_FRAMING_POLICY) -> dict:
    if policy not in ('single-image-balanced-fit/v10','single-image-balanced-fit/v11','single-image-balanced-fit/v12','single-image-balanced-fit/v13','single-image-balanced-fit/v14','single-image-balanced-fit/v15'):raise ValueError('Unsupported generalized framing policy')
    result=deepcopy(payload)
    result['installation_view']={'composition_policy':policy}
    if policy in ('single-image-balanced-fit/v11','single-image-balanced-fit/v12','single-image-balanced-fit/v13','single-image-balanced-fit/v14','single-image-balanced-fit/v15') and result.get('image_coverage',{}).get('role')!='location':result=resolve_display_side(result)
    if policy in ('single-image-balanced-fit/v14','single-image-balanced-fit/v15') and result.get('image_coverage',{}).get('role')!='location':result=clear_display_translation(result)
    result=bounded_display_translation(result)
    return interface_view(result)


def installation_image_roles(payload: dict, *, policy: str | None = None) -> list[str]:
    """Split positioning/detail when distributed parts cannot share a useful scale.

    Only selects image roles; never removes occurrences or assumes equivalent work.
    Representative detail coverage requires a separately confirmed group mapping.
    """
    bounds=payload.get('stage_geometry_root',{}).get('moving_bounds',[])
    individual=installation_feature_span(payload)
    ratio=_bounds_span(bounds)/max(individual,1.e-9)
    policy=policy or payload.get('installation_view',{}).get('composition_policy',CURRENT_FRAMING_POLICY)
    dual=bool(bounds) and ratio>FEATURE_SPREAD_LIMIT if policy in ('single-image-balanced-fit/v13','single-image-balanced-fit/v14','single-image-balanced-fit/v15') else len(bounds)>1 and ratio>10
    return ['location','detail'] if dual else ['installation']


def expand_installation_views(payload: dict, *, policy: str = CURRENT_FRAMING_POLICY, force_location: bool = False) -> list[dict]:
    """Diagnostic view expansion; preserve each original occurrence exactly once in details.

    Never infers equivalent work or representative quantities. Formal publisher must
    accept the full group before this expansion can replace an operation image.
    """
    original=deepcopy(payload)
    moving=original['moving_occurrences']
    bounds=original.get('stage_geometry_root',{}).get('moving_bounds',[])
    if not force_location and installation_image_roles(original,policy=policy)==['installation']:return [generalized_view(original,policy=policy)]
    if len(bounds)!=len(moving):raise ValueError('Multi-view expansion requires complete native bounds')
    extent=installation_feature_span(original)
    anchor_map={a['occurrence_id']:a['complete_point_root'] for a in original.get('arrow_anchors',[])}
    centers=[anchor_map[o] for o in moving] if all(o in anchor_map for o in moving) else [[.5*(float(b['min'][a])+float(b['max'][a])) for a in range(3)] for b in bounds]
    groups=[]
    for i,c in enumerate(centers):
        group=next((g for g in groups if all(math.dist(c,centers[j])<=4*max(extent,1.e-9) for j in g)),None)
        if group is None:groups.append([i])
        else:group.append(i)
    location=generalized_view(original,policy=policy)
    context=original.get('stage_geometry_root',{}).get('context_bounds',[])
    reference_span=max((_bounds_span([b]) for b in context),default=0.)
    context_status='available' if reference_span>=.5*_bounds_span(bounds) else 'insufficient_continuous_visible_context'
    location['image_coverage']={'schema_version':'installation-view-coverage/v1','role':'location','operation_occurrences':moving[:],'display_occurrences':moving[:],'detail_groups':[[moving[i] for i in g] for g in groups],'publication_status':'pending_group_review','positioning_context_status':context_status}
    if not original.get('torque_annotations',{}).get('labels') and not original.get('installation_notes',{}).get('labels'):
        notes={'schema_version':'installation-notes/v1','status':'confirmed','renderer':'creo_display_list/v1',
            'purpose':'spatial_group_indices_from_cad','number_kind':'position','style':'filled-cream-solid-dark/v1','labels':[{'text':'位置 '+str(n+1),'connection_id':'spatial-group-'+str(n+1),'occurrence_ids':[moving[g[0]]]} for n,g in enumerate(groups)],
            'protected_bounds':original.get('stage_geometry_root',{}).get('context_bounds',[])}
        if policy=='single-image-balanced-fit/v15':
            notes.update(anchor_state='authoritative_complete',layout_policy='ordered-perimeter-lane/v1')
        notes['contract_hash']='sha256:'+hashlib.sha256(json.dumps(notes,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()
        location['installation_notes']=notes;location=generalized_view(location,policy=policy)
    views=[location]
    for group in groups:
        detail=deepcopy(original);selected=[moving[i] for i in group];selected_set=set(selected)
        detail['moving_occurrences']=selected
        detail['visible_occurrences']=[o for o in original['visible_occurrences'] if o not in set(moving) or o in selected_set]
        detail['stage_geometry_root']['moving_bounds']=[bounds[i] for i in group]
        detail['occurrence_translation_vectors_root']={o:original['occurrence_translation_vectors_root'][o] for o in selected}
        detail['arrow_anchors']=[a for a in original['arrow_anchors'] if a['occurrence_id'] in selected_set]
        detail['constraint_ids']=[a['constraint_id'] for a in detail['arrow_anchors']]
        detail['receiver_point_root']=detail['arrow_anchors'][0]['complete_point_root']
        for key in ('torque_annotations','installation_notes'):
            if original.get(key,{}).get('labels'):raise ValueError('Annotated multi-view expansion requires reviewed annotation rebinding')
        detail['image_coverage']={'schema_version':'installation-view-coverage/v1','role':'detail','operation_occurrences':moving[:],'display_occurrences':selected,'total_locations':len(moving),'detail_group_index':len(views),'detail_group_count':len(groups),'publication_status':'pending_group_review'}
        detail['execution_mode']='diagnostic_preview';detail['production_eligible']=False
        views.append(generalized_view(detail,policy=policy))
    return views


def bounded_display_translation(payload: dict) -> dict:
    """Shorten presentation travel only with complete conservative clearance evidence."""
    result=deepcopy(payload);geometry=result.get('stage_geometry_root',{})
    moving=result.get('moving_occurrences',[]);bounds=geometry.get('moving_bounds',[]);context=geometry.get('context_bounds',[])
    visible_context=set(result.get('visible_occurrences',[]))-set(moving)
    if not moving or len(bounds)!=len(moving) or not visible_context or len(context)!=len(visible_context):return result
    vector=result['translation_vector_root'];length=math.sqrt(sum(float(x)**2 for x in vector))
    if length<=1.e-9:return result
    if any(any(abs(float(a)-float(b))>1.e-6 for a,b in zip(result['occurrence_translation_vectors_root'].get(o,[]),vector)) or len(result['occurrence_translation_vectors_root'].get(o,[]))!=3 for o in moving):return result
    direction=[float(x)/length for x in vector]
    thickness=max(sum(abs(direction[a])*(float(b['max'][a])-float(b['min'][a])) for a in range(3)) for b in bounds)
    distance=1.5*thickness
    if thickness<=1.e-9 or distance>=length*.99:return result
    candidate=[x*distance for x in direction]
    for b in bounds:
        for c in context:
            if all(min(float(b['max'][a])+candidate[a],float(c['max'][a]))-max(float(b['min'][a])+candidate[a],float(c['min'][a]))>1.e-8 for a in range(3)):return result
    result['presentation_translation']={'schema_version':'clearance-bounded-display-translation/v1','original_vector_root':vector[:],'original_distance':length,'display_distance':distance,'axis_thickness':thickness,'clearance_evidence':'all_visible_context_aabbs','rotation_unchanged':True}
    result['translation_vector_root']=candidate
    result['occurrence_translation_vectors_root']={o:candidate[:] for o in moving}
    for anchor in result.get('arrow_anchors',[]):anchor['expected_exploded_point_root']=[float(x)+candidate[a] for a,x in enumerate(anchor['complete_point_root'])]
    return result


def installation_feature_span(payload: dict) -> float:
    """Treat long/thin parts by their measured installation-normal thickness."""
    bounds=payload.get('stage_geometry_root',{}).get('moving_bounds',[])
    body=max((_bounds_span([b]) for b in bounds),default=0.)
    normal=payload.get('receiver_normal_root',[0,0,0]);length=math.sqrt(sum(float(x)**2 for x in normal))
    if length<=1.e-9:return body
    normal=[float(x)/length for x in normal]
    thickness=max((sum(abs(normal[a])*(float(b['max'][a])-float(b['min'][a])) for a in range(3)) for b in bounds),default=0.)
    return min(body,2*thickness) if thickness>1.e-9 else body


def resolve_display_side(payload: dict) -> dict:
    """Choose the opposite display side only when every receiver AABB clears it.

    This changes a diagnostic display pose, never the installed CAD pose.
    An intersecting AABB means clearance is unproven, not physical collision.
    """
    out=deepcopy(payload);g=out.get('stage_geometry_root',{})
    moving=out.get('moving_occurrences',[]);boxes=g.get('moving_bounds',[]);context=g.get('context_bounds',[])
    if not moving or len(boxes)!=len(moving) or len(context)!=len(set(out.get('visible_occurrences',[]))-set(moving)) or not context:return out
    vector=out.get('translation_vector_root',[0,0,0]);length=math.sqrt(sum(float(x)**2 for x in vector))
    if length<=1.e-9:return out
    axis=[float(x)/length for x in vector]
    normal=out.get('receiver_normal_root',[0,0,0]);normal_length=math.sqrt(sum(float(x)**2 for x in normal))
    if normal_length<=1.e-9 or sum(axis[a]*float(normal[a])/normal_length for a in range(3))<.99:return out
    if any(len(out.get('occurrence_translation_vectors_root',{}).get(o,[]))!=3 or any(abs(float(v)-float(vector[a]))>1.e-6 for a,v in enumerate(out['occurrence_translation_vectors_root'][o])) for o in moving):return out
    def clear(v):return not any(all(min(float(b['max'][a])+v[a],float(c['max'][a]))-max(float(b['min'][a])+v[a],float(c['min'][a]))>1.e-8 for a in range(3)) for b in boxes for c in context)
    if clear(vector):return out
    # Require a consistent receiver side, not a nearest-pixel guess.
    if any(sum(axis[a]*((float(b['min'][a])+float(b['max'][a]))-(float(c['min'][a])+float(c['max'][a])))/2 for a in range(3))>=0 for b in boxes for c in context):return out
    thickness=max(sum(abs(axis[a])*(float(b['max'][a])-float(b['min'][a])) for a in range(3)) for b in boxes)
    distance=max(length,1.5*thickness)
    if distance>2*length:return out
    alternate=[-x*distance for x in axis]
    if not clear(alternate):return out
    out['display_side_resolution']={'schema_version':'receiver-side-clearance/v1','original_normal_root':normal[:],'original_vector_root':vector[:],'display_vector_root':alternate[:],'evidence':'all_visible_context_aabbs','installed_pose_unchanged':True,'original_clearance':'unproven'}
    out['receiver_normal_root']=[-float(x) for x in normal]
    out['translation_vector_root']=alternate
    out['occurrence_translation_vectors_root']={o:alternate[:] for o in moving}
    for anchor in out.get('arrow_anchors',[]):anchor['expected_exploded_point_root']=[float(x)+alternate[a] for a,x in enumerate(anchor['complete_point_root'])]
    return out


def clear_display_translation(payload: dict) -> dict:
    """Resolve conservative AABB clearance intervals along the signed mount axis."""
    out=deepcopy(payload);g=out.get('stage_geometry_root',{});moving=out.get('moving_occurrences',[])
    boxes=g.get('moving_bounds',[]);context=g.get('context_bounds',[])
    if not moving or len(boxes)!=len(moving) or not context or len(context)!=len(set(out.get('visible_occurrences',[]))-set(moving)):return out
    vector=out.get('translation_vector_root',[0,0,0]);length=math.sqrt(sum(float(x)**2 for x in vector))
    if length<=1.e-9:return out
    axis=[float(x)/length for x in vector];normal=out['receiver_normal_root'];norm=math.sqrt(sum(float(x)**2 for x in normal))
    if norm<=1.e-9 or sum(axis[a]*float(normal[a])/norm for a in range(3))<.99:return out
    if any(len(out.get('occurrence_translation_vectors_root',{}).get(o,[]))!=3 or any(abs(float(v)-float(vector[a]))>1.e-6 for a,v in enumerate(out['occurrence_translation_vectors_root'][o])) for o in moving):return out
    def clear(v):return not any(all(min(float(b['max'][a])+v[a],float(c['max'][a]))-max(float(b['min'][a])+v[a],float(c['min'][a]))>1.e-8 for a in range(3)) for b in boxes for c in context)
    if clear(vector):return out
    feature=installation_feature_span(out)
    thickness=max(sum(abs(axis[a])*(float(b['max'][a])-float(b['min'][a])) for a in range(3)) for b in boxes)
    options=[]
    for sign in (1,-1):
        u=[sign*x for x in axis];forbidden=[]
        for b in boxes:
            for c in context:
                low=-math.inf;high=math.inf
                for a in range(3):
                    if abs(u[a])<1.e-12:
                        if min(float(b['max'][a]),float(c['max'][a]))<=max(float(b['min'][a]),float(c['min'][a])):low=math.inf;break
                    else:
                        v1=(float(c['min'][a])-float(b['max'][a]))/u[a];v2=(float(c['max'][a])-float(b['min'][a]))/u[a]
                        low=max(low,min(v1,v2));high=min(high,max(v1,v2))
                if low<high and high>=0:forbidden.append((low,high))
        distance=max(length,1.5*thickness)
        for low,high in sorted(forbidden):
            if low<=distance<=high:distance=high+.25*feature
        candidate=[distance*x for x in u]
        if distance<=4*feature and clear(candidate):options.append((distance,sign,candidate))
    if not options:
        out['display_clearance_status']='unresolved_within_feature_bound';return out
    distance,sign,candidate=min(options,key=lambda v:(v[0],-v[1]))
    out['display_clearance_resolution']={'schema_version':'cad-axis-clearance-intervals/v1','original_vector_root':vector[:],'original_normal_root':normal[:],'display_vector_root':candidate[:],'display_distance':distance,'feature_span':feature,'maximum_distance':4*feature,'evidence':'all_visible_context_aabbs','installed_pose_unchanged':True}
    out['receiver_normal_root']=[sign*float(v) for v in normal];out['translation_vector_root']=candidate
    out['occurrence_translation_vectors_root']={o:candidate[:] for o in moving}
    for anchor in out.get('arrow_anchors',[]):anchor['expected_exploded_point_root']=[float(x)+candidate[a] for a,x in enumerate(anchor['complete_point_root'])]
    return out
