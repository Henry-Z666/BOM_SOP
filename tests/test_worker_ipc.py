from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from sop_pipeline.agent.worker_cli import main


class WorkerIpcTests(unittest.TestCase):
    def test_windowed_worker_writes_json_response_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            request = workspace / "ipc" / "request.json"
            response = workspace / "ipc" / "response.json"
            request.parent.mkdir()
            request.write_text("{}", encoding="utf-8")
            exit_code = main(
                [
                    "--workspace", str(workspace),
                    "--action", "invalid-action",
                    "--request-file", str(request),
                    "--response-file", str(response),
                ]
            )
            payload = json.loads(response.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertIn("unsupported worker action", payload["error"])

    def test_worker_rejects_response_path_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as other:
            workspace = Path(folder)
            request = workspace / "request.json"
            request.write_text("{}", encoding="utf-8")
            outside = Path(other) / "response.json"
            exit_code = main(
                [
                    "--workspace", str(workspace),
                    "--action", "invalid-action",
                    "--request-file", str(request),
                    "--response-file", str(outside),
                ]
            )

        self.assertEqual(exit_code, 1)
        self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
