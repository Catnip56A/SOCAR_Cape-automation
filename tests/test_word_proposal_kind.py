"""Short / long Word proposal and the task type: the Word Proposal folder (a template is found by its name), the mapping
that wins over the name, the mandatory task-type box, the remembered choices. Sandboxed settings; Generate is only
pressed up to its error dialog, except in the last test."""
import json
from pathlib import Path

import harness as H
from PySide6.QtWidgets import QMessageBox

from ctr_tools import word_proposal_templates as WPT


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


def _folder(tag: str, names: list[str]) -> Path:
    """A folder of empty stand-in template files (only their names matter for the discovery)."""
    d = H.SANDBOX / f"wpt_{tag}"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"")
    return d


def _reset(w):
    w._word_proposal_map = {}
    w._word_proposal_folder_edit.setText("")
    w._word_proposal_kind_combo.setCurrentIndex(0)
    w._word_proposal_check.setChecked(False)


# ── the naming rule (no window) ──

def test_a_template_is_found_by_its_name_and_the_higher_version_wins():
    assert WPT.parse_name("Word_Proposal_Template_long.docx") == ("long", "General", 0)
    assert WPT.parse_name("Word_Proposal_Template_long_Scaffolding.docx") == ("long", "Scaffolding", 0)
    assert WPT.parse_name("Word_Proposal_Template_short_v2.docx") == ("short", "General", 2)
    assert WPT.parse_name("Word_Proposal_Template_long_Industrial_Cleaning.docx") == ("long", "Industrial Cleaning", 0)
    assert WPT.parse_name("Word_Proposal_Template_short_v3_Rope_Access.docx") == ("short", "Rope Access", 3)
    assert WPT.parse_name("Proposal.docx") is None and WPT.parse_name("Word_Proposal_Template_medium.docx") is None
    d = _folder("rule", ["Word_Proposal_Template_short.docx", "Word_Proposal_Template_short_v2.docx",
                         "Word_Proposal_Template_long_Insulation.docx", "notes.txt"])
    found = WPT.discover(d)
    assert found[("short", "General")].name == "Word_Proposal_Template_short_v2.docx"
    assert set(found) == {("short", "General"), ("long", "Insulation")}
    assert WPT.discover(H.SANDBOX / "no_such_folder") == {} and WPT.discover("") == {}


def test_a_mapping_wins_over_the_name_and_a_bad_saved_mapping_is_ignored():
    found = {("long", "Insulation"): Path("/f/Word_Proposal_Template_long_Insulation.docx")}
    mapping = {WPT.map_key("long", "Insulation"): "/other/renamed.docx", WPT.map_key("long", "Scaffolding"): "/other/s.docx"}
    assert WPT.resolve("long", "Insulation", found, mapping) == Path("/other/renamed.docx")
    assert WPT.resolve("long", "Scaffolding", found, mapping) == Path("/other/s.docx")
    assert WPT.resolve("short", "Insulation", found, mapping) is None
    assert WPT.types_for("long", found, mapping) == ["Insulation", "Scaffolding"]
    assert WPT.source_of("long", "Insulation", found, mapping) == "mapped" and WPT.source_of("long", "X", found, {}) == ""
    assert WPT.load_map("not json") == {} and WPT.load_map("[1]") == {} and WPT.load_map(json.dumps({"x": "y", "long|A": "/a"})) == {"long|A": "/a"}
    assert WPT.load_map(WPT.dump_map(mapping)) == mapping


# ── the window ──

