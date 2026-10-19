"""The Stock Code types list (Equipment / Consumable): the file module, how a row's kind is decided and when the
user is asked, the decisions window, the Settings editor, and the 'how to fix' hints. Fixture lists only; the
real shipped list is private and never read here (tests/harness.py points the module at the sandbox)."""
import json

import pandas as pd

import harness as H
from ctr_tools import stockcode_types as ST
from stats import cost_stats as cs
from stats.stats_view import StatsPanel, TypeDecisionsDialog


def _types(eq=(), cons=(), conf=None):
    return {"equipment": set(eq), "consumable": set(cons), "confirmed": dict(conf or {})}


def _items(*rows):
    """rows: (key, section or None, qty). Section is the CTR section the CTR row came from."""
    out = []
    for k, sec, q in rows:
        out.append({"key": k, "doc": "doc", "sheet": "", "side": "CTR" if sec else "MR", "qty": q,
                    "status": "RECHARGE", "section": sec, "description": f"item {k}"})
    return pd.DataFrame(out)


EQ, CONS = "Plant & Equipment", "Consumables & Materials"


# ── the file module ──────────────────────────────────────────────────────────

def test_the_list_is_seeded_from_the_shipped_one_on_first_use_and_roundtrips():
    ship = H.SANDBOX / "shipped_types.json"
    ship.write_text(json.dumps({"equipment": ["e1", "E2"], "consumables_stockcodes": ["c1"]}))
    real_ship, real_user = ST._BUNDLED_DEFAULT_PATH, ST._TYPES_PATH
    ST._BUNDLED_DEFAULT_PATH, ST._TYPES_PATH = ship, H.SANDBOX / "user_types_seed.json"
    try:
        t = ST.load_types()
        assert t["equipment"] == {"E1", "E2"} and t["consumable"] == {"C1"}      # upper-cased
        assert ST._TYPES_PATH.exists()                                            # the user's own copy now exists
        ST.set_type("c1", "Equipment")                                            # moves it, and confirms it
        t = ST.current_types()
        assert "C1" in t["equipment"] and "C1" not in t["consumable"] and t["confirmed"]["C1"] == "Equipment"
        ST.save_types(_types(eq={"A"}))
        assert ST.read_types(ST._TYPES_PATH)["equipment"] == {"A"}
    finally:
        ST._BUNDLED_DEFAULT_PATH, ST._TYPES_PATH = real_ship, real_user


def test_merge_adds_only_missing_codes_and_reset_replaces_with_the_shipped_list():
    ship = H.SANDBOX / "shipped_types2.json"
    ship.write_text(json.dumps({"equipment": ["E1", "E2"], "consumables_stockcodes": ["C1", "C2"]}))
    real_ship, real_user = ST._BUNDLED_DEFAULT_PATH, ST._TYPES_PATH
    ST._BUNDLED_DEFAULT_PATH, ST._TYPES_PATH = ship, H.SANDBOX / "user_types_merge.json"
    try:
        ST.save_types(_types(eq={"E1"}, cons={"C1", "MINE"}, conf={"C1": "Consumable"}))
        added = ST.merge_shipped()
        t = ST.current_types()
        assert added == 2 and t["equipment"] == {"E1", "E2"} and t["consumable"] == {"C1", "C2", "MINE"}
        assert t["confirmed"] == {"C1": "Consumable"}                              # the user's decision survives
        ST.reset_to_shipped()
        t = ST.current_types()
        assert t["equipment"] == {"E1", "E2"} and t["consumable"] == {"C1", "C2"} and not t["confirmed"]
    finally:
        ST._BUNDLED_DEFAULT_PATH, ST._TYPES_PATH = real_ship, real_user


def test_a_missing_or_broken_file_means_an_empty_list():
    broken = H.SANDBOX / "broken_types.json"
    broken.write_text("{not json")
    assert ST.read_types(broken) is None
    assert ST.read_types(H.SANDBOX / "nope.json") is None
    assert ST.empty_types() == _types()


