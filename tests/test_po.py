"""CTR Tracker purchase orders: the PDF reader (synthetic text and the sample PDFs), the company short names, the row
suggestions, the pricebook comparison, the writer (only the picked row's PO cells change) and the Purchase Orders
section of the window (pick a CTR, warnings, existing values asked, Type of PO suggestions). Sandbox copies of the test
tracker only; the real tracker, PO files and CTR files are only read."""
import json
import shutil
from datetime import date

import openpyxl

import harness as H
from ctr_tools import po as P
from ctr_tools import tracker_fast as TF
from ctr_tools.config import CFG

SAMPLE_DIR = H.ROOT / "TEST_Files" / "CTR tracker" / "PO"

PO_TEXT = """COMPANY: {company}
Address: Somewhere 1
Purchase Order 4300000001
Information
Purchase Order No: 4300000001
Order Date: 02-NOV-2026
Currency: {cur}
Item Description Quantity UoM Unit Price Total Price
{items}
Total Purchase Order Amount {cur} {total}
Acceptance Approvals
"""
ITEM = """ {n} Description: {desc}
Pricebook # {pb}
Master Agreement # CW1
Account Assignment: CC 10TEST{n}
Delivery Date: 31-DEC-2026
{amount}
"""


def _text(items, company="BP EXPLORATION (CASPIAN SEA) LIMITED", cur="AZN", total="300.00"):
    body = "".join(ITEM.format(n=n, desc=d, pb=pb, amount=a) for n, d, pb, a in items)
    return PO_TEXT.format(company=company, cur=cur, items=body, total=total)


def test_a_synthetic_po_is_read_field_by_field():
    po = P.parse_po_text(_text([(1, "Cleaning of tanks CTR-26-111", "4410000001", "300.00")]))
    assert (po.po_number, po.currency, po.total, po.order_date) == ("4300000001", "AZN", 300.0, date(2026, 11, 2))
    i = po.items[0]
    assert (i.number, i.description, i.pricebook, i.account, i.delivery, i.amount) == (
        1, "Cleaning of tanks CTR-26-111", "4410000001", "CC 10TEST1", date(2026, 12, 31), 300.0)
    assert not po.notes


def test_two_items_and_a_wrapped_company_and_a_wrapped_description():
    text = PO_TEXT.format(company="AZERBAIJAN INTERNATIONAL OPERATING\nCO.", cur="USD", total="1,300.50", items=
                          ITEM.format(n=1, desc="First job", pb="4410030190", amount="1,000.25") +
                          ITEM.format(n=2, desc="Second job\nthat wraps", pb="4410030190", amount="300.25"))
    po = P.parse_po_text(text)
    assert po.company == "AZERBAIJAN INTERNATIONAL OPERATING CO." and [i.number for i in po.items] == [1, 2]
    assert [i.amount for i in po.items] == [1000.25, 300.25] and po.items[1].description == "Second job that wraps"
    assert po.total == 1300.5 and not po.notes


def test_a_total_that_does_not_add_up_is_noted_and_a_non_po_is_refused():
    po = P.parse_po_text(_text([(1, "Job", "4410000001", "300.00")], total="999.00"))
    assert any("add up" in n for n in po.notes), po.notes
    for bad in ("hello world", "COMPANY: X\nPurchase Order No: 1\nCurrency: USD\n"):
        try:
            P.parse_po_text(bad)
        except P.POError:
            continue
        raise AssertionError("not refused: " + bad)


def test_the_sample_pdfs_are_read():
    files = sorted(SAMPLE_DIR.glob("2_.PO_*.PDF"))
    if len(files) < 2:
        raise H.Skip("the sample PO PDFs are not here")
    by_no = {p.po_number: p for p in (P.read_po(f) for f in files)}
    a = by_no["4301562184"]
    assert (a.currency, a.total, P.short_company(a.company)) == ("AZN", 13427.25, "BPCS")
    assert (a.items[0].pricebook, a.items[0].account, a.items[0].delivery) == ("4410021355", "CC 10UTDA1151", date(2026, 12, 31))
    b = by_no["4301562851"]
    assert (b.currency, b.total, P.short_company(b.company)) == ("USD", 1080.36, "AIOC")
    assert b.items[0].master_agreement == "PROVISION OF FABRIC MAINTENANCE SERVICES"


