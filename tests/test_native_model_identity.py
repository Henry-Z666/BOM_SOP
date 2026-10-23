import unittest

from sop_pipeline.agent.bom_cad_mapper import _node_identifiers
from sop_pipeline.agent.formal_render_planner import _fixed_scope_parent
from sop_pipeline.agent.bom_cad_mapper import BomOccurrenceMapping


class NativeModelIdentityTests(unittest.TestCase):
    def test_report_drawing_can_match_exact_native_parameter(self):
        node = {'model_name': 'VENDOR', 'parameters': {'DRAWING_NO': 'SX0100105704', 'DESCRIPTION': 'unrelated'}}
        identities = _node_identifiers(node)
        self.assertIn('SX0100105704', identities)
        self.assertNotIn('UNRELATED', identities)

    def test_family_generic_does_not_identify_installed_instance(self):
        identities = _node_identifiers({'descriptor_generic': 'GENERIC', 'descriptor_instance': 'INSTANCE.prt'})
        self.assertIn('INSTANCE', identities)
        self.assertNotIn('GENERIC', identities)

    def test_default_placement_identifies_nested_foundation(self):
        mapping = BomOccurrenceMapping(360, '7.1', 'X', 'base', 1, 359, ('4461/149',), 'matched', 'native')
        self.assertEqual(_fixed_scope_parent(mapping, ({'type': 'DEFAULT_PLACEMENT', 'occurrences': ['4461/149', '4461']},)), '4461')

    def test_creo_report_parameters_bind_vendor_named_pump(self):
        identities = _node_identifiers({'model_name':'E1631G18KW', 'parameters':{
            'SL_PRT_NUMBER':'130201000295', 'P_MODEL_NAME':'SX0100105704',
            'PTC_COMMON_NAME':'离心泵', 'PWGXH_CODE':'e1631 2.5B-18.5kW-2P/B5FAAATTZ-叶轮直径192.7mm'}})
        self.assertIn('SX0100105704', identities)
        self.assertIn('130201000295', identities)
        self.assertNotIn('离心泵', identities)


if __name__ == '__main__':
    unittest.main()
