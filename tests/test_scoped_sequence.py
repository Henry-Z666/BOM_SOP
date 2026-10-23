from pathlib import Path
from dataclasses import asdict
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json,unittest
from test_formal_render_planner import fixture
from sop_pipeline.agent.formal_render_planner import compile_formal_render_plan
from sop_pipeline.agent.scoped_sequence import compile_scoped_sequence


class ScopedSequenceTests(unittest.TestCase):
    def prepare(self,root):
        bom,draft,mapping,graph=fixture()
        plan=compile_formal_render_plan(bom,draft,mapping,graph)
        first=asdict(next(s for s in plan.steps if s.moving_occurrences==('10/2',)))
        second=asdict(next(s for s in plan.steps if s.moving_occurrences==('20/2',)))
        second['receiver_occurrences']=['10/2']
        second['arrow_anchors'][0]['receiver_occurrence_id']='10/2'
        graph['source_tree_fingerprint']='locked'
        for n in graph['occurrences']:n['id']=n['occurrence_id']
        files={'product.json':{'source_root':'.','cad_directory':'models','root_assembly':'root.asm.1'},
            'process-package.json':{'product_config':str(root/'product.json'),'operations':[],'sources':[]},
            'cad-graph.json':graph,'planning-blockers.json':{'steps':[first,second],'blocked_step_ids':[],
                'color_boundaries':plan.color_boundaries}}
        for name,value in files.items():(root/name).write_text(json.dumps(value),encoding='utf-8')
        return {'sequence_id':'branch','scope':'ROOT','preassembled_occurrences':['10/1'],
            'prerequisite_reason':'fixture preassembled base','steps':[
            {'source_step_id':first['step_id'],'occurrences':['10/2'],'title':'first'},
            {'source_step_id':second['step_id'],'occurrences':['20/2'],'title':'second'}]}

    def compile(self,root,selection):
        with patch('sop_pipeline.agent.scoped_sequence._validate_graph'),patch('sop_pipeline.agent.scoped_sequence._tree_hashes',return_value={}),patch('sop_pipeline.agent.scoped_sequence._hash_manifest',return_value='locked'):
            return compile_scoped_sequence(root,selection,root/'jobs.json')

    def test_completed_parts_persist_and_future_parts_stay_hidden(self):
        with TemporaryDirectory() as d:
            root=Path(d);selection=self.prepare(root);path=self.compile(root,selection)
            tasks=json.loads(path.read_text())['tasks']
            self.assertEqual(set(tasks[0]['payload']['visible_occurrences']),{'10/1','10/2'})
            self.assertEqual(set(tasks[1]['payload']['visible_occurrences']),{'10/1','10/2','20/2'})
            self.assertEqual(tasks[1]['depends_on'],[tasks[0]['step_id']])
            self.assertFalse(tasks[1]['payload']['production_eligible'])
            self.assertTrue(tasks[0]['payload']['bom_color_boundaries'])

    def test_missing_source_color_evidence_is_rejected(self):
        with TemporaryDirectory() as d:
            root=Path(d);selection=self.prepare(root)
            source=root/'planning-blockers.json'
            draft=json.loads(source.read_text(encoding='utf-8'))
            draft.pop('color_boundaries')
            source.write_text(json.dumps(draft),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'颜色边界证据'):
                self.compile(root,selection)
            self.assertFalse((root/'jobs.json').exists())

    def test_receiver_must_exist_before_installation(self):
        with TemporaryDirectory() as d:
            root=Path(d);selection=self.prepare(root);selection['steps'].reverse()
            with self.assertRaisesRegex(ValueError,'receiver has not been installed'):self.compile(root,selection)
            self.assertFalse((root/'jobs.json').exists())

    def test_same_occurrence_cannot_be_installed_twice(self):
        with TemporaryDirectory() as d:
            root=Path(d);selection=self.prepare(root);selection['steps'].append(selection['steps'][0])
            with self.assertRaisesRegex(ValueError,'repeated'):self.compile(root,selection)
