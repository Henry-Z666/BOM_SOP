import unittest
from sop_pipeline.agent.render_validation import validate_detail_roi_fraction

class DetailRoiFractionTests(unittest.TestCase):
    def payload(self, fraction=.9, role='detail'):
        return {'installation_view': {'roi_frame_fraction':fraction},'image_coverage':{'role':role}}
    def test_native_evidence_must_match(self):
        validate_detail_roi_fraction(self.payload(),{'frame_fraction':.9})
        with self.assertRaises(ValueError):validate_detail_roi_fraction(self.payload(),{'frame_fraction':.65})
        with self.assertRaises(KeyError):validate_detail_roi_fraction(self.payload(),{})
    def test_bounds_and_wrong_role_rejected(self):
        for value in [.49,.91,float('nan')]:
            with self.assertRaises(ValueError):validate_detail_roi_fraction(self.payload(value),{'frame_fraction':value})
        with self.assertRaises(ValueError):validate_detail_roi_fraction(self.payload(role='location'),{'frame_fraction':.9})
    def test_legacy_default_compatible(self):
        validate_detail_roi_fraction({'installation_view':{}},{})
    def test_reduced_frame_fraction_keeps_native_contract(self):
        validate_detail_roi_fraction(self.payload(.5),{'frame_fraction':.5})

if __name__=='__main__':unittest.main()
