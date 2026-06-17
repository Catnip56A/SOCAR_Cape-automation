"""
sheet_parser.py — Rigid parser for SOCAR Cape MR and CTR documents.

Primary method: Excel named ranges
-----------------------------------
CTR (Pricing sheet):
    equip_start / equip_end   →  Plant & Equipment table
    cons_start  / cons_end    →  Consumables & Materials table

MR (CH-* / NCH-* sheets, sheet-local ranges):
    cons_start / cons_end     →  Consumables table per sheet

Named ranges mark the top-left (header row, first col) and
bottom-right (last data row, last col) of each table rectangle.

Fallback: sheet-based keyword detection (for files without named ranges).

Public API
----------
    from sheet_parser import parse_workbook

    tables = parse_workbook("file.xlsm")
    # → list of {"source_file", "source_sheet", "table_name", "data": DataFrame}
"""

import re
import pandas as pd
from pathlib import Path
from io import BytesIO
import openpyxl
from openpyxl.utils import column_index_from_string


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SKIP_SHEETS: set = set()  # no automatic exclusions — user picks sheets in the UI


def _norm(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    return str(v).strip()


def _is_blank(v) -> bool:
    """True for empty, placeholder, or any numeric zero (0, 0.0, 0.00 …)."""
    s = _norm(v)
    if s in ("", "-", "AA"):
        return True
    try:
        return float(s) == 0.0
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# MR column mapping
# ---------------------------------------------------------------------------

_MR_HEADER_ROW = 8
_MR_PLACEHOLDER = {"STOCKCODE", "STOCKCODE *", "-", "0", "AA", "", "NAN"}


def _clean_mr_col(name: str) -> str | None:
    s = re.sub(r"\*", "", str(name)).strip()
    s = re.sub(r"\s+", " ", s)
    sl = s.lower()

    if s.startswith("Unnamed") or s == "":
        return None
    if "stockcode" in sl:
        return "Stock Code"
    if sl == "№":
        return "No"
    if sl.startswith("qty"):
        return "Qty"
    if sl.startswith("unit"):
        return "Unit"
    if "item specification" in sl or "item spec" in sl:
        return "Description"
    if "required delivery date" in sl or "delivery date" in sl:
        return "Delivery Date"
    if "planned return date" in sl or "return date" in sl:
        return "Return Date"
    if "rechargeable" in sl:
        return "Rechargeable"
    if "allocation" in sl:
        return "Allocation"
    return s


def _deduplicate(names: list) -> list:
    seen: dict = {}
    out = []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n} ({seen[n]})" if n is not None else None)
        else:
            seen[n] = 0
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# CTR Pricing column mapping
# ---------------------------------------------------------------------------

_PRICING_HEADER_KW = ["quantity", "qty", "unit", "rate", "total", "days"]


def _clean_pricing_col(name: str) -> str | None:
    s = _norm(name)
    if not s or s.lower().startswith("unnamed"):
        return None
    sl = s.lower()
    if sl in ("general equipment", "description"):
        return "Description"
    if sl in ("quantity", "qty"):
        return "Quantity"
    if sl == "unit":
        return "Unit"
    if "rate" in sl or "unit price" in sl:
        return "Rate"
    if sl == "days":
        return "Days"
    if sl == "total" and "sc" not in sl:
        return "Total"
    if "equipmentcode" in sl.replace(" ", "") or "productcode" in sl.replace(" ", ""):
        return "Stock Code"
    if "total sc" in sl:
        return None
    return s


# ---------------------------------------------------------------------------
# Stock code filters (shared)
# ---------------------------------------------------------------------------

_BAD_CODES = {"", "nan", "-", "0", "none"}


def _filter_stock_codes(df: pd.DataFrame) -> pd.DataFrame:
    """Drop CTR rows with missing/blank Stock Code."""
    if "Stock Code" not in df.columns:
        return df
    return df[
        df["Stock Code"].apply(
            lambda x: _norm(x).lower() not in _BAD_CODES and bool(_norm(x))
        )
    ].reset_index(drop=True)


