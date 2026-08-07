from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw

from sop_pipeline.pixel_arrow import FINAL_SIZE, compose, green_components


def native_canvas() -> Image.Image:
    return Image.new("RGB", (1800, 2400), (245, 245, 245))


def native_arrow(image: Image.Image, start: tuple[int, int], end: tuple[int, int]) -> None:
    draw = ImageDraw.Draw(image)
    draw.line([start, end], fill=(0, 255, 0), width=2)
    # Deliberately asymmetric arrowhead at installation endpoint.
    draw.line([end, (end[0] - 10, end[1] - 5)], fill=(0, 255, 0), width=2)
    draw.line([end, (end[0] - 10, end[1] + 5)], fill=(0, 255, 0), width=2)


class PixelArrowV3Tests(unittest.TestCase):
    def test_composes_fixed_frame_and_expected_arrow_count(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); base, calibration, output = root / "base.jpg", root / "cal.jpg", root / "final.jpg"
            native_canvas().save(base)
            cal = native_canvas()
            native_arrow(cal, (600, 900), (600, 980))
            native_arrow(cal, (850, 900), (850, 980))
            cal.save(calibration)
            compose(base, calibration, output, 2)
            rendered = Image.open(output)
            self.assertEqual(rendered.size, FINAL_SIZE)
            self.assertEqual(len(green_components(rendered)), 2)

    def test_blocks_component_count_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); base, calibration = root / "base.jpg", root / "cal.jpg"
            native_canvas().save(base); native_canvas().save(calibration)
            with self.assertRaisesRegex(ValueError, "components=0 expected=1"):
                compose(base, calibration, root / "final.jpg", 1)

    def test_blocks_too_short_arrow(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); base, calibration = root / "base.jpg", root / "cal.jpg"
            native_canvas().save(base); cal = native_canvas(); native_arrow(cal, (600, 900), (600, 910)); cal.save(calibration)
            with self.assertRaisesRegex(ValueError, "unreadable"):
                compose(base, calibration, root / "final.jpg", 1)

    def test_v3_runner_has_no_legacy_runner_dependency(self):
        runner = (Path(__file__).parents[1] / "creo_java" / "run_pixel_arrow_trial_v3.ps1").read_text(encoding="utf-8")
        forbidden = ("run_stage_batch.ps1", "run_bounded_stage_batch.ps1", "run_render.ps1", "fit_creo_image.ps1")
        self.assertTrue(all(name not in runner for name in forbidden))


if __name__ == "__main__":
    unittest.main()
