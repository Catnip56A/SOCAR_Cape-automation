"""The main window's minimum height (MA164): after Compare it must stay small enough for a 1080p screen
at 150 % scaling (1280 x 720 logical, ~640-670 px usable). It was 688 px when the results pane had no
scroll area; the results scroll instead now. Maximize / Hide results must still work."""
import pandas as pd

import harness as H

MAX_MIN_HEIGHT = 520          # generous headroom under the ~640 px a 1080p / 150 % screen leaves


def _compared_window():
    import app as A
    win = A.MainWindow()
    win.resize(1280, 700)
    win.show()
    H.pump(10)
    win._mr_df = pd.DataFrame({"Stock Code": ["A1", "B2"], "Qty": [1, 2], "Rechargeable": ["RECHARGE", "NONRECHARG"],
                               "Description": ["x", "y"], "_SourceFile": "mr.xlsx", "_SourceSheet": "S"})
    win._ctr_df = pd.DataFrame({"Stock Code": ["A1"], "Quantity": [1], "Rate": ["1"], "Description": ["x"],
                                "_SourceFile": "c.xlsx", "_SourceSheet": "S", "_Section": ["Plant & Equipment"]})
    win._run_compare()
    H.pump(30)
    assert H.wait(lambda: not win._stats_workers, 60)
    return win


def test_the_window_can_be_small_after_compare():
    win = _compared_window()
    assert win._results_w.isVisible()
    for i in range(win._main_tabs.count()):
        win._main_tabs.setCurrentIndex(i)
        win.resize(200, 200)
        H.pump(15)
        assert win.height() <= MAX_MIN_HEIGHT, (win._main_tabs.tabText(i), win.height())


def test_maximize_and_hide_results_still_work():
    win = _compared_window()
    sizes = win._splitter.sizes()
    assert sizes[1] > 0
    win._maximize_btn.setChecked(True)
    H.pump(10)
    assert not win._step_bar.isVisible() and win._splitter.sizes()[0] == 0
    win._maximize_btn.setChecked(False)
    H.pump(10)
    assert win._step_bar.isVisible() and win._splitter.sizes()[0] > 0
    win._collapse_btn.click()                       # Hide results
    H.pump(10)
    assert not win._results_body_w.isVisible() and win._results_w.isVisible()
    win._collapse_btn.click()                       # Show results
    H.pump(10)
    assert win._results_body_w.isVisible()
