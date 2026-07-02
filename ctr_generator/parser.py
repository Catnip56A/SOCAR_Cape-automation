"""
ctr_generator/parser.py

Reads the source files for CTR generation:
  - AZN pricebook  (4410030127_*.xlsx)
  - USD pricebook  (4410030190_*.xlsx)
  - SAGE export    (FROM_SAGE_*.xlsm)
  - CTR Request    (<job>_*.xlsm)

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
and "ctr_request" sections.

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
_req = CFG["ctr_request"]

_PRICEBOOK_SHEET   = _pb["sheet_name"]
_PRICEBOOK_SKIPROWS = _pb["skip_rows"]
_SAGE_SHEET        = _sg["sheet_name"]
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


def parse_usd_pricebook(src) -> pd.DataFrame:
    """
    Returns DataFrame with columns:
      stock_code | uom | supplier_desc | unit_price (float)

    Filtered to rows where product_type == 'SERVICE' and uom == 'DAY'.
    """
    df = _read_pricebook_raw(src, "USD Pricebook")
    df = df.rename(columns={
        _pb["col_stock_code"]:    "stock_code",
        _pb["col_product_type"]:  "product_type",
        _pb["col_uom"]:           "uom",
        _pb["col_supplier_desc"]: "supplier_desc",
        _pb["col_unit_price"]:    "unit_price",
    })
    df = df[["stock_code", "product_type", "uom", "supplier_desc", "unit_price"]].copy()
    df["stock_code"]    = df["stock_code"].fillna("").str.strip()
    df["product_type"]  = df["product_type"].fillna("").str.strip().str.upper()
    df["uom"]           = df["uom"].fillna("").str.strip().str.upper()
    df["supplier_desc"] = df["supplier_desc"].fillna("").str.strip().str.title()
    df["unit_price"]    = pd.to_numeric(df["unit_price"], errors="coerce").fillna(0.0)

    mask = (df["product_type"] == "SERVICE") & (df["uom"] == "DAY")
    df = df[mask].drop(columns=["product_type"]).copy()
    if df.empty:
        raise ValueError(
            f"{_display_name(src)} opened, but no SERVICE/DAY rows were found "
            f"after parsing. Is this the right USD Pricebook "
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
      - product does NOT start with 'S'
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

    mask = ~df["product"].str.upper().str.startswith("S")
    df = df[mask][["product", "long_description", "local_expect_cost", "unit_code"]].copy()
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# Equipment names database
# ─────────────────────────────────────────────────────────────────────────────

def parse_names_db(src) -> pd.DataFrame:
    """
    Reads an equipment names / stock-code lookup file (e.g. CTR_NAMES_DB.xlsx).

    Expected columns (row 0 = header row):
      product          — stock / product code used in CTR Requests
      long_description — canonical equipment name

    Extra columns are ignored so the file can grow new columns without
    requiring code changes.

    Returns DataFrame with columns: product | long_description
    Raises ValueError with an actionable message on any read failure.
    """
    try:
        df = pd.read_excel(_open(src), header=0, dtype=str)
    except Exception as e:
        raise _friendly_open_error(e, src, "Equipment Names DB", "(any sheet)") from e

    df.columns = [c.strip() for c in df.columns]

    required = {"product", "long_description"}
    missing = required - set(df.columns)
    if missing:
        found = ", ".join(df.columns[:10]) + ("…" if len(df.columns) > 10 else "")
        raise ValueError(
            f"{_display_name(src)} is missing expected column(s): "
            f"{', '.join(sorted(missing))}. Found columns: {found}. "
            f"Is this the right Equipment Names DB?"
        )

    df["product"]          = df["product"].fillna("").str.strip()
    df["long_description"] = df["long_description"].fillna("").str.strip()
    df = df[df["product"] != ""][["product", "long_description"]].copy()

    if df.empty:
        raise ValueError(
            f"{_display_name(src)} opened but contained no rows after filtering "
            f"empty product codes. Is this the right Equipment Names DB?"
        )
    return df.reset_index(drop=True)


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

    # The line-item table runs from data_start_row while col 1 (№) is a
    # sequential integer; below it (e.g. row 164 onward) sits an unrelated
    # logistics/extras block that must not be parsed as line items.
    manpower_rows, equipment_rows, consumable_rows = [], [], []
    r = _req["data_start_row"]
    while isinstance(ws.cell(row=r, column=1).value, (int, float)):
        manpower_desc = _v(r, _req["manpower_desc_col"])
        if manpower_desc:
            manpower_rows.append({
                "description":   manpower_desc,
                "quantity":      _v(r, _req["manpower_qty_col"]),
                "working_days":  _v(r, _req["manpower_working_days_col"]),
                "shift":         _v(r, _req["manpower_shift_col"]),
                "weekend":       _v(r, _req["manpower_weekend_col"]),
                "weekend_shift": _v(r, _req["manpower_weekend_shift_col"]),
            })

        equip_desc = _v(r, _req["equip_desc_col"])
        if equip_desc:
            equipment_rows.append({
                "stock_code":  _v(r, _req["equip_stock_code_col"]),
                "description": equip_desc,
                "uom":         _v(r, _req["equip_uom_col"]),
                "quantity":    _v(r, _req["equip_qty_col"]),
                "days":        _v(r, _req["equip_days_col"]),
            })

        cons_desc = _v(r, _req["cons_desc_col"])
        if cons_desc:
            consumable_rows.append({
                "stock_code":  _v(r, _req["cons_stock_code_col"]),
                "description": cons_desc,
                "uom":         _v(r, _req["cons_uom_col"]),
                "quantity":    _v(r, _req["cons_qty_col"]),
            })

        r += 1

    result["manpower_rows"]   = manpower_rows
    result["equipment_rows"]  = equipment_rows
    result["consumable_rows"] = consumable_rows
    wb.close()
    return result
