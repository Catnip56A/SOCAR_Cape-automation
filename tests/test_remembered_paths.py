"""The remembered file boxes (pricebooks, SAGE, Combined DB, templates, output folder): a path whose file is not on this
PC today is not shown but stays remembered; a box the user fills in or changes is saved; the paths are saved as soon as
Load Files has run, not only at close. Sandbox QSettings only (see harness)."""
import harness as H


def _fresh():
    w = H._new_generator()
    for e in w._path_edits().values():
        e.setText("")
    return w


def test_a_missing_file_is_not_shown_but_is_not_forgotten_either():
    w = H.generator()
    s = w._settings()
    gone = "/no/such/drive/AZN Pricebook.xlsx"
    here = str(H.INPUTS / "4410030190 - 12.02.2026.xlsx")
    s.setValue("paths/azn_pricebook", gone)
    s.setValue("paths/usd_pricebook", here)
    try:
        fresh = _fresh()
        fresh.restore_settings()
        assert fresh._azn_pb_edit.text() == "" and fresh._usd_pb_edit.text() == here
        fresh.save_settings()                                          # a normal close with the box still empty
        assert str(s.value("paths/azn_pricebook")) == gone             # not wiped
        again = _fresh()
        again.restore_settings()
        again._azn_pb_edit.setText(here)                               # the user picks another file: that is saved
        again.save_settings()
        assert str(s.value("paths/azn_pricebook")) == here
        third = _fresh()
        third.restore_settings()
        third._usd_pb_edit.setText("")                                 # a box that was shown and then cleared is saved empty
        third.save_settings()
        assert str(s.value("paths/usd_pricebook")) == ""
    finally:
        for k in ("azn_pricebook", "usd_pricebook"):
            s.remove(f"paths/{k}")


def test_the_paths_are_saved_when_load_files_has_run_not_only_at_close():
    w = H._new_generator()                                            # a fresh window: Load Files is pressed once
    s = w._settings()
    for k in ("azn_pricebook", "usd_pricebook", "sage_export", "combined_db"):
        s.remove(f"paths/{k}")
    w._load_files()
    assert H.wait(lambda: w._pending_loads == 0), "reference files did not load"
    assert str(s.value("paths/azn_pricebook")) == w._azn_pb_edit.text() != ""
    assert str(s.value("paths/sage_export")) == w._sage_edit.text() != ""
    assert str(s.value("paths/usd_pricebook")) == w._usd_pb_edit.text() != ""
    assert str(s.value("paths/combined_db")) == w._combined_db_edit.text() != ""
