"""Disparities: a document's rechargeability that contradicts SAGE's own tag is listed and not counted."""
import importlib

import pandas as pd

from stats import cost_stats as cs
import harness as H
from harness import W
from ctr_tools import source_choices as SC
from stats.stats_view import DisparitiesDialog, StatsPanel

COSTS = {"A": 10.0, "B": 20.0, "C": 30.0, "D": 40.0}
TAGS = {"A": cs.RECHARGEABLE, "B": cs.NON_RECHARGEABLE, "C": cs.RECHARGEABLE, "D": cs.NON_RECHARGEABLE}


def _items(*rows):
    # days = 1: these tests are about rechargeability, and equipment is costed x days
    return pd.DataFrame([{"key": k, "doc": "doc1", "sheet": "", "side": "CTR", "qty": q, "status": s,
                          "section": "Plant & Equipment", "days": 1.0} for k, q, s in rows])


def test_a_contradiction_is_not_costed_and_is_named():
    items = _items(("A", 2, "RECHARGE"),        # agrees with SAGE
                   ("B", 2, "RECHARGE"),        # document rechargeable, SAGE non-rechargeable
                   ("C", 2, "NONRECHARG"),      # the other way round
                   ("D", 2, None))              # the document does not say: not a disparity
    rows = cs.cost_rows(items, COSTS, True, sage_tags=TAGS)
    by = dict(zip(rows["key"], rows["problem"]))
    assert by == {"A": "", "B": cs.DISPARITY, "C": cs.DISPARITY, "D": ""}
    assert rows.loc[rows["key"] == "B", "cost"].isna().all()
    s = cs.summarize(rows)
    tot = s[s["doc"] == cs.TOTAL_LABEL].iloc[0]
    assert tot["Total cost"] == 10 * 2 + 40 * 2 and tot["disparity"] == 2
    t = cs.disparities(rows)
    assert set(t["Stock Code"]) == {"B", "C"}
    assert t.set_index("Stock Code").loc["B", "Document says"] == cs.RECHARGEABLE
    assert t.set_index("Stock Code").loc["B", "SAGE says"] == cs.NON_RECHARGEABLE


def test_codes_without_a_sage_tag_or_cost_are_not_disparities():
    items = _items(("Z", 1, "RECHARGE"), ("A", 1, "RECHARGE"))
    rows = cs.cost_rows(items, COSTS, True, sage_tags=TAGS)
    assert dict(zip(rows["key"], rows["problem"])) == {"Z": cs.NO_SAGE, "A": ""}
    assert cs.disparities(rows).empty
    typed = cs.cost_rows(items, COSTS, True, manual={"Z": 5.0}, sage_tags=TAGS)
    assert typed.loc[typed["key"] == "Z", "problem"].iloc[0] == ""        # a typed-in cost, SAGE has no tag


def test_a_disparity_is_left_out_whatever_the_quantity_setting():
    items = _items(("B", None, "RECHARGE"))
    for use_qty in (True, False):
        rows = cs.cost_rows(items, COSTS, use_qty, sage_tags=TAGS)
        assert rows["problem"].iloc[0] == cs.DISPARITY


def test_saved_statistics_keep_disparities_and_old_saves_still_load():
    rows = cs.cost_rows(_items(("B", 1, "RECHARGE")), COSTS, True, sage_tags=TAGS)
    back = cs.rows_from_records(cs.rows_to_records(rows))
    assert back["problem"].iloc[0] == cs.DISPARITY
    old = [{k: v for k, v in r.items() if k != "sage_bucket"} for r in cs.rows_to_records(rows)]
    assert cs.rows_from_records(old)["problem"].iloc[0] == ""            # saved before this existed


def test_the_excel_report_names_them():
    rows = cs.cost_rows(_items(("B", 1, "RECHARGE")), COSTS, True, sage_tags=TAGS)
    summary, items = cs.report_frames(rows)
    assert "…of which differs from SAGE" in summary.columns and "SAGE says (rechargeability)" in items.columns
    assert cs.DISPARITY in set(items["Not counted because"])


