"""CBAR rate log, validation, the documents-that-used-a-rate log, and the Inspector's CBAR tab.

No network and no real data folder: the file paths are redirected into the sandbox and the download is faked.
"""
from datetime import datetime

import harness as H
import ctr_tools.cbar_rates as C

H.SANDBOX.mkdir(exist_ok=True)


def _xml(usd="1.7000", eur="1.9800", nominal="1"):
    return (f'<ValCurs><ValType><Valute Code="USD"><Nominal>{nominal}</Nominal><Name>US Dollar</Name>'
            f'<Value>{usd}</Value></Valute><Valute Code="EUR"><Nominal>1</Nominal><Name>Euro</Name>'
            f'<Value>{eur}</Value></Valute></ValType></ValCurs>')


def _fresh(name):
    C._RATES_PATH = H.SANDBOX / f"{name}_rates.json"
    C._LOG_PATH = H.SANDBOX / f"{name}_log.json"
    C._USAGE_PATH = H.SANDBOX / f"{name}_usage.json"


def _fake(day, xml):
    C.fetch_latest = lambda max_days_back=5: (day, xml)


def test_every_fetched_day_is_kept_and_can_be_looked_up():
    _fresh("kept")
    assert C.usd_history() == [] and C.rate_on("2026-10-26") is None
    for d, usd in ((datetime(2026, 10, 25), "1.7000"), (datetime(2026, 10, 26), "1.7002")):
        _fake(d, _xml(usd))
        C.fetch_usd_rate()
    assert C.usd_history() == [("2026-10-25", 1.7), ("2026-10-26", 1.7002)]
    assert C.rate_on("2026-10-25") == 1.7
    assert C.saved_rate("USD") == (1.7002, "2026-10-26")          # the snapshot is still just the latest
    _fake(datetime(2026, 10, 26), _xml("1.7001"))                 # the same day again refreshes, never duplicates
    C.fetch_usd_rate()
    assert len(C.usd_history()) == 2 and C.rate_on("2026-10-26") == 1.7001


def test_validation_flags_typos_and_parsing_slips():
    ok = C.parse_rates(_xml())
    assert C.validate_rates(ok) == ([], [])
    errors, _ = C.validate_rates({"EUR": ok["EUR"]})
    assert errors and "no USD" in errors[0]
    errors, _ = C.validate_rates({"USD": {"value": "abc", "nominal": 1}})
    assert errors
    errors, _ = C.validate_rates({"USD": {"value": 0, "nominal": 1}})
    assert errors
    _, w = C.validate_rates(C.parse_rates(_xml(usd="17.0000")))
    assert any("outside the usual" in m for m in w)
    _, w = C.validate_rates(C.parse_rates(_xml(usd="170.0000", nominal="100")))     # per 100: fine after dividing
    assert any("nominal" in m for m in w) and not any("outside" in m for m in w)
    _, w = C.validate_rates(ok, previous=1.60)
    assert any("previous logged" in m for m in w)
    _, w = C.validate_rates(ok, day=datetime(2026, 10, 25), requested=datetime(2026, 10, 26))
    assert any("requested" in m for m in w)
    _, w = C.validate_rates(C.parse_rates(_xml(eur="19.8000")))
    assert any("EUR/USD" in m for m in w)


def test_typed_rates_are_only_ever_warned_about():
    ref = (1.7, "2026-10-26")
    assert C.parse_typed_rate("1,7") == 1.7 and C.parse_typed_rate("x") is None
    assert C.validate_typed_rate("1.70", ref) == []
    assert any("not a number" in m for m in C.validate_typed_rate("abc", ref))
    assert any("positive" in m for m in C.validate_typed_rate("-1", ref))
    assert any("outside" in m for m in C.validate_typed_rate("17", ref))
    assert any("differs" in m for m in C.validate_typed_rate("1.76", ref))
    assert C.validate_typed_rate("1.74", ref) == []                  # within 3 %


def test_the_whole_log_can_be_checked():
    _fresh("check")
    assert C.validate_log() == []
    for d, usd in ((datetime(2026, 10, 24), "1.7000"), (datetime(2026, 10, 25), "17.0000")):
        C.save_rates_json(d, C.parse_rates(_xml(usd)), C.DEFAULT_CURRENCIES)
    problems = C.validate_log()
    assert problems and all(p.startswith("2026-10-25") for p in problems)


def _xml_many(usd="1.7000", eur="1.9800", gbp="2.2500", try_="0.0400"):
    def v(code, name, value, nominal=1):
        return f'<Valute Code="{code}"><Nominal>{nominal}</Nominal><Name>{name}</Name><Value>{value}</Value></Valute>'
    return ("<ValCurs><ValType>" + v("USD", "US Dollar", usd) + v("EUR", "Euro", eur) + v("GBP", "Pound", gbp)
            + v("TRY", "Lira", try_) + "</ValType></ValCurs>")


