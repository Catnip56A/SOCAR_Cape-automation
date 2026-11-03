"""Compare's match modes: Stock Code only / Description only / Stock Code then Description (default),
remembered between runs. Description keys keep only letters and digits, upper case."""
import pandas as pd

import harness as H
from comparator.match_keys import (MATCH_BOTH, MATCH_DESC, MATCH_MODE_KEY, MATCH_STOCK, assign_keys, desc_key)


def _frames():
    mr = pd.DataFrame({
        "Stock Code":  ["S1", "S2", "", "S4"],
        "Description": ["Hot-wash hose 3/8\"", "Air  hose", "Tool box", "Only MR"],
        "Qty": [1, 2, 3, 4], "Unit": "EA", "_SourceFile": "mr.xlsm", "_SourceSheet": "S"})
    ctr = pd.DataFrame({
        "Stock Code":  ["S1", "X9", "", "S5"],
        "Description": ["HOT WASH HOSE 3/8", "AIR HOSE", "TOOLBOX", "Only CTR"],
        "Quantity": [1, 2, 3, 4], "Unit": "EA", "Rate": "1", "_SourceFile": "c.xlsx", "_SourceSheet": "S",
        "_Section": "Plant & Equipment"})
    return mr, ctr


def _keys(mode):
    mr, ctr, problem = assign_keys(*_frames(), mode)
    assert problem is None
    return set(mr["_KEY_"]) & set(ctr["_KEY_"]), set(mr["_KEY_"]), set(ctr["_KEY_"])


def test_desc_key_letters_and_digits_only():
    assert desc_key('Hot-wash  hose 3/8"') == desc_key("HOT WASH HOSE 3/8") == "HOTWASHHOSE38"
    assert desc_key(None) == desc_key(float("nan")) == desc_key(" - ") == ""


def test_stock_only():
    both, mr_keys, _ = _keys(MATCH_STOCK)
    assert both == {"S1"}
    assert "" not in mr_keys                       # a row without a code is not compared


def test_description_only():
    both, _, _ = _keys(MATCH_DESC)
    assert both == {"HOTWASHHOSE38", "AIRHOSE", "TOOLBOX"}


def test_stock_then_description():
    both, mr_keys, ctr_keys = _keys(MATCH_BOTH)
    # S1 by code, AIR HOSE (different codes) and TOOL BOX (no codes) by description
    assert both == {"S1", "AIRHOSE", "TOOLBOX"}
    assert mr_keys - both == {"S4"} and ctr_keys - both == {"S5"}       # unmatched rows keep their Stock Code


def test_unmatched_rows_with_the_same_description_do_not_merge():
    mr = pd.DataFrame({"Stock Code": ["A", "B"], "Description": ["same", "same"]})
    ctr = pd.DataFrame({"Stock Code": ["C"], "Description": ["other"]})
    m, c, _ = assign_keys(mr, ctr, MATCH_BOTH)
    assert list(m["_KEY_"]) == ["A", "B"] and list(c["_KEY_"]) == ["C"]


def test_missing_columns_are_reported():
    mr, ctr = _frames()
    _, _, problem = assign_keys(mr.drop(columns=["Stock Code"]), ctr, MATCH_STOCK)
    assert problem and problem[0] == "MR"
    _, _, problem = assign_keys(mr.drop(columns=["Description"]), ctr, MATCH_DESC)
    assert problem and problem[0] == "MR"
    _, _, problem = assign_keys(mr.drop(columns=["Description", "Stock Code"]), ctr, MATCH_BOTH)
    assert problem and problem[0] == "MR"


def _window():
    import app as A
    win = A.MainWindow()
    win.resize(1300, 800)
    win.show()
    H.pump(10)
    return A, win


def _compare(win):
    mr, ctr = _frames()
    win._mr_df, win._ctr_df = mr, ctr
    win._run_compare()
    H.pump(30)
    assert H.wait(lambda: not win._stats_workers, 60)


def test_window_default_and_compare_per_mode():
    A, win = _window()
    win._match_mode_combo.setCurrentIndex(win._match_mode_combo.findData(MATCH_BOTH))
    s = win._app_settings()
    s.remove(MATCH_MODE_KEY)
    assert A.MainWindow()._match_mode() == MATCH_BOTH               # nothing saved: the default
    expected = {MATCH_STOCK: 1, MATCH_DESC: 3, MATCH_BOTH: 3}
    for mode, n in expected.items():
        win._match_mode_combo.setCurrentIndex(win._match_mode_combo.findData(mode))
        _compare(win)
        assert len(win._display_df) == n, (mode, len(win._display_df))


def test_mode_is_remembered():
    A, win = _window()
    win._match_mode_combo.setCurrentIndex(win._match_mode_combo.findData(MATCH_DESC))
    assert str(win._app_settings().value(MATCH_MODE_KEY)) == MATCH_DESC
    assert A.MainWindow()._match_mode() == MATCH_DESC               # a new window = the app reopened
    win._match_mode_combo.setCurrentIndex(win._match_mode_combo.findData(MATCH_BOTH))


def test_selector_does_not_raise_the_window():
    import app as A
    A.MainWindow()._app_settings().remove(MATCH_MODE_KEY)
    _, win = _window()
    _compare(win)
    h = win.minimumSizeHint().height()
    print(f"[match modes] minimumSizeHint height after Compare = {h}")
    assert h <= 752