def test_sage_tag_map_uses_the_first_row_per_product():
    df = pd.DataFrame({"product": ["a", "a", "b"], "non_recharge": [True, False, False]})
    assert cs.sage_tag_map(df) == {"A": cs.NON_RECHARGEABLE, "B": cs.RECHARGEABLE}
    assert cs.sage_tag_map(pd.DataFrame()) == {}


def test_the_button_shows_only_when_there_are_disparities_and_opens_the_list():
    panel = StatsPanel()
    panel.show()
    panel.set_rows(cs.cost_rows(_items(("B", 1, "RECHARGE"), ("C", 1, "NONRECHARG"), ("A", 1, "RECHARGE")),
                                COSTS, True, sage_tags=TAGS))
    H.pump()
    assert panel._dispar_btn.isVisible() and panel._dispar_btn.text() == "Disparities (2)"
    assert not panel._dispar_btn.icon().isNull()
    assert "differs from SAGE" in panel._banner.text()
    dlg = DisparitiesDialog(None, panel)
    assert dlg.table.rowCount() == 2 and dlg.count.text() == "2 Stock Code(s) still need a choice"
    panel.set_rows(cs.cost_rows(_items(("A", 1, "RECHARGE")), COSTS, True, sage_tags=TAGS))
    H.pump()
    assert not panel._dispar_btn.isVisible()


def test_the_generator_lists_a_line_whose_price_contradicts_sage():
    w = H.generator()
    cs_tbl = w._req_cons_tbl
    target = None
    for r in range(cs_tbl.rowCount()):
        if not cs_tbl.item(r, W._CS_STATUS).text().startswith("✓"):
            continue
        key = cs_tbl.item(r, W._CS_MATCH).text().strip() or cs_tbl.item(r, W._CS_CODE).text().strip()
        if W._sage_non_recharge(w._sage_df, key) is False:             # SAGE: rechargeable
            try:
                float(cs_tbl.item(r, W._CS_PRICE).text())               # and the line is priced (rechargeable)
            except ValueError:
                continue
            target = (r, key)
            break
    assert target, "no priced consumable that SAGE calls rechargeable in the test request"
    r, key = target
    assert key.upper() not in set(w._stats_panel.disparity_table()["Stock Code"])
    cs_tbl.item(r, W._CS_PRICE).setText("NONRECHARG")                  # the CTR now says non-rechargeable
    w._refresh_stats()
    H.pump()
    assert key.upper() in set(w._stats_panel.disparity_table()["Stock Code"])
    assert w._stats_panel._dispar_btn.isVisible()
    row = w._stats_panel._costed_all.loc[w._stats_panel._costed_all["key"] == key.upper()].iloc[0]
    assert row["problem"] == cs.DISPARITY and pd.isna(row["cost"])


# ── choosing which source is right ───────────────────────────────────────────

def _base():
    return cs.cost_rows(_items(("A", 2, "RECHARGE"), ("B", 2, "RECHARGE"), ("C", 2, "NONRECHARG")),
                        COSTS, True, sage_tags=TAGS)


def test_choosing_sage_counts_the_row_with_sages_rechargeability():
    rows = cs.apply_manual(_base(), {}, True, {"B": "SAGE"})
    b = rows[rows["key"] == "B"].iloc[0]
    assert b["problem"] == "" and b["bucket"] == cs.NON_RECHARGEABLE and b["doc_bucket"] == cs.RECHARGEABLE
    assert b["cost"] == 40.0 and b["choice"] == "SAGE"


def test_choosing_the_document_counts_the_row_with_the_documents_value():
    rows = cs.apply_manual(_base(), {}, True, {"C": "DOC"})
    c = rows[rows["key"] == "C"].iloc[0]
    assert c["problem"] == "" and c["bucket"] == cs.NON_RECHARGEABLE and c["cost"] == 60.0
    assert set(cs.disparities(rows)["Stock Code"]) == {"B"}               # B is still open


