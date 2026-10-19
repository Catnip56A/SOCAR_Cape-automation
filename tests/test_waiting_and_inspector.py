"""F12 Data tab: every row that holds data has a preview (CTR Request lines, MR / CTR tables, the Tracker batch);
waiting marks (⏳) where something is still loading; and leaving an edited cell without writing keeps its warning."""
import types

import pandas as pd
from PySide6.QtCore import Qt

import harness as H
from ctr_tools import inspector as I
from ctr_tools import stockcode_types as ST
from stats import cost_stats as cs


class _Busy:                                   # a worker that has not finished
    def isFinished(self):
        return False


def _by_name(rows):
    return {r["name"]: r for r in rows}


# ── F12 Data tab ─────────────────────────────────────────────────────────────

def test_ctr_request_lines_have_a_preview_and_a_memory_figure():
    gen = H.generator()
    rows = _by_name(I.collect_data(types.SimpleNamespace(_ctr_gen=gen)))
    for label in ("CTR Request — manpower lines", "CTR Request — equipment lines",
                  "CTR Request — consumable lines", "CTR Request — transport lines"):
        r = rows[label]
        assert r["state"] == "loaded" and r["df"] is not None and len(r["df"]) == r["rows"] > 0, label
        assert r["bytes"] and r["bytes"] > 0, label                        # the memory column is filled in
    eq = rows["CTR Request — equipment lines"]["df"]
    assert "Description" in eq.columns and "Stock Code" in eq.columns        # the real table's headers and cells
    assert len(eq) == gen._req_equip_tbl.rowCount()


def test_mr_and_ctr_tables_preview_a_table_of_tables_and_keep_their_own_totals():
    win = types.SimpleNamespace(
        _mr_tables=[{"source_file": "/x/a.xlsx", "source_sheet": "S1", "table_name": "CH-1",
                     "data": pd.DataFrame({"Stock Code": ["A", "B"], "Qty": [1, 2]})},
                    {"source_file": "/x/a.xlsx", "source_sheet": "S2", "table_name": "NCH-1",
                     "data": pd.DataFrame({"Stock Code": ["C"], "Qty": [3]})}],
        _ctr_tables=[])
    rows = _by_name(I.collect_data(win))
    r = rows["MR tables (2)"]
    assert r["state"] == "loaded" and list(r["df"].columns) == ["File", "Sheet", "Table", "Rows", "Columns", "Memory"]
    assert list(r["df"]["Table"]) == ["CH-1", "NCH-1"] and list(r["df"]["Rows"]) == [2, 1]
    assert r["rows"] == 3 and r["bytes"] > 0                                  # the real totals, not the overview's
    assert rows["CTR tables (0)"]["state"] == "not loaded" and rows["CTR tables (0)"]["df"] is None


def test_the_tracker_batch_previews_its_entries():
    from ctr_tools.tracker import CTREntry
    trk = types.SimpleNamespace(_batch=[CTREntry(ctr_number="CTR-1", client="Acme", currency="USD", value=10.0),
                                        CTREntry(ctr_number="CTR-2", client="Bolt", currency="AZN", value=5.0)])
    rows = _by_name(I.collect_data(types.SimpleNamespace(_ctr_tracker=trk)))
    r = rows["CTR Tracker batch"]
    assert r["state"] == "loaded" and r["rows"] == 2 and r["df"] is not None
    assert list(r["df"]["ctr_number"]) == ["CTR-1", "CTR-2"] and "client" in r["df"].columns


def test_selecting_each_of_those_rows_in_the_tab_shows_a_preview():
    from ctr_tools.tracker import CTREntry
    gen = H.generator()
    win = types.SimpleNamespace(
        _ctr_gen=gen,
        _mr_tables=[{"source_file": "/x/a.xlsx", "source_sheet": "S", "table_name": "T",
                     "data": pd.DataFrame({"Stock Code": ["A"]})}],
        _ctr_tables=[], _ctr_tracker=types.SimpleNamespace(_batch=[CTREntry(ctr_number="CTR-1")]))
    tab = I.DataTab(win)
    tab.refresh()
    for name in ("CTR Request — manpower lines", "CTR Request — equipment lines",
                 "CTR Request — consumable lines", "CTR Request — transport lines", "MR tables (1)", "CTR Tracker batch"):
        r = next(i for i, row in enumerate(tab._rows) if row["name"] == name)
        tab._tbl.selectRow(r)
        assert tab._preview.rowCount() > 0 and tab._preview.columnCount() > 0, name
        assert name in tab._preview_label.text()