def test_company_short_names_ignore_capitals_and_a_trailing_limited():
    m = {"BP EXPLORATION (CASPIAN SEA)": "BPCS", "THE BAKU-TBILISI-CEYHAN PIPELINE COMPANY": "BTC"}
    assert P.short_company("BP Exploration (Caspian Sea) Limited", m) == "BPCS"
    assert P.short_company("BP EXPLORATION (CASPIAN SEA) LTD.", m) == "BPCS"
    assert P.short_company("the baku-tbilisi-ceyhan pipeline company", m) == "BTC"
    assert P.short_company("BP EXPLORATION (SHAH DENIZ) LIMITED", m) is None
    assert P.short_company("", m) is None
    assert P.short_company("GEORGIAN PIPELINE COMPANY")  == "GPC"       # the shipped list


def test_ctr_numbers_and_pricebooks_compare():
    assert P.ctr_base("CTR-22-117R1") == "CTR-22-117" == P.ctr_base("ctr 22 117 AZN") == P.ctr_base("CTR-22-117")
    assert P.ctr_base("PRP-26-050") == "PRP-26-050" and P.ctr_base("CC 10UTDA1151") == ""
    assert P.pricebook_differs("4410021355", "4410030127") and not P.pricebook_differs("4410021355", "4410021355")
    assert not P.pricebook_differs("", "4410021355") and not P.pricebook_differs("4410021355", "")


def _rec(row, ctr, desc="", value=None, currency="AZN", po=""):
    return {"row": row, "ctr": ctr, "description": desc, "value": value, "currency": currency, "po": po}


ROWS = [_rec(1, "CTR-26-100", "Painting of the pipe racks"),
        _rec(2, "CTR-26-111", "Something else entirely"),
        _rec(3, "CTR-26-120", "CTR Hydrocyclone liners cleaning activities at Serenja NORM storage area", 300.0),
        _rec(4, "CTR-26-130", "Hydrocyclone 145 liners cleaning at Serenja NORM storage", po="4300000001"),
        _rec(5, "", "no CTR number at all, cleaning Serenja NORM storage")]


def test_suggestions_come_from_the_description_and_a_typed_ctr_number_comes_first():
    item = P.POItem(description="Cleaning activities/Serenja NORM storage", amount=999.0)
    got = P.suggest_rows(item, ROWS)
    order = [s["row"] for s in got]
    assert set(order[:2]) == {3, 4} and 1 not in order and 5 not in order, got      # alike descriptions only
    assert order[0] == 4 or got[0]["score"] >= got[1]["score"]
    assert all("alike" in s["reasons"][0] for s in got)
    # the value and the PO number no longer count: a row with the same value but another description is not offered
    assert P.suggest_rows(P.POItem(description="zzz qqq", amount=300.0), ROWS, ) == []
    typed = P.suggest_rows(P.POItem(description="Cleaning of tanks CTR-26-111"), ROWS)
    assert typed[0]["row"] == 2 and typed[0]["reasons"] == ["the CTR number is in the PO text"], typed


def test_typos_and_word_endings_still_find_the_description():
    got = P.fuzzy_rows("cleaning liner serenga norm storege", ROWS)
    assert {r["row"] for r, _s in got} >= {3, 4}, got
    assert P.fuzzy_rows("", ROWS) == [] and P.fuzzy_rows("zzzz qqqq xxxx", ROWS) == []


def test_the_word_index_finds_typos_in_a_few_typed_words():
    idx = P.WordIndex([r["description"] for r in ROWS])
    found = [pos for pos, _s in idx.search("hidrocyclone serenga")]
    assert set(found) >= {2, 3}, found                    # positions of rows 3 and 4
    assert idx.search("") == [] and idx.search("zzzzzz") == []
    assert [pos for pos, _s in idx.search("racks")] == [0]
    assert idx.search("hydrocyclone racks") == []         # every typed word must be like a word of the description


