"""Compare Values: the Qty mode (all differences / only CTR < MR) in the detail and Combined views,
remembered between runs; Unit differences stay red in both modes."""
import pandas as pd

import harness as H


def _window():
    import app as A
    A.MainWindow()._app_settings().setValue(A._QTY_MODE_KEY, "all")    # the remembered mode leaks between tests
    win = A.MainWindow()
    win.resize(1300, 800)
    win.show()
    H.pump(10)
    # Q1: CTR has more, Q2: CTR has fewer, Q3: equal, Q4: CTR has fewer but Unit differs too
    codes = ["Q1", "Q2", "Q3", "Q4"]
    mr = pd.DataFrame({"Stock Code": codes, "Qty": [5, 5, 4, 5], "Unit": ["EA", "EA", "EA", "EA"],
                       "Description": "x", "_SourceFile": "mr.xlsx", "_SourceSheet": "S"})
    ctr = pd.DataFrame({"Stock Code": codes, "Quantity": [9, 2, 4, 1], "Unit": ["EA", "EA", "EA", "EA"],
                        "Description": "x", "Rate": "1", "_SourceFile": "c.xlsx", "_SourceSheet": "S",
                        "_Section": "Plant & Equipment"})
    win._mr_df, win._ctr_df = mr, ctr
    win._run_compare()
    H.pump(30)
    assert H.wait(lambda: not win._stats_workers, 60)
    return win


def _red_qty_codes(win, df, qty_cols):
    src = win._tab_matched.model().sourceModel()
    return {df.iloc[r]["Stock Code"] for (r, c), kind in src._highlights.items()
            if c in qty_cols and kind == "mismatch"}


def test_detail_view_follows_the_mode():
    win = _window()
    win._compare_vals_btn.setChecked(True)
    H.pump(10)
    assert _red_qty_codes(win, win._display_df, {"Qty (MR)", "Qty (CTR)"}) == {"Q1", "Q2", "Q4"}
    win._qty_mode_combo.setCurrentIndex(win._qty_mode_combo.findData("less"))
    H.pump(10)
    assert _red_qty_codes(win, win._display_df, {"Qty (MR)", "Qty (CTR)"}) == {"Q2", "Q4"}


def test_combined_view_follows_the_mode():
    win = _window()
    win._combined_btn.setChecked(True)
    H.pump(10)
    win._combined_cmp_btn.setChecked(True)
    H.pump(10)
    cols = {"Qty (MR) Total", "Qty (CTR) Total", "Diff (CTR − MR)"}
    assert _red_qty_codes(win, win._combined_df, cols) == {"Q1", "Q2", "Q4"}
    win._qty_mode_combo.setCurrentIndex(win._qty_mode_combo.findData("less"))
    H.pump(10)
    assert _red_qty_codes(win, win._combined_df, cols) == {"Q2", "Q4"}
    codes = {win._combined_df.iloc[r]["Stock Code"] for r in win._mismatch_rows}
    assert codes == {"Q2", "Q4"}                         # the arrows step through the same rows


def test_units_stay_red_in_less_mode_and_the_choice_is_remembered():
    import app as A
    win = _window()
    win._qty_mode_combo.setCurrentIndex(win._qty_mode_combo.findData("less"))
    assert str(win._app_settings().value(A._QTY_MODE_KEY)) == "less"
    assert A._qty_counts(5, 5, "less") is False and A._qty_counts(5, None, "less") is False
    assert A._qty_counts(5, 2, "less") is True and A._qty_counts(5, 9, "less") is False
    assert A._qty_counts(5, None, "all") is True and A._qty_counts(None, None, "all") is False
    win2 = A.MainWindow()                                 # a new window starts in the saved mode
    assert win2._qty_mode() == "less"


def test_missing_columns_are_shown_empty_and_grey_and_not_compared():
    import app as A
    win = _window()
    mr = win._mr_df.drop(columns=["Unit"])               # the MR file has no Unit column
    win._mr_df = mr
    win._run_compare()
    H.pump(30)
    assert H.wait(lambda: not win._stats_workers, 60)
    df = win._display_df
    for col in ("Unit (MR)", "Rechargeable (MR)", "Allocation"):
        assert col in df.columns and df[col].map(lambda v: A._norm_val(v) == "").all(), col
    assert "Unit (MR)" in win._absent_cols and "Qty (MR)" not in win._absent_cols
    model = win._tab_matched.model().sourceModel()
    c = list(df.columns).index("Unit (MR)")
    assert model.data(model.index(0, c), A.Qt.ItemDataRole.BackgroundRole).color() == A._EMPTY_BG
    win._compare_vals_btn.setChecked(True)
    H.pump(10)
    assert not any(col == "Unit (MR)" or col == "Unit (CTR)" for (_, col) in model._highlights)   # no false Unit mismatch
    # a highlight still wins over the grey
    assert model.data(model.index(0, list(df.columns).index("Qty (MR)")),
                      A.Qt.ItemDataRole.BackgroundRole).color() != A._EMPTY_BG