def test_a_source_being_read_shows_loading_and_a_waiting_mark():
    gen = H.generator_without_files()                                         # request loaded, the files are not
    gen._workers = [_Busy()]
    gen._pending_loads = 1
    win = types.SimpleNamespace(_ctr_gen=gen, _workers=[_Busy()], _stats_workers=[_Busy()],
                                _stats_panel=types.SimpleNamespace(base_rows=lambda: None))
    rows = _by_name(I.collect_data(win))
    assert rows["SAGE Export"]["state"] == "loading"                          # a path is chosen, it is being read
    assert rows["MR tables (0)"]["state"] == "loading" and rows["Statistics (cost / price rows)"]["state"] == "loading"
    tab = I.DataTab(win)
    tab.refresh()
    states = {tab._tbl.item(r, 0).text(): tab._tbl.item(r, 1).text() for r in range(tab._tbl.rowCount())}
    assert states["SAGE Export"].startswith("⏳")
    assert "loading" in tab._summary.text()


# ── waiting marks in the MR vs CTR tables ────────────────────────────────────

def _window():
    import app as A
    ST.save_types({"equipment": {"E1"}, "consumable": set(), "confirmed": {"E1": "Equipment"}})
    win = A.MainWindow()
    win.resize(1400, 800)
    win.show()
    H.pump(10)
    win._sage_edit.setText("")                                                # no SAGE Export chosen
    win._mr_df = pd.DataFrame({"Stock Code": ["E1", "E2"], "Qty": [1, 1], "Unit": "EA", "Description": "mr",
                               "_SourceFile": "mr.xlsx", "_SourceSheet": "S"})
    win._ctr_df = pd.DataFrame({"Stock Code": ["E1"], "Quantity": [1], "Unit": "EA", "Description": "ctr", "Rate": "1",
                                "Days": [3], "_SourceFile": "c.xlsx", "_SourceSheet": "P", "_Section": "Plant & Equipment"})
    win._run_compare()
    H.pump(20)
    return win


def _display(view, code, col):
    m = view.model()
    names = [m.headerData(c, Qt.Orientation.Horizontal) for c in range(m.columnCount())]
    c = names.index("Stock Code")
    r = next(r for r in range(m.rowCount()) if m.index(r, c).data() == code)
    idx = m.index(r, names.index(col))
    return idx.data(), idx.data(Qt.ItemDataRole.ToolTipRole)


def test_blank_days_and_local_cost_cells_show_a_waiting_mark_until_the_statistics_are_ready():
    win = _window()
    assert win._local_state == "waiting"
    text, tip = _display(win._tab_matched, "E1", "Days (MR)")
    assert text == "⏳ waiting" and "SAGE Export" in tip                      # no SAGE chosen: says what is awaited
    assert _display(win._tab_matched, "E1", "Local cost")[0] == "⏳ waiting"
    assert _display(win._tab_only_mr, "E2", "Days (CTR)")[0] == ""            # no CTR line there: nothing to wait for
    # the frames (and so the Excel export) never contain the mark
    assert not win._display_df["Local cost"].astype(str).str.contains("⏳").any()
    # ready: the marks go
    win._stats_panel.set_rows(cs.cost_rows(win._stats_items, {"E1": 7.0}, True))
    win._local_ready = True
    win._local_state = "ready"
    win._apply_local_costs()
    H.pump(10)
    assert _display(win._tab_matched, "E1", "Local cost")[0] == "7.0"
    assert "⏳" not in str(_display(win._tab_matched, "E1", "Days (MR)")[0])


def test_a_failed_statistics_run_stops_the_waiting_marks_and_the_tab_mark():
    win = _window()
    win._set_stats_tab_busy(True)
    idx = win._tabs.indexOf(win._stats_tab)
    assert "⏳" in win._tabs.tabText(idx)
    win._on_stats_done(win._stats_token, None, "boom")
    H.pump(10)
    assert "⏳" not in win._tabs.tabText(idx) and win._local_state == "failed"
    assert _display(win._tab_matched, "E1", "Local cost")[0] == ""


