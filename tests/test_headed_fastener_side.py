import unittest
from copy import deepcopy
from sop_pipeline.agent.headed_fastener_side import headed_fastener_side,validate_proven_headed_directions


class HeadedFastenerSideTests(unittest.TestCase):
    def fixture(self):
        p={'image_coverage':{'role':'detail'},'moving_occurrences':['bolt'],
           'material_annotations':{'material_rows':[{'name':'六角头螺栓','occurrence_ids':['bolt']}]},
           'stage_geometry_root':{'moving_bounds':[{'min':[-4,-16,-4],'max':[4,7,4]}]}}
        ref={'occurrence_id':'base','item_id':1,'item_type':'SURFACE','geometry':{'status':'available','point_root':[0,0,0],'direction_root':[0,-1,0]}}
        g={'assembly_sha256':'source','occurrences':[{'occurrence_id':'bolt','part_no':'M6X20.prt'},
           {'occurrence_id':'base','bounds_root':{'status':'available','min':[-20,-2,-20],'max':[20,0,20]}}],
           'constraints':[{'id':'seat','type':'MATE','occurrences':['bolt','base'],'assembly_reference':ref,'component_reference':deepcopy(ref)}]}
        return p,g

    def test_head_side_is_independent_of_plane_normal_sign(self):
        p,g=self.fixture()
        for sign in (-1,1):
            for key in ('assembly_reference','component_reference'):g['constraints'][0][key]['geometry']['direction_root']=[0,sign,0]
            self.assertEqual(headed_fastener_side(p,g)['head_side_root'],[0,1,0])

    def test_cylinder_axis_does_not_prove_head_side(self):
        p,g=self.fixture();g['constraints'][0]['type']='INSERT'
        with self.assertRaises(ValueError):headed_fastener_side(p,g)

    def test_slightly_rotated_root_aabb_at_nominal_head_limit(self):
        p,g=self.fixture();p['stage_geometry_root']['moving_bounds'][0]={'min':[-4,-16,-4],'max':[4,7.51,4]}
        # The nominal M6 head limit is 7.5 mm; transverse root-AABB
        # projection contributes a bounded excess for this small rotation.
        for key in ('assembly_reference','component_reference'):
            g['constraints'][0][key]['geometry']['direction_root']=[.00432,.9999906688,0]
        self.assertGreater(headed_fastener_side(p,g)['head_side_root'][1],.999)

    def test_nut_and_symmetric_body_are_not_inferred(self):
        p,g=self.fixture();p['material_annotations']['material_rows'][0]['name']='六角螺母'
        with self.assertRaises(ValueError):headed_fastener_side(p,g)
        p,g=self.fixture();p['stage_geometry_root']['moving_bounds'][0]['min'][1]=-7
        with self.assertRaises(ValueError):headed_fastener_side(p,g)

    def test_remote_seat_is_rejected(self):
        p,g=self.fixture();g['constraints'][0]['component_reference']['geometry']['point_root']=[100,0,0]
        with self.assertRaises(ValueError):headed_fastener_side(p,g)

    def test_publication_rejects_tip_first_or_sideways_movement(self):
        p,g=self.fixture();p['headed_fastener_side_evidence']=headed_fastener_side(p,g)
        for v in ([0,-20,0],[10,20,0]):
            p['occurrence_translation_vectors_root']={'bolt':v}
            with self.assertRaises(ValueError):validate_proven_headed_directions(p)
        p['occurrence_translation_vectors_root']={'bolt':[0,20,0]}
        validate_proven_headed_directions(p)
