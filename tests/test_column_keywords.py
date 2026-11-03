"""Column keywords (Settings › Saved lists): which header spellings find which column, the header rows, and the tools
around them (merge shipped, undo my entries, reset, import / export, checks before saving, test on a file) — plus the
same "Undo my entries" for the Stock Code types. Everything runs against the sandbox copies of the user's files."""
import json

import openpyxl

import harness as H
from comparator import column_keywords as CK
from comparator.sheet_parser import parse_workbook
from test_parser_notes import _ctr


def _fresh():
    CK.reset_to_shipped()
    return CK.current()


def test_the_shipped_words_reproduce_the_old_header_rules():
    cfg = _fresh()
    ctr = {"General Equipment": "Description", "Description": "Description", "Quantity": "Quantity", "QTY": "Quantity",
           "Unit": "Unit", "Unit Price": "Rate", "Rate per Day": "Rate", "Hourly Rate, AZN": "Rate", "Days": "Days",
           "Total": "Total", "Tot": "Total", "CTR total": "Total", "Total AZN": "Total",
           "EquipmentCode": "Stock Code", "ProductCode": "Stock Code", "Total SC Eq Cost": CK.IGNORE,
           "Price AZN": None, "Mark Up, %": None, "Total Plant & Equipment": None}
    for header, want in ctr.items():
        assert CK.match(cfg, "ctr", header)[0] == want, header
    mr = {"STOCKCODE *": "Stock Code", "№": "No", "Qty *": "Qty", "Unit *": "Unit",
          "Item Specifications in English: name, size/dimensions": "Description", "Required Delivery Date": "Delivery Date",
          "Planned Return Date *(for rechargeable items)": "Return Date", "Rechargeable": "Rechargeable",
          "Allocation *": "Allocation", "Comments(Any Specefic descriptions)": None}
    for header, want in mr.items():
        assert CK.match(cfg, "mr", header)[0] == want, header


def test_the_most_specific_word_wins_and_an_equals_sign_means_exactly():
    cfg = {"ctr": {"Unit": ["=unit"], "Rate": ["unit price"], "Total": ["total"], "Other": ["total sc"]}, "mr": {}}
    assert CK.match(cfg, "ctr", "Unit")[0] == "Unit"
    assert CK.match(cfg, "ctr", "Unit Price")[0] == "Rate"             # not Unit: "=unit" is exact, and "unit price" is longer
    assert CK.match(cfg, "ctr", "Total SC Eq Cost")[0] == "Other"       # two words beat one
    assert CK.match(cfg, "ctr", "Total")[0] == "Total" and CK.match(cfg, "ctr", "Units")[0] is None
    assert CK.word_matches("qty", "Qty *") and CK.word_matches("item specification", "Item Specifications in English")
    assert not CK.word_matches("tot", "Subtotal")                       # starts at a word, not inside one


def test_the_users_copy_is_created_from_the_shipped_lists_saved_checked_and_survives_a_bad_file():
    _fresh()
    assert CK._KEYWORDS_PATH.exists() and CK.read(CK._KEYWORDS_PATH) == CK.shipped()
    cfg = CK.current()
    cfg = json.loads(json.dumps(cfg))
    cfg["ctr"]["Quantity"].append("qnt")
    cfg["rows"]["ctr_consumables"] = 20
    CK.save(cfg)
    assert CK.current()["ctr"]["Quantity"][-1] == "qnt" and CK.header_row(CK.current(), "ctr_consumables") == 20
    CK._KEYWORDS_PATH.write_text("{ this is not json", encoding="utf-8")        # a damaged file: the shipped lists are used
    CK._cache = None
    assert CK.current() == CK.shipped()
    for bad in ({}, {"ctr": {}, "mr": "x"}, {"ctr": {"Quantity": "qty"}, "mr": {}}, {"ctr": {}, "mr": {}, "rows": {"mr": 0}},
                {"ctr": {}, "mr": {}, "rows": {"mr": "nine"}}, [1, 2]):
        try:
            CK.check_structure(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} was accepted")
    _fresh()


