"""Days cells are editable (double-click) in Matched / Only in MR / Only in CTR / Combined and in the Statistics list
of the MR vs CTR tab, for every equipment line. When the other side has a line too, a pop-up asks whether to sync it:
"Sync with CTR/MR", "Only this side" or Cancel. Typed days are kept per side and win over a line's own days."""
import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView

import harness as H
from ctr_tools import stockcode_types as ST
from stats import cost_stats as cs

COSTS = {"C1": 10.0, "C2": 20.0, "C3": 30.0, "C6": 5.0, "C7": 6.0}


_OPEN: list = []


def _window(total=True):
    """C1 equipment: MR 2, CTR 3 (days 4). C2 equipment: MR 5 + 1 (two documents), CTR 4 x 7 days + 5 x 182 days.
    C3 consumable. C6 equipment only in MR (3). C7 equipment only in CTR (2, days 9)."""
    import app as A
    for w in _OPEN:                                                         # one window at a time, so this one can be
        w.close()                                                           # the active window (an editor needs that)
        w.deleteLater()
    _OPEN.clear()
    H.pump(5)
    ST.save_types({"equipment": {"C1", "C2", "C6", "C7"}, "consumable": {"C3"},
                   "confirmed": {k: "Equipment" for k in ("C1", "C2", "C6", "C7")} | {"C3": "Consumable"}})
    win = A.MainWindow()
    _OPEN.append(win)
    win.resize(1500, 800)
    win.show()
    H.pump(10)
    win._mr_df = pd.DataFrame({
        "Stock Code": ["C1", "C2", "C2", "C3", "C6"], "Qty": [2, 5, 1, 7, 3], "Unit": "EA", "Description": "mr",
        "_SourceFile": ["mr1.xlsx", "mr1.xlsx", "mr2.xlsx", "mr1.xlsx", "mr1.xlsx"], "_SourceSheet": "S"})
    win._ctr_df = pd.DataFrame({
        "Stock Code": ["C1", "C2", "C2", "C3", "C7"], "Quantity": [3, 4, 5, 7, 2], "Unit": "EA", "Description": "ctr",
        "Rate": "1", "Days": [4, 7, 182, None, 9],
        "_SourceFile": ["a.xlsx", "a.xlsx", "b.xlsx", "a.xlsx", "a.xlsx"], "_SourceSheet": "P",
        "_Section": "Plant & Equipment"})
    win._run_compare()
    H.pump(30)
    assert H.wait(lambda: not win._stats_workers, 60)
    win._stats_panel.set_rows(cs.cost_rows(win._stats_items, COSTS, True))
    win._local_ready = True
    win._local_mult_chk.setChecked(total)
    win._apply_local_costs()
    H.pump(10)
    return win


def _names(view):
    m = view.model()
    return [m.headerData(c, Qt.Orientation.Horizontal) for c in range(m.columnCount())]


def _row(view, code):
    m = view.model()
    c = _names(view).index("Stock Code")
    return next(r for r in range(m.rowCount()) if m.index(r, c).data() == code)


def _cell(view, code, col):
    return view.model().index(_row(view, code), _names(view).index(col))


def _edit(view, code, col, text):
    """What typing into the cell and pressing Enter does."""
    return view.model().setData(_cell(view, code, col), text, Qt.ItemDataRole.EditRole)


def _flags_editable(view, code, col):
    return bool(view.model().flags(_cell(view, code, col)) & Qt.ItemFlag.ItemIsEditable)


def _chooser(win, answer, asked):
    def choose(key, side, other):
        asked.append((key, side, other))
        return answer
    win._stats_panel.days_sync_chooser = choose


def test_every_days_cell_of_an_equipment_line_is_editable_and_the_others_are_not():
    win = _window()
    only_mr = win._tab_only_mr
    assert _flags_editable(only_mr, "C6", "Days (MR)")                      # equipment, no days yet: editable
    assert not _flags_editable(only_mr, "C6", "Days (CTR)")                 # no CTR line on that side
    assert not _flags_editable(only_mr, "C6", "Qty (MR)")                   # other columns stay read-only
    m = win._tab_matched
    assert _flags_editable(m, "C1", "Days (MR)") and _flags_editable(m, "C1", "Days (CTR)")   # even where days exist
    assert not _flags_editable(m, "C3", "Days (MR)")                        # a consumable has no days to edit
    assert _flags_editable(win._tab_only_ctr, "C7", "Days (CTR)")


def test_typing_days_into_only_in_mr_prices_the_line_and_needs_no_question():
    win = _window()
    asked = []
    _chooser(win, "sync", asked)
    view = win._tab_only_mr
    assert view.model().index(_row(view, "C6"), _names(view).index("Local cost total (MR)")).data() == ""    # no days: no total
    assert _edit(view, "C6", "Days (MR)", "4")
    H.pump(20)
    assert asked == []                                                      # no CTR line: nothing to sync
    assert _cell(view, "C6", "Days (MR)").data() == "4.0"
    assert _cell(view, "C6", "Local cost total (MR)").data() == "60.0"      # 4 x 5 x 3
    assert win._stats_panel.days() == {"C6@MR": 4.0}


def test_sync_with_ctr_sets_both_sides_and_only_mr_leaves_the_ctr_line_alone():
    win = _window()
    m = win._tab_matched
    asked = []
    _chooser(win, "only", asked)
    _edit(m, "C1", "Days (MR)", "10")
    H.pump(20)
    assert asked == [("C1", "MR", "CTR")]
    assert _cell(m, "C1", "Days (MR)").data() == "10.0" and _cell(m, "C1", "Days (CTR)").data() == "4.0"
    assert win._stats_panel.days() == {"C1@MR": 10.0}
    _chooser(win, "sync", asked)
    _edit(m, "C1", "Days (MR)", "12")
    H.pump(20)
    assert _cell(m, "C1", "Days (MR)").data() == "12.0" and _cell(m, "C1", "Days (CTR)").data() == "12.0"
    assert win._stats_panel.days() == {"C1@MR": 12.0, "C1@CTR": 12.0}
    assert _cell(m, "C1", "Local cost total (CTR)").data() == "360.0"       # 12 x 10 x 3: the CTR's own 4 is overridden


