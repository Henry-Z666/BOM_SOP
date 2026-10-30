from dataclasses import replace
from types import SimpleNamespace
import unittest

from sop_pipeline.agent.bom_normalizer import NormalizedBom, NormalizedBomRow
from sop_pipeline.agent.publication_rebuild import compile_draft, compare_history


def row(number, level, quantity=1, boundary=False):
    return NormalizedBomRow(number, level, f'm{number}', f'd{number}', f'part{number}',
        '', quantity, '件', '', '', '', None, False,
        level_fill_rgb='FFFF00' if boundary else '', assembly_boundary=boundary)


def mapping(rows, allocations):
    return SimpleNamespace(rows=[SimpleNamespace(bom_row=r.row,
        parent_bom_row=next((p.row for p in reversed(rows[:i])
                            if r.level.startswith(p.level + '.')), None),
        occurrence_ids=tuple(allocations.get(r.row, [])), status='matched'
        if allocations.get(r.row) else 'missing', evidence='test exact identity')
        for i, r in enumerate(rows)])


def graph(*ids):
    return {'occurrences': [{'id': identity} for identity in ids], 'constraints': []}


class PublicationRebuildTests(unittest.TestCase):
    def bom(self, rows):
        return NormalizedBom('normalized-bom/v1', 'BOM', 1, {}, tuple(rows), ())

    def test_repeated_assembly_multiplies_quantity_and_groups_by_component(self):
        rows = [row(2, '0'), row(3, '0.1', 2), row(4, '0.1.1', 2, True)]
        ids = ['a/1', 'a/2', 'b/1', 'b/2']
        draft = compile_draft(self.bom(rows), mapping(rows, {2: ['ROOT'], 3: ['a', 'b'], 4: ids}), graph('a', 'b', *ids))
        self.assertEqual(draft['operation_count'], 1)
        self.assertEqual(draft['operations'][0]['materials'][0]['quantity'], 4)
        self.assertFalse(draft['production_eligible'])

    def test_yellow_supplier_children_never_become_materials(self):
        rows = [row(2, '0'), row(3, '0.1', boundary=True), row(4, '0.1.1')]
        draft = compile_draft(self.bom(rows), mapping(rows, {2: ['ROOT'], 3: ['a'], 4: ['a/1']}), graph('a', 'a/1'))
        self.assertEqual([m['row'] for o in draft['operations'] for m in o['materials']], [3])
        self.assertNotIn(4, [entry['row'] for entry in draft['allocation_ledger']])

    def test_surplus_instances_are_not_sliced_to_expected_count(self):
        rows = [row(2, '0'), row(3, '0.1', 1, True)]
        draft = compile_draft(self.bom(rows), mapping(rows, {2: ['ROOT'], 3: ['a', 'b']}), graph('a', 'b'))
        self.assertEqual(draft['operations'], [])
        self.assertEqual(draft['blocking_rows'], [3])
        self.assertEqual(draft['allocation_ledger'][1]['candidate_occurrences'], ['a', 'b'])

    def test_global_match_outside_parent_is_not_allocated(self):
        rows = [row(2, '0'), row(3, '0.1'), row(4, '0.1.1', 1, True)]
        draft = compile_draft(self.bom(rows), mapping(rows, {2: ['ROOT'], 3: ['a'], 4: ['b/1']}), graph('a', 'b/1'))
        self.assertEqual(draft['allocation_ledger'][2]['status'], 'unproven_parent_scope')

    def test_duplicate_allocation_blocks_both_rows(self):
        rows = [row(2, '0'), row(3, '0.1', 1, True), row(4, '0.2', 1, True)]
        draft = compile_draft(self.bom(rows), mapping(rows, {2: ['ROOT'], 3: ['a'], 4: ['a']}), graph('a'))
        self.assertEqual(draft['operations'], [])
        self.assertEqual(draft['blocking_rows'], [3, 4])

    def test_qsl_count_is_preserved_without_overriding_bom_or_applying_torque(self):
        rows = [row(2, '0'), row(3, '0.1', 6, True)]
        ids = list('abcdef')
        package = {'operations': [{'operation_id': 'q1', 'materials': ['m3'],
                                  'torque_claims': [{'value': '10'}]}]}
        draft = compile_draft(self.bom(rows), mapping(rows, {2: ['ROOT'], 3: ids}),
            graph(*ids), package, [{'material_code': 'm3', 'quantity_text': '4', 'sheet': 'QSL1'}])
        self.assertEqual(draft['operations'][0]['materials'][0]['quantity'], 6)
        self.assertEqual(draft['allocation_ledger'][1]['qsl_observations'][0]['quantity_text'], '4')
        self.assertEqual(draft['allocation_ledger'][1]['qsl_quantity_assessment'][0]['comparison'],
                         'different_quantity_scope_requires_check')
        self.assertNotIn('torque_annotations', draft['operations'][0])

    def test_history_comparison_never_mutates_new_draft(self):
        rows = [row(2, '0'), row(3, '0.1', 1, True)]
        draft = compile_draft(self.bom(rows), mapping(rows, {2: ['ROOT'], 3: ['a']}), graph('a'))
        old = {'operations': [{'number': 99, 'title': 'historical group', 'materials': [{'row': 3, 'material_code': 'm3', 'quantity': 7, 'occurrence_ids': ['old']}]}]}
        report = compare_history(draft, old)
        self.assertFalse(report['history_used_as_input'])
        self.assertEqual(draft['operations'][0]['materials'][0]['occurrence_ids'], ['a'])
        self.assertEqual(report['rows'][1]['comparison'], 'different')

    def test_numeric_length_is_not_replaced_with_one_cad_instance(self):
        rows = [row(2, '0'), replace(row(3, '0.1', 1, True), unit='米')]
        draft = compile_draft(self.bom(rows), mapping(rows, {2: ['ROOT'], 3: ['a']}), graph('a'))
        self.assertEqual(draft['operations'], [])
        self.assertEqual(draft['allocation_ledger'][1]['status'],
                         'non_count_quantity_requires_process_allocation')

    def test_new_bom_quantity_and_new_cad_paths_drive_result(self):
        first = [row(2, '0'), row(3, '0.1', 1, True)]
        second = [row(2, '0'), row(3, '0.1', 2, True)]
        a = compile_draft(self.bom(first), mapping(first, {2: ['ROOT'], 3: ['a']}), graph('a'))
        b = compile_draft(self.bom(second), mapping(second, {2: ['ROOT'], 3: ['new/1', 'new/2']}), graph('new/1', 'new/2'))
        self.assertEqual(a['operations'][0]['materials'][0]['quantity'], 1)
        self.assertEqual(b['operations'][0]['materials'][0]['quantity'], 2)
        self.assertEqual(b['operations'][0]['materials'][0]['occurrence_ids'], ['new/1', 'new/2'])