def test_merge_undo_and_reset_work_like_the_stock_code_types_ones():
    cfg = json.loads(json.dumps(_fresh()))
    cfg["ctr"]["Quantity"] += ["qnt", "=amount"]                          # my words
    cfg["ctr"]["Total"].remove("ctr total")                               # I deleted a shipped one
    cfg["rows"]["mr"] = 12
    CK.save(cfg)
    assert {w for _s, _c, w in CK.own_entries(CK.current())} == {"qnt", "=amount"}
    assert CK.new_shipped_words(CK.current()) == 1                        # "ctr total" is available again
    assert CK.merge_shipped() == 1                                        # ... and merging brings it back, keeping mine
    now = CK.current()
    assert "ctr total" in now["ctr"]["Total"] and "qnt" in now["ctr"]["Quantity"] and now["rows"]["mr"] == 12
    assert CK.undo_my_entries() == 2                                      # my two words go, the shipped stay, the row stays
    now = CK.current()
    assert "qnt" not in now["ctr"]["Quantity"] and "=amount" not in now["ctr"]["Quantity"] and now["rows"]["mr"] == 12
    assert CK.own_entries(now) == []
    cfg = json.loads(json.dumps(now))
    cfg["ctr"]["Days"].append("dd")
    CK.save(cfg)
    CK.reset_to_shipped()                                                 # reset: shipped words AND shipped rows
    assert CK.current() == CK.shipped()


def test_import_is_checked_first_and_export_roundtrips(tmp_path=None):
    cfg = json.loads(json.dumps(_fresh()))
    cfg["ctr"]["Quantity"].append("qnt")
    out = H.SANDBOX / "kw_export.json"
    CK.export_to(out, cfg)
    assert CK.import_from(out)["ctr"]["Quantity"][-1] == "qnt"
    bad = H.SANDBOX / "kw_bad.json"
    for text, why in (("{ nope", "JSON"), ('{"ctr": {}}', "mr"), ('{"ctr": {}, "mr": {}, "rows": {"mr": 9999}}', "between")):
        bad.write_text(text, encoding="utf-8")
        try:
            CK.import_from(bad)
        except ValueError as exc:
            assert why in str(exc), (text, str(exc))
        else:
            raise AssertionError(text)
    try:
        CK.import_from(H.SANDBOX / "no_such.json")
    except ValueError:
        pass
    else:
        raise AssertionError("a missing file was accepted")
    assert CK.current() == CK.shipped() or CK.current()["ctr"]["Quantity"] != cfg["ctr"]["Quantity"]   # nothing changed by importing
    _fresh()


def test_risky_words_are_reported_before_they_are_saved():
    cfg = json.loads(json.dumps(_fresh()))
    assert CK.problems(cfg) == []
    cfg["ctr"]["Quantity"] += ["q", "date", "unit price"]                  # too short, generic, and already under Rate
    cfg["ctr"]["Days"] = []
    cfg["rows"]["mr"] = 0
    text = " ".join(p["text"] for p in CK.problems(cfg))
    assert "“q” is very short" in text and "“date” is very generic" in text
    assert "“unit price” is in both Rate and Quantity" in text or "“unit price” is in both Quantity and Rate" in text
    assert "Days: no words" in text and "Header row (mr)" in text
    assert any(p["level"] == "error" for p in CK.problems(cfg))            # an invalid row stops a save
    _fresh()


def test_a_word_added_in_settings_teaches_the_parser_a_header_and_says_so():
    path = _ctr("kw_qnt.xlsx", cons_code_header="ProductCode", cons_header=("Description", "Qnt", "Unit", "Unit Price", "Total"))
    try:
        _fresh()
        t = parse_workbook(path)[0]
        warn = " ".join(n["text"] for n in t["notes"] if n["level"] == "warn")
        assert "no Quantity column was recognised" in warn and "Column keywords" in warn      # not silent
        cfg = json.loads(json.dumps(CK.current()))
        cfg["ctr"]["Quantity"].append("=qnt")
        CK.save(cfg)
        t = parse_workbook(path)[0]
        cons = t["data"][t["data"]["_Section"] == "Consumables & Materials"]
        assert cons["Quantity"].notna().all() and len(cons) == 12
        info = " ".join(n["text"] for n in t["notes"])
        assert "“Qnt” (column C) was read as Quantity (your word “qnt”)" in info
        assert not [n for n in t["notes"] if n["level"] == "warn"]
    finally:
        _fresh()


