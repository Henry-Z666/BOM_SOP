import math,unittest
from sop_pipeline.agent.installation_view import interface_view,installation_feature_span

class ConnectionReadabilityCameraTests(unittest.TestCase):
    def payload(self):
        box={'min':[-40,-40,-40],'max':[40,40,40]}
        return {'moving_occurrences':['m'],'visible_occurrences':['m','r'],'receiver_normal_root':[0,0,1],
                'translation_vector_root':[0,0,500],'occurrence_translation_vectors_root':{'m':[0,0,500]},
                'stage_geometry_root':{'moving_bounds':[{'min':[0,0,0],'max':[10,10,20]}],'context_bounds':[box]},
                'arrow_anchors':[{'complete_point_root':[0,0,0],'expected_exploded_point_root':[0,0,500]}],
                'presentation':{'variants':[{'camera_id':'fixed_123'}],'frame_gate':{}},
                'connection_camera':{'schema_version':'contact-interior-oblique/v1','foundation_occurrence':'r','foundation_bounds':box,'scale_policy':'projected-travel-to-feature/v1'}}
    def test_long_travel_uses_readable_projected_scale_without_moving_installed_pose(self):
        p=self.payload();o=interface_view(p);direction=o['camera']['position_direction_root'];normal=p['receiver_normal_root'];dot=sum(a*b for a,b in zip(normal,direction))
        projected=500*math.sqrt(max(0,1-dot*dot))
        self.assertLessEqual(projected,4*installation_feature_span(p)+1.e-6)
        self.assertGreater(projected,0)
        self.assertEqual(o['arrow_anchors'],p['arrow_anchors']);self.assertEqual(o['translation_vector_root'],p['translation_vector_root'])
    def test_unknown_policy_rejected(self):
        p=self.payload();p['connection_camera']['scale_policy']='manual-angle'
        with self.assertRaises(ValueError):interface_view(p)
    def test_occlusion_candidates_preserve_installation_geometry(self):
        directions=[]
        for quadrant in range(4):
            p=self.payload();p['connection_camera']['view_quadrant']=quadrant
            out=interface_view(p);directions.append(tuple(out['camera']['position_direction_root']))
            self.assertEqual(out['translation_vector_root'],p['translation_vector_root'])
            self.assertEqual(out['arrow_anchors'],p['arrow_anchors'])
            self.assertGreater(out['camera']['position_direction_root'][2],0)
        self.assertEqual(len(set(directions)),4)
    def test_invalid_quadrant_rejected(self):
        p=self.payload();p['connection_camera']['view_quadrant']=4
        with self.assertRaises(ValueError):interface_view(p)
    def test_second_readability_pass_uses_two_feature_spans(self):
        p=self.payload();p['connection_camera']['scale_policy']='projected-travel-to-feature/v2'
        direction=interface_view(p)['camera']['position_direction_root']
        projected=500*math.sqrt(max(0,1-direction[2]**2))
        self.assertLessEqual(projected,2*installation_feature_span(p)+1.e-6)
    def test_lateral_clearance_is_observed_from_moving_side(self):
        p=self.payload();p['receiver_normal_root']=[1,0,0];p['connection_camera'].pop('scale_policy')
        out=interface_view(p)
        self.assertGreater(out['camera']['position_direction_root'][2],0)
        self.assertEqual(out['translation_vector_root'],p['translation_vector_root'])
    def test_near_vertical_camera_uses_nonparallel_up(self):
        p=self.payload();p['receiver_normal_root']=[0,1,0];p['translation_vector_root']=[0,500,0]
        out=interface_view(p);direction=out['camera']['position_direction_root'];up=out['camera']['up_reference_root']
        self.assertLess(abs(sum(a*b for a,b in zip(direction,up))),.9)

if __name__=='__main__':unittest.main()
