"""Files edited on disk after they were read: the parsed SAGE / pricebooks are re-read instead of reused, Load Files
refreshes the MR vs CTR statistics, and Compare warns about MR / CTR files that changed after they were added.
Throwaway files in the test sandbox; the test files are only read."""
import os
import time

import openpyxl
import pandas as pd

import harness as H
from ctr_tools import window as W


def _sage_file(name, cost):
    path = H.SANDBOX / name
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "FROM SAGE"
    ws.append(["product", "local_expect_cost", "long_description", "analysis_b", "analysis_c", "analysis_a",
               "warehouse", "order_no", "name", "unit_code"])
    ws.append(["PROBE1", cost, "Probe item", "RECHARGE", "RECHARGE", None, "W1", "O1", "S", "EA"])
    wb.save(path)
    return path


def _touch_later(path, seconds=5):
    t = time.time() + seconds
    os.utime(path, (t, t))                                  # a different modified time even on a coarse clock


def test_a_files_fingerprint_changes_when_it_is_edited_and_is_none_when_it_is_missing():
    f = H.SANDBOX / "stamp.txt"
    f.write_text("a")
    first = W.file_stamp(f)
    f.write_text("a longer text")
    assert W.file_stamp(f) != first
    assert W.file_stamp(H.SANDBOX / "nothing.txt") is None and W.file_stamp("") is None


def test_the_parsed_tables_are_reused_only_while_their_file_is_unchanged():
    w = H._new_generator()
    sage = _sage_file("reuse_sage.xlsx", 10)
    usd, names = H.SANDBOX / "reuse_usd.xlsx", H.SANDBOX / "reuse_names.xlsx"
    usd.write_text("x")
    names.write_text("x")
    df = pd.DataFrame({"product": ["PROBE1"], "local_expect_cost": [10.0]})
    w._sage_df, w._sage_df_path, w._sage_df_stamp = df, str(sage), W.file_stamp(sage)
    w._usd_pb_df, w._usd_pb_df_path, w._usd_pb_df_stamp = df, str(usd), W.file_stamp(usd)
    w._names_db_df, w._names_db_df_path, w._names_db_df_stamp = df, str(names), W.file_stamp(names)
    assert w.loaded_sage_df(str(sage)) is df and w.loaded_usd_df(str(usd)) is df and w.loaded_names_df(str(names)) is df
    _sage_file("reuse_sage.xlsx", 500)                        # SAGE edited on disk
    usd.write_text("changed")                                 # USD Pricebook edited
    _touch_later(names)                                       # Combined DB saved again
    assert w.loaded_sage_df(str(sage)) is None                # -> not reused: the next job re-reads the file
    assert w.loaded_usd_df(str(usd)) is None and w.loaded_names_df(str(names)) is None


def test_load_files_renews_the_tables_and_reuse_follows_the_new_file():
    w = H._new_generator()
    sage = _sage_file("renew_sage.xlsx", 10)
    w._sage_edit.setText(str(sage))
    loaded = []
    w.files_loaded.connect(lambda: loaded.append(1))
    w._load_files()
    assert H.wait(lambda: w._pending_loads == 0, 120)
    assert loaded == [1]                                      # the app is told when the reference tables were re-read
    assert w._sage_df.local_expect_cost.iloc[0] == 10 and w.loaded_sage_df(str(sage)) is w._sage_df
    _sage_file("renew_sage.xlsx", 99)
    _touch_later(sage)
    assert w.loaded_sage_df(str(sage)) is None                # stale: not handed out
    w._load_files()                                           # Load Files again: re-read from disk
    assert H.wait(lambda: w._pending_loads == 0, 120)
    assert w._sage_df.local_expect_cost.iloc[0] == 99 and w.loaded_sage_df(str(sage)) is w._sage_df
    assert loaded == [1, 1]


# ── the MR vs CTR window ─────────────────────────────────────────────────────

