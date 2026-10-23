from sop_pipeline.agent.bom_visibility_frontier import classify_failed_references
import unittest


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(value) for name, value in globals().items()
                              if name.startswith('test_') and callable(value))


def test_unmapped_legacy_reference_is_not_proof_of_missing_bom_material():
    graph = {'occurrences': [{'occurrence_id': '10/20', 'model_name': 'old',
                             'retrieval_status': 'failed', 'feature_status_code': 0}]}
    mapping = {'rows': [{'bom_row': 1, 'occurrence_ids': ['10']}]}
    colors = {'rows': {'1': {'role': 'process_context'}}}
    assert classify_failed_references(graph, mapping, colors)[0]['classification'] == 'unmapped_active_reference_needs_identity_review'


def test_supplier_internal_geometry_and_white_external_material_remain_required():
    graph = {'occurrences': [
        {'occurrence_id': '10/20', 'model_name': 'supplier_internal', 'retrieval_status': 'failed', 'feature_status_code': 0},
        {'occurrence_id': '30', 'model_name': 'white_external', 'retrieval_status': 'failed', 'feature_status_code': 0}]}
    mapping = {'rows': [{'bom_row': 1, 'occurrence_ids': ['10']}, {'bom_row': 2, 'occurrence_ids': ['30']}]}
    colors = {'rows': {'1': {'role': 'issued_unit'}, '2': {'role': 'process_context'}}}
    assert [r['classification'] for r in classify_failed_references(graph, mapping, colors)] == ['supplied_unit_geometry_required', 'current_bom_required']


def test_source_suppression_precedes_supplier_geometry_requirement():
    graph = {'occurrences': [{'occurrence_id': '10/20', 'model_name': 'history', 'retrieval_status': 'failed', 'feature_status_code': 5}]}
    mapping = {'rows': [{'bom_row': 1, 'occurrence_ids': ['10']}]}
    colors = {'rows': {'1': {'role': 'issued_unit'}}}
    assert classify_failed_references(graph, mapping, colors)[0]['classification'] == 'source_suppressed_history'
