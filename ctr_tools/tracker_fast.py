"""
ctr_tools/tracker_fast.py

Fast path for writing CTR Tracker entries.

Placement: a CTR is written into the existing tracker row whose CTR
number column already contains its base number (currency suffix
stripped, e.g. "CTR-26-217 AZN" -> "CTR-26-217" — see
CFG["ctr_tracker"]["value_usd_formula"] for why AZN/USD share one base
number), *and* whose Currency/Revision cells (if not blank) already agree
with the entry being written — see _classify_target_row. Rows are
pre-created by hand; if no matching, still-empty row exists, the CTR is
skipped with a warning instead of guessing a placement. An earlier
version appended to the first blank row at the end of the sheet; that was
replaced because CTR numbers need to land next to the row a human already
reserved for them.

The one exception is a separate revision (revision_mode, always "separate"
from the tracker window) with no spare pre-created row available for that
CTR number, or whose only spare would break ascending revision order: a real row does get
inserted there, but not by this module — see tracker_xlwings.py, which
drives actual Excel to do it (so formula/range references shift exactly
as they would if a person inserted the row by hand) and always runs
*before* this module takes its one read/write snapshot of the sheet.
Every other write path here still never creates or shifts a row.

Performance: this edits only the target sheet's raw XML directly inside
the .xlsm zip and copies every other archive member — other sheets,
vbaProject.bin, drawings, styles, comments, the data-validation
extensions — byte-for-byte untouched, instead of round-tripping the
whole workbook through openpyxl (which has to build a full Python
object for every one of the ~500,000 existing cells across all 6 sheets
just to change a handful — ~11s vs ~3s on the real tracker file — and
silently drops anything it doesn't model, e.g. the sheet's data-
validation dropdown lists). This is the only writer — an earlier
openpyxl-based fallback in tracker.py implemented the now-retired
append-to-end placement and was removed rather than left inconsistent.
"""

from __future__ import annotations

import logging
import re
import zipfile
from datetime import date
from pathlib import Path

from lxml import etree

from ctr_tools.config import CFG
from ctr_tools.tracker import CTREntry, backup_tracker
from ctr_tools import tracker_xlwings

log = logging.getLogger(__name__)

_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_M = f"{{{_NS_MAIN}}}"

_EXCEL_EPOCH = date(1899, 12, 30)

# Stable, greppable marker substring for the "another entry earlier in
# this same batch already claims this exact CTR number + currency" skip
# reason (see _plan_insertions) — callers (tracker_window.py) match on
# this to offer an automatic retry in a follow-up batch, where the same
# entries resolve correctly against the first write's now-real on-disk
# row instead of colliding mid-batch.
IN_BATCH_CONFLICT_MARKER = "already has a pending write earlier in this same batch"

# Matches a trailing " AZN" / " USD" currency tag on a CTR ref, e.g. the
# "CTR-26-217 AZN" a generated CTR file's Ref field carries — the tracker
# itself has no such suffix in its CTR number column (currency lives in
# its own column), and one contract's AZN/USD documents share one row
# reservation, so the suffix is stripped before matching.
_CURRENCY_SUFFIX_RE = re.compile(r"\s+(AZN|USD)\s*$", re.IGNORECASE)


def _normalize_ctr_number(raw: str) -> str:
    return _CURRENCY_SUFFIX_RE.sub("", (raw or "").strip()).strip()


def _excel_serial(d: date) -> int:
    return (d - _EXCEL_EPOCH).days


def _col_letters(cell_ref: str) -> str:
    return re.match(r"[A-Za-z]+", cell_ref).group().upper()


def _col_index(letters: str) -> int:
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx


def _col_default_styles(sheet_root) -> dict[int, str]:
    """Column index -> default style id, from the sheet's <cols> element.
    Most rows beyond the historically hand-edited region of the tracker
    don't carry a per-cell style override, only this column-wide default —
    same as what Excel itself applies to a previously-untouched cell."""
    result: dict[int, str] = {}
    cols_elem = sheet_root.find(f"{_M}cols")
    if cols_elem is None:
        return result
    for col in cols_elem.findall(f"{_M}col"):
        style = col.get("style")
        if style is None:
            continue
        cmin, cmax = int(col.get("min")), int(col.get("max"))
        for i in range(cmin, cmax + 1):
            result[i] = style
    return result


def _style_with_number_format(
    styles_root, current_style: str, format_code: str,
) -> str | None:
    """The cellXfs index of the style that looks exactly like current_style
    (same fill, border, font, alignment) but displays numbers with
    format_code — or None if this workbook has no such style.

    Style ids are positions in a workbook's own style table and shift
    whenever Excel adds or reorders styles, so they can't be hard-coded
    per currency: a fixed id once pointed at a date format in the real
    tracker and USD values showed up as dates."""
    fmt_id = next(
        (n.get("numFmtId") for n in styles_root.findall(f"{_M}numFmts/{_M}numFmt")
         if n.get("formatCode") == format_code),
        None,
    )
    if fmt_id is None:
        return None
    xfs = styles_root.findall(f"{_M}cellXfs/{_M}xf")
    try:
        base = xfs[int(current_style)]
    except (ValueError, IndexError):
        return None

    def _shape(xf) -> tuple:
        attrs = {k: v for k, v in xf.attrib.items() if k not in ("numFmtId", "applyNumberFormat")}
        return (tuple(sorted(attrs.items())), tuple(etree.tostring(c) for c in xf))

    if base.get("numFmtId") == fmt_id:
        return str(current_style)
    base_shape = _shape(base)
    for i, xf in enumerate(xfs):
        if xf.get("numFmtId") == fmt_id and _shape(xf) == base_shape:
            return str(i)
    return None


def _get_or_create_cell(row_elem, cells: dict, row_num: int, col_letter: str,
                         default_styles: dict[int, str]):
    """Existing <c> for this ref if the template already has one (the
    common case for the hand-edited region), otherwise a new one styled
    to match the column's default and inserted in ascending column order
    (OOXML requires <c> children sorted by column)."""
    ref = f"{col_letter}{row_num}"
    c = cells.get(ref)
    if c is not None:
        return c

    col_idx = _col_index(col_letter)
    new_c = etree.Element(f"{_M}c")
    new_c.set("r", ref)
    style = default_styles.get(col_idx)
    if style is not None:
        new_c.set("s", style)

    insert_before = None
    for child in row_elem:
        child_ref = child.get("r")
        if child_ref and _col_index(_col_letters(child_ref)) > col_idx:
            insert_before = child
            break
    if insert_before is not None:
        insert_before.addprevious(new_c)
    else:
        row_elem.append(new_c)

    cells[ref] = new_c
    return new_c


