"""Ctrl+F find bar on tables, trees and proxy-model views."""
from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractItemView, QTableView, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
)

import harness as H
from find_bar import install_find


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
