from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from sop_pipeline.agent.creo_discovery import (
    PowerShellCreoDiscovery,
    SubprocessDiscoveryRunner,
    resolve_runtime_config,
)


def valid_graph() -> dict:
    return {
        "schema_version": "creo-cad-graph/v3",
        "assembly_file": "final.asm.2",
        "assembly_name": "final.asm",
        "assembly_version": 2,
        "root_coordinate_system": "root_asm",
        "root_occurrence": "ROOT",
        "default_view_matrix": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "occurrences": [
            {
                "id": "10",
                "occurrence_id": "10",
                "component_path": [10],
                "parent_occurrence": "ROOT",
                "part_no": "part.prt",
                "transform": {
                    "x_axis": [1.0, 0.0, 0.0],
                    "y_axis": [0.0, 1.0, 0.0],
                    "z_axis": [0.0, 0.0, 1.0],
                    "origin": [0.0, 0.0, 0.0],
                },
            }
        ],
        "constraints": [
            {
                "id": "10_K_1",
                "occurrences": ["10", "ROOT"],
                "type_code": 0,
                "type": "MATE",
                "assembly_reference": {
                    "occurrence_id": "ROOT",
                    "geometry": {
                        "status": "available",
                        "source": "surface",
                        "point_root": [0.0, 0.0, 0.0],
                        "direction_root": [0.0, 0.0, 1.0],
                    },
                },
                "component_reference": {
                    "occurrence_id": "10",
                    "geometry": {"status": "unavailable"},
                },
            }
        ],
    }


class FakeDiscoveryRunner:
    def __init__(self, payload: dict, *, mutate_source: bool = False) -> None:
        self.payload = payload
        self.mutate_source = mutate_source
        self.commands: list[list[str]] = []

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        run_root = Path(command[command.index("-RunWorkspace") + 1])
        run_root.mkdir(parents=True)
        (run_root / "cad-discovery.json").write_text(
            json.dumps(self.payload), encoding="utf-8"
        )
        (run_root / "discovery.complete").write_text("complete\n", encoding="utf-8")
        if self.mutate_source:
            source = Path(command[command.index("-ModelsDirectory") + 1])
            (source / "part.prt.1").write_bytes(b"changed")
        return subprocess.CompletedProcess(command, 0, "", "")


class CreoDiscoveryTests(unittest.TestCase):
    def test_runtime_config_reuses_persisted_run_config_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            workspace = Path(folder)
            persisted = workspace / "internal" / "creo-runtime.json"
            persisted.parent.mkdir(parents=True)
            persisted.write_text(
                json.dumps(
                    {
                        "schema_version": "creo-runtime/v1",
                        "creo_loadpoint": r"C:\Program Files\PTC\Creo",
                        "license_file": r"C:\ProgramData\PTC\license.dat",
                        "python_command": "QwenCreoSopAgent.exe",
                    }
                ),
                encoding="utf-8",
            )

            path = resolve_runtime_config(workspace)

        self.assertEqual(path, persisted)

    def test_runtime_config_records_the_active_python_command(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ,
            {
                "QWEN_CREO_LOADPOINT": r"C:\Creo",
                "QWEN_CREO_LICENSE_FILE": r"C:\Creo\license.dat",
            },
            clear=True,
        ):
            path = resolve_runtime_config(Path(folder))
            assert path is not None
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["python_command"], sys.executable)

    def test_completion_marker_bounds_a_hung_outer_process(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            code = (
                "from pathlib import Path; import sys,time; "
                "root=Path(sys.argv[sys.argv.index('-RunWorkspace')+1]); "
                "root.mkdir(parents=True,exist_ok=True); "
                "(root/'discovery.complete').write_text('complete'); time.sleep(60)"
            )
            runner = SubprocessDiscoveryRunner(
                timeout_seconds=5, completion_grace_seconds=0.1
            )
            started = time.perf_counter()
            result = runner.run(
                [sys.executable, "-c", code, "-RunWorkspace", str(root)]
            )

        self.assertEqual(result.returncode, 0)
        self.assertLess(time.perf_counter() - started, 3.0)

    def _run(self, root: Path, runner: FakeDiscoveryRunner) -> dict:
        cad = root / "cad"
        cad.mkdir()
        (cad / "final.asm.2").write_bytes(b"authoritative")
        (cad / "part.prt.1").write_bytes(b"part")
        runtime = root / "runtime.json"
        runtime.write_text("{}", encoding="utf-8")
        script = root / "discovery.ps1"
        script.write_text("", encoding="utf-8")
        adapter = PowerShellCreoDiscovery(
            powershell="pwsh",
            script=script,
            runtime_config=runtime,
            runner=runner,
        )
        return adapter.discover(cad, "final.asm.2", root / "run")

    def test_discovery_locks_hash_and_root_occurrence_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runner = FakeDiscoveryRunner(valid_graph())
            result = self._run(root, runner)

        self.assertEqual(result["schema_version"], "creo-cad-graph/v3")
        self.assertEqual(
            result["authoritative_assembly"]["relative_path"], "final.asm.2"
        )
        self.assertTrue(result["authoritative_assembly"]["sha256"].startswith("sha256:"))
        self.assertTrue(result["source_tree_fingerprint"].startswith("sha256:"))

    def test_discovery_rejects_any_source_cad_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runner = FakeDiscoveryRunner(valid_graph(), mutate_source=True)
            with self.assertRaisesRegex(RuntimeError, "SOURCE_CAD_MUTATED"):
                self._run(root, runner)

    def test_discovery_requires_connection_end_completion_marker(self) -> None:
        class MissingMarkerRunner(FakeDiscoveryRunner):
            def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
                result = super().run(command)
                run_root = Path(command[command.index("-RunWorkspace") + 1])
                (run_root / "discovery.complete").unlink()
                return result

        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, "COMPLETION_MISSING"):
                self._run(Path(folder), MissingMarkerRunner(valid_graph()))

    def test_discovery_rejects_unknown_constraint_occurrence(self) -> None:
        graph = valid_graph()
        graph["constraints"][0]["occurrences"][1] = "999"
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "未知 occurrence"):
                self._run(Path(folder), FakeDiscoveryRunner(graph))

    def test_discovery_rejects_wrong_actual_assembly_version(self) -> None:
        graph = valid_graph()
        graph["assembly_version"] = 3
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "锁定文件不一致"):
                self._run(Path(folder), FakeDiscoveryRunner(graph))

    def test_discovery_rejects_non_rigid_occurrence_transform(self) -> None:
        graph = valid_graph()
        graph["occurrences"][0]["transform"]["x_axis"] = [2.0, 0.0, 0.0]
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "刚体变换"):
                self._run(Path(folder), FakeDiscoveryRunner(graph))

    def test_final_assembly_cannot_escape_cad_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cad = root / "cad"
            cad.mkdir()
            runtime = root / "runtime.json"
            runtime.write_text("{}", encoding="utf-8")
            script = root / "discovery.ps1"
            script.write_text("", encoding="utf-8")
            adapter = PowerShellCreoDiscovery(
                powershell="pwsh",
                script=script,
                runtime_config=runtime,
                runner=FakeDiscoveryRunner(valid_graph()),
            )
            with self.assertRaisesRegex(ValueError, "安全相对路径"):
                adapter.discover(cad, "../escape.asm.1", root / "run")


if __name__ == "__main__":
    unittest.main()
