"""
ctr_tools/parser.py

Reads the source files for CTR generation:
  - AZN pricebook  (4410030127_*.xlsx)                — manpower rates
  - USD pricebook  (4410030190_*.xlsx-style)           — equipment rental rates
  - SAGE export    (dedicated file, e.g. "FROM SAGE *.xlsm") — consumables reference catalog
  - Combined DB    (CTR_NAMES_DB & CTR_REQUEST.xlsx)   — Equipment Names DB bridge
                                                          + CTR Request, in one file

AZN and USD pricebooks share one layout (verified from 4410030127):
  Rows 0–6 are header / guidance metadata.
  Data starts at row 7 (0-indexed).
  Column indices (0-based, matching Excel A=0):
    4  (E) = Part Number Extension  → stock_code  e.g. "MSU-NAT-OFF-12"
    5  (F) = Product Type           → product_type e.g. "SERVICE"
    6  (G) = UOM                    → uom          e.g. "HUR" or "DAY"
    9  (J) = Supplier Description   → supplier_desc e.g. "ROPE ACCESS SUPERVISOR"
    14 (O) = Unit Price             → unit_price
  On the USD pricebook, stock_code is NOT unique per equipment item — see
  parse_usd_pricebook.

These indices (and sheet names / skip rows) are configurable via
ctr_tools/template_config.json — see the "pricebook", "sage_export",
"names_db", and "ctr_request" sections.

SAGE export layout:
  Sheet "FROM SAGE" (an .xlsm export; configurable via sage_export.sheet_name)
    — consumables reference export, columns product | long_description |
    local_expect_cost | unit_code (+ extras). Used only for consumables
    matching — equipment now prices off the USD pricebook instead (see
    above).

Combined DB layout:
  Sheet "CTR_NAMES_DB_USD" — bridges a CTR Request equipment stock code to
    the canonical name used to look it up in the USD pricebook by
    description, for request-form codes that don't correspond 1:1 to a USD
    pricebook item (e.g. fleet items where several serials share one
    generic day rate):
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
    O6  = Comments (free text, merged O6:Q8)
    Row 12 = column headers for the combined line-item table.
    Rows 13+ = data rows, three independent blocks side by side:
      cols 1–7   Manpower      (№, Manpower, Quantity, Normal Working Days, Shift, Weekend, Shift)
      cols 8–12  Plant & Equipment (Stock Code, Name, UOM, Quantity, Duration Days)
      cols 13–16 Consumables   (Stock Code, Name, UOM, Quantity)
    Empty cells are stored as the literal string '-'.

  Beside the line-item table (v1.0.9 form: columns U:AA; older forms:
  below it) sits an extras block — "Additional Information" (Floatel /
  Flight tickets / Accommodation / Per Diem / Meal / Training, each
  "Required" or "Not Required"), "Type of Scaffold System" (a scaffold
  system and its tonnage), and "TRANSPORT" (type / quantity / duration
  per vehicle). It is read by _parse_request_extras, which locates each
  part by its own label text rather than by fixed cell — see there.

Every entry point below wraps file/sheet access so a missing sheet, locked
file, or corrupt workbook surfaces as one actionable sentence instead of a
raw pandas/openpyxl traceback — see _friendly_open_error.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import openpyxl
import pandas as pd

from ctr_tools.config import CFG

_pb     = CFG["pricebook"]
_usd_pb = CFG["usd_pricebook"]
_sg     = CFG["sage_export"]
_nm     = CFG["names_db"]
_req    = CFG["ctr_request"]
_xtr    = CFG["ctr_request_extras"]
_scf    = CFG["scaffold"]

_SAGE_SHEET     = _sg["sheet_name"]
_NAMES_DB_SHEET = _nm["sheet_name"]
_REQUEST_SHEET  = _req["sheet_name"]


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

def _read_pricebook_raw(src, file_kind: str, cfg: dict) -> pd.DataFrame:
    """Return a DataFrame with integer column names, data only (header skipped)."""
    sheet     = cfg["sheet_name"]
    skip_rows = cfg["skip_rows"]
    try:
        df = pd.read_excel(
            _open(src),
            sheet_name=sheet,
            header=None,
            skiprows=skip_rows,
            dtype=str,
        )
    except Exception as e:
        raise _friendly_open_error(e, src, file_kind, sheet) from e

    min_cols = cfg["col_unit_price"] + 1
    if df.shape[1] < min_cols:
        raise ValueError(
            f"{_display_name(src)} has only {df.shape[1]} column(s) on the "
            f'"{sheet}" sheet after the header rows — expected at '
            f"least {min_cols} (Part Number Extension, Product Type, UOM, Supplier "
            f"Description, Unit Price). Is this the right {file_kind}?"
        )
    return df


def _parse_pricebook(src, file_kind: str, cfg: dict) -> pd.DataFrame:
    """
    Returns DataFrame with columns:
      stock_code | uom | supplier_desc | unit_price (float)

    Sheet name, header skip rows, and column positions all come from `cfg`
    (the "pricebook" or "usd_pricebook" section of template_config.json —
    see ctr_tools/config.py), so a template layout change only needs a
    config edit, not a code change. No filtering — caller decides which
    rows to use.
    """
    df = _read_pricebook_raw(src, file_kind, cfg)
    df = df.rename(columns={
        cfg["col_stock_code"]:    "stock_code",
        cfg["col_uom"]:           "uom",
        cfg["col_supplier_desc"]: "supplier_desc",
        cfg["col_unit_price"]:    "unit_price",
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
            f"expected column after parsing. Is this the right {file_kind} "
            f'(sheet "{cfg["sheet_name"]}", standard {cfg["skip_rows"]}-row header)?'
        )
    return df.reset_index(drop=True)


def parse_azn_pricebook(src) -> pd.DataFrame:
    """Manpower rates — see _parse_pricebook. stock_code is unique per role/shift."""
    return _parse_pricebook(src, "AZN Pricebook", _pb)


def parse_usd_pricebook(src) -> pd.DataFrame:
    """
    Equipment rental rates — layout configured via the "usd_pricebook"
    section of template_config.json (defaults to the same "Item Details
    and Rates" layout as the AZN pricebook — see _parse_pricebook), but
    stock_code is NOT unique per item here: every equipment line is tagged
    with one of two generic rate-type codes (by convention ending "-NOR" /
    "-STBY", e.g. "EQ-GEN-NOR" / "EQ-GEN-STBY" for normal vs. standby
    rate). Callers must match equipment by supplier_desc instead,
    preferring the "-NOR" row when both exist for the same description —
    see _match_usd_equipment in window.py.
    """
    return _parse_pricebook(src, "USD Pricebook", _usd_pb)


# ─────────────────────────────────────────────────────────────────────────────
# SAGE export
# ─────────────────────────────────────────────────────────────────────────────

def parse_sage(src) -> pd.DataFrame:
    """
    Reads the FROM SAGE sheet and returns consumable rows:
      long_description | local_expect_cost (float) | unit_code | product |
      non_recharge (bool, derived from analysis_b)

    Used as a reference catalog for matching against CTR Request consumable
    line items by stock code or description — rows are not excluded based
    on analysis_b, since a customer can reference any SAGE-classified item
    (equipment rentals, misc charges, etc.) as a consumable line; the flag
    is instead surfaced via non_recharge so the caller can zero the price
    (mirroring equipment's NONRECHARG treatment) rather than dropping the
    row.

    non_recharge is True when analysis_b (case-insensitive, exact match)
    is one of sage_export.non_recharge_tags in template_config.json —
    default ["NONRECHARG", "NONRECHAR"]; edit that list, not this code, if
    a differently-worded tag shows up in a future export.

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

    required = {"product", "local_expect_cost", "long_description", "unit_code", "analysis_b"}
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

    non_recharge_tags = {t.strip().upper() for t in _sg.get("non_recharge_tags", [])}
    df["non_recharge"] = df["analysis_b"].fillna("").str.strip().str.upper().isin(non_recharge_tags)

    mask = df["product"].str.upper() != "S0000000000"
    df = df[mask][["product", "long_description", "local_expect_cost", "unit_code", "non_recharge"]].copy()
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# Equipment names database (stock code → canonical SAGE lookup name)
# ─────────────────────────────────────────────────────────────────────────────

def parse_equip_names_db(src) -> pd.DataFrame:
    """
    Reads the "CTR_NAMES_DB_USD" sheet of the Combined DB workbook.

    Columns are read by position (configurable via the "names_db" section of
    template_config.json — col_product / col_legacy_name / col_canonical_name),
    since the header labels on this sheet aren't standardized:
      product / stock code
      legacy name (CTR_CREATOR_LEGACY naming)
      canonical pricebook name, or the literal "NON-RECHARGEABLE"

    Returns DataFrame with columns:
      product | legacy_name | canonical_name | non_recharge (bool)

    The canonical name is looked up in the USD Pricebook by description
    (see _match_usd_equipment in window.py) — this sheet only resolves a
    request stock code to that name, it doesn't carry the price itself.
    non_recharge rows are shown using legacy_name instead, at a forced rate
    of 0.
    """
    try:
        df = pd.read_excel(
            _open(src), sheet_name=_NAMES_DB_SHEET, header=0, dtype=str,
        )
    except Exception as e:
        raise _friendly_open_error(e, src, "Equipment Names DB", _NAMES_DB_SHEET) from e

    _cols = (_nm["col_product"], _nm["col_legacy_name"], _nm["col_canonical_name"])
    if df.shape[1] < max(_cols):
        raise ValueError(
            f'{_display_name(src)} has only {df.shape[1]} column(s) on the '
            f'"{_NAMES_DB_SHEET}" sheet — expected at least {max(_cols)} (stock '
            f"code, legacy name, canonical pricebook name). Is this the right "
            f"Combined DB file?"
        )

    df = df.iloc[:, [c - 1 for c in _cols]].copy()
    df.columns = ["product", "legacy_name", "canonical_name"]
    df["product"]         = df["product"].fillna("").str.strip()
    df["legacy_name"]     = df["legacy_name"].fillna("").str.strip()
    df["canonical_name"]  = df["canonical_name"].fillna("").str.strip()
    df["non_recharge"]    = df["canonical_name"].str.upper() == "NON-RECHARGEABLE"
    df.loc[df["non_recharge"], "canonical_name"] = ""
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

def _norm_label(value) -> str:
    """Cell text reduced to a comparable label: trimmed, single-spaced, lowercase."""
    if value is None:
        return ""
    return " ".join(str(value).split()).lower()


def _as_number(value):
    """Cell value as a float, or None if it isn't a plain number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", "."))
        except ValueError:
            return None
    return None


def _parse_request_extras(ws) -> dict:
    """
    Reads the request form's "Additional Information / Type of Scaffold
    System / TRANSPORT" block, which sits beside (v1.0.9 form: columns
    U:AA, rows 3-29) or below (older forms: columns B:P under the line
    items) the main line-item table.

    Returns:
      additional_info : [{"label", "value", "required"}] — the
          Floatel / Flight tickets / Accomodation / Per Diem / Meal /
          Training list, with `required` true only for a cell reading
          exactly "Required" (its sibling option is the literal "Not
          Required", which a substring test would also match).
      transport_rows  : [{"transport_type", "quantity", "duration"}]
      scaffold        : {"system", "uom", "tonnage"} or None

    Nothing here is read from a fixed cell. Each part is found by
    searching the sheet for its own label text and then reading relative
    to that cell, because the block has already moved once between form
    revisions — and because a request form from before the block existed
    at all must come back as "no extras" rather than as an error or, worse,
    as whatever happens to sit at those coordinates instead.
    """
    max_row  = _xtr["search_max_row"]
    max_col  = _xtr["search_max_col"]
    span     = _xtr["max_block_rows"]
    streak_limit = _xtr["max_blank_streak"]

    def _cell(row, col):
        if row < 1 or col < 1:
            return None
        return ws.cell(row=row, column=col).value

    def _text(row, col) -> str:
        val = _cell(row, col)
        if val is None:
            return ""
        text = str(val).strip()
        return "" if text == "-" else text

    def _block_row_end(row: int, col: int, default_end: int) -> int:
        """
        Last row belonging to a block whose heading cell is vertically
        merged over it (the v1.0.9 form merges "TRANSPORT" down its own
        rows, U21:U29). That merge is the form's own statement of how far
        the block runs, so it beats the generic row-span/blank-streak
        guess — a stray value pasted just below the block can otherwise be
        read as one more row of it. Falls back to `default_end` when the
        heading isn't merged downward.
        """
        for merged in ws.merged_cells.ranges:
            if (merged.min_row <= row <= merged.max_row
                    and merged.min_col <= col <= merged.max_col
                    and merged.max_row > row):
                return min(merged.max_row, default_end)
        return default_end

    def _find(label: str):
        """First (row, col) whose text equals `label`; None if absent."""
        target = _norm_label(label)
        if not target:
            return None
        for row in range(1, max_row + 1):
            for col in range(1, max_col + 1):
                if _norm_label(_cell(row, col)) == target:
                    return row, col
        return None

    info_at      = _find(_xtr["label_additional_info"])
    transport_at = _find(_xtr["label_transport"])

    result = {"additional_info": [], "transport_rows": [], "scaffold": None}
    if info_at is None and transport_at is None:
        return result   # request form predates the block, or it was deleted

    # ── Additional Information: label / value pairs down the two columns
    #    starting at the heading itself. ────────────────────────────────────
    if info_at is not None:
        row0, col0 = info_at
        options = {_norm_label(v) for v in _xtr["option_values"]}
        required_option = _norm_label(_xtr["required_value"])
        blanks = 0
        for row in range(row0 + 1, _block_row_end(row0, col0, row0 + span) + 1):
            label = _text(row, col0)
            if not label:
                blanks += 1
                if blanks >= streak_limit:
                    break
                continue
            blanks = 0
            value = _text(row, col0 + 1)
            # A line belongs to this list only if its value cell holds one
            # of the form's two options — the same column carries unrelated
            # entries further down (Contingency, the scaffold system), and
            # they'd otherwise be read as Additional Information items.
            if _norm_label(value) not in options:
                continue
            result["additional_info"].append({
                "label":    label,
                "value":    value,
                "required": _norm_label(value) == required_option,
            })

    # ── TRANSPORT: the heading's own row also carries the Type/Quantity/
    #    Duration column headings to its right; fall back to the three
    #    columns immediately after it if they've been reworded. ────────────
    if transport_at is not None:
        row0, col0 = transport_at
        wanted = {
            "transport_type": _xtr["label_transport_type"],
            "quantity":       _xtr["label_transport_qty"],
            "duration":       _xtr["label_transport_duration"],
        }
        cols = {}
        for col in range(col0, col0 + max(4, span)):
            head = _norm_label(_cell(row0, col))
            for key, label in wanted.items():
                if key not in cols and head == _norm_label(label):
                    cols[key] = col
        for offset, key in enumerate(("transport_type", "quantity", "duration"), start=1):
            cols.setdefault(key, col0 + offset)

        blanks = 0
        for row in range(row0 + 1, _block_row_end(row0, col0, row0 + span) + 1):
            transport_type = _text(row, cols["transport_type"])
            if not transport_type:
                blanks += 1
                if blanks >= streak_limit:
                    break
                continue
            blanks = 0
            result["transport_rows"].append({
                "transport_type": transport_type,
                "quantity":       _text(row, cols["quantity"]),
                "duration":       _text(row, cols["duration"]),
            })

    # ── Scaffold: any cell in the block naming a scaffold system, other
    #    than the block's own "Type of Scaffold System" heading. The
    #    tonnage is the first number in the handful of cells to its right,
    #    and the unit is the text cell just before that number ("...|FM|
    #    TON|66.96"). Searching only the block's own bounding box keeps a
    #    manpower row reading "SCAFFOLDER" out of the results. ────────────
    anchors  = [a for a in (info_at, transport_at) if a is not None]
    row_from = min(a[0] for a in anchors)
    row_to   = min(max(a[0] for a in anchors) + span, max_row)
    col_from = min(a[1] for a in anchors)
    col_to   = min(max(a[1] for a in anchors) + span, max_col)

    keyword     = _norm_label(_xtr["scaffold_keyword"])
    heading     = _norm_label(_xtr["label_scaffold_header"])
    value_span  = _xtr["scaffold_value_span"]
    for row in range(row_from, row_to + 1):
        for col in range(col_from, col_to + 1):
            label = _norm_label(_cell(row, col))
            if not label or keyword not in label or label == heading:
                continue
            tonnage, uom = None, ""
            for step in range(1, value_span + 1):
                number = _as_number(_cell(row, col + step))
                if number is not None:
                    tonnage = number
                    uom = _text(row, col + step - 1) if step > 1 else ""
                    break
            if tonnage is None:
                continue   # the block is present but this scaffold line is unfilled
            result["scaffold"] = {
                "system":  _text(row, col),
                "uom":     uom or _scf.get("default_uom", "TON"),
                "tonnage": tonnage,
            }
            break
        if result["scaffold"]:
            break

    return result


def parse_ctr_request(src) -> dict:
    """
    Reads the REQUEST sheet of a CTR Request workbook (e.g. *.xlsm).

    Returns a dict with header fields:
      requester, client, date_of_survey, job_id_ref, surveyor,
      project_type, location, commencement_date, job_description, comments

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
        # The form's free-text Comments box, merged O6:Q8 — openpyxl reads
        # a merged range's value off its top-left cell, so O6 is the value.
        "comments":          _v(_req["row_comments"],           _req["col_comments"]),
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
    result.update(_parse_request_extras(ws))
    wb.close()
    return result
