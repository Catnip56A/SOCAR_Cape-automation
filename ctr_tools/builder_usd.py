"""
ctr_tools/builder_usd.py

Copies the USD CTR template and writes equipment + consumable rows.

Row/column positions are read from ctr_tools/template_config.json
(section "usd_template") so they can be adjusted without touching this
file when a new template version shifts the layout.

Default layout (217_USD template):

  Sheet "Main":
    E3  = CTR ref string
    O3  = job ref integer
    G112 = total_equipment
    G115 = total_consumables_raw
    E116 = markup_rate (fraction, e.g. 0.065 — the build_usd() markup_rate
           argument, written as a value so it stays visible/editable in
           Excel; defaults to usd_template.markup_rate if not given)
    G116 = consumables_markup (= total_consumables_raw * E116)
    G120 = total_consumables  (raw + markup)
    G126 = total_equipment   (summary row 4)
    G127 = total_consumables_raw (summary row 5)
    G130 = estimated_ctr_total_usd

    Each of these is a formula referencing its Pricing-sheet total
    directly, so they're correct whether or not the per-item lists below
    are written.

    Optional (usd_template.list_items_on_main, default false): when
    enabled, two independent item lists also get written, each growing
    downward inside its own section — "Plant & Equipment" and
    "Materials/Consumables & Others" — right before that section's own
    Total row; every fixed cell below a list then shifts down by however
    many extra rows it needed:
      C111, G111 = equipment list, one row per item (name, cost) — grows
                   into the "Plant & Equipment" block. Cells configurable
                   via cell_equip_list_name / cell_equip_list_cost. (G110,
                   the redundant mirror at the section header row, is left
                   untouched.)
      C119, G119 = consumables list, one row per item (name, cost) — grows
                   into the "Materials/Consumables & Others" block, below
                   the equipment list's own shift. Cells configurable via
                   cell_cons_list_name / cell_cons_list_cost.
    When disabled (the default), Main shows only the section totals above
    and per-item detail (description, qty, unit, rate, days, stock code)
    lives solely on the Pricing sheet — see below.

  Sheet "Pricing":
    Rows 3–6     : header block (client, ref, location, date, revision,
      scope) — each cell a formula mirroring its Main-sheet equivalent, so
      the two pages of one CTR always agree; see _mirror_pricing_header.
    Row 10       : equipment header
    Rows 11–210  : equipment data
      A=item#, B=desc, C=qty, D=unit, E=rate_per_day, F=days, G=total, H=counter, I=SC cost, J=stock_code
      E is either a numeric rate or the literal "NONRECHARG" for
      non-rechargeable items; G is guarded with IF(ISNUMBER(...)) so a
      "NONRECHARG" row totals 0 instead of an Excel #VALUE! error.
      I is a separate, internal-only figure — SAGE's local_expect_cost for
      that item times quantity, independent of G (what's actually billed,
      off the USD Pricebook) — see the "sage_cost" key on equip_rows below.
      It sits outside the sheet's print area (column G is the last printed
      column) and never reaches the PDF export, by construction of the
      template rather than anything this module does.
    Row 211      : G211=total_equipment, I211=SUM of the equipment I column
      (each row's SAGE cost × quantity) — no rechargeable/non-rechargeable
      split here; unlike consumables below, the template has no cells for
      one, and it wasn't asked for.

    Row 215      : consumables header
    Rows 216–365 : consumables data
      A=item#, B=desc, C=qty, D=unit, E=unit_price, F=total, H=counter, I=SC cost, J=product_code
      E is either a numeric price or the literal "NONRECHARG" for
      non-rechargeable items; F is guarded with IF(ISNUMBER(...)) so a
      "NONRECHARG" row totals 0 instead of an Excel #VALUE! error.
      I is the same kind of internal-only SAGE-cost-×-quantity figure as
      equipment's I column above — see consump_rows' "sage_cost" key below.
      Consumables' own price (E) already equals SAGE's local_expect_cost
      when rechargeable, but for a non-rechargeable row E shows the
      "NONRECHARG" sentinel instead of the real number, so I still needs
      its own independent value to stay populated on those rows too.
    Row 366      : F366=total_consumables_raw
    Row 367      : I367=Rechargeable SC Mat Cost total (SUM(I) minus the
      non-rechargeable total below)
    Row 368      : I368=Non-Rechargeable SC Mat Cost total (SUMIF on column
      E's "NONRECHARG" text)
    Row 369      : I369=Total SC Mat Cost (SUM of the whole I column)
      Rows 367–369 are fixed template cells (already labelled "Rechargeable:"
      / "Non-Rechargeable:" / "Total:" in column F) — not something this
      module creates. The two GM% rows immediately below them (370–371) are
      deliberately left untouched.

  Editable header fields (rows 3–6, shared layout with AZN template):
    B3 = Client, C3 = Sub-Client, B4 = Location, E4 = Date,
    G4 = Contract No, E5 = Revision, A6 = Scope / description (merged A6:G6),
    C1 = Comments, carried over from the CTR Request's own Comments box

Input values (description, quantity, rate, price, etc.) are written as
plain Python values. Anything derived from another cell — row totals,
section totals, the Main-sheet item lists, and the summary block — is
written as an Excel formula referencing that cell (including cross-sheet
references from "Main" to "Pricing"), so editing an input in Excel
recalculates its dependents. This only matters for the .xlsx output; the
PDF export is a flat rendering, so formulas there simply show their
last-calculated value.
"""

from __future__ import annotations

import logging
import shutil
from copy import copy
from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Alignment
from openpyxl.utils import column_index_from_string as _col_idx
from openpyxl.utils import get_column_letter as _col_letter
from openpyxl.worksheet.properties import PageSetupProperties

