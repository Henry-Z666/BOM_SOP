from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4


_RUNTIME_GLOBS = (
    ("creo_java", "*.ps1"),
    ("creo_java", "*.pro"),
    ("creo_java/src", "*.java"),
    ("creo_java/build", "*.class"),
    ("scripts", "fit_creo_image.ps1"),
)
_REQUIRED_RUNTIME_FILES = (
    "creo_java/run_input_discovery.ps1",
    "creo_java/run_agent_native_batch.ps1",
    "creo_java/stop_agent_native_worker.ps1",
    "creo_java/build/AutoCadDiscovery.class",
    "creo_java/build/NativeArrowWorker.class",
    "scripts/fit_creo_image.ps1",
)


def bundled_project_root() -> Path:
    """Return the source root or PyInstaller's unpacked resource root."""

    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parents[3]


def bundled_creo_script(filename: str) -> Path:
    return bundled_project_root() / "creo_java" / filename


def bundled_sop_template() -> Path:
    """Return the retained, product-neutral single-page SOP template."""

    template = bundled_project_root() / "assets" / "sop-template.xlsx"
    if not template.is_file():
        raise FileNotFoundError(f"Bundled SOP template is missing: {template}")
    return template


def materialized_creo_script(run_workspace: Path, filename: str) -> Path:
    """Return a durable copy of a bundled Creo script for one Agent run.

    A PyInstaller one-file executable unpacks data below a transient ``_MEI``
    directory.  Creo/J-Link workers outlive individual Agent commands and runs
    can be resumed after the GUI is restarted, so no persisted command may
    point at that transient directory.  The small, versioned runtime is copied
    once below the run's internal directory and is then safe for every retry.
    """

    root = materialize_creo_runtime(run_workspace)
    requested = Path(filename)
    if requested.is_absolute() or requested.name != filename:
        raise ValueError("Creo runtime filename must be a plain filename")
    script = root / "creo_java" / requested
    if not script.is_file():
        raise FileNotFoundError(f"Bundled Creo script is missing: {filename}")
    return script


def materialize_creo_runtime(run_workspace: Path) -> Path:
    source_root = bundled_project_root()
    files = _runtime_files(source_root)
    fingerprint = _runtime_fingerprint(source_root, files)
    runtime_parent = Path(run_workspace) / "internal" / "bundled-runtime"
    destination = runtime_parent / fingerprint[:16]
    marker = destination / ".runtime-complete.json"
    if _runtime_is_complete(destination, marker, fingerprint):
        return destination

    runtime_parent.mkdir(parents=True, exist_ok=True)
    temporary = runtime_parent / f".runtime-{uuid4().hex}"
    try:
        for relative in files:
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_root / relative, target)
        marker_payload = {
            "schema_version": "bundled-creo-runtime/v1",
            "fingerprint": fingerprint,
            "files": [relative.as_posix() for relative in files],
        }
        marker_target = temporary / marker.name
        marker_target.write_text(
            json.dumps(marker_payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        try:
            os.replace(temporary, destination)
        except FileExistsError:
            if not _runtime_is_complete(destination, marker, fingerprint):
                raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    if not _runtime_is_complete(destination, marker, fingerprint):
        raise RuntimeError("Durable Creo runtime materialization is incomplete")
    return destination


def _runtime_files(source_root: Path) -> tuple[Path, ...]:
    files: set[Path] = set()
    for relative_root, pattern in _RUNTIME_GLOBS:
        directory = source_root / relative_root
        if pattern == "fit_creo_image.ps1":
            candidates = (directory / pattern,)
        else:
            candidates = directory.glob(pattern)
        files.update(
            candidate.relative_to(source_root)
            for candidate in candidates
            if candidate.is_file()
        )
    missing = [
        relative
        for relative in _REQUIRED_RUNTIME_FILES
        if not (source_root / relative).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "Bundled Creo runtime is incomplete: " + ", ".join(missing)
        )
    return tuple(sorted(files, key=lambda value: value.as_posix()))


def _runtime_fingerprint(source_root: Path, files: tuple[Path, ...]) -> str:
    digest = sha256()
    for relative in files:
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update((source_root / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _runtime_is_complete(
    destination: Path, marker: Path, fingerprint: str
) -> bool:
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if payload.get("fingerprint") != fingerprint:
        return False
    listed = payload.get("files")
    return isinstance(listed, list) and all(
        (destination / str(relative)).is_file() for relative in listed
    )
