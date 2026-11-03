"""PDMS proposal (Word) in place of a CTR: the parser reads the items table, Compare matches it to an MR by
Description, and the Rate / Rechargeable check stays quiet for it. Reads the sample files only."""
import pandas as pd

import harness as H
from comparator.proposal_parser import _number, parse_proposal
from comparator.sheet_parser import parse_workbook

D = H.ROOT / "TEST_Files" / "MRvsCTR" / "PDMS MR vs CTR"


def _files():
    # (not the "~$…" lock files Word / Excel leave next to a file that is open)
    docx = next((f for f in sorted(D.glob("*.docx")) if not f.name.startswith("~$")), None)
    xlsm = next((f for f in sorted(D.glob("*.xlsm")) if not f.name.startswith("~$")), None)
    if not docx or not xlsm:
        raise H.Skip("PDMS sample files missing")
    return docx, xlsm


def test_number_formats():
    assert _number("1,119.06 ₼") == 1119.06 and _number("0.39 ₼") == 0.39 and _number("10") == 10
    assert _number("") is None and _number("Consumables total") is None


def test_parse_items_table_only():
    docx, _ = _files()
    tables = parse_proposal(docx)
    assert len(tables) == 1
    df = tables[0]["data"]
    assert len(df) == 16 and df["Description"].iloc[-1] == "Transportation"       # totals rows are not items
    assert not df["Description"].str.contains("total", case=False).any()
    assert list(df.columns[:6]) == ["_Section", "Description", "Quantity", "Unit", "Rate", "Total"]
    assert df.loc[df["Description"].str.startswith("ARMAFLEX-HT 22"), "Quantity"].iloc[0] == 14


def test_compare_against_the_mr():
    import app as A
    docx, xlsm = _files()
    mr = pd.concat([t["data"] for t in parse_workbook(xlsm)])
    ctr = pd.concat([t["data"] for t in parse_proposal(docx)])
    win = A.MainWindow()
    win.show()
    H.pump(10)
    win._mr_df, win._ctr_df = mr, ctr
    win._run_compare()
    H.pump(30)
    assert H.wait(lambda: not win._stats_workers, 60)
    assert len(win._display_df) == 13 and len(win._omr_dl) == 2 and len(win._octr_dl) == 3
    assert win._display_df["CTR Document"].str.endswith(".docx").all()
    assert not win._rate_rech_mismatch_cells                       # prices are numbers, MR says NONRECHARG
    assert win._proposal_chk.toolTip()


def _compare_pair(proposal_mode: bool):
    import app as A
    docx, xlsm = _files()
    mr = pd.concat([t["data"] for t in parse_workbook(xlsm)])
    ctr = pd.concat([t["data"] for t in parse_proposal(docx)])
    win = A.MainWindow()
    win.show()
    H.pump(10)
    win._proposal_chk.setChecked(proposal_mode)
    win._mr_df, win._ctr_df = mr, ctr
    win._run_compare()
    H.pump(30)
    assert H.wait(lambda: not win._stats_workers, 60)
    return win


def _header(view) -> list:
    from PySide6.QtCore import Qt
    m = view.model()
    return [m.headerData(i, Qt.Orientation.Horizontal) for i in range(m.columnCount())]


def test_proposal_mode_calls_the_key_column_identifier_and_hides_it():
    win = _compare_pair(True)
    for view in (win._tab_matched, win._tab_only_mr, win._tab_only_ctr):
        heads = _header(view)
        assert "Identifier" in heads and "Stock Code" not in heads, heads
        assert view.isColumnHidden(heads.index("Identifier"))
        assert view._user_hidden[0] == "Identifier"                              # the Columns button can bring it back
    heads = _header(win._tab_matched)
    for name in ("Rechargeable (MR)", "Allocation", "Days (MR)", "Days (CTR)", "Local cost"):
        assert win._tab_matched.isColumnHidden(heads.index(name)), name
    assert win._tab_matched.isColumnHidden(heads.index("Local cost"))
    assert not win._tab_matched.isColumnHidden(heads.index("Rate (CTR)"))      # the proposal's price is shown
    assert win._cols_btn.isEnabled() and win._cols_btn.text() == "Columns (6)"
    assert "Stock Code" in win._display_df.columns                              # the data keeps its name
    win._combined_btn.setChecked(True)                                          # the Combined view too
    H.pump(10)
    assert win._tab_matched.isColumnHidden(0) and "Identifier" in _header(win._tab_matched)


def test_without_proposal_mode_the_column_is_unchanged():
    win = _compare_pair(False)
    heads = _header(win._tab_matched)
    assert heads[0] == "Stock Code" and not win._tab_matched.isColumnHidden(0)


def test_hidden_columns_stay_hidden_when_the_statistics_rebuild_the_days_columns():
    """The statistics rebuild Days / Local cost (columns removed and inserted again): Qt forgets they were hidden."""
    win = _compare_pair(True)
    base = ["Identifier", "Rechargeable (MR)", "Allocation", "Days (MR)", "Days (CTR)"]
    for total in (False, True, False):                      # the toggle renames Local cost to "Local cost total (MR/CTR)"
        win._local_mult_chk.setChecked(total)
        win._local_ready = True
        win._apply_local_costs()
        H.pump(10)
        for view in (win._tab_matched, win._tab_only_mr, win._tab_only_ctr):
            heads = _header(view)
            local = [h for h in heads if h.startswith("Local cost")]
            assert len(local) == (2 if total else 1), heads
            for name in base + local:
                assert view.isColumnHidden(heads.index(name)), (name, heads)
            assert sorted(view._user_hidden.values()) == sorted(base + local), view._user_hidden
            assert all(heads[i] == n for i, n in view._user_hidden.items())
