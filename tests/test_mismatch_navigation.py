"""MA143: the Matched table's header click cycle (ascending, descending, original order), the up/down
mismatch arrows following the order on screen, stepping through red-only or orange-only rows in
Combined View, and the larger suggestion list of the document filter."""
import pandas as pd
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

import harness as H
from comparator import doc_filter


def _window():
    import app as A
    win = A.MainWindow()
    win.resize(1300, 800)
    win.show()
    H.pump(10)
    codes = ["C1", "C2", "C3", "C4", "C5"]
    # C1..C4: quantities differ (red). C2 and C4 also come from two MR documents (orange).
    mr_rows = [("C1", 5), ("C2", 5), ("C3", 7), ("C4", 1), ("C5", 3)]
    mr = pd.DataFrame({"Stock Code": [c for c, _ in mr_rows], "Qty": [q for _, q in mr_rows],
                       "Unit": "EA", "Description": "x", "_SourceFile": "mr1.xlsx", "_SourceSheet": "S"})
    mr2 = pd.DataFrame({"Stock Code": ["C2", "C4"], "Qty": [1, 1], "Unit": "EA", "Description": "x",
                        "_SourceFile": "mr2.xlsx", "_SourceSheet": "S"})
    mr = pd.concat([mr, mr2], ignore_index=True)
    ctr = pd.DataFrame({"Stock Code": codes, "Quantity": [6, 9, 7, 2, 3], "Unit": "EA", "Description": "x",
                        "Rate": "1", "_SourceFile": "c.xlsx", "_SourceSheet": "S",
                        "_Section": "Plant & Equipment"})
    win._mr_df, win._ctr_df = mr, ctr
    win._run_compare()
    H.pump(30)
    assert H.wait(lambda: not win._stats_workers, 60)
    return win


def _clicked_codes(win, n):
    """Press the down arrow n times and return the Stock Code under the cursor each time."""
    out = []
    for _ in range(n):
        win._next_mm_btn.click()
        H.pump(3)
        view = win._tab_matched
        col = [view.model().headerData(c, Qt.Orientation.Horizontal) for c in range(view.model().columnCount())].index("Stock Code")
        out.append(view.model().index(view.currentIndex().row(), col).data())
    return out


def test_the_header_click_cycles_ascending_descending_then_original_order():
    win = _window()
    view = win._tab_matched
    header = view.horizontalHeader()
    assert header.isSortIndicatorClearable()
    names = [view.model().headerData(c, Qt.Orientation.Horizontal) for c in range(view.model().columnCount())]
    col = names.index("Qty (MR)")
    x = header.sectionViewportPosition(col) + header.sectionSize(col) // 2

    def stock_codes():
        c = names.index("Stock Code")
        return [view.model().index(r, c).data() for r in range(view.model().rowCount())]

    original = stock_codes()
    for expected in ("asc", "desc", "none"):
        QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(x, 5))
        H.pump(5)
        state = "none" if header.sortIndicatorSection() < 0 else (
            "asc" if header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder else "desc")
        assert state == expected, (state, expected)
    assert stock_codes() == original                     # the third click restored the original order


def test_the_arrows_follow_the_order_on_screen_not_the_original_order():
    win = _window()
    win._compare_vals_btn.setChecked(True)               # Compare Values: five Matched rows differ
    H.pump(10)
    view = win._tab_matched
    names = [view.model().headerData(c, Qt.Orientation.Horizontal) for c in range(view.model().columnCount())]
    # Sort by Stock Code descending: the screen order is now the reverse of the original order.
    view.sortByColumn(names.index("Stock Code"), Qt.SortOrder.DescendingOrder)
    H.pump(5)
    n = len(win._mismatch_rows)
    assert n == 5
    win._nav_mismatch(0, absolute=True)
    screen_rows = [view.currentIndex().row()]
    for _ in range(n - 1):
        win._next_mm_btn.click()
        H.pump(3)
        screen_rows.append(view.currentIndex().row())
    assert screen_rows == sorted(screen_rows) and len(set(screen_rows)) == n, screen_rows   # top to bottom, each once
    codes = [view.model().index(r, names.index("Stock Code")).data() for r in screen_rows]
    assert codes == sorted(codes, reverse=True), codes                                          # the sorted order
    win._next_mm_btn.click()                              # past the last one: wraps to the first
    H.pump(3)
    assert view.currentIndex().row() == screen_rows[0]
    win._prev_mm_btn.click()                              # and back: the last one
    H.pump(3)
    assert view.currentIndex().row() == screen_rows[-1]


def test_in_combined_view_the_arrows_can_step_through_red_only_or_orange_only():
    win = _window()
    win._combined_btn.setChecked(True)
    H.pump(10)
    win._compare_vals_btn.setChecked(True)               # Show Combined (orange)
    win._combined_cmp_btn.setChecked(True)               # Compare Values (red)
    H.pump(10)
    kinds = win._mismatch_kinds
    assert any("red" in k for k in kinds.values()) and any("orange" in k for k in kinds.values())
    assert win._mm_kind_combo.isVisible()
    view = win._tab_matched
    names = [view.model().headerData(c, Qt.Orientation.Horizontal) for c in range(view.model().columnCount())]

    def walk(kind, steps=4):
        win._mm_kind_combo.setCurrentIndex(win._mm_kind_combo.findData(kind))
        H.pump(3)
        seen = {view.model().index(view.currentIndex().row(), names.index("Stock Code")).data()}
        seen |= set(_clicked_codes(win, steps))
        return seen

    red = {win._combined_df.iloc[r]["Stock Code"] for r, k in kinds.items() if "red" in k}
    orange = {win._combined_df.iloc[r]["Stock Code"] for r, k in kinds.items() if "orange" in k}
    assert walk("red") == red
    assert walk("orange") == orange
    assert walk("all") == red | orange


def test_the_selector_is_hidden_when_only_one_colour_exists():
    win = _window()
    win._compare_vals_btn.setChecked(True)               # plain Compare Values: red only
    H.pump(10)
    assert not win._mm_kind_combo.isVisible()


def test_the_document_filter_offers_more_than_forty_suggestions():
    pairs = [("MR", f"document {i:03d}.xlsx", "S") for i in range(150)]
    found = doc_filter.suggest("document", pairs)
    assert len(found) > 40
    assert len(doc_filter.suggest("document", pairs, limit=10)) == 10
