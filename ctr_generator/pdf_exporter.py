"""
ctr_generator/pdf_exporter.py

Converts an xlsx file to PDF using LibreOffice headless.
Looks for soffice on PATH first, then falls back to the standard install
locations per OS — LibreOffice's own installer does not add itself to PATH
on Windows, so relying on PATH alone misses most real installs there.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path


def _find_soffice() -> str | None:
    """Locate the soffice executable via PATH, then well-known install dirs."""
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found

    candidates: list[str] = []
    system = platform.system()
    if system == "Windows":
        for env_var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            base = os.environ.get(env_var)
            if base:
                candidates.append(str(Path(base) / "LibreOffice" / "program" / "soffice.exe"))
    elif system == "Darwin":
        candidates.append("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    else:
        candidates += [
            "/usr/bin/soffice",
            "/usr/local/bin/soffice",
            "/opt/libreoffice/program/soffice",
            "/snap/bin/libreoffice",
        ]
        candidates += [str(p) for p in Path("/opt").glob("libreoffice*/program/soffice")]

    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return None


def export_to_pdf(xlsx_path: str | Path, output_dir: str | Path) -> Path:
    """
    Convert xlsx_path to a PDF in output_dir using LibreOffice headless.

    Returns the Path of the created PDF.
    Raises RuntimeError if soffice is not found or conversion fails.
    """
    xlsx_path  = Path(xlsx_path)
    output_dir = Path(output_dir)

    soffice = _find_soffice()
    if soffice is None:
        raise RuntimeError(
            "LibreOffice (soffice) was not found on PATH or in the standard "
            "install locations.\n"
            "Please install LibreOffice — the default installer location "
            "(e.g. C:\\Program Files\\LibreOffice) is detected automatically, "
            "no PATH changes needed:\n"
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
