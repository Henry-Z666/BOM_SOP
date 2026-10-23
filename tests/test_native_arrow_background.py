import unittest
import numpy as np
from sop_pipeline.agent.render_validation import _native_arrow_background_difference,_arrow_raster_metrics

class NativeArrowBackgroundTests(unittest.TestCase):
    def metrics(self,final,background):
        p=_native_arrow_background_difference(final,background,color='red')
        return _arrow_raster_metrics(p,green_delta=20,target_pixel=(100,100),min_component_pixels=16,color='red')

    def test_changed_red_body_outline_does_not_trigger_border_clip(self):
        b=np.full((200,200,3),255,dtype=np.uint8);b[:60,180:]=[255,0,0];p=b.copy();p[59,179:]=[235,5,5];p[98:103,50:150]=[255,0,0]
        m=self.metrics(p,b);self.assertEqual(m.significant_components,1);self.assertGreater(m.border_margin_pixels,40)

    def test_real_clipped_arrow_is_not_removed(self):
        b=np.full((200,200,3),255,dtype=np.uint8);p=b.copy();p[98:103,0:150]=[255,0,0]
        self.assertEqual(self.metrics(p,b).border_margin_pixels,0)

    def test_orange_body_cannot_cover_missing_arrow(self):
        b=np.full((200,200,3),255,dtype=np.uint8);p=b.copy();p[30:80,30:80]=[175,85,20]
        self.assertIsNone(self.metrics(p,b).bbox)
