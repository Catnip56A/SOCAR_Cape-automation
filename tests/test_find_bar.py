"""Ctrl+F find bar on tables, trees and proxy-model views."""
from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractItemView, QTableView, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
)

import harness as H
from common.find_bar import install_find


def _table():
    t = QTableWidget(6, 3)
    t.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)     # like the Generator tables
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    rows = [["alpha", "Foo bar", "1"], ["beta", "x", "foo"], ["gamma", "none", "2"],
            ["delta", "FOO", "3"], ["eps", "e", "4"], ["zeta", "xfoox", "5"]]
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            t.setItem(r, c, QTableWidgetItem(v))
    t.resize(600, 300)
    t.show()
    t.setFocus()
    H.pump()
    return t


def _search(bar, text):
    bar._edit.setText(text)
    bar._text_changed()


def test_ctrl_f_opens_the_bar_and_matches_are_counted_per_row():
    t = _table()
    bar = install_find(t)
    QTest.keyClick(t, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    H.pump()
    assert bar.isVisible() and bar._edit.hasFocus()
    _search(bar, "foo")
    assert bar._count.text() == "1 of 4"                  # rows 0, 1, 3, 5 — one match per row
    assert t.selectionModel().isRowSelected(t.currentRow(), t.rootIndex())
    assert t.state() != t.State.EditingState, "moving to a match must not open a cell editor"


def test_next_previous_wrap_and_case_insensitive():
    t = _table()
    bar = install_find(t)
    bar.open_bar()
    _search(bar, "FOO")
    start = t.currentRow()
    seen = []
    for _ in range(5):
        bar.step(1)
        seen.append(t.currentRow())
    assert seen[3] == seen[-1] or len(set(seen)) == 4, seen          # four rows, then it wraps
    bar.step(-1)
    assert t.currentRow() == seen[-2]
    _search(bar, "nomatch")
    assert bar._count.text() == "No matches"
    assert start in (0, 1, 3, 5)


def test_hidden_rows_are_skipped_and_escape_closes():
    t = _table()
    bar = install_find(t)
    bar.open_bar()
    t.setRowHidden(3, True)
    bar._invalidate()
    _search(bar, "foo")
    assert bar._count.text().endswith("of 3")
    QTest.keyClick(bar._edit, Qt.Key.Key_Escape)
    assert not bar.isVisible()


def test_tree_opens_collapsed_groups():
    tr = QTreeWidget()
    tr.setColumnCount(2)
    bar = install_find(tr)
    g = QTreeWidgetItem(tr, ["group", ""])
    d = QTreeWidgetItem(g, ["doc A", ""])
    QTreeWidgetItem(d, ["SP4700000004", "not in SAGE"])
    tr.collapseAll()
    tr.resize(500, 300)
    tr.show()
    H.pump()
    _search(bar, "4700000")
    assert bar._count.text() == "1 of 1"
    assert tr.currentItem().text(0) == "SP4700000004" and d.isExpanded() and g.isExpanded()


def test_proxy_view_skips_hidden_columns():
    m = QStandardItemModel(4, 3)
    for r in range(4):
        for c in range(3):
            m.setItem(r, c, QStandardItem("needle" if (r, c) == (2, 1) else f"r{r}c{c}"))
    m.item(3, 2).setText("needle too")
    p = QSortFilterProxyModel()
    p.setSourceModel(m)
    v = QTableView()
    v.setModel(p)
    v.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    bar = install_find(v)
    v.resize(500, 300)
    v.show()
    H.pump()
    v.setColumnHidden(1, True)
    bar._invalidate()
    _search(bar, "needle")
    assert bar._count.text() == "1 of 1" and v.currentIndex().row() == 3


def test_the_apps_own_tables_have_it():
    w = H.generator()
    for t in (w._req_manpower_tbl, w._req_equip_tbl, w._req_cons_tbl, w._transport_tbl, w._stats_panel._tree):
        assert hasattr(t, "_find_bar")


def test_matches_from_a_replaced_model_are_never_used_again():
    """The results tables get a new model on every Compare, and the bar is installed before the first one
    exists. A cached match pointed into the old model; F3 / Ctrl+F then crashed the app (access violation)."""
    import gc
    view = QTableView()
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    bar = install_find(view)                                   # installed while the table has no model, as in the app

    def model(rows):
        m = QStandardItemModel()
        for row in rows:
            m.appendRow([QStandardItem(v) for v in row])
        return m
    view.setModel(model([["apple", "x"], ["banana", "y"], ["bandana", "z"]]))
    view.resize(500, 200)
    view.show()
    H.pump()
    _search(bar, "an")
    assert len(bar._matches()) == 2                            # banana, bandana: cached now
    old_cache = bar._cache
    assert old_cache is not None
    view.setModel(model([["angle", "1"], ["anchor", "2"], ["other", "3"], ["grand", "4"]]))   # a new Compare
    gc.collect()
    H.pump()
    found = bar._matches()                                     # the bar notices the new model when it is used next
    assert found is not old_cache and bar._cache is found     # the old matches are dropped, not reused
    assert [str(m.data()) for m in found] == ["angle", "anchor", "grand"]     # from the NEW model
    assert all(m.model() is view.model() for m in found)
    bar.step(1)                                                # F3 / Enter on the new content: no crash
    assert view.currentIndex().model() is view.model() and view.currentIndex().data() in ("angle", "anchor", "grand")
    bar.open_bar()                                             # and Ctrl+F
    assert "of 3" in bar._count.text() or "3 matches" in bar._count.text()
    # the new model's changes are noticed (the signals are connected to it)
    view.model().appendRow([QStandardItem("bang"), QStandardItem("5")])
    assert len(bar._matches()) == 4
    # a stale match handed straight to _goto is ignored
    stale_model = model([["anything"]])
    stale = stale_model.index(0, 0)
    before = view.currentIndex()
    bar._goto(stale)
    assert view.currentIndex() == before
    # the model removed altogether
    view.setModel(None)
    assert bar._matches() == [] and bar._cache is not None
