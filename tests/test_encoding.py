"""Manpower Encoding: the rate comes only from an Encoding; Onshore and Offshore never mix."""
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest

import harness as H
from harness import W


def _add(w, desc, enc="", **extra):
    w._append_manpower_row_from_request(
        {"description": desc, "quantity": 2, "working_days": 10, "encoding": enc, **extra})
    return w._req_manpower_tbl.rowCount() - 1


def _status(w, r):
    return w._req_manpower_tbl.item(r, W._MP_STATUS).text()


def _choose(w, r, code=None, typed=None):
    """Pick an Encoding the way a user does: `code` from the drop-down list, or `typed`
    letters (type-ahead) then Enter. Returns the editor."""
    tb = w._req_manpower_tbl
    d = tb.itemDelegateForColumn(W._MP_ENC)
    idx = tb.model().index(r, W._MP_ENC)
    ed = d.createEditor(tb, None, idx)
    if code is not None:
        ed.setCurrentText(code)
    if typed is not None:
        QTest.keyClicks(ed, typed)
    d.setModelData(ed, tb.model(), idx)
    return ed


def test_rows_wait_for_load_files_and_are_not_red():
    w = H.generator_without_files()
    red = QColor("#FFCDD2")
    for tbl, col in ((w._req_manpower_tbl, W._MP_STATUS), (w._req_equip_tbl, W._EQ_STATUS),
                     (w._req_cons_tbl, W._CS_STATUS)):
        for r in range(tbl.rowCount()):
            st = tbl.item(r, col).text()
            if st.startswith("✓"):
                continue                                  # a hand-set / manual row
            assert st.startswith("⏳"), st
            assert tbl.item(r, 0).data(W._ROW_TINT_ROLE) is None
            assert all(tbl.item(r, c).background().color() != red for c in range(tbl.columnCount()))
    H.msgs.clear()
    w._azn_tpl_edit.setText("x")
    w._generate()
    assert "waiting for the reference files" in H.all_text(H.msgs)


def test_a_row_without_encoding_gets_no_rate():
    w = H.generator()
    r = _add(w, "ROPE ACCESS LEVEL 3")
    tb = w._req_manpower_tbl
    assert _status(w, r).startswith("✗") and "Encoding" in _status(w, r)
    assert tb.item(r, W._MP_RATE).text() == ""
    assert tb.item(r, W._MP_SHIFT).text() == "" and tb.item(r, W._MP_ENC).text() == ""


def test_offered_encodings_follow_the_location():
    w = H.generator()
    assert w._job_location() == "Offshore"
    r = _add(w, "ROPE ACCESS LEVEL 3")
    opts = w._encoding_options(r)
    assert opts and all("-OFF-" in o for o in opts), opts
    r2 = _add(w, "PLANNER")                               # a role the Combined DB lists as always Onshore
    assert w._req_manpower_tbl.item(r2, W._MP_TYPE).text() == "Onshore"
    opts2 = w._encoding_options(r2)
    assert opts2 and all("-ON-" in o for o in opts2), opts2


def test_choosing_an_encoding_fills_the_row_and_the_rate():
    w = H.generator()
    r = _add(w, "ROPE ACCESS LEVEL 3")
    tb = w._req_manpower_tbl
    _choose(w, r, typed="nov")                            # type-ahead, any case
    assert tb.item(r, W._MP_ENC).text() == "NOV-NAT-OFF-12"
    assert [tb.item(r, c).text() for c in (W._MP_TYPE, W._MP_SHIFT, W._MP_SHIFTTYPE)] == \
        ["Offshore", "Night", "Overtime"]
    assert _status(w, r).startswith("✓") and float(tb.item(r, W._MP_RATE).text()) > 0


def test_a_value_outside_the_list_is_refused():
    w = H.generator()
    r = _add(w, "ROPE ACCESS LEVEL 3")
    tb = w._req_manpower_tbl
    _choose(w, r, typed="zzz")                            # nothing in the list starts with / contains it
    assert tb.item(r, W._MP_ENC).text() == ""
    _choose(w, r, "NS-NAT-ON-10")                         # a real code, but not offered for an Offshore row
    assert tb.item(r, W._MP_ENC).text() == ""
    assert "NS-NAT-ON-10" not in w._encoding_options(r)


def test_changing_or_clearing_the_encoding_clears_the_old_rate():
    w = H.generator()
    tb = w._req_manpower_tbl
    r = next(i for i in range(tb.rowCount()) if tb.item(i, W._MP_ENC).text() and _status(w, i).startswith("✓"))
    assert float(tb.item(r, W._MP_RATE).text()) > 0
    tb.item(r, W._MP_ENC).setText("")
    assert tb.item(r, W._MP_RATE).text() == "" and _status(w, r).startswith("✗")


def test_onshore_and_offshore_are_not_mixed():
    w = H.generator()
    r = _add(w, "PLANNER", "NS-NAT-OFF-12")
    assert "always Onshore" in _status(w, r)
    r = _add(w, "SCAFFOLDER", "NS-NAT-ON-10")
    assert "not mixed" in _status(w, r)
    r = _add(w, "SCAFFOLDER", "NS-NAT-OFF-12")
    assert _status(w, r).startswith("✓")


def test_editing_type_by_hand_drops_a_disagreeing_encoding():
    w = H.generator()
    tb = w._req_manpower_tbl
    r = _add(w, "ROPE ACCESS LEVEL 3", "NS-NAT-OFF-12")
    assert _status(w, r).startswith("✓")
    tb.item(r, W._MP_TYPE).setText("Onshore")
    assert tb.item(r, W._MP_ENC).text() == "" and tb.item(r, W._MP_RATE).text() == ""
