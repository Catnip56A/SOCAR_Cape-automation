"""Reload: read the CTR Request file again (Reload all, or the cells picked with Ctrl+Shift+click: every cell in the
picked rows x picked columns). Rows are built by the same code as the first load; only the chosen cells are taken over;
cells the user changed (or gave a formula) are replaced after a question; nothing real is touched (the request file
is only read, and the file's changes are made on the parsed copy)."""
import copy

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

import harness as H
from ctr_tools import window as W
from ctr_tools.parser import parse_ctr_request


def _data():
    return parse_ctr_request(str(H.INPUTS / "CTR_NAMES_DB & CTR_REQUEST 263.xlsx"))


def _snapshot(tbl):
    return [[(tbl.item(r, c).text() if tbl.item(r, c) else None) for c in range(tbl.columnCount())]
            for r in range(tbl.rowCount())]


def _all(tbl):
    return range(tbl.rowCount()), range(tbl.columnCount())


def test_every_loaded_row_knows_where_it_came_from_and_what_it_held():
    w = H.generator()
    for cat, tbl in w._reload_tables().items():
        for r in range(tbl.rowCount()):
            src = w._row_source(tbl, r)
            assert src and src[0] == cat, (cat, r, src)
            assert not any(w._cell_changed_by_user(tbl.item(r, c)) for c in range(tbl.columnCount())), (cat, r)
    w._add_manpower_row()
    assert w._row_source(w._req_manpower_tbl, w._req_manpower_tbl.rowCount() - 1) is None   # added by hand


def test_reload_all_restores_what_the_file_gives_and_drops_formulas():
    w = H.generator()
    w.show()
    data = _data()
    for cat, tbl in w._reload_tables().items():
        before = _snapshot(tbl)
        n = tbl.rowCount()
        # type over a quantity (a formula in manpower) and a description in the first row
        qty = {"manpower": W._MP_QTY, "equipment": W._EQ_QTY, "consumables": W._CS_QTY, "transport": W._TR_QTY}[cat]
        desc = {"manpower": W._MP_NAT, "equipment": W._EQ_UNIT, "consumables": W._CS_UNIT, "transport": W._TR_UOM}[cat]   # free text; a Description edit would teach a rename
        if n == 0:
            continue
        item = tbl.item(0, qty)
        if cat == "manpower":
            tbl.itemDelegateForColumn(qty)  # (a formula goes in the way the editor does it)
            was = tbl.blockSignals(True)
            item.setData(W._FORMULA_ROLE, ("=2+2", "4", None))
            tbl.blockSignals(was)
        item.setText("777")
        tbl.item(0, desc).setText("typed over")
        rows, cols = _all(tbl)
        counts = w._reload_counts(cat, rows, cols)
        assert counts["changed"] >= 2, (cat, counts)
        res = w._reload_from_data(cat, rows, cols, data, everything=True)
        assert res["gone"] == 0 and res["added"] == 0, (cat, res)
        assert _snapshot(tbl) == before, cat                          # exactly what the first load gave
        assert not w._reload_counts(cat, *_all(tbl))["changed"], cat
        assert tbl.item(0, qty).data(W._FORMULA_ROLE) is None or tbl.item(0, qty).data(W._FORMULA_ROLE)[1] != tbl.item(0, qty).text()


