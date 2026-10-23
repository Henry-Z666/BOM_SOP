import unittest
from unittest.mock import patch
from types import SimpleNamespace
from dataclasses import replace
from sop_pipeline.agent.bom_normalizer import color_boundary_contract,validate_color_boundary_rows
from sop_pipeline.agent.publication_scope import validate_publication_scope
from test_supplied_assembly_scope import row,bom


class ColorBoundaryContractTests(unittest.TestCase):
    def setUp(self):
        self.contract=color_boundary_contract(bom([row(1,'0'),row(2,'0.1',True),row(3,'0.1.1'),row(4,'0.1.2',True),row(5,'0.2')]))
        source=patch('sop_pipeline.agent.publication_scope._source_quantities',return_value={2:SimpleNamespace(quantity=1)})
        source.start();self.addCleanup(source.stop)

    def test_nested_yellow_still_belongs_to_outer_issued_unit(self):
        self.assertEqual(self.contract['rows']['4']['owner_row'],2)
        for internal in [3,4]:
            with self.assertRaisesRegex(ValueError,'internal to issued unit 2'):
                validate_color_boundary_rows(self.contract,[internal])

    def test_white_outside_issued_boundary_remains_process_context(self):
        self.assertEqual(self.contract['rows']['5']['role'],'process_context')
        validate_color_boundary_rows(self.contract,[2,5])

    def publication(self):
        return {'source_bom':'fixture.xlsx','source_bom_sha256':'sha256:fixture','color_boundaries':self.contract,'operations':[{'materials':[{'row':2,'quantity':1,'occurrence_ids':['A']}]}],'images':[{'payload':{'moving_occurrences':['A'],'source_bom_rows':[2]}}]}

    def test_publication_rejects_old_internal_operation(self):
        p=self.publication();p['operations'][0]['materials'][0]['row']=3
        with self.assertRaisesRegex(ValueError,'internal'):validate_publication_scope(p)

    def test_publication_rejects_unlisted_internal_geometry_and_duplicate_charges(self):
        p=self.publication();p['images'][0]['payload']['moving_occurrences']=['A/internal']
        with self.assertRaisesRegex(ValueError,'uncharged or internal'):validate_publication_scope(p)
        p=self.publication();p['operations'].append(p['operations'][0])
        with self.assertRaisesRegex(ValueError,'more than once'):validate_publication_scope(p)

    def test_valid_whole_issued_unit_can_publish(self):
        self.assertEqual(validate_publication_scope(self.publication())['material_occurrences'],1)

    def test_plan_roundtrip_and_lock_preserve_color_ownership(self):
        from dataclasses import asdict
        from test_formal_render_planner import fixture
        from sop_pipeline.agent.formal_render_planner import compile_formal_render_plan,formal_render_plan_from_dict,lock_formal_render_plan
        from sop_pipeline.agent.render_job_compiler import compile_locked_render_jobs
        b,d,m,g=fixture();plan=compile_formal_render_plan(b,d,m,g)
        restored=formal_render_plan_from_dict(asdict(plan))
        self.assertEqual(restored.color_boundaries,plan.color_boundaries)
        locked=lock_formal_render_plan(restored,{'subassembly-scope-0006':'作为已完成整体安装'})
        self.assertEqual(locked.color_boundaries,plan.color_boundaries)
        with self.assertRaisesRegex(ValueError,'颜色边界证据'):
            compile_locked_render_jobs(replace(locked,color_boundaries={}))
        jobs=compile_locked_render_jobs(locked)
        self.assertTrue(all(t.payload['bom_color_boundaries'] for t in jobs.tasks))
        # A stale plan reopening an internal row must fail before native work.
        contract={**locked.color_boundaries,'rows':{k:dict(v) for k,v in locked.color_boundaries['rows'].items()}}
        r=locked.steps[0].source_bom_rows[0];contract['rows'][str(r)].update(role='internal',owner_row=999)
        with self.assertRaisesRegex(ValueError,'internal to issued unit 999'):
            compile_locked_render_jobs(replace(locked,color_boundaries=contract))
