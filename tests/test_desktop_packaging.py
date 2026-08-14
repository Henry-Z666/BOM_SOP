from __future__ import annotations

from pathlib import Path
import unittest


class DesktopPackagingTests(unittest.TestCase):
    def test_desktop_entrypoint_and_pyinstaller_spec_exist(self) -> None:
        pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
        spec = Path("packaging/QwenCreoSopAgent.spec").read_text(encoding="utf-8")
        app = Path("src/sop_pipeline/desktop/app.py").read_text(encoding="utf-8")

        self.assertIn("qwen-creo-sop-agent", pyproject)
        self.assertIn("PySide6", pyproject)
        self.assertIn('excludes=["openai"]', spec)
        self.assertNotIn("creo_worker", app)
        self.assertNotIn("qwen_adapter", app)
        self.assertIn("--agent-worker", app)


if __name__ == "__main__":
    unittest.main()
