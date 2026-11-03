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
    from comparator.sheet_parser import parse_workbook

    tables = parse_workbook("file.xlsm")
    # → list of {"source_file", "source_sheet", "table_name", "data": DataFrame}
"""

import logging
import re
import pandas as pd
from pathlib import Path
from io import BytesIO
import openpyxl
from comparator import column_keywords as CK

log = logging.getLogger(__name__)
from openpyxl.utils import column_index_from_string, get_column_letter


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SKIP_SHEETS: set = {"socar-cape"}  # admin/index sheet in MR workbooks, not comparison data


def is_cover_sheet(sheet_name: str) -> bool:
    """True for an MR's Socar-Cape cover sheet — same table structure as the
    CH-*/NCH-* pages but repeats the whole request, so it's opt-in
    (parse_workbook's include_cover) rather than parsed by default."""
    return str(sheet_name).strip().lower() in _SKIP_SHEETS


def _norm(v) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):                       # NaN, and NaT (pandas' empty cell in a date-formatted column)
            return ""
    except (TypeError, ValueError):          # a list-like is never "missing"
        pass
    return str(v).strip()


_REF_RE = re.compile(r"(?<![A-Za-z0-9])CTR[\s\-_]?(\d{2})[\s\-_]?(\d{2,4})(?!\d)", re.I)


def find_ctr_ref(texts) -> str | None:
    """The first CTR number (CTR-26-319) in a list of texts, normalised, or None."""
    for t in texts:
        m = _REF_RE.search(str(t)) if t is not None else None
        if m:
            return f"CTR-{m.group(1)}-{m.group(2)}"
    return None


def _ref_of_ws(ws) -> str | None:
    """The CTR number written near the top of an openpyxl sheet (an MR page: next to "Date of request"; a CTR: "Ref:")."""
    try:
        cells = (c for row in ws.iter_rows(min_row=1, max_row=12, max_col=16, values_only=True) for c in row)
        return find_ctr_ref(cells)
    except Exception:
        return None


def _ref_of_sheet(file, sheet_name: str) -> str | None:
    try:
        top = pd.read_excel(file, sheet_name=sheet_name, header=None, nrows=12)
    except Exception:
        return None
    return find_ctr_ref(v for v in top.to_numpy().ravel() if isinstance(v, str))


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

_MR_PLACEHOLDER = {"STOCKCODE", "STOCKCODE *", "-", "0", "AA", "", "NAN"}


def _clean_mr_col(name: str) -> str | None:
    """An MR header -> its standard column name (the words are the user's, see column_keywords.py); a header that
    means nothing known keeps its own text."""
    s = re.sub(r"\*", "", str(name)).strip()
    s = re.sub(r"\s+", " ", s)
    if s.startswith("Unnamed") or s == "":
        return None
    concept, _word = CK.match(CK.current(), "mr", s)
    return concept or s


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

_CORE_CONCEPTS = ("Quantity", "Unit", "Rate", "Total", "Days")      # a header row shows several of these


def _clean_pricing_col(name: str) -> str | None:
    """A CTR header -> its standard column name (the words are the user's, see column_keywords.py); a header that
    means nothing known keeps its own text; the generator's internal cost columns are dropped."""
    s = _norm(name)
    if not s or s.lower().startswith("unnamed"):
        return None
    concept, _word = CK.match(CK.current(), "ctr", s)
    if concept == CK.IGNORE:
        return None
    return concept or s


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
    df["Stock Code"] = df["Stock Code"].apply(_norm)
    df = df[~df["Stock Code"].str.upper().isin(_MR_PLACEHOLDER)]
    df = df[~df["Stock Code"].str.match(r"^\d{1,3}$")]
    df = df[~df["Stock Code"].str.contains(":", na=False)]
    return df.reset_index(drop=True)


# A Stock Code looks like SP2300006500 / EQ0900000000 / PP8201000009: letters, then mostly digits, no spaces.
_CODE_RE = re.compile(r"^(?=.*[A-Za-z])(?=(?:.*\d){6,})[A-Za-z0-9][A-Za-z0-9\-_.]{5,}$")


