"""Disparities: "Choose for selected" gives one choice to every selected Stock Code. Generate: the empty
Manpower table warning (Continue / Cancel) can be switched off in Settings (on by default)."""
import pandas as pd
from PySide6.QtWidgets import QAbstractItemView, QMessageBox

import harness as H
from stats import cost_stats as cs
from stats.stats_view import DisparitiesDialog, StatsPanel

COSTS = {"A": 10.0, "B": 20.0, "C": 30.0, "D": 40.0}
TAGS = {"A": cs.RECHARGEABLE, "B": cs.NON_RECHARGEABLE, "C": cs.RECHARGEABLE, "D": cs.NON_RECHARGEABLE}


def _panel():
    items = pd.DataFrame([
        {"key": k, "doc": d, "sheet": "", "side": side, "qty": 1, "status": s,
         "section": "Plant & Equipment", "days": 1.0}
        for k, s in (("B", "RECHARGE"), ("C", "NONRECHARG"), ("D", "RECHARGE"))      # all three contradict SAGE
        for side, d in (("MR", "mr1"), ("CTR", "ctr1"))])                          # each code on two lines
    panel = StatsPanel()
    panel.set_rows(cs.cost_rows(items, COSTS, True, sage_tags=TAGS))
    return panel


def test_choose_for_selected_sets_every_selected_code_once():
    panel = _panel()
    got = []
    panel.choices_changed.connect(got.append)
    dlg = DisparitiesDialog(None, panel)
    assert dlg.table.rowCount() == 6                                    # 3 codes x 2 lines
    assert dlg.table.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    assert not dlg.choose_sel.isEnabled()
    code_col = cs.DISPARITY_COLUMNS.index("Stock Code") + 1
    rows_of = {}
    for r in range(dlg.table.rowCount()):
        rows_of.setdefault(dlg.table.item(r, code_col).text(), []).append(r)
    for r in rows_of["B"] + rows_of["C"]:                               # B and C, both lines each (not D)
        dlg.table.selectRow(r) if r == (rows_of["B"] + rows_of["C"])[0] else dlg.table.item(r, code_col).setSelected(True)
    H.pump()
    assert dlg.choose_sel.isEnabled() and sorted(dlg._selected_codes()) == ["B", "C"]
    dlg._apply_to_selected("SAGE")                                      # (the menu in front of it needs a real screen)
    assert panel.choices() == {"B": "SAGE", "C": "SAGE"}
    assert len(got) == 1                                                # one announcement, not one per code
    assert set(panel.disparity_table()["Stock Code"]) == {"D"}          # D is left
    dlg._show_resolved.setChecked(True)
    for r in range(dlg.table.rowCount()):
        if dlg.table.item(r, code_col).text() in ("B", "C"):
            dlg.table.item(r, code_col).setSelected(True)
    dlg._apply_to_selected(None)
    assert panel.choices() == {}


def test_choose_for_all_the_document():
    panel = _panel()
    dlg = DisparitiesDialog(None, panel)
    dlg.table.selectAll()
    H.pump()
    dlg._apply_to_selected("DOC")
    assert panel.choices() == {"B": "DOC", "C": "DOC", "D": "DOC"}
    assert panel.disparity_table().empty


class _Stop(Exception):
    pass


def _generate_stopping_after_checks(w):
    """Press Generate with an empty Manpower table; stop at the first thing after the checks."""
    w._req_manpower_tbl.setRowCount(0)
    H.resolve_disparities(w)
    w._azn_tpl_edit.setText(str(H.INPUTS / "AZN_TEMPLATE.xlsx"))
    w._usd_tpl_edit.setText(str(H.INPUTS / "USD_TEMPLATE.xlsx"))
    w._out_dir_edit.setText(str(H.SANDBOX / "out" / "empty_mp"))
    w._job_ref_edit.setText("999")
    w._client_edit.setText("Client")
    w._contract_no_azn_edit.setText("1")
    w._contract_no_usd_edit.setText("1")

    def stop(*a, **k):
        raise _Stop()
    w._collect_rows = stop
    H.msgs.clear()
    try:
        w._generate()
        return "ran"
    except _Stop:
        return "went on"
    finally:
        del w._collect_rows                  # the Generator is shared with the other tests


def test_empty_manpower_warns_by_default_and_cancel_stops():
    w = H.generator()
    assert w._warn_empty_manpower() is True                              # on unless switched off
    real = QMessageBox.warning
    QMessageBox.warning = staticmethod(lambda p, t, text, *a, **k: (H.msgs.append(("warning", t, text)),
                                                                  QMessageBox.StandardButton.Cancel)[1])
    try:
        out = _generate_stopping_after_checks(w)
    finally:
        QMessageBox.warning = real
    assert out == "ran"                                                  # Cancel: Generate stopped before collecting rows
    text = H.all_text(H.msgs)
    assert "Manpower table is empty" in text and "Cannot generate" not in text


def test_empty_manpower_continue_goes_on_and_the_setting_can_turn_it_off():
    w = H.generator()
    assert _generate_stopping_after_checks(w) == "went on"               # the recorded warning answers Yes
    assert "Manpower table is empty" in H.all_text(H.msgs)
    w._settings().setValue(w.KEY_WARN_EMPTY_MANPOWER, False)
    try:
        assert not w._warn_empty_manpower()
        assert _generate_stopping_after_checks(w) == "went on"
        assert "Manpower table is empty" not in H.all_text(H.msgs)       # no question at all
    finally:
        w._settings().remove(w.KEY_WARN_EMPTY_MANPOWER)


def test_the_settings_checkbox_follows_and_changes_the_setting():
    from common import settings_view as S
    w = H.generator()
    tab = S.SettingsTab(w)
    tab.refresh()
    assert tab._warn_mp_chk.isChecked()
    tab._warn_mp_chk.setChecked(False)
    try:
        assert not w._warn_empty_manpower()
        tab.refresh()
        assert not tab._warn_mp_chk.isChecked()
    finally:
        w._settings().remove(w.KEY_WARN_EMPTY_MANPOWER)
