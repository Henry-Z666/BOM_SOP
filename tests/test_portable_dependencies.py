from __future__ import annotations

from pathlib import Path
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

    def test_qwen_agent_metadata_uses_dashscope_sdk(self) -> None:
        metadata = Path(
            "skills/generate-creo-assembly-sop/agents/qwen.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn('api_style: "dashscope-python-sdk"', metadata)
        self.assertNotIn("openai-compatible", metadata.lower())


if __name__ == "__main__":
    unittest.main()
