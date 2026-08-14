from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
from xml.etree import ElementTree as ET
import zipfile


MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
OFFICE_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

HEADER_ALIASES = {
    "level": {"层级", "层次", "级次", "装配层级", "bom层级"},
    "material_code": {"物料编码", "物料号", "物料代码", "物料编号"},
    "drawing_no": {"图号", "零件图号", "图纸编号", "图纸号", "代号"},
    "name": {"名称", "物料名称", "零件名称", "品名"},
    "model": {"型号", "模型", "规格型号", "规格"},
    "quantity": {"数量", "用量", "单机用量", "装配数量"},
    "unit": {"单位", "计量单位"},
    "assembly_text": {"装配步骤", "组装步骤", "工艺内容", "装配内容", "作业内容"},
    "control_points": {"关键控制点", "关键控制要点", "控制要点", "质量要求"},
    "tools": {"工具", "工装", "工装工具", "使用工具"},
}


def _normal_text(value: str) -> str:
    return re.sub(r"[\s\-_/（）()：:、，,。.]", "", value).casefold()


NORMALIZED_ALIASES = {
    canonical: {_normal_text(alias) for alias in aliases}
    for canonical, aliases in HEADER_ALIASES.items()
}


@dataclass(frozen=True)
class NormalizedBomRow:
    row: int
    level: str
    material_code: str
    drawing_no: str
    name: str
    model: str
    quantity: float | int | str
    unit: str
    assembly_text: str
    control_points: str
    tools: str
    main_process_number: int | None
    process_only: bool


@dataclass(frozen=True)
class NormalizedBom:
    schema_version: str
    sheet_name: str
    header_row: int
    columns: dict[str, int]
    rows: tuple[NormalizedBomRow, ...]
    sheet_candidates: tuple[str, ...]

    @property
    def main_process_numbers(self) -> tuple[int, ...]:
        return tuple(sorted({row.main_process_number for row in self.rows if row.main_process_number is not None}))

    @property
    def renderable_process_numbers(self) -> tuple[int, ...]:
        return tuple(sorted({
            row.main_process_number
            for row in self.rows
            if row.main_process_number is not None and not row.process_only
        }))


def _column_index(cell_ref: str) -> int:
    match = re.match(r"[A-Z]+", cell_ref.upper())
    if match is None:
        raise ValueError(f"无效的 Excel 单元格引用：{cell_ref}")
    result = 0
    for character in match.group(0):
        result = result * 26 + ord(character) - 64
    return result


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in book.namelist():
        return []
    root = ET.fromstring(book.read("xl/sharedStrings.xml"))
    return ["".join(node.itertext()) for node in root.findall(f"{MAIN_NS}si")]


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    if cell.get("t") == "inlineStr":
        return "".join(cell.itertext()).strip()
    value = cell.find(f"{MAIN_NS}v")
    if value is None or value.text is None:
        return ""
    if cell.get("t") == "s":
        try:
            return shared[int(value.text)].strip()
        except (IndexError, ValueError):
            return ""
    return value.text.strip()