def _window_with_files():
    import app as A
    win = A.MainWindow()
    win.resize(1300, 800)
    win.show()
    H.pump(10)
    mr_file, ctr_file = H.SANDBOX / "mr_a.xlsx", H.SANDBOX / "ctr_a.xlsx"
    mr_file.write_text("mr")
    ctr_file.write_text("ctr")
    mr = pd.DataFrame({"Stock Code": ["A1"], "Qty": [1], "Unit": "EA", "Description": "x",
                       "_SourceFile": "mr_a.xlsx", "_SourceSheet": "S"})
    ctr = pd.DataFrame({"Stock Code": ["A1"], "Quantity": [1], "Unit": "EA", "Description": "x", "Rate": "1",
                        "_SourceFile": "ctr_a.xlsx", "_SourceSheet": "S", "_Section": "Plant & Equipment"})
    win._mr_tables = [{"source_file": "mr_a.xlsx", "source_sheet": "S", "table_name": "T", "data": mr}]
    win._ctr_tables = [{"source_file": "ctr_a.xlsx", "source_sheet": "S", "table_name": "T", "data": ctr}]
    win._mr_df, win._ctr_df = mr, ctr
    win._file_origin = {("mr", "mr_a.xlsx"): (str(mr_file), W.file_stamp(mr_file)),
                        ("ctr", "ctr_a.xlsx"): (str(ctr_file), W.file_stamp(ctr_file))}
    return win, mr_file, ctr_file


def test_compare_does_not_warn_when_the_files_are_unchanged():
    win, _m, _c = _window_with_files()
    asked = []
    win.changed_files_chooser = lambda changed: asked.append(changed) or True
    win._run_compare()
    H.pump(10)
    assert asked == [] and len(win._display_df) == 1
    assert H.wait(lambda: not win._stats_workers, 60)


def test_compare_names_the_files_that_changed_and_lets_the_user_cancel():
    win, mr_file, ctr_file = _window_with_files()
    mr_file.write_text("the MR file was edited")
    asked = []

    def choose(changed):
        asked.append(changed)
        return False                                          # Cancel: re-add the file first
    win.changed_files_chooser = choose
    win._run_compare()
    H.pump(10)
    assert [(s, n) for s, n, _w in asked[0]] == [("MR", "mr_a.xlsx")]
    assert "changed" in asked[0][0][2]
    assert win._display_df.empty                              # nothing was compared
    assert "cancelled" in win.statusBar().currentMessage().lower()
    win.changed_files_chooser = lambda changed: True          # "Compare anyway"
    win._run_compare()
    H.pump(10)
    assert len(win._display_df) == 1
    assert H.wait(lambda: not win._stats_workers, 60)


def test_a_file_that_is_gone_is_reported_too():
    win, mr_file, ctr_file = _window_with_files()
    ctr_file.unlink()
    changed = win._changed_source_files()
    assert changed == [("CTR", "ctr_a.xlsx", "the file is no longer there")]


def test_files_reopened_from_a_saved_comparison_are_not_checked():
    win, mr_file, ctr_file = _window_with_files()
    win._file_origin.clear()                                   # a saved comparison has no file on disk behind it
    mr_file.write_text("edited")
    assert win._changed_source_files() == []


def test_load_files_in_the_generator_refreshes_the_statistics_of_the_current_compare():
    win, _m, _c = _window_with_files()
    win.changed_files_chooser = lambda changed: True
    win._run_compare()
    H.pump(10)
    assert H.wait(lambda: not win._stats_workers, 60)
    calls = []
    win._start_stats_job = lambda *a, **k: calls.append(1)
    win._ctr_gen.files_loaded.emit()
    assert calls == [1]                                        # the statistics are recalculated from the fresh tables
    win._stats_items = None                                    # a reopened comparison: nothing to recalculate from
    win._ctr_gen.files_loaded.emit()
    assert calls == [1]


# ── "Reload changed files": the program re-adds the file instead of ✕ then Browse ───

_DOCS = H.ROOT / "TEST_Files" / "MRvsCTR" / "CTR 190 MR 1183"
_MR_A = _DOCS / "2026.09.25-ST-1183 Drip trays cleaning at SocarCape base yard.xlsm"
_MR_B = H.ROOT / "TEST_Files" / "MRvsCTR" / "MR VS CTR 3" / "2026.07.17-EA-916 - 2026 RA FM USD      4301514203-5.xlsm"
_CTR_A = _DOCS / "190 USD AZE Drip trays cleaning at Socarcape base yard.xlsx"