def test_a_reload_of_picked_cells_takes_the_picked_rows_times_the_picked_columns_only():
    w = H.generator()
    w.show()
    data = _data()
    tbl = w._req_manpower_tbl
    assert tbl.rowCount() >= 3
    before = _snapshot(tbl)
    for r, c in ((0, W._MP_QTY), (0, W._MP_NUMEMP), (1, W._MP_QTY), (1, W._MP_NUMEMP), (0, W._MP_NAT), (2, W._MP_QTY)):
        tbl.item(r, c).setText("55")
    rows, cols = {0, 1}, {W._MP_QTY, W._MP_NUMEMP}               # picks (0,QTY) and (1,NUMEMP): 2 rows x 2 columns
    res = w._reload_from_data("manpower", rows, cols, data)
    assert res["cells"] == 4 and res["changed"] == 4
    now = _snapshot(tbl)
    for r in (0, 1):
        for c in cols:
            assert now[r][c] == before[r][c], (r, c)              # all four read again, though only two were picked
    assert tbl.item(0, W._MP_NAT).text() == "55"                 # a column that was not picked keeps the user's text
    assert tbl.item(2, W._MP_QTY).text() == "55"                 # a row that was not picked is untouched
    assert tbl.item(0, W._MP_TOTAL).text() == f"{float(tbl.item(0, W._MP_NUMEMP).text()) * float(tbl.item(0, W._MP_QTY).text()) * float(tbl.item(0, W._MP_RATE).text() or 0):.2f}"


def test_a_changed_file_is_picked_up_and_rows_that_vanished_or_appeared_are_reported():
    w = H.generator()
    w.show()
    tbl = w._req_equip_tbl
    n = tbl.rowCount()
    assert n >= 2
    data = copy.deepcopy(_data())
    eq = [i for i in data["equipment_rows"]]
    eq[0]["quantity"] = 987
    data["equipment_rows"] = eq[:-1]                              # the last item is gone from the file
    last_scaffold = 1 if data.get("scaffold") else 0
    res = w._reload_from_data("equipment", range(tbl.rowCount()), range(tbl.columnCount()), data, everything=True)
    assert float(tbl.item(0, W._EQ_QTY).text()) == 987                # the file's new value
    assert res["gone"] == 1 + 0 * last_scaffold or res["gone"] >= 1
    assert tbl.rowCount() == n                                    # a vanished row stays as it is
    data2 = _data()
    data2["equipment_rows"] = data2["equipment_rows"] + [dict(data2["equipment_rows"][0], description="Brand new line", stock_code="")]
    res2 = w._reload_from_data("equipment", range(tbl.rowCount()), range(tbl.columnCount()), data2, everything=True)
    assert res2["added"] == 1 and tbl.rowCount() == n + 1
    assert w._row_source(tbl, n)[1] == len(data2["equipment_rows"]) - 1


def test_rows_added_by_hand_are_only_priced_again_and_never_cleared_by_a_pick():
    w = H.generator()
    w.show()
    data = _data()
    tbl = w._req_manpower_tbl
    w._add_manpower_row()
    r = tbl.rowCount() - 1
    tbl.item(r, W._MP_QTY).setText("8")
    res = w._reload_from_data("manpower", {r}, {W._MP_QTY}, data)           # a pick on a hand-made row: nothing to read
    assert res["cells"] == 0 and tbl.item(r, W._MP_QTY).text() == "8"
    res = w._reload_from_data("manpower", range(tbl.rowCount()), range(tbl.columnCount()), data, everything=True)
    assert res["manual"] == 1 and tbl.item(r, W._MP_QTY).text() == "8"      # priced again, the typed quantity stays
    assert w._reload_counts("manpower", {r}, {W._MP_QTY})["manual"] == 1


