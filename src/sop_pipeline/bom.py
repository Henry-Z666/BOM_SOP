"""Read the input XLSX without treating Excel as an authoring database."""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


@dataclass(frozen=True)
class BomItem:
    row: int
    level: str
    material_code: str
    drawing_no: str
    name: str
    model: str
    quantity: float | str
    unit: str
    assembly_text: str
    control_points: str
    tools: str


def _col(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref).group(0)
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - 64
    return value


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    value = cell.find(f"{NS}v")
    if value is None:
        inline = cell.find(f"{NS}is/{NS}t")
        return inline.text if inline is not None and inline.text else ""
    text = value.text or ""
    return shared[int(text)] if cell.get("t") == "s" else text


def read_bom(path: Path) -> list[BomItem]:
    with zipfile.ZipFile(path) as book:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ET.fromstring(book.read("xl/sharedStrings.xml"))
            shared = ["".join(node.itertext()) for node in root.findall(f"{NS}si")]
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        sheet = next(s for s in workbook.findall(f"{NS}sheets/{NS}sheet") if s.get("name") == "水箱BOM")
        rid = sheet.get(f"{REL_NS}id")
        rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in rels if r.get("Id") == rid)
        sheet_path = "xl/" + target.lstrip("/")
        root = ET.fromstring(book.read(sheet_path))

    rows: list[dict[int, str]] = []
    for row in root.findall(f"{NS}sheetData/{NS}row"):
        values = {_col(cell.get("r")): _cell_value(cell, shared) for cell in row.findall(f"{NS}c")}
        rows.append(values)

    items: list[BomItem] = []
    for index, values in enumerate(rows[1:], start=2):
        drawing = values.get(5, "").strip()
        if not drawing:
            continue
        qty_text = values.get(10, "")
        try:
            quantity: float | str = float(qty_text)
            if quantity.is_integer():
                quantity = int(quantity)
        except ValueError:
            quantity = qty_text
        items.append(BomItem(index, values.get(2, "").strip(), values.get(3, "").strip(), drawing,
                             values.get(6, "").strip(), values.get(7, "").strip(), quantity,
                             values.get(11, "").strip(), values.get(16, "").strip(),
                             values.get(17, "").strip(), values.get(18, "").strip()))
    return items


def direct_children(items: list[BomItem], parent_level: str) -> list[BomItem]:
    prefix = f"{parent_level}."
    depth = parent_level.count(".") + 1
    return [item for item in items if item.level.startswith(prefix) and item.level.count(".") == depth]
