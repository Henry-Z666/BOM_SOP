from __future__ import annotations

from dataclasses import dataclass, replace
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
    "process_route": {"对应工艺路线工序", "工艺路线工序"},
    "station": {"发料工位", "工位"},
    "cad_model_names": {"模型名称", "CAD模型名称"},
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
    process_route: str = ""
    station: str = ""
    cad_model_names: str = ""
    source_level: str = ""
    level_fill_rgb: str = ""
    assembly_boundary: bool = False


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


def normalize_bom(path: Path, *, root_bom_row: int | None = None) -> NormalizedBom:
    path = Path(path)
    try:
        book = zipfile.ZipFile(path)
    except (FileNotFoundError, zipfile.BadZipFile) as error:
        raise ValueError(f"BOM 必须是可读取的 XLSX 文件：{path}") from error
    with book:
        shared = _shared_strings(book)
        candidates: list[tuple[int, str, int, dict[str, int], list[tuple[int, dict[int, str]]]]] = []
        fills = _level_cell_fills(book)
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
                process_route=value(values, "process_route"),
                station=value(values, "station"),
                cad_model_names=value(values, "cad_model_names"),
                source_level=value(values, "level"),
                level_fill_rgb=fills.get((sheet_name,row_number,columns.get('level',0)),''),
                assembly_boundary=fills.get((sheet_name,row_number,columns.get('level',0)),'')=='FFFF00',
            )
        )
    if not result_rows:
        raise ValueError(f"BOM 工作表 {sheet_name!r} 没有有效物料行")
    if root_bom_row is not None:
        selected = next((row for row in result_rows if row.row == root_bom_row), None)
        if selected is None:
            raise ValueError("产品配置指定的 BOM 根行不存在")
        if not selected.level:
            if selected != result_rows[0] or any(not row.level for row in result_rows[1:]):
                raise ValueError("空层次根行必须唯一并位于物料层次之前")
            result_rows = [replace(row, level="0" if row.row == root_bom_row else "0." + row.level)
                for row in result_rows]
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


def _level_cell_fills(book: zipfile.ZipFile) -> dict[tuple[str,int,int],str]:
    """Retain explicit solid fill evidence; never infer yellow from a part name."""
    if 'xl/styles.xml' not in book.namelist():return {}
    styles=ET.fromstring(book.read('xl/styles.xml'))
    fills=styles.find(f'{MAIN_NS}fills');xfs=styles.find(f'{MAIN_NS}cellXfs')
    if fills is None or xfs is None:return {}
    colors=[]
    for fill in fills:
        pattern=fill.find(f'{MAIN_NS}patternFill');rgb=''
        if pattern is not None and pattern.get('patternType')=='solid':
            fg=pattern.find(f'{MAIN_NS}fgColor')
            if fg is not None:rgb=fg.get('rgb','')[-6:].upper()
        colors.append(rgb)
    style_colors=[colors[int(x.get('fillId','0'))] for x in xfs]
    result={}
    for name,part in _workbook_sheets(book):
        for cell in ET.fromstring(book.read(part)).iter(f'{MAIN_NS}c'):
            ref=cell.get('r','');match=re.fullmatch(r'([A-Z]+)(\d+)',ref)
            if match:
                index=int(cell.get('s','0'))
                rgb=style_colors[index] if index<len(style_colors) else ''
                if rgb:result[(name,int(match.group(2)),_column_index(ref))]=rgb
    return result


def supplied_assembly_owners(bom: NormalizedBom) -> dict[int,int]:
    """Yellow assemblies are indivisible issued units; their descendants are internal."""
    stack=[];owners={}
    for row in bom.rows:
        level=tuple(row.level.split('.')) if row.level else ()
        while stack and not (len(level)>len(stack[-1][0]) and level[:len(stack[-1][0])]==stack[-1][0]):stack.pop()
        if stack:owners[row.row]=stack[-1][1]
        elif row.assembly_boundary:stack.append((level,row.row))
    return owners


def assembly_scope_frontier(bom: NormalizedBom) -> NormalizedBom:
    owners=supplied_assembly_owners(bom)
    return replace(bom,rows=tuple(r for r in bom.rows if r.row not in owners))


def color_boundary_contract(bom: NormalizedBom) -> dict[str, Any]:
    """Persist issued-unit ownership before filtering; reuse at every pipeline seam."""
    owners = supplied_assembly_owners(bom)
    return {
        'schema_version': 'bom-color-boundary/v1',
        'source_column': 'level',
        'issued_fill_rgb': 'FFFF00',
        'rows': {
            str(r.row): {
                'level': r.source_level or r.level,
                'fill_rgb': r.level_fill_rgb,
                'owner_row': owners.get(r.row),
                'role': ('internal' if r.row in owners else
                         'issued_unit' if r.assembly_boundary else 'process_context'),
            } for r in bom.rows
        },
    }


def validate_color_boundary_rows(contract: dict[str, Any], source_rows: Any) -> None:
    """Prevent old plans or publication manifests reopening issued assemblies."""
    if contract.get('schema_version') != 'bom-color-boundary/v1':
        raise ValueError('Unsupported BOM color boundary contract')
    for number in source_rows:
        row = contract['rows'].get(str(number))
        if row is None:
            raise ValueError(f'BOM row {number} has no color boundary evidence')
        if row['role'] == 'internal' or row.get('owner_row') is not None:
            raise ValueError(f'BOM row {number} is internal to issued unit {row["owner_row"]}')

