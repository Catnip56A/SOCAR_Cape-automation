"""CTR Tracker: an older revision of the CTR is already in the tracker -> find_older_revisions lists the rows that could
be replaced, and replace_rows makes the write replace exactly that row (no insertion, so no Excel). Sandbox copies of the
test tracker only (the original is read-only)."""
import shutil
from datetime import date

import openpyxl
from openpyxl.utils import column_index_from_string as ci

import harness as H
from ctr_tools import tracker as T
from ctr_tools import tracker_fast as TF
from ctr_tools.config import CFG

CFGT = CFG["ctr_tracker"]


def _copy(name):
    src = H.ROOT / "TEST_Files" / "CTR tracker" / "CTR-Tracker copy.xlsm"
    if not src.exists():
        raise H.Skip("TEST_Files/CTR tracker is not here")
    work = H.SANDBOX / name
    shutil.copy2(src, work)
    return work


def _spare_rows(work, count=2):
    """Numbers of CTRs that have `count` empty USD rows reserved (a spare for each revision to be written)."""
    ws = openpyxl.load_workbook(work, data_only=True, keep_vba=True)[CFGT["sheet_name"]]
    by = {}
    for r in range(2, ws.max_row + 1):
        num = ws.cell(r, ci(CFGT["col_ctr_number"])).value
        if num and not ws.cell(r, ci(CFGT["col_description"])).value and ws.cell(r, ci(CFGT["col_currency"])).value in ("USD", None):
            by.setdefault(str(num), []).append(r)
    pick = next((n for n, rows in by.items() if len(rows) >= count), None)
    if pick is None:
        raise H.Skip("no CTR number with enough spare rows in the test tracker")
    return pick, by[pick][:count]


def _entry(number, revision, desc, value=100.0, currency="USD"):
    return T.CTREntry(client="C", ctr_number=number, revision=revision, ctr_date=date(2026, 10, 15), description=desc,
                      value=value, currency=currency, tracker_location="Onshore", job_type="x", total_equipment=value)


def _cell(work, row, key):
    ws = openpyxl.load_workbook(work, keep_vba=True)[CFGT["sheet_name"]]
    return ws.cell(row, ci(CFGT[key])).value


def _two_revisions(name):
    work = _copy(name)
    number, rows = _spare_rows(work)
    w0, *_ = TF.write_entries_fast(work, [_entry(number, "0", "first")], make_backup=False)
    w1, *_ = TF.write_entries_fast(work, [_entry(number, "1", "second", 200.0)], make_backup=False)
    assert w0 and w1, (w0, w1)
    return work, number, w0[0][2], w1[0][2]


def test_older_revisions_are_listed_nearest_first_and_the_same_revision_is_not_offered():
    work, number, r0, r1 = _two_revisions("rev_list.xlsm")
    found = TF.find_older_revisions(work, [_entry(number, "2", "third")])
    rows = found[0]
    assert [d["row"] for d in rows] == [r1, r0] and [d["revision"] for d in rows] == ["1", "0"], rows
    assert rows[0]["description"] == "second" and rows[0]["order_ok"] is True and rows[1]["order_ok"] is False
    assert TF.find_older_revisions(work, [_entry(number, "1", "again")]) == {}        # same revision: overwritten as always
    assert TF.find_older_revisions(work, [_entry(number, "-1", "lower")]) == {}       # nothing older than -1


def test_replacing_a_chosen_row_rewrites_that_row_only():
    work, number, r0, r1 = _two_revisions("rev_replace.xlsm")
    written, skipped, _b, _n = TF.write_entries_fast(
        work, [_entry(number, "2", "third", 300.0)], make_backup=False, replace_rows={0: r1})
    assert written == [(0, number, r1)] and not skipped, (written, skipped)
    assert _cell(work, r1, "col_description") == "third" and _cell(work, r1, "col_revision") == 2
    assert _cell(work, r0, "col_description") == "first" and _cell(work, r0, "col_revision") == 0   # untouched


def test_a_row_that_no_longer_matches_is_refused_with_a_reason():
    work, number, r0, r1 = _two_revisions("rev_stale.xlsm")
    written, skipped, _b, _n = TF.write_entries_fast(
        work, [_entry(number, "2", "third")], make_backup=False, replace_rows={0: r1 + 500})
    assert not written and "no longer a filled row" in skipped[0][2], skipped
    assert _cell(work, r1, "col_description") == "second"


def test_without_replace_rows_a_new_revision_still_takes_its_own_row_or_needs_excel():
    work, number, r0, r1 = _two_revisions("rev_default.xlsm")
    written, skipped, _b, _n = TF.write_entries_fast(work, [_entry(number, "2", "third")], make_backup=False)
    # on Linux with no spare left an insertion would need Excel: skipped with that reason; with a spare: its own row
    assert (written and written[0][2] not in (r0, r1)) or (skipped and "Excel" in skipped[0][2]), (written, skipped)
    assert _cell(work, r0, "col_description") == "first" and _cell(work, r1, "col_description") == "second"


# ── the window: the three modes, the dialog and a real write ─────────────────────────────────────────────

def _widget(work, batch, mode):
    from ctr_tools.tracker_window import CTRTrackerWidget
    w = CTRTrackerWidget()
    w._tracker_edit.setText(str(work))
    w._older_combo.setCurrentIndex(w._older_combo.findData(mode))
    w._batch = list(batch)
    return w