def _filter_mr_stock_codes(df: pd.DataFrame) -> pd.DataFrame:
    """Drop MR rows with placeholder or counter-only Stock Code."""
    if "Stock Code" not in df.columns:
        return df
    df = df.copy()
    df["Stock Code"] = df["Stock Code"].astype(str).str.strip()
    df = df[~df["Stock Code"].str.upper().isin(_MR_PLACEHOLDER)]
    df = df[~df["Stock Code"].str.match(r"^\d{1,3}$")]
    df = df[~df["Stock Code"].str.contains(":", na=False)]
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Named-range based parsing (primary method)
# ---------------------------------------------------------------------------

def _collect_named_range_refs(wb) -> dict:
    """
    Scan all workbook defined names (global and sheet-local) and group by
    the sheet they reference.
    Returns {sheet_name: {range_name: (row, col)}}
    """
    result: dict = {}
    try:
        name_list = list(wb.defined_names)
    except Exception:
        return result

    for name in name_list:
        try:
            defn = wb.defined_names[name]
            for sheet_title, cell_ref in defn.destinations:
                sheet_title = sheet_title.strip("'")
                ref = cell_ref.replace("$", "")
                m = re.match(r"([A-Z]+)(\d+)", ref)
                if m:
                    col = column_index_from_string(m.group(1))
                    row = int(m.group(2))
                    if sheet_title not in result:
                        result[sheet_title] = {}
                    result[sheet_title][name] = (row, col)
        except Exception:
            continue
    return result


def _extract_ws_range(ws, sr: int, sc: int, er: int, ec: int) -> list:
    """Return all cell values in the rectangular range (1-based, inclusive)."""
    rows = []
    for row in ws.iter_rows(
        min_row=sr, max_row=er, min_col=sc, max_col=ec, values_only=True
    ):
        rows.append(list(row))
    return rows


def _rows_to_df(rows: list, col_mapper, section: str) -> pd.DataFrame:
    """
    Convert raw rows where rows[0] is the header row into a clean DataFrame.
    col_mapper: callable(str) -> str | None
    """
    if len(rows) < 2:
        return pd.DataFrame()

    col_names = [col_mapper(_norm(h)) for h in rows[0]]

    data_rows = []
    for row in rows[1:]:
        if all(_is_blank(v) for v in row):
            continue
        row_dict = {"_Section": section}
        for col, val in zip(col_names, row):
            if col is not None and col not in row_dict:
                row_dict[col] = val
        data_rows.append(row_dict)

    return pd.DataFrame(data_rows) if data_rows else pd.DataFrame()


