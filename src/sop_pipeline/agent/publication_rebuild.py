"""Rebuild a traceable, unrendered publication draft without historical allocations.

Historical publication data is accepted only by ``compare_history``. It never
participates in matching, quantities, grouping or ordering.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import re
from zipfile import ZipFile

from .bom_normalizer import (NormalizedBom, assembly_scope_frontier,
    color_boundary_contract, normalize_bom, _read_sheet, _shared_strings,
    _workbook_sheets)
from .bom_cad_mapper import map_bom_to_occurrences
from .model_inventory import inventory_models
from .process_cli import ingest
from .publication_scope import effective_source_quantity


def read_card_quantities(paths: list[Path]) -> list[dict]:
    """QSL adapter: source quantities are observations, never replacement BOM totals."""
    result = []
    for path in paths:
        digest = sha256(path.read_bytes()).hexdigest()
        with ZipFile(path) as book:
            shared = _shared_strings(book)
            for sheet, part in _workbook_sheets(book):
                for row, cells in _read_sheet(book, part, shared):
                    code = cells.get(36, '').strip()
                    if 21 <= row <= 29 and re.fullmatch(r'\d{12}', code):
                        result.append({'file': path.name, 'sha256': digest,
                            'sheet': sheet, 'location': f'AJ{row}:AR{row}',
                            'material_code': code, 'quantity_text': cells.get(44, ''),
                            'name': cells.get(39, '')})
    return result


def _descendant(child: str, parent: str) -> bool:
    return parent == 'ROOT' or child.startswith(parent + '/')


def compile_draft(bom: NormalizedBom, mapping, graph: dict,
                  package: dict | None = None, card_quantities: list | None = None) -> dict:
    """Conservative V0: exact scoped allocations; conflicts remain in the ledger."""
    frontier = assembly_scope_frontier(bom)
    rows = {row.row: row for row in frontier.rows}
    all_rows = {row.row: row for row in bom.rows}
    mapped = {item.bom_row: item for item in mapping.rows}
    root = min(frontier.rows, key=lambda r: (len(r.level.split('.')), r.row))
    occurrences = {str(n.get('occurrence_id', n.get('id'))): n
                   for n in graph['occurrences']}
    if len(occurrences) != len(graph['occurrences']):
        raise ValueError('duplicate CAD occurrence identity')
    process_by_code = defaultdict(list)
    for operation in (package or {}).get('operations', []):
        for code in operation['materials']:
            process_by_code[code].append(operation)
    qsl_by_code = defaultdict(list)
    for observation in card_quantities or []:
        qsl_by_code[observation['material_code']].append(observation)
    ledger = []
    for row in frontier.rows:
        item = mapped[row.row]
        has_children = any(other.level.startswith(row.level + '.')
                           for other in frontier.rows)
        context = row.row == root.row or (has_children and not row.assembly_boundary)
        expected = effective_source_quantity(row, all_rows)
        candidates = list(item.occurrence_ids)
        if any(candidate != 'ROOT' and candidate not in occurrences for candidate in candidates):
            raise ValueError(f'BOM row {row.row} references an unknown CAD occurrence')
        status = 'construction_context' if context else item.status
        # The legacy mapper can fall back to global candidates if a parent is
        # unresolved. Such candidates must not become confirmed allocations.
        if not context and candidates:
            parent = mapped.get(item.parent_bom_row)
            if parent and (not parent.occurrence_ids or any(
                    not any(_descendant(candidate, scope) for scope in parent.occurrence_ids)
                    for candidate in candidates)):
                status = 'unproven_parent_scope'
            elif row.unit.strip().casefold() in {'米', 'm', 'mm', '毫米', '平方米', 'm2', 'm²',
                                                '千克', '公斤', 'kg', '克', 'g', '升', 'l'}:
                status = 'non_count_quantity_requires_process_allocation'
            elif (isinstance(expected, (int, float)) and expected > 0
                  and float(expected).is_integer()):
                status = 'matched' if len(candidates) == expected else 'quantity_conflict'
            else:
                status = 'unproven_count_unit'
        if row.process_only:
            status = 'process_only'
        assessments = []
        for observation in qsl_by_code[row.material_code]:
            text = observation['quantity_text'].strip()
            value = float(text) if re.fullmatch(r'\d+(?:\.\d+)?', text) else None
            state = ('not_numeric' if value is None else
                     'same_as_bom_total_scope_unconfirmed' if value == expected else
                     'same_as_bom_local_scope_unconfirmed' if value == row.quantity else
                     'different_quantity_scope_requires_check')
            assessments.append({'file': observation.get('file', ''),
                'sheet': observation['sheet'], 'observed_quantity': value,
                'comparison': state, 'automatically_overrides_bom': False})
        ledger.append({'row': row.row, 'name': row.name,
            'material_code': row.material_code, 'parent_row': item.parent_bom_row,
            'quantity_local': row.quantity, 'quantity_total': expected,
            'unit': row.unit, 'status': status, 'candidate_occurrences': candidates,
            'allocated_occurrences': candidates if status == 'matched' else [],
            'mapping_evidence': item.evidence,
            'qsl_observations': qsl_by_code[row.material_code],
            'qsl_quantity_assessment': assessments,
            'qsl_candidate_operations': [o['operation_id'] for o in process_by_code[row.material_code]]})
    # Never resolve shared candidate pools by taking the first N paths.
    owners = defaultdict(list)
    for entry in ledger:
        for occurrence in entry['allocated_occurrences']:
            owners[occurrence].append(entry)
    for entries in owners.values():
        if len(entries) > 1:
            for entry in entries:
                entry['status'] = 'shared_occurrence_conflict'
                entry['allocated_occurrences'] = []

    groups = {}
    for entry in ledger:
        if entry['status'] != 'matched':
            continue
        parent = entry['parent_row'] or root.row
        group = groups.setdefault(parent, {'group_id': f'bom-scope-{parent}',
            'title': rows[parent].name, 'materials': [], 'source_bom_rows': [],
            'instructions': [], 'qsl_candidate_operations': [], 'connections': []})
        row = rows[entry['row']]
        group['source_bom_rows'].append(row.row)
        group['materials'].append({**asdict(row), 'quantity': entry['quantity_total'],
            'occurrence_ids': entry['allocated_occurrences'],
            'quantity_basis': 'bom_ancestor_product_verified_by_cad_count'})
        for operation in process_by_code[row.material_code]:
            if operation['operation_id'] not in group['qsl_candidate_operations']:
                group['qsl_candidate_operations'].append(operation['operation_id'])
        # QSL text/torque are linked as candidate evidence, not silently applied
        # to every occurrence of the same material code.
        if row.assembly_text:
            group['instructions'].append(row.assembly_text)
    allocated_group = {occ: group for group in groups.values()
                       for m in group['materials'] for occ in m['occurrence_ids']}
    for connection in graph.get('constraints', []):
        members = connection.get('occurrences', [])
        affected = {allocated_group[occ]['group_id'] for occ in members if occ in allocated_group}
        for group in groups.values():
            if group['group_id'] in affected:
                group['connections'].append({'constraint_id': connection['id'],
                    'occurrences': members, 'type': connection.get('type')})
    operations = []
    for number, parent in enumerate(sorted(groups), 1):
        group = groups[parent]
        operations.append({'number': number, **group,
            'chapter': rows[parent].station or rows[parent].process_route or rows[parent].name,
            'images': [], 'status': 'awaiting_sequence_and_render',
            'ordering_basis': 'bom_scope_source_order_not_verified_installation_order'})
    conflicts = [entry for entry in ledger if entry['status'] not in
                 {'matched', 'construction_context', 'process_only', 'non_renderable'}]
    return {'schema_version': 'stage-sop-publication/v1',
        'generator': 'bom-cad-publication-rebuild/v0', 'production_eligible': False,
        'status': 'draft_not_rendered', 'operation_count': len(operations),
        'scope': 'BOM部件范围自动划分；顺序、QSL适用性及图片尚未批准',
        'color_boundaries': color_boundary_contract(bom),
        'operations': operations, 'images': [],
        'allocation_ledger': ledger, 'blocking_rows': [e['row'] for e in conflicts],
        'summary': dict(Counter(entry['status'] for entry in ledger)),
        'qsl_evidence': (package or {}).get('operations', []),
        'completed_occurrences': [], 'omitted_steps': []}


def compare_history(draft: dict, history: dict) -> dict:
    historical = defaultdict(lambda: {'quantity': 0, 'occurrences': set(), 'operations': []})
    for operation in history['operations']:
        for material in operation['materials']:
            item = historical[material['row']]
            item['quantity'] += material['quantity']
            item['occurrences'].update(material['occurrence_ids'])
            item['operations'].append(operation['number'])
    comparisons = []
    for entry in draft['allocation_ledger']:
        old = historical.pop(entry['row'], None)
        if old is None:
            state = 'not_in_history'
        elif (old['quantity'] == entry['quantity_total'] and
              old['occurrences'] == set(entry['allocated_occurrences'])):
            state = 'same'
        else:
            state = 'different'
        comparisons.append({'row': entry['row'], 'name': entry['name'], 'comparison': state,
            'new_status': entry['status'], 'bom_total': entry['quantity_total'],
            'historical_quantity': old['quantity'] if old else None,
            'historical_operations': old['operations'] if old else [],
            'historical_occurrences': sorted(old['occurrences']) if old else [],
            'new_occurrences': entry['allocated_occurrences']})
    return {'schema_version': 'publication-rebuild-comparison/v1',
        'history_used_as_input': False, 'rows': comparisons,
        'historical_groups': [{'number': op['number'], 'title': op['title'],
            'materials': [{'row': m['row'], 'material_code': m['material_code'],
                'quantity': m['quantity'], 'occurrence_ids': m['occurrence_ids']}
                for m in op['materials']], 'images': op.get('images', []),
            'instructions': op.get('instructions', [])} for op in history['operations']],
        'historical_reason_limit': 'Historical allocations and grouping can be restored; unrecorded reasons cannot be inferred.',
        'historical_rows_outside_frontier': sorted(historical),
        'summary': dict(Counter(item['comparison'] for item in comparisons))}


def rebuild_publication(product: Path, graph_file: Path, output: Path,
                        history_file: Path | None = None, with_qsl: bool = False) -> dict:
    config = json.loads(product.read_text(encoding='utf-8-sig'))
    source = (product.resolve().parent / config['source_root']).resolve()
    def local(name):
        target = (source / name).resolve()
        if not target.is_relative_to(source):
            raise ValueError('product source escapes source root')
        return target
    bom_file = local(config['bom'])
    bom = normalize_bom(bom_file, root_bom_row=config.get('root_bom_row'))
    graph = json.loads(graph_file.read_text(encoding='utf-8-sig'))
    assembly = local(config['cad_directory']) / config['root_assembly']
    if not assembly.resolve().is_relative_to(local(config['cad_directory'])):
        raise ValueError('root assembly escapes CAD directory')
    digest = sha256(assembly.read_bytes()).hexdigest()
    if graph.get('assembly_file') != config['root_assembly']:
        raise ValueError('CAD graph belongs to another root assembly')
    recorded = graph.get('authoritative_assembly', {}).get('sha256', graph.get('assembly_sha256', ''))
    if recorded.removeprefix('sha256:') != digest:
        raise ValueError('CAD graph assembly hash does not match source')
    inventory = inventory_models(local(config['cad_directory']), bom)
    mapping = map_bom_to_occurrences(bom, inventory, graph)
    package = ingest(product, output / 'source-analysis') if with_qsl else None
    observations = read_card_quantities([local(n) for n in config['process_cards']]) if with_qsl else []
    draft = compile_draft(bom, mapping, graph, package, observations)
    draft.update(source_bom=str(bom_file), source_bom_sha256=sha256(bom_file.read_bytes()).hexdigest(),
        source_assembly=str(assembly), source_assembly_sha256=digest,
        graph_sha256=sha256(graph_file.read_bytes()).hexdigest(), qsl_enabled=with_qsl)
    output.mkdir(parents=True, exist_ok=True)
    def save(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    save('出版排序内容.json', draft)
    save('cad-mapping.json', asdict(mapping))
    comparison = None
    if history_file:
        comparison = compare_history(draft, json.loads(history_file.read_text(encoding='utf-8-sig')))
        save('历史对照.json', comparison)
    lines = ['# 出版输入重建验证', '',
        '此结果是未出图的规划草案；不替代既有成品，不具备生产出版资格。', '',
        f"部件范围：{draft['operation_count']}；物料行状态：{draft['summary']}。", '',
        'BOM 数量保留本级用量，并乘构造父级数量；CAD 实例按完整路径计数。',
        '黄色来料内部不展开。候选超量、父级不符和重复计料均不自动放行。',
        'QSL 表格数量是页面/工艺范围内的观察值，不默认等于整机总量。',
        'QSL 文本和力矩暂以候选证据关联；尚未完成适用范围确认，不生成力矩标注。', '',
        '## 尚未确认的物料', '',
        '| BOM 行 | 名称 | 本级/整机数量 | CAD 候选数 | 状态 | QSL数量观察 |',
        '| --- | --- | --- | --- | --- | --- |']
    for entry in draft['allocation_ledger']:
        if entry['status'] in {'matched', 'construction_context'}:
            continue
        observations = '; '.join(dict.fromkeys(
            f"{q['sheet']}:{q['quantity_text']}" for q in entry['qsl_observations']))
        lines.append(f"| {entry['row']} | {entry['name']} | {entry['quantity_local']}/{entry['quantity_total']} | "
            f"{len(entry['candidate_occurrences'])} | {entry['status']} | {observations.replace('|', '／')} |")
    if comparison:
        lines += ['', '## 历史对照', '', str(comparison['summary']), '',
            'same 包括构造上下文等行，不等同于已确认的安装工序数量。',
            '完整历史分组、每组数量及实例见历史对照.json；这些只用于比较，未参与新分配。',
            '历史未记录的数量取舍原因不能从最终数字倒推。']
    (output / '重建验证.md').write_text('\n'.join(lines) + '\n', encoding='utf8')
    return draft


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('product', 'graph', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--history', type=Path)
    parser.add_argument('--with-qsl', action='store_true')
    args = parser.parse_args()
    draft = rebuild_publication(args.product, args.graph, args.output, args.history, args.with_qsl)
    print(json.dumps({'operation_count': draft['operation_count'], 'summary': draft['summary'],
        'blocking_rows': len(draft['blocking_rows']), 'production_eligible': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
