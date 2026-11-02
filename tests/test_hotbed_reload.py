"""PDMS mode: the Hotbed row's "Reload standard" button puts back the standard rate and currency from
template_config.json (read again at that moment), never touches the days, and asks before it overwrites."""
import json

from PySide6.QtWidgets import QMessageBox

import harness as H
import ctr_tools.config as C
from ctr_tools import window as W


def _hotbed(w, rate, cur, days):
    w._hotbed_rate_edit.setText(rate)
    w._hotbed_cur_combo.setCurrentText(cur)
    w._hotbed_days_edit.setText(days)


def _std():
    d = W.pdms_defaults()
    return f"{float(d['hotbed_rate']):g}", d["category_currencies"].get("hotbed", "AZN")


def test_the_button_is_on_the_hotbed_row_in_pdms_mode_only():
    w = H.generator()
    w.show()
    assert not w._hotbed_reload_btn.isVisibleTo(w)
    w._pdms_check.setChecked(True)
    assert w._hotbed_reload_btn.isVisibleTo(w) and "days are not touched" in w._hotbed_reload_btn.toolTip()
    w._pdms_check.setChecked(False)
    assert not w._hotbed_reload_btn.isVisibleTo(w)


def test_it_puts_back_the_standard_rate_and_currency_after_a_question_and_keeps_the_days():
    w = H.generator()
    w.show()
    w._pdms_check.setChecked(True)
    rate, cur = _std()
    _hotbed(w, "100", "USD", "5")
    helper = w._hotbed_rate_edit._formula_helper
    helper.formula = ("=50*2", "100")                                       # a formula typed in the rate box is replaced too
    asked = []
    real = QMessageBox.question
    try:
        QMessageBox.question = staticmethod(lambda p, t, text, *a, **k: (asked.append(text), QMessageBox.StandardButton.No)[1])
        w._reload_hotbed_standard()
        assert asked and f"100  →  {rate}" in asked[0] and f"USD  →  {cur}" in asked[0] and "days are not touched" in asked[0]
        assert (w._hotbed_rate_edit.text(), w._hotbed_cur_combo.currentText(), w._hotbed_days_edit.text()) == ("100", "USD", "5")   # No: nothing
        QMessageBox.question = staticmethod(lambda p, t, text, *a, **k: QMessageBox.StandardButton.Yes)
        w._reload_hotbed_standard()
    finally:
        QMessageBox.question = real
    assert (w._hotbed_rate_edit.text(), w._hotbed_cur_combo.currentText(), w._hotbed_days_edit.text()) == (rate, cur, "5")
    assert helper.formula is None
    assert "days unchanged" in w._ctr_req_status.text()
    w._pdms_check.setChecked(False)


def test_when_it_is_already_the_standard_nothing_is_asked():
    w = H.generator()
    w.show()
    w._pdms_check.setChecked(True)
    rate, cur = _std()
    _hotbed(w, rate, cur, "7")
    H.msgs.clear()
    w._reload_hotbed_standard()
    assert not [m for m in H.msgs if m[0] == "question"] and "already the standard" in w._ctr_req_status.text()
    assert w._hotbed_days_edit.text() == "7"
    w._pdms_check.setChecked(False)


def test_an_edit_of_the_config_file_applies_without_restarting_and_a_broken_file_changes_nothing():
    w = H.generator()
    w.show()
    w._pdms_check.setChecked(True)
    old_path, old_pdms = C._CONFIG_PATH, dict(C.CFG["pdms"])
    sandbox = H.SANDBOX / "template_config_hotbed.json"
    try:
        data = json.loads(old_path.read_text(encoding="utf-8"))
        data["pdms"]["hotbed_rate"] = 61.5                                   # the user edits the file ...
        sandbox.write_text(json.dumps(data), encoding="utf-8")
        C._CONFIG_PATH = sandbox
        _hotbed(w, "100", "USD", "")
        w._reload_hotbed_standard()                                          # ... and the button reads it again (the dialog answers Yes)
        assert w._hotbed_rate_edit.text() == "61.5" and C.CFG["pdms"]["hotbed_rate"] == 61.5
        sandbox.write_text("{ this is not json", encoding="utf-8")           # a broken file: the values stay, a warning says why
        _hotbed(w, "100", "USD", "")
        H.msgs.clear()
        w._reload_hotbed_standard()
        assert w._hotbed_rate_edit.text() == "100" and w._hotbed_cur_combo.currentText() == "USD"
        assert C.CFG["pdms"]["hotbed_rate"] == 61.5                          # CFG untouched by the failed read
        assert any(m[0] == "warning" and "could not be read" in str(m[-1]) and "not changed" in str(m[-1]) for m in H.msgs), H.msgs
    finally:
        C._CONFIG_PATH = old_path
        C.CFG["pdms"] = old_pdms
        w._pdms_check.setChecked(False)
    assert C.CFG["pdms"]["hotbed_rate"] == old_pdms["hotbed_rate"]