def _resolve_sheet_part(zf: zipfile.ZipFile, sheet_name: str) -> str:
    """Zip member path (e.g. 'xl/worksheets/sheet1.xml') for a sheet name,
    via xl/workbook.xml + xl/_rels/workbook.xml.rels."""
    wb_root = etree.fromstring(zf.read("xl/workbook.xml"))
    rid = None
    for sheet in wb_root.findall(f".//{_M}sheets/{_M}sheet"):
        if sheet.get("name") == sheet_name:
            rid = sheet.get(f"{{{_NS_REL}}}id")
            break
    if rid is None:
        raise ValueError(f'Sheet "{sheet_name}" not found in workbook.xml.')

    rels_root = etree.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    target = None
    for rel in rels_root:
        if rel.get("Id") == rid:
            target = rel.get("Target")
            break
    if target is None:
        raise ValueError(f'Relationship "{rid}" not found in workbook.xml.rels.')

    return target.lstrip("/") if target.startswith("/") else f"xl/{target}"


def _load_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    """Index -> text, from xl/sharedStrings.xml (older/legacy rows in the
    tracker use shared strings rather than inline strings)."""
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = etree.fromstring(zf.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(f"{_M}t")) for si in root.findall(f"{_M}si")]


def _cell_text(c_elem, shared_strings: list[str]) -> str:
    t = c_elem.get("t")
    if t == "inlineStr":
        is_elem = c_elem.find(f"{_M}is")
        return "".join(x.text or "" for x in is_elem.iter(f"{_M}t")) if is_elem is not None else ""
    v_elem = c_elem.find(f"{_M}v")
    if v_elem is None or v_elem.text is None:
        return ""
    if t == "s":
        idx = int(v_elem.text)
        return shared_strings[idx] if 0 <= idx < len(shared_strings) else ""
    return v_elem.text


def _cell_is_empty(c_elem) -> bool:
    return c_elem is None or len(c_elem) == 0


def _set_string_cell(c_elem, text: str) -> None:
    for child in list(c_elem):
        c_elem.remove(child)
    c_elem.set("t", "inlineStr")
    is_elem = etree.SubElement(c_elem, f"{_M}is")
    t_elem = etree.SubElement(is_elem, f"{_M}t")
    t_elem.text = text


def _set_numeric_cell(c_elem, value: float | int) -> None:
    for child in list(c_elem):
        c_elem.remove(child)
    if c_elem.get("t") not in (None, "n"):
        c_elem.set("t", "n")
    v_elem = etree.SubElement(c_elem, f"{_M}v")
    v_elem.text = repr(value) if isinstance(value, float) else str(value)


def _set_formula_cell(c_elem, formula: str) -> None:
    for child in list(c_elem):
        c_elem.remove(child)
    c_elem.attrib.pop("t", None)
    f_elem = etree.SubElement(c_elem, f"{_M}f")
    f_elem.text = formula
    etree.SubElement(c_elem, f"{_M}v")   # empty cached value — Excel recalculates on open


_A1_CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _sqref_token_range(token: str) -> tuple[str, int, str, int] | None:
    """Parses one whitespace-separated piece of a conditionalFormatting
    sqref attribute — "P7929:P7931" or a single cell "P7859" — into
    (start_col, start_row, end_col, end_row). None if it isn't a plain
    cell/range reference (nothing in this file's own sqref values isn't,
    but a truly malformed one shouldn't crash a write over it)."""
    parts = token.split(":")
    if len(parts) not in (1, 2):
        return None
    m1 = _A1_CELL_RE.match(parts[0])
    if not m1:
        return None
    if len(parts) == 1:
        return (m1.group(1), int(m1.group(2)), m1.group(1), int(m1.group(2)))
    m2 = _A1_CELL_RE.match(parts[1])
    if not m2:
        return None
    return (m1.group(1), int(m1.group(2)), m2.group(1), int(m2.group(2)))


def _ensure_duplicate_check_coverage(sheet_root, col_letter: str, row: int) -> None:
    """Makes sure `row`'s cell in `col_letter` is covered by some
    duplicateValues conditionalFormatting rule — the real tracker's own
    "flag repeated Descriptions" convention (see CTR Tracker feature
    notes). The real file's rule for column P has been split by Excel
    itself, from years of manual row insertions, into 100+ separate
    range fragments — some covering a single row — rather than one clean
    range, and a handful of rows have ended up with no fragment covering
    them at all (from an insertion Excel didn't extend the rule across
    cleanly). Called on every write to col_letter, this is a no-op when
    coverage already exists.

    A duplicateValues rule only ever compares cells *within its own
    sqref* — never against a different conditionalFormatting element,
    even one sharing the same dxfId, and never against the column as a
    whole (confirmed against the OOXML spec, not assumed). A brand-new
    single-cell rule for just this row would therefore have nothing to
    compare the cell against and could never actually highlight it —
    "covered" in name only. So instead of creating one, this appends the
    row as one more token onto whichever existing duplicateValues rule
    on this column already covers the most cells — its sqref is already
    a union of many disjoint ranges (that's exactly how the original,
    unfragmented rule would have looked), so adding one more token is
    consistent with its own existing shape, and the row is now genuinely
    compared against everything else in that rule's — the largest
    available — comparison set. This also heals any pre-existing gap the
    row happened to already have, the next time this code touches it.

    A sheet with no duplicateValues rule on this column at all (nothing
    to extend) is left alone — there's no existing style/rule to attach
    to, and inventing one here would be presuming a convention the real
    file doesn't actually have.
    """
    covered = False
    best_cf = None
    best_size = -1
    for cf in sheet_root.findall(f"{_M}conditionalFormatting"):
        rules = [r for r in cf.findall(f"{_M}cfRule") if r.get("type") == "duplicateValues"]
        if not rules:
            continue
        tokens = (cf.get("sqref") or "").split()
        ranges = [_sqref_token_range(t) for t in tokens]
        ranges = [r for r in ranges if r is not None and r[0] == col_letter and r[2] == col_letter]
        if not ranges:
            continue

        if any(r[1] <= row <= r[3] for r in ranges):
            covered = True
            break

        size = sum(r[3] - r[1] + 1 for r in ranges)
        if size > best_size:
            best_size, best_cf = size, cf

    if covered or best_cf is None:
        return

    existing_sqref = best_cf.get("sqref") or ""
    best_cf.set("sqref", f"{existing_sqref} {col_letter}{row}".strip())