from ctr_tools.config import CFG
from ctr_tools.naming import ctr_output_filename

log = logging.getLogger(__name__)

_usd = CFG["usd_template"]

_EQUIP_START = _usd["equip_data_start"]
_EQUIP_END   = _usd["equip_data_end"]   # default clear range; rows beyond here still write

_CONS_START  = _usd["cons_data_start"]
_CONS_END    = _usd["cons_data_end"]

_DEFAULT_MARKUP_RATE = _usd["markup_rate"]   # used when build_usd() isn't given an override

_LIST_ITEMS_ON_MAIN = bool(_usd.get("list_items_on_main", False))

# Pricing-sheet column positions for section totals (template structure constants)
_EQUIP_TOTAL_COL  = 7   # G — row total
_EQUIP_TOTAL_COL2 = 9   # I — SC cost total
_CONS_TOTAL_COL   = 6   # F — row total
_CONS_TOTAL_COL2  = 9   # I — SC cost total (Rechargeable/Non-Rechargeable/Total breakdown)
_RATE_PRICE_COL   = 5   # E — numeric rate/price, or the literal "NONRECHARG" sentinel

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y")

_NONRECHARG = "NONRECHARG"


def _cell_row_col(addr: str) -> tuple[int, int]:
    """Convert a cell address like 'G111' to (row=111, col=7)."""
    col_str = "".join(c for c in addr if c.isalpha())
    row_str = "".join(c for c in addr if c.isdigit())
    return int(row_str), _col_idx(col_str)


# (config key, column holding the row's label, text that label must contain).
# Every cell listed is written by build_usd at a fixed address; the label
# beside it proves the template really has that row there.
_LAYOUT_CHECKS = (
    ("cell_total_equip_2",             "A", "Total Plant & Equipment"),
    ("cell_total_cons_raw",            "C", "General Consumables"),
    ("cell_cons_markup_rate",          "C", "Mark up (For Consumables)"),
    ("cell_total_cons_markup",         "C", "Mark up (For Consumables)"),
    ("cell_total_cons_transport",      "C", "Transportation"),
    ("cell_total_cons_services_markup", "C", "Mark up (for services)"),
    ("cell_total_cons",                "A", "Total Material/Consumables/others"),
    ("cell_summary_project_support",   "B", "Total Project Support"),
    ("cell_summary_offshore",          "B", "Total Offshore Activities"),
    ("cell_summary_other",             "B", "Total Other Activities"),
    ("cell_summary_equip",             "B", "Total Plant & Equipment"),
    ("cell_summary_cons_raw",          "B", "Total Material/Consumables/others"),
    ("cell_contingency",               "B", "Contingency"),
    ("cell_summary_grand",             "A", "Estimated CTR Total"),
)


def _validate_template_layout(ws, template_path) -> None:
    """
    Check the loaded USD template's Main sheet matches the layout
    template_config.json describes, before anything is written. Cells are
    addressed by fixed position, so a template saved in a different layout
    would otherwise fail midway with openpyxl's "'MergedCell' object
    attribute 'value' is read-only" or, worse, write totals into the wrong
    rows. Raises ValueError listing every mismatch found.
    """
    problems: list[str] = []
    for key, label_col, expected in _LAYOUT_CHECKS:
        addr = _usd[key]
        row, col = _cell_row_col(addr)
        rng = next(
            (r for r in ws.merged_cells.ranges
             if r.min_row <= row <= r.max_row and r.min_col <= col <= r.max_col),
            None,
        )
        if rng is not None and (rng.min_row, rng.min_col) != (row, col):
            problems.append(f"'{expected}' value expected at {addr}, found merged cell {rng.coord}")
            continue
        label = ws[f"{label_col}{row}"].value
        if not (isinstance(label, str) and expected.lower() in label.lower()):
            problems.append(f"'{expected}' expected at {label_col}{row}, found {label!r}")

    if problems:
        problems = list(dict.fromkeys(problems))
        if len(problems) > 4:
            problems = problems[:4] + [f"{len(problems) - 4} more"]
        msg = (
            f"USD template layout doesn't match template_config.json "
            f"({'; '.join(problems)}). Is {Path(template_path).name} an "
            f"outdated template? Replace it with the current USD_TEMPLATE.xlsx."
        )
        log.error("%s [template: %s]", msg, template_path)
        raise ValueError(msg)


def _shift_cell(addr: str, row_shift: int) -> str:
    """Return addr with its row number increased by row_shift."""
    row, col = _cell_row_col(addr)
    return f"{_col_letter(col)}{row + row_shift}"


def _anchor_cell(ws, row: int, col: int):
    """
    The cell that actually stores what's displayed at (row, col).

    openpyxl represents every cell of a merged range except its top-left as
    a read-only MergedCell — assigning to one raises "'MergedCell' object
    attribute 'value' is read-only". Templates are re-merged by hand
    between revisions, so any cell this module addresses by fixed position
    can quietly end up inside somebody else's merge. Returns the range's
    anchor in that case (which is where the value visibly lands anyway), or
    None if the merge can't be resolved.
    """
    cell = ws.cell(row=row, column=col)
    if not isinstance(cell, MergedCell):
        return cell
    for rng in ws.merged_cells.ranges:
        if rng.min_row <= row <= rng.max_row and rng.min_col <= col <= rng.max_col:
            return ws.cell(row=rng.min_row, column=rng.min_col)
    return None


def _write_addr(ws, addr: str, value) -> None:
    """Write `value` at `addr`, following a merge to the cell that holds it."""
    cell = _anchor_cell(ws, *_cell_row_col(addr))
    if cell is not None:
        cell.value = value


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