def test_ctrl_shift_click_picks_cells_and_letting_go_asks_before_anything_is_reloaded():
    w = H.generator()
    w.show()
    w.activateWindow()
    H.pump(10)
    tbl = w._req_manpower_tbl
    tbl.scrollToItem(tbl.item(0, W._MP_QTY))
    mods = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    sel_before = tbl.selectionModel().selectedRows()
    for r, c in ((0, W._MP_QTY), (1, W._MP_NUMEMP)):
        pos = tbl.visualRect(tbl.model().index(r, c)).center()
        QTest.mouseClick(tbl.viewport(), Qt.MouseButton.LeftButton, mods, pos)
    assert w._picks("manpower") == {(0, W._MP_QTY), (1, W._MP_NUMEMP)}
    assert tbl.selectionModel().selectedRows() == sel_before              # nothing was selected or opened for editing
    pos = tbl.visualRect(tbl.model().index(1, W._MP_NUMEMP)).center()
    QTest.mouseClick(tbl.viewport(), Qt.MouseButton.LeftButton, mods, pos)  # a second click un-picks
    assert w._picks("manpower") == {(0, W._MP_QTY)}
    QTest.mouseClick(tbl.viewport(), Qt.MouseButton.LeftButton, mods, tbl.visualRect(tbl.model().index(1, W._MP_NUMEMP)).center())
    # the question: No keeps the picks and reloads nothing; Yes starts the reload of the rows x columns
    calls, asked = [], []
    w._start_reload = lambda cat, rows, cols, everything: calls.append((cat, sorted(rows), sorted(cols), everything))
    real = QMessageBox.question
    try:
        QMessageBox.question = staticmethod(lambda parent, title, text, *a, **k: (asked.append((title, text)), QMessageBox.StandardButton.No)[1])
        tbl.item(0, W._MP_QTY).setText("123")
        w._ask_reload_picked("manpower")
        assert calls == [] and len(w._picks("manpower")) == 2
        title, text = asked[0]
        assert title.startswith("Reload picked cells of") and "2 row(s) × 2 column(s)" in text and "All 4 cell(s)" in text
        assert "1 cell(s) you typed over" in text
        QMessageBox.question = staticmethod(lambda parent, title, text, *a, **k: QMessageBox.StandardButton.Yes)
        w._ask_reload_picked("manpower")
        assert calls == [("manpower", [0, 1], [W._MP_NUMEMP, W._MP_QTY], False)]
    finally:
        QMessageBox.question = real
        del w._start_reload                                                # the shared Generator must not keep the stub
    QTest.keyClick(tbl, Qt.Key_Escape)                                     # Escape drops the picks
    assert w._picks("manpower") == set()


def test_the_real_reload_reads_the_file_again_and_reports_what_it_did():
    w = H.generator()
    w.show()
    tbl = w._req_equip_tbl
    before = _snapshot(tbl)
    tbl.item(0, W._EQ_QTY).setText("424242")
    w._start_reload("equipment", *_all(tbl), everything=True)
    assert H.wait(lambda: w._ctr_req_status.text().startswith("Reload all Equipment"), 120), w._ctr_req_status.text()
    assert "were yours and are replaced" in w._ctr_req_status.text()
    assert _snapshot(tbl) == before


def test_the_buttons_say_reload_all_and_transport_has_one_too():
    from PySide6.QtWidgets import QPushButton
    w = H.generator()
    texts = {b.text() for b in w.findChildren(QPushButton)}
    assert {"Reload all Manpower", "Reload all Equipment", "Reload all Consumables", "Reload all Transport"} <= texts
    assert not any(t.startswith("Re-match All") for t in texts)


def test_picking_with_ctrl_shift_lets_go_of_the_row_a_plain_click_selected():
    w = H.generator()
    w.show()
    w.activateWindow()
    H.pump(10)
    tbl = w._req_manpower_tbl
    tbl.scrollToItem(tbl.item(0, W._MP_QTY))
    QTest.mouseClick(tbl.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     tbl.visualRect(tbl.model().index(0, W._MP_TYPE)).center())
    H.pump(3)
    assert {i.row() for i in tbl.selectedItems()} == {0}                    # a plain click selects the row
    mods = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    QTest.mouseClick(tbl.viewport(), Qt.MouseButton.LeftButton, mods,
                     tbl.visualRect(tbl.model().index(1, W._MP_QTY)).center())
    assert not tbl.selectedItems()                                          # ... and Ctrl+Shift lets go of it
    assert w._picks("manpower") == {(1, W._MP_QTY)}
    w._clear_picks()


def _teach(w, cat, row, renamed):
    """Type a new Description over a loaded row: the app saves it as a rename of the file's text."""
    tbl = w._reload_tables()[cat]
    col = w._DESC_COL[cat]
    raw = tbl.item(row, col).data(W._DESC_ANCHOR_ROLE)
    tbl.item(row, col).setText(renamed)
    assert w._saved_rename_for_row(cat, row) == (raw, renamed), (cat, raw)
    return raw


