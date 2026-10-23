from pathlib import Path
import unittest,json
from unittest.mock import patch
from tempfile import TemporaryDirectory
from sop_pipeline.agent.process_cli import rebuild

class RebuildTests(unittest.TestCase):
    def test_fresh_stage_isolates_stale_plan_and_locks_artifacts(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary); (root/'geometry-plan.json').write_text('stale')
            def replan(product, graph, stage):
                stage.mkdir(parents=True)
                (stage/'process-package.json').write_text(json.dumps({'source_package_fingerprint':'sha256:new'}))
                (stage/'planning-blockers.json').write_text('{}')
                self.assertFalse((stage/'geometry-plan.json').exists())
            def report(stage, output):
                target=output/'阶段评审.html';target.write_text('review');return target
            with patch('sop_pipeline.agent.process_cli.replan',side_effect=replan),patch('sop_pipeline.agent.phase_report.write_phase_report',side_effect=report):
                rebuild(root/'product.json',root,root/'graph.json')
            manifest=json.loads((root/'rebuild-manifest.json').read_text(encoding='utf-8'))
            self.assertFalse(manifest['production_eligible'])
            self.assertTrue(manifest['native_snapshot_reused'])
            self.assertEqual(len(manifest['artifacts']),2)
            self.assertEqual(manifest['source_package_fingerprint'],'sha256:new')

    def test_scan_failure_does_not_emit_success_manifest(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            with patch('sop_pipeline.agent.process_cli.analyze',side_effect=ValueError('CAD source changed')):
                with self.assertRaisesRegex(ValueError,'CAD source changed'):
                    rebuild(root/'product.json',root)
            self.assertFalse((root/'rebuild-manifest.json').exists())
