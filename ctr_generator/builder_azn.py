"""
ctr_generator/builder_azn.py

Copies the AZN CTR template and writes labor rows into sheet "Main AZN".

Row/column positions are read from ctr_generator/template_config.json
(section "azn_template") so they can be adjusted without touching this
file when a new template version shifts the layout.

Default layout (217_AZN template):
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

Input values (comment, employees, quantity, rate, etc.) are written as
plain Python values. Anything derived from another cell — row totals,
section totals, summary cells — is written as an Excel formula referencing
that cell, so editing an input in Excel recalculates its dependents. This
only matters for the .xlsx output; the PDF export is a flat rendering, so
formulas there simply show their last-calculated value.
"""

from __future__ import annotations

import shutil
from copy import copy
from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.utils import column_index_from_string as _col_idx
from openpyxl.utils import get_column_letter as _col_letter

from ctr_generator.config import CFG

_azn = CFG["azn_template"]

_ONSHORE_DATA_START  = _azn["onshore_data_start"]
_ONSHORE_DATA_END    = _azn["onshore_data_end"]
_ONSHORE_TOTAL_GAP   = _azn["onshore_total_gap"]   # blank rows between data end and total

_OFFSHORE_DATA_START = _azn["offshore_data_start"]
_OFFSHORE_DATA_END   = _azn["offshore_data_end"]
_OFFSHORE_TOTAL_GAP  = _azn["offshore_total_gap"]

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y")


def _cell_row_col(addr: str) -> tuple[int, int]:
    """Convert a cell address like 'G67' to (row=67, col=7)."""
    col_str = "".join(c for c in addr if c.isalpha())
    row_str = "".join(c for c in addr if c.isdigit())
    return int(row_str), _col_idx(col_str)


def _insert_rows_preserving_merges(ws, insert_row: int, amount: int) -> None:
    """
    openpyxl's insert_rows() shifts cell values down but leaves merged-cell
    ranges at their original row numbers — a stale merge left behind at the
    insertion point (e.g. a section-header row) silently swallows any data
    later written into whichever row now occupies that position. Unmerging
    *after* insert_rows() also fails (the merge's cached cell objects no
    longer match reality), so affected ranges must be unmerged first,
    inserted around, then re-merged at their shifted position.
    """
    affected = [
        (mr.min_row, mr.min_col, mr.max_row, mr.max_col)
        for mr in ws.merged_cells.ranges if mr.min_row >= insert_row
    ]
    for min_row, min_col, max_row, max_col in affected:
        ws.unmerge_cells(start_row=min_row, start_column=min_col,
                          end_row=max_row, end_column=max_col)

    ws.insert_rows(insert_row, amount)

    for min_row, min_col, max_row, max_col in affected:
        ws.merge_cells(start_row=min_row + amount, start_column=min_col,
                        end_row=max_row + amount, end_column=max_col)


