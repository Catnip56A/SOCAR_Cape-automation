"""
ctr_generator/config.py

Loads template layout positions from template_config.json.
Edit that file to adjust row/column positions when templates change,
instead of touching Python source.

Falls back to the built-in _DEFAULTS dict if the file is missing or
cannot be parsed, so the app always starts even with a broken config.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "template_config.json"

_DEFAULTS: dict = {
    "azn_template": {
        "sheet_name": "Main AZN",
        "onshore_data_start": 9,
        "onshore_data_end": 18,
        "onshore_total_gap": 1,   # blank rows between last data row and the total row
        "offshore_data_start": 23,
        "offshore_data_end": 62,
        "offshore_total_gap": 1,
        "cell_summary_onshore":  "G67",
        "cell_summary_offshore": "G68",
        "cell_summary_combined": "G71",
        "cell_ctr_ref":     "E3",
        "cell_job_ref":     "O3",
        "cell_client":      "B3",
        "cell_sub_client":  "C3",
        "cell_location":    "B4",
        "cell_date":        "E4",
        "cell_contract_no": "G4",
        "cell_revision":    "E5",
        "cell_scope":       "A6",
        "label_onshore_activities":  "Onshore Activities",
        "label_offshore_activities": "Offshore Activities",
    },
    "usd_template": {
        "main_sheet":    "Main",
        "pricing_sheet": "Pricing",
        "markup_rate": 0.065,
        "list_items_on_main": False,
        "equip_data_start": 11,
        "equip_data_end":   210,
        "cons_data_start": 216,
        "cons_data_end":   365,
        "cell_ctr_ref":     "E3",
        "cell_job_ref":     "O3",
        "cell_client":      "B3",
        "cell_sub_client":  "C3",
        "cell_location":    "B4",
        "cell_date":        "E4",
        "cell_contract_no": "G4",
        "cell_revision":    "E5",
        "cell_scope":       "A6",
        "cell_total_equip_2":     "G112",
        "cell_total_cons_raw":    "G115",
        "cell_cons_markup_rate":  "E116",
        "cell_total_cons_markup": "G116",
        "cell_total_cons":        "G120",
        "cell_summary_equip":     "G126",
        "cell_summary_cons_raw":  "G127",
        "cell_summary_grand":     "G130",
        "cell_equip_list_name": "C111",
        "cell_equip_list_cost": "G111",
        "cell_cons_list_name":  "C119",
        "cell_cons_list_cost":  "G119",
    },
    "pricebook": {
        "sheet_name": "Item Details and Rates",
        "skip_rows": 7,
        "col_stock_code":    4,
        "col_product_type":  5,
        "col_uom":           6,
        "col_supplier_desc": 9,
        "col_unit_price":    14,
    },
    # Maps an AZN labor stock code's shift-type prefix (the segment before
    # "-NAT-", e.g. "MSU" in "MSU-NAT-OFF-12") to [shift, shift_type].
    # A "GE" prefix (e.g. "GENOV-...") is stripped before lookup — see
    # ctr_generator/window.py _decode_azn_stock_code. Add new prefixes here
    # (no code changes needed) if the pricebook introduces one this table
    # doesn't cover yet — an unrecognized prefix makes manpower matching
    # refuse to guess and surface the row as unmatched instead.
    "azn_shift_prefixes": {
        "MS":  ["day",   "normal"],
        "MSU": ["day",   "normal"],
        "MF":  ["day",   "normal"],
        "OV":  ["day",   "overtime"],
        "SB":  ["day",   "standby"],
        "NS":  ["night", "normal"],
        "NOV": ["night", "overtime"],
        "NSB": ["night", "standby"],
    },
    "sage_export": {
        "sheet_name": "SAGE",
    },
    "names_db": {
        "sheet_name": "CTR_NAMES_DB_USD",
        "col_product":        1,
        "col_legacy_name":    2,
        "col_canonical_name": 3,
        "col_rate":           5,
    },
    "ctr_request": {
        "sheet_name": "CTR_REQUEST",
        "data_start_row": 13,
        "row_requester":         5, "col_requester":         3,
        "row_client":            5, "col_client":           11,
        "row_date_of_survey":    6, "col_date_of_survey":    3,
        "row_job_id_ref":        6, "col_job_id_ref":       11,
        "row_surveyor":          7, "col_surveyor":          3,
        "row_project_type":      7, "col_project_type":     11,
        "row_location":          8, "col_location":          3,
        "row_commencement_date": 8, "col_commencement_date": 11,
        "row_job_description":   9, "col_job_description":   3,
        "manpower_desc_col":          2,
        "manpower_qty_col":           3,
        "manpower_working_days_col":  4,
        "manpower_shift_col":         5,
        "manpower_status_col":        6,
        "manpower_encoding_col":      7,
        "equip_stock_code_col":     8,
        "equip_desc_col":            9,
        "equip_rechargability_col": 10,
        "equip_uom_col":            11,
        "equip_qty_col":            12,
        "equip_days_col":           13,
        "cons_stock_code_col":     14,
        "cons_desc_col":           15,
        "cons_uom_col":            16,
        "cons_qty_col":            17,
        "cons_rechargability_col": 18,
    },
}


def load_config() -> dict:
    """
    Load template_config.json, merging file values over _DEFAULTS
    section by section. Unknown keys in the file are included as-is.
    """
    if not _CONFIG_PATH.exists():
        return {k: dict(v) for k, v in _DEFAULTS.items()}

    try:
        with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        log.warning(
            "Could not load %s (%s) — using built-in defaults.", _CONFIG_PATH, exc
        )
        return {k: dict(v) for k, v in _DEFAULTS.items()}

    result: dict = {}
    for section, defaults in _DEFAULTS.items():
        result[section] = {**defaults, **data.get(section, {})}
    # Pass through any extra top-level sections from the file
    for section, values in data.items():
        if section not in result and not section.startswith("_"):
            result[section] = values
    return result


# Module-level singleton — import as `from ctr_generator.config import CFG`
CFG: dict = load_config()
