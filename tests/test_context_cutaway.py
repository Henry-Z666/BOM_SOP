import unittest
from sop_pipeline.agent.context_cutaway import context_cutaway,ray_box_entry
class CutawayTests(unittest.TestCase):
    def test_small_occluder_opt_in_still_protects_receiver(self):
        box={'status':'available','min':[2,-4,-4],'max':[4,4,4]}
        p={'image_coverage':{'role':'detail'},'moving_occurrences':['1'],'receiver_occurrences':['2'],'visible_occurrences':['1','2','3'],'camera':{'position_direction_root':[1,0,0]},'stage_geometry_root':{'moving_bounds':[{'min':[0,0,0],'max':[0,0,0]}]},'translation_vector_root':[0,0,0]}
        g={'occurrences':[{'occurrence_id':o,'part_no':'connector.prt','bounds_root':box} for o in ['2','3']]}
        with self.assertRaises(ValueError):context_cutaway(p,g)
        result=context_cutaway(p,g,minimum_context_span=0)
        self.assertEqual(result['context_cutaway']['hidden_context_occurrences'],['3'])
        self.assertEqual(result['receiver_occurrences'],['2'])
    def test_ray_requires_forward_intersection(self):
        box={'min':[2,-1,-1],'max':[4,1,1]}
        self.assertEqual(ray_box_entry([0,0,0],[1,0,0],box),2)
        self.assertIsNone(ray_box_entry([0,0,0],[-1,0,0],box))
    def test_protects_receiver_and_preserves_pose(self):
        box={'status':'available','min':[2,-50,-50],'max':[4,50,50]}
        p={'image_coverage':{'role':'detail'},'moving_occurrences':['1'],'receiver_occurrences':['2'],'visible_occurrences':['1','2','3'],'camera':{'position_direction_root':[1,0,0]},'stage_geometry_root':{'moving_bounds':[{'min':[0,0,0],'max':[0,0,0]}]},'translation_vector_root':[0,0,0]}
        g={'occurrences':[{'occurrence_id':o,'part_no':'wall.prt','bounds_root':box} for o in ['2','3']]}
        result=context_cutaway(p,g)
        self.assertEqual(result['context_cutaway']['hidden_context_occurrences'],['3'])
        self.assertEqual(result['translation_vector_root'],p['translation_vector_root'])
        self.assertEqual(result['receiver_occurrences'],p['receiver_occurrences'])