def _fake_dialog(monkey, choice, accepted=True, log=None):
    from ctr_tools import tracker_window as TW

    class _Dlg:
        def __init__(self, parent, entry, candidates, allow_new, backup_on):
            if log is not None:
                log.append({"allow_new": allow_new, "rows": [d["row"] for d in candidates]})

        def exec(self):
            return TW.QDialog.DialogCode.Accepted if accepted else TW.QDialog.DialogCode.Rejected

        def choice(self):
            return choice

        def deleteLater(self):
            pass
    monkey.append((TW, "_RevisionChoiceDialog", TW._RevisionChoiceDialog))
    TW._RevisionChoiceDialog = _Dlg


def _undo(monkey):
    for mod, name, old in reversed(monkey):          # the first patch saved the real class
        setattr(mod, name, old)


def test_ask_mode_offers_a_new_row_and_follows_the_choice_and_cancel():
    work, number, r0, r1 = _two_revisions("rev_ask.xlsm")
    batch = [_entry(number, "2", "third")]
    w = _widget(work, batch, "ask")
    patched, seen = [], []
    try:
        _fake_dialog(patched, r1, log=seen)
        rows, details = w._ask_older_revisions(str(work), batch, "ask", True)
        assert rows == {0: r1} and details[0]["revision"] == "1"
        assert seen[0]["allow_new"] is True and seen[0]["rows"] == [r1, r0]
        _fake_dialog(patched, None)                                    # "Create a new row"
        assert w._ask_older_revisions(str(work), batch, "ask", True) == ({}, {})
        _fake_dialog(patched, r1, accepted=False)                      # Cancel the write
        assert w._ask_older_revisions(str(work), batch, "ask", True) is None
    finally:
        _undo(patched)
        w.deleteLater()


def test_replace_mode_asks_only_which_row_and_not_at_all_with_one_candidate():
    work, number, r0, r1 = _two_revisions("rev_always.xlsm")
    batch = [_entry(number, "2", "third")]
    w = _widget(work, batch, "replace")
    patched, seen = [], []
    try:
        _fake_dialog(patched, r0, log=seen)
        rows, _d = w._ask_older_revisions(str(work), batch, "replace", True)
        assert rows == {0: r0} and seen[0]["allow_new"] is False        # two older rows: asked WHERE, no "new row" option
        seen.clear()
        between = [_entry(number, "0.5", "between")]                    # only revision 0 is older than 0.5: nothing to ask
        rows, _d = w._ask_older_revisions(str(work), between, "replace", True)
        assert rows == {0: r0} and not seen
    finally:
        _undo(patched)
        w.deleteLater()


def test_the_dialog_has_new_row_checked_and_the_red_warnings():
    from PySide6.QtWidgets import QLabel
    from ctr_tools.tracker_window import _RevisionChoiceDialog
    entry = _entry("CTR-26-1", "2", "x")
    cands = [{"row": 10, "revision": "1", "date": "2026-10-01", "value": "5", "description": "d", "order_ok": True},
             {"row": 9, "revision": "0", "date": "", "value": "", "description": "", "order_ok": False}]
    dlg = _RevisionChoiceDialog(None, entry, cands, allow_new=True, backup_on=True)
    assert dlg.choice() is None                                        # "Create a new row" is the default
    reds = [lbl.text() for lbl in dlg.findChildren(QLabel) if "C62828" in lbl.styleSheet()]
    assert any("backup copy" in t for t in reds) and any("ascending" in t for t in reds), reds
    off = _RevisionChoiceDialog(None, entry, cands, allow_new=True, backup_on=False)
    assert any("switched OFF" in lbl.text() for lbl in off.findChildren(QLabel) if "C62828" in lbl.styleSheet())
    no_new = _RevisionChoiceDialog(None, entry, cands, allow_new=False, backup_on=True)
    assert no_new.choice() == 10                                       # without the new-row option: the nearest older row


def test_a_real_write_in_replace_mode_replaces_the_one_older_row_and_says_so():
    work = _copy("rev_end2end.xlsm")
    number, rows = _spare_rows(work)
    first, *_ = TF.write_entries_fast(work, [_entry(number, "0", "first")], make_backup=False)
    r0 = first[0][2]
    batch = [_entry(number, "1", "second", 250.0)]
    w = _widget(work, batch, "replace")
    w._backup_check.setChecked(False)
    H.msgs.clear()
    w._start_write(skip_confirm=False)
    assert H.wait(lambda: any(m[1] == "Written" for m in H.msgs), 120), H.msgs
    text = next(m[2] for m in H.msgs if m[1] == "Written")
    assert f"row {r0} (replaced revision 0 with 1)" in text, text
    assert _cell(work, r0, "col_description") == "second" and _cell(work, r0, "col_revision") == 1
    w.deleteLater()


def test_new_mode_never_looks_for_older_revisions():
    work, number, r0, r1 = _two_revisions("rev_new.xlsm")
    batch = [_entry(number, "2", "third")]
    w = _widget(work, batch, "new")
    called = []
    real = w._ask_older_revisions
    w._ask_older_revisions = lambda *a, **k: called.append(1) or real(*a, **k)
    H.msgs.clear()
    w._start_write(skip_confirm=False)
    assert H.wait(lambda: any(m[1] == "Written" for m in H.msgs), 120), H.msgs
    assert not called
    w.deleteLater()
