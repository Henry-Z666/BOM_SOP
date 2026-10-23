from __future__ import annotations

import json
import math
from pathlib import Path

from .process_model import fingerprint


def validate_torque_audit(image: Path, payload: dict) -> tuple[str, ...]:
    contract = payload.get('torque_annotations')
    if not contract or not contract.get('labels'):
        return ()
    digest_payload = {key: value for key, value in contract.items() if key != 'contract_hash'}
    if (contract.get('status') != 'confirmed' or contract.get('renderer') != 'creo_display_list/v1'
            or contract.get('contract_hash') != fingerprint(digest_payload)):
        return ('TORQUE_CONTRACT_INVALID',)
    try:
        if payload.get('annotation_bundle'):
            from .material_annotations import annotation_channel_audit
            if payload['annotation_bundle']['channels'].get('torque')!=contract:raise ValueError('Changed torque channel')
            audit=annotation_channel_audit(image,payload,'torque')
        else:
            audit = json.loads(Path(str(image) + '.torque.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, KeyError, TypeError):
        return ('TORQUE_AUDIT_MISSING',)
    if (audit.get('schema_version') != 'torque-label-audit/v1'
            or audit.get('renderer') != 'creo_display_list/v1'
            or audit.get('contract_hash') != contract['contract_hash']
            or audit.get('overlap_check') != 'passed'):
        return ('TORQUE_AUDIT_INVALID',)
    expected = sorted((path, label['connection_id'], label['text'].replace('±', '+/-'))
        for label in contract['labels'] for path in label['occurrence_ids'])
    try:
        actual = sorted((label['occurrence_id'], label['connection_id'], label['text'])
            for label in audit['labels'])
        for label in audit['labels']:
            if label['anchor_source'] != 'native_solid_surface':
                raise ValueError('unproven torque anchor')
            for field, length in (('target_root', 3), ('text_box_camera', 4)):
                vector = label[field]
                if len(vector) != length or not all(isinstance(value, (int, float))
                    and not isinstance(value, bool) and math.isfinite(value) for value in vector):
                    raise ValueError('invalid native annotation geometry')
            if min(label['text_box_camera'][2:]) <= 0:
                raise ValueError('empty label box')
    except (KeyError, TypeError, ValueError):
        return ('TORQUE_AUDIT_INVALID',)
    if actual != expected:
        return ('TORQUE_COVERAGE_MISMATCH',)
    # No lossless depth evidence exists yet. Every annotated native raster requires
    # explicit human readability/occlusion review, regardless of AI image approval.
    return ('TORQUE_VISUAL_REVIEW_REQUIRED',)
