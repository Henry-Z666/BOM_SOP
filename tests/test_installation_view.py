import unittest
from sop_pipeline.agent.installation_view import interface_view, generalized_view, installation_image_roles, expand_installation_views, bounded_display_translation, with_native_connection_camera

class InstallationViewTests(unittest.TestCase):
    def test_rebinding_camera_preserves_requested_detail_frame_fraction(self):
        p=self.payload();p['installation_view']={'composition_policy':'single-image-balanced-fit/v15','roi_frame_fraction':.9}
        p['image_coverage']={'role':'detail'}
        out=interface_view(p)
        self.assertEqual(out['installation_view']['roi_frame_fraction'],.9)
        self.assertEqual(out['translation_vector_root'],p['translation_vector_root'])
    def test_contact_camera_uses_deepest_visible_receiver_ancestor(self):
        boxes=[{'min':[-20,-20,-20],'max':[20,20,20]},{'min':[-2,-2,-2],'max':[2,2,2]}]
        p={'moving_occurrences':['bolt'],'visible_occurrences':['frame','frame/support','bolt'],'receiver_occurrences':['frame/support/hole'],'stage_geometry_root':{'context_bounds':boxes}}
        q=with_native_connection_camera(p)
        self.assertEqual(q['connection_camera']['foundation_occurrence'],'frame/support')
        self.assertEqual(q['connection_camera']['foundation_bounds'],boxes[1])
        self.assertNotIn('connection_camera',p)
        p['receiver_occurrences']=['unseen/hole']
        with self.assertRaises(ValueError):with_native_connection_camera(p)
    def test_lateral_contact_view_changes_only_camera_pitch(self):
        p=self.payload();box={'min':[-100,-10,-50],'max':[100,0,50]}
        p.update(moving_occurrences=['bolt'],visible_occurrences=['base','bolt'],arrow_anchors=[{'complete_point_root':[90,0,0]}])
        p['stage_geometry_root']['context_bounds']=[box]
        p['connection_camera']={'schema_version':'contact-interior-oblique/v1','foundation_occurrence':'base','foundation_bounds':dict(box),'normal_weight':.3}
        out=interface_view(p)
        self.assertLess(out['camera']['position_direction_root'][1],.5)
        self.assertEqual(out['translation_vector_root'],p['translation_vector_root'])
        self.assertEqual(out['arrow_anchors'],p['arrow_anchors'])
        for invalid in (0,float('nan'),2):
            p['connection_camera']['normal_weight']=invalid
            with self.assertRaises(ValueError):interface_view(p)
    def test_connection_camera_uses_native_context_and_remains_upright(self):
        p=self.payload();box={'min':[-100,-10,-50],'max':[100,0,50]}
        p['moving_occurrences']=['bolt'];p['visible_occurrences']=['base','bolt']
        p['stage_geometry_root']['context_bounds']=[box]
        p['arrow_anchors']=[{'complete_point_root':[90,0,0]}]
        p['connection_camera']={'schema_version':'contact-interior-oblique/v1','foundation_occurrence':'base','foundation_bounds':dict(box)}
        out=interface_view(p)
        self.assertEqual(out['camera']['up_reference_root'],[0,1,0])
        self.assertLess(out['camera']['position_direction_root'][0],0)
        p['connection_camera']['foundation_bounds']={'min':[0,0,0],'max':[1,1,1]}
        with self.assertRaises(ValueError):interface_view(p)
    def payload(self,span=12):
        return {'receiver_normal_root':[0,1,0],'translation_vector_root':[0,5,0],
            'stage_geometry_root':{'moving_bounds':[{'min':[0,0,0],'max':[span,12,12]}]},
            'presentation':{'variants':[{'camera_id':'fixed_123'}],'frame_gate':{}},
            'camera_catalog':{}}
    def test_small_fastener_does_not_fit_large_receiver(self):
        p=self.payload();out=interface_view(p)
        self.assertEqual(out['installation_view']['fit_scope'],'moving_only')
        self.assertEqual(out['presentation']['variants'][0]['camera_id'],'interface_oblique')
        self.assertGreater(out['camera']['position_direction_root'][1],.65)
        self.assertNotIn('installation_view',p)
        self.assertEqual(out['presentation']['frame_gate']['min_subject_span'],.18)
        self.assertEqual(out['installation_view']['image_count'],1)
        self.assertFalse(out['installation_view']['overview_when_location_ambiguous'])
    def test_large_moving_part_uses_native_contact_surfaces(self):
        out=interface_view(self.payload(1800))
        self.assertEqual(out['installation_view']['fit_scope'],'moving_interfaces')
        self.assertTrue(out['installation_view']['allow_remote_body_clipping'])
    def test_invalid_normal_is_rejected(self):
        p=self.payload();p['receiver_normal_root']=[0,0,0]
        with self.assertRaises(ValueError):interface_view(p)

    def test_large_receiver_increases_context_with_bounded_margin(self):
        p=self.payload();p['stage_geometry_root']['context_bounds']=[{'min':[0,0,0],'max':[1800,80,20]}]
        out=interface_view(p)
        self.assertGreater(out['installation_view']['context_expansion'],2)
        self.assertGreaterEqual(out['installation_view']['selected_fit_level'],.35)

    def test_distributed_parts_use_union_extent(self):
        p=self.payload();p['stage_geometry_root']['moving_bounds'].append({'min':[300,0,0],'max':[312,12,12]})
        self.assertEqual(interface_view(p)['installation_view']['moving_union_span_mm'],312)

    def test_subject_scale_is_preserved_and_bounded(self):
        p=self.payload();p['installation_view']={'subject_scale':2.2}
        out=interface_view(p)
        self.assertAlmostEqual(out['installation_view']['selected_fit_level'],1.385186,places=5)
        self.assertEqual(interface_view(out)['installation_view'],out['installation_view'])
        p['installation_view']['subject_scale']=4
        with self.assertRaises(ValueError):interface_view(p)

    def test_generalized_fit_ignores_manual_multipliers(self):
        p=self.payload();p['installation_view']={'subject_scale':2.2}
        out=generalized_view(p)
        self.assertEqual(out['installation_view']['fit_scope'],'protected_surfaces')
        self.assertEqual(out['installation_view']['selected_fit_level'],.50)
        self.assertEqual(out['installation_view']['image_count'],1)
        self.assertEqual(interface_view(out)['camera'],out['camera'])

    def test_elongated_group_uses_distribution_direction(self):
        p=self.payload();p['stage_geometry_root']['moving_bounds'].append({'min':[1500,0,0],'max':[1512,12,12]})
        out=generalized_view(p)
        self.assertGreater(abs(out['camera']['position_direction_root'][0]),.8)
        self.assertGreater(out['camera']['position_direction_root'][1],.35)

    def test_torque_steps_reserve_annotation_space_automatically(self):
        p=self.payload();p['torque_annotations']={'labels':[{'text':'卡箍紧固力矩：10 N.m；紧固后划线'}]}
        self.assertEqual(generalized_view(p)['installation_view']['selected_fit_level'],.50)

    def test_distant_repeated_parts_choose_two_roles_without_mutating_coverage(self):
        p=self.payload();p['stage_geometry_root']['moving_bounds'].append({'min':[1500,0,0],'max':[1512,12,12]})
        self.assertEqual(installation_image_roles(p),['location','detail'])
        self.assertEqual(len(p['stage_geometry_root']['moving_bounds']),2)

    def test_nearby_parts_remain_single_image(self):
        p=self.payload();p['stage_geometry_root']['moving_bounds'].append({'min':[20,0,0],'max':[32,12,12]})
        self.assertEqual(installation_image_roles(p),['installation'])

    def test_v5_context_mapping_is_monotonic_and_bounded(self):
        values=[]
        for span in [12,24,96,768,12000]:
            p=self.payload();p['stage_geometry_root']['context_bounds']=[{'min':[0,0,0],'max':[span,12,12]}]
            values.append(generalized_view(p)['installation_view']['selected_fit_level'])
        self.assertEqual(values,sorted(values))
        self.assertTrue(all(.5<=v<=.82 for v in values))

    def test_v5_reserves_quantity_note_without_torque_claim(self):
        p=self.payload();p['installation_notes']={'labels':[{'text':'共四处'}]}
        out=generalized_view(p)
        self.assertEqual(out['installation_view']['selected_fit_level'],.35)
        self.assertNotIn('torque_annotations',out)

    def test_multiview_details_preserve_all_locations_without_representative_guess(self):
        p=self.payload();p['moving_occurrences']=['a','b'];p['visible_occurrences']=['a','b','receiver']
        p['stage_geometry_root']['moving_bounds'].append({'min':[1500,0,0],'max':[1512,12,12]})
        p['occurrence_translation_vectors_root']={'a':[0,5,0],'b':[0,5,0]}
        p['arrow_anchors']=[{'occurrence_id':o,'constraint_id':o+'c','complete_point_root':[i*1500,0,0]} for i,o in enumerate(['a','b'])]
        views=expand_installation_views(p)
        self.assertEqual(len(views),3)
        self.assertEqual([o for v in views[1:] for o in v['moving_occurrences']],['a','b'])
        self.assertEqual(p['moving_occurrences'],['a','b'])
        self.assertEqual(views[0]['image_coverage']['role'],'location')
        self.assertTrue(all(v['image_coverage']['operation_occurrences']==['a','b'] for v in views))

    def test_geometric_decisions_are_invariant_to_common_unit_scale(self):
        from copy import deepcopy
        p=self.payload();p['moving_occurrences']=['a','b'];p['stage_geometry_root']['moving_bounds'].append({'min':[1500,0,0],'max':[1512,12,12]})
        p['stage_geometry_root']['context_bounds']=[{'min':[0,0,0],'max':[1800,100,100]}]
        baseline=generalized_view(p)
        for scale in [.001, .0254, 10, 1000]:
            q=deepcopy(p)
            for key in ['moving_bounds','context_bounds']:
                for b in q['stage_geometry_root'][key]:
                    for corner in ['min','max']:b[corner]=[x*scale for x in b[corner]]
            self.assertEqual(installation_image_roles(q),installation_image_roles(p))
            out=generalized_view(q)
            self.assertEqual(out['installation_view']['selected_fit_level'],baseline['installation_view']['selected_fit_level'])
            self.assertEqual(out['camera'],baseline['camera'])

    def test_short_display_distance_requires_complete_clearance(self):
        p=self.payload();p.update(moving_occurrences=['a'],visible_occurrences=['a','r'],translation_vector_root=[0,100,0],occurrence_translation_vectors_root={'a':[0,100,0]})
        p['stage_geometry_root']['context_bounds']=[{'min':[0,0,0],'max':[12,12,12]}]
        self.assertEqual(bounded_display_translation(p)['translation_vector_root'],[0,18,0])
        self.assertEqual(p['translation_vector_root'],[0,100,0])
        p['stage_geometry_root']['context_bounds'][0]['max'][1]=300
        self.assertEqual(bounded_display_translation(p)['translation_vector_root'],[0,100,0])
        p['visible_occurrences'].append('unknown')
        self.assertNotIn('presentation_translation',bounded_display_translation(p))

    def test_long_thin_group_requires_contact_details_even_when_body_is_large(self):
        p=self.payload();p['moving_occurrences']=['a','b']
        p['stage_geometry_root']['moving_bounds']=[{'min':[0,0,0],'max':[1690,25,65]},{'min':[0,0,310],'max':[1690,25,375]}]
        self.assertEqual(installation_image_roles(p),['location','detail'])

    def test_multiview_does_not_copy_unreviewed_torque_bindings(self):
        p=self.payload();p['moving_occurrences']=['a','b'];p['visible_occurrences']=['a','b','r'];p['occurrence_translation_vectors_root']={'a':[0,5,0],'b':[0,5,0]}
        p['stage_geometry_root']['moving_bounds'].append({'min':[1500,0,0],'max':[1512,12,12]})
        p['arrow_anchors']=[{'occurrence_id':o,'constraint_id':o+'c','complete_point_root':[i*1500,0,0]} for i,o in enumerate(['a','b'])]
        p['torque_annotations']={'labels':[{'text':'紧固力矩：10 N.m','occurrence_ids':['a','b']}]}
        with self.assertRaisesRegex(ValueError,'annotation rebinding'):expand_installation_views(p)

    def test_current_locator_locks_complete_anchors_and_preserves_exact_cover(self):
        p=self.payload();p.update(moving_occurrences=['a','b'],visible_occurrences=['a','b','r'],occurrence_translation_vectors_root={'a':[0,5,0],'b':[0,5,0]})
        p['stage_geometry_root']['moving_bounds'].append({'min':[1500,0,0],'max':[1512,12,12]})
        p['stage_geometry_root']['context_bounds']=[{'min':[0,-10,0],'max':[1600,0,12]}]
        p['arrow_anchors']=[{'occurrence_id':o,'constraint_id':o+'c','complete_point_root':[i*1500,0,0]} for i,o in enumerate(['a','b'])]
        views=expand_installation_views(p)
        self.assertEqual(views[0]['installation_notes']['anchor_state'],'authoritative_complete')
        self.assertEqual(views[0]['installation_notes']['layout_policy'],'ordered-perimeter-lane/v1')
        self.assertEqual([o for v in views[1:] for o in v['moving_occurrences']],['a','b'])
        self.assertTrue(all(v['moving_visibility_evidence']=='native-occurrence-difference/v1' for v in views[1:]))
        self.assertNotIn('moving_visibility_evidence',views[0])


