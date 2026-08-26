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
        # Free-text comments carried over from the CTR Request's own
        # Comments box (O6). Row 1 is a tall banner row whose D1:G1 holds
        # the standing PO note; C1 sits beside it and is empty in the
        # template.
        "cell_comments":    "C1",
        # Placeholder text left scattered through the templates' helper
        # cells ("AA" down the hidden column-H mirror, "aa" in the Pricing
        # sheet's margin block). It is meaningless filler that was never
        # meant to be read, and normally sits in a hidden column or outside
        # the print area — but it surfaces the moment a template is saved
        # with those columns shown, so it is stripped from the generated
        # document. Only cells whose entire value is one of these strings
        # are cleared; the real mirror formulas beside them are untouched.
        "placeholder_texts": ["AA", "aa"],
        "label_onshore_activities":  "Onshore Activities",
        "label_offshore_activities": "Offshore Activities",
        # Roles always billed out of "Project Support", even when the
        # request's wording has no "support" in it (see
        # builder_azn.is_support_manpower). Matched as a case-insensitive
        # substring, so one "Project Engineer" entry also catches "Senior
        # Project Engineer" — use full role names here, never a fragment
        # short enough to appear inside an unrelated role.
        "support_roles": ["Project Engineer"],
        # "Third Party Activities" section — not present in the shipped AZN
        # template, so build_azn inserts it (header row, column-header row,
        # one row per transport line, total row) between the activities
        # total and the Summary block, but only when the CTR Request
        # actually has TRANSPORT lines. Layout copied from a hand-made CTR
        # that has one (see build_azn._write_third_party_section).
        "third_party_label":         "Third Party Activities",
        "third_party_total_label":   "Total Third Party Activities",
        "third_party_summary_label": "Total Other Activities",
        # Mark-up is stored as a fraction (0.065) and shown as a percentage,
        # matching how the USD template already formats its consumables
        # mark-up. Without this the column inherits the "Quantity" format of
        # the labor row its styling is copied from and prints "0.065".
        "third_party_markup_format": "0.0%",
        "third_party_headers": [
            "Comment", "Quantity", "Description", "Mark Up, %",
            "Duration&\nUOM", "Rate, AZN", "Total, AZN",
        ],
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
        "cell_comments":    "C1",
        # See azn_template.placeholder_texts — same filler, same treatment.
        "placeholder_texts": ["AA", "aa"],
        # Main-sheet blocks a USD CTR never fills: Project Support, the
        # Onshore/Offshore Activities section, and Third Party Activities —
        # labor and transport are priced on the AZN document, so on this one
        # they are always empty. They're contracted like any other unused
        # capacity; left expanded, the USD CTR's first page is ~90 blank
        # rows and every real figure (equipment, consumables, the summary,
        # the CTR total) is pushed onto page 2. Only rows that are actually
        # empty across the printed columns are contracted, so a template
        # that does carry something here keeps showing it.
        "main_unused_blocks": [[9, 18], [23, 62], [67, 107]],
        "main_printed_cols": 7,
        # The Pricing sheet carries its own copy of the header block. It
        # isn't a second set of inputs — each cell mirrors the Main sheet's
        # equivalent by formula, so the two pages of one CTR can't disagree
        # and editing Main in Excel updates Pricing too. Left unwritten,
        # these keep whatever the template file had, which on a template
        # made from a previous CTR is that job's number, client and site.
        "pricing_cell_client":     "C3",
        "pricing_cell_sub_client": "D3",
        "pricing_cell_ctr_ref":    "G3",
        "pricing_cell_location":   "C4",
        "pricing_cell_date":       "G4",
        "pricing_cell_revision":   "G5",
        "pricing_cell_scope":      "B6",
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

    # The "Additional Information / Type of Scaffold System / TRANSPORT"
    # block that the v1.0.9 CTR Request form carries alongside the line-item
    # table (columns U:AA, rows 3-29 in that revision; columns B:P below the
    # line items in the older hand-filled forms). Nothing here is addressed
    # by fixed cell: the block is located by searching for its own label
    # text and then reading relative to that, since it has already moved
    # once and a request form without the block at all (pre-1.0.9) must
    # parse as "no extras" rather than as an error.
    "ctr_request_extras": {
        "search_max_row": 400,
        "search_max_col": 45,
        "label_additional_info": "Additional Information",
        "label_transport": "TRANSPORT",
        "label_transport_type": "Type of Transport",
        "label_transport_qty": "Quantity",
        "label_transport_duration": "Duration",
        "label_scaffold_header": "Type of Scaffold System",
        # A scaffold entry is any cell inside the block whose text mentions
        # this word (e.g. "Conventional Scaffold", "System Scaffold") other
        # than the block's own "Type of Scaffold System" heading; the
        # tonnage is the first number in the few cells to its right, and
        # the unit is the text cell immediately before that number.
        "scaffold_keyword": "scaffold",
        "scaffold_value_span": 4,
        # An Additional Information line counts as requested only when its
        # value cell reads exactly this (case-insensitive) — the form's
        # other option is the literal "Not Required", which must not match
        # on a substring test.
        "required_value": "Required",
        "option_values": ["Required", "Not Required"],
        "max_block_rows": 24,
        "max_blank_streak": 4,
    },

    # Rates for the transport types a CTR Request can ask for. The request
    # form only says *what* and *how many/how long* — the AZN rate, the
    # mark-up and the duration's unit are commercial figures that live
    # here, seeded from a real CTR (see TEST_Files/3rd + scaffold). One
    # requested transport type can expand into several CTR lines (a minibus
    # bills the vehicle+driver and its fuel separately, and only the former
    # carries mark-up). A type with no entry here still gets a line — using
    # the requested wording, "_default"'s mark-up and a 0.00 rate — so it
    # shows up in the UI to be priced by hand rather than disappearing.
    "transport_rates": {
        "_default": [
            {"rate_azn": 0.0, "markup": 0.065, "uom": "Days"}
        ],
        "Mini Bus": [
            {"description": "Minibus+driver",  "rate_azn": 110.0, "markup": 0.065, "uom": "Days"},
            {"description": "Fuel for minibus", "rate_azn": 10.0, "markup": 0.0,   "uom": "Days"}
        ],
        "Truck": [
            {"description": "Truck", "rate_azn": 135.0, "markup": 0.065, "uom": "Trips"}
        ],
    },

    # Scaffold requested as a tonnage in the extras block becomes an
    # ordinary USD Plant & Equipment line — scaffold is equipment, and a
    # hand-made scaffold CTR shows it as one: name, tonnage, TON. Only the
    # unit fallback lives here, used when the request's own unit cell is
    # blank; the rate and duration come from the pricebook match and the
    # user, like any other equipment line.
    "scaffold": {
        "default_uom": "TON",
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
        # The request's Comments box — merged O6:Q8, so the text lives in
        # its top-left cell O6. Same position on the 1.0.8 and 1.0.9 forms.
        "row_comments":          6, "col_comments":         15,
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
