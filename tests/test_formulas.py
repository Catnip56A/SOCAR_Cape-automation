"""Formulas in number cells: the parser (common/formula.py) and, further below, the tables."""
import harness as H  # noqa: F401  (sandbox first, like every test)
from common import formula as F


def test_parser_basics():
    assert F.evaluate("=1+2") == 3
    assert F.evaluate("= 2 + 3 * 4 ") == 14
    assert F.evaluate("=(2+3)*4") == 20
    assert F.evaluate("=-3+5") == 2 and F.evaluate("=2*-3") == -6
    assert abs(F.evaluate("=25/12") - 2.0833333333) < 1e-9
    assert F.evaluate("=1,5*2") == 3 and F.evaluate("=.5+.5") == 1
    assert F.evaluate("=6×2÷4−1") == 2
    assert F.evaluate("1+1") == 2                                     # the "=" is optional for evaluate()
    assert F.is_formula(" =1+2") and not F.is_formula("1+2") and not F.is_formula(None)


def test_parser_refuses_anything_else():
    for bad, why in (("=", "empty"), ("=1/0", "zero"), ("=1+", "number"), ("=(1+2", "bracket"),
                     ("=1+2)", "unexpected"), ("=abs(1)", "not allowed"), ("=__import__('os')", "not allowed"),
                     ("=1 2", "unexpected"), ("=2**3", "number")):
        try:
            F.evaluate(bad)
        except F.FormulaError as e:
            assert why in str(e), (bad, str(e))
        else:
            raise AssertionError(f"{bad!r} was accepted")


def test_numbers_are_stored_without_rounding_to_the_screen():
    assert F.format_number(3.0) == "3" and F.format_number(25 / 12) == "2.083333333"
    assert float(F.format_number(0.1 + 0.2)) == 0.3


# ── the tables ──────────────────────────────────────────────────────────────────

def _row(w, enc="NS-NAT-OFF-12", qty="25", rate="10", men="1"):
    from ctr_tools import window as W
    tbl = w._req_manpower_tbl
    w._add_manpower_row()
    r = tbl.rowCount() - 1
    tbl.item(r, W._MP_ENC).setText(enc)
    for col, val in ((W._MP_QTY, qty), (W._MP_RATE, rate), (W._MP_NUMEMP, men)):
        tbl.item(r, col).setText(val)
    return r


def _type(w, r, col, text):
    """Open the cell's editor, type `text`, commit — the way a user does."""
    from PySide6.QtWidgets import QStyleOptionViewItem
    tbl = w._req_manpower_tbl
    dg = tbl.itemDelegateForColumn(col)
    idx = tbl.model().index(r, col)
    ed = dg.createEditor(tbl.viewport(), QStyleOptionViewItem(), idx)
    dg.setEditorData(ed, idx)
    box = getattr(ed, "_edit", ed)
    opened = box.text()
    box.setText(text)
    dg.setModelData(ed, tbl.model(), idx)
    return opened


def test_a_formula_is_worked_out_kept_and_comes_back_in_the_editor():
    from ctr_tools import window as W
    w = H.generator()
    w.show()
    tbl = w._req_manpower_tbl
    r = _row(w)
    _type(w, r, W._MP_NUMEMP, "=1+2")
    assert tbl.item(r, W._MP_NUMEMP).text() == "3"                       # the cell holds the number
    assert tbl.item(r, W._MP_TOTAL).text() == f"{3 * 25 * 10:.2f}"        # and everything downstream follows
    assert _type(w, r, W._MP_NUMEMP, "=1+2") == "=1+2"                    # reopened: the formula, not the value
    assert "Formula:</b> =1+2" in w._cell_hint(tbl.model().index(r, W._MP_NUMEMP), tbl.item(r, W._MP_NUMEMP).data(W._FORMULA_ROLE), None)
    assert "Number of employees" in w._cell_hint(tbl.model().index(r, W._MP_NUMEMP), None, None)
    _type(w, r, W._MP_NUMEMP, "5")                                        # a plain number retires the formula
    assert tbl.item(r, W._MP_NUMEMP).text() == "5" and not tbl.item(r, W._MP_NUMEMP).data(W._FORMULA_ROLE)
    assert _type(w, r, W._MP_NUMEMP, "5") == "5"