class DisplaySideResolutionTests(unittest.TestCase):
    def payload(self):
        return {'moving_occurrences':['m'],'visible_occurrences':['m','r'],'receiver_normal_root':[0,0,1],
                'translation_vector_root':[0,0,6],'occurrence_translation_vectors_root':{'m':[0,0,6]},
                'stage_geometry_root':{'moving_bounds':[{'min':[0,0,-10],'max':[10,10,-5]}],
                                       'context_bounds':[{'min':[-5,-5,-10],'max':[20,20,100]}]},
                'arrow_anchors':[{'complete_point_root':[5,5,-5]}]}
    def test_reverse_side_requires_complete_clearance_and_preserves_installed_point(self):
        from sop_pipeline.agent.installation_view import resolve_display_side
        p=self.payload();out=resolve_display_side(p)
        self.assertEqual(out['translation_vector_root'],[0,0,-7.5])
        self.assertEqual(out['receiver_normal_root'],[0,0,-1])
        self.assertEqual(out['arrow_anchors'][0]['complete_point_root'],[5,5,-5])
        self.assertEqual(out['arrow_anchors'][0]['expected_exploded_point_root'],[5,5,-12.5])
        self.assertNotIn('display_side_resolution',p)
    def test_missing_background_or_blocked_reverse_side_does_not_change_pose(self):
        from sop_pipeline.agent.installation_view import resolve_display_side
        p=self.payload();p['visible_occurrences'].append('unknown')
        self.assertNotIn('display_side_resolution',resolve_display_side(p))
        p=self.payload();p['stage_geometry_root']['context_bounds'][0]['min'][2]=-100
        self.assertNotIn('display_side_resolution',resolve_display_side(p))
    def test_clear_original_side_is_retained(self):
        from sop_pipeline.agent.installation_view import resolve_display_side
        p=self.payload();p['stage_geometry_root']['context_bounds'][0]['max'][2]=-11
        self.assertNotIn('display_side_resolution',resolve_display_side(p))

    def test_v11_resolves_side_before_camera_planning(self):
        from sop_pipeline.agent.installation_view import generalized_view
        p=self.payload();p['presentation']={'variants':[{'camera_id':'fixed_123'}],'frame_gate':{}};p['camera_catalog']={}
        out=generalized_view(p,policy='single-image-balanced-fit/v11')
        self.assertLess(out['camera']['position_direction_root'][2],0)
        self.assertEqual(out['display_side_resolution']['original_vector_root'],[0,0,6])
        with self.assertRaises(ValueError):generalized_view(p,policy='unknown')