def test_every_turned_on_currency_has_its_own_line_and_reloads_on_its_own():
    _fresh("fx")
    C._CURRENCIES_PATH = H.SANDBOX / "fx_currencies.json"
    assert C.enabled_currencies() == ["USD", "EUR", "GBP"]                   # the defaults; AZN is always there
    _fake(datetime(2026, 10, 26), _xml_many())
    w = H._new_generator()
    w.show()
    w._pdms_check.setChecked(True)
    assert list(w._fx) == ["USD", "EUR", "GBP"]
    for c in w._fx:
        w._fx[c]["edit"].setText("9.99")
    w._reload_cbar(["GBP"])                                                    # only GBP
    assert H.wait(lambda: w._fx["GBP"]["btn"].isEnabled() and w._fx["GBP"]["date"], 30)
    assert w._fx["GBP"]["edit"].text() == "2.2500" and w._fx["GBP"]["date"] == "2026-10-26"
    assert w._fx["USD"]["edit"].text() == "9.99" and w._fx["EUR"]["edit"].text() == "9.99"   # the others untouched
    assert w._fx["GBP"]["action"] == "Fetched from CBAR" and w._fx["GBP"]["stamp"]          # when it was fetched is recorded
    assert w._fx["USD"]["action"] != "Fetched from CBAR"                                       # the others: not fetched
    w._reload_cbar(None)                                                       # Reload all
    assert H.wait(lambda: w._fx["USD"]["btn"].isEnabled() and w._fx["USD"]["date"], 30)
    assert [w._fx[c]["edit"].text() for c in ("USD", "EUR", "GBP")] == ["1.7000", "1.9800", "2.2500"]
    assert C.rate_on("2026-10-26") == 1.7 and C.history("EUR") == [("2026-10-26", 1.98)]
    # turning TRY on in Settings adds its line; turning EUR off removes its line, keeping the others' values
    C.set_enabled_currencies(["USD", "GBP", "TRY"])
    w.cbar_currencies_changed.emit()
    assert list(w._fx) == ["USD", "GBP", "TRY"] and w._fx["GBP"]["edit"].text() == "2.2500"
    # the figures the proposal will use
    # (TRY was in the downloaded list, so the line starts from that saved rate)
    assert w._fx_rates() == {"USD": (1.7, "2026-10-26"), "GBP": (2.25, "2026-10-26"), "TRY": (0.04, "2026-10-26")}


def test_a_suspicious_rate_is_logged_but_the_old_rate_is_kept_in_the_window():
    from PySide6.QtWidgets import QMessageBox as _QMB
    _real_question = _QMB.question
    try:
        _suspicious_rate_body()
    finally:
        _QMB.question = _real_question


def _suspicious_rate_body():
    _fresh("ui")
    C._CURRENCIES_PATH = H.SANDBOX / "ui_currencies.json"
    _fake(datetime(2026, 10, 26), _xml_many())
    w = H._new_generator()
    w.show()
    w._pdms_check.setChecked(True)
    w._reload_cbar(["USD"])
    assert H.wait(lambda: w._fx["USD"]["btn"].isEnabled() and w._fx["USD"]["date"], 30)
    assert w._fx["USD"]["edit"].text() == "1.7000" and w._fx["USD"]["date"] == "2026-10-26"
    from PySide6.QtWidgets import QMessageBox
    yes_all = QMessageBox.question
    _fake(datetime(2026, 10, 27), _xml_many(usd="17.0000"))
    # the user is asked; answering No keeps the old rate
    QMessageBox.question = staticmethod(lambda parent, title, text, *a, **k: (H.msgs.append(("question", title, text)),
                                                                              QMessageBox.StandardButton.No)[1])
    H.msgs.clear()
    w._reload_cbar(["USD"])
    assert H.wait(lambda: w._fx["USD"]["btn"].isEnabled() and H.msgs, 30)
    assert "may still be correct" in str(H.msgs[-1][-1]) and "outside the usual" in str(H.msgs[-1][-1])
    assert w._fx["USD"]["edit"].text() == "1.7000" and w._fx["USD"]["date"] == "2026-10-26"      # old rate kept
    assert "not used" in w._fx["USD"]["note"].text()
    assert "not used (looked unusual)" in w._fx["USD"]["action"] and w._fx["USD"]["stamp"]
    assert C.rate_on("2026-10-27") == 17.0                           # what CBAR published is still on record
    assert C.saved_rate("USD") == (1.7, "2026-10-26")                # and the start-up rate was not touched
    # answering Yes uses it (it may be correct) and the next start begins from it
    QMessageBox.question = staticmethod(lambda parent, title, text, *a, **k: QMessageBox.StandardButton.Yes)
    w._reload_cbar(["USD"])
    assert H.wait(lambda: w._fx["USD"]["btn"].isEnabled() and w._fx["USD"]["date"] == "2026-10-27", 30)
    assert w._fx["USD"]["edit"].text() == "17.0000" and C.saved_rate("USD") == (17.0, "2026-10-27")
    assert w._fx["USD"]["action"] == "Fetched from CBAR; looked unusual, approved by the user"
    assert C.saved_checked_at("USD")                                          # the start-up rate remembers when it was checked
    QMessageBox.question = yes_all
    _fake(datetime(2026, 10, 28), _xml_many(usd="1.7000"))           # back to normal, but 17.0 is now "the previous day"
    H.msgs.clear()
    w._reload_cbar(["USD"])
    assert H.wait(lambda: w._fx["USD"]["btn"].isEnabled() and w._fx["USD"]["date"] == "2026-10-28", 30)
    assert w._fx["USD"]["edit"].text() == "1.7000" and "previous logged" in str(H.msgs[-1][-1])   # asked again, answered Yes
    _fake(datetime(2026, 10, 29), _xml_many(usd="1.7001"))           # an ordinary day: applied without asking
    H.msgs.clear()
    w._reload_cbar(["USD"])
    assert H.wait(lambda: w._fx["USD"]["btn"].isEnabled() and w._fx["USD"]["date"] == "2026-10-29", 30)
    assert w._fx["USD"]["edit"].text() == "1.7001" and not [m for m in H.msgs if m[0] == "question"]
    # an entry that cannot be read at all cannot be approved: the old rate stays
    _fake(datetime(2026, 10, 30), _xml_many(usd="abc"))
    H.msgs.clear()
    w._reload_cbar(["USD"])
    assert H.wait(lambda: w._fx["USD"]["btn"].isEnabled() and H.msgs, 30)
    assert w._fx["USD"]["edit"].text() == "1.7001" and "could not be used" in str(H.msgs[-1][-1])
    assert w._fx["USD"]["action"] == "Fetch could not be used — previous rate kept"
    w._fx["USD"]["edit"].setText("1.71")                             # typing is unrestricted
    w._fx["USD"]["edit"].textEdited.emit("1.71")
    assert w._fx["USD"]["date"] is None and w._fx["USD"]["note"].text() == "typed by hand"
    assert w._fx["USD"]["action"] == "Typed by hand"
    # a failed fetch leaves the field alone and re-enables the button
    def boom(max_days_back=5):
        raise RuntimeError("Could not reach CBAR: offline")
    C.fetch_latest = boom
    H.msgs.clear()
    w._reload_cbar(["USD"])
    assert H.wait(lambda: w._fx["USD"]["btn"].isEnabled() and H.msgs, 30)
    assert w._fx["USD"]["edit"].text() == "1.71" and "offline" in str(H.msgs[-1][-1])
    # the next window starts from the saved rate, without the network
    nxt = H._new_generator()._fx["USD"]
    assert nxt["edit"].text() == "1.7001" and nxt["action"] == "Loaded from the last saved CBAR rate" and nxt["stamp"]
    assert C.accept_snapshot("USD", "2030-01-01") is False             # a day that was never fetched


