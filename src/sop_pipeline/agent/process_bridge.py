"""Compile process evidence through the existing Creo geometry and publication seams."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from pathlib import Path

from .formal_render_planner import (
    FormalRenderPlan, _attach_affected_descendants, _complete_plan_state,
    _lock_final_stage_geometry, _topologically_order,
)
from .process_model import ProcessOperation, TorqueDecision, fingerprint, order_operations, resolve_torque
from .render_job_compiler import compile_locked_render_jobs
from .render_scheduler import RenderPlan
from .sop_publisher import SopStep


@dataclass(frozen=True)
class OperationBinding:
    operation_id: str
    step_ids: tuple[str, ...]
    confirmed_by: str
    applicability_reason: str
    torque_decisions: tuple[TorqueDecision, ...] = ()
    torque_required: bool = True
    no_torque_reason: str = ''
    scope: str = 'pending'
    torque_target_occurrences: tuple[str, ...] = ()


def assert_process_sources(operations: tuple[ProcessOperation, ...], sources: tuple[dict, ...]) -> None:
    """Recheck the whole input package at execution/publication, not just BOM/CAD."""
    known = {}
    for source in sources:
        path = Path(source['file'])
        actual = 'sha256:' + sha256(path.read_bytes()).hexdigest()
        if actual != source['sha256']:
            raise ValueError('PROCESS_SOURCE_CHANGED: ' + path.name)
        known[(path.name, actual)] = path
    for operation in operations:
        for evidence in (*operation.instructions, *(claim.evidence for claim in operation.torque_claims)):
            if (evidence.file, evidence.sha256) not in known:
                raise ValueError('process evidence is outside the locked source package')
    from .process_sources import read_source_package
    extracted = read_source_package(tuple(known.values()), card_files=tuple(Path(source['file'])
        for source in sources if source.get('role', 'process_card') == 'process_card'))
    facts = {(item['file'], item['sha256'], item['sheet'], item['location']): item['text']
        for item in extracted['facts']}
    for operation in extracted['operations']:
        for item in operation['instructions']:
            facts[(item['file'], item['sha256'], item['sheet'], item['location'])] = item['text']
    for operation in operations:
        for evidence in (*operation.instructions, *(claim.evidence for claim in operation.torque_claims)):
            if facts.get((evidence.file, evidence.sha256, evidence.sheet, evidence.location)) != evidence.text:
                raise ValueError('process evidence text does not match the original source')
        from .process_sources import claims_in
        for claim in operation.torque_claims:
            if claim not in claims_in(claim.evidence):
                raise ValueError('torque value does not match its source evidence')


def compile_process_jobs(plan: FormalRenderPlan, operations: tuple[ProcessOperation, ...],
                         bindings: tuple[OperationBinding, ...]) -> RenderPlan:
    order_operations(operations)  # Check unknown dependencies and cycles before touching geometry.
    by_operation = {operation.operation_id: operation for operation in operations}
    by_binding = {binding.operation_id: binding for binding in bindings}
    if len(by_binding) != len(bindings) or set(by_binding) - set(by_operation):
        raise ValueError('duplicate or unknown operation binding')
    step_owner = {}
    for binding in bindings:
        if not binding.confirmed_by.strip() or not binding.applicability_reason.strip() or not binding.step_ids:
            raise ValueError('operation applicability must be explicitly confirmed')
        if binding.scope not in {'main_assembly', 'subassembly'}:
            raise ValueError('operation scope must be explicitly confirmed')
        for step in binding.step_ids:
            if step in step_owner:
                raise ValueError('render step is owned by multiple operations')
            step_owner[step] = binding.operation_id
    if set(step_owner) != {step.step_id for step in plan.steps}:
        raise ValueError('every geometry step must have exactly one process operation')
    updated = []
    for step in plan.steps:
        operation = by_operation[step_owner[step.step_id]]
        binding = by_binding[operation.operation_id]
        if (binding.scope == 'main_assembly') != (step.stage_scope_occurrence == 'ROOT'):
            raise ValueError('process scope disagrees with the CAD assembly stage')
        missing = set(operation.depends_on) - set(by_binding)
        if missing:
            raise ValueError('process prerequisite has no render binding')
        dependencies = set(step.depends_on)
        for prerequisite in operation.depends_on:
            dependencies.update(by_binding[prerequisite].step_ids)
        # All original CAD/receiver dependencies survive process enrichment.
        updated.append(replace(step, title=operation.title, depends_on=tuple(sorted(dependencies))))
    updated = _topologically_order(updated)
    updated = _complete_plan_state(updated,
        {scope: set(values) for scope, values in plan.scope_base_occurrences.items()},
        set(plan.initial_completed_occurrences))
    updated = _lock_final_stage_geometry(updated, plan.occurrence_bounds_root, plan.camera_basis)
    updated = _attach_affected_descendants(updated)
    payload = {'geometry': plan.fingerprint, 'operations': [asdict(op) for op in operations],
        'bindings': [asdict(binding) for binding in bindings], 'steps': [asdict(step) for step in updated]}
    plan = replace(plan, steps=tuple(updated), fingerprint=fingerprint(payload),
        ready_steps=sum(step.status == 'ready' for step in updated),
        questioned_steps=sum(step.status != 'ready' for step in updated))
    jobs = compile_locked_render_jobs(plan)
    tasks = []
    for task in jobs.tasks:
        operation = by_operation[step_owner[task.step_id]]
        binding = by_binding[operation.operation_id]
        labels = []
        covered = set()
        for decision in binding.torque_decisions:
            claim = resolve_torque(operation, decision)
            paths = set(decision.occurrence_ids) & set(task.payload['moving_occurrences'])
            if not paths:
                continue
            if paths & covered:
                raise ValueError('a fastening occurrence has multiple torque decisions')
            covered.update(paths)
            labels.append({'connection_id': decision.connection_id, 'claim_id': claim.claim_id,
                'text': '紧固力矩：' + claim.text, 'occurrence_ids': sorted(paths),
                'evidence': asdict(claim.evidence), 'confirmed_by': decision.confirmed_by,
                'reason': decision.reason})
        all_paths = {path for step in updated if step.step_id in binding.step_ids
            for path in step.moving_occurrences}
        for decision in binding.torque_decisions:
            if set(decision.occurrence_ids) - all_paths:
                raise ValueError('torque targets an occurrence outside its operation')
        if not binding.torque_required and (binding.torque_decisions or not binding.no_torque_reason.strip()):
            raise ValueError('non-torque operation requires a reason and no torque decisions')
        targets = set(binding.torque_target_occurrences) if binding.torque_target_occurrences else all_paths
        if targets - all_paths:
            raise ValueError('torque requirement targets an occurrence outside its operation')
        required_here = targets & set(task.payload['moving_occurrences']) if binding.torque_required else set()
        if covered - required_here:
            raise ValueError('torque label targets an occurrence without a torque requirement')
        pending = binding.torque_required and covered != required_here
        annotations = {'schema_version': 'torque-annotations/v1',
            'status': 'pending' if pending else 'confirmed' if labels else 'not_applicable',
            'renderer': 'creo_display_list/v1', 'labels': [] if pending else labels}
        if pending:
            annotations['candidate_labels'] = labels
        annotations['contract_hash'] = fingerprint(annotations)
        enriched = {**task.payload,
            'source_bom_rows': list(next(step.source_bom_rows for step in updated if step.step_id == task.step_id)),
            'process_operation': asdict(operation),
            'process_confirmation': asdict(binding),
            'torque_annotations': annotations,
            'publication_parameter_status': 'pending' if pending else 'confirmed'}
        tasks.append(replace(task, main_process_id=operation.operation_id, payload=enriched))
    return replace(jobs, tasks=tuple(tasks))


def enrich_sop_step(step: SopStep, payload: dict) -> SopStep:
    operation = payload.get('process_operation')
    if not operation:
        return step
    annotation = payload['torque_annotations']
    controls = [step.control_points] if step.control_points else []
    controls.extend(f"{item['text']}（{item['connection_id']}）" for item in annotation['labels'])
    if annotation['status'] == 'pending':
        controls.append('力矩参数待确认，不可作为生产参数使用')
    instructions = '\n'.join(item['text'] for item in operation['instructions'])
    return replace(step, main_process_id=operation['operation_id'], main_process_name=operation['title'],
        title=operation['title'], process_text=instructions,
        control_points='\n'.join(controls), questioned=step.questioned or annotation['status'] == 'pending',
        preserve_process_order=True,
        parameter_status='pending' if annotation['status'] == 'pending' else 'confirmed')