# Entry attribute -> config column key, for the plain-string fields this
# writes. CTR number (col_ctr_number) is deliberately excluded — it's the
# search key a human already filled in, never written by this code.
#
# Split into two groups because they're treated differently when a row's
# cell already has something in it: the "core" fields are what
# _classify_target_row requires to be empty for a match (see its check_cols),
# so by the time we get here they're guaranteed blank (or allow_overwrite
# is on and blank-ness doesn't matter) — always written. The "soft"
# fields are explicitly allowed to already have a value (a human often
# pre-fills Client/Location/Project Code by hand when creating the row),
# so whatever's already there is preserved unless allow_overwrite is on.
_CORE_STRING_FIELDS = {
    "col_description": "description",
    "col_currency":    "currency",
}
_SOFT_STRING_FIELDS = {
    "col_client":       "client",
    "col_location":     "tracker_location",
    "col_project_code": "project_code",
    "col_job_type":     "job_type",
}


def _activity_matches_location(activity_type: str, tracker_location: str) -> bool:
    """The CTR's own labor section says "Onshore" or "Offshore" (see
    _find_activity_type in tracker.py); the tracker's Location bucket has
    a third option, "Georgia", for Georgia-sited work — which is onshore
    work, so it's treated as equivalent to "Onshore" here. No activity
    type found (e.g. a CTR with no manpower section) means there's
    nothing to cross-check, so it's not treated as a mismatch."""
    if not activity_type:
        return True
    normalized_location = "Onshore" if tracker_location == "Georgia" else tracker_location
    return activity_type == normalized_location


def _classify_target_row(
    row_by_num: dict[int, "etree._Element"],
    shared_strings: list[str],
    cfg: dict,
    base_ctr_number: str,
    currency: str,
    used_rows: set[int],
) -> tuple[str | None, int | None, bool, str]:
    """Finds the row reserved for base_ctr_number, distinguishing four
    outcomes so the caller can decide what a "revision" write should do:

        ("empty", row, has_filled_sibling, "")
            — a compatible row with nothing in it yet; write into it
              normally. has_filled_sibling is True when a *different* row
              for this same (CTR number, currency) already has data —
              i.e. this write is still a revision of that other row, even
              though it lands in an available spare rather than
              overwriting anything (see write_entries_fast).
        ("filled", row, True, "")
            — no empty row, but a row already holding data for this exact
              (CTR number, currency) — the caller decides overwrite vs.
              separate revision (see write_entries_fast).
        ("wrong_currency", None, False, reason)
            — this CTR number exists, but only ever for the *other*
              currency — nothing reserved for this one at all, as opposed
              to something already there that a write would collide with.
              The caller always creates a row for it (see
              write_entries_fast) — unlike a genuine revision conflict,
              there's nothing existing to overwrite or protect here.
        (None, None, False, reason)
            — the CTR number itself doesn't exist in the sheet at all.

    A row already labeled with a *different* currency (data a human, or an
    earlier write, put there) is treated as reserved for that other
    currency and skipped entirely — never matched, never reported as
    "filled" — so writing an AZN entry can't collide with a row waiting
    for its USD counterpart. This matches the real tracker's own
    convention of pre-creating one blank row per (CTR number, currency).

    Revision is deliberately *not* filtered on the same way — a row's
    Revision (AI) just records which revision its current data is, not a
    reservation label for a not-yet-written one (unlike currency, there's
    no real-file evidence of a "one row per revision" pre-creation
    convention), and overwriting a row is precisely how its revision is
    expected to change. It only matters once a row's classified: see
    write_entries_fast for how kind == "filled" is actually handled.

    Date/Description/Value determine emptiness (must be blank). A cell
    already holding the *same* currency is a pre-label reservation marker,
    not "data already there", so Currency isn't required blank here.
    Client, Location and Project Code are fine to already have something
    either way, since a human pre-creating the row often fills those in
    by hand as part of setting it up.
    """
    d_col   = cfg["col_ctr_number"]
    cur_col = cfg["col_currency"]
    check_cols = [cfg["col_date"], cfg["col_description"], cfg["col_value"]]

    empty_row: int | None = None
    filled_row: int | None = None
    any_ctr_number_row = False

    for row_num in sorted(row_by_num):
        row_elem = row_by_num[row_num]
        d_cell = next((c for c in row_elem if c.get("r") == f"{d_col}{row_num}"), None)
        if d_cell is None:
            continue
        if _cell_text(d_cell, shared_strings).strip() != base_ctr_number:
            continue
        # True regardless of used_rows — another entry in this same batch
        # already claiming this row as *its own* write target doesn't mean
        # the CTR number itself doesn't exist. Getting this wrong makes a
        # second entry for the same CTR number (e.g. its USD row, or
        # another revision) invisible to every later entry in the batch —
        # exactly the bug where only the first entry per CTR number ever
        # wrote successfully and everything else came back "no row found".
        any_ctr_number_row = True

        if row_num in used_rows:
            continue   # already claimed as a write target by another
                        # entry in this batch — can't be picked again, but
                        # its existence just above still counts

        cells = {c.get("r"): c for c in row_elem.findall(f"{_M}c")}

        cur_cell = cells.get(f"{cur_col}{row_num}")
        cur_text = _cell_text(cur_cell, shared_strings).strip() if cur_cell is not None else ""
        if cur_text and currency and cur_text.upper() != currency.strip().upper():
            continue   # reserved for the other currency

        if all(_cell_is_empty(cells.get(f"{col}{row_num}")) for col in check_cols):
            if empty_row is None:
                empty_row = row_num
        elif filled_row is None:
            filled_row = row_num

    if empty_row is not None:
        return "empty", empty_row, filled_row is not None, ""
    if filled_row is not None:
        return "filled", filled_row, True, ""
    if any_ctr_number_row:
        return "wrong_currency", None, False, (
            f'a row with CTR number "{base_ctr_number}" exists but only for a '
            f"different currency"
        )
    return None, None, False, f'no row found with CTR number "{base_ctr_number}" in column {d_col}'