def _write_item_list(
    ws, list_row: int, name_col: int, cost_col: int, items: list[tuple[str, float]],
) -> int:
    """
    Writes `items` as one row per item starting at list_row (name in
    name_col, cost in cost_col), inserting extra rows below the first item
    if there's more than one — mirrors how the Pricing sheet sections grow.
    Returns the number of extra rows inserted (0 if 0 or 1 items), so the
    caller can shift everything below by that amount.
    """
    shift = max(0, len(items) - 1)
    if shift:
        _insert_rows_preserving_merges(ws, list_row + 1, shift)
        for i in range(shift):
            _copy_row_format(ws, list_row, list_row + 1 + i, max(name_col, cost_col))
    for i, (name, cost) in enumerate(items):
        r = list_row + i
        ws.cell(row=r, column=name_col).value = name
        ws.cell(row=r, column=cost_col).value = cost
    return shift


def _copy_row_format(ws, src_row: int, dst_row: int, col_count: int) -> None:
    """Copy cell formatting (borders, fill, font, alignment) from one row to another."""
    src_dim = ws.row_dimensions.get(src_row)
    if src_dim and src_dim.height:
        ws.row_dimensions[dst_row].height = src_dim.height
    for col in range(1, col_count + 1):
        src = ws.cell(row=src_row, column=col)
        dst = ws.cell(row=dst_row, column=col)
        if src.has_style:
            dst.font          = copy(src.font)
            dst.border        = copy(src.border)
            dst.fill          = copy(src.fill)
            dst.number_format = src.number_format
            dst.alignment     = copy(src.alignment)


def _clear_placeholder_cells(ws, texts) -> int:
    """
    Blank every cell whose entire value is one of `texts`.

    The templates carry filler strings in their helper cells — "AA" all the
    way down the column-H mirror on the Main sheets, "aa" beside the
    Pricing sheet's margin block. They mean nothing; they normally go
    unnoticed because column H is hidden and those Pricing rows fall
    outside the print area, so they reappear as soon as a template is saved
    with the column shown. Since a generated CTR goes to a client, they're
    stripped rather than passed on.

    Matching is on the whole trimmed value, so the genuine content sharing
    those columns — the "=A10"/"=IF(B8=...)" mirror formulas, the real
    labels — is never touched. Returns how many cells were cleared.
    """
    wanted = {str(t).strip() for t in (texts or []) if str(t).strip()}
    if not wanted:
        return 0
    cleared = 0
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and cell.value.strip() in wanted:
                cell.value = None
                cleared += 1
    return cleared


def _extend_print_area(ws, extra_rows: int) -> None:
    """
    Push the sheet's print area down by `extra_rows`.

    The print area is a fixed range saved in the template ("$A$1:$G$77")
    and openpyxl does not move it when rows are inserted — so every row a
    generated CTR adds pushes that much of the document out the bottom of
    what actually prints. It is silent in Excel and only shows up in the
    PDF, where the tail of the document (the Estimated CTR Total line and
    the signature block) simply isn't there.

    Left alone if the sheet has no print area, or has a multi-range one
    this can't safely reason about — better to print the template's range
    than to guess wrong about a layout that isn't the one this understands.
    """
    area = ws.print_area
    if not area or extra_rows <= 0:
        return
    if isinstance(area, (list, tuple)):
        if len(area) != 1:
            return
        area = area[0]
    if "," in area or ":" not in area:
        return

    start, end = area.rsplit(":", 1)
    digits, cut = "", len(end)
    while cut > 0 and end[cut - 1].isdigit():
        cut -= 1
        digits = end[cut] + digits
    if not digits:
        return
    ws.print_area = f"{start}:{end[:cut]}{int(digits) + extra_rows}"


def _fit_to_page_width(ws) -> None:
    """See builder_azn._fit_to_page_width — same fix, forces one page wide
    instead of relying on the template's fixed print scale, plus a small
    side margin so the fitted content doesn't run edge-to-edge."""
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    if ws.sheet_properties.pageSetUpPr is None:
        ws.sheet_properties.pageSetUpPr = PageSetupProperties()
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = 0.3
    ws.page_margins.right = 0.3


def _parse_date(value):
    """Best-effort string → datetime conversion; returns value unchanged if unparseable."""
    if isinstance(value, str):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return value


def _set_cell(ws, row: int, col: int, value) -> None:
    """Write value at (row, col), skipping non-top-left merged-cell slots.

    Used for the bulk section writes, where the anchor of any merge inside
    the block is visited by the loop in its own right — so skipping is both
    safe and avoids clearing a merge that reaches outside the block."""
    cell = ws.cell(row=row, column=col)
    if not isinstance(cell, MergedCell):
        cell.value = value


def _apply_reference_style(ws, row: int, col: int, ref_cell) -> None:
    """Same merged-cell guard as _set_cell, for overriding a cell's display
    style (number format, font, alignment) independently of its value —
    copied from a known-good reference cell rather than trusted from
    whatever the cell being written already has.

    Guards against a one-off styling mistake on a single template row
    silently propagating onto every row generation writes into (confirmed
    on this repo's own USD_TEMPLATE.xlsx: the very last row of both the
    equipment and consumables blocks has a different currency format and
    centre alignment instead of the rest of the section's "$" format and
    right alignment — _copy_row_format would otherwise carry that onto any
    overflow row inserted past it, and _set_cell never touches style at
    all, so even a pre-existing template row keeps whatever it already had
    forever)."""
    cell = ws.cell(row=row, column=col)
    if not isinstance(cell, MergedCell):
        cell.number_format = ref_cell.number_format
        cell.font          = copy(ref_cell.font)
        cell.alignment     = copy(ref_cell.alignment)