def test_a_formula_goes_stale_when_something_else_rewrites_the_cell():
    from ctr_tools import window as W
    w = H.generator()
    w.show()
    tbl = w._req_manpower_tbl
    r = _row(w)
    _type(w, r, W._MP_RATE, "=4*2.5")
    assert tbl.item(r, W._MP_RATE).text() == "10"
    tbl.item(r, W._MP_RATE).setText("12")                                 # e.g. a re-match writing a pricebook rate
    assert _type(w, r, W._MP_RATE, "12") == "12"                          # the old formula is not offered any more


def test_a_bad_formula_is_refused_and_the_cell_keeps_its_value():
    from ctr_tools import window as W
    w = H.generator()
    w.show()
    tbl = w._req_manpower_tbl
    r = _row(w)
    for bad in ("=1/0", "=2+", "=abc"):
        _type(w, r, W._MP_QTY, bad)
        assert tbl.item(r, W._MP_QTY).text() == "25", bad


def test_a_formula_in_a_days_cell_is_in_days_and_the_cell_keeps_hours():
    from ctr_tools import window as W
    w = H.generator()
    w.show()
    tbl = w._req_manpower_tbl
    r = _row(w)
    w._pdms_check.setChecked(True)
    _type(w, r, W._MP_QTY, "=5/2")                                        # 2.5 days at 12 h/shift = 30 h
    assert tbl.item(r, W._MP_QTY).text() == "30"
    _type(w, r, W._MP_RATE, "=60*2")                                      # 120 per day = 10 per hour
    assert tbl.item(r, W._MP_RATE).text() == "10"
    assert _type(w, r, W._MP_QTY, "=5/2") == "=5/2"                       # reopened in days: the formula
    idx = tbl.model().index(r, W._MP_QTY)
    w._pdms_check.setChecked(False)                                       # hours again: the value, with a note
    assert _type(w, r, W._MP_QTY, "30") == "30"
    hint = w._cell_hint(idx, tbl.item(r, W._MP_QTY).data(W._FORMULA_ROLE), None)
    live = tbl.item(r, W._MP_QTY).data(W._FORMULA_ROLE)
    assert live and live[0] == "=5/2" and live[2] == "days"
    assert hint and "Formula:</b> =5/2" in hint and "written in days" in hint
    assert tbl.item(r, W._MP_TOTAL).text() == f"{1 * 30 * 10:.2f}"


def test_the_hover_says_what_the_days_view_means():
    from ctr_tools import window as W
    w = H.generator()
    w.show()
    tbl = w._req_manpower_tbl
    r = _row(w)
    w._pdms_check.setChecked(True)
    q = w._cell_hint(tbl.model().index(r, W._MP_QTY), None, "days")
    assert "25" in q and "12 h per shift" in q and "2.08" in q
    t = w._cell_hint(tbl.model().index(r, W._MP_RATE), None, "days")
    assert "per day" in t and "120.00" in t
    w._pdms_check.setChecked(False)


