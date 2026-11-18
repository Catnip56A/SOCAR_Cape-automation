"""Transport lines share one mark-up: editing it on a line asks whether the others change too; a line can be an
exception (fuel at 0 %) and is then left alone; the notes of the PDMS proposal quote the shared value."""
import harness as H
from harness import W
from PySide6.QtWidgets import QMessageBox

MK = W._TR_MARKUP


def _answer(label):
    """Press the button called `label` in the next question (the harness answers the first one otherwise)."""
    real = QMessageBox.clickedButton
    QMessageBox.clickedButton = lambda self: next(b for b in self.buttons() if b.text() == label)
    return real


def _table(w):
    w._transport_tbl.setRowCount(0)
    for typ in ("Mini Bus", "Truck"):                      # minibus+driver 5 %, fuel 0 %, truck 5 %
        for line in W._transport_lines(typ, "2", "3"):
            w._append_transport_row(line)
    return w._transport_tbl


def _marks(t):
    return [t.item(r, MK).text() for r in range(t.rowCount())]


def _flags(w):
    return [w._tr_is_exception(r) for r in range(w._transport_tbl.rowCount())]


def test_fuel_starts_as_an_exception_and_the_others_share():
    w = H.generator()
    t = _table(w)
    assert _marks(t) == ["5", "0", "5"]
    assert _flags(w) == [False, True, False]
    assert w._shared_third_party_markup() == 0.05
    assert t.item(1, MK).font().italic() and not t.item(0, MK).font().italic()


def test_changing_a_line_asks_and_all_lines_changes_the_others_but_not_the_exception():
    w = H.generator()
    t = _table(w)
    H.msgs.clear()
    real = _answer("All lines")
    try:
        t.item(0, MK).setText("8")
    finally:
        QMessageBox.clickedButton = real
    assert any(m[0] == "BOX" and "other 1 line(s)" in m[2] for m in H.msgs)
    assert _marks(t) == ["8", "0", "8"] and _flags(w) == [False, True, False]
    assert w._shared_third_party_markup() == 0.08
    assert t.item(2, W._TR_TOTAL).text() != "0.00"        # the changed line's total follows


def test_only_this_line_makes_it_an_exception_that_later_changes_leave_alone():
    w = H.generator()
    t = _table(w)
    real = _answer("Only this line")
    try:
        t.item(0, MK).setText("9")
    finally:
        QMessageBox.clickedButton = real
    assert _marks(t) == ["9", "0", "5"] and _flags(w) == [True, True, False]
    assert w._shared_third_party_markup() == 0.05         # the notes quote the lines that are not exceptions
    H.msgs.clear()
    t.item(0, MK).setText("9.5")                            # an exception: no question, only itself
    assert not any(m[0] == "BOX" for m in H.msgs)
    assert _marks(t) == ["9.5", "0", "5"]
    t.item(0, MK).setText("5")                            # typed to the shared value: shares again
    assert _flags(w) == [False, True, False]


def test_no_question_when_nothing_changes_or_there_is_no_one_else():
    w = H.generator()
    t = _table(w)
    H.msgs.clear()
    t.item(0, MK).setText("5.0")                           # the same figure retyped
    assert not any(m[0] == "BOX" for m in H.msgs)
    t.removeRow(2)                                          # the only other sharing line is gone
    t.item(0, MK).setText("7")
    assert not any(m[0] == "BOX" for m in H.msgs) and _marks(t) == ["7", "0"]


def test_a_line_added_by_hand_shares_and_none_shared_means_no_value():
    w = H.generator()
    t = _table(w)
    t.item(0, MK).setText("8")                              # (the first button of the harness: All lines)
    w._add_transport_row()
    assert t.item(t.rowCount() - 1, MK).text() == "8" and _flags(w)[-1] is False
    for r in range(t.rowCount()):                           # every line an exception
        t.item(r, MK).setData(W._TR_EXC_ROLE, True)
    assert w._shared_third_party_markup() is None
    w._transport_tbl.setRowCount(0)
    assert w._shared_third_party_markup() is None


def test_a_reload_keeps_the_rule_for_the_rebuilt_lines():
    w = H.generator()
    t = _table(w)
    assert t.rowCount() == 3
    H.load_request(w)                                       # the request's own transport lines are rebuilt
    n = w._transport_tbl.rowCount()
    assert all(isinstance(f, bool) for f in _flags(w)) and len(_flags(w)) == n


def test_the_status_can_be_changed_without_typing_a_figure():
    w = H.generator()
    t = _table(w)                                           # 5, 0 (exception), 5
    w._set_transport_exception([0], True)                   # an exception, its figure stays
    assert _marks(t) == ["5", "0", "5"] and _flags(w) == [True, True, False]
    assert w._shared_third_party_markup() == 0.05           # the one line left sharing
    w._set_transport_exception([1], False)                  # back to sharing: it takes the shared figure
    assert _marks(t) == ["5", "5", "5"] and _flags(w) == [True, False, False]
    assert t.item(1, W._TR_TOTAL).text() != "0.00"
    w._set_transport_exception([0, 1, 2], True)             # every line an exception
    assert w._shared_third_party_markup() is None
    t.item(1, MK).setText("7")                              # (asks nothing: exceptions only change themselves)
    w._set_transport_exception([1, 2], False)               # nobody shares: the first one starts it
    assert _marks(t) == ["5", "7", "7"] and _flags(w) == [True, False, False]
    assert w._shared_third_party_markup() == 0.07


def test_the_transport_table_has_the_menu():
    from PySide6.QtCore import Qt
    w = H.generator()
    assert w._transport_tbl.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
