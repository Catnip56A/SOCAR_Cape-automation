"""A CTR Request with manpower (and transport) but no equipment or consumables: the statistics box says so, and
Save statistics (Excel) writes the manpower and the price totals instead of refusing. The normal CTR Date is today
after a request is loaded (the PDMS Letter date's rule), not the request's commencement date. Sandbox only."""
import datetime
import openpyxl

import harness as H
from harness import W


def _manpower_only(w):
    for t in (w._req_equip_tbl, w._req_cons_tbl, w._transport_tbl, w._req_manpower_tbl):
        t.setRowCount(0)
    w._append_manpower_row_from_request(
        {"description": "SCAFFOLDER", "quantity": 2, "working_days": 10, "encoding": "NS-NAT-OFF-12"})
    H.pump(30)
    assert w._req_manpower_tbl.item(0, W._MP_STATUS).text().startswith("✓")
    w._refresh_stats()


def _save(w, out_dir):
    class _Dlg:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return W.QDialog.DialogCode.Accepted

        def folder(self):
            return str(out_dir)

        def deleteLater(self):
            pass

    real = W._SaveLocationDialog
    W._SaveLocationDialog = _Dlg
    try:
        H.msgs.clear()
        w._save_stats_excel()
    finally:
        W._SaveLocationDialog = real


def test_a_manpower_only_request_saves_its_manpower_and_totals():
    w = H.generator()
    _manpower_only(w)
    out = H.SANDBOX / "mp_only_stats"
    out.mkdir(exist_ok=True)
    _save(w, out)
    files = list(out.glob("*.xlsx"))
    assert len(files) == 1, H.msgs
    wb = openpyxl.load_workbook(files[0])
    assert wb.sheetnames == ["Manpower", "Price Totals", "Info"], wb.sheetnames
    mp = list(wb["Manpower"].iter_rows(values_only=True))
    assert mp[1][0] == "SCAFFOLDER" and float(mp[1][6]) > 0
    totals = {r[0]: r[2] for r in list(wb["Price Totals"].iter_rows(values_only=True))[1:]}
    assert float(totals["Manpower"]) == float(mp[1][6]) and float(totals["AZN CTR total"]) == float(mp[1][6])
    info = dict(list(wb["Info"].iter_rows(values_only=True))[1:])
    assert "no equipment or consumable lines" in info["What this is"]


def test_an_empty_request_still_says_nothing_to_save():
    w = H.generator()
    for t in (w._req_equip_tbl, w._req_cons_tbl, w._transport_tbl, w._req_manpower_tbl):
        t.setRowCount(0)
    w._refresh_stats()
    out = H.SANDBOX / "mp_none_stats"
    out.mkdir(exist_ok=True)
    _save(w, out)
    assert not list(out.glob("*.xlsx")) and any("Nothing to save" in m[1] for m in H.msgs)


def test_the_ctr_date_is_today_after_a_request_is_loaded_and_stays_editable():
    w = H.generator()                       # loads the test request, which has an estimated commencement date
    today = datetime.date.today().strftime("%Y-%m-%d")
    assert w._date_edit.text() == today
    w._date_edit.setText("2026-01-02")      # editable
    assert w._date_edit.text() == "2026-01-02"
    H.load_request(w)                       # a new request: today again, whatever the user typed before
    assert w._date_edit.text() == today