# ── how a row's kind is decided ──────────────────────────────────────────────

def _resolved(items, types):
    rows = cs.cost_rows(items, {"U": 5.0}, True, equip_rates={})
    return cs.apply_manual(rows, {}, True, None, types).set_index("key")


def test_the_list_decides_and_the_ctr_section_is_the_fallback():
    r = _resolved(_items(("E1", None, 1), ("C1", None, 1), ("S1", CONS, 1), ("N1", None, 1)),
                  _types(eq={"E1"}, cons={"C1"}))
    assert r.loc["E1", "kind"] == cs.EQUIPMENT and r.loc["C1", "kind"] == cs.CONSUMABLE
    assert r.loc["S1", "kind"] == cs.CONSUMABLE and r.loc["S1", "type_status"] == ""      # only the CTR section knows it
    assert r.loc["N1", "kind"] == "" and r.loc["N1", "type_status"] == "unknown"
    assert r.loc["N1", "category"] == cs.UNCATEGORISED


def test_sources_that_disagree_need_the_user_and_a_decision_settles_it_for_good():
    items = _items(("X1", CONS, 1), ("B1", None, 1))
    types = _types(eq={"X1", "B1"}, cons={"B1"})            # X1: list says Equipment, CTR says Consumable; B1: in both
    r = _resolved(items, types)
    assert r.loc["X1", "type_status"] == "conflict" and r.loc["X1", "kind"] == cs.EQUIPMENT      # the list is proposed
    assert "list says Equipment" in r.loc["X1", "type_note"] and "CTR section says Consumable" in r.loc["X1", "type_note"]
    assert r.loc["B1", "type_status"] == "conflict" and "in both lists" in r.loc["B1", "type_note"]
    issues = cs.type_issue_table(r.reset_index())
    assert set(issues["Stock Code"]) == {"X1", "B1"} and set(issues["Status"]) == {"Sources disagree"}
    assert issues.set_index("Stock Code").loc["X1", "Description"] == "item X1"          # the description is shown
    done = _resolved(items, _types(eq={"X1", "B1"}, cons={"B1"}, conf={"X1": "Consumable", "B1": "Equipment"}))
    assert (done["type_status"] == "").all()
    assert done.loc["X1", "kind"] == cs.CONSUMABLE and done.loc["B1", "kind"] == cs.EQUIPMENT   # the decision wins


def test_a_code_in_both_lists_is_only_asked_about_when_nothing_backs_up_equipment():
    r = _resolved(_items(("B1", EQ, 1), ("B2", CONS, 1), ("B3", None, 1)), _types(eq={"B1", "B2", "B3"}, cons={"B1", "B2", "B3"}))
    assert r.loc["B1", "type_status"] == "" and r.loc["B1", "kind"] == cs.EQUIPMENT      # the CTR section says Equipment too
    assert r.loc["B2", "type_status"] == "conflict"                                     # the CTR section says Consumable
    assert r.loc["B3", "type_status"] == "conflict" and "in both lists" in r.loc["B3", "type_note"]   # nothing backs it up


def test_a_row_with_an_unknown_or_undecided_type_is_not_priced_and_says_how_to_fix_it():
    rows = cs.cost_rows(_items(("N1", None, 2), ("X1", CONS, 2), ("E1", None, 2)), {"X1": 4.0, "N1": 4.0}, True,
                        equip_rates={"E1": 10.0})
    r = cs.apply_manual(rows, {}, True, None, _types(eq={"X1", "E1"}))
    p = cs.price_view(r, {"E1": 3.0}).set_index("key")
    assert p.loc["N1", "problem"] == cs.NO_TYPE and p.loc["X1", "problem"] == cs.TYPE_CONFLICT
    assert p.loc["E1", "cost"] == 60.0                                                      # known: priced as before
    assert "Settings" in cs.how_to_fix(cs.NO_TYPE) and "Stock Code types" in cs.how_to_fix(cs.NO_TYPE)
    assert "disagree" in cs.how_to_fix(cs.TYPE_CONFLICT, "", "list says X")
    assert "USD Pricebook" in cs.how_to_fix(cs.NO_RATE, cs.EQUIPMENT)
    assert "SAGE" in cs.how_to_fix(cs.NO_RATE, cs.CONSUMABLE)
    assert "Days" in cs.how_to_fix(cs.NO_DAYS) and cs.how_to_fix("") == ""


