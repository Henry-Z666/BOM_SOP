"""Keep explicitly scheduled terminal materials out of earlier CAD stages."""
from copy import deepcopy


def exclude_terminal_context(payload, terminal_occurrences):
    """Remove only future context, never a moving part or its receiver.

    Context bounds follow visible-context order. Keep the parallel sequence
    aligned so subsequent contact framing still uses the correct native body.
    """
    result = deepcopy(payload)
    roots = tuple(terminal_occurrences)
    belongs = lambda occurrence: any(occurrence == root or occurrence.startswith(root + '/') for root in roots)
    if any(belongs(o) for o in result['moving_occurrences'] + result['receiver_occurrences']):
        raise ValueError('Terminal exclusion would remove an active installation')
    moving = set(result['moving_occurrences'])
    context = [o for o in result['visible_occurrences'] if o not in moving]
    bounds = result['stage_geometry_root']['context_bounds']
    if len(context) != len(bounds):
        raise ValueError('Context geometry order is not proven')
    result['stage_geometry_root']['context_bounds'] = [box for o, box in zip(context, bounds) if not belongs(o)]
    result['visible_occurrences'] = [o for o in result['visible_occurrences'] if not belongs(o)]
    result['bom_visibility_exclusions'] = sorted(set(result.get('bom_visibility_exclusions', [])) | set(roots))
    result['terminal_stage_visibility'] = {'schema_version': 'terminal-stage-visibility/v1', 'future_occurrences': list(roots), 'reason': 'Packaging is applied after assembly and final inspection.'}
    return result