def _looks_like_code(v) -> bool:
    s = _norm(v)
    return bool(s) and s.lower() not in _BAD_CODES and bool(_CODE_RE.match(s))


def _note(level: str, text: str) -> dict:
    """A message about a parsed table: level "warn" (something may have been missed — look at it)
    or "info" (expected, for the record)."""
    return {"level": level, "text": text}


def _infer_code_col(data: list, col_names: list, known_col: int | None):
    """The index of an unnamed column whose cells look like Stock Codes (a CTR whose header row has no label
    above them), or None. `known_col` = where another table of the same sheet keeps its codes: preferred."""
    lines = [r for r in data if any(not _is_blank(v) for v in r)]
    if not lines:
        return None
    best, best_ratio = None, 0.0
    for k, name in enumerate(col_names):
        if name is not None:
            continue
        n = sum(1 for r in lines if k < len(r) and _looks_like_code(r[k]))
        ratio = n / len(lines)
        if k == known_col and n >= 1 and ratio >= 0.3:
            return k
        if n >= 1 and ratio >= 0.5 and ratio > best_ratio:
            best, best_ratio = k, ratio
    return best


def _pricing_block(rows: list, section: str, known_col: int | None = None,
                   drop_if_no_col: bool = False) -> tuple[pd.DataFrame, list, int | None]:
    """One CTR pricing table (rows[0] = its header row) -> (the lines that can be compared, notes, the column the
    Stock Codes were found in). A Stock Code column without a header is found by what its cells look like; a line
    that cannot be compared is never dropped silently: it is named in a note."""
    notes: list = []
    col_names = [_clean_pricing_col(_norm(h)) for h in rows[0]]
    cfg = CK.current()
    for k, h in enumerate(rows[0]):
        concept, word = CK.match(cfg, "ctr", _norm(h))
        if concept and concept != CK.IGNORE and CK.is_variant("ctr", concept, word):
            own = "" if CK.is_shipped_word("ctr", concept, word) else f" (your word “{word.lstrip('=')}”)"
            notes.append(_note("info", f"{section}: the header “{_norm(h)}” (column {get_column_letter(k + 1)}) "
                                       f"was read as {concept}{own}."))
    seen_concepts: dict[str, int] = {}
    for k, name in enumerate(col_names):
        if name in ("Description", "Quantity", "Unit", "Rate", "Days", "Total", "Stock Code"):
            if name in seen_concepts:
                notes.append(_note("warn", f"{section}: columns {get_column_letter(seen_concepts[name] + 1)} and "
                                           f"{get_column_letter(k + 1)} are both read as {name}; only the first "
                                           f"({get_column_letter(seen_concepts[name] + 1)}) is used."))
            else:
                seen_concepts[name] = k
    data = [list(r) for r in rows[1:] if not all(_is_blank(v) for v in r)]
    code_idx = col_names.index("Stock Code") if "Stock Code" in col_names else None
    if code_idx is None and data:
        k = _infer_code_col(data, col_names, known_col)
        if k is not None:
            col_names[k] = "Stock Code"
            code_idx = k
            found = [r[k] for r in data if k < len(r) and _looks_like_code(r[k])]
            notes.append(_note("warn", f"{section}: the Stock Code column has no header — read from column "
                                       f"{get_column_letter(k + 1)} ({len(found)} code(s) found, e.g. {_norm(found[0])})."))
    unknown = [_norm(h) for h, name in zip(rows[0], col_names) if _norm(h) and name == _norm(h)]   # kept as typed: not a standard column
    for need in ("Quantity", "Description"):
        if need not in col_names and data:
            guess = (f" The headers not read as any standard column: {', '.join('“' + u + '”' for u in unknown[:6])} — "
                     f"if one of them is the {need} column, add its exact spelling (for example “={unknown[0].lower()}”)"
                     f" under {need}." if unknown else "")
            notes.append(_note("warn", f"{section}: no {need} column was recognised in the header row "
                                       f"({', '.join(_norm(h) for h in rows[0] if _norm(h))[:120]}).{guess} "
                                       "See Settings › Column keywords."))
    records = []
    for r in data:
        d = {"_Section": section}
        for col, val in zip(col_names, r):
            if col is not None and col not in d:
                d[col] = val
        records.append(d)
    if not records:
        return pd.DataFrame(), notes, code_idx
    df = pd.DataFrame(records)
    if "Stock Code" not in df.columns and not drop_if_no_col:
        return df, notes, code_idx                       # no codes anywhere in this sheet: nothing to filter by
    ok = (df["Stock Code"].apply(lambda x: _norm(x).lower() not in _BAD_CODES and bool(_norm(x)))
          if "Stock Code" in df.columns else pd.Series(False, index=df.index))
    dropped = [i for i in df.index if not ok[i]]
    if dropped:
        plain, odd = [], []
        for i in dropped:
            desc = _norm(df.at[i, "Description"]) if "Description" in df.columns else ""
            desc = desc or next((_norm(v) for v in data[i] if _norm(v) and not _is_blank(v)), f"line {i + 1}")
            hit = next(((k2, v) for k2, v in enumerate(data[i]) if k2 != code_idx and _looks_like_code(v)), None)
            if hit:
                odd.append(f"{desc} (looks like a code in column {get_column_letter(hit[0] + 1)}: {_norm(hit[1])})")
            else:
                plain.append(desc)
        if odd:
            notes.append(_note("warn", f"{section}: {len(odd)} line(s) NOT compared — a value that looks like a Stock Code "
                                       f"is in a column that is not read as one: " + "; ".join(odd[:8])))
        if plain:
            more = f" … and {len(plain) - 8} more" if len(plain) > 8 else ""
            notes.append(_note("info", f"{section}: {len(plain)} line(s) without a Stock Code are not compared: "
                                       + "; ".join(plain[:8]) + more))
    kept = df[ok].reset_index(drop=True)
    if notes:
        notes.insert(0, _note("info", f"{section}: {len(df)} line(s) found, {len(kept)} compared."))
    return kept, notes, code_idx


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
        log.debug("Could not read workbook defined names", exc_info=True)
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
            log.debug("Could not resolve defined name %r", name, exc_info=True)
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


