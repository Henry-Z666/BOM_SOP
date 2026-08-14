from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .bom_normalizer import NormalizedBom, NormalizedBomRow


MODEL_PATTERN = re.compile(r"^(?P<base>.+)\.(?P<kind>asm|prt)(?:\.(?P<version>\d+))?$", re.IGNORECASE)


def normalize_identifier(value: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", value.upper())


@dataclass(frozen=True)
class ModelFile:
    relative_path: str
    base_name: str
    kind: str
    version: int


@dataclass(frozen=True)
class ModelInventory:
    schema_version: str
    files: tuple[ModelFile, ...]
    final_assembly: str
    assembly_candidates: tuple[str, ...]
    missing_bom_rows: tuple[int, ...]
    ambiguous_bom_rows: tuple[int, ...]
    non_modeled_bom_rows: tuple[int, ...]


def _level_depth(level: str) -> int:
    return level.count(".") if level else 999


def _root_row(bom: NormalizedBom) -> NormalizedBomRow:
    return min(bom.rows, key=lambda row: (_level_depth(row.level), row.row))


def _is_non_modeled_material(row: NormalizedBomRow) -> bool:
    unit = row.unit.strip().casefold()
    name = row.name.strip()
    measured_unit = unit in {"千克", "公斤", "kg", "米", "m"}
    material_name = any(token in name for token in ("板", "条", "软管", "胶", "线", "带"))
    return measured_unit and material_name and row.main_process_number is None


def inventory_models(cad_directory: Path, bom: NormalizedBom) -> ModelInventory:
    cad_directory = Path(cad_directory)
    parsed: list[ModelFile] = []
    for path in sorted(item for item in cad_directory.rglob("*") if item.is_file()):
        match = MODEL_PATTERN.match(path.name)
        if match is None:
            continue
        parsed.append(
            ModelFile(
                relative_path=path.relative_to(cad_directory).as_posix(),
                base_name=match.group("base"),
                kind=match.group("kind").lower(),
                version=int(match.group("version") or 0),
            )
        )
    if not parsed:
        raise ValueError("CAD 文件夹中没有找到 Creo ASM/PRT 文件")

    grouped: dict[tuple[str, str], list[ModelFile]] = {}
    for model in parsed:
        key = (normalize_identifier(model.base_name), model.kind)
        grouped.setdefault(key, []).append(model)
    latest: list[ModelFile] = []
    for versions in grouped.values():
        highest = max(model.version for model in versions)
        latest.extend(model for model in versions if model.version == highest)
    files = tuple(sorted(latest, key=lambda item: (item.kind, item.relative_path.casefold())))
    assemblies = [model for model in files if model.kind == "asm"]
    if not assemblies:
        raise ValueError("CAD 文件夹中没有找到最终总装候选 ASM")

    root = _root_row(bom)
    root_ids = {
        normalize_identifier(value)
        for value in (root.drawing_no, root.model, root.material_code)
        if value.strip()
    }

    def assembly_score(model: ModelFile) -> tuple[int, int, str]:
        base = normalize_identifier(model.base_name)
        exact = 1 if base in root_ids else 0
        return exact, model.version, model.relative_path.casefold()

    ranked = sorted(assemblies, key=assembly_score, reverse=True)
    best_score = assembly_score(ranked[0])[:2]
    assembly_candidates = tuple(
        sorted(model.relative_path for model in ranked if assembly_score(model)[:2] == best_score)
    )
    final_assembly = assembly_candidates[0]

    by_base: dict[str, list[ModelFile]] = {}
    for model in files:
        by_base.setdefault(normalize_identifier(model.base_name), []).append(model)
    missing: list[int] = []
    ambiguous: list[int] = []
    non_modeled: list[int] = []
    for row in bom.rows:
        identifiers = {
            normalize_identifier(value)
            for value in (row.drawing_no, row.model, row.material_code)
            if value.strip()
        }
        matches = {model.relative_path for identifier in identifiers for model in by_base.get(identifier, [])}
        if not matches and _is_non_modeled_material(row):
            non_modeled.append(row.row)
        elif not matches and not row.process_only:
            missing.append(row.row)
        elif len(matches) > 1:
            ambiguous.append(row.row)
    return ModelInventory(
        schema_version="model-inventory/v1",
        files=files,
        final_assembly=final_assembly,
        assembly_candidates=assembly_candidates,
        missing_bom_rows=tuple(missing),
        ambiguous_bom_rows=tuple(ambiguous),
        non_modeled_bom_rows=tuple(non_modeled),
    )
