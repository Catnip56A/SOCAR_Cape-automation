"""Purchase orders: everything needed to debug a PO is in app.log (what was read, what was suggested and picked, what
was about to be written, the answers to the questions, the result and every refusal), and visible in the Inspector (the
Data, Storage, Variables and Tasks tabs and the Snapshot) — as counts and paths only, never PO or CTR texts in the
Snapshot. Sandbox copies of the test tracker only."""
import logging
import shutil
import types
from datetime import date

import harness as H
import test_po as TP
from ctr_tools import inspector as INS
from ctr_tools import po as P
from ctr_tools import tracker_fast as TF
from ctr_tools.tasks import TASKS


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def lines(self, level=None):
        return [f"{r.levelname} {r.name.split('.')[-1]}: {r.getMessage()}" for r in self.records
                if level is None or r.levelno >= level]

    def has(self, *needles, level=None):
        return any(all(n in line for n in needles) for line in self.lines(level))


def _capturing():
    cap, root = _Capture(), logging.getLogger()
    old = root.level
    root.setLevel(logging.INFO)             # what the app runs with (common.app_logging.setup_logging)
    root.addHandler(cap)
    return cap, lambda: (root.removeHandler(cap), root.setLevel(old))


def test_a_po_that_is_read_and_a_file_that_is_refused_are_both_logged():
    files = sorted(TP.SAMPLE_DIR.glob("2_.PO_*.PDF"))
    if not files:
        raise H.Skip("the sample PO PDFs are not here")
    cap, undo = _capturing()
    try:
        P.read_po(files[0])
        bad = H.SANDBOX / "not_a_po.pdf"
        bad.write_bytes(b"this is not a pdf")
        try:
            P.read_po(bad)
            raise AssertionError("a non-PDF must be refused")
        except P.POError:
            pass
    finally:
        undo()
    assert cap.has("PO read:", "file=" + files[0].name, "po=", "currency=", "items=1", "total="), cap.lines()
    assert cap.has("PO file refused: not_a_po.pdf", level=logging.WARNING), cap.lines()
    assert not any("@" in line for line in cap.lines()), "no e-mail address (buyer data) may reach the log"


def test_the_picking_and_the_write_of_a_po_are_logged_step_by_step():
    from ctr_tools import po_view as PV
    w, dummy, PORow = TP._synthetic_section()
    po = TP._synthetic_po(desc="Cleaning activities Serenja NORM storage")
    cap, undo = _capturing()
    try:
        w._add_row(PORow(po=po, item=po.items[0]))
        w._populate(w._rows[0])                                    # the suggestions are built (and logged)
        r = w._rows[0]
        w._select_row(r, 3)
        real_read, real_write = PV.read_tracker_rows, PV.write_pos_fast
        PV.read_tracker_rows = lambda _p: w._tracker_rows
        PV.write_pos_fast = lambda path, assignments, make_backup=False: ([(0, assignments[0]["ctr"], 3)], [], None)
        seen, old = TP._patch_boxes()
        try:
            w._write()
            assert H.wait(lambda: not w._rows, 30), seen
        finally:
            TP._unpatch(old)
            PV.read_tracker_rows, PV.write_pos_fast = real_read, real_write
    finally:
        undo()
    for needle in (("PO 4300000001 item 1", "suggestion(s)"), ("PO 4300000001 item 1: CTR row 3 picked", "CTR-26-120"),
                   ("PO write requested", "1 of 1 item(s)", "backup OFF"), ("PO write: row 3", "po_number="),
                   ("PO write finished", "1 written", "rows [3]")):
        assert cap.has(*needle), (needle, cap.lines())
    assert not cap.has("removed from the list by the user"), "items removed after a write are not 'removed by the user'"
    w.deleteLater()


def test_every_refusal_and_a_failed_write_is_logged_with_the_reason():
    from ctr_tools import po_view as PV
    w, dummy, PORow = TP._synthetic_section()
    po = TP._synthetic_po()
    w._add_row(PORow(po=po, item=po.items[0]))
    r = w._rows[0]
    cap, undo = _capturing()
    seen, old = TP._patch_boxes()
    try:
        w._write()                                                 # nothing chosen
        w._select_row(r, 2)
        real_read, real_write = PV.read_tracker_rows, PV.write_pos_fast
        PV.read_tracker_rows = lambda _p: w._tracker_rows
        def locked(*a, **k):
            raise ValueError("Cannot save the tracker — it is open in Excel.")
        PV.write_pos_fast = locked
        try:
            w._write()
            assert H.wait(lambda: any(k == "critical" for k, _t in seen), 30), seen
        finally:
            PV.read_tracker_rows, PV.write_pos_fast = real_read, real_write
    finally:
        TP._unpatch(old)
        undo()
    assert cap.has("PO write refused: no item has a CTR chosen", level=logging.WARNING), cap.lines()
    assert cap.has("PO write failed, nothing lost", "open in Excel", level=logging.ERROR), cap.lines()
    assert cap.has("PO write: reminder shown", "Pricebook not checked"), cap.lines()
    w.deleteLater()


