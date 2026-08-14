from __future__ import annotations

from pathlib import Path
import tempfile


class ExcelComVerifier:
    """Final Windows QA using ordinary desktop Excel in read-only mode."""

    def verify(self, workbook_path: Path) -> None:
        try:
            import win32com.client
        except ImportError as error:
            raise RuntimeError("Excel COM verification requires pywin32") from error

        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        workbook = None
        try:
            workbook = excel.Workbooks.Open(
                str(workbook_path.resolve()),
                UpdateLinks=0,
                ReadOnly=True,
                IgnoreReadOnlyRecommended=True,
            )
            if workbook.Worksheets.Count < 1:
                raise ValueError("Excel opened a workbook without worksheets")
            for worksheet in workbook.Worksheets:
                if not worksheet.PageSetup.PrintArea:
                    raise ValueError(
                        f"Excel found no print area: {worksheet.Name}"
                    )
                if worksheet.Shapes.Count < 1:
                    raise ValueError(f"Excel found no image: {worksheet.Name}")
            with tempfile.TemporaryDirectory(prefix="qwen-sop-excel-audit-") as folder:
                pdf = Path(folder) / "audit.pdf"
                workbook.ExportAsFixedFormat(0, str(pdf.resolve()))
                if not pdf.is_file() or pdf.stat().st_size == 0:
                    raise ValueError("Excel PDF audit export failed")
        finally:
            if workbook is not None:
                workbook.Close(SaveChanges=False)
            excel.Quit()