def _browsed(win, side, path):
    """Adds a file the way the Browse button does (with its dialog answered), and waits for the parse."""
    import app as A
    A.QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([str(path)], ""))
    tables = win._mr_tables if side == "mr" else win._ctr_tables
    before = len(tables)
    win._browse(side)
    assert H.wait(lambda: len(tables) > before, 120)
    H.pump(20)


def _reload_window():
    import shutil
    import app as A
    win = A.MainWindow()
    win.resize(1300, 800)
    win.show()
    H.pump(10)
    copy = H.SANDBOX / f"reload_mr_{len(list(H.SANDBOX.glob('reload_mr_*')))}.xlsm"
    shutil.copy(_MR_B, copy)
    _browsed(win, "mr", copy)
    win.reload_report = lambda problems: win.__dict__.setdefault("reports", []).append(problems)
    return win, copy


def _wait_reloaded(win):
    assert H.wait(lambda: win._reload_pending == 0, 120)
    H.pump(20)


def test_the_reload_button_appears_for_a_changed_file_and_swaps_in_its_new_tables():
    import shutil
    win, copy = _reload_window()
    old_rows = sorted(len(t["data"]) for t in win._mr_tables)
    win._mark_changed_files()
    assert not win._reload_btn.isVisibleTo(win)                # nothing changed: no button
    shutil.copy(_MR_A, copy)                                   # the file was replaced by another version
    _touch_later(copy)
    win._mark_changed_files()
    assert win._reload_btn.isVisibleTo(win) and "(1)" in win._reload_btn.text()
    win._reload_changed_files()
    _wait_reloaded(win)
    assert sorted(len(t["data"]) for t in win._mr_tables) != old_rows
    assert all(t["source_file"] == copy.name for t in win._mr_tables)
    assert not win._reload_btn.isVisibleTo(win) and win._changed_source_files() == []
    assert all("no longer in the file" in line for r in getattr(win, "reports", []) for line in r)   # only a note, no problem


def test_an_unticked_sheet_stays_unticked_after_a_reload():
    import shutil
    win, copy = _reload_window()
    rows = [win._mr_table_list.itemWidget(win._mr_table_list.item(i)) for i in range(win._mr_table_list.count())]
    box = next(r._checkbox for r in rows if r._table_key is not None and r._checkbox.isChecked())
    key = next(r._table_key for r in rows if r._checkbox is box)
    box.setChecked(False)
    shutil.copy(_MR_B, copy)
    copy.write_bytes(copy.read_bytes())                        # same content, new modified time
    _touch_later(copy)
    win._reload_changed_files()
    _wait_reloaded(win)
    rows = [win._mr_table_list.itemWidget(win._mr_table_list.item(i)) for i in range(win._mr_table_list.count())]
    state = {r._table_key: r._checkbox.isChecked() for r in rows if r._table_key is not None}
    assert state[key] is False and len(state) >= 2


def test_a_deleted_file_asks_what_to_do_and_nothing_is_dropped_silently():
    win, copy = _reload_window()
    n = len(win._mr_tables)
    copy.unlink()
    win.missing_file_chooser = lambda side, name, path: "keep"
    win._reload_changed_files()
    _wait_reloaded(win)
    assert len(win._mr_tables) == n and win.reports and "gone" in win.reports[0][0]
    assert win._reload_btn.isVisibleTo(win)                    # still counted as changed
    win.missing_file_chooser = lambda side, name, path: "remove"
    win._reload_changed_files()
    _wait_reloaded(win)
    assert win._mr_tables == [] and win._mr_file_list.count() == 0 and win._changed_source_files() == []


def test_a_deleted_file_can_be_located_under_another_name():
    import shutil
    win, copy = _reload_window()
    moved = H.SANDBOX / "moved_and_renamed_mr.xlsm"
    shutil.copy(_MR_A, moved)
    copy.unlink()
    win.missing_file_chooser = lambda side, name, path: str(moved)
    win._reload_changed_files()
    _wait_reloaded(win)
    assert win._mr_tables and all(t["source_file"] == moved.name for t in win._mr_tables)
    names = [win._mr_file_list.item(i).data(0x0100) for i in range(win._mr_file_list.count())]
    assert names == [moved.name] and win._changed_source_files() == []


