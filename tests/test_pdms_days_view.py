"""PDMS mode shows the manpower table in days and a day rate (the proposal's own figures) while the cells keep
hours and the hourly rate: header / UOM labels, hours / shift length, rate x shift length, 2 decimals, editing in
days stores hours, a row with no shift length stays in hours and gets a red "!" with the reason."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QStyleOptionViewItem

import harness as H
from ctr_tools import window as W


def _row(w, enc, qty, rate):
    tbl = w._req_manpower_tbl
    w._add_manpower_row()
    r = tbl.rowCount() - 1
    tbl.item(r, W._MP_ENC).setText(enc)
    tbl.item(r, W._MP_QTY).setText(qty)
    tbl.item(r, W._MP_RATE).setText(rate)
    tbl.item(r, W._MP_NUMEMP).setText("1")
    return r


def _shown(w, r, col):
    tbl = w._req_manpower_tbl
    idx = tbl.model().index(r, col)
    if col == W._MP_RATE:
        return tbl.itemDelegateForColumn(col).parts(idx)[0]
    v = w._days_view(idx)
    return v["shown"] if v else tbl.item(r, col).text()


def test_days_and_day_rate_are_shown_but_hours_stay_stored():
    w = H.generator()
    w.show()
    tbl = w._req_manpower_tbl
    r = _row(w, "NS-NAT-OFF-12", "25", "10")
    assert (_shown(w, r, W._MP_QTY), _shown(w, r, W._MP_RATE), _shown(w, r, W._MP_UOM)) == ("25", "10", "Hours")
    normal = {c: tbl.horizontalHeaderItem(c).text() for c in (W._MP_QTY, W._MP_RATE)}
    w._pdms_check.setChecked(True)
    assert tbl.horizontalHeaderItem(W._MP_QTY).text() == "Quantity (Days)"
    assert tbl.horizontalHeaderItem(W._MP_RATE).text() == "Rate/Day"
    assert _shown(w, r, W._MP_QTY) == "2.08"                         # 25 / 12, not rounded to a whole number
    assert _shown(w, r, W._MP_RATE) == "120.00"                      # 10 x 12
    assert _shown(w, r, W._MP_UOM) == "Days"
    assert (tbl.item(r, W._MP_QTY).text(), tbl.item(r, W._MP_RATE).text(), tbl.item(r, W._MP_UOM).text()) \
        == ("25", "10", "Hours")                                      # stored: unchanged
    assert tbl.item(r, W._MP_TOTAL).text() == "250.00"                      # the total never depends on the view
    w._pdms_check.setChecked(False)
    assert {c: tbl.horizontalHeaderItem(c).text() for c in (W._MP_QTY, W._MP_RATE)} == normal
    assert _shown(w, r, W._MP_QTY) == "25" and _shown(w, r, W._MP_UOM) == "Hours"


def test_editing_in_days_stores_hours_and_an_untouched_editor_writes_nothing():
    w = H.generator()
    w.show()
    tbl = w._req_manpower_tbl
    r = _row(w, "NS-NAT-OFF-12", "25", "10")
    w._pdms_check.setChecked(True)
    for col, typed, stored in ((W._MP_QTY, "3", "36"), (W._MP_RATE, "60", "5")):
        dg = tbl.itemDelegateForColumn(col)
        idx = tbl.model().index(r, col)
        ed = dg.createEditor(tbl.viewport(), QStyleOptionViewItem(), idx)
        dg.setEditorData(ed, idx)
        box = getattr(ed, "_edit", ed)
        assert box.text() in ("2.083333333", "120"), box.text()         # full precision in the editor
        dg.setModelData(ed, tbl.model(), idx)                          # nothing typed: the cell is not touched
        assert tbl.item(r, col).text() == ("25" if col == W._MP_QTY else "10")
        box.setText(typed)
        dg.setModelData(ed, tbl.model(), idx)
        assert tbl.item(r, col).text() == stored
    assert _shown(w, r, W._MP_QTY) == "3.00" and _shown(w, r, W._MP_RATE) == "60.00"


def test_a_row_without_a_shift_length_stays_in_hours_with_a_red_exclamation_mark():
    w = H.generator()
    w.show()
    tbl = w._req_manpower_tbl
    r = _row(w, "", "25", "10")
    w._pdms_check.setChecked(True)
    assert (_shown(w, r, W._MP_QTY), _shown(w, r, W._MP_RATE), _shown(w, r, W._MP_UOM)) == ("25", "10", "Hours")
    v = w._days_view(tbl.model().index(r, W._MP_QTY))
    assert v["problem"] and "no Encoding" in v["problem"] and "shift length" in v["problem"]
    tbl.item(r, W._MP_ENC).setText("NAT-ON")                           # an Encoding without hours at its end
    tbl.item(r, W._MP_QTY).setText("25")                               # (a changed Encoding re-matches the row)
    tbl.item(r, W._MP_RATE).setText("10")
    assert "NAT-ON has no shift length" in w._days_view(tbl.model().index(r, W._MP_RATE))["problem"]
    # the mark is painted in the cell (red pixels), and the tooltip text is the reason
    from PySide6.QtGui import QImage, QPainter
    for col in (W._MP_QTY, W._MP_RATE):
        dg = tbl.itemDelegateForColumn(col)
        opt = QStyleOptionViewItem()
        opt.rect = tbl.visualRect(tbl.model().index(r, col))
        opt.rect.moveTo(0, 0)
        opt.widget = tbl
        img = QImage(opt.rect.size(), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.white)
        p = QPainter(img)
        dg.paint(p, opt, tbl.model().index(r, col))
        p.end()
        red = QColor("#C62828").rgb()
        assert any(img.pixel(x, y) == red or abs(QColor(img.pixel(x, y)).red() - 198) < 25
                   and QColor(img.pixel(x, y)).green() < 80
                   for x in range(img.width()) for y in range(img.height())), f"no red mark in column {col}"
    tbl.item(r, W._MP_ENC).setText("NS-NAT-OFF-12")                    # fixed: converted, no mark
    assert w._days_view(tbl.model().index(r, W._MP_QTY))["problem"] is None
    assert _shown(w, r, W._MP_QTY) == "2.08"
    w._pdms_check.setChecked(False)


def test_a_row_without_a_shift_length_is_skipped_in_pdms_mode_after_a_warning():
    from PySide6.QtWidgets import QMessageBox
    w = H.generator()
    w.show()
    tbl = w._req_manpower_tbl
    good = _row(w, "NS-NAT-OFF-12", "24", "10")
    bad = _row(w, "", "8", "10")
    tbl.item(bad, W._MP_DESC).setText("Row without hours")
    tbl.item(bad, W._MP_SHIFT).setText("Day")                              # everything else is filled in: only the hours are missing
    tbl.item(bad, W._MP_SHIFTTYPE).setText("Normal")
    tbl.item(bad, W._MP_NUMEMP).setText("1")
    w._pdms_check.setChecked(True)
    bad = {(id(c["tbl"]), c["row"]) for c in w._incomplete_cells()}         # rows with a missing value are left out first
    rows = w._collect_rows(True, bad)
    skipped = rows["no_shift"]
    assert "Row without hours" in skipped
    assert all(r["description"] != "Row without hours" for r in rows["support_rows"] + rows["other_rows"])
    assert all(r["hours_per_shift"] for r in rows["support_rows"] + rows["other_rows"])   # none left that cannot be converted
    normal = w._collect_rows(False, bad)                                 # the AZN / USD CTRs do not need it: still included
    assert "Row without hours" in [r["description"] for r in normal["support_rows"] + normal["other_rows"]]
    assert normal["no_shift"] == []
    # Generate warns (Yes / Cancel) and lists the row; Cancel stops before anything is written
    tpl = H.INPUTS / "PDMS_TEMPLATE_short.docx"
    if not tpl.is_file():
        raise H.Skip("PDMS_TEMPLATE_short.docx is not in TEST_Files/CTR creator")
    out = H.SANDBOX / "out" / "noshift"
    out.mkdir(parents=True, exist_ok=True)
    w._pdms_tpl_edit.setText(str(tpl))
    w._out_dir_edit.setText(str(out))
    w._job_ref_edit.setText("998")
    w._prp_edit.setText("PRP-26-998")
    H.resolve_disparities(w)
    asked = []
    real = QMessageBox.question
    try:
        QMessageBox.question = staticmethod(lambda parent, title, text, *a, **k: (asked.append((title, text)), QMessageBox.StandardButton.Cancel)[1])
        w._generate()
        H.pump(5)
        assert asked and "SKIPPED" in asked[0][1] and "Row without hours" in asked[0][1], asked
        assert not list(out.glob("*.docx"))
    finally:
        QMessageBox.question = real
    w._pdms_check.setChecked(False)


def test_manpower_is_listed_in_the_cost_statistics_box_but_not_costed_like_the_sage_lines():
    w = H.generator()
    w.show()
    tbl = w._req_manpower_tbl
    w._refresh_stats()
    H.pump(5)
    lst = w._mp_stats_tbl
    matched = [r for r in range(tbl.rowCount()) if tbl.item(r, W._MP_STATUS).text().startswith("✓")]
    assert matched and lst.rowCount() == len(matched) and lst.isVisibleTo(w)
    for i, r in enumerate(matched):                                              # the table's own figures, not a SAGE cost
        assert [lst.item(i, c).text() for c in (0, 1, 3, 4, 5, 6)] == [
            tbl.item(r, W._MP_DESC).text(), tbl.item(r, W._MP_ENC).text(), tbl.item(r, W._MP_QTY).text(),
            "Hours", tbl.item(r, W._MP_RATE).text(), tbl.item(r, W._MP_TOTAL).text()]
    assert "Not costed from SAGE" in w._mp_stats_lbl.text() and f"{len(matched)} matched" in w._mp_stats_lbl.text()
    w._pdms_check.setChecked(True)                                                # PDMS mode: days and a day rate, as in the table
    w._refresh_stats()
    H.pump(5)
    r = matched[0]
    ph = w._row_shift_hours(r)
    assert lst.item(0, 4).text() == "Days"
    assert lst.item(0, 3).text() == f"{float(tbl.item(r, W._MP_QTY).text()) / ph:.2f}"
    assert lst.item(0, 5).text() == f"{float(tbl.item(r, W._MP_RATE).text()) * ph:.2f}"
    assert "₼" in w._mp_stats_lbl.text()
    w._pdms_check.setChecked(False)