def _copy_row_format(ws, src_row: int, dst_row: int, col_count: int) -> None:
    """Copy cell formatting (borders, fill, font, alignment) from one row to another."""
    src_dim = ws.row_dimensions.get(src_row)
    if src_dim and src_dim.height:
        ws.row_dimensions[dst_row].height = src_dim.height
    for col in range(1, col_count + 1):
        src = ws.cell(row=src_row, column=col)
        dst = ws.cell(row=dst_row, column=col)
        if src.has_style:
            dst.font         = copy(src.font)
            dst.border       = copy(src.border)
            dst.fill         = copy(src.fill)
            dst.number_format = src.number_format
            dst.alignment    = copy(src.alignment)


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

    sheet_name = _azn["sheet_name"]
    try:
        wb = openpyxl.load_workbook(out_path, keep_vba=False)
        ws = wb[sheet_name]
    except KeyError as e:
        raise ValueError(
            f'AZN Template {template_path} has no sheet named "{sheet_name}". '
            f"Is this the right template?"
        ) from e
    except Exception as e:
        raise ValueError(f"Could not read AZN Template {template_path}: {e}") from e

    # ── Header cells ──────────────────────────────────────────────────────────
    ws[_azn["cell_ctr_ref"]] = f"CTR-26-{job_ref} AZN"
    ws[_azn["cell_job_ref"]] = int(job_ref)

    if header:
        if header.get("client"):      ws[_azn["cell_client"]]     = header["client"]
        if header.get("sub_client"):  ws[_azn["cell_sub_client"]] = header["sub_client"]
        if header.get("location"):    ws[_azn["cell_location"]]   = header["location"]
        if header.get("date"):        ws[_azn["cell_date"]]       = _parse_date(header["date"])
        if header.get("contract_no"): ws[_azn["cell_contract_no"]] = header["contract_no"]
        if header.get("revision") not in (None, ""):
            ws[_azn["cell_revision"]] = header["revision"]
        if header.get("scope"):       ws[_azn["cell_scope"]]      = header["scope"]

    # ── Insert extra rows before writing so overflow doesn't clobber the
    #    offshore section or the summary block ──────────────────────────────────
    _COLS = 9   # columns A–I used by data rows

    onshore_capacity = _ONSHORE_DATA_END - _ONSHORE_DATA_START + 1
    onshore_extra    = max(0, len(onshore_rows) - onshore_capacity)
    if onshore_extra:
        _insert_rows_preserving_merges(ws, _ONSHORE_DATA_END + 1, onshore_extra)
        for i in range(onshore_extra):
            _copy_row_format(ws, _ONSHORE_DATA_END, _ONSHORE_DATA_END + 1 + i, _COLS)

    # Offshore section has shifted down by onshore_extra
    off_start = _OFFSHORE_DATA_START + onshore_extra
    off_end   = _OFFSHORE_DATA_END   + onshore_extra

    offshore_capacity = off_end - off_start + 1
    offshore_extra    = max(0, len(offshore_rows) - offshore_capacity)
    if offshore_extra:
        _insert_rows_preserving_merges(ws, off_end + 1, offshore_extra)
        for i in range(offshore_extra):
            _copy_row_format(ws, off_end, off_end + 1 + i, _COLS)
    off_eff_end = off_end + offshore_extra

    # Total row-shift to apply to summary cell addresses
    _row_shift = onshore_extra + offshore_extra

    # ── Write section helper (captures ws via closure) ─────────────────────────
    def _write_section(rows, start, eff_end, total_gap) -> int:
        """Writes one section's rows and its total; returns the total row number."""
        clear_end = max(eff_end, start + len(rows) - 1) if rows else eff_end
        for row in range(start, clear_end + 1):
            for col in range(1, _COLS + 1):
                ws.cell(row=row, column=col).value = None

        for i, lr in enumerate(rows):
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

            ws.cell(row=r, column=1).value = str(lr.get("comment", ""))
            ws.cell(row=r, column=2).value = num_emp
            ws.cell(row=r, column=3).value = str(lr.get("description", ""))
            ws.cell(row=r, column=4).value = qty
            ws.cell(row=r, column=5).value = str(lr.get("uom", "Hours"))
            ws.cell(row=r, column=6).value = rate
            ws.cell(row=r, column=7).value = f"=B{r}*D{r}*F{r}"
            ws.cell(row=r, column=8).value = f"=G{r}"
            ws.cell(row=r, column=9).value = str(lr.get("nationality", "NAT"))

        # The total always sits total_gap rows after the section's full
        # capacity block (eff_end), not after however many rows were
        # actually written — otherwise a request with fewer rows than the
        # template's capacity writes the total into a blank filler row
        # instead of the fixed "Total Project Support"/"Total Offshore" row.
        # Summing the whole capacity range (not just the written rows) is
        # safe — the cleared rows above are blank and contribute 0.
        total_row = eff_end + total_gap + 1
        ws.cell(row=total_row, column=7).value = f"=SUM(G{start}:G{eff_end})"
        return total_row

    onshore_total_row  = _write_section(
        onshore_rows,  _ONSHORE_DATA_START, _ONSHORE_DATA_END + onshore_extra,  _ONSHORE_TOTAL_GAP)
    offshore_total_row = _write_section(
        offshore_rows, off_start,           off_eff_end,                        _OFFSHORE_TOTAL_GAP)

    # ── Summary cells — addresses from config, shifted by any inserted rows ────
    _r, _c = _cell_row_col(_azn["cell_summary_onshore"])
    onshore_summary_addr = f"{_col_letter(_c)}{_r + _row_shift}"
    ws.cell(row=_r + _row_shift, column=_c).value = f"=G{onshore_total_row}"

    _r, _c = _cell_row_col(_azn["cell_summary_offshore"])
    offshore_summary_addr = f"{_col_letter(_c)}{_r + _row_shift}"
    ws.cell(row=_r + _row_shift, column=_c).value = f"=G{offshore_total_row}"

    _r, _c = _cell_row_col(_azn["cell_summary_combined"])
    ws.cell(row=_r + _row_shift, column=_c).value = f"={onshore_summary_addr}+{offshore_summary_addr}"

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
