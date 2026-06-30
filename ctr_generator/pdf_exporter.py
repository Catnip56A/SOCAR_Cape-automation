"""
ctr_generator/pdf_exporter.py

Converts an xlsx file to PDF using LibreOffice headless.
Requires soffice (LibreOffice) to be on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def export_to_pdf(xlsx_path: str | Path, output_dir: str | Path) -> Path:
    """
    Convert xlsx_path to a PDF in output_dir using LibreOffice headless.

    Returns the Path of the created PDF.
    Raises RuntimeError if soffice is not found or conversion fails.
    """
    xlsx_path  = Path(xlsx_path)
    output_dir = Path(output_dir)

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        raise RuntimeError(
            "LibreOffice (soffice) was not found on PATH.\n"
            "Please install LibreOffice and ensure 'soffice' is accessible:\n"
            "  https://www.libreoffice.org/download/download/"
        )

    try:
        result = subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", str(output_dir),
                str(xlsx_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"LibreOffice did not finish converting {xlsx_path.name} within "
            f"120 seconds — it may be stuck. The spreadsheet itself is fine; "
            f"only the PDF export was skipped."
        ) from e

    if result.returncode != 0:
        raise RuntimeError(
            f"LibreOffice conversion failed for {xlsx_path.name}:\n"
            f"{result.stderr or result.stdout}"
        )

    pdf_path = output_dir / xlsx_path.with_suffix(".pdf").name
    if not pdf_path.exists():
        raise RuntimeError(
            f"PDF not found after conversion: {pdf_path}\n"
            f"soffice output: {result.stdout}"
        )

    return pdf_path