def test_the_real_writers_log_what_they_wrote_and_why_a_row_was_skipped():
    work = TP._copy("po_log_write.xlsm")
    rec = TP._free_row(TF.read_tracker_rows(work))
    cap, undo = _capturing()
    try:
        TF.write_pos_fast(work, [TP._assignment(rec), dict(TP._assignment(rec), ctr="CTR-99-999")], make_backup=True)
    finally:
        undo()
    assert cap.has("write_pos_fast: 2 assignment(s)", "backup "), cap.lines()
    assert cap.has("write_pos_fast: row", "now holds", "the tracker changed", level=logging.WARNING), cap.lines()
    assert cap.has("write_pos_fast: 1 row(s) written", f"[{rec['row']}]", "1 skipped"), cap.lines()


def test_the_saving_of_a_settings_list_is_logged():
    import json
    import ctr_tools.config as C
    d = H.SANDBOX / "po_log_cfg"
    d.mkdir(exist_ok=True)
    ship, mine = d / "shipped.json", d / "template_config.json"
    ship.write_text("{}", encoding="utf-8")
    mine.write_text(json.dumps({"ctr_tracker_po_companies": {"A": "a"}}), encoding="utf-8")
    old_paths, old_cfg = (C._SHIPPED_CONFIG_PATH, C._CONFIG_PATH), dict(C.CFG.get("ctr_tracker_po_companies", {}))
    cap, undo = _capturing()
    try:
        C._SHIPPED_CONFIG_PATH, C._CONFIG_PATH = ship, mine
        C.save_po_companies({"X CO": "XC", "Y CO": "YC"})
    finally:
        undo()
        C._SHIPPED_CONFIG_PATH, C._CONFIG_PATH = old_paths
        C.CFG["ctr_tracker_po_companies"] = old_cfg
    assert cap.has("Settings saved: ctr_tracker_po_companies now has 2 entries"), cap.lines()


# ── the Inspector ────────────────────────────────────────────────────────────────────────────────────────

def _tracker_with_a_po(work):
    from ctr_tools.tracker_window import CTRTrackerWidget
    from ctr_tools.po_view import _PORow
    t = CTRTrackerWidget()
    t._tracker_edit.setText(str(work))
    po = TP._synthetic_po(desc="Secret description of the job")
    po.path = str(H.SANDBOX / "PO_4300000001.pdf")
    t._po_section._add_row(_PORow(po=po, item=po.items[0]))
    t._po_section._ensure_tracker_rows()
    return t


def test_the_inspector_lists_the_po_items_the_tracker_rows_the_files_and_the_task():
    work = TP._copy("po_log_insp.xlsm")
    t = _tracker_with_a_po(work)
    job_seen = any(j.label == "Read tracker rows for purchase orders" for j in TASKS.jobs())
    assert job_seen, [j.label for j in TASKS.jobs()]                      # the 3-second read is a visible task
    assert H.wait(lambda: bool(t._po_section._tracker_rows), 60)
    H.pump(10)
    win = types.SimpleNamespace(_ctr_tracker=t)
    data = {r["name"]: r for r in INS.collect_data(win)}
    items = data["Purchase orders (items)"]
    assert items["loaded"] and items["rows"] == 1 and "PO_4300000001.pdf" in items["source"]
    assert {"PO", "Item", "Currency", "CTR row picked", "Warnings"} <= set(items["df"].columns)
    trows = data["Tracker rows (PO picker)"]
    assert trows["loaded"] and trows["rows"] > 100 and len(trows["df"]) == 200      # the count is real, the preview short
    storage = {(e["group"], e["label"]): e for e in INS.collect_storage(win)}
    assert ("CTR Tracker", "Purchase order file") in storage
    variables = {n: v for n, _t, v in INS.collect_variables(win)}
    assert variables["purchase orders.items listed"] == "1" and variables["purchase orders.items with a CTR picked"] == "0"
    assert int(variables["purchase orders.tracker rows held"]) > 100
    t.deleteLater()


def test_the_snapshot_carries_the_po_counts_but_no_po_or_ctr_text():
    import app as A
    win = A.MainWindow()
    work = TP._copy("po_log_snap.xlsm")
    from ctr_tools.po_view import _PORow
    po = TP._synthetic_po(desc="Secret description of the job")
    po.path = str(H.SANDBOX / "PO_4300000001.pdf")
    win._ctr_tracker._tracker_edit.setText(str(work))
    win._ctr_tracker._po_section._add_row(_PORow(po=po, item=po.items[0]))
    win._ctr_tracker._po_section._ensure_tracker_rows()
    assert H.wait(lambda: bool(win._ctr_tracker._po_section._tracker_rows), 60)
    H.pump(10)
    text = INS.build_snapshot(win)
    assert "Purchase orders (items)" in text and "purchase orders.items listed" in text
    assert "Tracker rows (PO picker)" in text and "Purchase order file" in text
    assert "Secret description" not in text, "a description must not reach the snapshot"
    assert "CTR-26-" not in text.split("Variables")[-1].split("Tasks")[0], "CTR numbers must not reach the variables"
    win.close()
    win.deleteLater()
