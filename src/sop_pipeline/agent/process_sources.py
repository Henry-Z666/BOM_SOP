"""Read source cells and DrawingML; preserve image-only facts as review items."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path, PurePosixPath
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from .bom_normalizer import _shared_strings, _workbook_sheets, _read_sheet
from .process_model import ProcessOperation, SourceEvidence, TorqueClaim, fingerprint, operation_payload


_TORQUE = re.compile(r'(?<![\d.])(\d+(?:\.\d+)?)(\s*(?:±|\+/-)\s*\d+(?:\.\d+)?\s*%?)?\s*'
    r'(N\s*[.·•・]?\s*[mM]|kgf\s*[.·•]?\s*cm)(?![A-Za-z])', re.IGNORECASE)
_DRAWING_NS = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
_REL_NS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def claims_in(evidence: SourceEvidence) -> tuple[TorqueClaim, ...]:
    # Conversion explanations are evidence for review, never parameter values.
    if re.search(r'换算|转换|(?<![\d.])1\s*kgf.*=', evidence.text, re.IGNORECASE):
        return ()
    result = []
    for match in _TORQUE.finditer(evidence.text):
        if float(match.group(1)) <= 0:
            continue
        unit = 'kgf.cm' if match.group(3).lower().startswith('kgf') else 'N.m'
        identity = fingerprint([asdict(evidence), match.start(), match.group(0)])
        result.append(TorqueClaim(identity, match.group(1), unit, evidence.text,
            re.sub(r'\s+', '', match.group(2) or ''), evidence))
    return tuple(result)


def _column_name(index: int) -> str:
    name = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _relationships(book: ZipFile, part: str) -> dict[str, str]:
    path = str(PurePosixPath(part).parent / '_rels' / (PurePosixPath(part).name + '.rels'))
    if path not in book.namelist():
        return {}
    result = {}
    for node in ET.fromstring(book.read(path)):
        if node.get('TargetMode') == 'External':
            continue
        target = node.get('Target', '')
        result[node.get('Id', '')] = target.lstrip('/') if target.startswith('/') else posixpath.normpath(
            posixpath.join(posixpath.dirname(part), target))
    return result


def read_process_card(path: Path) -> tuple[ProcessOperation, ...]:
    """QSL layout adapter. Core model has no QSL cell-coordinate dependencies."""
    digest = 'sha256:' + sha256(path.read_bytes()).hexdigest()
    operations = []
    with ZipFile(path) as book:
        shared = _shared_strings(book)
        for sheet, part in _workbook_sheets(book):
            if any(word in sheet for word in ('封面', '履历', '流程', '扭矩', '力矩', '通用')):
                continue
            evidence = []
            materials = []
            for row, values in _read_sheet(book, part, shared):
                for column, text in values.items():
                    if not text.strip():
                        continue
                    # AJ:AR control, materials and tools in this specific adapter.
                    if column == 36 and 8 <= row <= 18:
                        evidence.append(SourceEvidence(path.name, digest, sheet,
                            f'{_column_name(column)}{row}', text))
                    if column == 36 and 21 <= row <= 29 and re.fullmatch(r'\d{12}', text.strip()):
                        materials.append(text.strip())
            root = ET.fromstring(book.read(part))
            relationships = _relationships(book, part)
            for node in root.iter():
                if node.tag.rsplit('}', 1)[-1] != 'drawing':
                    continue
                target = relationships.get(node.get(_REL_NS + 'id', ''))
                if not target or target not in book.namelist():
                    continue
                drawing_root = ET.fromstring(book.read(target))
                for index, anchor in enumerate(drawing_root):
                    paragraphs = [''.join(item.itertext()) for item in anchor.iter(_DRAWING_NS + 't')]
                    text = ' '.join(paragraphs).strip()
                    if text:
                        evidence.append(SourceEvidence(path.name, digest, sheet,
                            f'{target}#anchor-{index}', text))
            if not evidence:
                continue
            scope = 'subassembly' if '分装' in sheet else 'pending'
            identity = 'op-' + fingerprint([digest, sheet]).split(':')[1][:20]
            operations.append(ProcessOperation(identity, sheet, scope, tuple(dict.fromkeys(materials)),
                tuple(evidence), tuple(claim for item in evidence for claim in claims_in(item))))
    return tuple(operations)


def read_source_package(files: tuple[Path, ...], *, card_files: tuple[Path, ...] | None = None) -> dict:
    operations = []
    facts = []
    media = []
    parameter_catalog = []
    manifest = []
    sheet_review = []
    selected_cards = {path.resolve() for path in (files if card_files is None else card_files)}
    for path in files:
        digest = 'sha256:' + sha256(path.read_bytes()).hexdigest()
        is_card = path.resolve() in selected_cards
        manifest.append({'file': str(path.resolve()), 'sha256': digest, 'role': 'process_card' if is_card else 'reference'})
        with ZipFile(path) as book:
            shared = _shared_strings(book)
            for sheet, part in _workbook_sheets(book):
                if is_card:
                    sheet_review.append({'file': path.name, 'sheet': sheet, 'status': 'pending_operation_review'})
                for row, values in _read_sheet(book, part, shared):
                    for column, text in values.items():
                        if text.strip():
                            item = SourceEvidence(path.name, digest, sheet, f'{_column_name(column)}{row}', text)
                            facts.append(asdict(item))
                            parameter_catalog.extend(asdict(claim) for claim in claims_in(item))
            for name in sorted(book.namelist()):
                if name.startswith('xl/media/'):
                    media.append({'file': path.name, 'source_sha256': digest, 'location': name,
                        'sha256': 'sha256:' + sha256(book.read(name)).hexdigest(),
                        'status': 'pending_image_fact_review'})
        if is_card:
            operations.extend(read_process_card(path))
    parsed = {(operation.instructions[0].file, operation.instructions[0].sheet) for operation in operations}
    for sheet in sheet_review:
        sheet['status'] = 'operation_candidate' if (sheet['file'], sheet['sheet']) in parsed else 'pending_no_operation_extracted'
    payload = operation_payload(tuple(operations))
    return {**payload, 'sources': manifest, 'facts': facts, 'media_review': media,
        'sheet_review': sheet_review,
        'parameter_catalog': parameter_catalog,
        'source_package_fingerprint': fingerprint(manifest)}