def _existing_revision(
    row_by_num: dict[int, "etree._Element"], shared_strings: list[str], cfg: dict, row_num: int,
) -> str:
    """The Revision (AI) cell's current text for an already-filled row, so
    a "separate revision" write can tell whether this is actually a new
    revision or just a correction to the same one it already has."""
    row_elem = row_by_num.get(row_num)
    if row_elem is None:
        return ""
    rev_col = cfg["col_revision"]
    rev_cell = next((c for c in row_elem if c.get("r") == f"{rev_col}{row_num}"), None)
    return _cell_text(rev_cell, shared_strings).strip() if rev_cell is not None else ""


def _row_currency(
    row_by_num: dict[int, "etree._Element"], shared_strings: list[str], cfg: dict, row_num: int,
) -> str:
    """The Currency (AC) cell's current text for a row — "" if it's blank,
    i.e. not yet explicitly labeled for either currency."""
    row_elem = row_by_num.get(row_num)
    if row_elem is None:
        return ""
    cur_col = cfg["col_currency"]
    cur_cell = next((c for c in row_elem if c.get("r") == f"{cur_col}{row_num}"), None)
    return _cell_text(cur_cell, shared_strings).strip().upper() if cur_cell is not None else ""


def _last_row_for_ctr(
    row_by_num: dict[int, "etree._Element"], shared_strings: list[str], cfg: dict,
    base_ctr_number: str,
) -> int | None:
    """The highest row number among every row sharing base_ctr_number
    (any currency) — used to place a CTR number's *first* row for a
    currency it's never had before, right after whichever currency's
    block already exists, so the two stay grouped together in the sheet
    rather than the new one landing somewhere unrelated. None if no row
    shares that CTR number at all.

    Deliberately ignores used_rows — this is purely a position lookup
    (where does this CTR number's block end), not a claim on a write
    target, so another entry in the same batch already writing into one
    of this CTR number's rows must not hide that row from this lookup."""
    d_col = cfg["col_ctr_number"]
    last_row = None
    for row_num in sorted(row_by_num):
        row_elem = row_by_num[row_num]
        d_cell = next((c for c in row_elem if c.get("r") == f"{d_col}{row_num}"), None)
        if d_cell is not None and _cell_text(d_cell, shared_strings).strip() == base_ctr_number:
            last_row = row_num
    return last_row


