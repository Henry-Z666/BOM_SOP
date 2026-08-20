from __future__ import annotations

from pathlib import Path
import tomllib
import unittest


class PortableDependencyTests(unittest.TestCase):
    def test_formal_python_runtime_has_no_openai_codex_or_node_dependency(self) -> None:
        pyproject = Path("pyproject.toml").read_text(encoding="utf-8").lower()
        source = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in Path("src").rglob("*.py")
        ).lower()

        self.assertNotRegex(pyproject, r"\bopenai\b|@oai/artifact-tool|codex_node")
        self.assertNotRegex(source, r"\bimport openai\b|\bfrom openai\b|codex_node")

    def test_runtime_contains_no_model_provider_dependency(self) -> None:
        config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        dependencies = config["project"]["dependencies"]
        allowed = {"numpy", "openpyxl", "pillow", "pyside6", "pywin32"}
        names = {
            value.split(">", 1)[0].split("=", 1)[0].split(";", 1)[0].strip().lower()
            for value in dependencies
        }

        self.assertEqual(names, allowed)


if __name__ == "__main__":
    unittest.main()
