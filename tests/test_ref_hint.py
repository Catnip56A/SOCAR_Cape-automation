"""MR vs CTR: an MR page that names another CTR than the loaded one (a stale NCH-1 page left over from an earlier request) is
marked in Step 2 with the reason; the app never unticks or hides it by itself."""
import openpyxl

import harness as H
from comparator.sheet_parser import find_ctr_ref, parse_workbook
from test_parser_notes import _ctr


def _mr(name, pages):
    """An MR with one page per (sheet name, CTR number, stock codes): header on row 9 like a real MR, the number next to
    "Date of request" (E6)."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet, ref, codes in pages:
        ws = wb.create_sheet(sheet)
        ws["B6"], ws["E6"] = "Date of request", ref
        for c, h in enumerate(["№", "STOCKCODE *", "Item Specifications", "", "Qty *", "Unit *"], start=1):
            ws.cell(9, c, h)
        for r, code in enumerate(codes, start=10):
            ws.cell(r, 1, r - 9)
            ws.cell(r, 2, code)
            ws.cell(r, 3, "THING")
            ws.cell(r, 5, 1)
            ws.cell(r, 6, "EA")
    path = H.SANDBOX / name
    wb.save(path)
    return path


def _ctr_with_ref(name, ref):
    wb = openpyxl.load_workbook(_ctr(name + "_src.xlsx", cons_code_header="ProductCode"))
    ws = wb["Pricing"]
    ws["F3"], ws["G3"] = "Ref:", ref
    path = H.SANDBOX / name
    wb.save(path)
    return path


def _row(lw, sheet):
    for i in range(lw.count()):
        cb = lw.itemWidget(lw.item(i))._checkbox
        if f"› {sheet}" in cb.text() or sheet in cb.text().split("›")[-1].split("  (")[0]:
            return cb
    raise AssertionError(f"no row for {sheet}")


def test_the_ctr_number_is_read_from_the_top_of_a_page_or_the_file_name():
    assert find_ctr_ref(["Ref: ", "CTR-26-319 "]) == "CTR-26-319"
    assert find_ctr_ref(["ctr 22 477"]) == "CTR-22-477" and find_ctr_ref(["no number here", None]) is None
    assert find_ctr_ref(["XCTR-26-319"]) is None                                   # not inside another word
    mr = parse_workbook(_mr("ref_mr.xlsx", [("NCH-1", "CTR-22-477", ["AN0100000001"]), ("NCH-2", None, ["AN0100000002"])]))
    assert {t["source_sheet"]: t["ref"] for t in mr} == {"NCH-1": "CTR-22-477", "NCH-2": None}
    ctr = parse_workbook(_ctr_with_ref("ref_ctr.xlsx", "CTR-26-319"))
    assert ctr[0]["ref"] == "CTR-26-319"


def test_an_mr_page_for_another_ctr_is_marked_with_the_reason_and_a_hint_to_use_socar_cape():
    import app as A
    mr_path = _mr("ref_mr2.xlsx", [("Socar-Cape", "CTR-26-319", ["AN4800000000", "AN4900000000"]),
                                   ("NCH-1", "CTR-22-477", ["AN0100000001"])])
    ctr_path = _ctr_with_ref("ref_ctr2.xlsx", "CTR-26-319")
    win = A.MainWindow()
    win.show()
    H.pump(5)
    win._mr_tables = parse_workbook(mr_path, include_cover=True)
    win._fill_table_list(win._mr_table_list, win._mr_tables, "mr")
    assert "other CTR" not in _row(win._mr_table_list, "NCH-1").text()               # no CTR loaded yet: nothing to compare with
    win._ctr_tables = parse_workbook(ctr_path)
    win._fill_table_list(win._ctr_table_list, win._ctr_tables, "ctr")                 # loading the CTR refreshes the MR rows
    stale, good = _row(win._mr_table_list, "NCH-1"), _row(win._mr_table_list, "Socar-Cape")
    assert "⚠ other CTR" in stale.text() and "other CTR" not in good.text()
    tip = stale.toolTip()
    assert "refers to CTR-22-477" in tip and "CTR-26-319" in tip and "unticking this page and ticking Socar-Cape" in tip
    assert stale.isChecked()                                                           # told, not decided: it stays ticked
    hints = win._ref_hints()
    assert len(hints) == 1 and "NCH-1" in hints[0] and "CTR-22-477" in hints[0]
    win._ctr_tables = []                                                               # CTR removed: the hint goes
    win._fill_table_list(win._ctr_table_list, win._ctr_tables, "ctr")
    assert "other CTR" not in _row(win._mr_table_list, "NCH-1").text()
