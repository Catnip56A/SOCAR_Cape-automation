"""CTR Tracker: scaffold moved from Equipment (AN) to its own column (AO), and a row's leftovers emptied when a new
CTR is written over it. Works on temp copies only; the test tracker is read-only (copied into the sandbox)."""
import shutil
from datetime import date

import openpyxl
from openpyxl.utils import column_index_from_string as ci

import harness as H
from ctr_tools import tracker as T
from ctr_tools import tracker_fast as TF
from ctr_tools.config import CFG


def _pricing_workbook():
    wb = openpyxl.Workbook()
    wb.active.title = "Main"
    ws = wb.create_sheet("Pricing")
    ws["B3"], ws["B4"] = "Scaffolder-National", "Scaffold Supervisor"       # labour above the block: not counted
    ws["B10"] = "General Equipment"
    rows = [("Conventional Scaffold", 100.0), ("HARNESS", 10.0), ("System Scaffold", 25.5), ("Scaffold materials", 4.5)]
    for i, (name, total) in enumerate(rows):
        ws.cell(11 + i, 2, name)
        ws.cell(11 + i, 7, total)
    ws["C15"] = "Total Plant & Equipment:"
    ws["B16"], ws["G16"] = "Scaffold after the block", 999.0                 # below the block: not counted
    return wb


def test_scaffold_lines_in_the_equipment_block_are_summed_and_nothing_else():
    assert T._scaffold_total(_pricing_workbook(), CFG["ctr_extract"]) == 130.0


def test_no_scaffold_or_no_pricing_sheet_gives_none():
    wb = _pricing_workbook()
    for r in (11, 13, 14):
        wb["Pricing"].cell(r, 2).value = "Other"
    assert T._scaffold_total(wb, CFG["ctr_extract"]) is None
    del wb["Pricing"]
    assert T._scaffold_total(wb, CFG["ctr_extract"]) is None


def _tracker_copy(name):
    src = H.ROOT / "TEST_Files" / "CTR tracker" / "CTR-Tracker copy.xlsm"
    if not src.exists():
        raise H.Skip("TEST_Files/CTR tracker is not here")
    work = H.SANDBOX / name
    shutil.copy2(src, work)
    cfg = CFG["ctr_tracker"]
    ws = openpyxl.load_workbook(work, data_only=True, keep_vba=True)[cfg["sheet_name"]]
    spare = next((r for r in range(2, ws.max_row + 1)
                  if ws.cell(r, ci(cfg["col_ctr_number"])).value and not ws.cell(r, ci(cfg["col_description"])).value), None)
    if spare is None:
        raise H.Skip("no spare row in the test tracker")
    return work, cfg, str(ws.cell(spare, ci(cfg["col_ctr_number"])).value), spare


def _cell(work, cfg, row, key):
    ws = openpyxl.load_workbook(work, keep_vba=True)[cfg["sheet_name"]]
    return ws.cell(row, ci(cfg[key])).value


def test_scaffold_goes_to_ao_and_is_taken_out_of_equipment():
    work, cfg, number, _ = _tracker_copy("tracker_scaffold.xlsm")
    entry = T.CTREntry(client="C", ctr_number=number, ctr_date=date(2026, 10, 15), description="scaffold test",
                       value=500.0, currency="USD", tracker_location="Onshore", job_type="x",
                       total_equipment=200.0, total_scaffold=130.0)
    written, skipped, _b, _n = TF.write_entries_fast(work, [entry], make_backup=False)
    assert written and not skipped, (written, skipped)
    row = written[0][2]
    assert _cell(work, cfg, row, "col_scaffolding") == 130.0 and cfg["col_scaffolding"] == "AO"
    assert _cell(work, cfg, row, "col_equipment") == 70.0
    assert "SUM(AL" in str(_cell(work, cfg, row, "col_total_usd")) and ":AW" in str(_cell(work, cfg, row, "col_total_usd"))   # AO is inside the total's range


def test_no_scaffold_leaves_equipment_whole_and_ao_empty():
    work, cfg, number, _ = _tracker_copy("tracker_noscaffold.xlsm")
    entry = T.CTREntry(client="C", ctr_number=number, description="no scaffold", value=500.0, currency="USD",
                       tracker_location="Onshore", job_type="x", total_equipment=200.0)
    written, _s, _b, _n = TF.write_entries_fast(work, [entry], make_backup=False)
    row = written[0][2]
    assert _cell(work, cfg, row, "col_equipment") == 200.0
    assert _cell(work, cfg, row, "col_scaffolding") is None


