"""The PDMS notes in the Generator: the Notes button (red until opened, red again for a new job), the floating window
(ticks, order, editing, placeholder values that are remembered / forgotten / suggested closest first), the values the
app fills itself, the Generate reminder (not a lock-out) and its Settings switch. Sandboxed settings and notes file."""
import harness as H
from harness import W
from PySide6.QtWidgets import QMessageBox

from ctr_tools import pdms_notes as N
from ctr_tools.pdms_notes_view import NoteEditor, PdmsNotesWindow


def _gen():
    N._NOTES_PATH = H.SANDBOX / "notes_window.json"
    N._NOTES_PATH.unlink(missing_ok=True)
    w = H.generator()
    if w._notes_win is not None:
        w._notes_win.deleteLater()
        w._notes_win = None
    w._notes_seen = False
    w._settings().remove(w.KEY_WARN_UNSEEN_NOTES)
    w._update_notes_button()
    return w


def _total():
    d = N.load_notes()
    return sum(len(N.notes_for(d, p)) for p in d["places"])


def test_the_button_is_red_until_the_notes_are_opened_and_red_again_for_a_new_job():
    w = _gen()
    w.show()
    H.pump(3)
    assert w._notes_btn.isHidden() or not w._notes_btn.isVisibleTo(w)             # only in PDMS mode
    w._pdms_check.setChecked(True)
    assert w._notes_btn.isVisibleTo(w)
    assert "#B71C1C" in w._notes_btn.styleSheet() and "look at them" in w._notes_btn.text()
    w._open_notes_window()
    assert w._notes_seen and w._notes_win.isVisible()
    assert "#B71C1C" not in w._notes_btn.styleSheet() and w._notes_btn.text() == f"Notes (15 of {_total()} ticked)…"
    w._job_ref_edit.setText("A-100")
    w._job_ref_edit.editingFinished.emit()                                         # another Job Ref
    assert not w._notes_seen and "#B71C1C" in w._notes_btn.styleSheet()
    w._open_notes_window()
    w._job_ref_edit.editingFinished.emit()                                         # the same Job Ref again: nothing
    assert w._notes_seen
    w._reset_notes_for_task()                                                      # (what a new CTR Request does)
    assert not w._notes_seen
    w._notes_win.close()
    w._pdms_check.setChecked(False)


def test_ticks_order_and_what_the_proposal_gets():
    w = _gen()
    win = w._ensure_notes_window()
    p = win.payload()
    assert [x["kind"] for x in p["items"]["schedule"]] == ["numbered"] + ["bullet"] * 5
    assert len(p["items"]["exclusions"]) == 6 and p["values"]["hours"] == "12" and p["values"]["location"] == "Offshore"
    win.tick("exclusions", "excl-lunch", False)
    assert len(win.payload()["items"]["exclusions"]) == 5 and win.counts() == (14, _total())
    win.tree.setCurrentItem(next(win.tree.topLevelItem(1).child(i) for i in range(6)
                                 if win.tree.topLevelItem(1).child(i).data(0, W.Qt.UserRole)[1] == "excl-laydown"))
    win._move(-1)                                                                  # saved at once
    # (it swaps with the note above it, ticked or not: container, laydown, lunch …)
    assert [n["id"] for n in win.ticked_notes("exclusions")][:2] == ["excl-container", "excl-laydown"]
    assert [n["id"] for n in N.notes_for(N.load_notes(), "exclusions")][:3] == ["excl-container", "excl-laydown", "excl-lunch"]
    win.search.setText("mark-up")                                                  # the search bar filters by text
    shown = [win.tree.topLevelItem(i).child(j).text(0) for i in range(3) for j in range(win.tree.topLevelItem(i).childCount())]
    assert shown and all("mark-up" in t.lower() for t in shown)
    win.search.setText("")
    win._reset_ticks()
    assert win.counts() == (15, _total())


