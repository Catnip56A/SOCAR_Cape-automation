"""Items without a Stock Code (a forklift, a truck): kept by the parsers when they have a Description and a Quantity,
compared by Description, and left out of the cost statistics (no SAGE cost without a code)."""
import pandas as pd

import harness as H
from comparator import sheet_parser as SP
from comparator.match_keys import MATCH_BOTH, MATCH_STOCK, assign_keys


def test_what_counts_as_an_item_without_a_code():
    f = SP._is_item_without_code
    assert f("FORKLIFT TRUCK", 1) and f("Truck (incl. driver)", "2,5") and f("x1", 1.0)
    assert not f("Forklift", 0) and not f("Forklift", None) and not f("Forklift", "")        # no quantity
    assert not f("-", 3) and not f("", 3) and not f(None, 3)                                  # no description
    assert not f("Total Equipment", 5) and not f("TOTALS", 5)                                 # a totals row


def test_mr_rows_without_a_code_keep_real_items_only():
    df = pd.DataFrame({
        "Stock Code":  ["SP2300006500", "-", "STOCKCODE", "12", "Name:", "AA"],
        "Description": ["BOLT", "Forklift truck", "-", "Hot water jet", "Tarlan", "Total"],
        "Qty":         [2, 1, "-", 3, None, 4]})
    out = SP._filter_mr_stock_codes(df)
    assert list(out["Description"]) == ["BOLT", "Forklift truck", "Hot water jet"]
    assert list(out["Stock Code"]) == ["SP2300006500", "", ""]


def test_ctr_block_keeps_items_without_a_code_and_says_so():
    rows = [["Description", "Quantity", "Unit", "Rate", "Days", "Total", "EquipmentCode"],
            ["Bolt", 2, "EA", 5, 7, 70, "SP2300006500"],
            ["FORKLIFT TRUCK", 1, "EA", 100, 3, 300, None],
            ["Truck", 1, "Trips", 50, None, 50, "-"],
            ["Total", None, None, None, None, 420, None]]
    kept, notes, _ = SP._pricing_block(rows, "Plant & Equipment")
    assert list(kept["Description"]) == ["Bolt", "FORKLIFT TRUCK", "Truck"]
    assert list(kept["Stock Code"]) == ["SP2300006500", "", ""]
    texts = " | ".join(n["text"] for n in notes)
    assert "compared by Description only" in texts and "FORKLIFT TRUCK" in texts and "Total" not in texts.split("only")[1]


def _frames():
    mr = pd.DataFrame({"Stock Code": ["A1", ""], "Description": ["Bolt", "Forklift  truck"], "Qty": [2, 1], "Unit": "EA",
                       "_SourceFile": "mr.xlsm", "_SourceSheet": "S"})
    ctr = pd.DataFrame({"Stock Code": ["A1", ""], "Description": ["Bolt", "FORKLIFT TRUCK"], "Quantity": [2, 1], "Unit": "EA",
                        "Rate": ["5", "100"], "_SourceFile": "c.xlsx", "_SourceSheet": "P", "_Section": "Plant & Equipment"})
    return mr, ctr


def test_compared_by_description_unless_stock_code_only():
    mr, ctr = _frames()
    m, c, _ = assign_keys(mr, ctr, MATCH_BOTH)
    assert set(m["_KEY_"]) & set(c["_KEY_"]) == {"A1", "FORKLIFTTRUCK"}
    m, c, _ = assign_keys(mr, ctr, MATCH_STOCK)
    assert set(m["_KEY_"]) == {"A1"} and set(c["_KEY_"]) == {"A1"}


def test_not_in_the_statistics():
    import app as A
    m, c, _ = assign_keys(*_frames(), MATCH_BOTH)
    items = A.MainWindow._build_stats_items(m, c)
    assert set(items["key"]) == {"A1"}                       # the forklift has no Stock Code
    assert not items["description"].str.contains("orklift", case=False).any()


def test_window_shows_the_item_matched_and_the_statistics_ignore_it():
    import app as A
    win = A.MainWindow()
    win.show()
    H.pump(10)
    win._mr_df, win._ctr_df = _frames()
    win._run_compare()
    H.pump(30)
    assert H.wait(lambda: not win._stats_workers, 60)
    assert len(win._display_df) == 2 and len(win._omr_dl) == 0 and len(win._octr_dl) == 0
    assert set(win._stats_items["key"]) == {"A1"}              # the statistics never saw the forklift