def test_a_new_ctr_over_a_filled_row_empties_what_it_does_not_fill():
    work, cfg, number, _ = _tracker_copy("tracker_leftovers.xlsm")
    first = T.CTREntry(client="C", ctr_number=number, ctr_date=date(2026, 10, 15), revision="3",
                       description="old CTR", value=500.0, currency="USD", tracker_location="Onshore",
                       job_type="x", total_equipment=200.0, total_scaffold=130.0, total_third_party=50.0,
                       general_consumables=20.0)
    written, _s, _b, _n = TF.write_entries_fast(work, [first], make_backup=False)
    row = written[0][2]
    assert _cell(work, cfg, row, "col_third_party") == 50.0
    second = T.CTREntry(client="C", ctr_number=number, revision="3", description="new CTR", value=80.0, currency="USD",
                        tracker_location="Onshore", job_type="x", total_equipment=60.0)
    written, skipped, _b, _n = TF.write_entries_fast(work, [second], make_backup=False, revision_mode="overwrite")
    assert written and written[0][2] == row and not skipped, (written, skipped)
    assert _cell(work, cfg, row, "col_equipment") == 60.0
    for key in ("col_scaffolding", "col_third_party", "col_customs_transport", "col_consumables_recharge",
                "col_date"):
        assert _cell(work, cfg, row, key) is None, key
    assert _cell(work, cfg, row, "col_description") == "new CTR" and _cell(work, cfg, row, "col_value") == 80.0


def test_fields_a_person_typed_by_hand_survive_a_new_ctr():
    work, cfg, number, _ = _tracker_copy("tracker_soft.xlsm")
    a = T.CTREntry(client="Typed client", ctr_number=number, description="a", value=1.0, currency="USD",
                   tracker_location="Offshore", job_type="Typed job", project_code="AZE0104")
    written, _s, _b, _n = TF.write_entries_fast(work, [a], make_backup=False)
    row = written[0][2]
    b = T.CTREntry(ctr_number=number, description="b", value=2.0, currency="USD")
    TF.write_entries_fast(work, [b], make_backup=False, revision_mode="overwrite")
    assert _cell(work, cfg, row, "col_client") == "Typed client"
    assert _cell(work, cfg, row, "col_project_code") == "AZE0104"


def test_a_value_keeps_its_cents_in_the_tracker_window_text():
    from ctr_tools.tracker_window import _value_text
    assert _value_text(13619.759999999998) == "13619.76"      # the :g format gave 13619.8
    assert _value_text(1234567.89) == "1234567.89"
    assert _value_text(500.0) == "500" and _value_text(0.5) == "0.5" and _value_text(None) == ""


def test_an_azn_ctr_with_equipment_fills_the_same_columns_converted_to_usd():
    work, cfg, number, _ = _tracker_copy("tracker_azn_all.xlsm")
    rate = cfg["azn_to_usd_rate"]
    entry = T.CTREntry(client="C", ctr_number=number, description="all-in AZN", value=500.0, currency="AZN",
                       tracker_location="Onshore", job_type="x", total_project_support=0.0, total_activities=170.0,
                       total_equipment=88646.0, total_scaffold=77384.0, total_third_party=1700.0,
                       general_consumables=340.0)
    written, skipped, _b, _n = TF.write_entries_fast(work, [entry], make_backup=False)
    assert written and not skipped, (written, skipped)
    row = written[0][2]
    assert _cell(work, cfg, row, "col_labor") == f"=(0.0+170.0)/{rate}"
    assert _cell(work, cfg, row, "col_equipment") == f"=11262.0/{rate}"
    assert _cell(work, cfg, row, "col_scaffolding") == f"=77384.0/{rate}"
    assert _cell(work, cfg, row, "col_third_party") == f"=1700.0/{rate}"
    assert _cell(work, cfg, row, "col_consumables_recharge") == f"=340.0/{rate}"
    assert _cell(work, cfg, row, "col_customs_transport") is None        # nothing to write for a zero / missing figure


def test_a_usd_ctr_with_manpower_gets_labor_unconverted_and_a_labour_free_one_none():
    work, cfg, number, _ = _tracker_copy("tracker_usd_labor.xlsm")
    entry = T.CTREntry(client="C", ctr_number=number, description="usd labour", value=500.0, currency="USD",
                       tracker_location="Onshore", job_type="x", total_project_support=10.0, total_activities=90.0)
    written, _s, _b, _n = TF.write_entries_fast(work, [entry], make_backup=False)
    assert _cell(work, cfg, written[0][2], "col_labor") == "=(10.0+90.0)/1"
    work2, cfg, number, _ = _tracker_copy("tracker_usd_nolabor.xlsm")
    entry = T.CTREntry(client="C", ctr_number=number, description="usd none", value=5.0, currency="USD",
                       tracker_location="Onshore", job_type="x", total_project_support=0.0, total_equipment=5.0)
    written, _s, _b, _n = TF.write_entries_fast(work2, [entry], make_backup=False)
    assert _cell(work2, cfg, written[0][2], "col_labor") is None
