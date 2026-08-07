"""Lock one Creo total-assembly version for a reproducible render batch."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


VERSIONED = re.compile(r"^(?P<stem>.+\.(?:asm|prt))\.(?P<version>\d+)$", re.IGNORECASE)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_latest(models_dir: Path, requested: str) -> Path:
    requested_path = Path(requested)
    stem = VERSIONED.match(requested_path.name)
    base = stem.group("stem") if stem else requested_path.name
    candidates: list[tuple[int, Path]] = []
    for candidate in models_dir.glob(base + ".*"):
        match = VERSIONED.match(candidate.name)
        if match and match.group("stem").lower() == base.lower():
            candidates.append((int(match.group("version")), candidate))
    if not candidates:
        raise FileNotFoundError(f"未找到总装版本：{models_dir / requested}")
    return max(candidates, key=lambda item: item[0])[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--assembly", required=True, help="总装文件名或不带版本的 basename")
    parser.add_argument("--camera-basis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    models_dir = args.models_dir.resolve()
    selected = resolve_latest(models_dir, args.assembly)
    basis = json.loads(args.camera_basis.read_text(encoding="utf-8"))
    selected_hash = sha256(selected)
    if Path(basis.get("assembly_file", "")).name.lower() != selected.name.lower():
        raise ValueError("相机基准不是该锁定总装生成的")
    if basis.get("assembly_sha256", "").lower() != selected_hash:
        raise ValueError("相机基准哈希与锁定总装不一致")
    match = VERSIONED.match(selected.name)
    payload = {
        "schema_version": "authoritative-assembly/v1",
        "assembly_file": selected.name,
        "assembly_version": int(match.group("version")) if match else None,
        "assembly_sha256": selected_hash,
        "coordinate_system": "root_asm",
        "camera_basis_file": str(args.camera_basis),
        "fixed_cameras": {
            "fixed_123": basis["fixed_123_view_matrix"],
            "fixed_456": basis["fixed_456_view_matrix"],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
