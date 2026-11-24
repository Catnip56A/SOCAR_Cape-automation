"""Short / long Word proposal: the selector next to the Word proposal box, the two template pickers (each needed only for its
own kind), the remembered choice and paths. Sandboxed settings; nothing is generated."""
import harness as H
from PySide6.QtWidgets import QMessageBox


def _blocked(w) -> str:
    """Press Generate in Word proposal mode and return the text of the 'Cannot generate' dialog (nothing is written)."""
    real = QMessageBox.warning
    QMessageBox.warning = staticmethod(lambda p, t, text, *a, **k: (H.msgs.append(("warning", t, text)),
                                                                  QMessageBox.StandardButton.Cancel)[1])
    H.msgs.clear()
    try:
        w._generate()
    finally:
        QMessageBox.warning = real
    return H.all_text(H.msgs)


def test_the_selector_picks_the_template_of_its_kind_and_only_that_one_is_needed():
    w = H.generator()
    w._word_proposal_check.setChecked(True)
    try:
        w._out_dir_edit.setText(str(H.SANDBOX / "out" / "kind"))
        w._job_ref_edit.setText("999")
        w._prp_edit.setText("PRP-26-777")
        w._word_proposal_tpl_edit.setText(str(H.INPUTS / "Word_Proposal_Template_short.docx"))
        w._word_proposal_tpl_long_edit.setText("")
        assert w._word_proposal_kind() == "short" and w._word_proposal_template_path().endswith("Word_Proposal_Template_short.docx")
        assert "Word Proposal (short)" not in _blocked(w) and "Word Proposal (long)" not in _blocked(w)
        w._word_proposal_kind_combo.setCurrentIndex(1)                                   # long: its picker is empty
        assert w._word_proposal_kind() == "long" and w._word_proposal_template_path() == ""
        assert "Word Proposal (long) not selected" in _blocked(w)
        w._word_proposal_tpl_long_edit.setText(str(H.SANDBOX / "nowhere" / "long.docx"))
        assert "Word Proposal (long) not found" in _blocked(w)
    finally:
        w._word_proposal_kind_combo.setCurrentIndex(0)
        w._word_proposal_check.setChecked(False)


def test_the_selector_and_both_pickers_are_only_enabled_in_word_proposal_mode():
    w = H.generator()
    w._word_proposal_check.setChecked(False)
    assert not w._word_proposal_kind_combo.isEnabled() and not w._word_proposal_tpl_edit.isEnabled() and not w._word_proposal_tpl_long_edit.isEnabled()
    assert w._word_proposal_kind_combo.isHidden()                       # the Short / Long selector is only there in Word proposal mode
    w._word_proposal_check.setChecked(True)
    try:
        assert not w._word_proposal_kind_combo.isHidden()
        assert w._word_proposal_kind_combo.isEnabled() and w._word_proposal_tpl_edit.isEnabled() and w._word_proposal_tpl_long_edit.isEnabled()
    finally:
        w._word_proposal_check.setChecked(False)


def test_the_kind_and_both_paths_are_remembered():
    w = H.generator()
    short, long_ = H.INPUTS / "Word_Proposal_Template_short.docx", H.INPUTS / "Word_Proposal_Template_short_v2.docx"
    if not (short.is_file() and long_.is_file()):
        raise H.Skip("the Word proposal templates are not in TEST_Files/CTR creator")
    w._word_proposal_tpl_edit.setText(str(short))
    w._word_proposal_tpl_long_edit.setText(str(long_))
    w._word_proposal_kind_combo.setCurrentIndex(1)
    w.save_settings()
    w._word_proposal_tpl_edit.setText("")
    w._word_proposal_tpl_long_edit.setText("")
    w._word_proposal_kind_combo.setCurrentIndex(0)
    w.restore_settings()
    try:
        assert w._word_proposal_tpl_edit.text() == str(short) and w._word_proposal_tpl_long_edit.text() == str(long_)
        assert w._word_proposal_kind() == "long"
    finally:
        w._word_proposal_kind_combo.setCurrentIndex(0)
        w._word_proposal_tpl_edit.setText("")
        w._word_proposal_tpl_long_edit.setText("")
        w.save_settings()


def test_generating_with_long_selected_uses_the_long_template_and_short_the_short_one():
    import docx
    long_, short = H.INPUTS / "Word_Proposal_Template_long.docx", H.INPUTS / "Word_Proposal_Template_short_v2.docx"
    if not (long_.is_file() and short.is_file()):
        raise H.Skip("the Word proposal templates are not in TEST_Files/CTR creator")
    from ctr_tools import word_proposal_notes as N
    N._NOTES_PATH = H.SANDBOX / "kind_e2e_notes.json"
    N._NOTES_PATH.unlink(missing_ok=True)
    w = H.generator()
    w._notes_seen = True                                              # the reminder is not what this test is about
    results = {}
    for kind, idx in (("short", 0), ("long", 1)):
        out = H.SANDBOX / "out" / f"kind_{kind}"
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*"):
            old.unlink()
        w._word_proposal_check.setChecked(True)
        w._word_proposal_tpl_edit.setText(str(short))
        w._word_proposal_tpl_long_edit.setText(str(long_))
        w._word_proposal_kind_combo.setCurrentIndex(idx)
        w._ensure_notes_window().choose_schedule("offshore-7-12")
        w._out_dir_edit.setText(str(out))
        w._job_ref_edit.setText("999")
        w._prp_edit.setText("PRP-26-123")
        H.resolve_disparities(w)
        H.msgs.clear()
        w._generate()
        assert H.wait(lambda: any(m[0] in ("information", "critical") for m in H.msgs), 120)
        assert not any(m[0] == "critical" for m in H.msgs), H.msgs
        docs = list(out.glob("*.docx"))
        assert len(docs) == 1, list(out.iterdir())
        results[kind] = docx.Document(str(docs[0]))
        w._word_proposal_check.setChecked(False)
        w._word_proposal_kind_combo.setCurrentIndex(0)
    short_text = "\n".join(p.text for p in results["short"].paragraphs)
    long_text = "\n".join(p.text for p in results["long"].paragraphs)
    assert "Table № 1" in short_text and "Table A. Personnel" not in short_text            # the short template
    assert "Table A. Personnel" in long_text and "Table № 1" not in long_text               # the long template
