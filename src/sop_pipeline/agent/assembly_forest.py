from __future__ import annotations

from dataclasses import dataclass

from .bom_normalizer import NormalizedBom, NormalizedBomRow
from .model_inventory import ModelInventory, matching_model_files_for_row


@dataclass(frozen=True)
class ProductTitleClassification:
    bom_row: int
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class AssemblyScope:
    root_bom_row: int
    level: str
    assembly_file: str
    source_bom_rows: tuple[int, ...]
    nested_asm_rows: tuple[int, ...]


@dataclass(frozen=True)
class AssemblyForest:
    schema_version: str
    product_titles: tuple[ProductTitleClassification, ...]
    roots: tuple[AssemblyScope, ...]
    unscoped_bom_rows: tuple[int, ...]

    @property
    def product_title_rows(self) -> tuple[int, ...]:
        return tuple(item.bom_row for item in self.product_titles)


def build_assembly_forest(
    bom: NormalizedBom,
    inventory: ModelInventory,
) -> AssemblyForest:
    """Classify one BOM into independent authoritative ASM roots.

    An empty product/header level is not automatically ignored. It is a title
    only when it has no exact ASM, owns no hierarchy descendants, and carries
    no process instruction. Every other row must belong to an ASM root or stay
    visible in the unscoped exception set.
    """

    rows = tuple(bom.rows)
    assembly_files: dict[int, str] = {}
    for row in rows:
        matches = tuple(
            model
            for model in matching_model_files_for_row(row, inventory.files)
            if model.kind == "asm"
        )
        if matches:
            assembly_files[row.row] = min(
                matches,
                key=lambda model: model.relative_path.casefold(),
            ).relative_path

    titles: list[ProductTitleClassification] = []
    for row in rows:
        evidence: list[str] = []
        if not _level_parts(row.level):
            evidence.append("位于产品/总装标题层级")
        if row.row not in assembly_files:
            evidence.append("图号、型号和物料编码均未找到ASM")
        if not any(_is_descendant(candidate, row) for candidate in rows):
            evidence.append("没有属于该行的后续层级部件")
        if not row.assembly_text.strip() and row.main_process_number is None:
            evidence.append("没有装配步骤或主工序文字")
        if len(evidence) == 4:
            titles.append(ProductTitleClassification(row.row, tuple(evidence)))

    title_rows = {item.bom_row for item in titles}
    assembly_rows = tuple(row for row in rows if row.row in assembly_files)
    roots = tuple(
        row
        for row in assembly_rows
        if not any(
            candidate.row != row.row and _is_descendant(row, candidate)
            for candidate in assembly_rows
        )
    )
    scopes: list[AssemblyScope] = []
    claimed: set[int] = set()
    for root in sorted(roots, key=lambda row: row.row):
        source = tuple(
            row.row
            for row in rows
            if row.row == root.row or _is_descendant(row, root)
        )
        nested = tuple(
            row.row
            for row in assembly_rows
            if row.row != root.row and _is_descendant(row, root)
        )
        claimed.update(source)
        scopes.append(
            AssemblyScope(
                root_bom_row=root.row,
                level=root.level,
                assembly_file=assembly_files[root.row],
                source_bom_rows=source,
                nested_asm_rows=nested,
            )
        )

    unscoped = tuple(
        row.row
        for row in rows
        if row.row not in title_rows and row.row not in claimed
    )
    return AssemblyForest(
        schema_version="assembly-forest/v1",
        product_titles=tuple(titles),
        roots=tuple(scopes),
        unscoped_bom_rows=unscoped,
    )


def scoped_bom(bom: NormalizedBom, scope: AssemblyScope) -> NormalizedBom:
    selected = set(scope.source_bom_rows)
    return NormalizedBom(
        schema_version=bom.schema_version,
        sheet_name=bom.sheet_name,
        header_row=bom.header_row,
        columns=dict(bom.columns),
        rows=tuple(row for row in bom.rows if row.row in selected),
        sheet_candidates=bom.sheet_candidates,
    )


def _level_parts(level: str) -> tuple[str, ...]:
    return tuple(part for part in str(level).split(".") if part)


def _is_descendant(row: NormalizedBomRow, parent: NormalizedBomRow) -> bool:
    child = _level_parts(row.level)
    ancestor = _level_parts(parent.level)
    return bool(ancestor) and len(child) > len(ancestor) and child[: len(ancestor)] == ancestor