def test_a_ctr_number_search_is_never_fuzzy():
    for text in ("334", "26-334", "CTR-26-334", "ctr 26 334", "CTR-22-117R1"):
        assert P.is_number_query(text), text
    for text in ("norm storage", "Train 2 Pump", "cleaning"):
        assert not P.is_number_query(text), text


# ── the writer ───────────────────────────────────────────────────────────────────────────────────────────

def _copy(name):
    src = H.tracker_copy()
    if not src.exists():
        raise H.Skip("TEST_Files/CTR tracker is not here")
    work = H.SANDBOX / name
    shutil.copy2(src, work)
    return work


def _free_row(rows):
    """A tracker row with a CTR number that has no PO fields filled (so a PO lands without a conflict)."""
    for r in reversed(rows):
        if r.get("ctr") and P.ctr_base(r["ctr"]) and not (r["po"] or r["account"] or r["po_type"] or r["line"] or r["client"]) \
                and r["po_value"] is None and r["delivery"] is None:
            return r
    raise H.Skip("no free CTR row in the test tracker")


def _assignment(rec, **over):
    f = {"po_number": "4399999999", "po_line": 1, "company": "BPCS", "account": "CC 10TESTX", "delivery": date(2027, 1, 5),
         "value": 1234.5, "po_type": "LIMIT/FIX"}
    f.update(over)
    return {"row": rec["row"], "ctr": rec["ctr"], "currency": "AZN", "fields": f}


def test_the_writer_fills_the_po_cells_of_the_picked_row_only():
    work = _copy("po_write.xlsm")
    before = TF.read_tracker_rows(work)
    rec = _free_row(before)
    written, skipped, _b = TF.write_pos_fast(work, [_assignment(rec)], make_backup=False)
    assert written == [(0, rec["ctr"], rec["row"])] and not skipped
    after = {r["row"]: r for r in TF.read_tracker_rows(work)}
    row = after[rec["row"]]
    assert (row["po"], row["line"], row["client"], row["account"], row["po_type"]) == ("4399999999", "1", "BPCS", "CC 10TESTX", "LIMIT/FIX")
    assert row["delivery"] == date(2027, 1, 5) and row["po_value"] == 1234.5
    for key in ("ctr", "description", "value", "currency", "revision"):
        assert row[key] == rec[key], key                                   # the CTR's own cells are not touched
    others = [r for r in before if r["row"] != rec["row"]]
    assert all(after[r["row"]] == r for r in others)                       # no other row changed
    ws = openpyxl.load_workbook(work, keep_vba=True)[CFG["ctr_tracker"]["sheet_name"]]
    assert "mmm" in ws[f"Q{rec['row']}"].number_format and ws[f"H{rec['row']}"].value == 4399999999   # a number, a date format


def test_a_field_left_out_is_not_touched_and_a_stale_row_is_refused():
    work = _copy("po_partial.xlsm")
    rec = _free_row(TF.read_tracker_rows(work))
    a = _assignment(rec)
    a["fields"] = {"po_number": "4399999998", "po_line": 1}
    written, _s, _b = TF.write_pos_fast(work, [a], make_backup=False)
    row = {r["row"]: r for r in TF.read_tracker_rows(work)}[rec["row"]]
    assert written and row["po"] == "4399999998" and not row["account"] and row["po_value"] is None and row["delivery"] is None
    stale = _assignment(rec)
    stale["ctr"] = "CTR-99-999"
    written, skipped, _b = TF.write_pos_fast(work, [stale], make_backup=False)
    assert not written and "tracker changed" in skipped[0][2], skipped
    gone = _assignment(rec)
    gone["row"] = 900000
    assert "does not exist" in TF.write_pos_fast(work, [gone], make_backup=False)[1][0][2]


def test_a_backup_copy_is_made_when_asked():
    work = _copy("po_backup.xlsm")
    rec = _free_row(TF.read_tracker_rows(work))
    _w, _s, backup = TF.write_pos_fast(work, [_assignment(rec)], make_backup=True)
    assert backup is not None and backup.exists() and backup != work