def _workbook_sheets(book: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(book.read("xl/workbook.xml"))
    relationships = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
    targets = {node.get("Id"): node.get("Target") for node in relationships}
    result: list[tuple[str, str]] = []
    for sheet in workbook.findall(f"{MAIN_NS}sheets/{MAIN_NS}sheet"):
        relation = sheet.get(f"{OFFICE_REL_NS}id")
        target = targets.get(relation)
        if not target:
            continue
        path = target.lstrip("/")
        if not path.startswith("xl/"):
            path = "xl/" + path
        result.append((sheet.get("name", ""), path))
    return result


def _read_sheet(book: zipfile.ZipFile, path: str, shared: list[str]) -> list[tuple[int, dict[int, str]]]:
    root = ET.fromstring(book.read(path))
    rows: list[tuple[int, dict[int, str]]] = []
    for row in root.findall(f"{MAIN_NS}sheetData/{MAIN_NS}row"):
        row_number = int(row.get("r", str(len(rows) + 1)))
        values = {
            _column_index(cell.get("r", "A1")): _cell_text(cell, shared)
            for cell in row.findall(f"{MAIN_NS}c")
        }
        rows.append((row_number, values))
    return rows


def _header_mapping(values: dict[int, str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for column, raw in values.items():
        normalized = _normal_text(raw)
        for canonical, aliases in NORMALIZED_ALIASES.items():
            if canonical not in mapping and normalized in aliases:
                mapping[canonical] = column
                break
    return mapping


def _sheet_score(rows: list[tuple[int, dict[int, str]]]) -> tuple[int, int, dict[str, int]]:
    best_row, best_mapping = 0, {}
    for row_number, values in rows[:40]:
        mapping = _header_mapping(values)
        if len(mapping) > len(best_mapping):
            best_row, best_mapping = row_number, mapping
    required = {"drawing_no", "name"}
    if not required.issubset(best_mapping):
        return 0, best_row, best_mapping
    data_rows = sum(1 for row_number, values in rows if row_number > best_row and values.get(best_mapping["drawing_no"], "").strip())
    return len(best_mapping) * 100 + min(data_rows, 99), best_row, best_mapping


CHINESE_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _chinese_integer(text: str) -> int | None:
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "百" in text:
        head, tail = text.split("百", 1)
        hundreds = CHINESE_DIGITS.get(head, 1)
        remainder = _chinese_integer(tail) if tail else 0
        return hundreds * 100 + (remainder or 0)
    if "十" in text:
        head, tail = text.split("十", 1)
        tens = CHINESE_DIGITS.get(head, 1) if head else 1
        ones = CHINESE_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + ones
    if len(text) == 1:
        return CHINESE_DIGITS.get(text)
    return None


def _main_process_number(text: str) -> int | None:
    match = re.search(r"第\s*([0-9一二两三四五六七八九十百零]+)\s*步", text)
    return _chinese_integer(match.group(1)) if match else None


def _process_only(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return bool(re.search(r"目视(?:化)?检查|检查.*是否|检验|测试|检漏", compact))


def _quantity(text: str) -> float | int | str:
    raw = text.strip()
    try:
        number = float(raw)
    except ValueError:
        return raw
    return int(number) if number.is_integer() else number


def normalize_bom(path: Path) -> NormalizedBom:
    path = Path(path)
    try:
        book = zipfile.ZipFile(path)
    except (FileNotFoundError, zipfile.BadZipFile) as error:
        raise ValueError(f"BOM 必须是可读取的 XLSX 文件：{path}") from error
    with book:
        shared = _shared_strings(book)
        candidates: list[tuple[int, str, int, dict[str, int], list[tuple[int, dict[int, str]]]]] = []
        for sheet_name, sheet_path in _workbook_sheets(book):
            rows = _read_sheet(book, sheet_path, shared)
            score, header_row, mapping = _sheet_score(rows)
            if score:
                candidates.append((score, sheet_name, header_row, mapping, rows))
    if not candidates:
        raise ValueError("BOM 中没有找到同时包含图号和名称的有效工作表")
    candidates.sort(key=lambda item: (-item[0], item[1].casefold()))
    _, sheet_name, header_row, columns, raw_rows = candidates[0]
    result_rows: list[NormalizedBomRow] = []

    def value(values: dict[int, str], key: str) -> str:
        column = columns.get(key)
        return values.get(column, "").strip() if column is not None else ""

    for row_number, values in raw_rows:
        if row_number <= header_row:
            continue
        drawing_no = value(values, "drawing_no")
        name = value(values, "name")
        if not drawing_no and not name:
            continue
        assembly_text = value(values, "assembly_text")
        result_rows.append(
            NormalizedBomRow(
                row=row_number,
                level=value(values, "level"),
                material_code=value(values, "material_code"),
                drawing_no=drawing_no,
                name=name,
                model=value(values, "model"),
                quantity=_quantity(value(values, "quantity")),
                unit=value(values, "unit"),
                assembly_text=assembly_text,
                control_points=value(values, "control_points"),
                tools=value(values, "tools"),
                main_process_number=_main_process_number(assembly_text),
                process_only=_process_only(assembly_text),
            )
        )
    if not result_rows:
        raise ValueError(f"BOM 工作表 {sheet_name!r} 没有有效物料行")
    top_score = candidates[0][0]
    tied = tuple(item[1] for item in candidates if item[0] == top_score)
    return NormalizedBom(
        schema_version="normalized-bom/v1",
        sheet_name=sheet_name,
        header_row=header_row,
        columns=dict(sorted(columns.items())),
        rows=tuple(result_rows),
        sheet_candidates=tied,
    )

