"""
ctr_generator/builder_usd.py

Copies the USD CTR template and writes equipment + consumable rows.

Row/column positions are read from ctr_generator/template_config.json
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
    Row 10       : equipment header
    Rows 11–210  : equipment data
      A=item#, B=desc, C=qty, D=unit, E=rate_per_day, F=days, G=total, H=counter, I=SC cost, J=stock_code
      E is either a numeric rate or the literal "NONRECHARG" for
      non-rechargeable items; G is guarded with IF(ISNUMBER(...)) so a
      "NONRECHARG" row totals 0 instead of an Excel #VALUE! error.
    Row 211      : G211=total_equipment, I211=total_equipment

    Row 215      : consumables header
    Rows 216–365 : consumables data
      A=item#, B=desc, C=qty, D=unit, E=unit_price, F=total, H=counter, I=SC cost, J=product_code
    Row 366      : F366=total_consumables_raw

  Editable header fields (rows 3–6, shared layout with AZN template):
    B3 = Client, C3 = Sub-Client, B4 = Location, E4 = Date,
    G4 = Contract No, E5 = Revision, A6 = Scope / description (merged A6:G6)

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

import shutil
from copy import copy
from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.utils import column_index_from_string as _col_idx
from openpyxl.utils import get_column_letter as _col_letter

from ctr_generator.config import CFG
from ctr_generator.naming import ctr_output_filename

_usd = CFG["usd_template"]

_EQUIP_START = _usd["equip_data_start"]
_EQUIP_END   = _usd["equip_data_end"]   # default clear range; rows beyond here still write

_CONS_START  = _usd["cons_data_start"]
_CONS_END    = _usd["cons_data_end"]

_DEFAULT_MARKUP_RATE = _usd["markup_rate"]   # used when build_usd() isn't given an override

_LIST_ITEMS_ON_MAIN = bool(_usd.get("list_items_on_main", False))

# Pricing-sheet column positions for section totals (template structure constants)
_EQUIP_TOTAL_COL  = 7   # G — row total
_EQUIP_TOTAL_COL2 = 9   # I — SC cost mirror
_CONS_TOTAL_COL   = 6   # F — row total

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y")

_NONRECHARG = "NONRECHARG"


def _cell_row_col(addr: str) -> tuple[int, int]:
    """Convert a cell address like 'G111' to (row=111, col=7)."""
    col_str = "".join(c for c in addr if c.isalpha())
    row_str = "".join(c for c in addr if c.isdigit())
    return int(row_str), _col_idx(col_str)


def _shift_cell(addr: str, row_shift: int) -> str:
    """Return addr with its row number increased by row_shift."""
    row, col = _cell_row_col(addr)
    return f"{_col_letter(col)}{row + row_shift}"


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
    """Write value to a cell, skipping non-top-left merged-cell slots."""
    cell = ws.cell(row=row, column=col)
    try:
        cell.value = value
    except AttributeError:
        # MergedCell — only the top-left corner is writable; skip others
        pass


def _safe_float(v, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if f == f else default   # NaN guard
    except (TypeError, ValueError):
        return default


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
        rate_per_day (float or 'NONRECHARG'), days (float),
        stock_code (str)

    consump_rows: list of dicts with keys:
        long_description (str), local_expect_cost (float),
        unit_code (str), product (str)

    header: optional dict with keys client, sub_client, location, scope,
        date, contract_no, revision. Only non-empty values overwrite the
        corresponding template cell — leaving a field blank preserves
        whatever the template already has. location and scope are also
        used to build the output filename (see ctr_generator.naming).

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

    # ── Main sheet: header ────────────────────────────────────────────────────
    ws_m[_usd["cell_ctr_ref"]] = f"CTR-26-{job_ref} USD"
    ws_m[_usd["cell_job_ref"]] = int(job_ref)

    if header:
        if header.get("client"):      ws_m[_usd["cell_client"]]     = header["client"]
        if header.get("sub_client"):  ws_m[_usd["cell_sub_client"]] = header["sub_client"]
        if header.get("location"):    ws_m[_usd["cell_location"]]   = header["location"]
        if header.get("date"):        ws_m[_usd["cell_date"]]       = _parse_date(header["date"])
        if header.get("contract_no"): ws_m[_usd["cell_contract_no"]] = header["contract_no"]
        if header.get("revision") not in (None, ""):
            ws_m[_usd["cell_revision"]] = header["revision"]
        if header.get("scope"):       ws_m[_usd["cell_scope"]]      = header["scope"]

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

    for i, er in enumerate(equip_rows):
        r         = _EQUIP_START + i
        item_no   = i + 1
        desc      = str(er.get("description", ""))
        qty       = _safe_float(er.get("quantity", 1), 1.0)
        unit      = str(er.get("unit", "DAY"))
        rate_raw  = er.get("rate_per_day", 0)
        days      = _safe_float(er.get("days", 30), 30.0)
        stock     = str(er.get("stock_code", ""))

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
        _set_cell(ws_p, r, 6,  days)
        # Guarded so a "NONRECHARG" rate totals 0 instead of erroring (#VALUE!).
        _set_cell(ws_p, r, 7,  f"=IF(ISNUMBER(E{r}),C{r}*E{r}*F{r},0)")
        _set_cell(ws_p, r, 8,  f"=A{r}")
        _set_cell(ws_p, r, 9,  f"=G{r}")
        _set_cell(ws_p, r, 10, stock)

    # The total always sits immediately after the section's full capacity
    # block (equip_eff_end), not after however many rows were actually
    # written — otherwise a request with fewer rows than the template's
    # capacity writes the total into a blank filler row instead of the
    # fixed "Total Plant & Equipment" row.
    equip_total_row = equip_eff_end + 1
    equip_total_col_letter = _col_letter(_EQUIP_TOTAL_COL)
    _set_cell(ws_p, equip_total_row, _EQUIP_TOTAL_COL,
              f"=SUM({equip_total_col_letter}{_EQUIP_START}:{equip_total_col_letter}{equip_eff_end})")
    _set_cell(ws_p, equip_total_row, _EQUIP_TOTAL_COL2, f"=G{equip_total_row}")

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
        price     = _safe_float(cr.get("local_expect_cost", 0), 0.0)
        product   = str(cr.get("product", ""))

        if desc:
            cons_cost_list.append((desc, f"='{pricing_sheet}'!F{r}"))

        _set_cell(ws_p, r, 1,  item_no)
        _set_cell(ws_p, r, 2,  desc)
        _set_cell(ws_p, r, 3,  qty)
        _set_cell(ws_p, r, 4,  unit)
        _set_cell(ws_p, r, 5,  price)
        _set_cell(ws_p, r, 6,  f"=C{r}*E{r}")
        _set_cell(ws_p, r, 8,  f"=A{r}")
        _set_cell(ws_p, r, 9,  f"=F{r}")
        _set_cell(ws_p, r, 10, product)

    # Same fixed-position rule as equip_total_row above.
    cons_total_row = cons_eff_end + 1
    cons_total_col_letter = _col_letter(_CONS_TOTAL_COL)
    _set_cell(ws_p, cons_total_row, _CONS_TOTAL_COL,
              f"=SUM({cons_total_col_letter}{cons_start}:{cons_total_col_letter}{cons_eff_end})")

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

    cons_total_addr = _shift_cell(_usd["cell_total_cons"], total_shift)
    ws_m[cons_total_addr] = f"={cons_raw_addr}+{cons_markup_addr}"

    summary_equip_addr = _shift_cell(_usd["cell_summary_equip"], total_shift)
    ws_m[summary_equip_addr] = f"={total_equip_addr}"

    summary_cons_raw_addr = _shift_cell(_usd["cell_summary_cons_raw"], total_shift)
    ws_m[summary_cons_raw_addr] = f"={cons_raw_addr}"

    summary_grand_addr = _shift_cell(_usd["cell_summary_grand"], total_shift)
    ws_m[summary_grand_addr] = f"={summary_equip_addr}+{cons_total_addr}"

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