def test_clearing_a_choice_brings_the_disparity_back_and_resolved_ones_can_be_listed():
    resolved = cs.apply_manual(_base(), {}, True, {"B": "SAGE", "C": "DOC"})
    assert cs.disparities(resolved).empty
    full = cs.disparities(resolved, include_resolved=True).set_index("Stock Code")
    assert full.loc["B", "Chosen"] == "SAGE" and full.loc["C", "Chosen"] == "Document"
    assert full.loc["B", "Document says"] == cs.RECHARGEABLE         # the document's own value is kept
    again = cs.apply_manual(_base(), {}, True, {})
    assert set(cs.disparities(again)["Stock Code"]) == {"B", "C"}


def test_a_choice_for_a_code_with_no_difference_changes_nothing():
    rows = cs.apply_manual(_base(), {}, True, {"A": "SAGE", "NOPE": "DOC"})
    assert rows[rows["key"] == "A"].iloc[0]["bucket"] == cs.RECHARGEABLE
    assert set(cs.disparities(rows)["Stock Code"]) == {"B", "C"}


def test_the_panel_announces_a_users_choice_but_not_one_it_was_given():
    panel = StatsPanel()
    panel.set_rows(_base())
    got = []
    panel.choices_changed.connect(got.append)
    panel.set_choice("B", "SAGE")
    assert got == [{"B": "SAGE"}] and set(panel.disparity_table()["Stock Code"]) == {"C"}
    panel.set_choices({"C": "DOC"})                                     # given from outside: silent
    assert got == [{"B": "SAGE"}] and panel.choices() == {"C": "DOC"}
    panel.set_choice("C", None)
    assert panel.choices() == {} and got[-1] == {}


def test_the_dialog_lists_resolved_codes_on_request():
    panel = StatsPanel()
    panel.set_rows(_base())
    panel.set_choice("B", "SAGE")
    dlg = DisparitiesDialog(None, panel)
    assert dlg.table.rowCount() == 1
    dlg._show_resolved.setChecked(True)
    assert dlg.table.rowCount() == 2
    chosen = {dlg.table.item(r, 3).text(): dlg.table.item(r, 7).text() for r in range(2)}
    assert chosen == {"B": "SAGE", "C": ""}
    assert all(dlg.table.cellWidget(r, 0) is not None for r in range(2))        # a Choose source button each, in the FIRST column
    assert dlg.table.horizontalHeaderItem(0).text() == ""                       # (so it never scrolls out of view)


def test_remembered_choices_roundtrip_and_ignore_junk():
    SC._CHOICES_PATH.write_text('{"a": "SAGE", "b": "DOC", "c": "maybe", "": "DOC"}')
    assert SC.load_choices() == {"A": "SAGE", "B": "DOC"}
    SC.save_choices({"X": "DOC"})
    assert SC.load_choices() == {"X": "DOC"}
    SC._CHOICES_PATH.unlink()
    assert SC.load_choices() == {}


def _disparity_line(w, doc_says):
    """(table, row, key) of a Generator line whose document value is `doc_says` and SAGE disagrees."""
    t = w._stats_panel.disparity_table()
    for key in t[t["Document says"] == doc_says]["Stock Code"]:
        for tbl, c_m, c_c, c_d, c_r, c_s in ((w._req_cons_tbl, W._CS_MATCH, W._CS_CODE, W._CS_DESC, W._CS_PRICE, W._CS_STATUS),
                                              (w._req_equip_tbl, W._EQ_MATCH, W._EQ_CODE, W._EQ_DESC, W._EQ_RATE, W._EQ_STATUS)):
            for r in range(tbl.rowCount()):
                if tbl.item(r, c_s).text().startswith("✓") and cs.norm_key(
                        tbl.item(r, c_m).text() or tbl.item(r, c_c).text() or tbl.item(r, c_d).text()) == key:
                    return tbl, r, key, c_r
    raise AssertionError(f"no Generator line whose document value is {doc_says} and SAGE differs")