def test_cancel_changes_nothing_and_the_cell_is_put_back():
    win = _window()
    m = win._tab_matched
    _chooser(win, None, [])
    _edit(m, "C1", "Days (CTR)", "99")
    H.pump(20)
    assert win._stats_panel.days() == {}
    assert _cell(m, "C1", "Days (CTR)").data() == "4.0"


def test_editing_the_ctr_side_offers_to_sync_with_mr():
    win = _window()
    asked = []
    _chooser(win, "sync", asked)
    _edit(win._tab_matched, "C1", "Days (CTR)", "6")
    H.pump(20)
    assert asked == [("C1", "CTR", "MR")]
    assert win._stats_panel.days() == {"C1@CTR": 6.0, "C1@MR": 6.0}


def test_bad_input_is_ignored_and_empty_clears_a_typed_value():
    win = _window()
    _chooser(win, "only", [])
    view = win._tab_only_mr
    _edit(view, "C6", "Days (MR)", "abc")
    _edit(view, "C6", "Days (MR)", "-3")
    H.pump(10)
    assert win._stats_panel.days() == {}
    _edit(view, "C6", "Days (MR)", "5")
    H.pump(15)
    assert win._stats_panel.days() == {"C6@MR": 5.0}
    _edit(view, "C6", "Days (MR)", "")
    H.pump(15)
    assert win._stats_panel.days() == {}                                    # cleared: back to waiting
    assert _cell(view, "C6", "Days (MR)").data() == ""


def test_the_combined_table_is_editable_too_and_a_side_value_applies_to_all_its_lines():
    win = _window()
    win._combined_btn.setChecked(True)
    H.pump(15)
    c = win._tab_matched
    assert _cell(c, "C2", "Days (CTR)").data() == "7 / 182"
    assert _flags_editable(c, "C2", "Days (CTR)")
    asked = []
    _chooser(win, "only", asked)
    assert _edit(c, "C2", "Days (CTR)", "10")
    H.pump(20)
    assert asked == [("C2", "CTR", "MR")]
    assert _cell(c, "C2", "Days (CTR)").data() == "10.0"                    # both CTR lines now use 10
    win._local_mult_chk.setChecked(True)
    H.pump(10)
    assert _cell(c, "C2", "Local cost total (CTR)").data() == "1800.0"      # 20 x 4 x 10 + 20 x 5 x 10
    _edit(c, "C2", "Days (CTR)", "10.0")                                    # Enter without a change: no question
    assert asked == [("C2", "CTR", "MR")]


def test_a_real_double_click_opens_the_editor_on_an_editable_cell_only():
    win = _window()
    win._tabs.setCurrentWidget(win._stack_only_mr)
    win.activateWindow()                                                    # an editor needs an active window
    win.raise_()
    H.pump(15)
    view = win._tab_only_mr
    for col, expect in (("Days (MR)", True), ("Qty (MR)", False)):
        idx = view.model().index(_row(view, "C6"), _names(view).index(col))
        view.scrollTo(idx)
        H.pump(5)
        pos = view.visualRect(idx).center()
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=pos)          # (Qt's test tool needs the cell current,
        QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=pos)         #  as a user's first click makes it)
        H.pump(10)
        assert (view.state() == QAbstractItemView.State.EditingState) is expect, col
        if expect:
            QTest.keyClick(view.focusWidget(), Qt.Key.Key_Escape)           # leave the editor without changing anything
            H.pump(5)
            assert view.state() != QAbstractItemView.State.EditingState
    assert win._stats_panel.days() == {}


def test_editing_a_days_cell_in_the_statistics_list_asks_the_same_question():
    import stats.stats_view as SV
    win = _window()
    p = win._stats_panel
    asked = []
    _chooser(win, "sync", asked)
    item = None
    tree = p._tree
    for i in range(tree.topLevelItemCount()):
        g = tree.topLevelItem(i)
        for j in range(g.childCount()):
            d = g.child(j)
            d.setExpanded(True)
            for n in range(d.childCount()):
                it = d.child(n)
                if it.text(0) == "C1" and d.text(0) == "mr1.xlsx":
                    item = it
    assert item is not None and SV.C_DAYS in p._editable_cols(item)
    item.setText(SV.C_DAYS, "8")                                           # what the editor does when committed
    H.pump(20)
    assert asked == [("C1", "MR", "CTR")]
    assert p.days() == {"C1@MR": 8.0, "C1@CTR": 8.0}


def test_side_days_are_saved_with_the_comparison_and_reload():
    win = _window()
    _chooser(win, "only", [])
    _edit(win._tab_matched, "C1", "Days (MR)", "10")
    H.pump(20)
    snap = win._stats_snapshot()
    assert snap["days"] == {"C1@MR": 10.0}
    rows = cs.rows_from_records(snap["rows"])
    p = __import__("stats.stats_view", fromlist=["StatsPanel"]).StatsPanel(use_stock_types=True)
    p.restore(rows, {}, True, days=snap["days"])
    eff = p.effective_rows()

    def days(side, key):
        return eff[(eff["side"] == side) & (eff["key"] == key)].iloc[0]["days"]
    assert days("MR", "C1") == 10.0 and days("CTR", "C1") == 4.0