def test_the_excel_price_items_carry_a_how_to_fix_column():
    rows = cs.cost_rows(_items(("N1", None, 2), ("E1", None, 2)), {}, True, equip_rates={"E1": 10.0})
    _s, items = cs.price_report_frames(rows, stock_types=_types(eq={"E1"}), days={"E1": 2.0})
    it = items.set_index("Stock Code")
    assert it.loc["E1", "Price"] == 40.0 and it.loc["E1", "How to fix"] == ""
    assert it.loc["N1", "Not counted because"] == cs.NO_TYPE and "Stock Code types" in it.loc["N1", "How to fix"]


def test_the_generators_own_lines_are_not_affected_by_the_list():
    rows = cs.cost_rows(_items(("E1", EQ, 2)).assign(rate=3.0, days=4.0), {"E1": 1.0}, True)
    r = cs.apply_manual(rows, {}, True, None, None)             # no list for the Generator's panel
    assert r.iloc[0]["kind"] == cs.EQUIPMENT and r.iloc[0]["type_status"] == ""
    assert cs.price_view(r).iloc[0]["cost"] == 2 * 3.0 * 4.0


# ── the panel and the decisions window ───────────────────────────────────────

def _panel(items, types):
    ST.save_types(types)
    p = StatsPanel(use_stock_types=True)
    p.set_rows(cs.cost_rows(items, {"X1": 4.0, "B1": 4.0}, True, equip_rates={"X1": 10.0, "B1": 10.0}))
    return p


def test_the_button_counts_the_codes_that_need_a_decision_and_the_window_lists_them_with_descriptions():
    p = _panel(_items(("X1", CONS, 1), ("N1", None, 1), ("OK", EQ, 1)), _types(eq={"X1", "OK"}))
    assert p._types_btn.isVisibleTo(p) and "(2)" in p._types_btn.text()
    dlg = TypeDecisionsDialog(None, p)
    assert dlg.table.rowCount() == 2
    cols = cs.TYPE_ISSUE_COLUMNS
    shown = {dlg.table.item(r, 1).text(): [dlg.table.item(r, c + 1).text() for c in range(len(cols))] for r in range(2)}
    assert shown["X1"][cols.index("Description")] == "item X1" and shown["X1"][cols.index("Proposed")] == "Equipment"
    assert shown["N1"][cols.index("Status")] == "Unknown" and shown["N1"][cols.index("Proposed")] == ""
    assert dlg.accept_all.isEnabled()                            # one line has a proposal to accept
    assert all(dlg.table.cellWidget(r, 0) is not None for r in range(2))             # a Decide… button each, first column


def test_deciding_writes_the_list_and_the_code_is_priced_afterwards():
    p = _panel(_items(("X1", CONS, 1), ("N1", None, 1)), _types(eq={"X1"}))
    got = []
    p.types_changed.connect(lambda: got.append(1))
    p.set_mode("price")
    p.set_days({"X1": 2.0})
    assert p._priced_all.set_index("key").loc["X1", "problem"] == cs.TYPE_CONFLICT
    p.decide_types({"X1": "Equipment", "N1": "Consumable"})
    assert got == [1]
    t = ST.current_types()
    assert "X1" in t["equipment"] and "N1" in t["consumable"] and t["confirmed"] == {"X1": "Equipment", "N1": "Consumable"}
    priced = p._priced_all.set_index("key")
    assert priced.loc["X1", "problem"] == "" and priced.loc["X1", "cost"] == 1 * 10.0 * 2.0   # equipment: rate x days
    assert priced.loc["N1", "problem"] == cs.NO_RATE                                            # now it needs a SAGE cost
    assert not p._types_btn.isVisibleTo(p)                                                      # nothing left to decide