def test_the_settings_list_and_the_available_currencies():
    _fresh("avail")
    C._CURRENCIES_PATH = H.SANDBOX / "avail_currencies.json"
    assert C.available_currencies() == [("EUR", ""), ("GBP", ""), ("USD", "")]       # nothing fetched yet: just the defaults
    _fake(datetime(2026, 10, 26), _xml_many())
    names = dict(C.fetch_available())
    assert names["TRY"] == "Lira" and names["USD"] == "US Dollar"                     # everything CBAR published, not only the turned-on
    assert C.enabled_currencies() == ["USD", "EUR", "GBP"]
    C.set_enabled_currencies(["gbp", "AZN", "TRY", "gbp"])
    assert C.enabled_currencies() == ["GBP", "TRY"]                                   # AZN is never listed, duplicates and case folded
    # other currencies: a big jump is only a warning, a broken entry is not logged
    rates = C.parse_rates(_xml_many(gbp="abc"))
    assert C.validate_currency("GBP", rates)[0]
    _, w = C.validate_currency("TRY", C.parse_rates(_xml_many(try_="0.0500")), previous=0.04)
    assert w and "more than 5%" in w[0]


def test_documents_that_used_a_rate_are_logged_and_listed_in_the_inspector():
    from ctr_tools.inspector import CbarTab, build_snapshot
    _fresh("usage")
    assert C.load_usage() == []
    C.save_rates_json(datetime(2026, 10, 26), C.parse_rates(_xml()), C.DEFAULT_CURRENCIES)
    C.log_usage("PRP-26-001 Job A.docx", "PRP-26-001", {"USD": (1.7, "2026-10-26"), "GBP": (2.25, "2026-10-26")})
    C.log_usage("PRP-26-002 Job B.docx", "PRP-26-002", {"USD": (1.75, None)})
    usage = C.load_usage()
    assert [u["source"] for u in usage] == ["CBAR", "typed"] and usage[1]["rate_date"] is None
    tab = CbarTab()
    tab.refresh()
    assert tab._table.rowCount() == 2
    assert tab._table.item(0, 1).text() == "PRP-26-002 Job B.docx"           # newest first
    assert "USD 1.7000 (2026-10-26)" in tab._table.item(1, 3).text() and "GBP 2.2500 (2026-10-26)" in tab._table.item(1, 3).text()
    assert tab._table.item(0, 4).text() == "1.7500 (typed)" and "1 day(s) logged" in tab._note.text()
    # the chart and the list follow the chosen currency: GBP was used by the first document only
    C.save_rates_json(datetime(2026, 10, 26), C.parse_rates(_xml_many()), C.DEFAULT_CURRENCIES)
    tab.refresh()
    tab._cur.setCurrentText("GBP")
    assert tab._code() == "GBP" and tab._row_days == ["", "2026-10-26"] and tab._rates == {"2026-10-26": 2.25}
    assert tab._table.item(0, 4).text() == "—" and tab._table.item(1, 4).text() == "2.2500 (2026-10-26)"