def test_the_task_type_is_mandatory_and_the_list_follows_the_folder_and_the_kind():
    w = H.generator()
    d = _folder("window", ["Word_Proposal_Template_short_v2.docx", "Word_Proposal_Template_long_Insulation.docx",
                           "Word_Proposal_Template_long_Rope_Access.docx"])
    w._word_proposal_check.setChecked(True)
    try:
        w._out_dir_edit.setText(str(H.SANDBOX / "out" / "kind"))
        w._job_ref_edit.setText("999")
        w._prp_edit.setText("PRP-26-777")
        w._word_proposal_folder_edit.setText(str(d))
        combo = w._word_proposal_type_combo
        assert [combo.itemText(i) for i in range(combo.count())] == ["Choose task type…", "General"]       # short: General only
        assert w._word_proposal_type() == "" and w._word_proposal_template_path() == ""
        assert "Choose the task type" in _blocked(w)                                                       # mandatory
        w._word_proposal_kind_combo.setCurrentIndex(1)                                                    # long
        assert [combo.itemText(i) for i in range(combo.count())] == ["Choose task type…", "Insulation", "Rope Access"]
        combo.setCurrentIndex(combo.findData("Insulation"))
        assert w._word_proposal_template_path().endswith("Word_Proposal_Template_long_Insulation.docx")
        w._word_proposal_kind_combo.setCurrentIndex(0)                                                    # short has no Insulation
        assert w._word_proposal_type() == "" and "Choose the task type" in _blocked(w)
    finally:
        _reset(w)


def test_a_missing_template_is_named_in_the_error_and_a_mapped_file_is_used():
    w = H.generator()
    w._word_proposal_check.setChecked(True)
    try:
        w._out_dir_edit.setText(str(H.SANDBOX / "out" / "kind2"))
        w._job_ref_edit.setText("999")
        w._prp_edit.setText("PRP-26-777")
        w._word_proposal_map = {WPT.map_key("long", "Insulation"): str(H.SANDBOX / "nowhere" / "x.docx")}
        w._word_proposal_kind_combo.setCurrentIndex(1)
        w._refresh_word_proposal_types("Insulation")
        assert w._word_proposal_template_path().endswith("x.docx")
        assert "Word proposal template (long / Insulation) not found" in _blocked(w)
        w._word_proposal_map = {}
        w._word_proposal_use_template("long", H.SANDBOX / "nowhere" / "y.docx", "Rope Access")
        w._word_proposal_map.pop(WPT.map_key("long", "Rope Access"))                                      # a type with no file left
        w._refresh_word_proposal_types("Rope Access")
        assert w._word_proposal_type() == ""                                                              # gone from the list
    finally:
        _reset(w)


def test_the_folder_the_mapping_the_kind_and_the_task_type_are_remembered():
    w = H.generator()
    d = _folder("remember", ["Word_Proposal_Template_long_Insulation.docx"])
    w._word_proposal_folder_edit.setText(str(d))
    w._word_proposal_map = {WPT.map_key("long", "Scaffolding"): str(H.SANDBOX / "s.docx")}
    w._word_proposal_kind_combo.setCurrentIndex(1)
    w._refresh_word_proposal_types("Insulation")
    w.save_settings()
    w._word_proposal_folder_edit.setText("")
    w._word_proposal_map = {}
    w._word_proposal_kind_combo.setCurrentIndex(0)
    w.restore_settings()
    try:
        assert w._word_proposal_folder_edit.text() == str(d)
        assert w._word_proposal_map == {WPT.map_key("long", "Scaffolding"): str(H.SANDBOX / "s.docx")}
        assert w._word_proposal_kind() == "long" and w._word_proposal_type() == "Insulation"
        combo = w._word_proposal_type_combo
        assert [combo.itemText(i) for i in range(combo.count())] == ["Choose task type…", "Insulation", "Scaffolding"]
    finally:
        _reset(w)
        w.save_settings()


def test_the_folder_row_the_kind_and_the_type_box_are_only_there_in_word_proposal_mode():
    w = H.generator()
    w._word_proposal_check.setChecked(False)
    assert not w._word_proposal_kind_combo.isEnabled() and not w._word_proposal_folder_edit.isEnabled()
    assert not w._word_proposal_type_combo.isEnabled() and not w._word_proposal_manage_btn.isEnabled()
    assert w._word_proposal_kind_combo.isHidden() and w._word_proposal_type_combo.isHidden()
    w._word_proposal_check.setChecked(True)
    try:
        assert not w._word_proposal_kind_combo.isHidden() and not w._word_proposal_type_combo.isHidden()
        assert w._word_proposal_kind_combo.isEnabled() and w._word_proposal_folder_edit.isEnabled()
        assert w._word_proposal_type_combo.isEnabled() and w._word_proposal_manage_btn.isEnabled()
    finally:
        w._word_proposal_check.setChecked(False)


