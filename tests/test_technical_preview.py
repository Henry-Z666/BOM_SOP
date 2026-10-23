from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch
from sop_pipeline.agent.technical_preview import compile_technical_previews

class TechnicalPreviewTests(unittest.TestCase):
    def test_changed_cad_cannot_generate_preview(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'product.json').write_text(json.dumps({'source_root':'.','cad_directory':'models','root_assembly':'root.asm.1'}))
            (root/'process-package.json').write_text(json.dumps({'product_config':str(root/'product.json')}))
            (root/'cad-graph.json').write_text(json.dumps({'source_tree_fingerprint':'old'}))
            with patch('sop_pipeline.agent.creo_discovery._validate_graph'),patch('sop_pipeline.agent.creo_discovery._tree_hashes',return_value={}):
                with self.assertRaisesRegex(ValueError,'current CAD sources'):
                    compile_technical_previews(root,('step',),root/'jobs.json')
            self.assertFalse((root/'jobs.json').exists())
