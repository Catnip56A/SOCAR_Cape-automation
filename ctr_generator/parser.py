"""
ctr_generator/parser.py

Reads the source files for CTR generation:
  - AZN pricebook  (4410030127_*.xlsx)                        — manpower rates
  - Combined DB    (CTR_NAMES_DB & CTR_REQUEST.xlsx)           — SAGE, equipment
                                                                  names bridge,
                                                                  and CTR Request,
                                                                  all in one file

Pricebook layout (verified from 4410030127):
  Rows 0–6 are header / guidance metadata.
  Data starts at row 7 (0-indexed).
  Column indices (0-based, matching Excel A=0):
    4  (E) = Part Number Extension  → stock_code  e.g. "MSU-NAT-OFF-12"
    5  (F) = Product Type           → product_type e.g. "SERVICE"
    6  (G) = UOM                    → uom          e.g. "HUR" or "DAY"
    9  (J) = Supplier Description   → supplier_desc e.g. "ROPE ACCESS SUPERVISOR"
    14 (O) = Unit Price             → unit_price

These indices (and sheet names / skip rows) are configurable via
ctr_generator/template_config.json — see the "pricebook", "sage_export",
"names_db", and "ctr_request" sections.

Combined DB layout:
  Sheet "SAGE" — unchanged consumables/equipment rate export, columns
    product | long_description | local_expect_cost | unit_code (+ extras).
    Equipment rates now live here too (keyed by stock code), not in a
    separate USD Pricebook file.

  Sheet "CTR_NAMES_DB_USD" — bridges a CTR Request equipment stock code to
    the canonical name used to look it up in SAGE by description, for the
    codes that aren't themselves present as a SAGE product code (e.g. fleet
    items where several serials share one generic day rate):
      col A = product / stock code
      col B = legacy name (CTR_CREATOR_LEGACY naming)
      col C = canonical pricebook name, or the literal "NON-RECHARGEABLE"
              tag for items that are never billed to the client — those are
              always shown using the column B name at a rate of 0.

CTR Request layout (verified from 102290776 sample):
  Sheet "REQUEST":
    C5  = Name of Requester        J5  = Client / Contract №
    C6  = Date of Survey           J6  = Job ID / Reference
    C7  = Surveyor                 J7  = Project Type (Offshore/Onshore)
    C8  = Asset / Site             J8  = Estimated Project Commencement Date
    C9  = Job Description (Scope)
    Row 12 = column headers for the combined line-item table.
    Rows 13+ = data rows, three independent blocks side by side:
      cols 1–7   Manpower      (№, Manpower, Quantity, Normal Working Days, Shift, Weekend, Shift)
      cols 8–12  Plant & Equipment (Stock Code, Name, UOM, Quantity, Duration Days)
      cols 13–16 Consumables   (Stock Code, Name, UOM, Quantity)
    Empty cells are stored as the literal string '-'.

Every entry point below wraps file/sheet access so a missing sheet, locked
file, or corrupt workbook surfaces as one actionable sentence instead of a
raw pandas/openpyxl traceback — see _friendly_open_error.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import openpyxl
import pandas as pd

from ctr_generator.config import CFG

_pb  = CFG["pricebook"]
_sg  = CFG["sage_export"]
_nm  = CFG["names_db"]
_req = CFG["ctr_request"]

_PRICEBOOK_SHEET   = _pb["sheet_name"]
_PRICEBOOK_SKIPROWS = _pb["skip_rows"]
_SAGE_SHEET        = _sg["sheet_name"]
_NAMES_DB_SHEET    = _nm["sheet_name"]
_REQUEST_SHEET     = _req["sheet_name"]


def _open(src) -> BytesIO | Path:
    if isinstance(src, (bytes, bytearray)):
        return BytesIO(src)
    return Path(src)


def _display_name(src) -> str:
    return str(src) if isinstance(src, (str, Path)) else "the selected file"


def _friendly_open_error(exc: Exception, src, file_kind: str, expected_sheet: str) -> ValueError:
    """
    Translates a raw exception from pd.read_excel / openpyxl.load_workbook into
    one actionable sentence naming the file and what was expected, instead of
    a raw pandas/openpyxl traceback the user has no way to act on.
    """
    name = _display_name(src)
    msg = str(exc)
    msg_lower = msg.lower()

    if isinstance(exc, FileNotFoundError):
        return ValueError(f"{file_kind} not found: {name}")
    if isinstance(exc, PermissionError):
        return ValueError(
            f"Cannot open {name} — it may be open in Excel in another window. "
            f"Close it there and try again."
        )
    if isinstance(exc, (ValueError, KeyError)) and "sheet" in msg_lower:
        return ValueError(
            f'Could not find a sheet named "{expected_sheet}" in {name}. '
            f"Is this the right {file_kind}?"
        )
    if "format cannot be determined" in msg_lower or "not a zip file" in msg_lower:
        return ValueError(
            f"{name} doesn't look like a valid Excel file — it may be corrupted "
            f"or saved in an unsupported format. Try re-saving it from Excel and "
            f"loading it again."
        )
    return ValueError(f"Could not read {name} as a {file_kind}: {msg}")


# ─────────────────────────────────────────────────────────────────────────────
# Pricebook helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read_pricebook_raw(src, file_kind: str) -> pd.DataFrame:
    """Return a DataFrame with integer column names, data only (header skipped)."""
    try:
        df = pd.read_excel(
            _open(src),
            sheet_name=_PRICEBOOK_SHEET,
            header=None,
            skiprows=_PRICEBOOK_SKIPROWS,
            dtype=str,
        )
    except Exception as e:
        raise _friendly_open_error(e, src, file_kind, _PRICEBOOK_SHEET) from e

    min_cols = _pb["col_unit_price"] + 1
    if df.shape[1] < min_cols:
        raise ValueError(
            f"{_display_name(src)} has only {df.shape[1]} column(s) on the "
            f'"{_PRICEBOOK_SHEET}" sheet after the header rows — expected at '
            f"least {min_cols} (Part Number Extension, Product Type, UOM, Supplier "
            f"Description, Unit Price). Is this the right {file_kind}?"
        )
    return df


def parse_azn_pricebook(src) -> pd.DataFrame:
    """
    Returns DataFrame with columns:
      stock_code | uom | supplier_desc | unit_price (float)

    No filtering — caller decides which rows to use.
    """
    df = _read_pricebook_raw(src, "AZN Pricebook")
    df = df.rename(columns={
        _pb["col_stock_code"]:   "stock_code",
        _pb["col_uom"]:          "uom",
        _pb["col_supplier_desc"]: "supplier_desc",
        _pb["col_unit_price"]:   "unit_price",
    })
    df = df[["stock_code", "uom", "supplier_desc", "unit_price"]].copy()
    df["stock_code"]    = df["stock_code"].fillna("").str.strip()
    df["uom"]           = df["uom"].fillna("").str.strip()
    df["supplier_desc"] = df["supplier_desc"].fillna("").str.strip().str.title()
    df["unit_price"]    = pd.to_numeric(df["unit_price"], errors="coerce").fillna(0.0)
    df = df[df["stock_code"] != ""]
    if df.empty:
        raise ValueError(
            f"{_display_name(src)} opened, but no rows had a stock code in the "
            f"expected column after parsing. Is this the right AZN Pricebook "
            f'(sheet "{_PRICEBOOK_SHEET}", standard {_PRICEBOOK_SKIPROWS}-row header)?'
        )
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# SAGE export
# ─────────────────────────────────────────────────────────────────────────────

def parse_sage(src) -> pd.DataFrame:
    """
    Reads the FROM SAGE sheet and returns consumable rows:
      long_description | local_expect_cost (float) | unit_code | product

    Used as a reference catalog for matching against CTR Request consumable
    line items by stock code or description — not filtered by analysis_b
    (e.g. NONRECHARG), since a customer can reference any SAGE-classified
    item (equipment rentals, misc charges, etc.) as a consumable line.

    Filters:
      - excludes the "S0000000000" placeholder code (generic
        "Service Order Description" rows with arbitrary, unmatchable prices —
        not a real stock code). Real stock codes that merely start with "S"
        (e.g. "SP3000054035") are kept; an earlier version of this filter
        excluded any code starting with "S" and silently dropped those too.
    """
    try:
        df = pd.read_excel(
            _open(src),
            sheet_name=_SAGE_SHEET,
            header=0,
            dtype=str,
        )
    except Exception as e:
        raise _friendly_open_error(e, src, "SAGE Export", _SAGE_SHEET) from e

    # Normalise column names (strip whitespace in case of minor variations)
    df.columns = [c.strip() for c in df.columns]

    required = {"product", "local_expect_cost", "long_description", "unit_code"}
    missing = required - set(df.columns)
    if missing:
        found = ", ".join(df.columns[:10]) + ("…" if len(df.columns) > 10 else "")
        raise ValueError(
            f'{_display_name(src)} is missing expected column(s) on the "{_SAGE_SHEET}" '
            f"sheet: {', '.join(sorted(missing))}. Found columns: {found}. "
            f"Is this the right SAGE export?"
        )

    df["product"]           = df["product"].fillna("").str.strip()
    df["long_description"]  = df["long_description"].fillna("").str.strip()
    df["unit_code"]         = df["unit_code"].fillna("EA").str.strip()
    df["local_expect_cost"] = pd.to_numeric(df["local_expect_cost"], errors="coerce").fillna(0.0)

    mask = df["product"].str.upper() != "S0000000000"
    df = df[mask][["product", "long_description", "local_expect_cost", "unit_code"]].copy()
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# Equipment names database (stock code → canonical SAGE lookup name)
# ─────────────────────────────────────────────────────────────────────────────

def parse_equip_names_db(src) -> pd.DataFrame:
    """
    Reads the "CTR_NAMES_DB_USD" sheet of the Combined DB workbook.

    Columns are read by position (configurable via the "names_db" section of
    template_config.json — col_product / col_legacy_name / col_canonical_name
    / col_rate), since the header labels on this sheet aren't standardized:
      product / stock code
      legacy name (CTR_CREATOR_LEGACY naming)
      canonical pricebook name, or the literal "NON-RECHARGEABLE"
      USD rate — authoritative for this stock code, used directly instead
        of looking the canonical name up in SAGE

    Returns DataFrame with columns:
      product | legacy_name | canonical_name | non_recharge (bool) | rate

    non_recharge rows always bill at 0 regardless of the rate column (some
    NON-RECHARGEABLE rows carry a leftover nonzero rate that should be
    ignored) — the caller should use legacy_name for display in that case.
    Rechargeable rows use the rate column directly.
    """
    try:
        df = pd.read_excel(
            _open(src), sheet_name=_NAMES_DB_SHEET, header=0, dtype=str,
        )
    except Exception as e:
        raise _friendly_open_error(e, src, "Equipment Names DB", _NAMES_DB_SHEET) from e

    _cols = (_nm["col_product"], _nm["col_legacy_name"], _nm["col_canonical_name"], _nm["col_rate"])
    if df.shape[1] < max(_cols):
        raise ValueError(
            f'{_display_name(src)} has only {df.shape[1]} column(s) on the '
            f'"{_NAMES_DB_SHEET}" sheet — expected at least {max(_cols)} (stock '
            f"code, legacy name, canonical pricebook name, UOM, USD rate). Is "
            f"this the right Combined DB file?"
        )

    df = df.iloc[:, [c - 1 for c in _cols]].copy()
    df.columns = ["product", "legacy_name", "canonical_name", "rate"]
    df["product"]         = df["product"].fillna("").str.strip()
    df["legacy_name"]     = df["legacy_name"].fillna("").str.strip()
    df["canonical_name"]  = df["canonical_name"].fillna("").str.strip()
    df["non_recharge"]    = df["canonical_name"].str.upper() == "NON-RECHARGEABLE"
    df.loc[df["non_recharge"], "canonical_name"] = ""
    df["rate"] = pd.to_numeric(df["rate"], errors="coerce").fillna(0.0)
    df.loc[df["non_recharge"], "rate"] = 0.0
    df = df[df["product"] != ""].reset_index(drop=True)

    if df.empty:
        raise ValueError(
            f"{_display_name(src)} opened but contained no rows after filtering "
            f'empty stock codes on the "{_NAMES_DB_SHEET}" sheet. Is this the '
            f"right Combined DB file?"
        )
    return df


# ─────────────────────────────────────────────────────────────────────────────
# CTR Request workbook
# ─────────────────────────────────────────────────────────────────────────────

def parse_ctr_request(src) -> dict:
    """
    Reads the REQUEST sheet of a CTR Request workbook (e.g. *.xlsm).

    Returns a dict with header fields:
      requester, client, date_of_survey, job_id_ref, surveyor,
      project_type, location, commencement_date, job_description

    Plus three line-item lists (empty if the request has none filled in):
      manpower_rows, equipment_rows, consumable_rows

    Cells containing the literal '-' are treated as "not requested" and
    excluded from the line-item lists.
    """
    try:
        wb = openpyxl.load_workbook(_open(src), data_only=True)
    except Exception as e:
        raise _friendly_open_error(e, src, "CTR Request", _REQUEST_SHEET) from e

    if _REQUEST_SHEET not in wb.sheetnames:
        sheets = ", ".join(wb.sheetnames)
        wb.close()
        raise ValueError(
            f'Could not find a sheet named "{_REQUEST_SHEET}" in {_display_name(src)}. '
            f"Found sheets: {sheets}. Is this the right CTR Request file?"
        )
    ws = wb[_REQUEST_SHEET]

    def _v(row, col):
        val = ws.cell(row=row, column=col).value
        if val is None:
            return ""
        s = str(val).strip()
        return "" if s == "-" else s

    result = {
        "requester":         _v(_req["row_requester"],         _req["col_requester"]),
        "client":            _v(_req["row_client"],            _req["col_client"]),
        "date_of_survey":    _v(_req["row_date_of_survey"],    _req["col_date_of_survey"]),
        "job_id_ref":        _v(_req["row_job_id_ref"],        _req["col_job_id_ref"]),
        "surveyor":          _v(_req["row_surveyor"],          _req["col_surveyor"]),
        "project_type":      _v(_req["row_project_type"],      _req["col_project_type"]),
        "location":          _v(_req["row_location"],          _req["col_location"]),
        "commencement_date": ws.cell(
            row=_req["row_commencement_date"],
            column=_req["col_commencement_date"],
        ).value,
        "job_description":   _v(_req["row_job_description"],   _req["col_job_description"]),
    }

    # The line-item table runs from data_start_row; below it (e.g. row 164
    # onward in the standard template) sits an unrelated logistics/extras
    # block that must not be parsed as line items.
    #
    # Manpower rows are still gated on col 1 (№) being a sequential integer
    # — that column is specifically the manpower row counter, and the
    # logistics block reuses the manpower description column with text
    # like "Additional Information", so col 1 is the only reliable guard
    # there.
    #
    # Equipment/consumables are gated on their stock-code column instead —
    # a request can have far more equipment/consumable lines than manpower
    # lines (or the requester may not have extended the № column to match
    # when pasting in extra rows), so tying their continuation to col 1
    # would silently truncate the list once № ran out even though real
    # stock codes continue below. A stock code is recognised by containing
    # a digit, which every real code does (e.g. "S0000000000") but the
    # logistics block's column-reused labels ("TRANSPORT") do not.
    def _looks_like_stock_code(value: str) -> bool:
        return bool(value) and any(ch.isdigit() for ch in value)

    manpower_rows, equipment_rows, consumable_rows = [], [], []
    r = _req["data_start_row"]
    blank_streak = 0
    _MAX_BLANK_STREAK = 5    # consecutive empty rows before the table is considered done
    _MAX_ROWS_SCANNED  = 1000  # hard safety cap against a malformed sheet
    scanned = 0
    while blank_streak < _MAX_BLANK_STREAK and scanned < _MAX_ROWS_SCANNED:
        has_row_number = isinstance(ws.cell(row=r, column=1).value, (int, float))

        manpower_desc = _v(r, _req["manpower_desc_col"])
        has_manpower  = has_row_number and bool(manpower_desc)

        equip_code = _v(r, _req["equip_stock_code_col"])
        equip_desc = _v(r, _req["equip_desc_col"])
        has_equip  = _looks_like_stock_code(equip_code) and bool(equip_desc)

        cons_code = _v(r, _req["cons_stock_code_col"])
        cons_desc = _v(r, _req["cons_desc_col"])
        has_cons  = _looks_like_stock_code(cons_code) and bool(cons_desc)

        if not (has_manpower or has_equip or has_cons or has_row_number):
            blank_streak += 1
            r += 1
            scanned += 1
            continue
        blank_streak = 0

        if has_manpower:
            manpower_rows.append({
                "description":    manpower_desc,
                "quantity":       _v(r, _req["manpower_qty_col"]),
                "working_days":   _v(r, _req["manpower_working_days_col"]),
                "shift":          _v(r, _req["manpower_shift_col"]),
                "status":         _v(r, _req["manpower_status_col"]),
                "encoding":       _v(r, _req["manpower_encoding_col"]),
            })

        if has_equip:
            equipment_rows.append({
                "stock_code":     equip_code,
                "description":    equip_desc,
                "rechargability": _v(r, _req["equip_rechargability_col"]),
                "uom":            _v(r, _req["equip_uom_col"]),
                "quantity":       _v(r, _req["equip_qty_col"]),
                "days":           _v(r, _req["equip_days_col"]),
            })

        if has_cons:
            consumable_rows.append({
                "stock_code":     cons_code,
                "description":    cons_desc,
                "uom":            _v(r, _req["cons_uom_col"]),
                "quantity":       _v(r, _req["cons_qty_col"]),
                "rechargability": _v(r, _req["cons_rechargability_col"]),
            })

        r += 1
        scanned += 1

    result["manpower_rows"]   = manpower_rows
    result["equipment_rows"]  = equipment_rows
    result["consumable_rows"] = consumable_rows
    wb.close()
    return result
