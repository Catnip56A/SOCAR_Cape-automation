"""
ctr_generator/tracker_fast.py

Fast path for writing CTR Tracker entries.

Placement: a CTR is written into the existing tracker row whose CTR
number column already contains its base number (currency suffix
stripped, e.g. "CTR-26-217 AZN" -> "CTR-26-217" — see
CFG["ctr_tracker"]["value_usd_formula"] for why AZN/USD share one base
number). Rows are pre-created by hand; this module never creates a new
row and never shifts existing ones — if no matching, still-empty row
exists, the CTR is skipped with a warning instead of guessing a
placement. An earlier version appended to the first blank row at the
end of the sheet; that was replaced because CTR numbers need to land
next to the row a human already reserved for them, and because true row
insertion (shifting thousands of existing rows, their formulas, cell
comments, and data-validation ranges) was judged too risky to hand-roll
against a shared production file.

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

from ctr_generator.config import CFG
from ctr_generator.tracker import CTREntry, backup_tracker

log = logging.getLogger(__name__)

_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_M = f"{{{_NS_MAIN}}}"

_EXCEL_EPOCH = date(1899, 12, 30)

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


# Entry attribute -> config column key, for the plain-string fields this
# writes. CTR number (col_ctr_number) is deliberately excluded — it's the
# search key a human already filled in, never written by this code.
#
# Split into two groups because they're treated differently when a row's
# cell already has something in it: the "core" fields are what
# _find_target_row requires to be empty for a match (see its check_cols),
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


def _find_target_row(
    row_by_num: dict[int, "etree._Element"],
    shared_strings: list[str],
    cfg: dict,
    base_ctr_number: str,
    used_rows: set[int],
    allow_overwrite: bool,
) -> tuple[int | None, str]:
    """A row whose CTR number column matches base_ctr_number, or
    (None, reason) — distinguishing "no such CTR number anywhere" from
    "found it, but that row is already filled in" so the skip warning is
    actually useful.

    Only the core data fields (date/description/value/currency/revision)
    have to be empty for a match to count as available — Client,
    Location and Project Code are fine to already have something, since
    a human pre-creating the row often fills those in by hand as part of
    setting it up. allow_overwrite skips the emptiness requirement
    entirely and returns the first matching row regardless of its
    current contents."""
    d_col = cfg["col_ctr_number"]
    check_cols = [
        cfg["col_date"], cfg["col_description"],
        cfg["col_value"], cfg["col_currency"], cfg["col_revision"],
    ]
    found_but_filled = False

    for row_num in sorted(row_by_num):
        if row_num in used_rows:
            continue
        row_elem = row_by_num[row_num]
        d_ref = f"{d_col}{row_num}"
        d_cell = next((c for c in row_elem if c.get("r") == d_ref), None)
        if d_cell is None:
            continue
        if _cell_text(d_cell, shared_strings).strip() != base_ctr_number:
            continue

        if allow_overwrite:
            return row_num, ""

        cells = {c.get("r"): c for c in row_elem.findall(f"{_M}c")}
        if all(_cell_is_empty(cells.get(f"{col}{row_num}")) for col in check_cols):
            return row_num, ""
        found_but_filled = True

    if found_but_filled:
        return None, f'a row with CTR number "{base_ctr_number}" exists but is already filled in'
    return None, f'no row found with CTR number "{base_ctr_number}" in column {d_col}'


def write_entries_fast(
    tracker_path: str | Path,
    entries: list[CTREntry],
    make_backup: bool = False,
    allow_overwrite: bool = False,
) -> tuple[list[tuple[str, int]], list[tuple[str, str]], Path | None]:
    """Writes each entry into the existing tracker row reserved for its
    CTR number (see module docstring), backs up if requested, and saves
    the workbook in place. Returns (written, skipped, backup_path):
    written is [(ctr_number, row), ...] for entries actually written;
    skipped is [(ctr_number, reason), ...] for entries that couldn't be
    placed. The file is only touched if at least one entry was written.

    allow_overwrite (off by default) lets a match land on a row whose
    core data fields already have something in them, instead of treating
    that as "already filled" and skipping it — use with care, since it
    can silently replace real data with no undo besides the backup."""
    if not entries:
        raise ValueError("No CTR entries to write.")
    if len(entries) > 10:
        raise ValueError("At most 10 CTRs can be written at once.")

    tracker_path = Path(tracker_path)
    cfg = CFG["ctr_tracker"]
    backup_path = backup_tracker(tracker_path) if make_backup else None

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

    written: list[tuple[str, int]] = []
    skipped: list[tuple[str, str]] = []
    used_rows: set[int] = set()

    for entry in entries:
        if not _activity_matches_location(entry.activity_type, entry.tracker_location):
            skipped.append((
                entry.ctr_number,
                f'CTR labor section says "{entry.activity_type} Activities" but the '
                f'selected Location is "{entry.tracker_location}" — resolve the '
                "mismatch before retrying"
            ))
            continue

        base = _normalize_ctr_number(entry.ctr_number)
        if not base:
            skipped.append((entry.ctr_number or "(blank CTR number)", "no CTR number to match on"))
            continue

        row, reason = _find_target_row(row_by_num, shared_strings, cfg, base, used_rows, allow_overwrite)
        if row is None:
            skipped.append((entry.ctr_number, reason))
            continue
        used_rows.add(row)

        row_elem = row_by_num[row]
        cells = {c.get("r"): c for c in row_elem.findall(f"{_M}c")}

        def _cell(col_letter: str, _row=row, _cells=cells, _row_elem=row_elem):
            return _get_or_create_cell(_row_elem, _cells, _row, col_letter, default_styles)

        for cfg_key, attr in _CORE_STRING_FIELDS.items():
            value = getattr(entry, attr)
            if value:
                _set_string_cell(_cell(cfg[cfg_key]), value)

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
            style = cfg["value_style_by_currency"].get(entry.currency)
            if style is not None:
                value_cell.set("s", str(style))
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

        written.append((entry.ctr_number, row))

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

    return written, skipped, backup_path
