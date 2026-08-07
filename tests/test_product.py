import json
from pathlib import Path
import tempfile
import unittest

from sop_pipeline.product import load_product


class ProductConfigTests(unittest.TestCase):
    def _write_product(self, root: Path, **overrides: object) -> Path:
        (root / "input" / "models").mkdir(parents=True)
        (root / "input" / "BOM.xlsx").write_bytes(b"bom")
        (root / "input" / "SOP-template.xlsx").write_bytes(b"template")
        (root / "input" / "models" / "final.asm.1").write_bytes(b"asm")
        data = {
            "schema_version": "assembly-sop-product/v1",
            "product_id": "demo",
            "bom_file": "input/BOM.xlsx",
            "models_dir": "input/models",
            "sop_template": "input/SOP-template.xlsx",
            "final_assembly": "final.asm.1",
            "bom_sheet": "BOM",
        }
        data.update(overrides)
        config = root / "products" / "demo" / "product.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps(data), encoding="utf-8")
        return config

    def test_resolves_product_paths_from_checkout_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            product = load_product(self._write_product(root))
        self.assertEqual(product.product_id, "demo")
        self.assertEqual(product.final_assembly_path.name, "final.asm.1")
        self.assertEqual(product.bom_sheet, "BOM")

    def test_rejects_missing_final_assembly(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            config = self._write_product(root, final_assembly="missing.asm.1")
            with self.assertRaises(FileNotFoundError):
                load_product(config)
