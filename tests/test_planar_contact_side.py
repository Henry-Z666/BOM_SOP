import unittest
from unittest.mock import patch
from sop_pipeline.agent.planar_contact_side import resolve_planar_contact_side

class PlanarContactSideTests(unittest.TestCase):
    def test_planar_mate_allows_tangential_face_sample_separation(self):
        payload={'image_coverage':{'role':'detail'},'moving_occurrences':['pad'],'translation_vector_root':[0,-20,0],'arrow_anchors':[{'occurrence_id':'pad','complete_point_root':[0,0,0]}],'stage_geometry_root':{'moving_bounds':[{'min':[-10,0,-10],'max':[10,2,10]}]}}
        graph={'assembly_sha256':'source','occurrences':[{'occurrence_id':'base','bounds_root':{'status':'available','min':[-30,-2,-30],'max':[30,0,30]}}], 'constraints':[{'id':'mate','occurrences':['pad','base'],'type':'MATE_OFF','assembly_reference':{'occurrence_id':'base','item_id':1,'item_type':'SURFACE','geometry':{'status':'available','direction_root':[0,-1,0],'point_root':[-20,0,-20]}},'component_reference':{'geometry':{'status':'available','direction_root':[0,1,0],'point_root':[0,0,0]}}}]}
        with patch('sop_pipeline.agent.installation_view.interface_view',side_effect=lambda p:p):
            result=resolve_planar_contact_side(payload,graph)
        self.assertEqual(result['translation_vector_root'],[0,20,0])
        self.assertEqual(result['constraint_ids'],['mate'])
        graph['constraints'][0]['type']='ALIGN_OFF';graph['constraints'][0]['offset']=.8
        graph['constraints'][0]['assembly_reference']['geometry']['point_root'][1]=-.8
        with patch('sop_pipeline.agent.installation_view.interface_view',side_effect=lambda p:p):
            result=resolve_planar_contact_side(payload,graph,bound_travel=True)
        self.assertEqual(result['translation_vector_root'],[0,7,0])
        graph['constraints'][0]['offset']=2
        with self.assertRaises(ValueError):resolve_planar_contact_side(payload,graph)
    def test_rejects_long_axial_part_without_a_proven_thin_face(self):
        payload={'image_coverage':{'role':'detail'},'moving_occurrences':['bolt'],'stage_geometry_root':{'moving_bounds':[{'min':[-2,0,-2],'max':[2,30,2]}]}}
        graph={'occurrences':[],'constraints':[{'id':'c','occurrences':['bolt','base'],'type':'INSERT','assembly_reference':{'item_type':'SURFACE','geometry':{'status':'available','direction_root':[0,1,0],'point_root':[0,0,0]}},'component_reference':{'geometry':{'status':'available','point_root':[0,0,0]}}}]}
        with self.assertRaisesRegex(ValueError,'No local native'):resolve_planar_contact_side(payload,graph)
    def test_rejects_remote_height_datum(self):
        payload={'image_coverage':{'role':'detail'},'moving_occurrences':['pad'],'stage_geometry_root':{'moving_bounds':[{'min':[-20,0,-10],'max':[20,4,10]}]}}
        graph={'occurrences':[],'constraints':[{'id':'c','occurrences':['pad','base'],'type':'MATE_OFF','assembly_reference':{'item_type':'SURFACE','geometry':{'status':'available','direction_root':[0,1,0],'point_root':[500,0,0]}},'component_reference':{'geometry':{'status':'available','point_root':[0,0,0]}}}]}
        with self.assertRaisesRegex(ValueError,'No local native'):resolve_planar_contact_side(payload,graph)