def _revision_sort_key(revision_text: str) -> tuple[int, float]:
    """Ordering for revision comparison: negative numbers, then blank
    ("no value"), then zero and positive numbers — in that order. Blank
    sorts strictly between negative and zero, not merely "after
    everything numeric": e.g. revision "-1" < "" < "0" < "1".

    Numeric revisions ("2", "10") compare as numbers, not text, so "10"
    correctly sorts after "9" rather than before it. Text with a number
    embedded in it ("Rev2", "2-draft") is stripped down to that number
    for comparison purposes only — the original text is still what's
    actually written to the sheet (see write_entries_fast), this key
    only ever decides ordering. Text with no number in it at all (or a
    genuinely blank revision) has nothing to compare by, so it falls in
    the same "no value" slot."""
    text = (revision_text or "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text) if text else None
    if match is None:
        return (1, 0.0)                  # no value / no embedded number
    value = float(match.group())
    return (0, value) if value < 0 else (2, value)   # negative, or zero-and-up


def _filled_revision_rows(
    row_by_num: dict[int, "etree._Element"],
    shared_strings: list[str],
    cfg: dict,
    base_ctr_number: str,
    currency: str,
    used_rows: set[int],
) -> list[tuple[tuple, int, str]]:
    """Every *filled* row for this exact (CTR number, currency), as
    (revision_sort_key, row_num, revision_text), sorted by revision.
    Rows reserved for the other currency and still-blank spares are left
    out — neither has a revision to order against."""
    d_col   = cfg["col_ctr_number"]
    cur_col = cfg["col_currency"]
    rev_col = cfg["col_revision"]
    check_cols = [cfg["col_date"], cfg["col_description"], cfg["col_value"]]

    filled: list[tuple[tuple, int, str]] = []
    for row_num in sorted(row_by_num):
        if row_num in used_rows:
            continue
        row_elem = row_by_num[row_num]
        d_cell = next((c for c in row_elem if c.get("r") == f"{d_col}{row_num}"), None)
        if d_cell is None or _cell_text(d_cell, shared_strings).strip() != base_ctr_number:
            continue
        cells = {c.get("r"): c for c in row_elem.findall(f"{_M}c")}
        cur_cell = cells.get(f"{cur_col}{row_num}")
        cur_text = _cell_text(cur_cell, shared_strings).strip() if cur_cell is not None else ""
        if cur_text and currency and cur_text.upper() != currency.strip().upper():
            continue
        if all(_cell_is_empty(cells.get(f"{col}{row_num}")) for col in check_cols):
            continue   # not filled — irrelevant to revision ordering
        rev_cell = cells.get(f"{rev_col}{row_num}")
        rev_text = _cell_text(rev_cell, shared_strings).strip() if rev_cell is not None else ""
        filled.append((_revision_sort_key(rev_text), row_num, rev_text))

    filled.sort(key=lambda t: t[0])
    return filled


def _insertion_row_for_revision(
    row_by_num: dict[int, "etree._Element"],
    shared_strings: list[str],
    cfg: dict,
    base_ctr_number: str,
    currency: str,
    new_revision: str,
    used_rows: set[int],
) -> int:
    """Where a brand-new row for new_revision belongs, in ascending
    revision order among every *filled* row already there for this exact
    (CTR number, currency) — not just appended after whichever one
    _classify_target_row happened to find first. Returns the row number
    to insert *at* (Excel shifts that row, and everything below it, down
    by one) — the row currently holding the smallest existing revision
    that's greater than new_revision, so the new row lands between it and
    whatever comes before. If new_revision is higher than every existing
    one (the common case — always adding the latest), that's simply one
    past the last existing revision row."""
    filled = _filled_revision_rows(
        row_by_num, shared_strings, cfg, base_ctr_number, currency, used_rows,
    )
    new_key = _revision_sort_key(new_revision)
    for key, row_num, _text in filled:
        if key > new_key:
            return row_num
    # Higher than every existing revision (or there were none found here,
    # which shouldn't happen given the caller only calls this once it
    # already knows a filled row exists) — land right after the last one.
    return (filled[-1][1] + 1) if filled else None


def _check_spare_row(
    filled: list[tuple[tuple, int, str]], spare_row: int, new_revision: str,
) -> tuple[str, int | None]:
    """Whether a blank spare row can take new_revision without breaking
    ascending revision order among this (CTR, currency)'s filled rows:

        ("same", row)      — a filled row already holds this exact
                             revision; it's a correction, so that row is
                             overwritten in place and the spare is left
                             alone.
        ("misordered", None) — using the spare would put new_revision above
                             a lower revision or below a higher one.
        ("ok", None)       — every lower revision is above the spare and
                             every higher one below it (or nothing filled).
    """
    wanted = (new_revision or "").strip()
    new_key = _revision_sort_key(wanted)
    for _key, row_num, text in filled:
        if text == wanted:
            return "same", row_num
    for key, row_num, _text in filled:
        if (key < new_key and row_num > spare_row) or (key > new_key and row_num < spare_row):
            return "misordered", None
    return "ok", None


def _validated_base(entry: CTREntry) -> tuple[str | None, str]:
    """Base (currency-suffix-stripped) CTR number for entry, or (None,
    reason) if it can't even be considered — shared by both passes below
    so they never disagree about which entries are eligible."""
    if not _activity_matches_location(entry.activity_type, entry.tracker_location):
        return None, (
            f'CTR labor section says "{entry.activity_type} Activities" but the '
            f'selected Location is "{entry.tracker_location}" — resolve the '
            "mismatch before retrying"
        )
    base = _normalize_ctr_number(entry.ctr_number)
    if not base:
        return None, "no CTR number to match on"
    return base, ""


def _plan_insertions(
    tracker_path: Path, cfg: dict, entries: list[CTREntry],
    allow_overwrite: bool, revision_mode: str,
) -> tuple[list[tuple[int, str]], dict[int, int], dict[int, str], dict[int, int]]:
    """Read-only planning pass, against the file's *current* on-disk
    state, for the two situations that need a brand-new row inserted:

      - "wrong_currency": this CTR number exists, but has never had a row
        for this entry's currency at all — always creates one, regardless
        of allow_overwrite/revision_mode, since nothing existing is being
        touched or replaced; there's no "overwrite" happening here.
      - "filled" + genuinely a new revision: gated behind allow_overwrite
        and revision_mode == "separate", same as before.

    Must run, and any resulting insertion must happen, strictly before the
    real read/write pass below opens the file: that pass takes one
    snapshot of the sheet and writes it back in a single shot at the end,
    so an insertion done concurrently with or after that snapshot would
    simply be overwritten and lost.

    When *two* entries in the same batch both need a row inserted for the
    same CTR number (its other currency, or another new revision), the
    two brand-new rows are indistinguishable to the real pass below — both
    are blank except for the CTR number — so it can't tell which one was
    computed for which entry, and picks whichever happens to come first.
    That's not just cosmetic: it can silently land a new revision *above*
    an older one, since the position each insertion actually lands at
    depends on every other insertion in the same batch shifting things
    as they're applied. So insert_at_row here is only ever the position
    computed against this pass's own read-only snapshot, before any of
    them have actually happened — final_row_for_idx below is what the
    real pass must use instead of re-discovering a target from scratch.

    A blank spare row is only used when it keeps this (CTR, currency)'s
    filled rows in ascending revision order (see _check_spare_row). If it
    wouldn't — e.g. a blank revision arriving after revision 1 while the
    only spare sits below it — the spare is left alone, a row is inserted
    at the ordered position instead, and the skipped spare is reported in
    spare_skipped. If a filled row already holds the entry's exact
    revision, that row is overwritten in place instead.

    Returns (to_insert, final_row_for_idx, pre_skip, spare_skipped):
      to_insert         — [(insert_at_row, base_ctr_number), ...] for
                           tracker_xlwings.insert_revision_rows, each
                           computed against this snapshot.
      final_row_for_idx — entries[] index -> the row it will actually
                           land on once every insertion in to_insert has
                           been applied (accounting for each one shifting
                           the others) — the real pass writes directly
                           into this row for these entries, skipping
                           re-classification entirely.
      pre_skip          — entries[] index -> reason, for anything this
                           pass already knows can't be written (invalid).
      spare_skipped     — entries[] index -> the blank spare row (in this
                           snapshot's numbering) it was NOT written into
                           because that would have broken revision order.
    """
    with zipfile.ZipFile(tracker_path, "r") as zin:
        sheet_part = _resolve_sheet_part(zin, cfg["sheet_name"])
        probe_root = etree.fromstring(zin.read(sheet_part))
        probe_strings = _load_shared_strings(zin)
    probe_row_by_num = {
        int(r.get("r")): r for r in probe_root.findall(f".//{_M}sheetData/{_M}row")
    }
    probe_used: set[int] = set()

    # (idx, insert_at_row, base_ctr_number, flexible), in the order
    # entries are walked below — turned into to_insert/final_row_for_idx
    # afterward. flexible is True for a currency's brand-new first row
    # (wrong_currency below) — it has no revision to order against, just
    # "somewhere in this CTR's block" — and False for a revision insert,
    # which has a specific position it must land at relative to its own
    # currency's existing rows. See the tie-break note below for why this
    # distinction matters.
    raw_insertions: list[tuple[int, int, str, bool]] = []
    pre_skip: dict[int, str] = {}
    spare_skipped: dict[int, int] = {}
    in_place_rows: dict[int, int] = {}   # idx -> filled row holding the same revision

    # (base_ctr_number, currency) -> the entries[] idx that already claims
    # it, earlier in this same batch. A second entry for the exact same
    # pair is deliberately refused here rather than routed through
    # _classify_target_row/_insertion_row_for_revision: both of those only
    # ever see this pass's single on-disk snapshot, so they can't tell an
    # entry apart from a sibling that's about to occupy the very row it
    # would need to compare itself against — the earlier entry's revision
    # isn't "real" yet as far as this snapshot is concerned. Skipping here
    # keeps the entry in the batch table (see tracker_window.py) so it can
    # be retried in a later batch, once the first write is genuinely on
    # disk and ordering can be computed correctly against it.
    claimed_currency: dict[tuple[str, str], int] = {}

    for idx, entry in enumerate(entries):
        base, reason = _validated_base(entry)
        if base is None:
            pre_skip[idx] = reason
            continue

        currency_key = (base, (entry.currency or "").strip().upper())
        prior_idx = claimed_currency.get(currency_key)
        if prior_idx is not None:
            pre_skip[idx] = (
                f'CTR number "{base}" ({entry.currency or "no currency set"}) '
                f"{IN_BATCH_CONFLICT_MARKER} (entry #{prior_idx + 1}) — write it in a "
                "separate batch afterward."
            )
            continue
        claimed_currency[currency_key] = idx

        kind, row, _, _ = _classify_target_row(
            probe_row_by_num, probe_strings, cfg, base, entry.currency, probe_used,
        )

        if kind == "wrong_currency":
            # No row was ever reserved for this currency — not a conflict
            # to resolve, just a currency this CTR number hasn't had a row
            # for yet. Always create one, right after whichever currency's
            # block already exists, so the two stay grouped together.
            last_row = _last_row_for_ctr(probe_row_by_num, probe_strings, cfg, base)
            if last_row is not None:
                raw_insertions.append((idx, last_row + 1, base, True))
            continue

        if kind is None:
            continue   # real pass reports the "no row found" skip itself
        probe_used.add(row)

        if kind == "empty":
            filled = _filled_revision_rows(
                probe_row_by_num, probe_strings, cfg, base, entry.currency, set(),
            )
            verdict, same_row = _check_spare_row(filled, row, entry.revision)
            if verdict == "ok":
                continue   # a normal write
            probe_used.discard(row)   # the spare stays free for a later entry
            if verdict == "same":
                probe_used.add(same_row)
                in_place_rows[idx] = same_row
                continue
            insert_at = _insertion_row_for_revision(
                probe_row_by_num, probe_strings, cfg, base,
                entry.currency, entry.revision, set(),
            )
            raw_insertions.append((idx, insert_at, base, False))
            spare_skipped[idx] = row
            continue

        if not (allow_overwrite and revision_mode == "separate"):
            continue   # real pass overwrites `row` directly, or skips it — either way, nothing to plan here

        # Same revision as what's already there — a correction, not a new
        # revision — so the real pass overwrites `row` in place instead.
        # Blank counts as a revision value here too (matching
        # _revision_sort_key's "no value" category): a blank entry into a
        # row that's also blank is "the same" and overwrites, not a
        # different revision needing a spare/inserted row of its own.
        if (entry.revision or "").strip() == _existing_revision(
            probe_row_by_num, probe_strings, cfg, row,
        ):
            continue

        spare_kind, spare_row, _, _ = _classify_target_row(
            probe_row_by_num, probe_strings, cfg, base, entry.currency, probe_used,
        )
        # A row already has at least one revision for this (CTR, currency)
        # by this point, so any spare found here must already be
        # explicitly labeled with the matching currency to be reused — an
        # unlabeled blank one is more likely reserved for the *other*
        # currency (the real tracker's own pairing convention) than a
        # free-for-all slot, and taking it would leave that currency
        # without its intended spare. A row's *first* revision (the
        # `kind == "empty"` branch above, before this point) still treats
        # a blank currency cell as compatible with either, since that's
        # the ordinary case of a human-precreated row getting labeled on
        # first use.
        has_matching_spare = (
            spare_kind == "empty"
            and _row_currency(probe_row_by_num, probe_strings, cfg, spare_row) == entry.currency.strip().upper()
        )
        if has_matching_spare:
            probe_used.add(spare_row)   # reserve it too, in case of a duplicate CTR number later in this same batch
        else:
            # Not `probe_used` here — reading existing revisions for
            # ordering must see every row actually on the sheet,
            # including `row` itself (already marked used above so no
            # *other* entry in this batch writes into it, which is an
            # unrelated concern from being read for comparison here).
            insert_at = _insertion_row_for_revision(
                probe_row_by_num, probe_strings, cfg, base,
                entry.currency, entry.revision, set(),
            )
            raw_insertions.append((idx, insert_at, base, False))

    # Simulate the cumulative shift each insertion causes on the others,
    # processed from the top of the sheet down: an insertion at row X
    # pushes every row at/after X down by one, so any *other* insertion
    # whose own raw target is >= X ends up one row further down for each
    # such earlier (lower-numbered) insertion that lands before it. This
    # exactly predicts what tracker_xlwings.insert_revision_rows actually
    # produces (it applies them bottom-up so it can use these same raw
    # row numbers directly, without needing to track shifts itself).
    #
    # Ties (two insertions computed at the identical raw row) are broken
    # by putting a revision insert (flexible=False) before a currency's
    # brand-new first row (flexible=True) rather than by original
    # entries[] order — a batch adding both a CTR's first-ever row for one
    # currency and a new revision for its *other*, already-existing
    # currency can compute the exact same raw row for both (right after
    # that currency's own last existing row happens to be right after the
    # other currency's block). The revision insert has one correct place
    # to land, relative to its own currency's existing rows; the new
    # currency's first row has no such constraint — it just needs to be
    # somewhere in this CTR's block — so it's the one that should absorb
    # the tie and get pushed one row further down, not the other way
    # around. Two ties of the same flexibility fall back to entries[]
    # order, same as before.
    ordered = sorted(raw_insertions, key=lambda t: (t[1], t[3]))
    to_insert: list[tuple[int, str]] = []
    final_row_for_idx: dict[int, int] = {}
    shift = 0
    for idx, insert_at, base, _flexible in ordered:
        final_row_for_idx[idx] = insert_at + shift
        to_insert.append((insert_at, base))
        shift += 1

    for idx, same_row in in_place_rows.items():
        # Rows at/after an insertion point shift down by one for each one.
        final_row_for_idx[idx] = same_row + sum(1 for at, _b in to_insert if at <= same_row)

    return to_insert, final_row_for_idx, pre_skip, spare_skipped


def write_entries_fast(
    tracker_path: str | Path,
    entries: list[CTREntry],
    make_backup: bool = False,
    allow_overwrite: bool = True,
    revision_mode: str = "separate",
) -> tuple[
    list[tuple[int, str, int]], list[tuple[int, str, str]], Path | None, list[str],
]:
    """Writes each entry into the existing tracker row reserved for its
    CTR number (see module docstring), backs up if requested, and saves
    the workbook in place. Returns (written, skipped, backup_path, notes):
    written is [(entries_idx, ctr_number, row), ...] for entries actually
    written; skipped is [(entries_idx, ctr_number, reason), ...] for
    entries that couldn't be placed; notes is a list of human-readable
    lines about blank spare rows deliberately left unused because writing
    into them would have broken ascending revision order (the entry got an
    inserted row at the ordered position instead). entries_idx is this call's own
    position in `entries` — the only unambiguous way for a caller to map
    a result back to the entry it came from, since two entries can share
    the same CTR number (e.g. two revisions of one CTR in one batch — see
    IN_BATCH_CONFLICT_MARKER). The file is only touched if at least one
    entry was written.

    allow_overwrite and revision_mode default to True / "separate" — the
    tracker window no longer offers them as choices. allow_overwrite lets a match land on a row whose
    core data fields already have something in them, instead of treating
    that as "already filled" and skipping it — use with care, since it
    can silently replace real data with no undo besides the backup.

    revision_mode ("overwrite" or "separate"; ignored unless
    allow_overwrite is on) decides *how* an already-filled row gets
    handled: "overwrite" replaces its data in place; "separate" leaves it
    untouched and writes into a spare pre-created row sharing the same
    CTR number and currency instead — or, if none is available and this
    really is a new revision (entry.revision differs from what's already
    on the matched row; writing the *same* revision again overwrites in
    place instead, same as "overwrite" would), inserts a new one via
    Excel automation (see tracker_xlwings.py — Windows/macOS with Excel
    installed only), positioned in ascending revision order among any
    other rows already there for that CTR number and currency — between
    two existing revisions if entry.revision falls between them, not
    just appended after whichever one happens to be found first.

    A CTR number that has rows for one currency but has never had one for
    the entry's currency at all is handled independently of both
    allow_overwrite and revision_mode — there's nothing existing to
    overwrite or protect there, just a currency this CTR number hasn't
    had a row for yet, so one is always created (via the same Excel
    automation as above), right after whichever currency's block already
    exists so the two stay grouped together.

    Any entry that carries a revision number (from the CTR file's own
    header field) gets it written to the dedicated Revision column (AI) —
    row order (and AI itself) is what marks a row as a revision, so
    nothing is separately written to note that elsewhere."""
    if not entries:
        raise ValueError("No CTR entries to write.")
    if len(entries) > 10:
        raise ValueError("At most 10 CTRs can be written at once.")

    tracker_path = Path(tracker_path)
    cfg = CFG["ctr_tracker"]
    backup_path = backup_tracker(tracker_path) if make_backup else None

    # Always runs (not just when allow_overwrite is on) — a missing-currency
    # row needs creating regardless; _plan_insertions itself gates the
    # separate-revision-insertion logic behind allow_overwrite internally.
    to_insert, final_row_for_idx, pre_skip, spare_skipped = _plan_insertions(
        tracker_path, cfg, entries, allow_overwrite, revision_mode,
    )

    if to_insert:
        try:
            tracker_xlwings.insert_revision_rows(
                tracker_path, cfg["sheet_name"],
                [(insert_at, base, cfg["col_ctr_number"]) for insert_at, base in to_insert],
                row_local_formulas=[tuple(pair) for pair in cfg.get("row_local_formulas", [])],
            )
        except (tracker_xlwings.ExcelAutomationUnavailable, RuntimeError) as exc:
            for idx in final_row_for_idx:
                pre_skip[idx] = str(exc)
            final_row_for_idx = {}
            spare_skipped = {}   # nothing was inserted — the spare stays the only free row

    with zipfile.ZipFile(tracker_path, "r") as zin:
        sheet_part = _resolve_sheet_part(zin, cfg["sheet_name"])
        sheet_root = etree.fromstring(zin.read(sheet_part))
        shared_strings = _load_shared_strings(zin)
        infos = {i.filename: i for i in zin.infolist()}
        all_data = {name: zin.read(name) for name in zin.namelist() if name != sheet_part}

    row_by_num = {
        int(r.get("r")): r
        for r in sheet_root.findall(f".//{_M}sheetData/{_M}row")
    }
    default_styles = _col_default_styles(sheet_root)
    styles_root = etree.fromstring(all_data["xl/styles.xml"])

    written: list[tuple[int, str, int]] = []
    skipped: list[tuple[int, str, str]] = [
        (idx, entries[idx].ctr_number, reason) for idx, reason in pre_skip.items()
    ]
    used_rows: set[int] = set()

    for idx, entry in enumerate(entries):
        if idx in pre_skip:
            continue

        base, reason = _validated_base(entry)
        if base is None:
            skipped.append((idx, entry.ctr_number or "(blank CTR number)", reason))
            continue

        if idx in final_row_for_idx:
            # The planning pass already inserted (or found a spare for)
            # this exact entry and knows precisely which row it landed
            # on — use that directly instead of re-classifying, which
            # can't tell this entry's newly-inserted row apart from any
            # *other* entry's in the same batch (both are blank except
            # for the CTR number) and could otherwise hand this entry a
            # row meant for a sibling insertion, silently scrambling
            # currency grouping or revision order.
            row = final_row_for_idx[idx]
        else:
            kind, row, _, reason = _classify_target_row(
                row_by_num, shared_strings, cfg, base, entry.currency, used_rows,
            )
            if kind is None:
                skipped.append((idx, entry.ctr_number, reason))
                continue
            if kind == "wrong_currency":
                # The planning pass should have resolved this via
                # insertion (making it findable via final_row_for_idx
                # above) or already recorded a pre_skip reason if that
                # insertion failed — reaching this branch means neither
                # happened, which shouldn't occur; skip rather than
                # silently doing nothing with it.
                skipped.append((
                    idx, entry.ctr_number,
                    f'a row with CTR number "{base}" exists but never had one for '
                    f'currency "{entry.currency}", and no row could be created for it',
                ))
                continue
            if kind == "filled":
                if not allow_overwrite:
                    skipped.append((
                        idx, entry.ctr_number,
                        f'a row with CTR number "{base}" exists but is already filled in',
                    ))
                    continue
                # Blank counts as a revision value here too — a blank
                # entry into a row whose revision is also blank is "the
                # same", not a different revision.
                same_revision = (entry.revision or "").strip() == _existing_revision(
                    row_by_num, shared_strings, cfg, row,
                )
                if revision_mode == "separate" and not same_revision:
                    # The planning pass should have resolved this to a
                    # spare row (found via final_row_for_idx above), an
                    # insertion, or a pre_skip entry — reaching "filled"
                    # here means none of those happened. Treat it as
                    # unresolved rather than silently overwriting a row
                    # the user explicitly chose not to touch.
                    skipped.append((
                        idx, entry.ctr_number,
                        f'a row with CTR number "{base}" already has data and no spare or '
                        "inserted row was available for it",
                    ))
                    continue
                # Either revision_mode == "overwrite", or it's "separate"
                # but entry.revision matches what this row already has —
                # not actually a new revision, just a correction to the
                # same one, so it overwrites in place rather than
                # spawning another row.
        used_rows.add(row)

        row_elem = row_by_num[row]
        cells = {c.get("r"): c for c in row_elem.findall(f"{_M}c")}

        def _cell(col_letter: str, _row=row, _cells=cells, _row_elem=row_elem):
            return _get_or_create_cell(_row_elem, _cells, _row, col_letter, default_styles)

        for cfg_key, attr in _CORE_STRING_FIELDS.items():
            value = getattr(entry, attr)
            if value:
                _set_string_cell(_cell(cfg[cfg_key]), value)
                if cfg_key == "col_description":
                    _ensure_duplicate_check_coverage(sheet_root, cfg[cfg_key], row)

        for cfg_key, attr in _SOFT_STRING_FIELDS.items():
            value = getattr(entry, attr)
            if not value:
                continue
            cell = _cell(cfg[cfg_key])
            if allow_overwrite or _cell_is_empty(cell):
                _set_string_cell(cell, value)

        if entry.ctr_date is not None:
            _set_numeric_cell(_cell(cfg["col_date"]), _excel_serial(entry.ctr_date))
        if entry.value is not None:
            value_cell = _cell(cfg["col_value"])
            _set_numeric_cell(value_cell, entry.value)
            format_code = cfg["value_format_by_currency"].get(entry.currency)
            if format_code is not None:
                style = _style_with_number_format(
                    styles_root, value_cell.get("s") or default_styles.get(
                        _col_index(cfg["col_value"]), "0"), format_code,
                )
                if style is not None:
                    value_cell.set("s", style)
        if entry.revision:
            try:
                _set_numeric_cell(_cell(cfg["col_revision"]), int(entry.revision))
            except ValueError:
                _set_string_cell(_cell(cfg["col_revision"]), entry.revision)

        formula = cfg["value_usd_formula"].format(row=row, rate=cfg["azn_to_usd_rate"])
        _set_formula_cell(_cell(cfg["col_value_usd"]), formula)

        # Cost breakdown (AL:AX) — best-effort, "not all applicable" to
        # every CTR. AZN CTRs (pure labor documents) only ever contribute
        # Labor; USD CTRs (equipment/consumables/third-party documents)
        # contribute the rest — see CFG["ctr_tracker"] for why Labor
        # embeds a computed literal rather than a cross-workbook reference.
        touched_breakdown = False

        if entry.currency == "AZN":
            if entry.total_project_support is not None or entry.total_activities is not None:
                support = round(entry.total_project_support or 0.0, 2)
                activities = round(entry.total_activities or 0.0, 2)
                labor_formula = cfg["labor_formula"].format(
                    support=support, activities=activities, rate=cfg["azn_to_usd_rate"],
                )
                _set_formula_cell(_cell(cfg["col_labor"]), labor_formula)
                touched_breakdown = True

        elif entry.currency == "USD":
            if entry.total_equipment is not None:
                _set_numeric_cell(_cell(cfg["col_equipment"]), entry.total_equipment)
                touched_breakdown = True
            if entry.total_third_party is not None:
                _set_numeric_cell(_cell(cfg["col_third_party"]), entry.total_third_party)
                touched_breakdown = True
            if entry.transportation_customs is not None or entry.transportation_markup_amount is not None:
                customs_total = (
                    (entry.transportation_customs or 0.0)
                    + (entry.transportation_markup_amount or 0.0)
                )
                _set_numeric_cell(_cell(cfg["col_customs_transport"]), customs_total)
                touched_breakdown = True
            if entry.general_consumables is not None:
                _set_numeric_cell(_cell(cfg["col_consumables_recharge"]), entry.general_consumables)
                touched_breakdown = True

        if touched_breakdown:
            markup_formula = cfg["consumables_markup_formula"].format(
                row=row, rate=cfg["consumables_markup_rate"],
            )
            _set_formula_cell(_cell(cfg["col_consumables_markup"]), markup_formula)
            total_formula = cfg["total_usd_formula"].format(row=row)
            _set_formula_cell(_cell(cfg["col_total_usd"]), total_formula)

        written.append((idx, entry.ctr_number, row))

    if written:
        new_sheet_xml = etree.tostring(
            sheet_root, xml_declaration=True, encoding="UTF-8", standalone=True
        )
        tmp_path = tracker_path.with_name(tracker_path.name + ".tmp")
        try:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
                for name, info in infos.items():
                    data = new_sheet_xml if name == sheet_part else all_data[name]
                    zout.writestr(info, data)
            tmp_path.replace(tracker_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    # A spare left unused is shifted down by every insertion at/above it.
    notes = [
        f"row {spare + sum(1 for at, _b in to_insert if at <= spare)} left blank — "
        f"using it for {entries[idx].ctr_number} would have broken revision order "
        f"(a row was inserted instead)"
        for idx, spare in sorted(spare_skipped.items())
        if idx not in pre_skip
    ]

    return written, skipped, backup_path, notes
