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
    G110, G112 = total_equipment
    G115       = total_consumables_raw
    G116       = consumables_markup
    G120       = total_consumables  (raw + markup)
    G126       = total_equipment   (summary row 4)
    G127       = total_consumables_raw (summary row 5)
    G130       = estimated_ctr_total_usd

  Sheet "Pricing":
    Row 10       : equipment header
    Rows 11–210  : equipment data
      A=item#, B=desc, C=qty, D=unit, E=rate_per_day, F=days, G=total, H=counter, I=SC cost, J=stock_code
    Row 211      : G211=total_equipment, I211=total_equipment

    Row 215      : consumables header
    Rows 216–365 : consumables data
      A=item#, B=desc, C=qty, D=unit, E=unit_price, F=total, H=counter, I=SC cost, J=product_code
    Row 366      : F366=total_consumables_raw

  Editable header fields (rows 3–6, shared layout with AZN template):
    B3 = Client, C3 = Sub-Client, B4 = Location, E4 = Date,
    G4 = Contract No, E5 = Revision, A6 = Scope / description (merged A6:G6)

All values written as plain Python numbers — no formula reliance.
"""

from __future__ import annotations

import shutil
from copy import copy
from datetime import datetime
from pathlib import Path

import openpyxl

from ctr_generator.config import CFG

_usd = CFG["usd_template"]

_EQUIP_START = _usd["equip_data_start"]
_EQUIP_END   = _usd["equip_data_end"]   # default clear range; rows beyond here still write

_CONS_START  = _usd["cons_data_start"]
_CONS_END    = _usd["cons_data_end"]

_MARKUP_RATE = _usd["markup_rate"]

# Pricing-sheet column positions for section totals (template structure constants)
_EQUIP_TOTAL_COL  = 7   # G — row total
_EQUIP_TOTAL_COL2 = 9   # I — SC cost mirror
_CONS_TOTAL_COL   = 6   # F — row total

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y")


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
        whatever the template already has.

    Returns the path to the saved xlsx file.
    """
    template_path = Path(template_path)
    output_dir    = Path(output_dir)

    if not template_path.is_file():
        raise ValueError(f"USD Template not found: {template_path}")

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Cannot create output folder {output_dir}: {e}") from e

    out_path = output_dir / f"{job_ref}_USD_WCH_CTR.xlsx"
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
        ws_p.insert_rows(_EQUIP_END + 1, equip_extra)
        for i in range(equip_extra):
            _copy_row_format(ws_p, _EQUIP_END, _EQUIP_END + 1 + i, _COLS)

    # Consumables section has shifted down by equip_extra
    cons_start = _CONS_START + equip_extra
    cons_end   = _CONS_END   + equip_extra

    cons_capacity = cons_end - cons_start + 1
    cons_extra    = max(0, len(consump_rows) - cons_capacity)
    if cons_extra:
        ws_p.insert_rows(cons_end + 1, cons_extra)
        for i in range(cons_extra):
            _copy_row_format(ws_p, cons_end, cons_end + 1 + i, _COLS)

    # ── Pricing sheet: clear and write equipment rows ─────────────────────────
    equip_eff_end   = _EQUIP_END + equip_extra
    equip_clear_end = max(equip_eff_end, _EQUIP_START + len(equip_rows) - 1) if equip_rows else equip_eff_end
    for row in range(_EQUIP_START, equip_clear_end + 1):
        for col in range(1, _COLS + 1):
            _set_cell(ws_p, row, col, None)

    total_equipment = 0.0
    for i, er in enumerate(equip_rows):
        r         = _EQUIP_START + i
        item_no   = i + 1
        desc      = str(er.get("description", ""))
        qty       = _safe_float(er.get("quantity", 1), 1.0)
        unit      = str(er.get("unit", "DAY"))
        rate_raw  = er.get("rate_per_day", 0)
        days      = _safe_float(er.get("days", 30), 30.0)
        stock     = str(er.get("stock_code", ""))

        try:
            rate = float(rate_raw)
        except (TypeError, ValueError):
            rate = 0.0

        row_total = rate * days * qty
        total_equipment += row_total

        _set_cell(ws_p, r, 1,  item_no)
        _set_cell(ws_p, r, 2,  desc)
        _set_cell(ws_p, r, 3,  qty)
        _set_cell(ws_p, r, 4,  unit)
        _set_cell(ws_p, r, 5,  rate)
        _set_cell(ws_p, r, 6,  days)
        _set_cell(ws_p, r, 7,  row_total)
        _set_cell(ws_p, r, 8,  item_no)
        _set_cell(ws_p, r, 9,  row_total)
        _set_cell(ws_p, r, 10, stock)

    equip_total_row = _EQUIP_START + len(equip_rows)
    _set_cell(ws_p, equip_total_row, _EQUIP_TOTAL_COL,  total_equipment)
    _set_cell(ws_p, equip_total_row, _EQUIP_TOTAL_COL2, total_equipment)

    # ── Pricing sheet: clear and write consumables rows ───────────────────────
    cons_eff_end   = cons_end
    cons_clear_end = max(cons_eff_end, cons_start + len(consump_rows) - 1) if consump_rows else cons_eff_end
    for row in range(cons_start, cons_clear_end + 1):
        for col in range(1, _COLS + 1):
            _set_cell(ws_p, row, col, None)

    total_consumables_raw = 0.0
    for i, cr in enumerate(consump_rows):
        r         = cons_start + i
        item_no   = i + 1
        desc      = str(cr.get("long_description", ""))
        qty       = _safe_float(cr.get("quantity", 1), 1.0)
        unit      = str(cr.get("unit_code", "EA"))
        price     = _safe_float(cr.get("local_expect_cost", 0), 0.0)
        product   = str(cr.get("product", ""))

        row_total = price * qty
        total_consumables_raw += row_total

        _set_cell(ws_p, r, 1,  item_no)
        _set_cell(ws_p, r, 2,  desc)
        _set_cell(ws_p, r, 3,  qty)
        _set_cell(ws_p, r, 4,  unit)
        _set_cell(ws_p, r, 5,  price)
        _set_cell(ws_p, r, 6,  row_total)
        _set_cell(ws_p, r, 8,  item_no)
        _set_cell(ws_p, r, 9,  row_total)
        _set_cell(ws_p, r, 10, product)

    cons_total_row = cons_start + len(consump_rows)
    _set_cell(ws_p, cons_total_row, _CONS_TOTAL_COL, total_consumables_raw)

    # ── Derived totals ────────────────────────────────────────────────────────
    consumables_markup      = total_consumables_raw * _MARKUP_RATE
    total_consumables       = total_consumables_raw + consumables_markup
    estimated_ctr_total_usd = total_equipment + total_consumables

    # ── Main sheet: totals ────────────────────────────────────────────────────
    ws_m[_usd["cell_total_equip_1"]]     = total_equipment
    ws_m[_usd["cell_total_equip_2"]]     = total_equipment
    ws_m[_usd["cell_total_cons_raw"]]    = total_consumables_raw
    ws_m[_usd["cell_total_cons_markup"]] = consumables_markup
    ws_m[_usd["cell_total_cons"]]        = total_consumables
    ws_m[_usd["cell_summary_equip"]]     = total_equipment
    ws_m[_usd["cell_summary_cons_raw"]]  = total_consumables_raw
    ws_m[_usd["cell_summary_grand"]]     = estimated_ctr_total_usd

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