# ── the window section ───────────────────────────────────────────────────────────────────────────────────

def _section(work, files=None, fresh=True):
    from ctr_tools.po_view import POSectionWidget
    if fresh:                                    # the remembered Type of PO texts live in the sandbox QSettings
        POSectionWidget._settings().remove("ctr_tracker/po_types")
    return POSectionWidget(lambda: str(work), lambda: False, lambda: files or {})


def _synthetic_po(desc="Cleaning of tanks", amount=1234.5, company="BP EXPLORATION (CASPIAN SEA) LIMITED", pb="4410000001"):
    return P.parse_po_text(_text([(1, desc, pb, f"{amount:,.2f}")], company=company, total=f"{amount:,.2f}"))


def _load(w, po):
    from ctr_tools.po_view import _PORow
    for item in po.items:
        w._add_row(_PORow(po=po, item=item))
    w._ensure_tracker_rows()
    assert H.wait(lambda: bool(w._tracker_rows), 60), "the tracker rows were not read"
    H.pump()


def _pick(w, r, row):
    w._select_row(r, row)                      # what choosing it from the typed-search list does


def test_the_company_is_filled_and_an_unknown_one_is_flagged_red():
    work = _copy("po_ui1.xlsm")
    w = _section(work)
    po = _synthetic_po()
    _load(w, po)
    r = w._rows[0]
    assert r.company_edit.text() == "BPCS"
    warn, _ = w.problems(r)
    assert any("No CTR chosen" in x for x in warn)
    w.clear()
    _load(w, _synthetic_po(company="SOME OTHER CO"))
    r = w._rows[0]
    assert r.company_edit.text() == "" and any("short-name list" in x for x in w.problems(r)[0])
    w.deleteLater()


def test_picking_a_ctr_checks_currency_and_pricebook_and_a_double_choice():
    work = _copy("po_ui2.xlsm")
    rows = TF.read_tracker_rows(work)
    rec = _free_row(rows)
    ctr_file = H.SANDBOX / "ctr_pb.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["F3"], ws["G3"] = "Pricebook #: ", 4410000002          # differs from the PO's 4410000001
    wb.save(ctr_file)
    w = _section(work, {P.ctr_base(rec["ctr"]): str(ctr_file)})
    po = _synthetic_po(desc=f"Job for {rec['ctr']}")
    po.currency = "USD" if rec["currency"] != "USD" else "AZN"   # the other currency than the row's
    po2 = _synthetic_po(desc="second")
    po2.po_number = "4300000002"
    _load(w, po)
    _load(w, po2)
    r1, r2 = w._rows
    assert r1.suggested and r1.suggested[0]["row"] == rec["row"] or any(s["row"] == rec["row"] for s in r1.suggested)
    _pick(w, r1, rec["row"])
    warn, _ = w.problems(r1)
    assert any("Pricebook differs" in x for x in warn) and r1.ctr_pricebook == "4410000002", warn
    if rec["currency"]:
        assert any("The PO is in" in x for x in warn), warn
    assert "color:#C62828" in r1.note.text()
    _pick(w, r2, rec["row"])
    assert any("Another item points" in x for x in w.problems(r2)[0]) and any("Another item points" in x for x in w.problems(r1)[0])
    w.deleteLater()


def _patch_boxes(answer=True):
    from ctr_tools import po_view as PV
    old = (PV.QMessageBox.question, PV.QMessageBox.information, PV.QMessageBox.warning, PV.QMessageBox.critical)
    seen = []
    PV.QMessageBox.question = staticmethod(lambda *a, **k: (seen.append(("question", a[2])),
                                                           PV.QMessageBox.StandardButton.Yes if answer else PV.QMessageBox.StandardButton.Cancel)[1])
    PV.QMessageBox.information = staticmethod(lambda *a, **k: seen.append(("info", a[2])))
    PV.QMessageBox.warning = staticmethod(lambda *a, **k: seen.append(("warning", a[2])))
    PV.QMessageBox.critical = staticmethod(lambda *a, **k: seen.append(("critical", a[2])))
    return seen, old