def test_accept_all_takes_every_proposal_but_leaves_unknown_codes():
    p = _panel(_items(("X1", CONS, 1), ("B1", None, 1), ("N1", None, 1)), _types(eq={"X1", "B1"}, cons={"B1"}))
    t = p.type_issue_table()
    clash = t[t["Status"] == "Sources disagree"]
    p.decide_types({str(c): str(k) for c, k in zip(clash["Stock Code"], clash["Proposed"])})
    left = p.type_issue_table()
    assert list(left["Stock Code"]) == ["N1"] and list(left["Status"]) == ["Unknown"]


def test_the_generators_panel_has_no_types_button():
    p = StatsPanel()                                             # use_stock_types is off by default
    p.set_rows(cs.cost_rows(_items(("E1", EQ, 1)), {"E1": 1.0}, True))
    assert not p._types_btn.isVisibleTo(p)


# ── Settings ─────────────────────────────────────────────────────────────────

def test_the_settings_row_counts_and_the_editor_marks_a_users_changes_as_decisions():
    from common import settings_view as SVW
    w = H.generator()
    ST.save_types(_types(eq={"E1", "B1"}, cons={"C1", "B1"}, conf={"C1": "Consumable"}))
    tab = SVW.SettingsTab(w)
    assert tab._counts["types"].text().startswith("2 equipment · 2 consumable")
    dlg = SVW._TypesDialog(None, ST.current_types(), {"E1": "Big pump", "C1": "Rope"})
    rows = {dlg._table.item(r, 0).text(): r for r in range(dlg._table.rowCount())}
    assert dlg._table.item(rows["E1"], 2).text() == "Big pump"              # descriptions are shown
    assert "both lists" in dlg._table.item(rows["B1"], 3).text()
    dlg._table.selectRow(rows["E1"])
    dlg._set_selected("Consumable")                                          # change E1
    dlg._table.selectRow(rows["C1"])
    dlg._remove()                                                            # drop C1
    res = dlg.result_types()
    assert res["equipment"] == {"B1"}                        # B1 was in both lists: shown (and kept) as Equipment
    assert res["consumable"] == {"E1"}                       # E1 changed, C1 removed
    assert res["confirmed"].get("E1") == "Consumable" and "C1" not in res["confirmed"]


def test_search_in_the_editor_hides_other_rows():
    from common import settings_view as SVW
    dlg = SVW._TypesDialog(None, _types(eq={"EQ1"}, cons={"PP1", "PP2"}), {"PP2": "Yellow rope"})
    dlg._search.setText("rope")
    hidden = [dlg._table.isRowHidden(r) for r in range(dlg._table.rowCount())]
    names = [dlg._table.item(r, 0).text() for r in range(dlg._table.rowCount())]
    assert [n for n, h in zip(names, hidden) if not h] == ["PP2"]


def test_the_restore_count_of_the_types_file_is_the_number_of_codes():
    from common import settings_view as SVW
    f = H.SANDBOX / "types_count.json"
    f.write_text(json.dumps({"equipment": ["A", "B"], "consumables_stockcodes": ["C"], "confirmed": {}}))
    assert SVW.read_entries(f) == 3


def test_the_shipped_list_is_hidden_from_git_and_bundled_when_present():
    import subprocess
    root = H.ROOT
    out = subprocess.run(["git", "check-ignore", "ctr_tools/stockcode_types.json"], cwd=root,
                         capture_output=True, text=True)
    assert out.returncode == 0, "the shipped Stock Code list must be git-ignored (privacy)"
    assert "privacy" in (root / ".gitignore").read_text().lower()
    assert "stockcode_types.json" in (root / "app.spec").read_text()
