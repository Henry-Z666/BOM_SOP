import json
import tempfile
import unittest
from pathlib import Path
from sop_pipeline.agent.material_annotations import attach_material_numbers, validate_material_audit, annotation_channel_audit
from sop_pipeline.agent.process_model import fingerprint

class MaterialAnnotationsTests(unittest.TestCase):
    def payload(self):return {'moving_occurrences':['a','b'],'stage_geometry_root':{'context_bounds':[]}}
    def rows(self):return [{'material_code':'FIRST','name':'另一物料','occurrence_ids':['other']},{'material_code':'BOLT','name':'螺栓','occurrence_ids':['a','b']}]
    def test_page_number_survives_local_filter_and_all_instances_remain(self):
        out=attach_material_numbers(self.payload(),page_id='P2',material_rows=self.rows(),label_mode='number')
        self.assertEqual(out['material_annotations']['labels'][0]['text'],'物料 2')
        self.assertEqual(out['material_annotations']['labels'][0]['occurrence_ids'],['a','b'])
        p=self.payload();p['moving_occurrences']=['b']
        detail=attach_material_numbers(p,page_id='P2',material_rows=self.rows(),label_mode='number')
        self.assertEqual(detail['material_annotations']['labels'][0]['text'],'物料 2')
        self.assertNotIn('material_annotations',p)
    def test_default_uses_exact_source_name_and_preserves_binding(self):
        out=attach_material_numbers(self.payload(),page_id='P2',material_rows=self.rows())
        label=out['material_annotations']['labels'][0]
        self.assertEqual(label['text'],'螺栓')
        self.assertEqual(label['table_number'],2)
        self.assertEqual(label['occurrence_ids'],['a','b'])
        self.assertEqual(out['material_annotations']['label_mode'],'name')
    def test_repeated_material_has_one_callout_without_reducing_installed_instances(self):
        original=self.payload()
        out=attach_material_numbers(original,page_id='P2',material_rows=self.rows(),group_repeated_names=True)
        label=out['material_annotations']['labels'][0]
        self.assertEqual(out['moving_occurrences'],['a','b'])
        self.assertEqual(label['represents_occurrence_ids'],['a','b'])
        self.assertEqual(len(label['occurrence_ids']),1)
        self.assertEqual(label['text'],'螺栓；本图2件')
        self.assertEqual(out['material_annotations']['material_rows'],self.rows())
    def test_missing_duplicate_or_locator_bindings_block(self):
        with self.assertRaises(ValueError):attach_material_numbers(self.payload(),page_id='P2',material_rows=[{'material_code':'X','name':'X','occurrence_ids':['a']}])
        rows=self.rows();rows.append({'material_code':'Y','name':'Y','occurrence_ids':['a']})
        with self.assertRaises(ValueError):attach_material_numbers(self.payload(),page_id='P2',material_rows=rows)
        p=self.payload();p['image_coverage']={'role':'location'}
        with self.assertRaises(ValueError):attach_material_numbers(p,page_id='P2',material_rows=self.rows())
    def test_joint_audit_retains_torque_semantics_and_catches_missing_instance(self):
        p=self.payload();torque={'status':'confirmed','renderer':'creo_display_list/v1','labels':[{'text':'螺栓紧固力矩：10 N.m','connection_id':'bolt','occurrence_ids':['a']}]};torque['contract_hash']=fingerprint(torque);p['torque_annotations']=torque
        out=attach_material_numbers(p,page_id='P2',material_rows=self.rows());bundle=out['annotation_bundle']
        labels=[{'occurrence_id':o,'connection_id':l['connection_id'],'text':l['text'],'anchor_source':'native_solid_surface','target_root':[1,2,3],'text_box_camera':[0,0,5,5]} for l in bundle['labels'] for o in l['occurrence_ids']]
        with tempfile.TemporaryDirectory() as directory:
            image=Path(directory)/'sample.jpg';audit=Path(str(image)+'.note.json')
            record={'schema_version':'installation-note-audit/v1','contract_hash':bundle['contract_hash'],'overlap_check':'passed','style':'filled-cream-solid-dark/v1','labels':labels}
            audit.write_text(json.dumps(record),encoding='utf-8')
            self.assertEqual(validate_material_audit(image,out),())
            self.assertEqual(annotation_channel_audit(image,out,'torque')['labels'][0]['connection_id'],'bolt')
            record['labels']=labels[1:];audit.write_text(json.dumps(record),encoding='utf-8')
            self.assertIn('MATERIAL_LABEL_COVERAGE_MISMATCH',validate_material_audit(image,out))
            out['material_annotations']['labels'][0]['text']='物料 9'
            self.assertIn('MATERIAL_LABEL_EVIDENCE_INVALID',validate_material_audit(image,out))