def test_hovering_a_dot_shows_its_details_and_clicking_goes_to_its_documents():
    from PySide6.QtCore import QDateTime, QPointF
    from PySide6.QtWidgets import QToolTip
    from ctr_tools.inspector import CbarTab
    _fresh("hover")
    for d, usd in ((datetime(2026, 10, 24), "1.7000"), (datetime(2026, 10, 25), "1.7001"),
                   (datetime(2026, 10, 26), "1.7002")):
        C.save_rates_json(d, C.parse_rates(_xml(usd)), C.DEFAULT_CURRENCIES)
    C.log_usage("PRP-26-001 Job A.docx", "PRP-26-001", {"USD": (1.7001, "2026-10-25")})
    C.log_usage("PRP-26-002 Job B.docx", "PRP-26-002", {"USD": (1.7002, "2026-10-26")})
    C.log_usage("PRP-26-003 Job C.docx", "PRP-26-003", {"USD": (1.7001, "2026-10-25")})
    tab = CbarTab()
    tab.resize(700, 600)
    tab.show()
    H.pump(5)
    tab.refresh()

    def pt(day):
        return QPointF(QDateTime.fromString(day, "yyyy-MM-dd").toMSecsSinceEpoch(), 1.7)

    tab._on_hover(pt("2026-10-25"), True)                       # a day two documents used
    tip = QToolTip.text()
    assert "2026-10-25" in tip and "1.7001" in tip and "2 document(s)" in tip
    assert "PRP-26-001 Job A.docx" in tip and "PRP-26-003 Job C.docx" in tip and "Job B" not in tip
    tab._on_hover(pt("2026-10-24"), True)                       # a day nobody used
    assert "No document used this rate" in QToolTip.text()
    tab._on_hover(pt("2026-10-24"), False)
    assert H.wait(lambda: QToolTip.text() == "", 3)               # Qt hides a tip after a short delay

    tab._on_click(pt("2026-10-25"))                             # click: those rows are selected in the table below
    picked = sorted({i.row() for i in tab._table.selectedIndexes()})
    assert [tab._table.item(r, 2).text() for r in picked] == ["PRP-26-003", "PRP-26-001"]    # newest first
    assert "2 document(s) used" in tab._note.text()
    tab._on_click(pt("2026-10-24"))                             # a day with no documents: nothing selected, says so
    assert not tab._table.selectedIndexes() and "no document" in tab._note.text()
    # the real chart signals are connected to the same handlers
    tab._every.hovered.emit(pt("2026-10-26"), True)
    assert "PRP-26-002" in QToolTip.text()
    tab._dots.clicked.emit(pt("2026-10-26"))
    assert {tab._table.item(i.row(), 2).text() for i in tab._table.selectedIndexes()} == {"PRP-26-002"}