def _parse_named_ranges(wb, display_name: str) -> list:
    """
    Extract tables from named ranges equip_start/equip_end and cons_start/cons_end.
    Returns list of result dicts compatible with parse_workbook output.

    All non-skipped sheets that carry named ranges are parsed; the caller
    (Streamlit multiselect) decides which sheets to include in the comparison.
    """
    sheet_ranges = _collect_named_range_refs(wb)
    results = []

    # Iterate in workbook sheet order
    for sheet_name in wb.sheetnames:
        ranges = sheet_ranges.get(sheet_name)
        if not ranges:
            continue
        if sheet_name.strip().lower() in _SKIP_SHEETS:
            continue

        ws = wb[sheet_name]

        # CTR: Plant & Equipment section
        if "equip_start" in ranges and "equip_end" in ranges:
            sr, sc = ranges["equip_start"]
            er, ec = ranges["equip_end"]
            rows = _extract_ws_range(ws, sr, sc, er, ec)
            df = _rows_to_df(rows, _clean_pricing_col, "Plant & Equipment")
            df = _filter_stock_codes(df)
            if not df.empty:
                df["_SourceSheet"] = sheet_name
                df["_SourceFile"] = display_name
                results.append({
                    "source_file":  display_name,
                    "source_sheet": sheet_name,
                    "table_name":   "Plant & Equipment",
                    "data":         df,
                })

        # MR or CTR: Consumables section
        if "cons_start" in ranges and "cons_end" in ranges:
            sr, sc = ranges["cons_start"]
            er, ec = ranges["cons_end"]
            rows = _extract_ws_range(ws, sr, sc, er, ec)
            if rows:
                header_text = "".join(
                    _norm(h).lower().replace(" ", "") for h in rows[0] if _norm(h)
                )
                is_mr = "stockcode" in header_text

                if is_mr:
                    df = _rows_to_df(rows, _clean_mr_col, sheet_name)
                    df = _filter_mr_stock_codes(df)
                    table_name = sheet_name
                else:
                    df = _rows_to_df(rows, _clean_pricing_col, "Consumables & Materials")
                    df = _filter_stock_codes(df)
                    table_name = "Consumables & Materials"

                if not df.empty:
                    df["_SourceSheet"] = sheet_name
                    df["_SourceFile"] = display_name
                    results.append({
                        "source_file":  display_name,
                        "source_sheet": sheet_name,
                        "table_name":   table_name,
                        "data":         df,
                    })

    return results


# ---------------------------------------------------------------------------
# Fallback: sheet-based detection (for files without named ranges)
# ---------------------------------------------------------------------------

def _is_pricing_header(vals: list) -> bool:
    """Row has no counter in col 0 and contains 3+ pricing keywords."""
    if _norm(vals[0] if vals else ""):
        return False
    text = " ".join(_norm(v).lower() for v in vals if _norm(v))
    return sum(1 for kw in _PRICING_HEADER_KW if kw in text) >= 3


def _parse_ctr_pricing_sheet(file, sheet_name: str, source_file: str) -> pd.DataFrame:
    """Fallback CTR parser: keyword-based header detection."""
    raw = pd.read_excel(file, sheet_name=sheet_name, header=None)

    header_rows = [i for i in range(len(raw)) if _is_pricing_header(list(raw.iloc[i]))]
    if not header_rows:
        return pd.DataFrame()

    all_parts = []
    _skip_label_kw = ["total", "rechargeable", "mark up", "gm (", "client:", "location:", "scope:"]

    for idx, h in enumerate(header_rows):
        end = header_rows[idx + 1] if idx + 1 < len(header_rows) else len(raw)

        section = sheet_name
        for lb in range(h - 1, max(h - 6, -1), -1):
            above = list(raw.iloc[lb])
            col0 = _norm(above[0]) if above else ""
            col1 = _norm(above[1]) if len(above) > 1 else ""
            if col0 == "" and col1 and col1.lower() not in ("", "nan", "aa"):
                if not any(kw in col1.lower() for kw in _skip_label_kw):
                    section = col1
                    break

        col_names = [_clean_pricing_col(_norm(v)) for v in raw.iloc[h]]

        rows = []
        for i in range(h + 1, end):
            row_vals = list(raw.iloc[i])
            try:
                counter = float(row_vals[0])
                if pd.isna(counter):
                    continue
            except (TypeError, ValueError):
                continue
            col1 = _norm(row_vals[1]) if len(row_vals) > 1 else ""
            if not col1 or col1 == "0":
                continue
            row_dict = {"_Section": section}
            for col, val in zip(col_names, row_vals):
                if col is not None and col not in row_dict:
                    row_dict[col] = val
            rows.append(row_dict)

        if rows:
            all_parts.append(pd.DataFrame(rows))

    if not all_parts:
        return pd.DataFrame()

    result = pd.concat(all_parts, ignore_index=True, sort=False)
    result["_SourceSheet"] = sheet_name
    result["_SourceFile"] = source_file
    return _filter_stock_codes(result)