def test_the_statistics_tab_title_shows_a_mark_only_while_busy():
    win = _window()
    idx = win._tabs.indexOf(win._stats_tab)
    win._set_stats_tab_busy(True)
    assert win._tabs.tabText(idx).endswith("⏳")
    win._set_stats_tab_busy(False)
    assert win._tabs.tabText(idx) == "Statistics"


# ── the CTR Generator: waiting for Load Files ────────────────────────────────

def test_the_generators_statistics_and_totals_say_they_wait_for_load_files():
    w = H.generator_without_files()
    w._refresh_stats()
    H.pump(5)
    assert "⏳ Waiting for Load Files" in w._stats_panel._banner.text()
    assert "⏳ Waiting for Load Files" in w._price_totals_lbl.text()
    assert "line(s) cannot be matched" in w._price_totals_lbl.text()


# ── leaving an edited cell without writing keeps its warning ─────────────────

def test_opening_a_days_cell_in_the_statistics_list_and_leaving_it_empty_keeps_the_warning():
    import stats.stats_view as SV
    import test_mr_days as T
    from PySide6.QtWidgets import QAbstractItemDelegate, QLineEdit
    p = T._panel()                                                           # E4 is equipment with no days: orange "type days"
    item = T._item(p, "E4")
    assert item.text(SV.C_DAYS) == "type days" and item.background(SV.C_DAYS).color().name() == "#ffe0b2"
    p._on_double_clicked(item, SV.C_DAYS)                                    # the editor opens: the warning text is blanked
    assert item.text(SV.C_DAYS) == ""
    p._tree.itemDelegate().closeEditor.emit(QLineEdit(), QAbstractItemDelegate.EndEditHint.NoHint)   # ... and is closed, nothing typed
    H.pump(15)
    again = T._item(p, "E4")
    assert again.text(SV.C_DAYS) == "type days" and again.background(SV.C_DAYS).color().name() == "#ffe0b2"
    assert "days" in again.toolTip(SV.C_DAYS).lower() and p.days() == {}


def test_opening_a_unit_cost_cell_for_a_code_not_in_sage_and_leaving_it_empty_keeps_its_warning():
    import stats.stats_view as SV
    from PySide6.QtWidgets import QAbstractItemDelegate, QLineEdit
    p = SV.StatsPanel()
    p.set_rows(cs.cost_rows(pd.DataFrame([{"key": "NOSAGE", "doc": "d", "sheet": "", "side": "CTR", "qty": 1,
                                           "status": "RECHARGE", "section": "Consumables & Materials"}]), {}, True))
    tree = p._tree

    def find():
        for i in range(tree.topLevelItemCount()):
            g = tree.topLevelItem(i)
            for j in range(g.childCount()):
                d = g.child(j)
                d.setExpanded(True)
                for n in range(d.childCount()):
                    return d.child(n)
    item = find()
    assert item.text(SV.C_UNIT) == "not in SAGE"
    p._on_double_clicked(item, SV.C_UNIT)
    tree.itemDelegate().closeEditor.emit(QLineEdit(), QAbstractItemDelegate.EndEditHint.NoHint)
    H.pump(15)
    assert find().text(SV.C_UNIT) == "not in SAGE" and p.manual_costs() == {}


def test_leaving_a_days_cell_of_a_table_empty_keeps_the_orange_and_the_reason():
    import test_days_edit as D
    win = D._window()
    view = win._tab_only_mr
    idx = D._cell(view, "C6", "Days (MR)")
    before_tip = idx.data(Qt.ItemDataRole.ToolTipRole)
    asked = []
    D._chooser(win, "sync", asked)
    view.model().setData(idx, "", Qt.ItemDataRole.EditRole)                  # the editor is committed with nothing typed
    H.pump(15)
    idx = D._cell(view, "C6", "Days (MR)")
    assert win._stats_panel.days() == {} and asked == []
    b = view.model().data(idx, Qt.ItemDataRole.BackgroundRole)
    assert b is not None and b.color().name() == "#ffe0b2"                   # still orange
    assert idx.data(Qt.ItemDataRole.ToolTipRole) == before_tip and "needs Days" in before_tip