def test_a_file_that_cannot_be_read_keeps_its_old_tables_and_is_reported():
    win, copy = _reload_window()
    before = [t["data"] for t in win._mr_tables]
    copy.write_bytes(b"this is not a workbook")
    _touch_later(copy)
    win._reload_changed_files()
    _wait_reloaded(win)
    assert [t["data"] for t in win._mr_tables] == before or len(win._mr_tables) == len(before)
    assert all(a is b for a, b in zip((t["data"] for t in win._mr_tables), before))
    assert win.reports and copy.name in win.reports[0][0]
    assert win._reload_btn.isVisibleTo(win)                    # still the old version: still flagged


def test_compare_can_reload_the_changed_files_and_then_compare():
    import shutil
    win, copy = _reload_window()
    _browsed(win, "ctr", _CTR_A)
    assert win._changed_source_files() == []
    shutil.copy(_MR_B, copy)
    _touch_later(copy)
    seen = []
    win.changed_files_chooser = lambda changed: seen.append(changed) or "reload"
    win._run_compare()
    _wait_reloaded(win)
    assert H.wait(lambda: "Compared at" in win.statusBar().currentMessage(), 60) and len(seen) == 1
    assert win._changed_source_files() == [] and seen[0][0][:2] == ("MR", copy.name)
    assert H.wait(lambda: not win._stats_workers, 120)


def test_a_table_that_the_new_version_no_longer_has_is_named():
    import shutil
    win, copy = _reload_window()                               # CH-1 + Socar-Cape sheet
    shutil.copy(_MR_A, copy)                                   # the new version has only the Socar-Cape sheet
    _touch_later(copy)
    win._reload_changed_files()
    _wait_reloaded(win)
    assert win.reports and any("CH-1" in line and "no longer" in line for line in win.reports[0])
    assert {t["table_name"] for t in win._mr_tables} == {"Socar-Cape"}


def test_a_file_without_a_location_says_why_it_cannot_be_reloaded():
    win, mr_file, ctr_file = _window_with_files()
    win._file_origin.clear()
    win._mr_file_list.clear()
    win._add_file_row(win._mr_file_list, "mr", "mr_a.xlsx")
    win._mark_changed_files()
    label = win._mr_file_list.itemWidget(win._mr_file_list.item(0))._label
    assert "saved comparison" in label.toolTip() and not win._reload_btn.isVisibleTo(win)


def test_stopping_the_parse_during_a_reload_keeps_the_old_tables():
    import shutil
    from ctr_tools.tasks import TASKS
    import app as A
    win, copy = _reload_window()
    second = H.SANDBOX / "reload_second.xlsm"
    shutil.copy(_MR_B, second)
    _browsed(win, "mr", second)
    before = {t["source_file"]: len(t["data"]) for t in win._mr_tables}
    shutil.copy(_MR_A, copy)
    shutil.copy(_MR_A, second)
    _touch_later(copy)
    _touch_later(second)
    win._reload_changed_files()
    TASKS.stop_all()                                           # "Stop" in the progress dialog
    _wait_reloaded(win)
    for name in (copy.name, second.name):
        mine = [t for t in win._mr_tables if t["source_file"] == name]
        assert mine                                            # never left without tables
    stopped = [line for r in getattr(win, "reports", []) for line in r if "stopped" in line]
    flagged = {n for _s, n, _w in win._changed_source_files()}
    assert flagged == {line.split(": ")[1].split(" —")[0].split(";")[0] for line in stopped}


# ── the five caveats found by the challenger agent ──────────────────────────────────