def test_generate_is_held_back_while_a_disparity_is_unresolved():
    w = H.generator()
    open_codes = set(w._stats_panel.disparity_table()["Stock Code"])
    assert open_codes
    w._azn_tpl_edit.setText(str(H.INPUTS / "AZN_TEMPLATE.xlsx"))
    w._usd_tpl_edit.setText(str(H.INPUTS / "USD_TEMPLATE.xlsx"))
    w._out_dir_edit.setText(str(H.SANDBOX / "out" / "blocked"))
    w._job_ref_edit.setText("999")
    H.msgs.clear()
    w._generate()
    text = H.all_text(H.msgs)
    assert "differs from SAGE" in text and "Cannot generate" in "\n".join(str(m[1]) for m in H.msgs)
    assert not list((H.SANDBOX / "out" / "blocked").glob("*")), "nothing may be written"
    for k in open_codes:
        w._stats_panel.set_choice(k, "DOC")
    assert w._stats_panel.disparity_table().empty                     # now it is free to generate


def test_choosing_sage_makes_the_line_say_what_sage_says():
    w = H.generator()
    # document says Rechargeable (a priced line), SAGE says Non-Rechargeable: the cell becomes NONRECHARG
    tbl, r, key, c_rate = _disparity_line(w, cs.RECHARGEABLE)
    before = tbl.item(r, c_rate).text()
    assert before.upper() != "NONRECHARG"
    w._stats_panel.set_choice(key, "SAGE")
    H.pump()
    assert tbl.item(r, c_rate).text() == "NONRECHARG"
    assert key not in set(w._stats_panel.disparity_table()["Stock Code"])
    # and the other way: document says Non-Rechargeable, SAGE says Rechargeable: the cell is emptied
    tbl, r, key, c_rate = _disparity_line(w, cs.NON_RECHARGEABLE)
    assert tbl.item(r, c_rate).text().upper() == "NONRECHARG"
    w._stats_panel.set_choice(key, "SAGE")
    H.pump()
    assert tbl.item(r, c_rate).text() == ""                           # a price must be entered; nothing is assumed
    assert any(c["tbl"] is tbl and c["row"] == r and c["field"] in ("Rate", "Price") for c in w._incomplete_cells())


def test_clearing_a_sage_choice_gives_the_cell_back_what_it_said():
    w = H.generator()
    # the cell said NONRECHARG, SAGE says rechargeable: choosing SAGE empties it; clearing the choice restores it
    tbl, r, key, c_rate = _disparity_line(w, cs.NON_RECHARGEABLE)
    original = tbl.item(r, c_rate).text()
    assert original.upper() == "NONRECHARG"
    w._stats_panel.set_choice(key, "SAGE")
    H.pump()
    assert tbl.item(r, c_rate).text() == ""
    w._stats_panel.set_choice(key, None)                              # "Clear my choice"
    H.pump(15)
    assert tbl.item(r, c_rate).text() == original                     # given back
    assert key in set(w._stats_panel.disparity_table()["Stock Code"])  # and the disparity is open again
    # the other direction: a priced cell turned into NONRECHARG comes back as the price
    tbl, r, key, c_rate = _disparity_line(w, cs.RECHARGEABLE)
    price = tbl.item(r, c_rate).text()
    w._stats_panel.set_choice(key, "SAGE")
    H.pump()
    assert tbl.item(r, c_rate).text() == "NONRECHARG"
    w._stats_panel.set_choice(key, None)
    H.pump(15)
    assert tbl.item(r, c_rate).text() == price