def _safe_float(v, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if f == f else default   # NaN guard
    except (TypeError, ValueError):
        return default


# Row-height estimate for wrapped comment text. Deliberately rough and
# deliberately pessimistic: a column's width is expressed in characters of
# the workbook's *default* font, so measuring a smaller cell font against it
# under-counts how many characters fit on a line, over-counts the lines, and
# leaves the row slightly taller than strictly needed. A little too tall
# wastes a few points of page; too short silently cuts the comment off.
_DEFAULT_ROW_HEIGHT     = 15.0
_DEFAULT_FONT_SIZE      = 11.0
_LINE_HEIGHT_FACTOR     = 1.35    # point size -> line box height
_MAX_COMMENT_ROW_HEIGHT = 200.0


def _wrapped_row_height(text: str, col_width, font_size, base_height: float) -> float:
    """Height in points for `text` wrapped inside a column `col_width` wide."""
    chars_per_line = max(10, int(col_width or _DEFAULT_ROW_HEIGHT))
    lines = sum(
        max(1, -(-len(para) // chars_per_line))          # ceil division
        for para in str(text).split("\n")
    )
    needed = max(1, lines) * (font_size or _DEFAULT_FONT_SIZE) * _LINE_HEIGHT_FACTOR + 4
    return max(base_height, min(needed, _MAX_COMMENT_ROW_HEIGHT))


def _write_comments(ws, addr: str, text: str) -> None:
    """
    Write the CTR Request's comments into `addr`, forcing the cell to read
    horizontally and wrap.

    Both templates style that cell with textRotation=90 and no wrap — it
    sits in the tall banner row beside the standing PO note, where it was
    only ever meant to hold a short sideways tag. Left as-is, a real
    sentence of comments comes out running vertically down the page and
    clipped at the column edge. The row is also grown, if needed,
    to fit however many lines the wrapped text takes — never shrunk, so the
    PO note sharing row 1 keeps its space, and never touched at all when
    there are no comments.
    """
    # The comments cell is addressed by fixed position too, so follow a
    # merge to whatever cell actually holds it (see _anchor_cell).
    cell = _anchor_cell(ws, *_cell_row_col(addr))
    if cell is None:
        return
    cell.value = text
    cell.alignment = Alignment(
        horizontal="left", vertical="center", wrap_text=True, textRotation=0)
    if not text:
        return

    # Give the row an explicit height tall enough for the wrapped text,
    # never shorter than whatever the template set (row 1 also carries the
    # PO note, which needs its own space). Clearing the height so the row
    # auto-fits instead does NOT work here: Excel would auto-fit on open,
    # but LibreOffice — which renders the PDF export — just draws the row at
    # its default height, and the whole banner row came out as a sliver.
    row = cell.row
    col = cell.column_letter
    base  = ws.row_dimensions[row].height or _DEFAULT_ROW_HEIGHT
    width = ws.column_dimensions[col].width if col in ws.column_dimensions else None
    ws.row_dimensions[row].height = _wrapped_row_height(
        text, width, cell.font.size, base)


def _mirror_pricing_header(ws_p, main_sheet: str) -> None:
    """
    Point the Pricing sheet's header block at the Main sheet's, by formula.

    Pricing repeats the CTR's identity — client, ref, location, date,
    revision, scope — at the top of its own page. Nothing used to write it,
    so those cells kept whatever the template file happened to hold; on a
    template saved from a previous CTR (which is how these templates are
    made in practice) page 2 of every generated document carried the
    *previous* job's CTR number, client and site while page 1 showed the
    right ones.

    Mirroring by formula rather than copying the values means the two pages
    of one CTR can't drift apart, and that editing a header field on Main
    in Excel updates Pricing with it — the same convention the rest of this
    module follows for anything derived from another cell. Each is guarded
    so an empty source cell shows as empty instead of Excel's bare 0.
    """
    def _mirror(pricing_key: str, main_key: str) -> None:
        src = f"'{main_sheet}'!{_usd[main_key]}"
        _write_addr(ws_p, _usd[pricing_key], f'=IF({src}="","",{src})')

    _mirror("pricing_cell_client",     "cell_client")
    _mirror("pricing_cell_sub_client", "cell_sub_client")
    _mirror("pricing_cell_ctr_ref",    "cell_ctr_ref")
    _mirror("pricing_cell_location",   "cell_location")
    _mirror("pricing_cell_date",       "cell_date")
    _mirror("pricing_cell_revision",   "cell_revision")
    _mirror("pricing_cell_scope",      "cell_scope")


def build_usd(
    equip_rows: list[dict],
    consump_rows: list[dict],
    template_path: str | Path,
    output_dir: str | Path,
    job_ref: str,
    header: dict | None = None,
    markup_rate: float | None = None,
) -> Path:
    """
    equip_rows: list of dicts with keys:
        description (str), quantity (float), unit (str),
        rate_per_day (float or 'NONRECHARG'), days (float, or "" to leave
        the cell blank for a line whose duration isn't known yet),
        stock_code (str), sage_cost (float — SAGE's local_expect_cost for
        this item, independent of rate_per_day; feeds the Pricing sheet's
        internal "Total SC Eq Cost" column only, defaults to 0.0)

    consump_rows: list of dicts with keys:
        long_description (str), local_expect_cost (float or 'NONRECHARG'),
        unit_code (str), product (str), quantity (float), sage_cost (float
        — SAGE's local_expect_cost for this item; unlike local_expect_cost
        above, always the real number even on a non-rechargeable row where
        local_expect_cost has been replaced by 'NONRECHARG'; feeds the
        Pricing sheet's internal "Total SC Mat Cost" column, defaults to 0.0)

    header: optional dict with keys client, sub_client, location, scope,
        date, contract_no, revision, comments. Only non-empty values
        overwrite the corresponding template cell — leaving a field blank
        preserves whatever the template already has, except contract_no
        and comments which are always written. location and scope are also
        used to build the output filename (see ctr_tools.naming).

    markup_rate: consumables markup as a fraction (e.g. 0.065 for 6.5%).
        Defaults to usd_template.markup_rate from template_config.json
        when omitted — callers (e.g. the UI's per-preset markup field)
        override it here rather than editing the config file.

    Returns the path to the saved xlsx file.
    """
    template_path = Path(template_path)
    output_dir    = Path(output_dir)
    markup_rate   = _DEFAULT_MARKUP_RATE if markup_rate is None else markup_rate

    if not template_path.is_file():
        raise ValueError(f"USD Template not found: {template_path}")

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Cannot create output folder {output_dir}: {e}") from e

    out_path = output_dir / ctr_output_filename(
        job_ref, "USD", (header or {}).get("location", ""), (header or {}).get("scope", ""))
    try:
        shutil.copy2(template_path, out_path)
    except PermissionError as e:
        raise ValueError(
            f"Cannot write {out_path} — it may be open in Excel in another "
            f"window. Close it there and try again."
        ) from e
    except OSError as e:
        raise ValueError(f"Could not copy USD template to {out_path}: {e}") from e

    main_sheet    = _usd["main_sheet"]
    pricing_sheet = _usd["pricing_sheet"]
    try:
        wb    = openpyxl.load_workbook(out_path, keep_vba=False)
        ws_m  = wb[main_sheet]
        ws_p  = wb[pricing_sheet]
    except KeyError as e:
        raise ValueError(
            f'USD Template {template_path} is missing a "{main_sheet}" or '
            f'"{pricing_sheet}" sheet. Is this the right template?'
        ) from e
    except Exception as e:
        raise ValueError(f"Could not read USD Template {template_path}: {e}") from e

    try:
        _validate_template_layout(ws_m, template_path)
    except ValueError:
        # Don't leave a copy of the unmodified template in the output folder
        # looking like a finished CTR.
        wb.close()
        out_path.unlink(missing_ok=True)
        raise

    # ── Main sheet: header ────────────────────────────────────────────────────
    _write_addr(ws_m, _usd["cell_ctr_ref"], f"CTR-26-{job_ref} USD")
    _write_addr(ws_m, _usd["cell_job_ref"], int(job_ref))

    if header:
        if header.get("client"):
            _write_addr(ws_m, _usd["cell_client"], header["client"])
        if header.get("sub_client"):
            _write_addr(ws_m, _usd["cell_sub_client"], header["sub_client"])
        if header.get("location"):
            _write_addr(ws_m, _usd["cell_location"], header["location"])
        if header.get("date"):
            _write_addr(ws_m, _usd["cell_date"], _parse_date(header["date"]))
        # Contract No and Comments are always written from the UI field,
        # even blank — unlike the other header fields here, a stale value
        # left over from the template file is actively wrong for this job
        # rather than a harmless default, so a blank UI field must blank
        # the cell instead of silently preserving whatever the template had.
        _write_addr(ws_m, _usd["cell_contract_no"], header.get("contract_no", ""))
        _write_comments(ws_m, _usd["cell_comments"], header.get("comments", ""))
        if header.get("revision") not in (None, ""):
            _write_addr(ws_m, _usd["cell_revision"], header["revision"])
        if header.get("scope"):
            _write_addr(ws_m, _usd["cell_scope"], header["scope"])

    # ── Pricing sheet: header block, mirrored from Main ───────────────────────
    _mirror_pricing_header(ws_p, main_sheet)

    _COLS = 10   # columns A–J used by data rows

    # ── Insert extra rows before writing so sections don't overwrite each other ─
    equip_capacity = _EQUIP_END - _EQUIP_START + 1
    equip_extra    = max(0, len(equip_rows) - equip_capacity)
    if equip_extra:
        _insert_rows_preserving_merges(ws_p, _EQUIP_END + 1, equip_extra)
        for i in range(equip_extra):
            _copy_row_format(ws_p, _EQUIP_END, _EQUIP_END + 1 + i, _COLS)

    # Consumables section has shifted down by equip_extra
    cons_start = _CONS_START + equip_extra
    cons_end   = _CONS_END   + equip_extra

    cons_capacity = cons_end - cons_start + 1
    cons_extra    = max(0, len(consump_rows) - cons_capacity)
    if cons_extra:
        _insert_rows_preserving_merges(ws_p, cons_end + 1, cons_extra)
        for i in range(cons_extra):
            _copy_row_format(ws_p, cons_end, cons_end + 1 + i, _COLS)

    # ── Pricing sheet: clear and write equipment rows ─────────────────────────
    equip_eff_end   = _EQUIP_END + equip_extra
    equip_clear_end = max(equip_eff_end, _EQUIP_START + len(equip_rows) - 1) if equip_rows else equip_eff_end
    for row in range(_EQUIP_START, equip_clear_end + 1):
        for col in range(1, _COLS + 1):
            _set_cell(ws_p, row, col, None)

    # Items are (name, cost) pairs, where cost is a formula string referencing
    # this same row's total on the Pricing sheet — not a duplicated literal —
    # so the Main-sheet list stays in sync with the Pricing sheet in Excel.
    equip_cost_list: list[tuple[str, str]] = []
    cons_cost_list:  list[tuple[str, str]] = []

    # Reference style for the Rate/Price column (E), read once from each
    # section's first row rather than trusted from whatever's already on
    # the row being written — see _apply_reference_style.
    equip_rate_ref = ws_p.cell(row=_EQUIP_START, column=_RATE_PRICE_COL)
    cons_price_ref = ws_p.cell(row=cons_start,   column=_RATE_PRICE_COL)

    for i, er in enumerate(equip_rows):
        r         = _EQUIP_START + i
        item_no   = i + 1
        desc      = str(er.get("description", ""))
        qty       = _safe_float(er.get("quantity", 1), 1.0)
        unit      = str(er.get("unit", "DAY"))
        rate_raw  = er.get("rate_per_day", 0)
        # A blank duration is written as a blank cell, not as a default —
        # see the Days handling in window._generate. Excel reads the empty
        # cell as 0 in this row's total formula, so the line still shows
        # its description, quantity and unit while totalling 0.00.
        days_raw  = er.get("days", 30)
        days      = "" if days_raw in (None, "") else _safe_float(days_raw, 30.0)
        stock     = str(er.get("stock_code", ""))
        sage_cost = round(_safe_float(er.get("sage_cost", 0), 0.0), 6)

        # A non-rechargeable item carries the "NONRECHARG" sentinel instead of
        # a numeric rate (set upstream once the pricebook/DB match marks it
        # non-rechargeable) — preserved verbatim so it prints instead of a
        # misleading 0.00. Any other non-numeric junk still collapses to 0.0.
        try:
            rate: float | str = float(rate_raw)
        except (TypeError, ValueError):
            rate = _NONRECHARG if str(rate_raw).strip().upper() == _NONRECHARG else 0.0

        if desc:
            equip_cost_list.append((desc, f"='{pricing_sheet}'!G{r}"))

        _set_cell(ws_p, r, 1,  item_no)
        _set_cell(ws_p, r, 2,  desc)
        _set_cell(ws_p, r, 3,  qty)
        _set_cell(ws_p, r, 4,  unit)
        _set_cell(ws_p, r, 5,  rate)
        _apply_reference_style(ws_p, r, 5, equip_rate_ref)
        _set_cell(ws_p, r, 6,  days if days != "" else None)
        # Guarded so a "NONRECHARG" rate totals 0 instead of erroring (#VALUE!).
        _set_cell(ws_p, r, 7,  f"=IF(ISNUMBER(E{r}),C{r}*E{r}*F{r},0)")
        _set_cell(ws_p, r, 8,  f"=A{r}")
        # Internal-only: SAGE's local_expect_cost × quantity, independent of
        # this row's own billed total (G) — see the module docstring.
        _set_cell(ws_p, r, 9,  f"=C{r}*{sage_cost}")
        _set_cell(ws_p, r, 10, stock)

    # Rows left blank because there were fewer items than the section's
    # fixed capacity are hidden rather than deleted — deleting would desync
    # the SUM formula's row references below, while hiding is purely
    # cosmetic and both Excel and LibreOffice's PDF export skip hidden rows
    # when printing, so the exported PDF shows only the rows that actually
    # have data.
    #
    # Only the unused rows are contracted, and a row that does have data is
    # explicitly expanded — a template is made by saving a previous CTR, so
    # it arrives with that job's unused rows already hidden, and without
    # this a row written into one of them would be filled in correctly but
    # stay invisible in both Excel and the PDF. A section that uses its
    # whole capacity has nothing to contract and neither loop does anything.
    for row in range(_EQUIP_START, _EQUIP_START + len(equip_rows)):
        ws_p.row_dimensions[row].hidden = False
    for row in range(_EQUIP_START + len(equip_rows), equip_eff_end + 1):
        ws_p.row_dimensions[row].hidden = True

    # The total always sits immediately after the section's full capacity
    # block (equip_eff_end), not after however many rows were actually
    # written — otherwise a request with fewer rows than the template's
    # capacity writes the total into a blank filler row instead of the
    # fixed "Total Plant & Equipment" row.
    equip_total_row = equip_eff_end + 1
    equip_total_col_letter = _col_letter(_EQUIP_TOTAL_COL)
    _set_cell(ws_p, equip_total_row, _EQUIP_TOTAL_COL,
              f"=SUM({equip_total_col_letter}{_EQUIP_START}:{equip_total_col_letter}{equip_eff_end})")
    equip_sc_col_letter = _col_letter(_EQUIP_TOTAL_COL2)
    _set_cell(ws_p, equip_total_row, _EQUIP_TOTAL_COL2,
              f"=SUM({equip_sc_col_letter}{_EQUIP_START}:{equip_sc_col_letter}{equip_eff_end})")

    # ── Pricing sheet: clear and write consumables rows ───────────────────────
    cons_eff_end   = cons_end + cons_extra
    cons_clear_end = max(cons_eff_end, cons_start + len(consump_rows) - 1) if consump_rows else cons_eff_end
    for row in range(cons_start, cons_clear_end + 1):
        for col in range(1, _COLS + 1):
            _set_cell(ws_p, row, col, None)

    for i, cr in enumerate(consump_rows):
        r         = cons_start + i
        item_no   = i + 1
        desc      = str(cr.get("long_description", ""))
        qty       = _safe_float(cr.get("quantity", 1), 1.0)
        unit      = str(cr.get("unit_code", "EA"))
        price_raw = cr.get("local_expect_cost", 0)
        product   = str(cr.get("product", ""))
        sage_cost = round(_safe_float(cr.get("sage_cost", 0), 0.0), 6)

        # Same "NONRECHARG" sentinel handling as equipment above — preserved
        # verbatim so it prints instead of a misleading 0.00.
        try:
            price: float | str = float(price_raw)
        except (TypeError, ValueError):
            price = _NONRECHARG if str(price_raw).strip().upper() == _NONRECHARG else 0.0

        if desc:
            cons_cost_list.append((desc, f"='{pricing_sheet}'!F{r}"))

        _set_cell(ws_p, r, 1,  item_no)
        _set_cell(ws_p, r, 2,  desc)
        _set_cell(ws_p, r, 3,  qty)
        _set_cell(ws_p, r, 4,  unit)
        _set_cell(ws_p, r, 5,  price)
        _apply_reference_style(ws_p, r, 5, cons_price_ref)
        # Guarded so a "NONRECHARG" price totals 0 instead of erroring (#VALUE!).
        _set_cell(ws_p, r, 6,  f"=IF(ISNUMBER(E{r}),C{r}*E{r},0)")
        _set_cell(ws_p, r, 8,  f"=A{r}")
        # Internal-only: SAGE's local_expect_cost × quantity, independent of
        # this row's own billed total (F) — see the module docstring. Unlike
        # F, this stays populated on a "NONRECHARG" row too.
        _set_cell(ws_p, r, 9,  f"=C{r}*{sage_cost}")
        _set_cell(ws_p, r, 10, product)

    # Same expand-used / contract-unused pass as the equipment section above.
    for row in range(cons_start, cons_start + len(consump_rows)):
        ws_p.row_dimensions[row].hidden = False
    for row in range(cons_start + len(consump_rows), cons_eff_end + 1):
        ws_p.row_dimensions[row].hidden = True

    # Same fixed-position rule as equip_total_row above.
    cons_total_row = cons_eff_end + 1
    cons_total_col_letter = _col_letter(_CONS_TOTAL_COL)
    _set_cell(ws_p, cons_total_row, _CONS_TOTAL_COL,
              f"=SUM({cons_total_col_letter}{cons_start}:{cons_total_col_letter}{cons_eff_end})")

    # Rechargeable / Non-Rechargeable / Total SC Mat Cost breakdown — three
    # fixed template rows immediately below the total row (already labelled
    # "Rechargeable:" / "Non-Rechargeable:" / "Total:" in column F; this
    # module only ever writes column I). Non-Rechargeable is a SUMIF keyed
    # on column E's "NONRECHARG" text — the same sentinel every row's price
    # cell already carries — and Rechargeable is the remainder, so the two
    # always add up to the Total exactly.
    cons_sc_col_letter    = _col_letter(_CONS_TOTAL_COL2)
    cons_price_col_letter = _col_letter(_RATE_PRICE_COL)
    cons_non_recharge_row = cons_total_row + 2
    cons_sc_total_row     = cons_total_row + 3
    cons_recharge_row     = cons_total_row + 1
    _set_cell(ws_p, cons_non_recharge_row, _CONS_TOTAL_COL2,
              f'=SUMIF({cons_price_col_letter}{cons_start}:{cons_price_col_letter}{cons_eff_end},'
              f'"{_NONRECHARG}",{cons_sc_col_letter}{cons_start}:{cons_sc_col_letter}{cons_eff_end})')
    _set_cell(ws_p, cons_sc_total_row, _CONS_TOTAL_COL2,
              f"=SUM({cons_sc_col_letter}{cons_start}:{cons_sc_col_letter}{cons_eff_end})")
    _set_cell(ws_p, cons_recharge_row, _CONS_TOTAL_COL2,
              f"={cons_sc_col_letter}{cons_sc_total_row}-{cons_sc_col_letter}{cons_non_recharge_row}")

    # The sheet's own furniture is never spare capacity, so it is never
    # contracted: the two section totals, and everything sitting between
    # the equipment block and the consumables block — the spacer rows, the
    # "Consumables & Materials" heading and its column headers. The
    # template arrives with that heading row hidden (a previous CTR was
    # saved that way), which prints a consumables table with no title above
    # it while every row around it shows.
    for row in range(equip_total_row, cons_start):
        ws_p.row_dimensions[row].hidden = False
    for row in range(cons_total_row, cons_sc_total_row + 1):
        ws_p.row_dimensions[row].hidden = False

    # ── Main sheet: item lists (optional, usd_template.list_items_on_main).
    #    When enabled, one row per item (name/cost) is written per section,
    #    growing downward from cell_equip_list_name/_cost and
    #    cell_cons_list_name/_cost respectively — inside the "Plant &
    #    Equipment" and "Materials/Consumables & Others" blocks, right
    #    before each one's own Total row. The consumables list's configured
    #    row is shifted down by equip_shift first, since the equipment list
    #    above it already moved it when it grew.
    #    When disabled, Main is left untouched here — per-item detail lives
    #    only on the Pricing sheet, and the section totals below are
    #    unaffected either way (each is a direct formula to its
    #    Pricing-sheet total, not a sum of these Main-sheet rows). ────────
    if _LIST_ITEMS_ON_MAIN:
        equip_list_row, equip_name_col = _cell_row_col(_usd["cell_equip_list_name"])
        _,              equip_cost_col = _cell_row_col(_usd["cell_equip_list_cost"])
        equip_shift = _write_item_list(
            ws_m, equip_list_row, equip_name_col, equip_cost_col, equip_cost_list)

        cons_list_row, cons_name_col = _cell_row_col(_usd["cell_cons_list_name"])
        _,             cons_cost_col = _cell_row_col(_usd["cell_cons_list_cost"])
        cons_shift = _write_item_list(
            ws_m, cons_list_row + equip_shift, cons_name_col, cons_cost_col, cons_cost_list)
    else:
        equip_shift = 0
        cons_shift  = 0

    total_shift = equip_shift + cons_shift

    # ── Main sheet: totals — cells between the two lists shift by
    #    equip_shift only; everything at/after the consumables list shifts
    #    by both. Every cell here is a formula: a direct cross-sheet
    #    reference to its Pricing-sheet total, or an arithmetic combination
    #    of other Main-sheet cells already addressed above — never a
    #    duplicated literal. The Summary block keeps equipment and
    #    consumables as separate line items (no double-counting). ──────────
    total_equip_addr = _shift_cell(_usd["cell_total_equip_2"], equip_shift)
    ws_m[total_equip_addr] = f"='{pricing_sheet}'!G{equip_total_row}"

    cons_raw_addr = _shift_cell(_usd["cell_total_cons_raw"], equip_shift)
    ws_m[cons_raw_addr] = f"='{pricing_sheet}'!F{cons_total_row}"

    # The rate itself is written into its own visible template cell (E116 by
    # default, the "Mark up (For Consumables)" row) rather than baked into
    # the formula as a literal, so it still shows correctly if opened in
    # Excel and can be hand-tweaked there like any other input cell.
    cons_markup_rate_addr = _shift_cell(_usd["cell_cons_markup_rate"], equip_shift)
    ws_m[cons_markup_rate_addr] = markup_rate

    cons_markup_addr = _shift_cell(_usd["cell_total_cons_markup"], equip_shift)
    ws_m[cons_markup_addr] = f"={cons_raw_addr}*{cons_markup_rate_addr}"

    # Transportation and Customs Clearance, and its own markup — both
    # editable template inputs (always 0 for a USD CTR today, since
    # transport is priced on the AZN document, but still real line items
    # shown in the Materials/Consumables & Others block and left alone
    # here rather than written) — must still be included in the section's
    # own Total below, the same as General Consumables and its markup are.
    cons_transport_addr = _shift_cell(_usd["cell_total_cons_transport"], equip_shift)
    cons_services_markup_addr = _shift_cell(_usd["cell_total_cons_services_markup"], equip_shift)

    # Total Material/Consumables/others = every line item shown above it in
    # this block. The template's own formula here (=G115+G116) silently
    # dropped Transportation and its markup — harmless while both are 0,
    # but wrong the moment either is hand-edited.
    cons_total_addr = _shift_cell(_usd["cell_total_cons"], total_shift)
    ws_m[cons_total_addr] = (
        f"={cons_raw_addr}+{cons_markup_addr}+{cons_transport_addr}+{cons_services_markup_addr}"
    )

    summary_equip_addr = _shift_cell(_usd["cell_summary_equip"], total_shift)
    ws_m[summary_equip_addr] = f"={total_equip_addr}"

    # Summary row 5 mirrors the section's own Total line exactly, so it can
    # never disagree with "Total Material/Consumables/others" shown just
    # above the Summary block — the template's own formula here referenced
    # G115 (raw consumables only), silently dropping markup and transport
    # from every generated CTR's Summary table.
    summary_cons_addr = _shift_cell(_usd["cell_summary_cons_raw"], total_shift)
    ws_m[summary_cons_addr] = f"={cons_total_addr}"

    # The other four Summary rows (Project Support, Offshore Activities,
    # Other Activities, Contingency) are never written by a USD CTR — see
    # the main_unused_blocks handling above, all three activity sections
    # are always 0 today — but Estimated CTR Total must still be a real
    # sum of all six Summary rows so a manual edit to any of them (or a
    # nonzero Contingency) actually flows through. The template's own
    # formula here (=G126+G120) silently dropped the other four.
    summary_support_addr  = _shift_cell(_usd["cell_summary_project_support"], total_shift)
    contingency_addr      = _shift_cell(_usd["cell_contingency"], total_shift)

    summary_grand_addr = _shift_cell(_usd["cell_summary_grand"], total_shift)
    ws_m[summary_grand_addr] = f"=SUM({summary_support_addr}:{contingency_addr})"

    # Every row inserted above moved each sheet's tail down by that much;
    # the print area has to follow or the bottom of the CTR silently stops
    # appearing in the PDF. Main grows only by the optional item lists
    # (list_items_on_main); Pricing grows by whatever the equipment and
    # consumables blocks needed beyond the template's capacity.
    _extend_print_area(ws_m, total_shift)
    _extend_print_area(ws_p, equip_extra + cons_extra)

    # The Main sheet's labor and third-party blocks are never written by a
    # USD CTR — labor and transport are priced on the AZN document — so any
    # row of them that is genuinely blank across the printed columns is
    # contracted, the same rule the data blocks follow. A row that does hold
    # something is left alone rather than guessed at.
    #
    # When a whole block is blank (the normal case for a USD CTR), one row
    # is deliberately left visible under the header rather than contracting
    # every row — a section with zero rows between its header and its Total
    # reads as broken, not "nothing to report here".
    _printed_cols = _usd.get("main_printed_cols", 7)
    for _first, _last in _usd.get("main_unused_blocks", []):
        block_rows = range(_first, _last + 1)
        all_blank = all(
            ws_m.cell(row=row, column=c).value in (None, "")
            for row in block_rows for c in range(1, _printed_cols + 1)
        )
        for row in block_rows:
            if all_blank and row == _first:
                ws_m.row_dimensions[row].hidden = False
                continue
            if all(ws_m.cell(row=row, column=c).value in (None, "")
                   for c in range(1, _printed_cols + 1)):
                ws_m.row_dimensions[row].hidden = True

    _placeholders = _usd.get("placeholder_texts", [])
    _clear_placeholder_cells(ws_m, _placeholders)
    _clear_placeholder_cells(ws_p, _placeholders)

    _fit_to_page_width(ws_m)
    _fit_to_page_width(ws_p)

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
