"""Red and orange highlights stay visible when their row is selected (common/attention.py): in the results tables
(mismatch red, Combined orange, missing-Days orange, and any colour kind added later) and in the Statistics list
(red 'not counted' rows, the orange Days cell). The rest of a selected row keeps the normal blue selection.
The tests paint the widgets and read real pixels."""
import pandas as pd
from PySide6.QtCore import QModelIndex
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QTableView

import harness as H


def _table(highlights, days_needed=()):
    import app as A
    df = pd.DataFrame({"Stock Code": ["R1", "O1", "D1", "P1"], "Other": ["x", "x", "x", "x"]})
    model = A.PandasModel(df)
    model.set_highlights(highlights)
    model.set_days_needed(set(days_needed))
    view = QTableView()
    view.setModel(model)
    view.setItemDelegate(A.ResultTableDelegate(view))
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
    view.resize(500, 220)
    view.show()
    H.pump(10)
    return view


def _pixel(view, row, col):
    """The colour inside a cell, near its right edge (clear of the text and of the thin frame)."""
    idx = view.model().index(row, col)
    r = view.visualRect(idx)
    img = view.viewport().grab().toImage()
    return img.pixelColor(r.right() - 8, r.top() + r.height() // 2)


def _close(c: QColor, hexcolor: str, tol=6) -> bool:
    t = QColor(hexcolor)
    return abs(c.red() - t.red()) <= tol and abs(c.green() - t.green()) <= tol and abs(c.blue() - t.blue()) <= tol


def test_selected_red_and_orange_cells_keep_their_colours_in_the_results_tables():
    view = _table({(0, "Stock Code"): "mismatch", (1, "Stock Code"): "combined"}, {(2, "Stock Code")})
    view.selectAll()
    H.pump(10)
    assert _close(_pixel(view, 0, 0), "#FFCDD2")                 # red mismatch
    assert _close(_pixel(view, 1, 0), "#FFE0B2")                 # orange (Combined)
    assert _close(_pixel(view, 2, 0), "#FFE0B2")                 # orange: missing Days
    plain = _pixel(view, 3, 0)                                   # a plain cell of a selected row: the normal blue selection
    assert plain.blue() > plain.red() + 40, plain.name()
    other = _pixel(view, 0, 1)                                   # the neighbour of a highlighted cell is plain too
    assert other.blue() > other.red() + 40, other.name()


def test_unselected_highlights_look_the_same():
    view = _table({(0, "Stock Code"): "mismatch"}, {(2, "Stock Code")})
    assert _close(_pixel(view, 0, 0), "#FFCDD2") and _close(_pixel(view, 2, 0), "#FFE0B2")


def test_a_colour_kind_added_later_is_covered_without_touching_the_delegate():
    import app as A
    A._HIGHLIGHT_COLORS["test-green"] = (QColor("#C8E6C9"), QColor("#1B5E20"))
    try:
        view = _table({(3, "Stock Code"): "test-green"})
        view.selectAll()
        H.pump(10)
        assert _close(_pixel(view, 3, 0), "#C8E6C9")             # the new colour stays visible when selected
    finally:
        A._HIGHLIGHT_COLORS.pop("test-green", None)


def test_the_statistics_list_keeps_red_rows_and_the_orange_days_cell_when_selected():
    import stats.stats_view as SV
    import test_mr_days as T
    p = T._panel()                                               # E1 is priced (days from the CTR); E4 has no days
    p.resize(1200, 500)
    p.show()
    H.pump(10)
    e4, e1 = T._item(p, "E4"), T._item(p, "E1")

    def px(item, col):
        r = p._tree.visualRect(p._tree.indexFromItem(item, col))
        return p._tree.viewport().grab().toImage().pixelColor(r.left() + 4, r.top() + 2)     # a corner clear of the text
    p._tree.setCurrentItem(e4)
    H.pump(10)
    assert _close(px(e4, SV.C_DAYS), "#FFE0B2")                  # orange Days cell, selected row
    assert _close(px(e4, SV.C_QTY), "#FFEBEE")                   # the rest of a not-counted row: red tint, selected
    p._tree.setCurrentItem(e1)
    H.pump(10)
    sel = px(e1, SV.C_QTY)                                       # a counted row: the normal blue selection
    assert sel.blue() > sel.red() + 40, sel.name()


def test_mark_attention_is_what_new_tree_highlights_should_use():
    from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem
    from common.attention import ATTENTION_ROLE, mark_attention
    tree = QTreeWidget()
    it = QTreeWidgetItem(tree)
    mark_attention(it, 0, QColor("#112233"), QColor("#445566"))
    assert it.data(0, ATTENTION_ROLE) is True
    assert it.background(0).color().name() == "#112233" and it.foreground(0).color().name() == "#445566"
