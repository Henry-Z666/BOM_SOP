"""Source-locked continuous assembly previews with explicit local prerequisites."""
from pathlib import Path
from dataclasses import asdict, replace
import json
from .creo_discovery import _tree_hashes, _hash_manifest, _validate_graph
from .process_bridge import assert_process_sources
from .process_model import operation_from_dict
from .formal_render_planner import (formal_render_plan_from_dict, _camera_basis,
    _occurrence_bounds_root, _lock_final_stage_geometry, _canonical_hash, _complete_plan_state)
from .render_job_compiler import compile_locked_render_jobs


def compile_scoped_sequence(analysis: Path, selection: dict, output: Path) -> Path:
    read=lambda name: json.loads((analysis/name).read_text(encoding='utf-8-sig'))
    package, graph, draft=read('process-package.json'),read('cad-graph.json'),read('planning-blockers.json')
    config_path=Path(package['product_config']);config=json.loads(config_path.read_text(encoding='utf-8-sig'))
    models=(config_path.parent/config['source_root']/config['cad_directory']).resolve()
    _validate_graph(graph,config['root_assembly'])
    if graph['source_tree_fingerprint']!=_hash_manifest(_tree_hashes(models)):
        raise ValueError('sequence CAD snapshot changed')
    assert_process_sources(tuple(operation_from_dict(x) for x in package['operations']),tuple(package['sources']))
    scope=selection['scope'];base=set(selection['preassembled_occurrences'])
    nodes={n['id']:n for n in graph['occurrences']};bounds=_occurrence_bounds_root(nodes)
    if not base or base-set(nodes) or not selection.get('prerequisite_reason'):
        raise ValueError('sequence requires evidenced preassembled receivers')
    originals={s['step_id']:s for s in draft['steps']};blocked=set(draft['blocked_step_ids'])
    available=set(base);selected=[];seen=set();previous=None;origins={}
    for index,item in enumerate(selection['steps'],1):
        original=originals[item['source_step_id']];s=dict(original);moving=set(item['occurrences'])
        if original['status']!='ready' or original['step_id'] in blocked:
            raise ValueError('sequence source step is not geometry ready')
        if not moving or moving-set(original['moving_occurrences']) or moving&available or moving&seen:
            raise ValueError('sequence occurrence selection is invalid or repeated')
        anchors=[a for a in original['arrow_anchors'] if a['occurrence_id'] in moving]
        receivers={a['receiver_occurrence_id'] for a in anchors}
        if receivers-available:
            raise ValueError('sequence receiver has not been installed: '+str(sorted(receivers-available)))
        ident=f"{selection['sequence_id']}-{index:02d}"
        origins[ident]={'source_step_id':original['step_id'],'original_dependencies':original['depends_on']}
        vectors={p:list(original['occurrence_translation_vectors_root'][p]) for p in sorted(moving)}
        s.update(step_id=ident,title=item['title'],stage_scope_occurrence=scope,
            moving_occurrences=sorted(moving),receiver_occurrences=sorted(receivers),arrow_anchors=anchors,
            constraint_ids=[a['constraint_id'] for a in anchors],state_delta=sorted(moving),
            affected_descendants=[],depends_on=[previous] if previous else [],
            occurrence_translation_vectors_root=vectors)
        selected.append(s);available.update(moving);seen.update(moving);previous=ident
    if not selected:raise ValueError('empty sequence')
    basis=_camera_basis(graph)
    payload={'schema_version':'formal-render-plan/v2','assembly_file':graph['assembly_file'],
        'camera_basis':basis,'occurrence_bounds_root':bounds,'initial_completed_occurrences':[],
        'scope_base_occurrences':{scope:sorted(base)},'steps':selected,'diagnostics':[],
        'ready_steps':len(selected),'questioned_steps':0,'checkpoint_interval':1,
        'scope_decisions':{},'color_boundaries':draft.get('color_boundaries',{}),
        'fingerprint':_canonical_hash(selection)}
    plan=formal_render_plan_from_dict(payload)
    steps=_complete_plan_state(list(plan.steps),{scope:base},set())
    plan=replace(plan,steps=tuple(_lock_final_stage_geometry(steps,bounds,basis)))
    jobs=compile_locked_render_jobs(plan)
    tasks=[replace(task,payload={**task.payload,'execution_mode':'diagnostic_preview',
        'production_eligible':False,'preview_purpose':'continuous_scoped_sequence',
        'original_process_dependencies':origins[task.step_id]['original_dependencies'],
        'sequence_prerequisites':selection['prerequisite_reason'],'parameter_status':'pending'}) for task in jobs.tasks]
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({'schema_version':jobs.schema_version,'tasks':[asdict(t) for t in tasks],
        'selection':selection,'source_tree_fingerprint':graph['source_tree_fingerprint']},ensure_ascii=False,indent=2),encoding='utf-8')
    output.with_name('geometry-plan.json').write_text(json.dumps(asdict(plan),ensure_ascii=False,indent=2),encoding='utf-8')
    return output