def _unpatch(old):
    from ctr_tools import po_view as PV
    (PV.QMessageBox.question, PV.QMessageBox.information, PV.QMessageBox.warning, PV.QMessageBox.critical) = old


def test_writing_from_the_window_fills_the_row_and_remembers_the_type_of_po():
    work = _copy("po_ui3.xlsm")
    rec = _free_row(TF.read_tracker_rows(work))
    w = _section(work)
    _load(w, _synthetic_po(amount=777.25))
    r = w._rows[0]
    _pick(w, r, rec["row"])
    r.type_combo.setEditText("LIMIT/FIX")
    from ctr_tools import po_view as PV

    class _NoDialog:                           # nothing is filled on that row, so nothing may be asked
        def __init__(self, *a, **k):
            raise AssertionError("the overwrite dialog must not open for an empty row")
    real = PV._OverwriteDialog
    PV._OverwriteDialog = _NoDialog
    seen, old = _patch_boxes()
    try:
        w._write()
        assert H.wait(lambda: not w._rows, 60), seen
        assert H.wait(lambda: any(k == "info" for k, _t in seen), 20), seen
    finally:
        PV._OverwriteDialog = real
        _unpatch(old)
    row = {x["row"]: x for x in TF.read_tracker_rows(work)}[rec["row"]]
    assert row["po"] == "4300000001" and row["po_value"] == 777.25 and row["po_type"] == "LIMIT/FIX" and row["client"] == "BPCS"
    assert row["account"] == "CC 10TEST1" and row["delivery"] == date(2026, 12, 31)
    assert "LIMIT/FIX" in w._types and w._types.count("LIMIT/FIX") == 1     # shipped suggestion, not added twice
    assert "LIMIT/FIX" in json.loads(str(w._settings().value("ctr_tracker/po_types")))
    w.deleteLater()


def test_existing_different_values_are_asked_and_kept_unless_replaced():
    from ctr_tools import po_view as PV
    work = _copy("po_ui4.xlsm")
    rec = _free_row(TF.read_tracker_rows(work))
    TF.write_pos_fast(work, [_assignment(rec, company="OLDCO", account="CC OLD", value=5.0, delivery=None)], make_backup=False)
    results = {}
    for tag, replace in (("keep", False), ("replace", True)):
        w = _section(work)
        _load(w, _synthetic_po(amount=777.25))
        r = w._rows[0]
        _pick(w, r, rec["row"])
        asked = []

        class _Dlg:
            def __init__(self, parent, conflicts, titles, _flag=replace):
                asked.append(conflicts)
                self._flag = _flag
                self._idx = list(conflicts)

            def exec(self):
                return PV.QDialog.DialogCode.Accepted

            def replace_flags(self):
                return {i: self._flag for i in self._idx}

            def deleteLater(self):
                pass
        real = PV._OverwriteDialog
        PV._OverwriteDialog = _Dlg
        seen, old = _patch_boxes()
        try:
            w._write()
            assert H.wait(lambda: not w._rows, 60), seen
            H.wait(lambda: any(k == "info" for k, _t in seen), 20)
        finally:
            PV._OverwriteDialog = real
            _unpatch(old)
        labels = {label for label, _o, _n in asked[0][0]}
        assert {"Company", "Account assignment", "PO value"} <= labels, labels
        results[tag] = {x["row"]: x for x in TF.read_tracker_rows(work)}[rec["row"]]
        w.deleteLater()
        if tag == "keep":
            assert results[tag]["client"] == "OLDCO" and results[tag]["account"] == "CC OLD" and results[tag]["po_value"] == 5.0
            assert results[tag]["delivery"] == date(2026, 12, 31)          # the empty/equal cells were still filled
    assert results["replace"]["client"] == "BPCS" and results["replace"]["po_value"] == 777.25


