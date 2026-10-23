import unittest
from unittest.mock import patch
from types import SimpleNamespace
from sop_pipeline.agent.publication_scope import validate_publication_scope,effective_source_quantity

class SourceQuantityTests(unittest.TestCase):
    def test_child_quantity_multiplies_constructed_parent_count(self):
        rows={2:SimpleNamespace(level='0',quantity=1),3:SimpleNamespace(level='0.32',quantity=22),4:SimpleNamespace(level='0.32.2',quantity=2)}
        self.assertEqual(effective_source_quantity(rows[4],rows),44)
    def publication(self):
        return {'source_bom':'source.xlsx','source_bom_sha256':'sha256:abc',
                'color_boundaries':{'schema_version':'bom-color-boundary/v1','rows':{'8':{'role':'process_context','owner_row':None}}},
                'operations':[{'materials':[{'row':8,'material_code':'cable','name':'线缆','quantity':.633,'unit':'米','occurrence_ids':[],
                'quantity_basis':'source_bom_without_independent_cad_occurrence','non_modeled_evidence':{'mapping_status':'non_renderable','source_process_records':[{'sheet':'布线'}]}}]}], 'images':[]}
    def source(self):return {8:SimpleNamespace(material_code='cable',name='线缆',quantity=.633,unit='米')}
    def test_preserve_fractional_source_use(self):
        with patch('sop_pipeline.agent.publication_scope._source_quantities',return_value=self.source()):validate_publication_scope(self.publication())
    def test_reject_count_replacing_length(self):
        d=self.publication();d['operations'][0]['materials'][0]['quantity']=1
        with patch('sop_pipeline.agent.publication_scope._source_quantities',return_value=self.source()),self.assertRaises(ValueError):validate_publication_scope(d)
    def test_reject_supplier_internal_source_only_row(self):
        d=self.publication();d['color_boundaries']['rows']['8'].update(role='internal',owner_row=7)
        with self.assertRaises(ValueError):validate_publication_scope(d)
    def test_reject_invented_occurrence(self):
        d=self.publication();d['operations'][0]['materials'][0]['occurrence_ids']=['fake']
        with self.assertRaises(ValueError):validate_publication_scope(d)
    def test_reject_split_native_steps_exceeding_one_bom_quantity(self):
        d=self.publication();material=d['operations'][0]['materials'][0]
        material.pop('quantity_basis');material.update(quantity=1,occurrence_ids=['a'])
        d['operations'].append({'materials':[{**material,'occurrence_ids':['b']}]})
        source={8:SimpleNamespace(quantity=1)}
        with patch('sop_pipeline.agent.publication_scope._source_quantities',return_value=source),self.assertRaisesRegex(ValueError,'overcharged'):validate_publication_scope(d)
