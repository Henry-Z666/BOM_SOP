"""BOM issued-boundary checks shared by stage publication runners."""
from typing import Any
from .bom_normalizer import validate_color_boundary_rows
from functools import lru_cache
from pathlib import Path
import hashlib
from .headed_fastener_side import validate_proven_headed_directions

@lru_cache(maxsize=4)
def _source_quantities(path: str, digest: str):
    from .bom_normalizer import normalize_bom
    source=Path(path)
    if hashlib.sha256(source.read_bytes()).hexdigest()!=digest.removeprefix('sha256:'):
        raise ValueError('Publication BOM source hash changed')
    return {r.row:r for r in normalize_bom(source,root_bom_row=2).rows}


def effective_source_quantity(row, rows):
    """A child quantity is per parent; repeated constructed groups multiply it."""
    quantity=row.quantity
    if not isinstance(quantity,(int,float)):return quantity
    by_level={r.level:r for r in rows.values() if getattr(r,'level',None)}
    parts=getattr(row,'level','').split('.')
    for length in range(1,len(parts)):
        parent=by_level.get('.'.join(parts[:length]))
        if parent and isinstance(parent.quantity,(int,float)):
            quantity*=parent.quantity
    return quantity


def validate_publication_scope(publication: dict[str, Any]) -> dict[str, int]:
    contract = publication['color_boundaries']
    materials = [m for op in publication['operations'] for m in op['materials']]
    validate_color_boundary_rows(contract, (m['row'] for m in materials))
    charged = [o for m in materials for o in m['occurrence_ids']]
    if len(charged) != len(set(charged)):
        raise ValueError('An installed occurrence is charged more than once')
    source_rows=None
    source_only_rows=set()
    material_totals={}
    for material in materials:
        material_totals[material['row']]=material_totals.get(material['row'],0)+material['quantity']
        if material.get('quantity_basis')=='source_bom_without_independent_cad_occurrence':
            if material['occurrence_ids'] or material['row'] in source_only_rows:
                raise ValueError('Source-only material has a fabricated or repeated charge')
            if source_rows is None:source_rows=_source_quantities(publication['source_bom'],publication['source_bom_sha256'])
            row=source_rows[material['row']]
            if any(material[key]!=getattr(row,key) for key in ('material_code','name','unit')) or material['quantity']!=effective_source_quantity(row,source_rows):
                raise ValueError('Non-modeled material differs from current source BOM')
            evidence=material.get('non_modeled_evidence',{})
            if evidence.get('mapping_status') not in ('missing','non_renderable','ambiguous') or not evidence.get('source_process_records'):
                raise ValueError('Non-modeled material lacks CAD and process evidence')
            if evidence.get('mapping_status')=='ambiguous' and not evidence.get('unassigned_native_candidates'):
                raise ValueError('Ambiguous native material requires explicit candidate evidence')
            source_only_rows.add(material['row'])
        elif material['quantity']!=len(material['occurrence_ids']):
            raise ValueError('Published quantity differs from installed occurrences')
    if source_rows is None:source_rows=_source_quantities(publication['source_bom'],publication['source_bom_sha256'])
    for number,total in material_totals.items():
        expected=effective_source_quantity(source_rows[number],source_rows)
        if isinstance(expected,(int,float)) and total>expected+1e-8:
            raise ValueError(f'BOM row {number} is overcharged: {total} > {expected}')
    allowed = set(charged)
    references_present=any(image.get('method_reference') for image in publication['images'])
    by_task={image['task_id']:image for image in publication['images']} if references_present else {}
    by_operation={operation['number']:operation for operation in publication['operations']} if references_present else {}
    for image in publication['images']:
        payload = image['payload']
        reference=image.get('method_reference')
        if reference:
            source=by_task.get(reference['source_task_id'])
            if not source or source['operation']!=reference['source_operation'] or source.get('method_reference'):
                raise ValueError('Connection method reference has no original native source')
            if source['payload'].get('torque_annotations',{}).get('labels'):
                raise ValueError('A generic connection method example cannot transfer source-specific torque bubbles')
            code=reference['material_code']
            if not all(any(m['material_code']==code for m in by_operation[number]['materials']) for number in (image['operation'],source['operation'])):
                raise ValueError('Connection method reference changes the material specification')
            source_file=Path(source['image_path'])
            if image['image_path']!=source['image_path'] or hashlib.sha256(source_file.read_bytes()).hexdigest()!=reference['source_image_sha256']:
                raise ValueError('Connection method reference native image bytes changed')
            rule=by_operation[image['operation']].get('same_material_method_reference',{})
            if rule.get('source_task_id')!=reference['source_task_id'] or rule.get('locator_task_id') not in by_task:
                raise ValueError('Connection method example lacks its own intact installation locator')
        validate_proven_headed_directions(payload)
        if payload.get('diagnostic_only'):
            raise ValueError('An isolated diagnostic picture cannot enter the published SOP')
        packing = publication.get('terminal_packaging_rule', {})
        packing_root = packing.get('packing_occurrence')
        if packing.get('mechanical_stages_must_exclude_packaging') and packing_root:
            if image['operation'] not in packing.get('source_operations', ()) and any(
                o == packing_root or o.startswith(packing_root + '/')
                for o in payload['visible_occurrences']
            ):
                raise ValueError('Final packaging is visible in an earlier mechanical stage')
        validate_color_boundary_rows(contract, payload.get('source_bom_rows', ()))
        if not set(payload['moving_occurrences']) <= allowed:
            raise ValueError('Image includes an uncharged or internal installation occurrence')
    return {'operations': len(publication['operations']), 'material_occurrences': len(charged)}