def test_two_columns_for_one_standard_name_are_reported():
    path = H.SANDBOX / "kw_dup.xlsx"
    wb = openpyxl.load_workbook(_ctr("kw_dup_src.xlsx", cons_code_header="ProductCode"))
    ws = wb["Pricing"]
    ws["G20"] = "Unit Price 2"                                              # a second Rate column in the consumables header
    wb.save(path)
    t = parse_workbook(path)[0]
    assert any("both read as Rate" in n["text"] and n["level"] == "warn" for n in t["notes"]), t["notes"]


def test_the_mr_header_row_is_configurable():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "NCH-1"
    heads = ["№", "STOCKCODE *", "Item Specifications", "", "Qty *", "Unit *"]
    for c, h in enumerate(heads, start=1):
        ws.cell(12, c, h)                                                   # the header sits on row 12, not 9
    for r, code in enumerate(["AN0100000001", "AN0100000002"], start=13):
        ws.cell(r, 1, r - 12)
        ws.cell(r, 2, code)
        ws.cell(r, 3, "THING")
        ws.cell(r, 5, 1)
        ws.cell(r, 6, "EA")
    path = H.SANDBOX / "kw_mr.xlsx"
    wb.save(path)
    try:
        _fresh()
        assert parse_workbook(path) == []                                   # row 9 is empty: nothing found (and said)
        cfg = json.loads(json.dumps(CK.current()))
        cfg["rows"]["mr"] = 12
        CK.save(cfg)
        t = parse_workbook(path)
        assert len(t) == 1 and list(t[0]["data"]["Stock Code"]) == ["AN0100000001", "AN0100000002"]
    finally:
        _fresh()


def test_ctr_header_rows_can_be_set_for_equipment_and_consumables_and_a_wrong_row_is_warned_about():
    path = _ctr("kw_rows.xlsx", cons_code_header="ProductCode",
                cons_header=("Item", "Count", "Measure", "Cost each", "Sum"))     # the consumables header is not recognisable
    try:
        _fresh()
        base = " ".join(n["text"] for n in parse_workbook(path)[0]["notes"])
        assert "not recognised" in base                                       # (the unknown header row is warned about)
        cfg = json.loads(json.dumps(CK.current()))
        cfg["rows"]["ctr_equipment"], cfg["rows"]["ctr_consumables"] = 10, 20
        cfg["ctr"]["Quantity"].append("=count")
        cfg["ctr"]["Description"].append("=item")
        CK.save(cfg)
        t = parse_workbook(path)[0]
        assert set(t["data"]["_Section"]) == {"Plant & Equipment", "Consumables & Materials"}
        cons = t["data"][t["data"]["_Section"] == "Consumables & Materials"]
        assert len(cons) == 12 and cons["Quantity"].notna().all() and cons["Description"].notna().all()
        cfg["rows"]["ctr_consumables"] = 14                                    # a row that is not a header
        CK.save(cfg)
        warn = " ".join(n["text"] for n in parse_workbook(path)[0]["notes"] if n["level"] == "warn")
        assert "Row 14" in warn and "no recognisable column names" in warn
        cfg["rows"]["ctr_consumables"] = 400
        CK.save(cfg)
        assert any("below the end of the sheet" in n["text"] for n in parse_workbook(path)[0]["notes"])
    finally:
        _fresh()


