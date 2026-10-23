from pathlib import Path
from dataclasses import asdict, replace
import json
from sop_pipeline.agent.formal_render_planner import formal_render_plan_from_dict, _camera_basis, _occurrence_bounds_root, _lock_final_stage_geometry, _canonical_hash
from sop_pipeline.agent.render_job_compiler import compile_locked_render_jobs

def compile_technical_previews(analysis: Path, step_ids: tuple[str, ...], output: Path) -> Path:
    """Build isolated geometry fixtures without approving production dependencies or parameters."""
    from sop_pipeline.agent.creo_discovery import _tree_hashes, _hash_manifest, _validate_graph
    from sop_pipeline.agent.process_bridge import assert_process_sources
    from sop_pipeline.agent.process_model import operation_from_dict
    read = lambda name: json.loads((analysis / name).read_text(encoding='utf-8-sig'))
    package = read('process-package.json')
    g = read('cad-graph.json')
    config_path = Path(package['product_config'])
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    models = (config_path.parent / config['source_root'] / config['cad_directory']).resolve()
    _validate_graph(g, config['root_assembly'])
    if g.get('source_tree_fingerprint') != _hash_manifest(_tree_hashes(models)):
        raise ValueError('preview native snapshot does not match current CAD sources')
    assert_process_sources(tuple((operation_from_dict(x) for x in package['operations'])), tuple(package['sources']))
    if not step_ids or len(set(step_ids)) != len(step_ids):
        raise ValueError('preview step IDs must be nonempty and unique')
    if (analysis / 'planning-blockers.json').exists():
        plan = read('planning-blockers.json')
        blocked = set(plan['blocked_step_ids'])
    else:
        plan = read('geometry-plan.json')
        blocked = set()
    steps = plan['steps']
    basis = _camera_basis(g)
    bounds = _occurrence_bounds_root({n['id']: n for n in g['occurrences']})
    tasks = []
    for ident in step_ids:
        s = next((dict(x) for x in steps if x['step_id'] == ident), None)
        if s is None or s['status'] != 'ready' or ident in blocked:
            raise ValueError('preview requires an unblocked geometry-ready step: ' + ident)
        spans = [max((bounds[i]['max'][j] - bounds[i]['min'][j] for j in range(3))) for i in s['moving_occurrences'] if i in bounds]
        magnitude = sum((v * v for v in s['translation_vector_root'])) ** 0.5
        scale = min(1.0, 0.5 * max(spans) / magnitude) if spans and magnitude > 0 else 1.0
        s['translation_vector_root'] = [v * scale for v in s['translation_vector_root']]
        s['occurrence_translation_vectors_root'] = {i: [v * scale for v in vector] for i, vector in s['occurrence_translation_vectors_root'].items()}
        original = s['depends_on']
        s['depends_on'] = []
        s['visible_occurrences'] = sorted(set(s['moving_occurrences'] + s['receiver_occurrences']))
        s['complete_state_hash'] = _canonical_hash({'purpose': 'geometry_fixture_preview', 'visible': s['visible_occurrences']})
        payload = {'schema_version': 'formal-render-plan/v2', 'assembly_file': g['assembly_file'], 'camera_basis': basis, 'occurrence_bounds_root': bounds, 'initial_completed_occurrences': [], 'scope_base_occurrences': {s['stage_scope_occurrence']: s['receiver_occurrences']}, 'steps': [s], 'diagnostics': [], 'ready_steps': 1, 'questioned_steps': 0, 'checkpoint_interval': 1, 'scope_decisions': {}, 'fingerprint': _canonical_hash(s)}
        plan = formal_render_plan_from_dict(payload)
        plan = replace(plan, steps=tuple(_lock_final_stage_geometry(list(plan.steps), bounds, basis)))
        jobs = compile_locked_render_jobs(plan)
        task = jobs.tasks[0]
        task = replace(task, payload={**task.payload, 'execution_mode': 'diagnostic_preview', 'production_eligible': False, 'preview_purpose': 'geometry_fixture_only', 'original_process_dependencies': original, 'parameter_status': 'pending'})
        tasks.append(task)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'schema_version': 'render-plan/v2', 'tasks': [asdict(x) for x in tasks]}, ensure_ascii=False, indent=2), encoding='utf-8')
    return output