def test_the_templates_window_maps_unmaps_adds_and_removes_rows():
    from ctr_tools.word_proposal_templates_view import WordProposalTemplatesDialog
    d = _folder("dialog", ["Word_Proposal_Template_long_Insulation.docx", "Word_Proposal_Template_short_v2.docx"])
    dlg = WordProposalTemplatesDialog(str(d), {WPT.map_key("long", "Extra"): str(H.SANDBOX / "extra.docx")})
    rows = [(dlg._tbl.item(r, 0).text(), dlg._tbl.item(r, 1).text(), dlg._tbl.item(r, 3).text()) for r in range(dlg._tbl.rowCount())]
    assert rows == [("Short", "General", "by name"), ("Long", "Extra", "missing file"), ("Long", "Insulation", "by name")]
    dlg._tbl.selectRow(2)                                                    # Long / Insulation, found by name
    assert dlg._selected() == ("long", "Insulation")
    assert dlg._btn_map.isEnabled() and not dlg._btn_name.isEnabled() and not dlg._btn_remove.isEnabled()
    dlg._map[WPT.map_key("long", "Insulation")] = str(H.SANDBOX / "renamed.docx")        # what Map to a file… stores
    dlg._fill(("long", "Insulation"))
    assert dlg._btn_name.isEnabled() and not dlg._btn_remove.isEnabled()                 # found by name too: cannot be removed
    dlg._unmap_selected()
    assert WPT.map_key("long", "Insulation") not in dlg.mapping()
    dlg._tbl.selectRow(1)                                                    # Long / Extra exists only because of its mapping
    assert dlg._btn_remove.isEnabled()
    dlg._remove_selected()
    assert dlg.mapping() == {} and dlg._tbl.rowCount() == 2


def test_generating_long_for_a_task_type_and_short_uses_the_template_of_that_kind_and_type():
    import docx
    long_, short = H.INPUTS / "Word_Proposal_Template_long_Scaffolding.docx", H.INPUTS / "Word_Proposal_Template_short_v2.docx"
    typed = H.INPUTS / "Word_Proposal_Template_long_Insulation.docx"
    if not (long_.is_file() and short.is_file() and typed.is_file()):
        raise H.Skip("the Word proposal templates are not in TEST_Files/CTR creator")
    from ctr_tools import word_proposal_notes as N
    N._NOTES_PATH = H.SANDBOX / "kind_e2e_notes.json"
    N._NOTES_PATH.unlink(missing_ok=True)
    folder = H.SANDBOX / "wpt_e2e"                                          # a folder of links to the real templates
    folder.mkdir(exist_ok=True)
    for f in (long_, short, typed):
        link = folder / f.name
        if not link.exists():
            link.symlink_to(f)
    w = H.generator()
    w._notes_seen = True                                              # the reminder is not what this test is about
    results = {}
    for kind, task in (("short", "General"), ("long", "Scaffolding"), ("long", "Insulation")):
        out = H.SANDBOX / "out" / f"kind_{kind}_{task}"
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*"):
            old.unlink()
        w._word_proposal_check.setChecked(True)
        w._word_proposal_folder_edit.setText(str(folder))
        w._word_proposal_kind_combo.setCurrentIndex(1 if kind == "long" else 0)
        w._refresh_word_proposal_types(task)
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
        results[(kind, task)] = docx.Document(str(docs[0]))
        w._word_proposal_check.setChecked(False)
    _reset(w)
    text = {k: "\n".join(p.text for p in d.paragraphs) for k, d in results.items()}
    assert "Table № 1" in text[("short", "General")] and "Table A. Personnel" not in text[("short", "General")]
    assert "Table A. Personnel" in text[("long", "Scaffolding")] and "Table № 1" not in text[("long", "Scaffolding")]
    assert "Table A. Personnel" in text[("long", "Insulation")]
    assert "Third-Party services" not in text[("long", "Insulation")]                       # the Insulation template has no such table