def test_the_editor_shows_the_words_marks_mine_and_checks_before_saving():
    from PySide6.QtWidgets import QMessageBox
    from common.keywords_view import KeywordsDialog, describe, split_words
    assert split_words(" qty, =unit ,\nunit price,qty") == ["qty", "=unit", "unit price"]
    cfg = _fresh()
    dlg = KeywordsDialog(None, cfg)
    assert dlg.collected() == cfg                                              # nothing edited: the same configuration
    row = next(r for r in range(dlg._table.rowCount())
               if (dlg._table.item(r, 0).text(), dlg._table.item(r, 1).text()) == ("CTR", "Quantity"))
    dlg._table.item(row, 2).setText("=quantity, =qty, =qnt")
    assert dlg._table.item(row, 3).text() == "=qnt"                            # "yours" lists what is not shipped
    assert "=qnt" in dlg.collected()["ctr"]["Quantity"]
    dlg._mr_row.setValue(12)
    dlg._eq_row.setValue(10)
    rows = dlg.collected()["rows"]
    assert rows == {"mr": 12, "ctr_equipment": 10, "ctr_consumables": None}
    # a risky word asks first: Cancel keeps the window open and nothing is returned
    dlg._table.item(row, 2).setText("=quantity, q")
    real = QMessageBox.question
    asked = []
    try:
        QMessageBox.question = staticmethod(lambda p, t, text, *a, **k: (asked.append(text), QMessageBox.StandardButton.Cancel)[1])
        dlg._save()
        assert asked and "“q” is very short" in asked[0] and not hasattr(dlg, "_result")
        QMessageBox.question = staticmethod(lambda p, t, text, *a, **k: QMessageBox.StandardButton.Yes)
        dlg._save()
        assert dlg.result_cfg()["ctr"]["Quantity"] == ["=quantity", "q"]
    finally:
        QMessageBox.question = real
    report = describe(cfg, "x.xlsx", parse_workbook(_ctr("kw_desc.xlsx")))
    assert "x.xlsx" in report and "Pricing" in report and "columns:" in report and "CTR equipment auto" in report


def test_settings_has_the_row_with_its_counts_and_buttons():
    from PySide6.QtWidgets import QPushButton, QToolButton
    from common import settings_view as SVW
    _fresh()
    w = H.generator()
    tab = SVW.SettingsTab(w)
    assert "words" in tab._counts["keywords"].text() and "yours" not in tab._counts["keywords"].text()
    texts = {b.text() for b in tab.findChildren(QPushButton)}
    assert {"Edit…", "Reset…"} <= texts
    more = [b for b in tab.findChildren(QToolButton) if b.text().startswith("More")]
    menus = [[a.text() for a in b.menu().actions()] for b in more]
    assert ["Merge shipped…", "Undo my entries…"] in menus                         # Stock Code types: the same dropdown ...
    assert ["Merge shipped…", "Undo my entries…", "Import…", "Export…"] in menus    # ... as the Column keywords
    cfg = json.loads(json.dumps(CK.current()))
    cfg["ctr"]["Quantity"].append("qnt")
    CK.save(cfg)
    tab.refresh()
    assert "1 yours" in tab._counts["keywords"].text()
    _fresh()


def test_stock_code_types_undo_my_entries_keeps_the_shipped_list():
    import ctr_tools.stockcode_types as ST
    ship_path = H.SANDBOX / "undo_shipped_types.json"
    ship_path.write_text(json.dumps({"equipment": ["E1", "E2", "B1"], "consumables_stockcodes": ["C1", "B1"],
                                     "confirmed": {"B1": "Equipment"}}), encoding="utf-8")
    old = ST._BUNDLED_DEFAULT_PATH
    ST._BUNDLED_DEFAULT_PATH = ship_path
    try:
        ST._TYPES_PATH.unlink(missing_ok=True)
        ST._cache = None
        ST.load_types()                                                       # seeded from the shipped list
        ST.set_type("NEW1", ST.EQUIPMENT)                                      # my entry
        ST.set_type("C1", ST.EQUIPMENT)                                        # I moved a shipped code
        ST.confirm_types({"E2": "Equipment"})                                  # a decision of mine about a shipped code
        types = ST.read_types(ST._TYPES_PATH)
        types["equipment"].discard("E1")                                       # I deleted a shipped code
        ST.save_types(types)
        mine, ship = ST.read_types(ST._TYPES_PATH), ST.shipped_types()
        assert ST.my_entries_count(mine, ship) == 3                            # NEW1, C1 moved, E2's decision
        assert ST.undo_my_entries() == 3
        after = ST.read_types(ST._TYPES_PATH)
        assert "NEW1" not in after["equipment"] and "C1" in after["consumable"] and "C1" not in after["equipment"]
        assert "E1" not in after["equipment"]                                  # deleted shipped codes stay deleted ...
        assert ST.merge_shipped() == 1 and "E1" in ST.read_types(ST._TYPES_PATH)["equipment"]    # ... until merged
        assert "E2" not in ST.read_types(ST._TYPES_PATH)["confirmed"] and ST.read_types(ST._TYPES_PATH)["confirmed"] == {"B1": "Equipment"}
    finally:
        ST._BUNDLED_DEFAULT_PATH = old
        ST._TYPES_PATH.unlink(missing_ok=True)
        ST._cache = None