def test_values_are_remembered_when_chosen_not_while_typing_and_can_be_forgotten():
    w = _gen()
    win = w._ensure_notes_window()
    assert "working_days" in win._boxes and "hours" in win._boxes
    box = win._boxes["working_days"]
    box.combo.setEditText("Mon")
    box.typed.emit("working_days", "Mon")                                         # typing: the proposal follows, nothing is kept
    assert win.values["working_days"] == "Mon" and "working_days" not in N.load_notes()["values"]
    win.set_value("working_days", "Monday to Friday")                             # chosen / finished: remembered and saved
    assert N.load_notes()["values"]["working_days"] == ["Monday to Friday"]
    assert any("Monday to Friday" in win.tree.topLevelItem(0).child(j).text(0) for j in range(6))    # the text follows
    box.set_value("")
    box._typed("mond")
    assert box._model.stringList()[0] == "Monday to Friday"                        # the closest first
    win.set_value("working_days", "Monday to Friday", remember=False)
    box.combo.setEditText("Monday to Friday")
    box.forget_btn.click()
    assert "working_days" not in N.load_notes()["values"]
    assert win.payload()["values"]["working_days"] == "Monday to Friday"           # (the proposal keeps the value shown)


def test_the_app_fills_what_it_knows_and_a_typed_value_wins():
    w = _gen()
    w._project_type_edit.setText("Onshore")
    w._markup_spin.setValue(10.0)
    t = w._transport_tbl
    t.setRowCount(0)
    for line in W._transport_lines("Mini Bus", "2", "3"):
        w._append_transport_row(line)                                              # 5 % shared, fuel an exception
    win = w._ensure_notes_window()
    v = win.payload()["values"]
    assert v["location"] == "Onshore" and v["markup_materials"] == "10" and v["markup_third_party"] == "5"
    assert v["currency"] == "AZN"
    win.set_value("markup_materials", "12")                                        # the user typed one
    w._markup_spin.setValue(9.0)
    win.set_auto(w._notes_auto())
    assert win.payload()["values"]["markup_materials"] == "12" and win.payload()["values"]["markup_third_party"] == "5"
    w._set_transport_exception([0], True)                                          # every line an exception: nothing known
    assert w._notes_auto()["markup_third_party"] == ""
    assert win.missing() == [] or "markup_third_party" in win.missing()


def test_editing_adding_and_deleting_notes_are_saved_at_once():
    w = _gen()
    win = w._ensure_notes_window()
    note = next(n for n in win.data["notes"] if n["id"] == "price-validity")
    ed = NoteEditor(win, win.data, note)
    ed.text.setPlainText("Prices Validity - valid for {validity_days} days from {start_date}.")
    here, pre = ed._place_boxes["exclusions"]
    here.setChecked(True)                                                          # also in Exclusions, not pre-ticked
    pre.setChecked(False)
    win.after_edit(ed.apply())
    saved = N.load_notes()
    n2 = next(n for n in saved["notes"] if n["id"] == "price-validity")
    assert "{start_date}" in n2["text"] and set(n2["places"]) == {"price_basis", "exclusions"}
    assert "price-validity" in win.ticks["price_basis"] and "price-validity" not in win.ticks["exclusions"]
    assert "start_date" in win.used_placeholders() and "start_date" in win.missing()   # her own placeholder, no value yet
    ed2 = NoteEditor(win, win.data, None)
    ed2.text.setPlainText("A new exclusion.")
    ed2.kind.setCurrentIndex(ed2.kind.findData("numbered"))
    ed2._place_boxes["exclusions"][0].setChecked(True)
    new = ed2.apply()
    win.after_edit(new)
    assert new["id"] in win.ticks["exclusions"] and N.notes_for(N.load_notes(), "exclusions")[-1]["text"] == "A new exclusion."
    win.delete_note(new["id"])
    assert all(n["id"] != new["id"] for n in N.load_notes()["notes"]) and new["id"] not in win.ticks["exclusions"]


class _Stop(Exception):
    pass


def _generate_to_the_checks(w):
    """Press Generate in PDMS mode up to the warning dialog; the answer is Cancel, so nothing is written."""
    w._pdms_check.setChecked(True)
    w._pdms_tpl_edit.setText(str(H.INPUTS / "PDMS_TEMPLATE_short.docx"))
    w._out_dir_edit.setText(str(H.SANDBOX / "out" / "notes_ui"))
    w._job_ref_edit.setText("999")
    w._prp_edit.setText("PRP-26-888")
    H.resolve_disparities(w)
    real = QMessageBox.warning
    QMessageBox.warning = staticmethod(lambda p, t, text, *a, **k: (H.msgs.append(("warning", t, text)),
                                                                  QMessageBox.StandardButton.Cancel)[1])
    H.msgs.clear()
    try:
        w._generate()
    finally:
        QMessageBox.warning = real
        w._pdms_check.setChecked(False)
    return H.all_text(H.msgs)


