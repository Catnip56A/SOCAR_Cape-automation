"""
ctr_generator/builder_azn.py

Copies the AZN CTR template and writes labor rows into sheet "Main AZN".

Template layout (verified from 217_AZN template):
  Row 3  : E3 = CTR ref string, O3 = job ref integer
  Row 7  : "Project Support" section header
  Row 8  : column headers for Project Support section
  Rows 9–18 : onshore / project-support data rows
  Row 20 : G20 = total project support
  Row 21 : "Offshore Activities" section header
  Row 22 : column headers for Offshore Activities section
  Rows 23–62: offshore data rows
  Row 64 : G64 = total offshore activities
  Summary (rows 66–71):
    G67 = total project support
    G68 = total offshore activities
    G71 = estimated CTR total

  Editable header fields (rows 3–6, shared layout with USD template):
    B3 = Client, C3 = Sub-Client, B4 = Location, E4 = Date,
    G4 = Contract No, E5 = Revision, A6 = Scope / description (merged A6:G6)

All values written as plain Python numbers — no formula reliance.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import openpyxl

# Project Support (Onshore) section — rows 9–18, total at G20
_ONSHORE_DATA_START  = 9
_ONSHORE_DATA_END    = 18   # 10 writable rows

# Offshore Activities section — rows 23–62, total at G64
_OFFSHORE_DATA_START = 23
_OFFSHORE_DATA_END   = 62

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y")


def _parse_date(value):
    """Best-effort string → datetime conversion; returns value unchanged if unparseable."""
    if isinstance(value, str):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return value


def build_azn(
    onshore_rows: list[dict],
    offshore_rows: list[dict],
    template_path: str | Path,
    output_dir: str | Path,
    job_ref: str,
    header: dict | None = None,
) -> Path:
    """
    Write labor rows into a copy of the AZN template.

    onshore_rows / offshore_rows: list of dicts with keys:
        comment, num_employees (int/float), description (str),
        quantity (float), uom (str), rate_azn (float), nationality (str)

    header: optional dict with keys client, sub_client, location, scope,
        date, contract_no, revision. Only non-empty values overwrite the
        corresponding template cell — leaving a field blank preserves
        whatever the template already has.

    Returns the path to the saved xlsx file.
    """
    template_path = Path(template_path)
    output_dir    = Path(output_dir)

    if not template_path.is_file():
        raise ValueError(f"AZN Template not found: {template_path}")

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Cannot create output folder {output_dir}: {e}") from e

    out_path = output_dir / f"{job_ref}_AZN_WCH_CTR.xlsx"
    try:
        shutil.copy2(template_path, out_path)
    except PermissionError as e:
        raise ValueError(
            f"Cannot write {out_path} — it may be open in Excel in another "
            f"window. Close it there and try again."
        ) from e
    except OSError as e:
        raise ValueError(f"Could not copy AZN template to {out_path}: {e}") from e

    try:
        wb = openpyxl.load_workbook(out_path, keep_vba=False)
        ws = wb["Main AZN"]
    except KeyError as e:
        raise ValueError(
            f'AZN Template {template_path} has no sheet named "Main AZN". '
            f"Is this the right template?"
        ) from e
    except Exception as e:
        raise ValueError(f"Could not read AZN Template {template_path}: {e}") from e

    # ── Header cells ──────────────────────────────────────────────────────────
    ws["E3"] = f"CTR-26-{job_ref} AZN"
    ws["O3"] = int(job_ref)

    if header:
        if header.get("client"):       ws["B3"] = header["client"]
        if header.get("sub_client"):   ws["C3"] = header["sub_client"]
        if header.get("location"):     ws["B4"] = header["location"]
        if header.get("date"):         ws["E4"] = _parse_date(header["date"])
        if header.get("contract_no"):  ws["G4"] = header["contract_no"]
        if header.get("revision") not in (None, ""):
            ws["E5"] = header["revision"]
        if header.get("scope"):        ws["A6"] = header["scope"]

    def _write_section(rows, start, end):
        for row in range(start, end + 1):
            for col in range(1, 10):
                ws.cell(row=row, column=col).value = None

        section_total = 0.0
        for i, lr in enumerate(rows):
            if i >= (end - start + 1):
                break
            r = start + i
            try:
                num_emp = float(lr.get("num_employees", 1) or 1)
            except (ValueError, TypeError):
                num_emp = 1.0
            try:
                qty = float(lr.get("quantity", 0) or 0)
            except (ValueError, TypeError):
                qty = 0.0
            try:
                rate = float(lr.get("rate_azn", 0) or 0)
            except (ValueError, TypeError):
                rate = 0.0

            row_total = num_emp * qty * rate
            section_total += row_total

            ws.cell(row=r, column=1).value = str(lr.get("comment", ""))
            ws.cell(row=r, column=2).value = num_emp
            ws.cell(row=r, column=3).value = str(lr.get("description", ""))
            ws.cell(row=r, column=4).value = qty
            ws.cell(row=r, column=5).value = str(lr.get("uom", "Hours"))
            ws.cell(row=r, column=6).value = rate
            ws.cell(row=r, column=7).value = row_total
            ws.cell(row=r, column=8).value = row_total
            ws.cell(row=r, column=9).value = str(lr.get("nationality", "NAT"))

        return section_total

    total_onshore  = _write_section(onshore_rows,  _ONSHORE_DATA_START,  _ONSHORE_DATA_END)
    total_offshore = _write_section(offshore_rows, _OFFSHORE_DATA_START, _OFFSHORE_DATA_END)

    # ── Totals ────────────────────────────────────────────────────────────────
    ws["G20"] = total_onshore
    ws["G64"] = total_offshore
    ws["G67"] = total_onshore
    ws["G68"] = total_offshore
    ws["G71"] = total_onshore + total_offshore

    try:
        wb.save(out_path)
    except PermissionError as e:
        raise ValueError(
            f"Cannot save {out_path} — it may be open in Excel in another "
            f"window. Close it there and try again."
        ) from e
    finally:
        wb.close()
    return out_path
