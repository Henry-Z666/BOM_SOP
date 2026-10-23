"""Separate supplied whole units from constructed parents and legacy CAD refs."""
from __future__ import annotations


def classify_failed_references(graph: dict, mapping: dict, colors: dict) -> list[dict]:
    identities: dict[str, list[int]] = {}
    for row in mapping['rows']:
        for occurrence in row.get('occurrence_ids', []):
            identities.setdefault(occurrence, []).append(row['bom_row'])
    issued = {occurrence: row for occurrence, rows in identities.items()
              for row in rows if colors['rows'][str(row)]['role'] == 'issued_unit'}
    result = []
    for node in graph['occurrences']:
        if node.get('retrieval_status') != 'failed':
            continue
        occurrence = node['occurrence_id']
        rows = identities.get(occurrence, [])
        owners = [row for parent, row in issued.items()
                  if occurrence == parent or occurrence.startswith(parent + '/')]
        if node.get('feature_status_code') in {2, 3, 4, 5}:
            classification = 'source_suppressed_history'
        elif any(colors['rows'][str(row)]['role'] != 'internal' for row in rows):
            classification = 'current_bom_required'
        elif owners:
            classification = 'supplied_unit_geometry_required'
        else:
            classification = 'unmapped_active_reference_needs_identity_review'
        result.append({'occurrence': occurrence, 'model': node['model_name'],
                       'bom_rows': rows, 'issued_owner_rows': sorted(set(owners)),
                       'classification': classification})
    return result
