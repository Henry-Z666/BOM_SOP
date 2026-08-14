from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook
from PIL import Image

from sop_pipeline.agent.sop_publisher import SopImage, SopStep, SopPublisher


def _image(folder: Path, name: str) -> Path:
    path = folder / name
    Image.new("RGB", (320, 240), "white").save(path)
    return path


def _step(index: int, process: int, image: Path, **changes) -> SopStep:
    values = dict(
        step_id=f"step-{index}",
        main_process_id=str(process),
        main_process_name=f"主工序 {process}",
        title=f"安装步骤 {index}",
        image=SopImage(f"image-{index}", image),
        materials=((f"M-{index}", "零件", 1),),
        process_text="按图装配",
        control_points="确认安装到位",
        tools="常规工具",
    )
    values.update(changes)
    return SopStep(**values)


class SopPublisherTests(unittest.TestCase):
    def test_42_images_are_grouped_by_8_processes_with_continuation_pages(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            steps = []
            index = 1
            for process in range(1, 9):
                count = 6 if process <= 2 else 5
                for _ in range(count):
                    steps.append(_step(index, process, _image(root, f"{index}.jpg")))
                    index += 1
            workbook_path = SopPublisher(images_per_page=2).publish(
                tuple(steps), root / "交付结果"
            )
            workbook = load_workbook(workbook_path)

            self.assertEqual(sum(len(sheet._images) for sheet in workbook), 42)
            self.assertTrue(any("续页" in name for name in workbook.sheetnames))
            self.assertGreater(len(workbook.sheetnames), 8)
            self.assertEqual(
                {path.name for path in (root / "交付结果").iterdir()},
                {"SOP.xlsx", "步骤图片"},
            )

    def test_100_main_processes_do_not_assume_two_digit_sheet_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shared_image = _image(root, "shared.jpg")
            steps = tuple(
                _step(index, index, shared_image) for index in range(1, 101)
            )
            workbook_path = SopPublisher().publish(steps, root / "交付结果")
            workbook = load_workbook(workbook_path, read_only=False)

        self.assertEqual(len(workbook.sheetnames), 100)
        self.assertIn("主工序 100", workbook.sheetnames)

    def test_pending_delivery_keeps_candidates_then_final_publish_cleans_them(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            recommended = _image(root, "recommended.jpg")
            alternate = _image(root, "alternate.jpg")
            step = _step(
                1,
                1,
                recommended,
                questioned=True,
                candidates=(
                    SopImage("candidate-a", recommended, "a", True),
                    SopImage("candidate-b", alternate, "b", False),
                ),
            )
            publisher = SopPublisher()
            pending = publisher.publish((step,), root / "交付结果", pending=True)
            pending_book = load_workbook(pending)

            self.assertEqual(pending.name, "SOP_待确认.xlsx")
            self.assertIn("待确认", pending_book.active["A2"].value)
            self.assertEqual(len(list((root / "交付结果" / "步骤图片").iterdir())), 2)

            final_step = _step(1, 1, recommended)
            final = publisher.publish((final_step,), root / "交付结果", pending=False)

            self.assertEqual(final.name, "SOP.xlsx")
            self.assertFalse((root / "交付结果" / "SOP_待确认.xlsx").exists())
            self.assertEqual(len(list((root / "交付结果" / "步骤图片").iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
