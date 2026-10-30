"""Optional actual Excel-to-PDF previews. Not required to build the XLSX."""
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory


def render_preview(output: Path, pages: list[int], backend='auto'):
    try:
        import pymupdf as fitz
    except ImportError as error:
        raise RuntimeError('Preview needs: pip install PyMuPDF. XLSX publication does not need it.') from error
    data = json.loads((output / '出版内容.json').read_text(encoding='utf8'))
    if not pages or len(set(pages)) != len(pages) or any(n < 1 or n > len(data['pages']) for n in pages):
        raise ValueError('Preview page numbers are outside the workbook')
    target = output / 'SOP页预览'
    target.mkdir(parents=True, exist_ok=True)
    libreoffice = shutil.which('soffice')
    if not libreoffice:
        candidate = Path('C:/Program Files/LibreOffice/program/soffice.exe')
        libreoffice = str(candidate) if candidate.is_file() else None
    if backend == 'libreoffice' or (backend == 'auto' and libreoffice):
        if not libreoffice:
            raise RuntimeError('LibreOffice is not installed or soffice is not on PATH')
        with TemporaryDirectory(prefix='lo-preview-', dir=target) as directory:
            work = Path(directory)
            settings = json.dumps({'PageRange': {'type': 'string', 'value': ','.join(map(str, sorted(pages)))}})
            result = subprocess.run([libreoffice, '-env:UserInstallation=' + (work/'profile').resolve().as_uri(),
                '--headless', '--convert-to', 'pdf:calc_pdf_Export:' + settings,
                '--outdir', str(work.resolve()), str((output/data['publication_file']).resolve())],
                capture_output=True, text=True, timeout=120)
            pdf = work / Path(data['publication_file']).with_suffix('.pdf').name
            if result.returncode or not pdf.exists():
                raise RuntimeError('LibreOffice PDF export failed: ' + result.stderr + result.stdout)
            with fitz.open(pdf) as document:
                if len(document) != len(pages):
                    raise ValueError('Unexpected PDF page count; check worksheet print areas')
                for index, number in enumerate(sorted(pages)):
                    document[index].get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(str(target/f'第{number:02}页.png'))
                    print(f'PAGE_RENDERED {number}', flush=True)
        return
    try:
        import pythoncom
        import win32com.client
    except ImportError as error:
        raise RuntimeError('Excel preview needs local Excel and pip install pywin32, or install LibreOffice.') from error
    pythoncom.CoInitialize()
    excel = workbook = None
    try:
        excel = win32com.client.DispatchEx('Excel.Application')
        excel.Visible = False
        excel.DisplayAlerts = False
        workbook = excel.Workbooks.Open(str((output / data['publication_file']).resolve()), UpdateLinks=0, ReadOnly=True)
        for number in pages:
            pdf = target / f'第{number:02}页.pdf'
            workbook.Worksheets(number).ExportAsFixedFormat(0, str(pdf.resolve()))
            with fitz.open(pdf) as document:
                if len(document) != 1:
                    raise ValueError(f'Worksheet {number} prints to more than one page')
                document[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(str(target / f'第{number:02}页.png'))
            print(f'PAGE_RENDERED {number}', flush=True)
    finally:
        if workbook is not None:
            workbook.Close(False)
        if excel is not None:
            excel.Quit()
        pythoncom.CoUninitialize()