def test_the_type_of_po_suggestions_start_with_the_shipped_ones_and_deleting_is_remembered():
    work = _copy("po_ui5.xlsm")
    w = _section(work)
    shipped = ["CONTRACT", "LIMIT", "LIMIT/FIX", "STANDARD", "STANDARD/FIX"]
    assert w._types == shipped                                             # nothing saved yet: the shipped list
    w._remember_types(["limit/fix", "BRAND NEW"])                          # a repeat ignoring capitals is not added twice
    assert w._types == shipped + ["BRAND NEW"]
    again = _section(work, fresh=False)
    assert again._types == shipped + ["BRAND NEW"]                         # remembered
    _load(again, _synthetic_po())
    combo = again._rows[0].type_combo
    assert [combo.itemText(i) for i in range(combo.count())] == shipped + ["BRAND NEW"]
    again._types = ["STANDARD"]                                            # what the "suggestions…" dialog does on Save
    again._save_types()
    again._refresh_type_combos()
    assert [combo.itemText(i) for i in range(combo.count())] == ["STANDARD"]
    assert _section(work, fresh=False)._types == ["STANDARD"]              # the deleted shipped ones do not come back
    for x in (w, again):
        x.deleteLater()


def test_a_workbook_picked_after_the_po_is_loaded_reads_its_rows_and_gives_suggestions():
    work = _copy("po_late.xlsm")
    path = {"p": ""}
    from ctr_tools.po_view import POSectionWidget, _PORow
    POSectionWidget._settings().remove("ctr_tracker/po_types")
    w = POSectionWidget(lambda: path["p"], lambda: False, lambda: {})
    po = _synthetic_po()
    for item in po.items:
        w._add_row(_PORow(po=po, item=item))
    w._ensure_tracker_rows()
    assert not w._tracker_rows and "Pick the tracker workbook" in w._status.text()
    path["p"] = str(work)
    w.tracker_path_changed()                                   # what the Tracker tab's box does when it changes
    assert H.wait(lambda: bool(w._tracker_rows), 60), "the rows were not read after the workbook was picked"
    H.pump(10)
    assert w._search_model is not None and w._search_model.rowCount() > 100
    assert "tracker rows read" in w._status.text()
    w.deleteLater()



# ── the CTR box: the description is prefilled as text only; the CTR file belongs to the picked row ─────────────

def _synthetic_section(files=None):
    from ctr_tools.po_view import POSectionWidget, _PORow
    POSectionWidget._settings().remove("ctr_tracker/po_types")
    dummy = H.SANDBOX / "dummy_tracker.xlsm"
    dummy.write_bytes(b"")
    w = POSectionWidget(lambda: str(dummy), lambda: False, lambda: files or {})
    w._tracker_rows = [dict(r, client="", account="", po_type="", line="", delivery=None, po_value=None, revision="")
                       for r in ROWS]
    w._rows_for = str(dummy)
    w._search_model = w._build_search_model(w._tracker_rows)
    return w, dummy, _PORow


def test_the_po_description_is_prefilled_in_the_ctr_box_but_nothing_is_picked_or_written():
    w, dummy, PORow = _synthetic_section()
    po = _synthetic_po(desc="Cleaning activities/Serenja NORM storage")
    w._add_row(PORow(po=po, item=po.items[0]))
    r = w._rows[0]
    assert r.ctr_combo.currentText() == "Cleaning activities/Serenja NORM storage"     # copied for you, no pasting
    assert w._chosen_row(r) is None and any("No CTR chosen" in x for x in w.problems(r)[0])
    assert [r.ctr_combo.itemData(i) for i in range(r.ctr_combo.count())][:2] != []     # the starred suggestions are listed
    seen, old = _patch_boxes()
    try:
        w._write()                                    # only the prefilled text: no CTR chosen -> nothing to write
    finally:
        _unpatch(old)
    assert any("Choose the CTR" in t for _k, t in seen) and dummy.stat().st_size == 0 and r in w._rows
    w._populate(r)                                    # reloading the rows keeps the text
    assert r.ctr_combo.currentText() == "Cleaning activities/Serenja NORM storage"
    r.ctr_combo.setEditText("my own words")
    w._populate(r)
    assert r.ctr_combo.currentText() == "my own words"                                  # what was typed is not overwritten
    w.deleteLater()