def test_adding_a_file_with_a_name_that_is_already_loaded_replaces_it_instead_of_doubling():
    import shutil
    win, copy = _reload_window()                               # reload_mr_N.xlsm = _MR_B (CH-1 + cover)
    rows = sorted(len(t["data"]) for t in win._mr_tables)
    other = H.SANDBOX / "elsewhere2"
    other.mkdir(exist_ok=True)
    twin = other / copy.name                                   # same name, other folder, other content
    shutil.copy(_MR_A, twin)
    n_before = len(win._mr_tables)
    _browsed_replace(win, "mr", twin)
    assert win._mr_file_list.count() == 1                      # still one row
    assert all(t["source_file"] == copy.name for t in win._mr_tables)
    assert sorted(len(t["data"]) for t in win._mr_tables) != rows and len(win._mr_tables) != n_before + 1
    assert win._file_origin[("mr", copy.name)][0] == str(twin)  # later changes are watched on the NEW path
    _touch_later(twin)
    assert [n for _s, n, _w in win._changed_source_files()] == [copy.name]


def _browsed_replace(win, side, path):
    import app as A
    A.QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([str(path)], ""))
    win._browse(side)
    assert H.wait(lambda: win._reload_pending == 0 and not any(w.isRunning() for w in win._workers), 120)
    H.pump(30)


def test_a_failed_replace_keeps_the_old_tables_and_the_old_origin():
    import app as A
    win, copy = _reload_window()
    bad = H.SANDBOX / "elsewhere3"
    bad.mkdir(exist_ok=True)
    twin = bad / copy.name
    twin.write_bytes(b"not a workbook")
    old_origin, n = win._file_origin[("mr", copy.name)], len(win._mr_tables)
    shown = []
    A.QMessageBox.warning = staticmethod(lambda *a, **k: shown.append(a[2]))
    _browsed_replace(win, "mr", twin)
    assert len(win._mr_tables) == n and win._file_origin[("mr", copy.name)] == old_origin and shown


def test_compare_anyway_is_offered_when_a_file_cannot_be_reloaded():
    win, copy = _reload_window()
    _browsed(win, "ctr", _CTR_A)
    copy.unlink()
    win.missing_file_chooser = lambda side, name, path: "keep"      # stays stuck
    win.changed_files_chooser = lambda changed: "reload"
    offered = []
    win.reload_report = lambda lines: offered.append(lines) or True  # "Compare anyway"
    win._run_compare()
    _wait_reloaded(win)
    assert offered and H.wait(lambda: "Compared at" in win.statusBar().currentMessage(), 60)
    assert H.wait(lambda: not win._stats_workers, 120)


def test_unticked_sheets_stay_unticked_when_a_located_file_has_another_name():
    import shutil
    win, copy = _reload_window()
    rows = [win._mr_table_list.itemWidget(win._mr_table_list.item(i)) for i in range(win._mr_table_list.count())]
    ch = next(r for r in rows if r._table_key and r._table_key[2] == "CH-1")
    ch._checkbox.setChecked(False)
    moved = H.SANDBOX / "renamed_copy_of_b.xlsm"
    shutil.copy(_MR_B, moved)
    copy.unlink()
    win.missing_file_chooser = lambda side, name, path: str(moved)
    win._reload_changed_files()
    _wait_reloaded(win)
    rows = [win._mr_table_list.itemWidget(win._mr_table_list.item(i)) for i in range(win._mr_table_list.count())]
    state = {r._table_key[2]: r._checkbox.isChecked() for r in rows if r._table_key}
    assert state["CH-1"] is False and all(r._table_key[0] == moved.name for r in rows if r._table_key)


def test_files_cannot_be_removed_or_cleared_while_a_reload_is_parsing():
    import shutil
    win, copy = _reload_window()
    shutil.copy(_MR_A, copy)
    _touch_later(copy)
    win._reload_changed_files()
    assert win._reload_pending
    win._remove_file("mr", copy.name)
    win._clear("mr")
    assert win._mr_file_list.count() == 1 and win._mr_tables       # nothing was removed
    _wait_reloaded(win)
    assert win._mr_tables and win._mr_file_list.count() == 1


def test_the_files_are_checked_off_the_ui_thread_when_the_window_is_activated():
    win, copy = _reload_window()
    _touch_later(copy)
    win._check_files_in_background()
    assert H.wait(lambda: win._reload_btn.isVisibleTo(win), 30) and "(1)" in win._reload_btn.text()
    assert win._stamp_worker.isFinished() or H.wait(lambda: win._stamp_worker.isFinished(), 10)
