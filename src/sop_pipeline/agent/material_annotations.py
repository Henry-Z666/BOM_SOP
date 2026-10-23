"""Page-scoped material numbers, sharing native callout layout with torque."""
from copy import deepcopy
from pathlib import Path
import json
import math
from .process_model import fingerprint

STYLE = 'filled-cream-solid-dark/v1'

def attach_material_numbers(payload: dict, *, page_id: str, material_rows: list[dict], label_mode: str = 'name', group_repeated_names: bool = False) -> dict:
    """Rows are the ordered SOP page table, with explicit occurrence bindings."""
    if label_mode not in ('name','number'):raise ValueError('Unknown material label mode')
    if not page_id or not material_rows:raise ValueError('Material numbers require a fixed page table')
    if payload.get('image_coverage',{}).get('role')=='location':raise ValueError('Location numbers and material numbers use distinct view roles')
    result=deepcopy(payload);moving=set(result['moving_occurrences']);seen=set();labels=[];identities=set()
    for number,row in enumerate(material_rows,1):
        if not row.get('material_code') or not row.get('name'):raise ValueError('Material row identity missing')
        identity=(row['material_code'],row['name'])
        if identity in identities:raise ValueError('Page table must merge identical material rows before numbering')
        identities.add(identity)
        paths=[p for p in row['occurrence_ids'] if p in moving]
        if seen.intersection(paths) or len(paths)!=len(set(paths)):raise ValueError('Duplicate material occurrence binding')
        seen.update(paths)
        if paths:
            label={'text':row['name'] if label_mode=='name' else '物料 '+str(number),'connection_id':f'page-material-{number}',
                'occurrence_ids':paths,'material_code':row['material_code'],'material_name':row['name'],'table_number':number}
            if group_repeated_names and label_mode=='name' and len(paths)>1:
                # A material identity needs one readable callout, while the
                # native arrows and difference evidence still cover EVERY
                # installed instance. Do not hide or reduce physical parts.
                geometry=dict(zip(result['moving_occurrences'],result.get('stage_geometry_root',{}).get('moving_bounds',[])))
                centers={p:[(geometry[p]['min'][i]+geometry[p]['max'][i])/2 for i in range(3)] for p in paths if p in geometry}
                representative=paths[len(paths)//2]
                if len(centers)==len(paths):
                    middle=[sum(c[i] for c in centers.values())/len(paths) for i in range(3)]
                    representative=min(paths,key=lambda p:sum((centers[p][i]-middle[i])**2 for i in range(3)))
                label.update(text=row['name']+f'；本图{len(paths)}件',occurrence_ids=[representative],represents_occurrence_ids=paths)
            labels.append(label)
    if seen!=moving:raise ValueError('Material labels must cover every moving occurrence')
    contract={'schema_version':'material-annotations/v1','status':'confirmed','renderer':'creo_display_list/v1',
        'page_id':page_id,'style':STYLE,'material_rows':deepcopy(material_rows),'labels':labels}
    if label_mode=='name':contract['label_mode']='name'
    if group_repeated_names:contract['label_grouping']='same-material-one-callout/v1'
    contract['contract_hash']=fingerprint(contract);result['material_annotations']=contract
    channels={}
    for kind,key in [('material','material_annotations'),('torque','torque_annotations'),('note','installation_notes')]:
        source=result.get(key)
        if source and source.get('labels'):
            if source.get('status')!='confirmed':raise ValueError('Unconfirmed annotation channel')
            channels[kind]=deepcopy(source)
    from .installation_view import installation_feature_span
    radius=installation_feature_span(result);protected=[]
    anchors=[a['complete_point_root'] for a in result.get('arrow_anchors',[]) if a['occurrence_id'] in moving]
    for box in result.get('stage_geometry_root',{}).get('context_bounds',[]):
        for center in anchors:
            lo=[max(box['min'][i],center[i]-radius) for i in range(3)]
            hi=[min(box['max'][i],center[i]+radius) for i in range(3)]
            if all(lo[i]<=hi[i] for i in range(3)):protected.append({'min':lo,'max':hi})
    bundle={'schema_version':'installation-notes/v1','status':'confirmed','renderer':'creo_display_list/v1',
        'purpose':'material_and_process_annotations','style':STYLE,'channels':channels,'labels':[],
        'protected_bounds':protected,'context_protection':'cad-anchor-neighborhood/v1','context_radius':radius}
    for kind,source in channels.items():
        for label in source['labels']:
            entry=deepcopy(label);entry['connection_id']=kind+'|'+label['connection_id'];bundle['labels'].append(entry)
    bundle['contract_hash']=fingerprint(bundle);result['annotation_bundle']=bundle
    return result

def annotation_channel_audit(image: Path,payload: dict,kind: str) -> dict:
    bundle=payload['annotation_bundle']
    if bundle['contract_hash']!=fingerprint({k:v for k,v in bundle.items() if k!='contract_hash'}):raise ValueError('Changed annotation bundle')
    audit=json.loads(Path(str(image)+'.note.json').read_text(encoding='utf-8'))
    if audit.get('contract_hash')!=bundle['contract_hash'] or audit.get('schema_version')!='installation-note-audit/v1' or audit.get('overlap_check')!='passed':raise ValueError('Invalid shared annotation audit')
    prefix=kind+'|';labels=[]
    for label in audit['labels']:
        if label['connection_id'].startswith(prefix):
            entry=dict(label);entry['connection_id']=entry['connection_id'][len(prefix):];labels.append(entry)
    result=dict(audit);result['labels']=labels;result['contract_hash']=bundle['channels'][kind]['contract_hash']
    if kind=='torque':result['schema_version']='torque-label-audit/v1'
    return result

def validate_material_audit(image: Path,payload: dict) -> tuple[str,...]:
    contract=payload.get('material_annotations')
    if not contract:return ()
    try:
        if contract['contract_hash']!=fingerprint({k:v for k,v in contract.items() if k!='contract_hash'}) or contract.get('style')!=STYLE:raise ValueError('Changed material table')
        if payload['annotation_bundle']['channels']['material']!=contract:raise ValueError('Changed material channel')
        rebuilt=attach_material_numbers(payload,page_id=contract['page_id'],material_rows=contract['material_rows'],label_mode=contract.get('label_mode','number'),group_repeated_names=contract.get('label_grouping')=='same-material-one-callout/v1')
        if rebuilt['material_annotations']!=contract:raise ValueError('Material number differs from page row')
        audit=annotation_channel_audit(image,payload,'material')
        if audit.get('style')!=STYLE:raise ValueError('Material style differs from torque style')
        expected=sorted((p,l['connection_id'],l['text']) for l in contract['labels'] for p in l['occurrence_ids'])
        actual=sorted((l['occurrence_id'],l['connection_id'],l['text']) for l in audit['labels'])
        if expected!=actual:return ('MATERIAL_LABEL_COVERAGE_MISMATCH',)
        roi=None
        if payload.get('installation_view',{}).get('coordinate_adapter')=='cad_roi_screen_transform/v1':
            roi=json.loads(Path(str(image)+'.roi.json').read_text(encoding='utf-8'))['camera_plane_to_final_pixels']
            if len(roi)!=4 or not all(math.isfinite(x) for x in roi) or roi[0]<=0 or roi[1]>=0:raise ValueError('Invalid material label mapping')
        failures=[]
        for label in audit['labels']:
            if label['anchor_source']!='native_solid_surface':raise ValueError('Material number requires moving surface')
            for key,size in [('target_root',3),('text_box_camera',4)]:
                v=label[key]
                if len(v)!=size or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in v):raise ValueError('Invalid native label geometry')
            if min(label['text_box_camera'][2:])<=0:raise ValueError('Empty material label')
            if roi:
                x,y,w,h=label['text_box_camera'];sx,sy,ox,oy=roi
                pixels=[sx*x+ox,sx*(x+w)+ox,sy*y+oy,sy*(y+h)+oy]
                if min(pixels)<16 or max(pixels)>1584:failures.append('MATERIAL_LABEL_CLIPPED')
                if min(abs(sx*w),abs(sy*h))<24:failures.append('MATERIAL_LABEL_TOO_SMALL')
        return tuple(dict.fromkeys(failures))
    except (OSError,ValueError,KeyError,TypeError):return ('MATERIAL_LABEL_EVIDENCE_INVALID',)
