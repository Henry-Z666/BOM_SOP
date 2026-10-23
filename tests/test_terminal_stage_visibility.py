import unittest
from sop_pipeline.agent.terminal_stage_visibility import exclude_terminal_context


class TerminalStageVisibilityTests(unittest.TestCase):
    def test_context_bounds_follow_remaining_occurrences(self):
        p={'moving_occurrences':['part'],'receiver_occurrences':['frame'],'visible_occurrences':['frame','pack/child','part','other'],'stage_geometry_root':{'context_bounds':[{'id':'frame'},{'id':'pack'},{'id':'other'}]}}
        q=exclude_terminal_context(p,['pack'])
        self.assertEqual(q['visible_occurrences'],['frame','part','other'])
        self.assertEqual(q['stage_geometry_root']['context_bounds'],[{'id':'frame'},{'id':'other'}])
        self.assertEqual(len(p['visible_occurrences']),4)

    def test_receiver_cannot_be_removed(self):
        with self.assertRaises(ValueError):
            exclude_terminal_context({'moving_occurrences':['part'],'receiver_occurrences':['pack/child']},['pack'])
