"""The CTR parser never drops a line silently: a Stock Code column without a header is found by what its cells look
like, and every line that cannot be compared is named in the table's notes (shown in Step 2 and in the report)."""
import openpyxl

import harness as H
from comparator.sheet_parser import parse_workbook

EQUIP = [("CONVENTIONAL SCAFFOLD", "TON", None), ("SALA MINI BLOCK 1.8M", "EA", "EQ0900000000"),
         ("FULL BODY HARNESS XL", "EA", "PP6700000001"), ("FALL ARRESTER+RESCUE", "EA", "EQ4900015000"),
         ("Truck for Materials", "EA", None), ("Minibus (Including Driver)", "EA", None)]
CONS = [(f"ITEM {i}", "EA", f"SP23{i:08d}") for i in range(1, 13)]


def _ctr(name, cons_code_header=None, equip_code_in_k_for=None,
         cons_header=("Description", "Quantity", "Unit", "Unit Price", "Total")):
    """A CTR like SOCAR's CTR-26-319: the equipment table labels its code column (J), the consumables table (below it)
    has no label above its codes (`cons_code_header` = the label, or None). No named ranges."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pricing"
    ws["B2"], ws["B7"], ws["B9"] = "COST TIME RESOURCE SHEET", "Pricing Schedule", "Plant & Equipment"
    for c, t in zip("BCDEFGIJK", ("General Equipment", "Quantity", "Unit", "Rate per Day", "Days", "Total",
                                  "Total SC Eq Cost", "EquipmentCode", "Price AZN")):
        ws[f"{c}10"] = t
    for i, (desc, unit, code) in enumerate(EQUIP):
        r = 11 + i
        ws[f"A{r}"], ws[f"B{r}"], ws[f"C{r}"], ws[f"D{r}"], ws[f"E{r}"], ws[f"F{r}"], ws[f"G{r}"] = i + 1, desc, 1, unit, 1, 1, 1
        if code:
            ws[f"K{r}" if i == equip_code_in_k_for else f"J{r}"] = code
    ws["C17"], ws["G17"] = "Total Plant & Equipment", 6
    ws["B19"] = "Consumables & Materials"
    for c, t in zip("BCDEF", cons_header):
        ws[f"{c}20"] = t
    if cons_code_header:
        ws["J20"] = cons_code_header
    for i, (desc, unit, code) in enumerate(CONS):
        r = 21 + i
        ws[f"A{r}"], ws[f"B{r}"], ws[f"C{r}"], ws[f"D{r}"], ws[f"E{r}"], ws[f"F{r}"], ws[f"J{r}"] = i + 1, desc, 1, unit, 1, 1, code
    ws["C33"], ws["F33"] = "Materials and Consumables", 12
    path = H.SANDBOX / name
    wb.save(path)
    return path


def _only(tabs):
    assert len(tabs) == 1
    return tabs[0]


def test_a_stock_code_column_without_a_header_is_found_by_its_content():
    t = _only(parse_workbook(_ctr("nohdr.xlsx")))
    codes = set(t["data"]["Stock Code"])
    assert len(t["data"]) == 18                                          # 3 equipment + 12 consumables (was 3) + 3 lines without a code, kept
    assert {"EQ0900000000", "PP6700000001", "EQ4900015000", "SP2300000001", "SP2300000012"} <= codes
    warns = [n["text"] for n in t["notes"] if n["level"] == "warn"]
    assert len(warns) == 1 and "no header" in warns[0] and "column J" in warns[0] and "12 code(s)" in warns[0]


def test_lines_without_a_code_are_kept_and_named():
    t = _only(parse_workbook(_ctr("named.xlsx")))
    info = " ".join(n["text"] for n in t["notes"] if n["level"] == "info")
    assert "6 line(s) found, 6 compared" in info                           # (they used to be dropped: "3 compared")
    assert "3 line(s) without a Stock Code are compared by Description only" in info
    for desc in ("CONVENTIONAL SCAFFOLD", "Truck for Materials", "Minibus (Including Driver)"):
        assert desc in info, desc
    assert "SALA MINI BLOCK" not in info
    blank = t["data"][t["data"]["Stock Code"].astype(str).str.strip() == ""]
    assert len(blank) == 3 and set(blank["Description"]) >= {"CONVENTIONAL SCAFFOLD", "Truck for Materials"}


def test_a_properly_labelled_ctr_has_no_warning():
    t = _only(parse_workbook(_ctr("labelled.xlsx", cons_code_header="ProductCode")))
    assert len(t["data"]) == 18                                           # 15 with a code + 3 kept without one
    assert not [n for n in t["notes"] if n["level"] == "warn"]


def test_a_code_in_another_column_is_not_dropped_silently():
    t = _only(parse_workbook(_ctr("oddcol.xlsx", cons_code_header="ProductCode", equip_code_in_k_for=1)))
    assert len(t["data"]) == 17                                           # 14 + 3 lines without a code; the SALA line has its code in K: not compared ...
    warn = " ".join(n["text"] for n in t["notes"] if n["level"] == "warn")
    assert "SALA MINI BLOCK" in warn and "column K" in warn and "EQ0900000000" in warn   # ... and it says so


def test_the_notes_reach_the_table_list_in_step_2():
    import app as A
    t = _only(parse_workbook(_ctr("ui.xlsx")))
    win = A.MainWindow()
    win.show()
    H.pump(5)
    win._ctr_tables = [t]
    win._fill_table_list(win._ctr_table_list, win._ctr_tables, "ctr")
    H.pump(3)
    row_w = win._ctr_table_list.itemWidget(win._ctr_table_list.item(0))
    cb = row_w._checkbox
    assert "⚠ 1 note(s)" in cb.text() and "no header" in cb.toolTip() and "Truck for Materials" in cb.toolTip()
    # a table whose notes are only information says so without the warning colour
    t2 = _only(parse_workbook(_ctr("ui2.xlsx", cons_code_header="ProductCode")))
    win._ctr_tables = [t2]
    win._fill_table_list(win._ctr_table_list, win._ctr_tables, "ctr")
    cb2 = win._ctr_table_list.itemWidget(win._ctr_table_list.item(0))._checkbox
    assert "ⓘ lines skipped" in cb2.text() and "⚠" not in cb2.text()


def _load_like_browse(win, side, path):
    """What Browse does after the file dialog: list the file, parse it, wait."""
    H.msgs.clear()
    lw = win._ctr_file_list if side == "ctr" else win._mr_file_list
    win._add_file_row(lw, side, path.name)
    win._parse(side, [(path.name, path.read_bytes())])
    H.wait(lambda: all(not w.isRunning() for w in win._workers), 30)
    H.pump(15)
    return lw.itemWidget(lw.item(lw.count() - 1))._label


def test_a_file_that_gives_no_tables_or_cannot_be_read_is_told_to_the_user():
    import app as A
    nothing = H.SANDBOX / "nothing_here.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "Notes"
    wb.active["A1"] = "just some text"
    wb.save(nothing)
    broken = H.SANDBOX / "broken.xlsx"
    broken.write_bytes(b"this is not a zip file")
    win = A.MainWindow()
    win.show()
    H.pump(5)
    label = _load_like_browse(win, "ctr", nothing)                         # valid Excel, nothing to compare
    warned = [m for m in H.msgs if m[0] == "warning" and m[1] == "No tables found"]
    assert warned and "nothing_here.xlsx" in warned[0][2] and "Pricing Schedule" in warned[0][2], H.msgs
    assert "nothing_here.xlsx" in win.statusBar().currentMessage()
    assert label.toolTip().startswith('No tables were read from "nothing_here.xlsx"') and "Pricing Schedule" in label.toolTip()
    assert win._ctr_tables == []
    label = _load_like_browse(win, "ctr", broken)                          # not an Excel file: the error dialog + the row says so
    assert any(m[0] == "critical" for m in H.msgs)
    assert "could not be read" in label.toolTip()
    label = _load_like_browse(win, "mr", nothing)                          # the MR side names its own expectations
    assert any(m[0] == "warning" and "STOCKCODE" in m[2] for m in H.msgs)
    win._remove_file("ctr", "nothing_here.xlsx")                            # removing the file forgets the reason
    assert ("ctr", "nothing_here.xlsx") not in win._unread_files
    good = _ctr("good_after.xlsx", cons_code_header="ProductCode")
    label = _load_like_browse(win, "ctr", good)                            # a normal file gets no such warning
    assert not [m for m in H.msgs if m[0] == "warning" and m[1] == "No tables found"]
    assert "No tables were read" not in label.toolTip() and "could not be read" not in label.toolTip(), label.toolTip()


def test_a_renamed_total_header_is_still_a_header_and_is_said():
    for name, hdr in (("tot.xlsx", "Tot"), ("ctrtotal.xlsx", "CTR total")):
        t = _only(parse_workbook(_ctr(name, cons_code_header="ProductCode",
                                      cons_header=("Description", "Quantity", "Unit", "Unit Price", hdr))))
        df = t["data"]
        cons = df[df["_Section"] == "Consumables & Materials"]
        assert len(cons) == 12 and cons["Total"].notna().all(), hdr          # its own table, its own Total column
        assert "Days" not in cons or cons["Days"].isna().all(), hdr           # not read with the equipment columns
        assert any(hdr in n["text"] and "read as Total" in n["text"] for n in t["notes"]), t["notes"]
        assert not [n for n in t["notes"] if n["level"] == "warn"]


def test_a_header_row_that_is_not_recognised_is_warned_about():
    # no pricing words at all: the lines would be read with the equipment table's columns
    t = _only(parse_workbook(_ctr("weird.xlsx", cons_code_header="ProductCode",
                                  cons_header=("Item", "Count", "Measure", "Cost each", "Sum"))))
    warn = " ".join(n["text"] for n in t["notes"] if n["level"] == "warn")
    assert "Row 20" in warn and "Count" in warn and "not recognised" in warn, t["notes"]


def test_a_file_that_was_read_but_is_faulty_is_reported_and_marked_when_it_is_browsed():
    import app as A
    faulty = _ctr("faulty.xlsx", cons_code_header="ProductCode",
                  cons_header=("Descrip", "Quantity", "Unit", "Unit Price", "Total"))      # no column is recognised as Description
    win = A.MainWindow()
    win.show()
    H.pump(5)
    label = _load_like_browse(win, "ctr", faulty)
    dlgs = [m for m in H.msgs if m[0] == "warning" and m[1] == "Problems found in the files"]
    assert dlgs and "faulty.xlsx" in dlgs[0][2] and "no Description column" in dlgs[0][2] and "Column keywords" in dlgs[0][2], H.msgs
    assert label.text().startswith("⚠ ") and "not as expected" in label.toolTip() and "no Description column" in label.toolTip()
    assert len(win._ctr_tables) == 1                                        # it is still loaded (its other lines can be compared)
    good = _ctr("fine.xlsx", cons_code_header="ProductCode")
    label = _load_like_browse(win, "ctr", good)                             # a normal file: no dialog, no mark
    assert not [m for m in H.msgs if m[1] == "Problems found in the files"]
    assert not label.text().startswith("⚠")
    win._remove_file("ctr", "faulty.xlsx")                                  # removing the file forgets the problem
    assert ("ctr", "faulty.xlsx") not in win._file_problems


def test_an_empty_date_formatted_cell_is_not_a_header_called_nat():
    """Excel stores an empty cell of a date-formatted column and pandas reads it as NaT ("not a time"): it must be
    empty, not a column named "NaT" (found in the Pricing sheet of CTR-26-319, column AO)."""
    import datetime
    from comparator.sheet_parser import _norm
    import pandas as pd
    assert _norm(pd.NaT) == "" and _norm(float("nan")) == "" and _norm(None) == "" and _norm(" x ") == "x"
    assert _norm(0) == "0" and _norm(datetime.date(2026, 11, 2)) == "2026-11-02"
    wb = openpyxl.load_workbook(_ctr("nat_src.xlsx", cons_code_header="ProductCode"))
    ws = wb["Pricing"]
    for r in range(1, 36):                                                   # a date-formatted column far to the right
        ws.cell(r, 41).number_format = "yyyy-mm-dd"
    ws.cell(5, 41).value = datetime.date(2026, 10, 7)                        # one real date so the column reads as dates
    path = H.SANDBOX / "nat.xlsx"
    wb.save(path)
    t = _only(parse_workbook(path))
    cols = [str(c) for c in t["data"].columns]
    assert "NaT" not in cols, cols
    assert not any("NaT" in n["text"] for n in t["notes"]), t["notes"]


def test_the_missing_column_warning_names_the_headers_it_did_not_recognise():
    t = _only(parse_workbook(_ctr("hint.xlsx", cons_code_header="ProductCode",
                                  cons_header=("Descrip", "Quantity", "Unit", "Unit Price", "Total"))))
    warn = " ".join(n["text"] for n in t["notes"] if n["level"] == "warn")
    assert "no Description column was recognised" in warn
    assert "“Descrip”" in warn and "“=descrip”" in warn and "Column keywords" in warn      # the exact spelling to add
