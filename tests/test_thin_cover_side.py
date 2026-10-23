import unittest
from sop_pipeline.agent.thin_cover_side import resolve_thin_cover_side


class ThinCoverSideTests(unittest.TestCase):
    def fixture(self,low,high):
        p={'moving_occurrences':['cover'],'stage_geometry_root':{'moving_bounds':[{'min':[low,-20,-20],'max':[high,20,20]}]},'arrow_anchors':[{'complete_point_root':[low,0,0]}]}
        ref={'occurrence_id':'fan','item_id':1,'item_type':'SURFACE','geometry':{'direction_root':[1,0,0]}}
        g={'assembly_sha256':'source','occurrences':[{'occurrence_id':'fan','bounds_root':{'status':'available','min':[-10,-25,-25],'max':[10,25,25]}}],'constraints':[{'id':'axis','occurrences':['cover','fan'],'type':'INSERT','assembly_reference':ref}]}
        return p,g

    def test_opposite_external_faces_withdraw_in_opposite_directions(self):
        for lo,hi,sign in ((10,14,1),(-15,-11,-1)):
            p,g=self.fixture(lo,hi);out=resolve_thin_cover_side(p,g)
            self.assertEqual(out['receiver_normal_root'],[sign,0,0])
            self.assertEqual(out['arrow_anchors'][0]['complete_point_root'],p['arrow_anchors'][0]['complete_point_root'])
            self.assertGreater(out['translation_vector_root'][0]*sign,0)

    def test_embedded_remote_and_tangentially_disjoint_plates_rejected(self):
        for lo,hi in ((-2,2),(30,34)):
            p,g=self.fixture(lo,hi)
            with self.assertRaises(ValueError):resolve_thin_cover_side(p,g)
        p,g=self.fixture(10,14);p['stage_geometry_root']['moving_bounds'][0]['min'][1]=40;p['stage_geometry_root']['moving_bounds'][0]['max'][1]=80
        with self.assertRaises(ValueError):resolve_thin_cover_side(p,g)
