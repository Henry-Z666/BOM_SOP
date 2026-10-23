"""First-stage process pipeline: ingest, compile, native render, reviewed publish."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path

from .bom_normalizer import normalize_bom
from .bundle_paths import bundled_creo_script
from .creo_discovery import powershell_command, resolve_runtime_config
from .creo_worker import AgentNativeCreoWorker
from .formal_render_planner import formal_render_plan_from_dict, FormalRenderPlan
from .process_bridge import OperationBinding, assert_process_sources, compile_process_jobs, enrich_sop_step
from .process_model import TorqueDecision, fingerprint, operation_from_dict
from .process_model import TorqueClaim, SourceEvidence
from .process_sources import read_source_package
from .render_scheduler import RenderPlan, RenderTask
from .sop_publisher import SopImage, SopPublisher, SopStep


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def _operations(package: dict) -> tuple:
    return tuple(operation_from_dict(item) for item in package['operations'])


def _bindings(value: dict) -> tuple[OperationBinding, ...]:
    return tuple(OperationBinding(**{**item, 'step_ids': tuple(item['step_ids']),
        'torque_target_occurrences': tuple(item.get('torque_target_occurrences', ())),
        'torque_decisions': tuple(TorqueDecision(**{**decision,
            'occurrence_ids': tuple(decision['occurrence_ids']),
            'acknowledged_claim_ids': tuple(decision['acknowledged_claim_ids'])})
            for decision in item.get('torque_decisions', []))}) for item in value['bindings'])


def ingest(product_file: Path, output: Path) -> dict:
    config = _load(product_file)
    if config.get('schema_version') != 'process-product/v1':
        raise ValueError('unsupported process product configuration')
    root = (product_file.resolve().parent / config['source_root']).resolve()
    def source(name: str) -> Path:
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError('source path escapes product package')
        return path
    files = tuple(source(name) for name in [config['bom'], *config['process_cards'], *config['standards']])
    if len(set(files)) != len(files):
        raise ValueError('duplicate source workbook')
    package = read_source_package(files, card_files=tuple(source(name) for name in config['process_cards']))
    bom = normalize_bom(source(config['bom']), root_bom_row=config.get('root_bom_row'))
    package['bom'] = asdict(bom)
    package['product_id'] = config['product_id']
    package['product_config'] = str(product_file.resolve())
    package['status'] = 'pending_applicability_and_cad_binding'
    # Give every material candidate explicit row identities. A match is a suggestion,
    # never a production occurrence binding or a proof of model equivalence.
    for operation in package['operations']:
        operation['candidate_bom_rows'] = [row.row for row in bom.rows
            if row.material_code in operation['materials']]
    _save(output / 'process-package.json', package)
    summary = ['# 工艺资料解析与待确认清单', '',
        '本清单是阶段性分析结果，不是已批准生产 SOP。', '',
        f"产品：{config['product_id']}；操作候选：{len(package['operations'])}；"
        f"带单位力矩陈述：{sum(len(item['torque_claims']) for item in package['operations'])}。", '',
        '图片中的事实需另行核验；工艺卡型号适用性、总装/分装范围、实例位置及跨工序依赖均需明确后才能编译。', '',
        '| 操作 | 工艺范围 | BOM 候选行 | 力矩陈述 |', '| --- | --- | --- | --- |']
    for item in package['operations']:
        claims = '; '.join(f"{claim['value']}{claim['tolerance']} {claim['unit']}"
            for claim in item['torque_claims']) or '待确认 / 未提取到明确数值'
        summary.append(f"| {item['title'].replace('|','／')} | {item['scope']} | "
            f"{','.join(map(str,item['candidate_bom_rows'])) or '无精确物料匹配'} | {claims} |")
    (output / '资料待确认清单.md').write_text('\n'.join(summary) + '\n', encoding='utf-8')
    return package


def compile_package(package_file: Path, geometry_file: Path, decisions_file: Path, output: Path) -> dict:
    package, decisions = _load(package_file), _load(decisions_file)
    if decisions.get('source_package_fingerprint') != package['source_package_fingerprint']:
        raise ValueError('decisions belong to a different source package')
    operations = _operations(package)
    by_operation = {operation.operation_id: operation for operation in operations}
    for operation_id, dependencies in decisions.get('operation_dependencies', {}).items():
        if operation_id not in by_operation:
            raise ValueError('dependency decision references unknown operation')
        by_operation[operation_id] = replace(by_operation[operation_id], depends_on=tuple(dependencies))
    catalog = {item['claim_id']: TorqueClaim(**{**item, 'evidence': SourceEvidence(**item['evidence'])})
        for item in package.get('parameter_catalog', [])}
    for operation_id, claim_ids in decisions.get('additional_torque_claims', {}).items():
        if operation_id not in by_operation or set(claim_ids) - set(catalog):
            raise ValueError('parameter decision references unknown operation or source claim')
        operation = by_operation[operation_id]
        claims = {claim.claim_id: claim for claim in operation.torque_claims}
        claims.update({key: catalog[key] for key in claim_ids})
        by_operation[operation_id] = replace(operation, torque_claims=tuple(claims.values()))
    operations = tuple(by_operation.values())
    assert_process_sources(operations, tuple(package['sources']))
    geometry = formal_render_plan_from_dict(_load(geometry_file))
    if not isinstance(geometry, FormalRenderPlan):
        raise ValueError('stage one requires one authoritative assembly')
    if decisions.get('geometry_fingerprint') != geometry.fingerprint:
        raise ValueError('decisions belong to a different CAD plan')
    jobs = compile_process_jobs(geometry, operations, _bindings(decisions))
    assembly = geometry.camera_basis.get('assembly_sha256', '')
    config = _load(Path(package['product_config'])) if package.get('product_config') else None
    cad_sources = []
    if config:
        models = (Path(package['product_config']).parent / config['source_root'] / config['cad_directory']).resolve()
        assembly_path = models / geometry.assembly_file
        if not assembly_path.is_relative_to(models) or not assembly_path.is_file():
            raise ValueError('locked geometry assembly is outside product models')
        if 'sha256:' + sha256(assembly_path.read_bytes()).hexdigest() != assembly:
            raise ValueError('locked geometry does not match current product assembly')
        from .model_inventory import MODEL_PATTERN
        cad_sources = [{'relative_path': item.relative_to(models).as_posix(),
            'sha256': 'sha256:' + sha256(item.read_bytes()).hexdigest()}
            for item in sorted(models.rglob('*')) if item.is_file() and MODEL_PATTERN.match(item.name)]
    result = {**asdict(jobs), 'source_package': str(package_file.resolve()),
        'source_package_file_hash': 'sha256:' + sha256(package_file.read_bytes()).hexdigest(),
        'cad_sources': cad_sources,
        'process_jobs_fingerprint': fingerprint(asdict(jobs))}
    _save(output, result)
    return result


def _locked_jobs(path: Path) -> tuple[dict, RenderPlan]:
    value = _load(path)
    package_path = Path(value['source_package'])
    if 'sha256:' + sha256(package_path.read_bytes()).hexdigest() != value['source_package_file_hash']:
        raise ValueError('compiled process package was edited')
    package = _load(package_path)
    active_operations = {item['payload']['process_operation']['operation_id']:
        operation_from_dict(item['payload']['process_operation']) for item in value['tasks']}
    assert_process_sources(tuple(active_operations.values()), tuple(package['sources']))
    plan = RenderPlan(value['schema_version'], tuple(RenderTask(**{**item,
        'depends_on': tuple(item['depends_on'])}) for item in value['tasks']))
    if fingerprint(asdict(plan)) != value['process_jobs_fingerprint']:
        raise ValueError('compiled render jobs were edited')
    return value, plan


def render(jobs_file: Path, models: Path, output: Path) -> dict:
    value, plan = _locked_jobs(jobs_file)
    _assert_cad_sources(value, models)
    runtime = resolve_runtime_config(output)
    if runtime is None:
        raise ValueError('Creo runtime configuration is missing')
    worker = AgentNativeCreoWorker(powershell=powershell_command(),
        batch_script=bundled_creo_script('run_agent_native_batch.ps1'), models_root=models,
        render_plan_json=jobs_file.resolve(), runtime_config=runtime)
    session = worker.open_session(output, plan)
    results = []
    try:
        for task in plan.tasks:
            print(f'渲染 {task.step_id}: {task.payload["title"]}', flush=True)
            attempt = worker.render(session, task, 1)
            image = session.output_directory / (task.task_id + '.jpg')
            results.append({'step_id': task.step_id, **asdict(attempt), 'image_path': str(image.resolve())})
            _save(output / 'render-results.json', {'jobs_fingerprint': value['process_jobs_fingerprint'], 'steps': results})
    finally:
        worker.close_session(session)
    _assert_cad_sources(value, models)
    return {'jobs_fingerprint': value['process_jobs_fingerprint'], 'steps': results}


def publish(jobs_file: Path, results_file: Path, reviews_file: Path, output: Path) -> Path:
    value, plan = _locked_jobs(jobs_file)
    results, reviews = _load(results_file), _load(reviews_file)
    if results['jobs_fingerprint'] != value['process_jobs_fingerprint']:
        raise ValueError('render results belong to different jobs')
    by_result = {item['step_id']: item for item in results['steps']}
    by_review = {item['step_id']: item for item in reviews['steps']}
    steps = []
    package = _load(Path(value['source_package']))
    if package.get('product_config'):
        config = _load(Path(package['product_config']))
        _assert_cad_sources(value, (Path(package['product_config']).parent /
            config['source_root'] / config['cad_directory']).resolve())
    bom_rows = {row['row']: row for row in package.get('bom', {}).get('rows', [])}
    pending = False
    from .render_validation import DeterministicNativeRenderValidator
    from .gate_policy import GateCategory, classify_failures
    for task in plan.tasks:
        result = by_result.get(task.step_id)
        if result is None or result['disposition'] not in {'passed', 'questioned'} or not result.get('output_hash'):
            raise ValueError('missing or failed native rendering: ' + task.step_id)
        image = Path(result['image_path'])
        digest = 'sha256:' + sha256(image.read_bytes()).hexdigest()
        if digest != result['output_hash']:
            raise ValueError('rendered image changed after validation')
        report = DeterministicNativeRenderValidator().validate(image, image.with_suffix('.arrow.json'), task.payload)
        decision = classify_failures(report.failures)
        if report.failures and decision.category != GateCategory.HUMAN_REVIEW:
            raise ValueError('native structural gate failed: ' + ','.join(report.failures))
        review = by_review.get(task.step_id, {})
        approved = (review.get('approved') is True and review.get('image_hash') == digest
            and bool(str(review.get('confirmed_by', '')).strip()))
        pending = pending or not approved
        material_rows = [bom_rows[row] for row in task.payload.get('source_bom_rows', []) if row in bom_rows]
        if not material_rows:
            raise ValueError('publication requires traceable BOM material rows')
        materials = tuple((row['material_code'] or row['drawing_no'], row['name'],
            len(task.payload['moving_occurrences']) if len(material_rows)==1
            and isinstance(row['quantity'], int) else row['quantity']) for row in material_rows)
        step = SopStep(task.step_id, task.main_process_id, task.payload['title'], task.payload['title'],
            SopImage(task.step_id, image), materials, '', '', '', questioned=not approved)
        step = enrich_sop_step(step, task.payload)
        pending = pending or step.parameter_status == 'pending'
        steps.append(step)
    return SopPublisher().publish(tuple(steps), output, pending=pending)


def _assert_cad_sources(value: dict, models: Path) -> None:
    from .model_inventory import MODEL_PATTERN
    expected = value.get('cad_sources', [])
    if not expected:
        raise ValueError('compiled jobs lack a complete CAD input lock')
    actual = [{'relative_path': path.relative_to(models).as_posix(),
        'sha256': 'sha256:' + sha256(path.read_bytes()).hexdigest()}
        for path in sorted(models.rglob('*')) if path.is_file() and MODEL_PATTERN.match(path.name)]
    if actual != expected:
        raise ValueError('CAD sources changed since process compilation')


def main() -> None:
    parser = argparse.ArgumentParser(description='Source-backed mechanical process MVP')
    commands = parser.add_subparsers(dest='command', required=True)
    ingest_parser = commands.add_parser('ingest')
    ingest_parser.add_argument('--product', type=Path, required=True)
    ingest_parser.add_argument('--output', type=Path, required=True)
    analyze_parser = commands.add_parser('analyze')
    analyze_parser.add_argument('--product', type=Path, required=True)
    analyze_parser.add_argument('--output', type=Path, required=True)
    replan_parser = commands.add_parser('replan')
    for name in ('product', 'graph', 'output'):
        replan_parser.add_argument('--' + name, type=Path, required=True)
    rebuild_parser = commands.add_parser('rebuild')
    rebuild_parser.add_argument('--product', type=Path, required=True)
    rebuild_parser.add_argument('--graph', type=Path)
    rebuild_parser.add_argument('--output', type=Path, required=True)
    preview_parser = commands.add_parser('preview')
    preview_parser.add_argument('--analysis', type=Path, required=True)
    preview_parser.add_argument('--step', action='append', required=True)
    preview_parser.add_argument('--output', type=Path, required=True)
    sequence_parser = commands.add_parser('sequence-preview')
    for name in ('analysis', 'selection', 'output'):
        sequence_parser.add_argument('--' + name, type=Path, required=True)
    lock_parser = commands.add_parser('lock')
    for name in ('geometry', 'scope-decisions', 'output'):
        lock_parser.add_argument('--' + name, type=Path, required=True)
    compile_parser = commands.add_parser('compile')
    for name in ('package', 'geometry', 'decisions', 'output'):
        compile_parser.add_argument('--' + name, type=Path, required=True)
    render_parser = commands.add_parser('render')
    for name in ('jobs', 'models', 'output'):
        render_parser.add_argument('--' + name, type=Path, required=True)
    publish_parser = commands.add_parser('publish')
    for name in ('jobs', 'results', 'reviews', 'output'):
        publish_parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'ingest':
        value = ingest(args.product, args.output)
        print(f"已提取 {len(value['operations'])} 个工艺操作候选")
    elif args.command == 'analyze':
        analyze(args.product, args.output)
    elif args.command == 'replan':
        replan(args.product, args.graph, args.output)
    elif args.command == 'rebuild':
        print(rebuild(args.product, args.output, args.graph))
    elif args.command == 'preview':
        from .technical_preview import compile_technical_previews
        print(compile_technical_previews(args.analysis, tuple(args.step), args.output))
    elif args.command == 'sequence-preview':
        from .scoped_sequence import compile_scoped_sequence
        print(compile_scoped_sequence(args.analysis, _load(args.selection), args.output))
    elif args.command == 'lock':
        from .formal_render_planner import lock_formal_render_plan
        geometry = formal_render_plan_from_dict(_load(args.geometry))
        _save(args.output, asdict(lock_formal_render_plan(geometry, _load(args.scope_decisions))))
    elif args.command == 'compile':
        value = compile_package(args.package, args.geometry, args.decisions, args.output)
        print(f"已编译 {len(value['tasks'])} 个原生渲染任务")
    elif args.command == 'render':
        render(args.jobs, args.models, args.output)
    else:
        print(publish(args.jobs, args.results, args.reviews, args.output))


def analyze(product_file: Path, output: Path) -> None:
    """Use the same native discovery and geometry compiler as the desktop pipeline."""
    from .bom_normalizer import NormalizedBom, NormalizedBomRow
    from .model_inventory import inventory_models
    from .draft_planner import create_draft_plan
    from .bom_cad_mapper import map_bom_to_occurrences
    from .formal_render_planner import compile_formal_render_plan
    from .creo_discovery import PowerShellCreoDiscovery
    package = ingest(product_file, output)
    config = _load(product_file)
    models = (product_file.resolve().parent / config['source_root'] / config['cad_directory']).resolve()
    bom_data = package['bom']
    bom = NormalizedBom(**{**bom_data, 'rows': tuple(NormalizedBomRow(**row) for row in bom_data['rows']),
        'sheet_candidates': tuple(bom_data['sheet_candidates'])})
    inventory = inventory_models(models, bom)
    if inventory.final_assembly != config['root_assembly']:
        raise ValueError('explicit product root and exact BOM model match disagree')
    runtime = resolve_runtime_config(output)
    if runtime is None:
        raise ValueError('Creo runtime configuration is missing')
    print('扫描锁定总装：' + inventory.final_assembly, flush=True)
    graph = PowerShellCreoDiscovery(powershell=powershell_command(),
        script=bundled_creo_script('run_input_discovery.ps1'), runtime_config=runtime).discover(
            models, inventory.final_assembly, output)
    _save(output / 'cad-graph.json', graph)
    _plan_graph(package, bom, inventory, graph, output)


def replan(product_file: Path, graph_file: Path, output: Path) -> None:
    """Rebuild from a native snapshot only while every CAD source remains locked."""
    from .creo_discovery import _validate_graph, _tree_hashes, _hash_manifest
    from .bom_normalizer import NormalizedBom, NormalizedBomRow
    from .model_inventory import inventory_models
    package = ingest(product_file, output)
    config = _load(product_file)
    models = (product_file.resolve().parent / config['source_root'] / config['cad_directory']).resolve()
    graph = _load(graph_file)
    _validate_graph(graph, config['root_assembly'])
    if graph.get('source_tree_fingerprint') != _hash_manifest(_tree_hashes(models)):
        raise ValueError('native graph CAD source lock has changed')
    manifest = graph.get('authoritative_assembly', {})
    if manifest.get('relative_path') != config['root_assembly'] or manifest.get('sha256') != 'sha256:' + sha256((models / config['root_assembly']).read_bytes()).hexdigest():
        raise ValueError('native graph authoritative assembly lock mismatch')
    data = package['bom']
    bom = NormalizedBom(**{**data, 'rows': tuple(NormalizedBomRow(**row) for row in data['rows']),
        'sheet_candidates': tuple(data['sheet_candidates'])})
    inventory = inventory_models(models, bom)
    _save(output / 'cad-graph.json', graph)
    _plan_graph(package, bom, inventory, graph, output)


def _plan_graph(package, bom, inventory, graph, output: Path) -> None:
    from .draft_planner import create_draft_plan
    from .bom_cad_mapper import map_bom_to_occurrences
    from .formal_render_planner import compile_formal_render_plan, PlanningDependencyCycle
    draft = create_draft_plan(bom, inventory, assembly_batch=True)
    mapping = map_bom_to_occurrences(bom, inventory, graph, draft)
    _save(output / 'cad-mapping.json', asdict(mapping))
    try:
        geometry = compile_formal_render_plan(bom, draft, mapping, graph)
    except PlanningDependencyCycle as error:
        _save(output / 'scope-requests.json', {'requests': [asdict(item) for item in error.scope_requests]})
        _save(output / 'planning-blockers.json', {**error.diagnostic_payload(),
            'source_package_fingerprint': package['source_package_fingerprint'],
            'cad_source_tree_fingerprint': graph['source_tree_fingerprint']})
        _binding_requests(package, error.steps, output, None)
        print(f'规划待确认：依赖冲突影响 {len(error.blocked_ids)} 个步骤；完整草案已保存，未编译正式任务', flush=True)
        return
    _save(output / 'geometry-plan.json', asdict(geometry))
    _save(output / 'scope-requests.json', {'requests': [asdict(item) for item in geometry.diagnostics
        if item.code == 'SUBASSEMBLY_SCOPE_UNCONFIRMED']})
    _binding_requests(package, geometry.steps, output, geometry.fingerprint)
    print('CAD 几何计划已生成；范围与工艺绑定仍待确认', flush=True)


def _binding_requests(package, steps, output: Path, geometry_fingerprint) -> None:
    _save(output / 'operation-binding-requests.json', {
        'geometry_fingerprint': geometry_fingerprint,
        'production_eligible': False,
        'source_package_fingerprint': package['source_package_fingerprint'],
        'steps': [{'step_id': step.step_id, 'bom_rows': step.source_bom_rows,
            'moving_occurrences': step.moving_occurrences, 'scope': step.stage_scope_occurrence,
            'candidate_operation_ids': [operation['operation_id'] for operation in package['operations']
                if set(step.source_bom_rows) & set(operation['candidate_bom_rows'])]}
            for step in steps]})


def rebuild(product_file: Path, output: Path, graph_file: Path | None = None) -> Path:
    """Reconstruct the entire review stage; cached native truth requires exact source locks."""
    from .phase_report import write_phase_report
    # Fresh stage directory prevents old successful plans surviving a blocked rebuild.
    from uuid import uuid4
    stage = output / ('analysis-' + uuid4().hex)
    if graph_file is None:
        analyze(product_file, stage)
    else:
        replan(product_file, graph_file, stage)
    report = write_phase_report(stage, output)
    _save(output / 'rebuild-manifest.json', {
        'schema_version': 'process-rebuild/v1', 'production_eligible': False,
        'product_config': str(product_file.resolve()), 'analysis_directory': str(stage.resolve()),
        'native_snapshot_reused': graph_file is not None,
        'source_package_fingerprint': _load(stage / 'process-package.json')['source_package_fingerprint'],
        'report': str(report.resolve()),
        'artifacts': [{'file': str(path.resolve()), 'sha256': 'sha256:' + sha256(path.read_bytes()).hexdigest()}
            for path in sorted(stage.glob('*.json'))]})
    return report


if __name__ == '__main__':
    main()