def test_generate_reminds_about_unseen_notes_but_does_not_lock_out_and_the_setting_can_turn_it_off():
    w = _gen()
    text = _generate_to_the_checks(w)
    assert "notes of the proposal have not been looked at" in text and "Generate anyway?" in text     # a Continue / Cancel
    w._open_notes_window()
    w._notes_win.close()
    assert "have not been looked at" not in _generate_to_the_checks(w)                                 # opened: no reminder
    w._reset_notes_for_task()
    w._settings().setValue(w.KEY_WARN_UNSEEN_NOTES, False)
    try:
        assert not w._warn_unseen_notes() and "have not been looked at" not in _generate_to_the_checks(w)
    finally:
        w._settings().remove(w.KEY_WARN_UNSEEN_NOTES)


def test_the_settings_switch_follows_and_changes_the_setting():
    from common import settings_view as S
    w = _gen()
    tab = S.SettingsTab(w)
    tab.refresh()
    assert tab._warn_notes_chk.isChecked()
    tab._warn_notes_chk.setChecked(False)
    try:
        assert not w._warn_unseen_notes()
    finally:
        w._settings().remove(w.KEY_WARN_UNSEEN_NOTES)


def _generate_for_real(w, tpl, name):
    """Generate the PDMS proposal through the window (warnings answered Yes by the harness); returns the docx."""
    import docx
    w._pdms_check.setChecked(True)
    out = H.SANDBOX / "out" / name
    out.mkdir(parents=True, exist_ok=True)
    w._pdms_tpl_edit.setText(str(tpl))
    w._out_dir_edit.setText(str(out))
    w._job_ref_edit.setText("999")
    w._prp_edit.setText("PRP-26-" + name[-3:])
    H.resolve_disparities(w)
    H.msgs.clear()
    w._generate()
    assert H.wait(lambda: any(m[0] in ("information", "critical") for m in H.msgs), 120)
    assert not any(m[0] == "critical" for m in H.msgs), H.msgs
    docs = list(out.glob("*.docx"))
    assert len(docs) == 1, list(out.iterdir())
    w._pdms_check.setChecked(False)
    return docx.Document(str(docs[0])), H.all_text(H.msgs)


def test_an_edited_and_a_new_note_reach_the_generated_proposal_and_an_old_template_says_it_ignores_them():
    v2, old = H.INPUTS / "PDMS_TEMPLATE_short_v2.docx", H.INPUTS / "PDMS_TEMPLATE_short.docx"
    if not v2.is_file() or not old.is_file():
        raise H.Skip("the PDMS templates are not in TEST_Files/CTR creator")
    w = _gen()
    win = w._ensure_notes_window()
    note = next(n for n in win.data["notes"] if n["id"] == "excl-lunch")
    ed = NoteEditor(win, win.data, note)
    ed.text.setPlainText("Lunch for all personnel is provided by {provider}.")          # an edit, with a placeholder of her own
    win.after_edit(ed.apply())
    win.set_value("provider", "the Client")
    ed2 = NoteEditor(win, win.data, None)
    ed2.text.setPlainText("A new exclusion about the crane.")
    ed2._place_boxes["exclusions"][0].setChecked(True)
    win.after_edit(ed2.apply())
    w._open_notes_window()
    w._notes_win.close()
    d, text = _generate_for_real(w, v2, "notes_e2e_v2_001")
    texts = [p.text for p in d.paragraphs]
    assert "Lunch for all personnel is provided by the Client." in texts            # the edit, with its value
    assert "A new exclusion about the crane." in texts                              # the new note
    assert "Lunch for labor and fuel for equipment will be provided by Client." not in texts
    assert "have not been looked at" not in text and "no note tags" not in text
    d2, text2 = _generate_for_real(w, old, "notes_e2e_old_002")                     # the old template: told, not silently ignored
    assert "has no note tags" in text2 and "A new exclusion about the crane." not in [p.text for p in d2.paragraphs]
    w._pdms_tpl_edit.setText(str(old))
    assert w._template_has_note_tags() is False
    w._open_notes_window()
    assert not w._notes_win.template_note.isHidden()
    w._notes_win.close()