def test_a_priced_cell_holds_a_number_and_a_currency_shown_as_its_sign():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QStyleOptionViewItem
    from ctr_tools import window as W
    _fresh("cells")
    C._CURRENCIES_PATH = H.SANDBOX / "cells_currencies.json"
    w = H.generator()
    w.show()
    H.pump(5)
    eq = w._req_equip_tbl
    assert eq.columnCount() == len(W._EQUIPMENT_HEADERS) and "Currency" not in W._EQUIPMENT_HEADERS   # no separate column
    normal = {k: tbl.horizontalHeaderItem(col).text() for k, tbl, col in
              ((k, t, c) for k, t, c in w._money_columns())}
    assert normal == {"manpower": "Rate AZN", "equipment": "Rate/Day USD", "consumables": "Unit Price USD",
                      "transport": "Rate AZN"}
    assert w._groups["equipment"].title() == "Plant & Equipment — USD"
    w._pdms_check.setChecked(True)
    for key, tbl, col in w._money_columns():                                   # no fixed currency in the Rate headers
        assert not any(c in tbl.horizontalHeaderItem(col).text() for c in ("AZN", "USD"))
    assert eq.horizontalHeaderItem(W._EQ_TOTAL).text() == "Total AZN"           # totals are in manats
    assert [w._cat_combos[k].currentText() for k in ("manpower", "equipment", "consumables", "transport")] \
        == ["AZN", "USD", "USD", "AZN"]
    assert [w._cat_combos["equipment"].itemText(i) for i in range(w._cat_combos["equipment"].count())] \
        == ["AZN", "USD", "EUR", "GBP"]
    # a real row: 2 x 10 per day x 3 days; the default is USD, and USD is 1.70 manats
    r = next(i for i in range(eq.rowCount()) if eq.item(i, W._EQ_STATUS).text().startswith("✓"))
    w._fx["USD"]["edit"].setText("1.7000")
    w._fx["GBP"]["edit"].setText("2.2500")
    for col, val in ((W._EQ_QTY, "2"), (W._EQ_RATE, "10"), (W._EQ_DAYS, "3")):
        eq.item(r, col).setText(val)
    delegate = eq.itemDelegateForColumn(W._EQ_RATE)
    idx = eq.model().index(r, W._EQ_RATE)
    assert delegate.parts(idx) == ("10", "$")                                  # the number and the sign
    assert eq.item(r, W._EQ_TOTAL).text() == f"{2 * 10 * 3 * 1.7:.2f}"          # converted to manats
    # the editor: number box + a drop-down of codes; choosing GBP keeps the number and changes only the currency
    ed = delegate.createEditor(eq.viewport(), QStyleOptionViewItem(), idx)
    delegate.setEditorData(ed, idx)
    assert ed._edit.text() == "10" and ed._combo.currentText() == "USD"
    assert [ed._combo.itemText(i) for i in range(ed._combo.count())] == ["AZN", "USD", "EUR", "GBP"]
    ed._combo.setCurrentText("GBP")
    delegate.setModelData(ed, eq.model(), idx)
    assert eq.item(r, W._EQ_RATE).text() == "10" and delegate.parts(idx) == ("10", "£")
    assert eq.item(r, W._EQ_TOTAL).text() == f"{2 * 10 * 3 * 2.25:.2f}"
    H.pump(3)                                                                    # the title follows a moment later
    assert w._groups["equipment"].title() == "Plant & Equipment — USD, GBP"      # the main one first, then the others used
    assert "GBP" in w._currencies_in_use()
    ed._combo.setCurrentText("USD")                                             # back to the default: it follows the box again
    delegate.setModelData(ed, eq.model(), idx)
    H.pump(3)
    assert eq.item(r, W._EQ_RATE).data(W._CUR_ROLE) is None and w._groups["equipment"].title() == "Plant & Equipment — USD"
    w._cat_combos["equipment"].setCurrentText("EUR")                            # change the whole category
    assert w._groups["equipment"].title() == "Plant & Equipment — EUR" and delegate.parts(idx) == ("10", "€")
    w._fx["EUR"]["edit"].setText("1.9800")
    assert eq.item(r, W._EQ_TOTAL).text() == f"{2 * 10 * 3 * 1.98:.2f}"
    # a text such as NONRECHARG has no sign; a currency with no rate gives a 0.00 total with a tooltip
    eq.item(r, W._EQ_RATE).setText("NONRECHARG")
    assert delegate.parts(idx) == ("NONRECHARG", "")
    eq.item(r, W._EQ_RATE).setText("10")
    w._fx["EUR"]["edit"].setText("")
    assert eq.item(r, W._EQ_TOTAL).text() == "0.00" and "No CBAR rate for EUR" in eq.item(r, W._EQ_TOTAL).toolTip()
    # the sums are in manats too, and the normal CTR look comes back untouched
    assert "₼" in w._usd_equip_lbl.text()
    w._pdms_check.setChecked(False)
    assert eq.horizontalHeaderItem(W._EQ_RATE).text() == "Rate/Day USD" and eq.horizontalHeaderItem(W._EQ_TOTAL).text() == "Total USD"
    assert eq.item(r, W._EQ_TOTAL).text() == f"{2 * 10 * 3:.2f}" and "$" in w._usd_equip_lbl.text()
    assert delegate.parts(idx) == ("10", "$")                                     # outside PDMS mode the category's own currency
    assert w._groups["equipment"].title() == "Plant & Equipment — USD"


def test_signs_of_currencies():
    assert [C.currency_sign(c) for c in ("AZN", "usd", "EUR", "GBP", "TRY", "RUB")] == ["₼", "$", "€", "£", "₺", "₽"]
    assert C.currency_sign("XYZ") == "XYZ"                                         # no common sign: the code


