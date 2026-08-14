from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import ArtifactRef
from .store import RunStore


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class ArtifactStore:
    """Write immutable run artifacts atomically and register their hashes."""

    def __init__(self, store: RunStore) -> None:
        self._store = store

    @staticmethod
    def _target(run_workspace: Path, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("产物路径必须是批次内的安全相对路径")
        target = (run_workspace / candidate).resolve()
        root = run_workspace.resolve()
        if target != root and root not in target.parents:
            raise ValueError("产物路径逃逸运行批次目录")
        return target

    def write_json(
        self,
        *,
        run_id: str,
        run_workspace: Path,
        kind: str,
        relative_path: str,
        value: Any,
    ) -> ArtifactRef:
        target = self._target(run_workspace, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        temporary = target.with_name(f".{target.name}.tmp-{uuid4().hex}")
        try:
            with temporary.open("xb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        created_at = datetime.now(timezone.utc).isoformat()
        reference = ArtifactRef(
            artifact_id=uuid4().hex,
            run_id=run_id,
            kind=kind,
            relative_path=relative_path,
            sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
            created_at=created_at,
        )
        self._store.add_artifact(reference)
        return reference

    def read_json(self, run_workspace: Path, relative_path: str) -> dict[str, Any]:
        target = self._target(run_workspace, relative_path)
        return json.loads(target.read_text(encoding="utf-8"))

