"""
ctr_tools/pdf_exporter.py

Converts an xlsx file to PDF using LibreOffice headless.
Looks for soffice on PATH first, then falls back to the standard install
locations per OS — LibreOffice's own installer does not add itself to PATH
on Windows, so relying on PATH alone misses most real installs there.

A Word proposal (PDMS) is exported with Word itself when it is installed (export_docx_to_pdf): Word draws
its own file as designed, LibreOffice places the anchored shapes of the long proposal slightly differently.
Any problem with Word (not installed, a dialog, a timeout) falls back to LibreOffice.
"""

from __future__ import annotations

import base64
import logging
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

WORD_TIMEOUT = 150      # seconds for Word to open, update the contents page numbers and export


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


# ── Word (PDMS proposal) ──────────────────────────────────────────────────────

def _powershell() -> str | None:
    """powershell.exe on Windows, and from WSL (Windows interop); None elsewhere."""
    return shutil.which("powershell.exe") or (shutil.which("powershell") if platform.system() == "Windows" else None)


def _windows_path(p: Path, ps: str) -> str:
    """The path as Windows (and Word) names it; under WSL a Linux path becomes \\\\wsl.localhost\\…"""
    if platform.system() == "Windows":
        return str(p)
    out = subprocess.run(["wslpath", "-w", str(p)], capture_output=True, text=True, timeout=15)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(f"wslpath failed for {p}")
    return out.stdout.strip()


def _word_script(src: str, dst: str, progid: str = "Word.Application") -> str:
    q = lambda t: "'" + t.replace("'", "''") + "'"
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "$w = $null; $d = $null; $global:code = 0\n"
        "$before = @(Get-Process WINWORD -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })\n"
        "[Console]::Out.WriteLine('WORD-BEFORE: ' + ($before -join ','))\n"   # so a hung run can stop only the Word it started
        "[Console]::Out.Flush()\n"
        "try {\n"
        f"  $w = New-Object -ComObject {progid}\n"
        "  $w.Visible = $false\n"
        "  $w.DisplayAlerts = 0\n"
        f"  $d = $w.Documents.Open({q(src)}, $false, $true)\n"            # no conversion dialog, read-only
        "  try { foreach ($t in $d.TablesOfContents) { $t.UpdatePageNumbers() } } catch {}\n"
        f"  $d.ExportAsFixedFormat({q(dst)}, 17)\n"                       # 17 = PDF
        "} catch {\n"
        "  [Console]::Out.WriteLine('WORD-ERROR: ' + $_.Exception.Message)\n"   # a clean line instead of PowerShell's XML
        "  $global:code = 1\n"
        "} finally {\n"
        "  try { if ($d) { $d.Close([ref]0) } } catch {}\n"               # 0 = without saving
        "  try { if ($w -and $w.Documents.Count -eq 0) { $w.Quit([ref]0) } } catch {}\n"   # never close Word windows that are not ours
        "}\n"
        "exit $global:code\n")


def _stop_started_word(output: str, ps: str) -> None:
    """After a timeout: stop the Word process this run started, however early it timed out. The script printed the
    Word processes that existed before it began; a process that is new and has no window (ours is invisible) is
    stopped, a Word window the user opened is never touched. Best effort."""
    line = next((ln for ln in (output or "").splitlines() if ln.startswith("WORD-BEFORE:")), None)
    if line is None:
        return                                          # not even started: nothing of ours exists
    before = [x.strip() for x in line.split(":", 1)[1].split(",") if x.strip().isdigit()]
    time.sleep(4)                                       # a Word that was still starting shows up by now
    cmd = ("$b = @(" + ",".join(before) + "); "
           "Get-Process WINWORD -ErrorAction SilentlyContinue | "
           "Where-Object { $b -notcontains $_.Id -and $_.MainWindowHandle -eq 0 } | Stop-Process -Force")
    try:
        subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True, timeout=30)
        log.warning("Word did not finish: the Word process started for the export was stopped.")
    except (OSError, subprocess.SubprocessError):
        pass


def export_docx_via_word(docx_path: str | Path, output_dir: str | Path, timeout: int = WORD_TIMEOUT,
                         progid: str = "Word.Application") -> Path:
    """The PDF of a Word file made by Word itself (the contents page numbers are updated in the PDF, the .docx is
    not changed). Raises RuntimeError when Word cannot be used."""
    docx_path, output_dir = Path(docx_path).resolve(), Path(output_dir).resolve()
    ps = _powershell()
    if ps is None:
        raise RuntimeError("PowerShell (needed to drive Word) is not available.")
    pdf_path = output_dir / docx_path.with_suffix(".pdf").name
    started = time.time()
    script = _word_script(_windows_path(docx_path, ps), _windows_path(pdf_path, ps), progid)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    proc = subprocess.Popen([ps, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        proc.kill()
        out, _err = proc.communicate()
        _stop_started_word(out or (e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else e.stdout or ""), ps)
        raise RuntimeError(f"Word did not finish within {timeout} seconds.") from e
    res = subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
    if res.returncode != 0:
        lines = [ln for ln in ((res.stdout or "") + "\n" + (res.stderr or "")).splitlines() if "WORD-ERROR:" in ln]
        detail = lines[0].split("WORD-ERROR:", 1)[1].strip() if lines else f"exit code {res.returncode}"
        raise RuntimeError("Word could not export the PDF: " + detail)
    if not pdf_path.exists() or pdf_path.stat().st_mtime < started - 2:
        raise RuntimeError("Word reported success but no new PDF was written.")
    return pdf_path


def export_docx_to_pdf(docx_path: str | Path, output_dir: str | Path) -> tuple[Path, str]:
    """PDF of a Word proposal: made by Word when it can be, else by LibreOffice. Returns (pdf path, "Word" or
    "LibreOffice"); raises RuntimeError only when both fail (the LibreOffice message)."""
    if os.environ.get("CTR_PDF_ENGINE", "").strip().lower() != "libreoffice":     # that value forces LibreOffice (tests, or if Word misbehaves)
        try:
            return export_docx_via_word(docx_path, output_dir), "Word"
        except (RuntimeError, OSError) as exc:
            log.info("PDF through Word not used (%s); using LibreOffice.", exc)
    return export_to_pdf(docx_path, output_dir), "LibreOffice"