def test_a_cell_in_another_currency_needs_its_rate_and_reaches_the_proposal():
    import docx
    from ctr_tools import window as W
    template = H.INPUTS / "PDMS_TEMPLATE_short.docx"
    if not template.is_file():
        raise H.Skip("PDMS_TEMPLATE_short.docx is not in TEST_Files/CTR creator")
    _fresh("e2e")
    C._CURRENCIES_PATH = H.SANDBOX / "e2e_currencies.json"
    w = H.generator()
    w.show()
    w._pdms_check.setChecked(True)
    eq = w._req_equip_tbl
    r = next(i for i in range(eq.rowCount()) if eq.item(i, W._EQ_STATUS).text().startswith("✓")
             and not eq.item(i, W._EQ_DESC).data(W._SCAFFOLD_ROLE))
    desc = eq.item(r, W._EQ_DESC).text()
    for col, val in ((W._EQ_QTY, "2"), (W._EQ_RATE, "10"), (W._EQ_DAYS, "3")):
        eq.item(r, col).setText(val)
    eq.item(r, W._EQ_RATE).setData(W._CUR_ROLE, "GBP")
    w._fx["GBP"]["edit"].setText("")
    out = H.SANDBOX / "out" / "pdms_gbp"
    out.mkdir(parents=True, exist_ok=True)
    w._pdms_tpl_edit.setText(str(template))
    w._out_dir_edit.setText(str(out))
    w._job_ref_edit.setText("999")
    w._prp_edit.setText("PRP-26-888")
    H.resolve_disparities(w)
    H.msgs.clear()
    w._generate()                                                                 # GBP is used but has no rate
    H.pump(5)
    assert any("GBP→AZN rate must be a positive number" in str(m[-1]) for m in H.msgs), H.msgs
    assert not list(out.glob("*.docx"))
    w._fx["GBP"]["edit"].setText("2.2500")
    H.msgs.clear()
    w._generate()
    assert H.wait(lambda: any(m[0] in ("information", "critical") for m in H.msgs), 120)
    assert not any(m[0] == "critical" for m in H.msgs), H.msgs
    doc = docx.Document(str(next(out.glob("PRP-26-888*.docx"))))
    row = next(r_ for r_ in doc.tables[1].rows if r_.cells[0].text == desc)       # the equipment table
    assert row.cells[-1].text == f"{2 * 3 * 10 * 2.25:,.2f} ₼"                    # 2 x 3 days x 10 GBP x 2.25
    used = [u for u in C.load_usage() if u["prp"] == "PRP-26-888"][0]["rates"]
    assert set(used) == {"USD", "GBP"} and used["GBP"]["rate"] == 2.25 and used["GBP"]["source"] == "typed"


