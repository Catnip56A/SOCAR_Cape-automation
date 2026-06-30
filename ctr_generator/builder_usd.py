"""
ctr_generator/builder_usd.py

Copies the USD CTR template and writes equipment + consumable rows.

Template layout (verified from 217_USD template):

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
from datetime import datetime
from pathlib import Path

import openpyxl

_EQUIP_START  = 11
_EQUIP_END    = 210   # inclusive
_EQUIP_TOTAL  = 211

_CONS_START   = 216
_CONS_END     = 365   # inclusive
_CONS_TOTAL   = 366

_MARKUP_RATE  = 0.065

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y")


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

    try:
        wb    = openpyxl.load_workbook(out_path, keep_vba=False)
        ws_m  = wb["Main"]
        ws_p  = wb["Pricing"]
    except KeyError as e:
        raise ValueError(
            f"USD Template {template_path} is missing a \"Main\" or \"Pricing\" "
            f"sheet. Is this the right template?"
        ) from e
    except Exception as e:
        raise ValueError(f"Could not read USD Template {template_path}: {e}") from e

    # ── Main sheet: header ────────────────────────────────────────────────────
    ws_m["E3"] = f"CTR-26-{job_ref} USD"
    ws_m["O3"] = int(job_ref)

    if header:
        if header.get("client"):       ws_m["B3"] = header["client"]
        if header.get("sub_client"):   ws_m["C3"] = header["sub_client"]
        if header.get("location"):     ws_m["B4"] = header["location"]
        if header.get("date"):         ws_m["E4"] = _parse_date(header["date"])
        if header.get("contract_no"):  ws_m["G4"] = header["contract_no"]
        if header.get("revision") not in (None, ""):
            ws_m["E5"] = header["revision"]
        if header.get("scope"):        ws_m["A6"] = header["scope"]

    # ── Pricing sheet: clear equipment rows ───────────────────────────────────
    for row in range(_EQUIP_START, _EQUIP_END + 1):
        for col in range(1, 11):   # A–J
            _set_cell(ws_p, row, col, None)

    # ── Pricing sheet: write equipment rows ───────────────────────────────────
    total_equipment = 0.0
    for i, er in enumerate(equip_rows):
        if i >= (_EQUIP_END - _EQUIP_START + 1):
            break

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
            rate = 0.0   # NONRECHARG or non-numeric → treat as 0

        row_total = rate * days * qty
        total_equipment += row_total

        _set_cell(ws_p, r, 1,  item_no)
        _set_cell(ws_p, r, 2,  desc)
        _set_cell(ws_p, r, 3,  qty)
        _set_cell(ws_p, r, 4,  unit)
        _set_cell(ws_p, r, 5,  rate)      # plain number, 0 if NONRECHARG
        _set_cell(ws_p, r, 6,  days)
        _set_cell(ws_p, r, 7,  row_total)
        _set_cell(ws_p, r, 8,  item_no)   # counter column mirrors A
        _set_cell(ws_p, r, 9,  row_total)
        _set_cell(ws_p, r, 10, stock)

    # Equipment totals row
    _set_cell(ws_p, _EQUIP_TOTAL, 7, total_equipment)
    _set_cell(ws_p, _EQUIP_TOTAL, 9, total_equipment)

    # ── Pricing sheet: clear consumables rows ─────────────────────────────────
    for row in range(_CONS_START, _CONS_END + 1):
        for col in range(1, 11):
            _set_cell(ws_p, row, col, None)

    # ── Pricing sheet: write consumables rows ─────────────────────────────────
    total_consumables_raw = 0.0
    for i, cr in enumerate(consump_rows):
        if i >= (_CONS_END - _CONS_START + 1):
            break

        r         = _CONS_START + i
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

    # Consumables totals row
    _set_cell(ws_p, _CONS_TOTAL, 6, total_consumables_raw)

    # ── Derived totals ────────────────────────────────────────────────────────
    consumables_markup      = total_consumables_raw * _MARKUP_RATE
    total_consumables       = total_consumables_raw + consumables_markup
    estimated_ctr_total_usd = total_equipment + total_consumables

    # ── Main sheet: totals ────────────────────────────────────────────────────
    ws_m["G110"] = total_equipment
    ws_m["G112"] = total_equipment
    ws_m["G115"] = total_consumables_raw
    ws_m["G116"] = consumables_markup
    ws_m["G120"] = total_consumables
    ws_m["G126"] = total_equipment
    ws_m["G127"] = total_consumables_raw
    ws_m["G130"] = estimated_ctr_total_usd

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
