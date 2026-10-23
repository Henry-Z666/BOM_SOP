import hashlib,json,tempfile,unittest
from pathlib import Path
from PIL import Image
from sop_pipeline.agent.render_validation import validate_receiver_ghosting

class ReceiverGhostingTests(unittest.TestCase):
    def test_disposable_assembly_state_requires_source_hash_and_zero_remaining_models(self):
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'native.jpg';image.write_bytes(b'native image');source=Path(folder)/'root.asm.3';source.write_bytes(b'immutable assembly')
            digest=hashlib.sha256(source.read_bytes()).hexdigest()
            p={'moving_occurrences':['bolt'],'receiver_occurrences':['column'],'authoritative_assembly':{'sha256':'sha256:'+digest},'receiver_ghosting':{'schema_version':'native-receiver-ghost/v1','style':'native_transparent','occurrence_ids':['column']}}
            proof={'schema_version':'native-receiver-ghost/v1','occurrence_ids':['column'],'command':'ProCmdViewTranspShaded','reset_strategy':'discarded_assembly_display_models/v1','display_state_discarded':True,'poses_restored':True,'assembly_count_after':0,'discarded_models':['root.asm'],'source_assembly_file':str(source),'source_assembly_sha256_before':digest,'source_assembly_sha256_after':digest,'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest()}
            path=Path(str(image)+'.ghost.json');path.write_text(json.dumps(proof),encoding='utf8');validate_receiver_ghosting(image,p)
            proof['assembly_count_after']=1;path.write_text(json.dumps(proof),encoding='utf8')
            with self.assertRaises(ValueError):validate_receiver_ghosting(image,p)
            proof['assembly_count_after']=0;path.write_text(json.dumps(proof),encoding='utf8');source.write_bytes(b'changed')
            with self.assertRaises(ValueError):validate_receiver_ghosting(image,p)
    def test_requires_native_restoration_and_immutable_rasters(self):
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'native.jpg';image.write_bytes(b'validated native image')
            p={'moving_occurrences':['bolt'],'receiver_occurrences':['column'],'receiver_ghosting':{'schema_version':'native-receiver-ghost/v1','style':'native_transparent','occurrence_ids':['column']}}
            before=Path(str(image)+'.appearance-before.bmp');after=Path(str(image)+'.appearance-restored.bmp')
            Image.new('RGB',(3,3),'teal').save(before);after.write_bytes(before.read_bytes())
            proof={'schema_version':'native-receiver-ghost/v1','occurrence_ids':['column'],'command':'ProCmdViewTranspShaded','restore_command':'ProCmdViewShadedEdges','appearance_restored':True,'restored_pixels_identical':True,'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'before_sha256':hashlib.sha256(before.read_bytes()).hexdigest(),'restored_sha256':hashlib.sha256(after.read_bytes()).hexdigest()}
            path=Path(str(image)+'.ghost.json');path.write_text(json.dumps(proof),encoding='utf8');validate_receiver_ghosting(image,p)
            proof['appearance_restored']=False;path.write_text(json.dumps(proof),encoding='utf8')
            with self.assertRaises(ValueError):validate_receiver_ghosting(image,p)
            proof['appearance_restored']=True;Image.new('RGB',(3,3),'white').save(after);proof['restored_sha256']=hashlib.sha256(after.read_bytes()).hexdigest();path.write_text(json.dumps(proof),encoding='utf8')
            with self.assertRaises(ValueError):validate_receiver_ghosting(image,p)