def _dark_columns(delegate, idx, widget, width=240, height=26):
    """Paint one cell into a picture; the x positions where something dark was drawn."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QPainter, QPixmap
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
    pm = QPixmap(width, height)
    pm.fill(QColor("white"))
    option = QStyleOptionViewItem()
    option.rect, option.widget, option.palette = QRect(0, 0, width, height), widget, widget.palette()
    option.state = QStyle.StateFlag.State_Enabled
    painter = QPainter(pm)
    delegate.paint(painter, option, idx)
    painter.end()
    img = pm.toImage()
    return sorted({x for x in range(width) for y in range(height) if img.pixelColor(x, y).lightness() < 140})


def test_hotbed_in_the_price_totals_one_click_currency_and_the_non_rechargeable_word():
    from PySide6.QtCore import QEvent, QPointF, QPoint, Qt
    from PySide6.QtGui import QMouseEvent
    from ctr_tools import window as W
    _fresh("hb")
    C._CURRENCIES_PATH = H.SANDBOX / "hb_currencies.json"
    w = H.generator()
    w.show()
    w._pdms_check.setChecked(True)
    H.pump(3)
    for code, rate in (("USD", "1.7000"), ("GBP", "2.2500")):
        w._fx[code]["edit"].setText(rate)
    # -- Hotbed in the price totals, under Transport; the manpower-only sums say they do not hold it
    base = w.price_totals()
    assert base["hotbed"] == 0 and "Hotbed (PDMS proposal)" in w._price_totals_lbl.text()
    w._hotbed_rate_edit.setText("59")
    w._hotbed_days_edit.setText("10")
    t = w.price_totals()
    assert t["hotbed"] == 590 and abs(t["azn_total"] - (t["manpower"] + t["transport"] + 590)) < 1e-6
    html = w._price_totals_lbl.text()
    assert html.index("AZN — Transport") < html.index("AZN — Hotbed") < html.index("AZN CTR total")
    assert "without the Hotbed line" in html and "Hotbed not included" in w._azn_total_lbl.text()
    w._hotbed_cur_combo.setCurrentText("GBP")                                    # 59 pounds × 10 days × 2.25
    assert abs(w.price_totals()["hotbed"] - 59 * 10 * 2.25) < 1e-6
    w._hotbed_days_edit.setText("")                                              # no days: left out everywhere
    assert w.price_totals()["hotbed"] == 0 and "without the Hotbed line" not in w._price_totals_lbl.text()
    assert "Hotbed not included" not in w._azn_total_lbl.text()
    w._pdms_check.setChecked(False)
    assert "Hotbed (PDMS proposal)" not in w._price_totals_lbl.text()
    w._pdms_check.setChecked(True)
    # -- one click: the right half opens the drop-down, picking a currency stores it at once and closes the editor
    eq = w._req_equip_tbl
    r = next(i for i in range(eq.rowCount()) if eq.item(i, W._EQ_STATUS).text().startswith("✓"))
    for col, val in ((W._EQ_QTY, "2"), (W._EQ_RATE, "10"), (W._EQ_DAYS, "3")):
        eq.item(r, col).setText(val)
    delegate = eq.itemDelegateForColumn(W._EQ_RATE)
    idx = eq.model().index(r, W._EQ_RATE)
    option = W.QStyleOptionViewItem()
    option.rect = eq.visualRect(idx)

    def click(x):
        return QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(x, 5), Qt.MouseButton.LeftButton,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    delegate.editorEvent(click(option.rect.x() + 5), eq.model(), option, idx)
    assert delegate._popup_next is False                                          # the number half: just edit the number
    delegate.editorEvent(click(option.rect.x() + option.rect.width() - 5), eq.model(), option, idx)
    assert delegate._popup_next is True                                           # the currency half: open the list
    eq.edit(idx)
    ed = eq.indexWidget(idx)
    assert ed is not None and delegate._popup_next is False
    assert eq.item(r, W._EQ_TOTAL).text() == f"{2 * 10 * 3 * 1.7:.2f}"
    ed._combo.setCurrentText("GBP")
    ed._combo.activated.emit(ed._combo.currentIndex())                            # picking is the whole edit
    assert eq.item(r, W._EQ_RATE).data(W._CUR_ROLE) == "GBP"
    assert eq.item(r, W._EQ_TOTAL).text() == f"{2 * 10 * 3 * 2.25:.2f}"           # recalculated at once
    H.pump(5)
    assert eq.indexWidget(idx) is None                                            # and the editor is gone
    # -- non-rechargeable: the word, over the whole cell, no sign
    num_cols = _dark_columns(delegate, idx, eq)
    assert num_cols                                                               # a price: the number left, the sign right
    left_half = [x for x in num_cols if x < 120]
    right_half = [x for x in num_cols if x >= 120]
    assert left_half and right_half                                               # both halves are drawn on
    eq.item(r, W._EQ_RATE).setText("NONRECHARG")
    assert delegate.parts(idx) == ("NONRECHARG", "")
    word_cols = _dark_columns(delegate, idx, eq)
    assert max(word_cols) - min(word_cols) > 70                                   # a word, wider than a number
    assert min(word_cols) < 14                                                    # it starts at the LEFT edge of the cell
    assert delegate.NON_RECHARGEABLE == "NON-RECHARGEABLE"                        # in capitals
    w._pdms_check.setChecked(False)                                               # the same in the normal CTR tables
    assert min(_dark_columns(delegate, idx, eq)) < 14 and max(_dark_columns(delegate, idx, eq)) - min(_dark_columns(delegate, idx, eq)) > 70


def test_the_hotbed_include_box_decides_only_for_an_incomplete_line():
    _fresh("hbbox")
    C._CURRENCIES_PATH = H.SANDBOX / "hbbox_currencies.json"
    w = H.generator()
    w.show()
    w._pdms_check.setChecked(True)
    box = w._hotbed_incl_chk
    assert box.isChecked()                                                         # on by default
    w._hotbed_rate_edit.setText("59")
    w._hotbed_days_edit.setText("")
    assert w._hotbed_values() == {"rate": 59.0, "days": None, "currency": "AZN"}   # in, with just what is given
    assert not w._hotbed_complete() and w.price_totals()["hotbed"] == 0           # not calculated
    assert "inserted as given" in w._hotbed_total_lbl.text()
    w._hotbed_rate_edit.setText("")
    assert w._hotbed_values()["rate"] is None and w._hotbed_values()["days"] is None   # even empty: the line is there
    box.setChecked(False)                                                          # unticked: an incomplete line is left out
    assert w._hotbed_values() is None and "left out" in w._hotbed_total_lbl.text()
    w._hotbed_rate_edit.setText("59")
    w._hotbed_days_edit.setText("10")                                              # complete: calculated, whatever the box says
    assert w._hotbed_values()["days"] == 10.0 and w._hotbed_complete() and w.price_totals()["hotbed"] == 590
    assert "= 590.00" in w._hotbed_total_lbl.text()
    box.setChecked(True)
    assert w.price_totals()["hotbed"] == 590
    # the Hotbed's currency needs a rate only while it has a rate to convert
    w._hotbed_cur_combo.setCurrentText("GBP")
    assert "GBP" in w._currencies_in_use()
    w._hotbed_rate_edit.setText("")
    assert "GBP" not in w._currencies_in_use()


def test_markup_is_ten_percent_in_pdms_mode_and_the_ctr_value_otherwise():
    from ctr_tools import window as W
    w = H.generator()
    w.show()
    normal = W._DEFAULT_MARKUP_PCT
    assert normal == 6.5 and w._markup_spin.value() == normal
    w._pdms_check.setChecked(True)
    assert w._markup_spin.value() == 10.0 and w._markup_rate() == 0.10          # PDMS: usually 10 %
    assert "10.00% markup" in w._usd_cons_lbl.text()
    w._markup_spin.setValue(8.0)                                                  # it varies: changed by hand
    assert w._markup_rate() == 0.08
    w._pdms_check.setChecked(False)
    assert w._markup_spin.value() == normal                                      # back to the CTR's 6.5 %
    w._markup_spin.setValue(7.0)                                                  # a CTR value typed by hand is kept too
    w._pdms_check.setChecked(True)
    assert w._markup_spin.value() == 10.0
    w._pdms_check.setChecked(False)
    assert w._markup_spin.value() == 7.0
    w._markup_spin.setValue(normal)


def test_the_reload_all_buttons_explain_the_consequences_and_only_go_on_after_a_yes():
    from PySide6.QtWidgets import QMessageBox
    from ctr_tools import window as W
    w = H.generator()
    w.show()
    w._pdms_check.setChecked(True)
    calls = []
    w._start_reload = lambda cat, rows, cols, everything: calls.append((cat, everything))   # what the Yes would run
    real = QMessageBox.question
    asked = []
    try:
        QMessageBox.question = staticmethod(lambda parent, title, text, *a, **k: (asked.append((title, text)),
                                                                                  QMessageBox.StandardButton.No)[1])
        w._ask_reload_all("equipment")
        assert calls == [] and len(asked) == 1                        # No: nothing is reloaded
        title, text = asked[0]
        assert title == "Reload all Equipment" and "CTR Request file" in text and "currency" in text
        QMessageBox.question = staticmethod(lambda parent, title, text, *a, **k: QMessageBox.StandardButton.Yes)
        w._ask_reload_all("equipment")
        assert calls == [("equipment", True)]                         # Yes: it runs, for everything
        asked.clear()
        QMessageBox.question = staticmethod(lambda parent, title, text, *a, **k: (asked.append(text), QMessageBox.StandardButton.No)[1])
        w._pdms_check.setChecked(False)
        w._ask_reload_all("manpower")
        assert "currency" not in asked[0] and "shift length" in asked[0]    # the PDMS line only in PDMS mode
    finally:
        QMessageBox.question = real
    assert w._rematch_note().text().startswith("Reload all reads")


def test_the_storage_list_and_the_snapshot_know_the_pdms_template_and_the_cbar_files():
    from ctr_tools import inspector as I
    _fresh("snap")
    C._CURRENCIES_PATH = H.SANDBOX / "snap_currencies.json"
    w = H.generator()
    w.show()
    w._pdms_check.setChecked(True)
    w._pdms_tpl_edit.setText(str(H.INPUTS / "PDMS_TEMPLATE_short.docx"))
    w._fx["USD"]["edit"].setText("1.7000")
    w._on_fx_typed("USD")

    class _Win:                                                      # the parts of the main window the lists read
        _ctr_gen = w
    labels = {e["label"] for e in I.collect_storage(_Win())}
    assert any(l.startswith("PDMS Template") for l in labels)
    assert {"CBAR rates (saved, what the app starts from)", "CBAR rate log", "CBAR currencies turned on",
            "CBAR document-usage log"} <= labels
    C.save_rates_json(datetime(2026, 10, 26), C.parse_rates(_xml_many()), C.DEFAULT_CURRENCIES)
    C.log_usage("PRP-26-001 Secret job.docx", "PRP-26-001", {"USD": (1.7, "2026-10-26")})
    text = I.build_snapshot(_Win(), None)
    assert "PDMS proposal mode: on" in text and "USD: 1.7000" in text and "Typed by hand" in text
    assert "Default currencies:" in text and "PRP-26-001" not in text and "Secret job" not in text   # no document names


def test_a_rate_that_was_never_fetched_is_logged_as_the_default_not_as_typed():
    _fresh("dflt")
    C._CURRENCIES_PATH = H.SANDBOX / "dflt_currencies.json"
    w = H.generator()
    w.show()
    w._pdms_check.setChecked(True)
    assert w._fx["USD"]["action"] == "Default value, not fetched yet" and w._fx["USD"]["date"] is None
    assert w._pdms_payload()["rates_used"]["USD"] == (1.7, None, "default")
    w._on_fx_typed("USD")                                                      # once typed over, it is "typed" again
    assert w._pdms_payload()["rates_used"]["USD"][2] is None
    # the usage log and the Inspector keep the difference
    C.log_usage("a.docx", "P1", {"USD": (1.7, None, "default"), "EUR": (1.98, None), "GBP": (2.25, "2026-10-26")})
    entry = C.load_usage()[-1]
    assert entry["rates"]["USD"]["source"] == "default" and entry["source"] == "default"
    assert entry["rates"]["EUR"]["source"] == "typed" and entry["rates"]["GBP"]["source"] == "CBAR"
    from ctr_tools.inspector import CbarTab
    tab = CbarTab()
    tab.refresh()
    assert "USD 1.7000 (default)" in tab._table.item(0, 3).text() and "EUR 1.9800 (typed)" in tab._table.item(0, 3).text()


def test_a_hotbed_whose_currency_has_no_rate_says_so_in_the_price_totals():
    _fresh("hbfx")
    C._CURRENCIES_PATH = H.SANDBOX / "hbfx_currencies.json"
    w = H.generator()
    w.show()
    w._pdms_check.setChecked(True)
    w._hotbed_rate_edit.setText("20")
    w._hotbed_days_edit.setText("3")
    w._hotbed_cur_combo.setCurrentText("GBP")
    w._fx["GBP"]["edit"].setText("")
    w._refresh_price_totals()
    assert w.price_totals()["hotbed"] == 0 and "no CBAR rate for GBP" in w._price_totals_lbl.text()
    w._fx["GBP"]["edit"].setText("2.25")
    w._refresh_price_totals()
    assert abs(w.price_totals()["hotbed"] - 20 * 3 * 2.25) < 1e-6 and "no CBAR rate" not in w._price_totals_lbl.text()
