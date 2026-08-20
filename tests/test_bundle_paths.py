from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from sop_pipeline.agent.bundle_paths import _replace_runtime_directory


class BundlePathsTests(unittest.TestCase):
    def test_runtime_replace_retries_a_brief_windows_permission_lock(self) -> None:
        with patch(
            "sop_pipeline.agent.bundle_paths.os.replace",
            side_effect=(PermissionError("scanner lock"), None),
        ) as replace, patch("sop_pipeline.agent.bundle_paths.time.sleep") as sleep:
            _replace_runtime_directory(
                Path("temporary"),
                Path("destination"),
                marker=Path("missing-marker"),
                fingerprint="sha256:test",
            )

        self.assertEqual(replace.call_count, 2)
        sleep.assert_called_once_with(0.1)


if __name__ == "__main__":
    unittest.main()