def test_the_typed_search_is_exact_for_a_ctr_number_and_fuzzy_only_for_words():
    from PySide6.QtCore import Qt
    w, _d, PORow = _synthetic_section()
    po = _synthetic_po()
    w._add_row(PORow(po=po, item=po.items[0]))
    r = w._rows[0]

    def shown(q):
        r.proxy.set_query(q)
        return [r.proxy.index(i, 0).data(Qt.ItemDataRole.UserRole) for i in range(r.proxy.rowCount())]
    assert shown("26-120") == [3]                                   # a CTR number: exact
    assert shown("26-999") == []                                    # ... and never fuzzy, however alike
    assert shown("hydrocyclone serenja") and set(shown("hydrocyclone serenja")) == {3, 4}      # all words, exact
    assert set(shown("hidrocyclone serenga")) >= {3, 4}             # a typo: found by the description
    assert shown("") == [] and shown("qqqqq wwwww") == []
    w.deleteLater()


def test_the_ctr_file_is_forgotten_when_the_ctr_changes_and_a_yellow_reminder_does_not_block():
    ctr_file = H.SANDBOX / "ctr_file_111.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws[CFG["ctr_extract"].get("cell_ctr_ref", "E3")] = "CTR-26-111 AZN"
    ws["F3"], ws["G3"] = "Pricebook #: ", 4410000001                       # the PO's own pricebook
    wb.save(ctr_file)
    w, dummy, PORow = _synthetic_section()
    po = _synthetic_po(pb="4410000001")
    w._add_row(PORow(po=po, item=po.items[0]))
    r = w._rows[0]
    yellow = "color:#B8860B"
    w._select_row(r, 2)                                                    # CTR-26-111
    assert any("Pricebook not checked" in m for m in w.reminders(r)) and yellow in r.note.text()
    w._read_pricebook(r, str(ctr_file))
    w._refresh_note(r)
    assert (r.ctr_pricebook, r.ctr_file_ctr) == ("4410000001", "CTR-26-111") and w.reminders(r) == []
    assert not any("Pricebook differs" in x for x in w.problems(r)[0])
    w._select_row(r, 3)                                                    # another CTR: the file no longer applies
    assert r.ctr_file == "" and r.ctr_pricebook == "" and r.file_btn.text() == "CTR file…"
    assert any("Pricebook not checked" in m for m in w.reminders(r))
    w._read_pricebook(r, str(ctr_file))                                    # the file of 111 for the chosen 120
    w._refresh_note(r)
    rem = w.reminders(r)
    assert rem and "CTR-26-111" in rem[0] and "CTR-26-120" in rem[0] and yellow in r.note.text(), rem
    # only a reminder: the write still goes ahead as far as the confirmation, which lists it
    from ctr_tools import po_view as PV
    real_read = PV.read_tracker_rows
    PV.read_tracker_rows = lambda _path: w._tracker_rows               # the dummy tracker file has no sheet to read
    seen, old = _patch_boxes(answer=False)
    try:
        w._write()
    finally:
        _unpatch(old)
        PV.read_tracker_rows = real_read
    question = next(t for k, t in seen if k == "question")
    assert "Check before writing" in question and "CTR-26-111" in question
    w.deleteLater()


# ── the CTR box: the list follows what is in the box; Up/Down and the wheel never pick ───────────────────────