def _parse_mr_sheet(file, sheet_name: str, source_file: str) -> pd.DataFrame:
    """Fallback MR parser: header always at row index 8."""
    df = pd.read_excel(file, sheet_name=sheet_name, header=_MR_HEADER_ROW)

    keep_idx, keep_names = [], []
    for i, c in enumerate(df.columns):
        name = _clean_mr_col(str(c))
        if name is not None:
            keep_idx.append(i)
            keep_names.append(name)

    df = df.iloc[:, keep_idx].copy()
    df.columns = _deduplicate(keep_names)

    if "Stock Code" not in df.columns:
        return pd.DataFrame()

    df = _filter_mr_stock_codes(df)
    df["_SourceSheet"] = sheet_name
    df["_SourceFile"] = source_file
    return df


def _detect_format(file, sheet_name: str) -> str | None:
    try:
        probe = pd.read_excel(file, sheet_name=sheet_name, header=_MR_HEADER_ROW, nrows=0)
        if any("STOCKCODE" in str(c).upper() for c in probe.columns):
            return "mr"
    except Exception:
        pass
    try:
        raw10 = pd.read_excel(file, sheet_name=sheet_name, header=None, nrows=10)
        for i in range(len(raw10)):
            text = " ".join(_norm(v).lower() for v in raw10.iloc[i] if _norm(v))
            if "pricing schedule" in text:
                return "ctr_pricing"
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_workbook(path, sheet_names=None, filename=None) -> list:
    """
    Parse one workbook and return all detected tables.

    Parameters
    ----------
    path        : str, Path, or BytesIO
    sheet_names : list[str] or None  — if None, all non-admin sheets are tried
    filename    : str or None        — display name when path is BytesIO

    Returns
    -------
    list of dicts:
        {"source_file": str, "source_sheet": str, "table_name": str,
         "data": pd.DataFrame}
    """
    if isinstance(path, BytesIO):
        display_name = filename or "uploaded_file"
        raw_bytes = path.getvalue()
    else:
        path = Path(path)
        display_name = path.name
        raw_bytes = None

    def _make_file():
        return BytesIO(raw_bytes) if raw_bytes is not None else path

    # ---- Method 1: Named-range based (primary) ----
    try:
        wb = openpyxl.load_workbook(
            BytesIO(raw_bytes) if raw_bytes is not None else str(path),
            read_only=True, data_only=True
        )
        results = _parse_named_ranges(wb, display_name)
        wb.close()
        if results:
            return results
    except Exception:
        pass

    # ---- Method 2: Sheet-based keyword detection (fallback) ----
    file_ref = _make_file()
    xls = pd.ExcelFile(file_ref)
    all_sheets = xls.sheet_names

    if sheet_names is None:
        sheet_names = [s for s in all_sheets if s.strip().lower() not in _SKIP_SHEETS]

    results = []
    for sn in sheet_names:
        if sn not in all_sheets:
            continue

        fmt = _detect_format(_make_file(), sn)
        if fmt is None:
            continue

        try:
            if fmt == "mr":
                df = _parse_mr_sheet(_make_file(), sn, display_name)
            else:
                df = _parse_ctr_pricing_sheet(_make_file(), sn, display_name)
        except Exception:
            continue

        if df.empty:
            continue

        results.append({
            "source_file":  display_name,
            "source_sheet": sn,
            "table_name":   sn,
            "data":         df,
        })


    return results


# ---------------------------------------------------------------------------
# CLI  —  python sheet_parser.py file.xlsm [SHEET ...]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python sheet_parser.py FILE.xlsx [SHEET ...]")
        sys.exit(1)

    tables = parse_workbook(sys.argv[1], sys.argv[2:] or None)
    print(f"\nFound {len(tables)} table(s):\n")
    for t in tables:
        df = t["data"]
        print(f"  [{t['table_name']}]  {len(df)} rows × {len(df.columns)} cols")
        print(f"    Columns : {list(df.columns)}")
        print(f"    First row: {df.iloc[0].tolist() if len(df) else '(empty)'}\n")
