"""MA173: days for Document price in the MR vs CTR tab. A CTR line carries its own Days; an MR line takes the days
of the matching CTR line when the CTRs agree; when two or more CTRs give different days the user picks one (or types
their own); with no days to take the Days cell is highlighted and the line waits for the user."""
import json

import pandas as pd

import harness as H
from ctr_tools import stockcode_types as ST
from stats import cost_stats as cs
from stats.stats_view import StatsPanel


def _frames():
    mr = pd.DataFrame({"_KEY_": ["E1", "E2", "E3", "E4", "E5"], "Stock Code": ["E1", "E2", "E3", "E4", "E5"],
                       "Qty": [1, 1, 1, 1, 1], "Rechargeable": "RECHARGE", "Description": "mr", "_SourceFile": "mr.xlsx",
                       "_SourceSheet": "S"})
    ctr = pd.DataFrame({"_KEY_": ["E1", "E2", "E2", "E3", "E1"], "Stock Code": ["E1", "E2", "E2", "E3", "E1"],
                        "Quantity": 1, "Rate": "5", "Days": [7, 7, 182, None, 7], "Description": "ctr",
                        "_SourceFile": ["a.xlsx", "a.xlsx", "b.xlsx", "a.xlsx", "b.xlsx"],
                        "_SourceSheet": "P", "_Section": "Plant & Equipment"})
    return mr, ctr


def _items():
    import app as A
    mr, ctr = _frames()
    return A.MainWindow._build_stats_items(mr, ctr)


def _get(df, side, key, col):
    """One value from a frame that has `side` and `key` columns (key is the Stock Code)."""
    hit = df[(df["side"] == side) & (df["key"] == key)]
    return hit.iloc[0][col]


def test_an_mr_line_takes_the_days_of_the_matching_ctr_when_the_ctrs_agree():
    it = _items()
    assert _get(it, "MR", "E1", "days") == 7 and _get(it, "MR", "E1", "days_note").startswith("from")   # two CTRs, both 7
    assert pd.isna(_get(it, "CTR", "E3", "days"))                                                       # a CTR line keeps its own (none)


def test_ctrs_that_disagree_give_no_days_but_the_options_with_their_documents():
    it = _items()
    assert pd.isna(_get(it, "MR", "E2", "days")) and "7 / 182" in _get(it, "MR", "E2", "days_note")
    opts = json.loads(_get(it, "MR", "E2", "days_options"))
    assert [(o["days"], o["docs"]) for o in opts] == [(7.0, ["a.xlsx"]), (182.0, ["b.xlsx"])]


def test_with_no_ctr_or_no_days_there_is_nothing_to_take():
    it = _items()
    assert pd.isna(_get(it, "MR", "E4", "days")) and _get(it, "MR", "E4", "days_note") == ""          # no CTR line at all
    assert pd.isna(_get(it, "MR", "E3", "days")) and "no days" in _get(it, "MR", "E3", "days_note")   # the CTR line has none


def _rows():
    ST.save_types({"equipment": {"E1", "E2", "E3", "E4", "E5"}, "consumable": set(), "confirmed": {}})
    return cs.cost_rows(_items(), {}, True, equip_rates={k: 10.0 for k in ("E1", "E2", "E3", "E4", "E5")})


def _price(rows, typed=None):
    r = cs.apply_manual(rows, {}, True, None, ST.current_types())
    return cs.price_view(r, typed or {})


def test_price_uses_the_ctr_days_and_leaves_the_others_waiting():
    p = _price(_rows())
    assert _get(p, "MR", "E1", "cost") == 1 * 10.0 * 7                                  # from the matching CTR
    for key in ("E2", "E3", "E4"):
        assert _get(p, "MR", key, "problem") == cs.NO_DAYS, key                         # disagree / no days / no CTR


def test_what_the_user_types_overrides_days_taken_from_a_ctr_and_fills_the_rest():
    p = _price(_rows(), {"E1": 30.0, "E2": 182.0})
    assert _get(p, "MR", "E1", "cost") == 300.0                                          # typed beats the CTR's 7
    assert _get(p, "MR", "E2", "cost") == 1820.0                                         # chosen between the CTRs
    assert _get(p, "CTR", "E1", "days") == 7.0                                           # a CTR line keeps its own days


def _panel():
    p = StatsPanel(use_stock_types=True)
    p.set_rows(_rows())
    p.set_mode("price")
    return p


def _item(panel, key, doc="mr.xlsx"):
    tree = panel._tree
    for i in range(tree.topLevelItemCount()):
        g = tree.topLevelItem(i)
        for j in range(g.childCount()):
            d = g.child(j)
            d.setExpanded(True)
            for n in range(d.childCount()):
                it = d.child(n)
                if it.text(0) == key and d.text(0) == doc:
                    return it
    raise AssertionError(key)


def test_the_days_cell_waits_highlighted_and_for_disagreeing_ctrs_offers_each_ctr():
    import stats.stats_view as SV
    p = _panel()
    e4 = _item(p, "E4")
    assert e4.text(SV.C_DAYS) == "type days" and e4.background(SV.C_DAYS).color().name() == "#ffe0b2"
    e2 = _item(p, "E2")
    assert e2.text(SV.C_DAYS) == "choose…"
    choices = StatsPanel.days_choices(e2)
    assert choices == [("7 days — a.xlsx", 7.0), ("182 days — b.xlsx", 182.0)]
    assert StatsPanel.days_choices(e4) == []                                             # nothing to choose between


def test_picking_one_of_the_ctrs_days_sets_them_and_prices_the_line():
    import stats.stats_view as SV
    p = _panel()
    got = []
    p.days_changed.connect(got.append)
    p.set_chosen_days("E2", 182.0)                                                       # what the menu does for "182 days — b.xlsx"
    H.pump(10)
    assert got and got[-1] == {"E2": 182.0} and p.days() == {"E2": 182.0}
    assert _item(p, "E2").text(SV.C_DAYS) == "182"
    p.set_chosen_days("E2", None)                                                        # "Clear my choice"
    H.pump(10)
    assert p.days() == {} and _item(p, "E2").text(SV.C_DAYS) == "choose…"


def test_the_excel_price_items_say_where_the_days_came_from():
    _s, items = cs.price_report_frames(_rows(), stock_types=ST.current_types(), days={})
    assert _get(items.rename(columns={"Side": "side", "Stock Code": "key"}), "MR", "E1", "Days note").startswith("from")
    got = items.rename(columns={"Side": "side", "Stock Code": "key"})
    assert _get(got, "MR", "E1", "Days") == 7
    assert "7 / 182" in _get(got, "MR", "E2", "Days note") and "Days" in _get(got, "MR", "E2", "How to fix")
