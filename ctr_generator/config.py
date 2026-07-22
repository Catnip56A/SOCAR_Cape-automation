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
    # USD Pricebook (equipment rental rates) — same template layout as
    # "pricebook" above (AZN manpower) by default, kept as its own section
    # so the sheet name / column positions can be adjusted independently if
    # the equipment pricebook format ever diverges from the AZN one.
    "usd_pricebook": {
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
        "sheet_name": "FROM SAGE",
        # analysis_b values (case-insensitive exact match) that mark a SAGE
        # row as non-rechargeable — "NONRECHAR" is a legacy typo variant
        # seen alongside "NONRECHARG" in real exports. Add more here (no
        # code change needed) if a differently-worded tag shows up.
        "non_recharge_tags": ["NONRECHARG", "NONRECHAR"],
    },
    "names_db": {
        "sheet_name": "CTR_NAMES_DB_USD",
        "col_product":        1,
        "col_legacy_name":    2,
        "col_canonical_name": 3,
    },
    # Cell layout for READING a generated CTR output file (AZN or USD —
    # both templates share the same header cell positions, see azn_template
    # / usd_template above). Used by the CTR Tracker tab to pull client,
    # CTR number, date, revision, description and currency straight off a
    # finished CTR so they don't have to be retyped.
    "ctr_extract": {
        "cell_client":     "B3",
        "cell_ctr_ref":    "E3",
        "cell_location":   "B4",
        "cell_date":       "E4",
        "cell_revision":   "E5",
        "cell_currency":   "G5",
        "cell_scope":      "A6",
        # None of the totals below sit at a fixed row — the summary section
        # shifts down depending on how many manpower/equipment/consumable
        # line items a CTR has, so each is found by searching for its label
        # text instead of a hardcoded cell (e.g. "Estimated CTR Total" is
        # row 71 in a short CTR, row 130 in a longer one). A label can
        # appear twice (once as its own section's subtotal, again in the
        # final Summary rollup with the same figure) — the search takes the
        # last match found, which lands on the Summary rollup when there is
        # one and the section subtotal otherwise (e.g. "Total Third Party
        # Activities" has no separate Summary line).
        "value_label":      "Estimated CTR Total",
        "value_label_col":  "A",
        "value_result_col": "G",
        "search_max_row":   400,
        # Labels searched across columns A/B/C (A: unlabelled totals like
        # "Estimated CTR Total"; B: numbered Summary rollup lines; C: the
        # Materials/Consumables breakdown) — same value_result_col (G).
        "label_cols": ["A", "B", "C"],
        "label_total_project_support":  "Total Project Support",
        "label_total_third_party":      "Total Third Party Activities",
        "label_total_equipment":        "Total Plant & Equipment",
        "label_general_consumables":    "General Consumables",
        "label_consumables_markup":     "Mark up (For Consumables)",
        "label_transportation_customs": "Transportation and Customs Clearance",
        "label_transportation_markup":  "Mark up (for services)",
        # The manpower section is labelled "Onshore Activities" or
        # "Offshore Activities" depending on the CTR's own project type —
        # read here (and combined with label_total_project_support to make
        # the tracker's Labor figure) and also used to sanity-check against
        # the tracker Location chosen in the UI before writing anything.
        "activity_labels":    ["Onshore Activities", "Offshore Activities"],
        "activity_label_col": "A",
    },

    # CTR Tracker tab — a CTR is written into the existing tracker row whose
    # "CTR number" column already contains its base number (currency suffix
    # stripped — see value_usd_formula note below on why AZN/USD share one
    # base number). Rows are pre-created by hand; this never creates a new
    # row or shifts existing ones. If no matching, still-empty row exists,
    # the CTR is skipped with a warning rather than guessing where to put
    # it. Column letters here match the header row of that sheet; update
    # them here (not in code) if the tracker's layout changes.
    "ctr_tracker": {
        "sheet_name":      "CTR Tracker",
        "header_row":      4,
        "col_client":        "C",
        "col_ctr_number":    "D",
        "col_location":      "L",
        "col_date":          "N",
        "col_project_code":  "O",
        "col_description":   "P",
        "col_value":         "S",
        "col_currency":      "AC",
        "col_revision":      "AI",
        # Total CTR value converted to USD — always rewritten with this
        "col_value_usd":     "AJ",
        # formula (AC/S are that row's own currency/value columns) using
        # the row actually being written, e.g. row 8903 becomes:
        #   =IF(AC8903="USD", S8903, IF(AC8903="AZN", S8903/1.7, ""))
        "value_usd_formula": 'IF(AC{row}="USD", S{row}, IF(AC{row}="AZN", S{row}/{rate}, ""))',
        # Column S's number format shows the currency's own symbol instead
        # of tracking whatever style the placeholder row happened to have.
        # Style ids come from this tracker's own styles.xml — AZN uses
        # "#,##0.00 [$₼-42C]" (id 72), USD uses "$#,##0.00" (id 159).
        "value_style_by_currency": {"AZN": 72, "USD": 159},

        # Cost-breakdown columns (AL:AX in the tracker header). Populated
        # best-effort from whatever totals a CTR's own summary section has
        # — "not all are applicable" to every CTR, so a column with no
        # matching source in the file is simply left blank. AZN CTRs (pure
        # labor documents) only ever contribute Labor; USD CTRs (equipment/
        # consumables/third-party documents) contribute the rest. Matches
        # the convention already used throughout the tracker's real
        # historical rows: Labor and the AZN->USD conversion embed a
        # computed literal (not a cross-workbook cell reference — Excel
        # formulas can't reach into another file's cells here), while
        # Consumables markup and the row Total are formulas over the
        # tracker's own row (safe — that row number is only known once
        # placement is decided, not assumed up front).
        "col_labor":               "AM",
        "col_equipment":           "AN",
        "col_third_party":         "AV",
        "col_customs_transport":   "AR",
        "col_consumables_recharge": "AT",
        "col_consumables_markup":  "AU",
        "col_total_usd":           "AX",
        "azn_to_usd_rate":         1.7,
        "consumables_markup_rate": 0.065,
        "labor_formula":             "({support}+{activities})/{rate}",
        "consumables_markup_formula": "AT{row}*{rate}",
        "total_usd_formula":          "SUM(AL{row}:AW{row})-AS{row}",
    },

    # Site name -> (Project Code, tracker Location bucket) lookup offered
    # in the CTR Tracker "Location" dropdown. Selecting a site fills both
    # the Project Code and Location (Onshore/Offshore/Georgia) fields.
    # Edit/add entries here — no code changes needed.
    "ctr_tracker_locations": {
        "Company Shared Costs":  {"project_code": "FMA-SHARE",       "tracker_location": "Offshore"},
        "Central Azeri":         {"project_code": "FMA0026-CAP",     "tracker_location": "Offshore"},
        "West Azeri":            {"project_code": "FMA0027-WAP",     "tracker_location": "Offshore"},
        "East Azeri":            {"project_code": "FMA0028-EA",      "tracker_location": "Offshore"},
        "Deep Water Gunashli":   {"project_code": "FMA0029-DWGP",    "tracker_location": "Offshore"},
        "Shah Deniz":            {"project_code": "FMA0030-SD",      "tracker_location": "Offshore"},
        "Chirag 1":              {"project_code": "FMA0031-CHG1",    "tracker_location": "Offshore"},
        "West Chirag":           {"project_code": "FMA0032-WCH",     "tracker_location": "Offshore"},
        "Sangachal Terminal":    {"project_code": "FMA0033-ST",      "tracker_location": "Onshore"},
        "Azerbaijan Pipelines":  {"project_code": "FMA0034-AZPL",    "tracker_location": "Onshore"},
        "Georgia Pipelines":     {"project_code": "FMA0035-GEOPL",   "tracker_location": "Georgia"},
        "Offshore Projects":     {"project_code": "FMA0036-OFFSHPRJ", "tracker_location": "Offshore"},
        "Onshore Projects":      {"project_code": "FMA0037-SHDPRJ",  "tracker_location": "Onshore"},
        "Shah Deniz Bravo":      {"project_code": "FMA0062-SDB",     "tracker_location": "Offshore"},
        "ACE (Azeri-Central-East) Project": {"project_code": "FMA0119-ACE", "tracker_location": "Offshore"},
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