def _parse_named_ranges(wb, display_name: str, include_cover: bool = False) -> list:
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
        if is_cover_sheet(sheet_name) and not include_cover:
            continue

        ws = wb[sheet_name]
        if ws.sheet_state != "visible":
            # Sheet is hidden or veryHidden in the workbook (e.g. unused CH-*/
            # NCH-* template pages in an MR) — not a page the user actually
            # sees or fills in, even though it still carries named ranges.
            continue

        # CTR: Plant & Equipment section
        if "equip_start" in ranges and "equip_end" in ranges:
            sr, sc = ranges["equip_start"]
            er, ec = ranges["equip_end"]
            rows = _extract_ws_range(ws, sr, sc, er, ec)
            df, notes, _ = _pricing_block(rows, "Plant & Equipment") if len(rows) >= 2 else (pd.DataFrame(), [], None)
            if not df.empty:
                df["_SourceSheet"] = sheet_name
                df["_SourceFile"] = display_name
                results.append({
                    "source_file":  display_name,
                    "source_sheet": sheet_name,
                    "table_name":   "Plant & Equipment",
                    "data":         df,
                    "notes":        notes,
                    "ref":          _ref_of_ws(ws) or find_ctr_ref([display_name]),
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
                notes: list = []

                if is_mr:
                    df = _rows_to_df(rows, _clean_mr_col, sheet_name)
                    df = _filter_mr_stock_codes(df)
                    table_name = sheet_name
                else:
                    df, notes, _ = (_pricing_block(rows, "Consumables & Materials") if len(rows) >= 2
                                    else (pd.DataFrame(), [], None))
                    table_name = "Consumables & Materials"

                if not df.empty:
                    df["_SourceSheet"] = sheet_name
                    df["_SourceFile"] = display_name
                    results.append({
                        "source_file":  display_name,
                        "source_sheet": sheet_name,
                        "table_name":   table_name,
                        "data":         df,
                        "notes":        notes,
                        "ref":          _ref_of_ws(ws) or (None if is_mr else find_ctr_ref([display_name])),
                    })

    return results


# ---------------------------------------------------------------------------
# Fallback: sheet-based detection (for files without named ranges)
# ---------------------------------------------------------------------------

def _row_concepts(vals: list) -> set:
    cfg = CK.current()
    return {c for c in (CK.match(cfg, "ctr", _norm(v))[0] for v in vals if _norm(v)) if c}


def _is_pricing_header(vals: list, relaxed: bool = False) -> bool:
    """Row has no counter in col 0 and shows 3+ of the pricing columns (Quantity, Unit, Rate, Total, Days) by the
    words in Settings. `relaxed` (the row sits right under a section title such as "Consumables & Materials")
    accepts 2 plus "Description": a header with one column renamed is still a header."""
    if _norm(vals[0] if vals else ""):
        return False
    found = _row_concepts(vals)
    hits = len(found & set(_CORE_CONCEPTS))
    if hits >= 3:
        return True
    return relaxed and hits >= 2 and "Description" in found


def _is_title_row(vals: list) -> bool:
    """A section title: nothing in column A, text in column B, (almost) nothing else — "Consumables & Materials"."""
    if len(vals) < 2 or _norm(vals[0]) or not _norm(vals[1]):
        return False
    return sum(1 for v in vals[2:] if _norm(v) and _norm(v).lower() != "aa") <= 1


def _parse_ctr_pricing_sheet(file, sheet_name: str, source_file: str) -> tuple[pd.DataFrame, list]:
    """Fallback CTR parser: keyword-based header detection. Returns (the lines that can be compared, notes about
    what could not be: see _pricing_block)."""
    raw = pd.read_excel(file, sheet_name=sheet_name, header=None)

    def _under_title(i: int) -> bool:
        return any(_is_title_row(list(raw.iloc[j])) for j in range(max(i - 2, 0), i))

    header_rows = [i for i in range(len(raw))
                   if _is_pricing_header(list(raw.iloc[i]), relaxed=_under_title(i))]

    # Header rows set in Settings (Excel row numbers) replace the search for that table; the other table is still found.
    cfg = CK.current()
    forced: dict[int, str] = {}
    pre_notes: list = []
    for key, name in (("ctr_equipment", "Plant & Equipment"), ("ctr_consumables", "Consumables & Materials")):
        row = CK.header_row(cfg, key)
        if not row:
            continue
        if row > len(raw):
            pre_notes.append(_note("warn", f"The {name} header row set in Settings ({row}) is below the end of the "
                                           f"sheet ({len(raw)} rows) — it was not used."))
            continue
        forced[row - 1] = name
        found = _row_concepts(list(raw.iloc[row - 1]))
        if len(found & set(_CORE_CONCEPTS)) < 2:
            texts = [_norm(v) for v in list(raw.iloc[row - 1]) if _norm(v)]
            pre_notes.append(_note("warn", f"Row {row} (the {name} header row set in Settings) has no recognisable "
                                           f"column names ({', '.join(texts[:8]) or 'it is empty'}) — check the row number."))
    if forced:
        eq = next((r for r, n in forced.items() if n.startswith("Plant")), None)
        co = next((r for r, n in forced.items() if n.startswith("Consum")), None)
        if eq is not None and co is not None:
            header_rows = sorted(forced)
        elif eq is not None:
            header_rows = [eq] + [h for h in header_rows if h > eq]
        else:
            header_rows = [h for h in header_rows if h < co] + [co]
    if not header_rows:
        return pd.DataFrame(), pre_notes

    # A row that looks like a table header (text in several columns, directly above numbered lines) but was not
    # recognised: those lines would be read with the columns of the table above. Said, never silent.
    suspects = []
    for i in range(len(raw) - 1):
        if i in header_rows or _norm(raw.iat[i, 0]):
            continue
        texts = [_norm(v) for v in list(raw.iloc[i])[1:12] if _norm(v) and not isinstance(v, (int, float))]
        nxt = list(raw.iloc[i + 1])
        try:
            numbered = not pd.isna(float(nxt[0]))
        except (TypeError, ValueError):
            numbered = False
        if len(texts) >= 3 and numbered and len(texts) == sum(1 for v in list(raw.iloc[i])[1:12] if _norm(v)):
            suspects.append((i, texts))

    _skip_label_kw = ["total", "rechargeable", "mark up", "gm (", "client:", "location:", "scope:"]

    blocks = []                                       # (section, [header row, line, line …])
    for idx, h in enumerate(header_rows):
        end = header_rows[idx + 1] if idx + 1 < len(header_rows) else len(raw)

        section = forced.get(h, sheet_name)
        for lb in (range(h - 1, max(h - 6, -1), -1) if h not in forced else ()):
            above = list(raw.iloc[lb])
            col0 = _norm(above[0]) if above else ""
            col1 = _norm(above[1]) if len(above) > 1 else ""
            if col0 == "" and col1 and col1.lower() not in ("", "nan", "aa"):
                if not any(kw in col1.lower() for kw in _skip_label_kw):
                    section = col1
                    break

        lines = [list(raw.iloc[h])]
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
            lines.append(row_vals)
        blocks.append((section, lines))

    # The column the codes sit in at the table that does label it: where to look in a table that does not.
    labelled = [[_clean_pricing_col(_norm(v)) for v in lines[0]] for _s, lines in blocks]
    known = next((n.index("Stock Code") for n in labelled if "Stock Code" in n), None)
    any_code_col = known is not None or any(
        _infer_code_col([list(r) for r in lines[1:]], names, None) is not None
        for (_s, lines), names in zip(blocks, labelled) if len(lines) > 1)

    parts, notes = [], list(pre_notes)
    first_h = header_rows[0]
    for i, texts in suspects:
        if i > first_h:
            notes.append(_note("warn", f"Row {i + 1} ({', '.join(texts[:6])}) looks like a table header but was not "
                                       "recognised — the lines under it were read with the columns of the table "
                                       "above, so their figures may be in the wrong columns. Use the standard header "
                                       "names (Description, Quantity, Unit, Unit Price / Rate, Total)."))
    for section, lines in blocks:
        if len(lines) < 2:
            continue
        df, n, _k = _pricing_block(lines, section, known, drop_if_no_col=any_code_col)
        notes += n
        if not df.empty:
            parts.append(df)
    if not parts:
        return pd.DataFrame(), notes

    result = pd.concat(parts, ignore_index=True, sort=False)
    result["_SourceSheet"] = sheet_name
    result["_SourceFile"] = source_file
    return result, notes


def _mr_header_row0() -> int:
    """The MR header row as pandas wants it (0-based): the one set in Settings, else Excel row 9."""
    return (CK.header_row(CK.current(), "mr") or CK.SHIPPED_ROWS["mr"]) - 1


def _parse_mr_sheet(file, sheet_name: str, source_file: str) -> pd.DataFrame:
    """Fallback MR parser: the header is at the row set in Settings (Excel row 9 unless changed)."""
    df = pd.read_excel(file, sheet_name=sheet_name, header=_mr_header_row0())

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
        probe = pd.read_excel(file, sheet_name=sheet_name, header=_mr_header_row0(), nrows=0)
        if any(CK.match(CK.current(), "mr", str(c))[0] == "Stock Code" for c in probe.columns):
            return "mr"
    except Exception:
        log.debug("MR-format probe failed for sheet %r", sheet_name, exc_info=True)
    try:
        raw10 = pd.read_excel(file, sheet_name=sheet_name, header=None, nrows=10)
        for i in range(len(raw10)):
            text = " ".join(_norm(v).lower() for v in raw10.iloc[i] if _norm(v))
            if "pricing schedule" in text:
                return "ctr_pricing"
    except Exception:
        log.debug("CTR-pricing-format probe failed for sheet %r", sheet_name, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _stamped(results: list) -> list:
    """Every table remembers which Column keywords it was read with ("kw"): see column_keywords.fingerprint."""
    fp = CK.fingerprint()
    for t in results:
        t["kw"] = fp
    return results


def parse_workbook(path, sheet_names=None, filename=None, include_cover=False) -> list:
    """
    Parse one workbook and return all detected tables.

    Parameters
    ----------
    path        : str, Path, or BytesIO
    sheet_names : list[str] or None  — if None, all non-admin sheets are tried
    filename    : str or None        — display name when path is BytesIO
    include_cover : bool             — also parse the MR "Socar-Cape" cover sheet
                    (skipped by default; the caller decides whether to use it)

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
    visible_sheets: set | None = None
    try:
        wb = openpyxl.load_workbook(
            BytesIO(raw_bytes) if raw_bytes is not None else str(path),
            read_only=True, data_only=True
        )
        visible_sheets = {s for s in wb.sheetnames if wb[s].sheet_state == "visible"}
        results = _parse_named_ranges(wb, display_name, include_cover)
        wb.close()
        if results:
            return _stamped(results)
    except Exception:
        log.debug(
            "Named-range parsing failed for %r, falling back to sheet-based "
            "keyword detection", display_name, exc_info=True,
        )

    # ---- Method 2: Sheet-based keyword detection (fallback) ----
    file_ref = _make_file()
    try:
        xls = pd.ExcelFile(file_ref)
    except FileNotFoundError:
        raise ValueError(f"File not found: {display_name}")
    except PermissionError as e:
        raise ValueError(
            f"Cannot open {display_name} — it may be open in Excel in another "
            f"window. Close it there and try again."
        ) from e
    except Exception as e:
        msg = str(e).lower()
        if "format cannot be determined" in msg or "not a zip file" in msg:
            raise ValueError(
                f"{display_name} doesn't look like a valid Excel file — it may "
                f"be corrupted or saved in an unsupported format."
            ) from e
        raise ValueError(f"Could not read {display_name}: {e}") from e
    all_sheets = xls.sheet_names
    if visible_sheets is not None:
        # Hidden/veryHidden sheets (e.g. unused CH-*/NCH-* template pages in
        # an MR) aren't pages the user actually sees or fills in — same
        # reasoning as the sheet_state check in _parse_named_ranges, applied
        # here too since this fallback runs whenever a file has no named
        # ranges at all (cons_start/cons_end, equip_start/equip_end).
        all_sheets = [s for s in all_sheets if s in visible_sheets]

    if sheet_names is None:
        sheet_names = [s for s in all_sheets
                       if include_cover or not is_cover_sheet(s)]

    results = []
    for sn in sheet_names:
        if sn not in all_sheets:
            continue

        fmt = _detect_format(_make_file(), sn)
        if fmt is None:
            continue

        try:
            notes = []
            if fmt == "mr":
                df = _parse_mr_sheet(_make_file(), sn, display_name)
            else:
                df, notes = _parse_ctr_pricing_sheet(_make_file(), sn, display_name)
        except Exception:
            # Unlike the format-detection probes above, this drops a sheet
            # that *did* look parseable — worth a real warning, since it's
            # otherwise a silent, undiagnosable gap in the parsed results.
            log.warning(
                "Failed to parse sheet %r (detected as %r) in %r — "
                "skipping it", sn, fmt, display_name, exc_info=True,
            )
            continue

        if df.empty:
            continue

        results.append({
            "source_file":  display_name,
            "source_sheet": sn,
            "table_name":   sn,
            "data":         df,
            "notes":        notes,
            "ref":          _ref_of_sheet(_make_file(), sn) or (find_ctr_ref([display_name]) if fmt != "mr" else None),
        })


    return _stamped(results)


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