def test_a_reloaded_description_is_the_files_own_text_and_the_saved_rename_is_kept_or_dropped():
    from ctr_tools.desc_renames import get_desc_rename, load_desc_renames
    w = H.generator()
    w.show()
    data = _data()
    for cat, store in (("manpower", "manpower"), ("equipment", "equipment"), ("consumables", "consumable")):
        tbl = w._reload_tables()[cat]
        col = w._DESC_COL[cat]
        file_text = tbl.item(0, col).text()
        raw = _teach(w, cat, 0, "My own wording")
        assert raw == file_text
        assert w._renames_in_play(cat, {0}, {col}) == [(raw, "My own wording")]
        assert w._renames_in_play(cat, {0}, {col + 1}) == []                  # the Description column is not part of it
        res = w._reload_from_data(cat, {0}, {col}, data, drop_renames=False)  # kept: the file's text shows
        assert tbl.item(0, col).text() == file_text and res["renames_dropped"] == 0
        assert w._saved_rename_for_row(cat, 0) == (raw, "My own wording")
        assert not w._cell_changed_by_user(tbl.item(0, col))                  # and it counts as loaded, not as an edit
        res = w._reload_from_data(cat, {0}, {col}, data, drop_renames=True)   # dropped: gone from the store and the file
        assert res["renames_dropped"] == 1 and w._saved_rename_for_row(cat, 0) is None
        assert tbl.item(0, col).text() == file_text
        if cat == "manpower":
            assert get_desc_rename(load_desc_renames(), store, raw) is None


def test_the_reload_asks_what_to_do_with_saved_renames_before_it_starts():
    from PySide6.QtWidgets import QMessageBox
    w = H.generator()
    w.show()
    tbl = w._req_manpower_tbl
    col = W._MP_DESC
    raw = _teach(w, "manpower", 0, "Reworded")
    Std = QMessageBox.StandardButton
    real = QMessageBox.question
    asked = []
    try:
        QMessageBox.question = staticmethod(lambda parent, title, text, *a, **k: (asked.append((title, text)), Std.Cancel)[1])
        before = w._ctr_req_status.text()
        w._start_reload("manpower", {0}, {col}, False)                     # Cancel: nothing happens at all
        assert w._ctr_req_status.text() == before and tbl.item(0, col).text() == "Reworded"
        title, text = asked[0]
        assert title == "Saved Description renames" and raw in text and "Reworded" in text and "Cancel" in text
        QMessageBox.question = staticmethod(lambda parent, title, text, *a, **k: Std.No)
        w._start_reload("manpower", {0}, {col}, False)                     # No: the file's text, the rename stays
        assert H.wait(lambda: tbl.item(0, col).text() == raw, 60)
        assert w._saved_rename_for_row("manpower", 0) == (raw, "Reworded")
        H.pump(5)
        _teach(w, "manpower", 0, "Reworded again")
        QMessageBox.question = staticmethod(lambda parent, title, text, *a, **k: Std.Yes)
        w._start_reload("manpower", {0}, {col}, False)                     # Yes: the file's text, the rename deleted
        assert H.wait(lambda: "saved rename(s) deleted" in w._ctr_req_status.text(), 60), w._ctr_req_status.text()
        assert w._saved_rename_for_row("manpower", 0) is None and tbl.item(0, col).text() == raw
        asked.clear()
        QMessageBox.question = staticmethod(lambda parent, title, text, *a, **k: (asked.append(text), Std.Yes)[1])
        w._start_reload("manpower", {0}, {W._MP_QTY}, False)                # no Description picked: no such question
        assert H.wait(lambda: w._ctr_req_status.text().startswith("Reload all Manpower"), 60)
        assert asked == []
    finally:
        QMessageBox.question = real
