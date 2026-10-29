import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

MODULE=Path(__file__).resolve().parents[1]/'scripts/cdu900/publication_inputs.py'
spec=importlib.util.spec_from_file_location('publication_inputs_for_test',MODULE)
inputs=importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)

class PublicationInputBundleTests(unittest.TestCase):
    def test_paths_follow_current_checkout_and_preserve_other_values(self):
        with TemporaryDirectory() as directory:
            bundle=Path(directory)
            source={'image_path':r'C:\old\project\a.jpg','nested':[{'hash':'sha256:original','path':'C:/old/project/a.jpg'}],'occurrence':'46/125'}
            actual=inputs.relocate(source,{'C:/old/project/a.jpg':'native-images/a.jpg'},bundle)
            expected=str((bundle/'native-images/a.jpg').resolve())
            self.assertEqual(actual['image_path'],expected)
            self.assertEqual(actual['nested'][0],{'hash':'sha256:original','path':expected})
            self.assertEqual(actual['occurrence'],'46/125')
            self.assertEqual(source['image_path'],r'C:\old\project\a.jpg')

    def test_bundle_path_cannot_escape_input_directory(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'escapes'):
                inputs.relocate('old',{'old':'../outside.jpg'},Path(directory))

    def test_unknown_evidence_is_not_silently_rewritten(self):
        source={'model':'ROOT.ASM','evidence':'unmapped/history.json'}
        self.assertEqual(inputs.relocate(source,{}),source)
