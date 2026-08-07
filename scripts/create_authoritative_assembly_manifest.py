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


def load_product_paths(config_path: Path) -> tuple[Path, str]:
    """Read the portable product package without importing project code."""
    config_path = config_path.resolve()
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "assembly-sop-product/v1":
        raise ValueError("不支持的产品配置版本")
    root = next((parent for parent in config_path.parents if (parent / "pyproject.toml").is_file()), config_path.parent)
    models_dir = Path(data["models_dir"])
    if not models_dir.is_absolute():
        models_dir = root / models_dir
    assembly = data.get("final_assembly")
    if not isinstance(assembly, str) or not assembly:
        raise ValueError("产品配置缺少 final_assembly")
    return models_dir, assembly


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-config", type=Path, help="products/<product>/product.json")
    parser.add_argument("--models-dir", type=Path, help="模型目录（未使用产品包时必填）")
    parser.add_argument("--assembly", help="总装文件名或不带版本的 basename（未使用产品包时必填）")
    parser.add_argument("--camera-basis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.product_config:
        if args.models_dir or args.assembly:
            parser.error("--product-config 不能与 --models-dir 或 --assembly 同时使用")
        models_dir, assembly = load_product_paths(args.product_config)
    elif args.models_dir and args.assembly:
        models_dir, assembly = args.models_dir, args.assembly
    else:
        parser.error("请提供 --product-config，或同时提供 --models-dir 与 --assembly")
    models_dir = models_dir.resolve()
    selected = resolve_latest(models_dir, assembly)
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
