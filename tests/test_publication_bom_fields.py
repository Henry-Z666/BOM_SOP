from types import SimpleNamespace
import unittest
from sop_pipeline.agent.skill_handlers import _publication_process_name, _publication_quantity


class PublicationBomFieldsTests(unittest.TestCase):
    def test_assembly_name_not_first_loose_part(self):
        part = SimpleNamespace(name="固定件")
        self.assertEqual(_publication_process_name("process-asm-0003", {3: SimpleNamespace(name="框架部件")}, part), "框架部件")

    def test_non_assembly_process_preserves_source_fallback(self):
        self.assertEqual(_publication_process_name("process-001", {}, SimpleNamespace(name="工序一")), "工序一")

    def test_split_actions_do_not_repeat_whole_bom_quantity(self):
        row = SimpleNamespace(quantity=6)
        self.assertEqual(_publication_quantity(SimpleNamespace(moving_occurrences=(1, 2)), row, 1), 2)
        self.assertEqual(_publication_quantity(SimpleNamespace(moving_occurrences=(3, 4, 5, 6)), row, 1), 4)

    def test_fractional_bom_and_multi_material_actions_preserve_quantities(self):
        self.assertEqual(_publication_quantity(SimpleNamespace(moving_occurrences=()), SimpleNamespace(quantity=1.25), 1), 1.25)
        self.assertEqual(_publication_quantity(SimpleNamespace(moving_occurrences=(1, 2)), SimpleNamespace(quantity=6), 2), 6)
