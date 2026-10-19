"""Full text of a cut-off cell is shown on hover; a cell's own tooltip is kept."""
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem

import harness as H
from common.hover_text import install_elided_tooltips, tooltip_for

LONG = "A very long description that certainly does not fit in a narrow column of a table"


def _table():
    t = QTableWidget(3, 2)
    install_elided_tooltips(t)
    install_elided_tooltips(t)                            # idempotent
    t.setColumnWidth(0, 80)
    t.setColumnWidth(1, 400)
    t.resize(600, 200)
    t.show()
    H.pump()
    return t


def test_only_a_cut_off_cell_gets_the_full_text():
    t = _table()
    t.setItem(0, 0, QTableWidgetItem(LONG))
    t.setItem(1, 0, QTableWidgetItem("short"))
    t.setItem(0, 1, QTableWidgetItem(LONG))
    assert LONG in tooltip_for(t, t.model().index(0, 0))
    assert tooltip_for(t, t.model().index(1, 0)) is None          # fits
    assert tooltip_for(t, t.model().index(0, 1)) is None          # wide column: fits
    t.setColumnWidth(0, 700)
    H.pump()
    assert tooltip_for(t, t.model().index(0, 0)) is None          # widened later: nothing to add


def test_a_cells_own_tooltip_follows_the_full_text():
    t = _table()
    it = QTableWidgetItem(LONG)
    it.setToolTip("MR from selected documents: 10 of 40")
    t.setItem(2, 0, it)
    tip = tooltip_for(t, t.model().index(2, 0))
    assert LONG in tip and "MR from selected documents" in tip and tip.index(LONG) < tip.index("MR from")


def test_tree_rows_are_measured_after_their_indentation():
    tr = QTreeWidget()
    tr.setColumnCount(2)
    install_elided_tooltips(tr)
    tr.setColumnWidth(0, 120)
    top = QTreeWidgetItem(tr, [LONG, "x"])
    top.setToolTip(0, "document details")
    kid = QTreeWidgetItem(top, ["child " + LONG, "y"])
    top.setExpanded(True)
    tr.resize(500, 200)
    tr.show()
    H.pump()
    assert "document details" in tooltip_for(tr, tr.indexFromItem(top, 0))
    assert LONG in tooltip_for(tr, tr.indexFromItem(kid, 0))
    assert tooltip_for(tr, tr.indexFromItem(top, 1)) is None


def test_markup_in_a_cell_is_escaped():
    t = _table()
    t.setItem(0, 0, QTableWidgetItem("<b>bold</b> " + LONG))
    tip = tooltip_for(t, t.model().index(0, 0))
    assert "&lt;b&gt;" in tip and "<b>bold</b>" not in tip