class FeatureReadabilityRoleTests(unittest.TestCase):
    def test_two_small_valves_below_legacy_threshold_require_detail(self):
        p={'receiver_normal_root':[1,0,0],'stage_geometry_root':{'moving_bounds':[{'min':[0,0,0],'max':[28,14,14]},{'min':[128,252,0],'max':[156,266,14]}]}}
        self.assertEqual(installation_image_roles(p,policy='single-image-balanced-fit/v12'),['installation'])
        self.assertEqual(installation_image_roles(p,policy='single-image-balanced-fit/v13'),['location','detail'])
    def test_one_large_thin_ring_requires_whole_location_and_local_contact(self):
        p={'receiver_normal_root':[1,0,0],'stage_geometry_root':{'moving_bounds':[{'min':[0,0,0],'max':[5.5,119,119]}]}}
        self.assertEqual(installation_image_roles(p),['location','detail'])
        self.assertEqual(installation_image_roles(p,policy='single-image-balanced-fit/v12'),['installation'])


class AxisClearanceIntervalTests(unittest.TestCase):
    def test_display_gap_uses_geometry_interval_and_retains_installed_point(self):
        from sop_pipeline.agent.installation_view import clear_display_translation
        p={'moving_occurrences':['m'],'visible_occurrences':['m','r'],'receiver_normal_root':[1,0,0],
           'translation_vector_root':[2,0,0],'occurrence_translation_vectors_root':{'m':[2,0,0]},
           'stage_geometry_root':{'moving_bounds':[{'min':[-2,0,0],'max':[2,2,2]}],'context_bounds':[{'min':[-10,-5,-5],'max':[10,5,5]}]},
           'arrow_anchors':[{'complete_point_root':[0,0,0]}]}
        out=clear_display_translation(p)
        self.assertEqual(out['translation_vector_root'],[13,0,0])
        self.assertEqual(out['arrow_anchors'][0]['complete_point_root'],[0,0,0])
        self.assertEqual(out['arrow_anchors'][0]['expected_exploded_point_root'],[13,0,0])
        self.assertEqual(p['translation_vector_root'],[2,0,0])
        p['stage_geometry_root']['context_bounds'][0]['min'][0]=-100
        p['stage_geometry_root']['context_bounds'][0]['max'][0]=100
        out=clear_display_translation(p)
        self.assertEqual(out['display_clearance_status'],'unresolved_within_feature_bound')
        self.assertEqual(out['translation_vector_root'],[2,0,0])