def test_test_on_a_file_shows_every_table_it_found_below_the_summary():
    from common.keywords_view import TestResultDialog
    cfg = _fresh()
    tables = parse_workbook(_ctr("kw_view.xlsx", cons_code_header="ProductCode"))
    dlg = TestResultDialog(None, cfg, "kw_view.xlsx", tables)
    assert dlg.tabs.count() == len(tables) == 1
    tbl = dlg.tabs.widget(0)
    heads = [tbl.horizontalHeaderItem(c).text() for c in range(tbl.columnCount())]
    df = tables[0]["data"]
    assert tbl.rowCount() == len(df) == 18                                  # every line the parser read (15 with a code + 3 without), not only a count
    assert heads[0] == "Section" and "Stock Code" in heads and "Quantity" in heads
    assert not any(h.startswith("_") for h in heads)                         # the internal columns are not shown
    col = heads.index("Stock Code")
    assert {tbl.item(r, col).text() for r in range(tbl.rowCount())} == set(df["Stock Code"])
    assert "(18)" in dlg.tabs.tabText(0)
    empty = TestResultDialog(None, cfg, "nothing.xlsx", [])                 # nothing found: it says so instead of an empty grid
    assert empty.tabs.count() == 1 and empty.tabs.tabText(0) == "No tables"


def _wait_idle(win, what="parse"):
    H.wait(lambda: not win._reload_pending and all(not w.isRunning() for w in win._workers), 60)
    H.pump(20)


def test_saving_the_keywords_offers_to_re_read_the_loaded_files_and_marks_them_until_then():
    import app as A
    from PySide6.QtWidgets import QMessageBox
    from common import settings_view as SVW
    path = _ctr("kw_reread.xlsx", cons_code_header="ProductCode", cons_header=("Description", "Qnt", "Unit", "Unit Price", "Total"))
    Std = QMessageBox.StandardButton
    real = QMessageBox.question
    try:
        _fresh()
        win = A.MainWindow()
        win.show()
        H.pump(5)
        win._file_origin[("ctr", path.name)] = (str(path), A.file_stamp(str(path)))
        win._add_file_row(win._ctr_file_list, "ctr", path.name)
        win._parse("ctr", [(path.name, path.read_bytes())])
        _wait_idle(win)

        def warns():
            return sum(1 for t in win._ctr_tables for n in t["notes"] if n["level"] == "warn")
        assert warns() == 1 and win._changed_source_files() == []                 # read with the words of that moment
        cfg = json.loads(json.dumps(CK.current()))
        cfg["ctr"]["Quantity"].append("=qnt")
        CK.save(cfg)                                                              # what Settings › Edit… › Save writes
        assert win._changed_source_files() == [("CTR", path.name, "read with earlier Column keywords")]
        asked = []
        QMessageBox.question = staticmethod(lambda p, t, text, *a, **k: (asked.append(text), Std.No)[1])
        win._settings_tab.keywords_changed.emit()                                 # ... and the signal it sends
        assert asked and path.name in asked[0] and "previous words" in asked[0]
        label = win._ctr_file_list.itemWidget(win._ctr_file_list.item(0))._label
        assert label.text().startswith("⟳ ") and "previous Column keywords" in label.toolTip() and win._reload_btn.isVisible()
        assert warns() == 1                                                       # No: nothing was re-read, it stays marked
        QMessageBox.question = staticmethod(lambda p, t, text, *a, **k: Std.Yes)
        win._settings_tab.keywords_changed.emit()                                 # Yes: re-read with the new words
        _wait_idle(win)
        assert warns() == 0 and win._changed_source_files() == [] and not win._reload_btn.isVisible()
        assert not win._ctr_file_list.itemWidget(win._ctr_file_list.item(0))._label.text().startswith("⟳")
        tab = SVW.SettingsTab(H.generator())                                      # every save / merge / undo / import / reset sends it
        got = []
        tab.keywords_changed.connect(lambda: got.append(1))
        tab._merge_keywords()
        assert got == [1]
    finally:
        QMessageBox.question = real
        _fresh()
