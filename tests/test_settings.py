"""Settings window: reset with backups, keeping the newest few, restore, an unwritable folder."""
import json
import os
import stat

import ctr_tools.presets as P
import harness as H
from common import settings_view as S
class _Gen:
    """The few Generator methods the Settings window calls, over the sandboxed files."""
    sage_path_changed = type("Sig", (), {"connect": staticmethod(lambda f: None)})()

    def sage_path(self): return ""
    def output_dir(self): return ""
    def stats_save_dir(self): return ""
    def saved_data_counts(self):
        d = json.loads(P._PRESETS_PATH.read_text()) if P._PRESETS_PATH.exists() else {}
        return {"aliases": 0, "renames": 0, "presets": len(d)}
    def reload_saved_data(self): self.reloaded = getattr(self, "reloaded", 0) + 1
    def open_alias_manager(self, category=None): pass


def _clean():
    for p in P._PRESETS_PATH.parent.glob("ctr_presets*"):
        p.unlink()


def _write(n_entries=1, tag="a"):
    P._PRESETS_PATH.write_text(json.dumps({f"{tag}{i}": {"client": "c"} for i in range(n_entries)}))


def test_reset_presets_empties_the_list_and_keeps_a_backup():
    _clean()
    _write(2)
    tab = S.SettingsTab(_Gen())
    tab._reset_presets()
    assert not P._PRESETS_PATH.exists()
    baks = S.backups_for(P._PRESETS_PATH)
    assert len(baks) == 1 and S.read_entries(baks[0]) == 2


def test_only_the_newest_backups_are_kept():
    _clean()
    for i in range(S.KEEP_BACKUPS + 3):
        _write(1, tag=f"n{i}")
        S.copy_backup(P._PRESETS_PATH)
    baks = S.backups_for(P._PRESETS_PATH)
    assert len(baks) == S.KEEP_BACKUPS
    assert baks == sorted(baks, reverse=True)


def test_restore_brings_a_backup_back_and_saves_the_current_list_first():
    _clean()
    _write(3, tag="old")
    old = S.copy_backup(P._PRESETS_PATH)
    _write(1, tag="now")
    tab = S.SettingsTab(_Gen())

    class _Pick:                                          # stands in for the list window: pick `old`
        def __init__(self, *a): pass
        def exec(self): return S.QDialog.DialogCode.Accepted
        def chosen(self): return old
    real = S._RestoreDialog
    S._RestoreDialog = _Pick
    try:
        tab._restore("CTR presets", P._PRESETS_PATH)
    finally:
        S._RestoreDialog = real
    assert S.read_entries(P._PRESETS_PATH) == 3
    assert any(S.read_entries(b) == 1 for b in S.backups_for(P._PRESETS_PATH)), "current list was not saved first"
    assert tab._gen.reloaded >= 1


def test_the_restore_list_shows_dates_and_entry_counts():
    _clean()
    _write(2)
    b = S.copy_backup(P._PRESETS_PATH)
    dlg = S._RestoreDialog(None, "CTR presets", [b])
    assert "2 entries" in dlg._list.item(0).text()
    assert dlg.chosen() == b
    b.write_text("not json")
    dlg2 = S._RestoreDialog(None, "CTR presets", [b])
    assert "unreadable" in dlg2._list.item(0).text() and not dlg2._ok.isEnabled()


def test_an_unwritable_folder_is_refused_without_changing_anything():
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise H.Skip("running as root: permissions are not enforced")
    _clean()
    _write(2)
    folder = P._PRESETS_PATH.parent
    folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        H.msgs.clear()
        S.SettingsTab(_Gen())._reset_presets()
        assert P._PRESETS_PATH.exists(), "the list must be untouched"
        assert any("cannot be written" in str(m[-1]) for m in H.msgs), H.msgs
    finally:
        folder.chmod(stat.S_IRWXU)


def test_entry_counts():
    assert S.count_entries({"manpower": {"a": "b", "c": "d"}, "equipment": {}, "consumable": {"x": "y"}}) == 3
    assert S.count_entries({"preset one": {"client": "c"}, "preset two": {}}) == 2
    assert S.count_entries([]) == 0
