from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
import shutil
from typing import Protocol

from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as WorksheetImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.page import PageMargins


@dataclass(frozen=True)
class SopImage:
    image_id: str
    path: Path
    candidate_id: str | None = None
    recommended: bool = False
    placeholder: bool = False


@dataclass(frozen=True)
class SopStep:
    step_id: str
    main_process_id: str
    main_process_name: str
    title: str
    image: SopImage
    materials: tuple[tuple[str, str, int], ...]
    process_text: str
    control_points: str
    tools: str
    questioned: bool = False
    candidates: tuple[SopImage, ...] = ()


class WorkbookVerifier(Protocol):
    def verify(self, workbook_path: Path) -> None: ...


class OpenpyxlWorkbookVerifier:
    def verify(self, workbook_path: Path) -> None:
        workbook = load_workbook(workbook_path, read_only=False, data_only=False)
        if not workbook.sheetnames:
            raise ValueError("published workbook has no worksheets")
        for sheet in workbook.worksheets:
            if not sheet.print_area:
                raise ValueError(f"worksheet has no print area: {sheet.title}")
            if not sheet._images:
                raise ValueError(f"worksheet has no step image: {sheet.title}")
        workbook.close()


class SopPublisher:
    """Built-in, main-process-paginated XLSX publisher with a strict delivery root."""

    def __init__(
        self,
        *,
        images_per_page: int = 2,
        verifier: WorkbookVerifier | None = None,
    ) -> None:
        if images_per_page < 1:
            raise ValueError("images_per_page must be positive")
        self.images_per_page = images_per_page
        self.verifier = verifier or OpenpyxlWorkbookVerifier()

    def publish(
        self,
        steps: tuple[SopStep, ...],
        delivery_directory: Path,
        *,
        pending: bool = False,
    ) -> Path:
        if not steps:
            raise ValueError("SOP publication requires at least one step")
        delivery_directory.mkdir(parents=True, exist_ok=True)
        image_directory = delivery_directory / "步骤图片"
        image_directory.mkdir(parents=True, exist_ok=True)

        copied = self._copy_delivery_images(steps, image_directory, pending=pending)
        self._clean_known_delivery_artifacts(
            delivery_directory,
            image_directory,
            keep_images=set(copied.values()),
            pending=pending,
        )
        workbook = self._build_workbook(
            steps, copied, image_directory, pending=pending
        )
        filename = "SOP_待确认.xlsx" if pending else "SOP.xlsx"
        target = delivery_directory / filename
        temporary = delivery_directory / f".{filename}.tmp.xlsx"
        try:
            workbook.save(temporary)
        finally:
            # openpyxl keeps image streams on the workbook until it is closed.
            # Explicit closure is required for long Agent runs and for Windows
            # delivery directories that may be replaced immediately afterward.
            workbook.close()
        self.verifier.verify(temporary)
        temporary.replace(target)
        self._verify_delivery_whitelist(delivery_directory, pending=pending)
        return target

    def _copy_delivery_images(
        self,
        steps: tuple[SopStep, ...],
        image_directory: Path,
        *,
        pending: bool,
    ) -> dict[str, str]:
        copied: dict[str, str] = {}
        ordinal = 1
        for step in steps:
            images = step.candidates if pending and step.candidates else (step.image,)
            for image in images:
                if not image.path.is_file():
                    raise FileNotFoundError(image.path)
                suffix = image.path.suffix.lower() or ".png"
                candidate = f"-{image.candidate_id}" if image.candidate_id else ""
                destination_name = (
                    f"{ordinal:04d}-{_safe_filename(step.step_id)}{candidate}{suffix}"
                )
                destination = image_directory / destination_name
                shutil.copy2(image.path, destination)
                copied[image.image_id] = destination_name
                ordinal += 1
        return copied

    def _build_workbook(
        self,
        steps: tuple[SopStep, ...],
        copied: dict[str, str],
        image_directory: Path,
        *,
        pending: bool,
    ) -> Workbook:
        workbook = Workbook()
        workbook.remove(workbook.active)
        grouped: OrderedDict[tuple[str, str], list[SopStep]] = OrderedDict()
        for step in steps:
            grouped.setdefault(
                (step.main_process_id, step.main_process_name), []
            ).append(step)

        used_names: set[str] = set()
        for (_, process_name), process_steps in grouped.items():
            for page_index, start in enumerate(
                range(0, len(process_steps), self.images_per_page), start=1
            ):
                page_steps = process_steps[start : start + self.images_per_page]
                suffix = "" if page_index == 1 else f"-续页{page_index}"
                sheet_name = _unique_sheet_name(process_name + suffix, used_names)
                sheet = workbook.create_sheet(sheet_name)
                self._format_page(
                    sheet,
                    process_name,
                    page_steps,
                    copied,
                    image_directory,
                    pending,
                )
        return workbook

    def _format_page(
        self,
        sheet,
        process_name,
        steps,
        copied,
        image_directory,
        pending,
    ) -> None:
        sheet.sheet_view.showGridLines = False
        sheet.merge_cells("A1:H1")
        sheet["A1"] = process_name
        sheet["A1"].font = Font(name="Microsoft YaHei", size=18, bold=True, color="FFFFFF")
        sheet["A1"].fill = PatternFill("solid", fgColor="1F4E78")
        sheet["A1"].alignment = Alignment(horizontal="center", vertical="center")
        sheet.row_dimensions[1].height = 30
        sheet.merge_cells("A2:H2")
        sheet["A2"] = "待确认：当前采用 Agent 推荐图，请选择候选或输入修正" if pending else "已通过自动校验"
        sheet["A2"].font = Font(
            name="Microsoft YaHei",
            size=11,
            bold=pending,
            color="C00000" if pending else "548235",
        )
        sheet["A2"].alignment = Alignment(horizontal="center")
        sheet["A2"].fill = PatternFill(
            "solid", fgColor="FCE4D6" if pending else "E2F0D9"
        )
        for column, width in zip("ABCDEFGH", (15, 15, 15, 15, 18, 18, 18, 18), strict=True):
            sheet.column_dimensions[column].width = width

        thin = Side(style="thin", color="B7C9D6")
        for block_index, step in enumerate(steps):
            top = 4 + block_index * 20
            sheet.merge_cells(start_row=top, start_column=1, end_row=top, end_column=8)
            title = sheet.cell(top, 1, f"{step.step_id}  {step.title}")
            title.font = Font(name="Microsoft YaHei", size=12, bold=True, color="1F1F1F")
            title.fill = PatternFill("solid", fgColor="D9EAF7")
            title.alignment = Alignment(vertical="center")
            labels = (
                ("工艺说明", step.process_text),
                ("关键控制", step.control_points),
                ("工装工具", step.tools),
                ("物料", "；".join(f"{code} {name} ×{qty}" for code, name, qty in step.materials)),
            )
            for offset, (label, value) in enumerate(labels):
                row = top + 2 + offset * 3
                sheet.merge_cells(start_row=row, start_column=6, end_row=row, end_column=8)
                sheet.cell(row, 6, label).font = Font(name="Microsoft YaHei", bold=True, color="1F4E78")
                sheet.merge_cells(start_row=row + 1, start_column=6, end_row=row + 2, end_column=8)
                body = sheet.cell(row + 1, 6, value)
                body.font = Font(name="Microsoft YaHei", size=10)
                body.alignment = Alignment(wrap_text=True, vertical="top")
                for row_cells in sheet.iter_rows(
                    min_row=row, max_row=row + 2, min_col=6, max_col=8
                ):
                    for cell in row_cells:
                        cell.border = Border(bottom=thin)

        # Images are inserted after styles so each page can reference delivery files.
        for block_index, step in enumerate(steps):
            top = 4 + block_index * 20
            publication_image = _publication_image(step, pending)
            # Keep openpyxl away from long-lived Windows file handles.  The
            # workbook owns an in-memory stream until save completes, so the
            # delivery image can be atomically replaced or cleaned afterward.
            image_bytes = (
                image_directory / copied[publication_image.image_id]
            ).read_bytes()
            worksheet_image = WorksheetImage(BytesIO(image_bytes))
            scale = min(480 / worksheet_image.width, 285 / worksheet_image.height)
            worksheet_image.width *= scale
            worksheet_image.height *= scale
            sheet.add_image(worksheet_image, f"A{top + 2}")

        last_row = 3 + len(steps) * 20
        sheet.print_area = f"A1:H{last_row}"
        sheet.print_options.horizontalCentered = True
        sheet.page_setup.orientation = "portrait"
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 1
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_margins = PageMargins(
            left=0.25, right=0.25, top=0.35, bottom=0.35, header=0.1, footer=0.1
        )

    def _clean_known_delivery_artifacts(
        self,
        delivery_directory: Path,
        image_directory: Path,
        *,
        keep_images: set[str],
        pending: bool,
    ) -> None:
        for existing in image_directory.iterdir():
            if existing.is_file() and existing.name not in keep_images:
                existing.unlink()
        obsolete = delivery_directory / ("SOP.xlsx" if pending else "SOP_待确认.xlsx")
        if obsolete.exists():
            obsolete.unlink()

    @staticmethod
    def _verify_delivery_whitelist(delivery_directory: Path, *, pending: bool) -> None:
        workbook_name = "SOP_待确认.xlsx" if pending else "SOP.xlsx"
        actual = {entry.name for entry in delivery_directory.iterdir()}
        expected = {workbook_name, "步骤图片"}
        if actual != expected:
            raise ValueError(f"delivery directory violates whitelist: {sorted(actual)}")


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*]", "_", value).strip(" .")
    return cleaned or "step"


def _publication_image(step: SopStep, pending: bool) -> SopImage:
    if pending and step.candidates:
        return next(
            (candidate for candidate in step.candidates if candidate.recommended),
            step.candidates[0],
        )
    return step.image


def _unique_sheet_name(requested: str, used: set[str]) -> str:
    base = re.sub(r"[\\/*?:\[\]]", "_", requested).strip() or "主工序"
    base = base[:31]
    candidate = base
    ordinal = 2
    while candidate.casefold() in used:
        suffix = f"-{ordinal}"
        candidate = base[: 31 - len(suffix)] + suffix
        ordinal += 1
    used.add(candidate.casefold())
    return candidate
