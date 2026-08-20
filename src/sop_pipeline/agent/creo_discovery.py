from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Protocol
from uuid import uuid4

from .bundle_paths import bundled_creo_script


SUPPORTED_SCHEMA = "creo-cad-graph/v3"


class DiscoveryRunner(Protocol):
    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]: ...


class SubprocessDiscoveryRunner:
    def __init__(
        self,
        timeout_seconds: int = 1800,
        completion_grace_seconds: float = 10.0,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.completion_grace_seconds = completion_grace_seconds

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        run_root = Path(command[command.index("-RunWorkspace") + 1])
        complete = run_root / "discovery.complete"
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            return_code = process.poll()
            if return_code is not None:
                return subprocess.CompletedProcess(command, return_code)
            if complete.is_file():
                try:
                    return_code = process.wait(timeout=self.completion_grace_seconds)
                    return subprocess.CompletedProcess(command, return_code)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                    return subprocess.CompletedProcess(command, 0)
            time.sleep(0.2)
        process.kill()
        process.wait(timeout=10)
        raise subprocess.TimeoutExpired(command, self.timeout_seconds)


class CreoDiscoveryPort(Protocol):
    def discover(
        self,
        cad_directory: Path,
        final_assembly: str,
        run_workspace: Path,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class StaticCreoDiscovery:
    """Test adapter for the same deep discovery interface used by Creo."""

    payload: dict[str, Any]

    def discover(
        self,
        cad_directory: Path,
        final_assembly: str,
        run_workspace: Path,
    ) -> dict[str, Any]:
        del cad_directory, final_assembly, run_workspace
        return json.loads(json.dumps(self.payload))


class PowerShellCreoDiscovery:
    """Run product-neutral J-Link discovery against a disposable CAD copy."""

    def __init__(
        self,
        *,
        powershell: str,
        script: Path,
        runtime_config: Path,
        runner: DiscoveryRunner | None = None,
    ) -> None:
        self.powershell = powershell
        self.script = Path(script).resolve()
        self.runtime_config = Path(runtime_config).resolve()
        self.runner = runner or SubprocessDiscoveryRunner()

    def discover(
        self,
        cad_directory: Path,
        final_assembly: str,
        run_workspace: Path,
    ) -> dict[str, Any]:
        cad_directory = Path(cad_directory).resolve()
        run_workspace = Path(run_workspace).resolve()
        assembly_path = _safe_source_path(cad_directory, final_assembly)
        before = _tree_hashes(cad_directory)
        attempt = run_workspace / "internal" / f"creo-discovery-{uuid4().hex}"
        command = [
            self.powershell,
            "-NoProfile",
            "-File",
            str(self.script),
            "-ModelsDirectory",
            str(cad_directory),
            "-AssemblyRelativePath",
            Path(final_assembly).as_posix(),
            "-RunWorkspace",
            str(attempt),
            "-RuntimeConfig",
            str(self.runtime_config),
        ]
        try:
            result = self.runner.run(command)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("CREO_DISCOVERY_TIMEOUT") from error
        except OSError as error:
            raise RuntimeError("CREO_DISCOVERY_PROCESS_ERROR") from error
        if result.returncode != 0:
            trace = attempt / "discovery.log"
            detail = ""
            if trace.is_file():
                detail = trace.read_text(
                    encoding="utf-8", errors="replace"
                ).strip()[-2000:]
            raise RuntimeError(f"CREO_DISCOVERY_FAILED: {detail}")
        complete = attempt / "discovery.complete"
        if not complete.is_file():
            raise RuntimeError("CREO_DISCOVERY_COMPLETION_MISSING")
        output = attempt / "cad-discovery.json"
        if not output.is_file():
            raise RuntimeError("CREO_DISCOVERY_OUTPUT_MISSING")
        try:
            payload = json.loads(output.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("CREO_DISCOVERY_OUTPUT_INVALID_JSON") from error
        after = _tree_hashes(cad_directory)
        if before != after:
            raise RuntimeError("SOURCE_CAD_MUTATED_DURING_DISCOVERY")
        _validate_graph(payload, Path(final_assembly).name)
        payload["authoritative_assembly"] = {
            "schema_version": "authoritative-assembly/v1",
            "relative_path": Path(final_assembly).as_posix(),
            "filename": assembly_path.name,
            "sha256": "sha256:" + _file_hash(assembly_path),
            "root_coordinate_system": payload["root_coordinate_system"],
            "actual_model_name": payload["assembly_name"],
            "actual_file_version": int(payload["assembly_version"]),
        }
        payload["source_tree_fingerprint"] = _hash_manifest(before)
        return payload


def bundled_discovery_script() -> Path:
    return bundled_creo_script("run_input_discovery.ps1")


def powershell_command() -> str:
    configured = os.environ.get("CREO_SOP_POWERSHELL_COMMAND", "").strip()
    if configured:
        return configured
    return shutil.which("pwsh.exe") or shutil.which("powershell.exe") or "powershell.exe"


def resolve_runtime_config(run_workspace: Path) -> Path | None:
    configured = os.environ.get("CREO_SOP_RUNTIME_CONFIG", "").strip()
    if configured:
        path = Path(configured).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Creo 运行配置不存在：{path}")
        return path
    loadpoint = os.environ.get("CREO_SOP_LOADPOINT", "").strip()
    license_file = os.environ.get("CREO_SOP_LICENSE_FILE", "").strip()
    if loadpoint or license_file:
        if not loadpoint or not license_file:
            raise ValueError("Creo 安装目录和许可证文件必须同时配置")
        path = Path(run_workspace) / "internal" / "creo-runtime.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "schema_version": "creo-runtime/v1",
                    "creo_loadpoint": loadpoint,
                    "license_file": license_file,
                    "creo_app": os.environ.get("CREO_SOP_APP", "PMA"),
                    "creo_feature_name": os.environ.get(
                        "CREO_SOP_FEATURE_NAME", ""
                    ),
                    "java_command": os.environ.get("CREO_SOP_JAVA_COMMAND", "java"),
                    "javac_command": os.environ.get("CREO_SOP_JAVAC_COMMAND", "javac"),
                    "python_command": sys.executable,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return path
    persisted = Path(run_workspace) / "internal" / "creo-runtime.json"
    if persisted.is_file():
        try:
            payload = json.loads(persisted.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as error:
            raise ValueError(f"当前运行批次的 Creo 运行配置已损坏：{persisted}") from error
        if (
            payload.get("schema_version") != "creo-runtime/v1"
            or not str(payload.get("creo_loadpoint", "")).strip()
            or not str(payload.get("license_file", "")).strip()
        ):
            raise ValueError(f"当前运行批次的 Creo 运行配置不完整：{persisted}")
        return persisted
    return None


def _safe_source_path(cad_directory: Path, relative: str) -> Path:
    requested = Path(relative)
    if requested.is_absolute() or ".." in requested.parts:
        raise ValueError("最终总装必须是 CAD 目录内的安全相对路径")
    result = (cad_directory / requested).resolve()
    if cad_directory not in result.parents or not result.is_file():
        raise FileNotFoundError(f"最终总装不存在：{relative}")
    return result


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _file_hash(path)
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def _hash_manifest(items: dict[str, str]) -> str:
    encoded = json.dumps(
        items, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def _validate_graph(payload: dict[str, Any], expected_filename: str) -> None:
    if payload.get("schema_version") != SUPPORTED_SCHEMA:
        raise ValueError(f"不支持的 Creo discovery 版本：{payload.get('schema_version')}")
    if str(payload.get("assembly_file", "")).casefold() != expected_filename.casefold():
        raise ValueError("Creo 实际打开的总装文件与锁定版本不一致")
    if payload.get("root_coordinate_system") != "root_asm":
        raise ValueError("Creo discovery 必须使用总装根坐标系")
    if not _finite_matrix(payload.get("default_view_matrix")):
        raise ValueError("Creo discovery 缺少默认视图矩阵")
    occurrences = payload.get("occurrences")
    constraints = payload.get("constraints")
    if not isinstance(occurrences, list) or not occurrences:
        raise ValueError("Creo discovery 没有 occurrence")
    if not isinstance(constraints, list):
        raise ValueError("Creo discovery 缺少 constraints")
    assembly_name = str(payload.get("assembly_name", "")).strip()
    assembly_version = payload.get("assembly_version")
    if not assembly_name or isinstance(assembly_version, bool) or not isinstance(
        assembly_version, int
    ):
        raise ValueError("Creo discovery 缺少实际打开的模型版本")
    expected_name, expected_version = _creo_name_and_version(expected_filename)
    if assembly_name.casefold() != expected_name.casefold() or (
        expected_version is not None and assembly_version != expected_version
    ):
        raise ValueError("Creo 实际打开的模型名称或版本与锁定文件不一致")
    known = {"ROOT"}
    paths: set[tuple[int, ...]] = set()
    for node in occurrences:
        occurrence_id = str(node.get("occurrence_id", ""))
        path = tuple(int(value) for value in node.get("component_path", []))
        if not occurrence_id or not path or occurrence_id in known or path in paths:
            raise ValueError("Creo discovery 包含重复或无效 occurrence")
        if occurrence_id != "/".join(str(value) for value in path):
            raise ValueError("occurrence_id 与 component_path 不一致")
        expected_parent = (
            "ROOT" if len(path) == 1 else "/".join(str(value) for value in path[:-1])
        )
        if str(node.get("parent_occurrence", "")) != expected_parent:
            raise ValueError("Creo occurrence 父路径与 component_path 不一致")
        if not _valid_pose(node.get("transform")):
            raise ValueError("Creo occurrence 缺少有效的根坐标刚体变换")
        bounds = node.get("bounds_root")
        if bounds is not None and not _valid_bounds(bounds):
            raise ValueError("Creo occurrence 包含无效的根坐标包围盒")
        known.add(occurrence_id)
        paths.add(path)
    for node in occurrences:
        if str(node.get("parent_occurrence", "")) not in known:
            raise ValueError("Creo occurrence 引用了未知父路径")
    for edge in constraints:
        ends = edge.get("occurrences")
        if not isinstance(ends, list) or len(ends) != 2:
            raise ValueError("Creo 约束必须连接两个 occurrence")
        if any(str(value) not in known for value in ends):
            raise ValueError("Creo 约束引用了未知 occurrence")
        for reference_name in ("assembly_reference", "component_reference"):
            reference = edge.get(reference_name)
            if reference is None or not isinstance(reference, dict):
                continue
            reference_occurrence = reference.get("occurrence_id")
            if reference_occurrence is not None and str(reference_occurrence) not in known:
                raise ValueError("Creo 约束引用几何属于未知 occurrence")
            geometry = reference.get("geometry")
            if not isinstance(geometry, dict) or geometry.get("status") != "available":
                continue
            direction = geometry.get("direction_root")
            point = geometry.get("point_root")
            if not _finite_vector(direction) or not _finite_vector(point):
                raise ValueError("Creo 约束几何包含无效根坐标")
            length = math.sqrt(sum(float(value) ** 2 for value in direction))
            if abs(length - 1.0) > 1.0e-6:
                raise ValueError("Creo 约束方向必须是根坐标单位向量")


def _finite_vector(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(isinstance(item, (int, float)) and math.isfinite(item) for item in value)
    )


def _finite_matrix(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(
            isinstance(row, list)
            and len(row) == 4
            and all(
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                and math.isfinite(item)
                for item in row
            )
            for row in value
        )
    )


def _valid_bounds(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("status") == "unavailable":
        return True
    if value.get("status") != "available":
        return False
    low = value.get("min")
    high = value.get("max")
    return (
        _finite_vector(low)
        and _finite_vector(high)
        and all(float(low[index]) <= float(high[index]) for index in range(3))
    )


def _valid_pose(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    axes = [value.get(name) for name in ("x_axis", "y_axis", "z_axis")]
    origin = value.get("origin")
    if not all(_finite_vector(axis) for axis in axes) or not _finite_vector(origin):
        return False
    vectors = [[float(item) for item in axis] for axis in axes]
    if any(abs(_dot(axis, axis) - 1.0) > 1.0e-6 for axis in vectors):
        return False
    if any(
        abs(_dot(vectors[left], vectors[right])) > 1.0e-6
        for left, right in ((0, 1), (0, 2), (1, 2))
    ):
        return False
    cross = [
        vectors[0][1] * vectors[1][2] - vectors[0][2] * vectors[1][1],
        vectors[0][2] * vectors[1][0] - vectors[0][0] * vectors[1][2],
        vectors[0][0] * vectors[1][1] - vectors[0][1] * vectors[1][0],
    ]
    return _dot(cross, vectors[2]) > 1.0 - 1.0e-6


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _creo_name_and_version(filename: str) -> tuple[str, int | None]:
    stem, separator, suffix = filename.rpartition(".")
    if separator and suffix.isdigit():
        return stem, int(suffix)
    return filename, None