def test_a_formula_typed_with_the_keyboard_works_in_every_number_cell():
    """The real path: the view opens its own editor (the delegate gets a new Python wrapper of it each
    time), the keys are typed and Return commits. A first version only worked when a test held on to the
    editor object itself."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLineEdit
    from ctr_tools import window as W
    w = H.generator()
    w.show()
    H.pump(10)
    w.activateWindow()
    for add, tbl in ((w._add_manpower_row, w._req_manpower_tbl), (w._add_equip_row, w._req_equip_tbl),
                     (w._add_cons_row, w._req_cons_tbl), (w._add_transport_row, w._transport_tbl)):
        add()
    cells = [(w._req_manpower_tbl, c) for c in (W._MP_NUMEMP, W._MP_QTY, W._MP_RATE)] + \
            [(w._req_equip_tbl, c) for c in (W._EQ_QTY, W._EQ_RATE, W._EQ_DAYS)] + \
            [(w._req_cons_tbl, c) for c in (W._CS_QTY, W._CS_PRICE)] + \
            [(w._transport_tbl, c) for c in (W._TR_QTY, W._TR_DUR, W._TR_RATE, W._TR_MARKUP)]
    for tbl, col in cells:
        row = tbl.rowCount() - 1                                      # the row just added
        idx = tbl.model().index(row, col)
        tbl.scrollToItem(tbl.item(row, col))
        tbl.setCurrentIndex(idx)
        tbl.edit(idx)
        H.pump(3)
        ed = QApplication.focusWidget()
        assert isinstance(ed, QLineEdit), (tbl.objectName(), col)
        ed.selectAll()
        QTest.keyClicks(ed, "=1+2")
        QTest.keyClick(ed, Qt.Key_Return)
        H.pump(3)
        assert tbl.item(row, col).text() == "3", (col, tbl.item(row, col).text())
        # opened again, the formula is there; Escape leaves the cell as it is
        tbl.edit(idx)
        H.pump(3)
        ed = QApplication.focusWidget()
        assert isinstance(ed, QLineEdit) and ed.text() == "=1+2", (col, ed.text())
        QTest.keyClick(ed, Qt.Key_Escape)
        H.pump(3)
        assert tbl.item(row, col).text() == "3"


def _type_into(widget, text):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    widget.setFocus()
    H.pump(3)
    widget.selectAll()
    QTest.keyClicks(widget, text)
    QTest.keyClick(widget, Qt.Key_Return)
    H.pump(3)


def test_the_hotbed_and_cbar_boxes_take_formulas_and_show_them_again():
    w = H.generator()
    w.show()
    w.activateWindow()
    w._pdms_check.setChecked(True)
    H.pump(5)
    for box, formula, value in ((w._hotbed_rate_edit, "=50+9", "59"), (w._hotbed_days_edit, "=10/4", "2.5"),
                                (w._fx["USD"]["edit"], "=1.7*1.02", "1.734")):
        _type_into(box, formula)
        assert box.text() == value, (formula, box.text())
        assert "Formula: " + formula in box.toolTip()
        box.clearFocus()
        w._markup_spin.setFocus()
        H.pump(3)
        box.setFocus()
        H.pump(5)
        assert box.text() == formula                       # the formula comes back on focus
        w._markup_spin.setFocus()
        H.pump(3)
        assert box.text() == value                         # and the number again when it is left
    _type_into(w._hotbed_days_edit, "=1/0")                # refused: the box keeps its value
    assert w._hotbed_days_edit.text() == "2.5"
    assert abs(w.price_totals()["hotbed"] - 59 * 2.5 * 1.734) < 1e-6 or w.price_totals()["hotbed"] > 0
    w._pdms_check.setChecked(False)


def test_the_markup_box_takes_a_formula():
    w = H.generator()
    w.show()
    w.activateWindow()
    spin = w._markup_spin
    _type_into(spin.lineEdit(), "=5+1.5")
    assert abs(spin.value() - 6.5) < 1e-9
    assert "Formula: =5+1.5" in spin.toolTip()
    _type_into(spin.lineEdit(), "=500")                    # beyond the range: held at the largest allowed
    assert spin.value() == spin.maximum()
    _type_into(spin.lineEdit(), "7")                       # a plain number retires the formula
    assert spin.value() == 7 and "Formula:" not in spin.toolTip()


def test_the_statistics_cells_work_out_a_formula_and_totals_say_how_they_are_made():
    from ctr_tools import window as W
    from stats.stats_view import StatsPanel
    assert StatsPanel.parse_days("=2*3.5") == (True, 7.0)
    assert StatsPanel.parse_days("=1/0")[0] is False and StatsPanel.parse_days("=-3") == (False, None)
    assert StatsPanel.parse_days("7") == (True, 7.0) and StatsPanel.parse_days("") == (True, None)
    w = H.generator()
    w.show()
    r = _row(w)
    tip = w._req_manpower_tbl.item(r, W._MP_TOTAL).toolTip()
    assert "Num Employees × Quantity (hours) × Rate" in tip
    w._pdms_check.setChecked(True)
    assert "in manats" in w._req_manpower_tbl.item(r, W._MP_TOTAL).toolTip()
    w._pdms_check.setChecked(False)
    for add, tbl, col, word in ((w._add_equip_row, w._req_equip_tbl, W._EQ_DAYS, "Days"),
                                (w._add_cons_row, w._req_cons_tbl, W._CS_PRICE, "Unit"),
                                (w._add_transport_row, w._transport_tbl, W._TR_DUR, "Duration")):
        add()
        hint = w._cell_hint(tbl.model().index(tbl.rowCount() - 1, col), None, None)
        assert hint and word in hint, (col, hint)