def test_a_hint_is_shown_in_the_list_not_written_and_the_optional_notes_are_not_ticked():
    w = _gen()
    win = w._ensure_notes_window()
    shown = [win.tree.topLevelItem(2).child(j).text(0) for j in range(win.tree.topLevelItem(2).childCount())]
    assert any(t.endswith("[ONLY AZN]") for t in shown) and any(t.endswith("[ONLY REIMBURSABLE PROPOSAL]") for t in shown)
    assert win.counts()[0] == 15 and not win.ticks["price_basis"] & {"opt-prices-azn", "opt-advance"}
    win.search.setText("only usd")
    kept = [win.tree.topLevelItem(i).child(j).text(0) for i in range(3) for j in range(win.tree.topLevelItem(i).childCount())]
    assert len(kept) == 3 and all("[ONLY USD]" in t for t in kept)                       # the same note in the three places
    win.search.setText("")
    win.tick("price_basis", "opt-prices-azn", True)                                       # the user ticks one
    p = win.payload()["items"]["price_basis"]
    assert any(x["text"].startswith("All prices are in AZN") and "[" not in x["text"] for x in p)
    ed = NoteEditor(win, win.data, next(n for n in win.data["notes"] if n["id"] == "opt-advance"))
    ed.hint.setText("ONLY LUMPSUM")
    win.after_edit(ed.apply())
    assert next(n for n in N.load_notes()["notes"] if n["id"] == "opt-advance")["hint"] == "ONLY LUMPSUM"


def test_new_placeholders_and_prepared_values_through_the_dialogs():
    from ctr_tools.pdms_notes_view import NewPlaceholderDialog, PlaceholderManager
    w = _gen()
    win = w._ensure_notes_window()
    # a new placeholder from the window
    dlg = NewPlaceholderDialog(win, win.data)
    dlg.name_edit.setText("crane_days")
    dlg.label_edit.setText("Crane days")
    dlg.default_edit.setText("3")
    dlg._accept()
    assert dlg.name == "crane_days"
    win.after_placeholder_change()
    assert "crane_days" in N.load_notes()["placeholders"]
    bad = NewPlaceholderDialog(win, win.data)
    real = QMessageBox.warning
    seen = []
    QMessageBox.warning = staticmethod(lambda p, t, text, *a, **k: seen.append(text))
    try:
        bad.name_edit.setText("hours")                                                 # one the app knows
        bad._accept()
    finally:
        QMessageBox.warning = real
    assert bad.name == "" and seen and "exists already" in seen[0]
    # it is offered in the note editor and put into a note, which then gets its box with the starting value
    ed = NoteEditor(win, win.data, None)
    assert any(ed.ph_combo.itemData(i) == "crane_days" for i in range(ed.ph_combo.count()))
    ed.text.setPlainText("A crane is on site for ")
    ed.add_placeholder_choice("crane_days")                                            # (also what its New placeholder… button does)
    ed.text.insertPlainText(" days.")
    ed._place_boxes["exclusions"][0].setChecked(True)
    win.after_edit(ed.apply())
    assert "crane_days" in win._boxes and win.payload()["values"]["crane_days"] == "3"
    # prepared values, in the manager
    chg = []
    mgr = PlaceholderManager(win, win.data, lambda: (chg.append(1), win.after_placeholder_change()), "working_days")
    assert mgr.current_name() == "working_days" and not mgr.del_ph.isEnabled()          # a known one cannot be deleted
    mgr.add_edit.setText("Monday to Saturday")
    mgr._add_value()
    assert "Monday to Saturday" in N.load_notes()["values"]["working_days"] and chg
    mgr.values.setCurrentRow(mgr.values.count() - 1)
    mgr._make_default()
    assert N.load_notes()["placeholders"]["working_days"]["default"] == "Monday to Saturday"
    mgr.values.selectAll()
    mgr._delete_values()                                                               # many at once
    assert "working_days" not in N.load_notes()["values"]
    mgr.names.setCurrentRow(next(i for i in range(mgr.names.count()) if mgr.names.item(i).data(W.Qt.UserRole) == "crane_days"))
    assert mgr.del_ph.isEnabled()
    mgr.delete_placeholder("crane_days")
    assert "crane_days" not in N.load_notes()["placeholders"]
