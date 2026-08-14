from __future__ import annotations

from pathlib import Path
import unittest


class DesktopPackagingTests(unittest.TestCase):
    def test_desktop_entrypoint_and_pyinstaller_spec_exist(self) -> None:
        pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
        spec = Path("packaging/QwenCreoSopAgent.spec").read_text(encoding="utf-8")
        build_script = Path("packaging/build.ps1").read_text(encoding="utf-8")
        entrypoint = Path("packaging/entrypoint.py").read_text(encoding="utf-8")
        app = Path("src/sop_pipeline/desktop/app.py").read_text(encoding="utf-8")

        self.assertIn("qwen-creo-sop-agent", pyproject)
        self.assertIn("PySide6", pyproject)
        self.assertIn('excludes=["openai"]', spec)
        self.assertIn('"run_input_discovery.ps1"', spec)
        self.assertIn('"AutoCadDiscovery.java"', spec)
        self.assertIn('"AutoCadDiscovery.class"', spec)
        self.assertNotIn('(str(root / "creo_java"), "creo_java")', spec)
        self.assertIn("creo_java\\build.ps1", build_script)
        self.assertIn("AutoCadDiscovery.class", build_script)
        self.assertIn("sop_pipeline.desktop.app", entrypoint)
        self.assertNotIn("creo_worker", app)
        self.assertNotIn("qwen_adapter", app)
        self.assertIn("--agent-worker", app)


if __name__ == "__main__":
    unittest.main()
