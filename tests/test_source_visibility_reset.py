import hashlib,json,tempfile,unittest
from pathlib import Path
from sop_pipeline.agent.render_validation import validate_source_visibility_reset


class SourceVisibilityResetTests(unittest.TestCase):
    def test_native_proof_is_bound_to_image_and_preserves_auxiliary_layer(self):
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'native.jpg';image.write_bytes(b'native raster bytes')
            payload={'source_visibility_normalization':'native-source-visibility-reset/v1'}
            proof={'schema_version':'native-source-visibility-reset/v1','command':'ProCmdViewShowAll','stage_exclusion_count':4,'auxiliary_layer_retained':True,'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest()}
            path=Path(str(image)+'.visibility-reset.json')
            path.write_text(json.dumps(proof),encoding='utf8');validate_source_visibility_reset(image,payload)
            for key,value in [('command','ProCmdViewShaded'),('auxiliary_layer_retained',False),('image_sha256','changed')]:
                changed={**proof,key:value};path.write_text(json.dumps(changed),encoding='utf8')
                with self.assertRaises(ValueError):validate_source_visibility_reset(image,payload)