def _shown_labels(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def test_the_dropdown_follows_what_is_typed_and_arrow_keys_never_pick():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    w, _d, PORow = _synthetic_section()
    po = _synthetic_po(desc="Cleaning activities Serenja NORM storage")
    w._add_row(PORow(po=po, item=po.items[0]))
    r = w._rows[0]
    c = r.ctr_combo
    c.showPopup()
    starred = _shown_labels(c)
    assert starred and all(x.startswith("★") for x in starred)             # untouched: the suggestions for the description
    c.hidePopup()
    assert w._chosen_row(r) is None and c.currentText() == "Cleaning activities Serenja NORM storage"
    # something typed: the list is that search, not the old suggestions
    c.lineEdit().setText("painting racks")
    r.proxy.set_query("painting racks")
    c.showPopup()
    typed = _shown_labels(c)
    c.hidePopup()
    assert typed and not any(x.startswith("★") for x in typed) and "row 1 " in typed[0], typed
    assert c.currentText() == "painting racks" and w._chosen_row(r) is None  # the typed text stays; nothing was picked
    # Down / Up / the wheel only open the list: no CTR is picked and the text does not change
    c.lineEdit().setFocus()
    QTest.keyClick(c.lineEdit(), Qt.Key.Key_Down)
    H.pump(5)
    assert c.currentText() == "painting racks" and c.currentIndex() == -1 and w._chosen_row(r) is None
    c.hidePopup()
    # a picked row keeps showing the suggestions of the description (+ itself)
    w._select_row(r, 2)
    c.showPopup()
    assert any("row 2 " in x for x in _shown_labels(c)) and w._chosen_row(r) == 2
    c.hidePopup()
    w.deleteLater()


def test_an_emptied_box_gets_the_po_description_back_when_left():
    w, _d, PORow = _synthetic_section()
    po = _synthetic_po(desc="Cleaning activities Serenja NORM storage")
    w._add_row(PORow(po=po, item=po.items[0]))
    r = w._rows[0]
    r.ctr_combo.setEditText("")
    r.ctr_combo.lineEdit().editingFinished.emit()
    assert r.ctr_combo.currentText() == "Cleaning activities Serenja NORM storage" and w._chosen_row(r) is None
    w.deleteLater()


# ── a failed write loses nothing ─────────────────────────────────────────────────────────────────────────

def test_a_failed_po_write_keeps_every_item_and_says_so():
    from ctr_tools import po_view as PV
    w, dummy, PORow = _synthetic_section()
    po = _synthetic_po()
    w._add_row(PORow(po=po, item=po.items[0]))
    r = w._rows[0]
    w._select_row(r, 2)
    r.type_combo.setEditText("LIMIT/FIX")
    real_read, real_write = PV.read_tracker_rows, PV.write_pos_fast
    PV.read_tracker_rows = lambda _p: w._tracker_rows
    def locked(*a, **k):
        raise ValueError("Cannot save the tracker — it is open in Excel.")
    PV.write_pos_fast = locked
    seen, old = _patch_boxes()
    try:
        w._write()
        assert H.wait(lambda: any(k == "critical" for k, _t in seen), 30), seen
    finally:
        _unpatch(old)
        PV.read_tracker_rows, PV.write_pos_fast = real_read, real_write
    msg = next(t for k, t in seen if k == "critical")
    assert "open in Excel" in msg and "Nothing was lost" in msg
    assert w._rows == [r] and w._chosen_row(r) == 2 and r.type_combo.currentText() == "LIMIT/FIX"   # all still there
    assert w._write_btn.isEnabled()                                                                   # and can be retried
    w.deleteLater()


def test_a_failed_ctr_write_keeps_the_batch_and_says_so():
    from ctr_tools import tracker_window as TW
    from ctr_tools.tracker import CTREntry
    dummy = H.SANDBOX / "dummy_tracker2.xlsm"
    dummy.write_bytes(b"")
    w = TW.CTRTrackerWidget()
    w._tracker_edit.setText(str(dummy))
    entry = CTREntry(client="C", ctr_number="CTR-26-1", description="d", value=1.0, currency="USD", job_type="x")
    w._batch = [entry]
    real = TW.write_entries_fast
    def locked(*a, **k):
        raise ValueError("Cannot save the tracker — it is locked.")
    TW.write_entries_fast = locked
    seen, old = _patch_boxes()
    try:
        w._start_write(skip_confirm=True)
        assert H.wait(lambda: any(k == "critical" for k, _t in seen), 30), seen
    finally:
        _unpatch(old)
        TW.write_entries_fast = real
    msg = next(t for k, t in seen if k == "critical")
    assert "locked" in msg and "Nothing was lost" in msg and w._batch == [entry]
    w.deleteLater()