def test_switching_to_the_document_also_gives_the_cell_back():
    w = H.generator()
    tbl, r, key, c_rate = _disparity_line(w, cs.NON_RECHARGEABLE)
    original = tbl.item(r, c_rate).text()
    w._stats_panel.set_choice(key, "SAGE")
    H.pump()
    assert tbl.item(r, c_rate).text() == ""
    w._stats_panel.set_choice(key, "DOC")                             # changed their mind: the document is right
    H.pump(15)
    assert tbl.item(r, c_rate).text() == original


def test_a_cell_the_user_edited_after_the_change_is_never_overwritten_by_the_restore():
    w = H.generator()
    tbl, r, key, c_rate = _disparity_line(w, cs.NON_RECHARGEABLE)
    w._stats_panel.set_choice(key, "SAGE")
    H.pump()
    assert tbl.item(r, c_rate).text() == ""
    tbl.item(r, c_rate).setText("12.50")                              # the user types a price into the emptied cell
    H.pump(15)
    w._stats_panel.set_choice(key, None)
    H.pump(15)
    assert tbl.item(r, c_rate).text() == "12.50"                      # their edit stays


def test_choosing_the_document_changes_no_cell():
    w = H.generator()
    tbl, r, key, c_rate = _disparity_line(w, cs.NON_RECHARGEABLE)
    before = tbl.item(r, c_rate).text()
    w._stats_panel.set_choice(key, "DOC")
    H.pump()
    assert tbl.item(r, c_rate).text() == before


def test_both_statistics_lists_share_the_choices():
    APP = importlib.import_module("app")
    mw = APP.MainWindow()
    gen = mw._ctr_gen
    comp = mw._stats_panel
    comp.set_choice("EQ1", "SAGE")                                   # chosen in the MR vs CTR list
    assert gen.stats_panel().choices() == {"EQ1": "SAGE"} and SC.load_choices() == {"EQ1": "SAGE"}
    gen.stats_panel().set_choice("EQ2", "DOC")                      # chosen in the Generator
    assert comp.choices() == {"EQ1": "SAGE", "EQ2": "DOC"}
    gen.stats_panel().set_choice("EQ1", None)
    assert comp.choices() == {"EQ2": "DOC"} and SC.load_choices() == {"EQ2": "DOC"}
    SC._CHOICES_PATH.unlink()
    gen.reload_saved_data()                                         # Settings reset: both lists follow
    assert comp.choices() == {} and gen.stats_panel().choices() == {}
    mw.close()


def test_the_items_name_is_shown_next_to_its_code():
    names = {"B": "WIDGET, BLUE", "C": "WIDGET, RED"}
    rows = cs.cost_rows(_items(("B", 1, "RECHARGE"), ("C", 1, "NONRECHARG")), COSTS, True,
                        sage_tags=TAGS, sage_names=names)
    t = cs.disparities(rows).set_index("Stock Code")
    assert t.loc["B", "Item"] == "WIDGET, BLUE" and t.loc["C", "Item"] == "WIDGET, RED"
    panel = StatsPanel()
    panel.set_rows(rows)
    dlg = DisparitiesDialog(None, panel)
    col = cs.DISPARITY_COLUMNS.index("Item") + 1                            # the button is column 0
    assert {dlg.table.item(r, col).text() for r in range(dlg.table.rowCount())} == {"WIDGET, BLUE", "WIDGET, RED"}
    _s, items = cs.report_frames(rows)
    assert "Item (SAGE name)" in items.columns
    old = [{k: v for k, v in r.items() if k != "name"} for r in cs.rows_to_records(rows)]
    assert (cs.rows_from_records(old)["name"] == "").all()                  # saved before names existed
    df = pd.DataFrame({"product": ["a", "a"], "long_description": [" First ", "Second"]})
    assert cs.sage_name_map(df) == {"A": "First"}


def test_the_generator_shows_sage_names_for_its_disparities():
    w = H.generator()
    t = w._stats_panel.disparity_table()
    assert len(t) and (t["Item"].str.len() > 0).all(), t[["Stock Code", "Item"]].head()
