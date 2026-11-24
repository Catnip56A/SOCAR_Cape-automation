"""
ctr_tools/po_view.py

The "Purchase orders" section of the CTR Tracker tab.

Workflow: load one or more PO files (SAP PDFs) — each PO item gets a row; pick the CTR (tracker row) each item belongs
to (the app suggests rows from the CTR number typed in the item text, the same value, words the descriptions share —
the user decides), check the short company name and type the Type of PO, then "Write POs to Tracker…". Only the cells
a PO fills are written (PO number, line, company, account assignment, delivery date, PO value, Type of PO); where a
cell already holds a different value the user is asked, PO by PO. A pricebook number that differs from the CTR's is
shown as a warning. The writer is tracker_fast.write_pos_fast; the reader/suggestions are ctr_tools.po.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from PySide6.QtCore import QModelIndex, QSettings, QSortFilterProxyModel, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QKeyEvent, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QDialog, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ctr_tools.po import (
    POData, POError, POItem, WordIndex, ctr_base, is_number_query, pricebook_differs, read_ctr_file_info, read_po,
    short_company, suggest_rows,
)
from ctr_tools.config import CFG
from ctr_tools.tasks import TASKS
from ctr_tools.tracker_fast import read_tracker_rows, write_pos_fast
from ctr_tools.window import _group_css, _make_table

log = logging.getLogger(__name__)

PRIMARY = "#1976D2"
MUTED = "#757575"
RED = "#C62828"
YELLOW = "#B8860B"          # the reminders (check before writing) — dark yellow so it reads on white

_HEADERS = ["PO", "Item", "Company", "Description", "Value", "Delivery", "Account", "Type of PO",
            "CTR (tracker row)", "Note", "CTR file", ""]
(_C_PO, _C_ITEM, _C_COMPANY, _C_DESC, _C_VALUE, _C_DELIVERY, _C_ACCOUNT, _C_TYPE, _C_CTR, _C_NOTE, _C_FILE,
 _C_REMOVE) = range(12)
_SEARCH_ROLE = Qt.ItemDataRole.UserRole + 1      # the lower-case text a typed search is matched against
_TYPES_KEY = "ctr_tracker/po_types"

# field -> (tracker record key, label shown to the user)
_FIELDS = {
    "po_number": ("po", "PO number"), "po_line": ("line", "PO line"), "company": ("client", "Company"),
    "account": ("account", "Account assignment"), "delivery": ("delivery", "Delivery date"),
    "value": ("po_value", "PO value"), "po_type": ("po_type", "Type of PO"),
}


def _fmt_money(v) -> str:
    return f"{v:,.2f}" if isinstance(v, (int, float)) else ""


def _fmt_date(d) -> str:
    return d.strftime("%d-%b-%Y") if isinstance(d, date) else ""


def _same(field_name: str, old, new) -> bool:
    """True when an existing tracker value already equals what the PO would write."""
    if old in (None, ""):
        return False
    if field_name == "value":
        try:
            return abs(float(old) - float(new)) < 0.005
        except (TypeError, ValueError):
            return False
    if field_name == "delivery":
        return old == new
    if field_name == "po_line":
        try:
            return int(float(old)) == int(new)
        except (TypeError, ValueError):
            return False
    return str(old).strip().lower() == str(new).strip().lower()


def _show(field_name: str, v) -> str:
    if field_name == "value":
        return _fmt_money(v)
    if field_name == "delivery":
        return _fmt_date(v)
    return "" if v is None else str(v)


class _WordFilter(QSortFilterProxyModel):
    """The completer's list of tracker rows for what is typed in the CTR box.
    * Every word typed must occur somewhere in the row's CTR number, description, company, PO, account, currency or
      value (any order) — exact, newest rows first. A CTR number (or part of one) is always searched this way.
    * Only when that finds nothing and the text is not a CTR number, the rows with the most alike DESCRIPTION are
      listed (RapidFuzz on the words: a typo or another word ending still counts), most alike first."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rank: dict[int, int] = {}
        self.setDynamicSortFilter(True)

    def set_query(self, text: str) -> None:
        text = (text or "").strip()
        src = self.sourceModel()
        rank: dict[int, int] = {}
        if text and src is not None:
            words = text.lower().split()
            exact = [i for i in range(src.rowCount()) if all(w in (src.index(i, 0).data(_SEARCH_ROLE) or "") for w in words)]
            if exact:
                rank = {i: n for n, i in enumerate(exact)}
            elif not is_number_query(text):
                index = getattr(src, "word_index", None)
                if index is not None:
                    rank = {src_i: n for n, (src_i, _score) in enumerate(
                        (index.positions[pos], sc) for pos, sc in index.search(text))}
        self._rank = rank
        self.invalidate()
        self.sort(0)

    def filterAcceptsRow(self, row: int, parent=QModelIndex()) -> bool:
        return row in self._rank

    def lessThan(self, left, right) -> bool:
        return self._rank.get(left.row(), 1 << 30) < self._rank.get(right.row(), 1 << 30)


class _CtrCombo(QComboBox):
    """The CTR box. Up/Down and the mouse wheel must never pick a CTR by accident (a plain editable combo steps to the
    next item and writes it into the box), and the list that opens must follow what is in the box right now: the
    suggestions for the PO description while it is untouched, the matches for the typed words once something was typed."""

    def __init__(self, before_popup, parent=None):
        super().__init__(parent)
        self._before_popup = before_popup

    def showPopup(self) -> None:
        self._before_popup()
        super().showPopup()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and not self.view().isVisible():
            self.showPopup()                       # open the list; picking is for Enter or a click
            return
        super().keyPressEvent(e)

    def wheelEvent(self, e) -> None:
        e.ignore()                                 # scrolling the table over the box must not change the CTR


@dataclass
class _PORow:
    po: POData
    item: POItem
    company_edit: QLineEdit | None = None
    type_combo: QComboBox | None = None
    ctr_combo: QComboBox | None = None
    proxy: _WordFilter | None = None
    completer: QCompleter | None = None
    note: QLabel | None = None
    file_btn: QPushButton | None = None
    ctr_pricebook: str = ""
    ctr_file: str = ""
    ctr_file_ctr: str = ""          # the CTR number found inside the picked CTR file
    picked: int | None = None        # the tracker row last picked (the picked CTR file belongs to that one)
    suggested: list = field(default_factory=list)


class _RowsWorker(QThread):
    """Reads the tracker's rows (about 3 s on the real file) off the UI thread."""
    done = Signal(str, list)
    error = Signal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path
        self.job = TASKS.begin_job("Read tracker rows for purchase orders", [Path(path).name], stoppable=False)

    def run(self):
        self.job.start(0)
        t0 = time.time()
        try:
            rows = read_tracker_rows(self._path)
        except Exception as exc:
            log.exception("Reading the tracker rows failed: %s", Path(self._path).name)
            self.job.failed(0, str(exc))
            self.job.finish()
            self.error.emit(str(exc))
            return
        log.info("PO: %d tracker rows read from %s in %.1f s", len(rows), Path(self._path).name, time.time() - t0)
        self.job.done(0)
        self.job.finish()
        self.done.emit(self._path, rows)


class _PoWriteWorker(QThread):
    finished_ok = Signal(list, list, object)     # written, skipped, backup path | None
    error = Signal(str)

    def __init__(self, path: str, assignments: list[dict], make_backup: bool, parent=None):
        super().__init__(parent)
        self._path, self._assignments, self._backup = path, assignments, make_backup
        self.job = TASKS.begin_job("Write purchase orders to CTR Tracker",
                                   [f"Write {len(assignments)} PO item(s)"], stoppable=False)

    def run(self):
        self.job.start(0)
        try:
            written, skipped, backup = write_pos_fast(self._path, self._assignments, make_backup=self._backup)
        except Exception as exc:
            log.exception("Writing the purchase orders failed")
            self.job.failed(0, str(exc))
            self.job.finish()
            self.error.emit(str(exc))
            return
        self.job.done(0)
        self.job.finish()
        self.finished_ok.emit(written, skipped, backup)


class _OverwriteDialog(QDialog):
    """Cells that already hold a different value, PO by PO: the user decides per PO whether the PO's values replace
    them (otherwise those cells are left as they are and only empty ones are filled)."""

    def __init__(self, parent, conflicts: dict[int, list[tuple[str, str, str]]], titles: dict[int, str]):
        super().__init__(parent)
        self.setWindowTitle("Values already in the tracker")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        note = QLabel("These tracker rows already hold a different value in a column a PO fills. Tick a PO to replace "
                      "its old values with the new ones; left unticked, the old values stay and only empty cells are "
                      "filled.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {MUTED};")
        lay.addWidget(note)
        inner = QWidget()
        il = QVBoxLayout(inner)
        self._checks: dict[int, QCheckBox] = {}
        for idx, lines in conflicts.items():
            box = QGroupBox(titles[idx])
            bl = QVBoxLayout(box)
            for label, old, new in lines:
                bl.addWidget(QLabel(f"{label}:  {old}   →   <b>{new}</b>"))
            chk = QCheckBox("Replace the old values with the new ones")
            bl.addWidget(chk)
            self._checks[idx] = chk
            il.addWidget(box)
        il.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(min(360, 90 + 90 * len(conflicts)))
        lay.addWidget(scroll)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("Continue")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)

    def replace_flags(self) -> dict[int, bool]:
        return {i: c.isChecked() for i, c in self._checks.items()}


class _TypesDialog(QDialog):
    """The remembered Type of PO texts: remove the ones that should not be suggested any more."""

    def __init__(self, parent, types: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Type of PO suggestions")
        lay = QVBoxLayout(self)
        note = QLabel("The texts you typed in Type of PO before. They are offered as suggestions; remove the ones you "
                      "do not want offered again.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {MUTED};")
        lay.addWidget(note)
        self._list = QListWidget()
        self._list.addItems(types)
        lay.addWidget(self._list)
        row = QHBoxLayout()
        rem = QPushButton("Remove selected")
        rem.clicked.connect(lambda: [self._list.takeItem(self._list.row(i)) for i in self._list.selectedItems()])
        ok = QPushButton("Save")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(rem)
        row.addStretch(1)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)

    def types(self) -> list[str]:
        return [self._list.item(i).text() for i in range(self._list.count())]


class POSectionWidget(QGroupBox):
    """tracker_path(): the tracker workbook path; backup(): whether to back it up first; ctr_files(): {CTR base
    number: path of a loaded CTR file} (used to compare pricebooks) — all supplied by the Tracker tab."""

    def __init__(self, tracker_path, backup, ctr_files, parent=None):
        super().__init__("Purchase Orders", parent)
        self.setStyleSheet(_group_css(PRIMARY))
        self._tracker_path, self._backup, self._ctr_files = tracker_path, backup, ctr_files
        self._rows: list[_PORow] = []
        self._tracker_rows: list[dict] = []
        self._search_model: QStandardItemModel | None = None
        self._rows_for: str = ""
        self._rows_worker: _RowsWorker | None = None
        self._write_worker: _PoWriteWorker | None = None
        self._types = self._load_types()

        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        info = QLabel(
            "Load the PO file(s) (PDF), pick the CTR each item belongs to, then write. A PO fills the tracker row's PO "
            "number, PO line, Company (short name), Account Assignment, PO value (in the PO's currency) and the "
            "delivery date (PO Valid from). The suggestions are only a help — you choose the row. A cell that "
            "already holds a different value is only replaced when you say so.")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        lay.addWidget(info)

        bar = QHBoxLayout()
        self._load_btn = QPushButton("Load PO file(s)…")
        self._load_btn.clicked.connect(self._choose_files)
        self._types_btn = QPushButton("Type of PO suggestions…")
        self._types_btn.clicked.connect(self._edit_types)
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self.clear)
        bar.addWidget(self._load_btn)
        bar.addWidget(self._types_btn)
        bar.addWidget(self._clear_btn)
        bar.addStretch(1)
        lay.addLayout(bar)

        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        self._tbl = _make_table(_HEADERS, stretch_col=_C_DESC)
        self._tbl.setFixedHeight(220)
        self._tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)   # a blue row made the red notes unreadable
        for col, w in ((_C_PO, 90), (_C_ITEM, 40), (_C_COMPANY, 80), (_C_VALUE, 110), (_C_DELIVERY, 90),
                       (_C_ACCOUNT, 110), (_C_TYPE, 120), (_C_CTR, 330), (_C_FILE, 80), (_C_NOTE, 300),
                       (_C_REMOVE, 28)):
            self._tbl.setColumnWidth(col, w)
        lay.addWidget(self._tbl)

        self._write_btn = QPushButton("Write POs to Tracker…")
        self._write_btn.setFixedHeight(36)
        self._write_btn.setEnabled(False)
        self._write_btn.setStyleSheet(f"""
            QPushButton {{ background-color: {PRIMARY}; color: white; border: none; border-radius: 6px;
                          font-size: 13px; font-weight: bold; }}
            QPushButton:hover    {{ background-color: #1565C0; }}
            QPushButton:disabled {{ background-color: #E0E0E0; color: #9E9E9E; }}
        """)
        self._write_btn.clicked.connect(self._write)
        lay.addWidget(self._write_btn)

    # ── read-only views of the state, for the Inspector (F12) ───────────────

    def inspector_items(self) -> list[dict]:
        """One dict per PO item in the list: what was read from the PO and what was chosen/typed so far."""
        out = []
        for r in self._rows:
            out.append({
                "PO": r.po.po_number, "Item": r.item.number, "PO company": r.po.company, "Company (typed)": r.company_edit.text(),
                "Currency": r.po.currency, "Amount": r.item.amount, "Delivery": r.item.delivery, "Account": r.item.account,
                "Pricebook (PO)": r.item.pricebook, "CTR row picked": self._chosen_row(r), "Pricebook (CTR file)": r.ctr_pricebook,
                "Type of PO": r.type_combo.currentText(), "Suggestions": len(r.suggested),
                "Warnings": len(self.problems(r)[0]), "Reminders": len(self.reminders(r)),
            })
        return out

    def inspector_files(self) -> tuple[list[str], list[str]]:
        """(PO files loaded, CTR files picked for the pricebook check) — paths only."""
        pos = sorted({r.po.path for r in self._rows if r.po.path})
        ctrs = sorted({r.ctr_file for r in self._rows if r.ctr_file})
        return pos, ctrs

    def inspector_rows_state(self) -> tuple[list[dict], str, bool]:
        """(the tracker rows held for the pickers, the workbook they came from, whether they are being read now)."""
        try:
            reading = self._rows_worker is not None and self._rows_worker.isRunning()
        except RuntimeError:                      # the thread object is already gone
            reading = False
        return self._tracker_rows, self._rows_for, reading

    # ── Type of PO suggestions (remembered, deletable) ──────────────────────

    @staticmethod
    def _settings() -> QSettings:
        return QSettings("SOCAR", "CTRGenerator")

    def _load_types(self) -> list[str]:
        s = self._settings()
        if not s.contains(_TYPES_KEY):          # nothing saved yet: the shipped suggestions (a deleted one stays deleted)
            return [str(x) for x in CFG["ctr_tracker_po"].get("default_types", [])]
        try:
            data = json.loads(str(s.value(_TYPES_KEY, "[]")))
            return [str(x) for x in data if str(x).strip()] if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []

    def _save_types(self) -> None:
        self._settings().setValue(_TYPES_KEY, json.dumps(self._types, ensure_ascii=False))

    def _remember_types(self, used: list[str]) -> None:
        before = list(self._types)
        for t in used:
            t = t.strip()
            if t and t.lower() not in (x.lower() for x in self._types):
                self._types.append(t)
        self._save_types()
        if self._types != before:
            log.info("PO: Type of PO suggestions now %s", self._types)
        self._refresh_type_combos()

    def _refresh_type_combos(self) -> None:
        for r in self._rows:
            text = r.type_combo.currentText()
            r.type_combo.blockSignals(True)
            r.type_combo.clear()
            r.type_combo.addItems(self._types)
            r.type_combo.setEditText(text)
            r.type_combo.blockSignals(False)

    def _edit_types(self) -> None:
        dlg = _TypesDialog(self, list(self._types))
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        types = dlg.types() if accepted else None
        dlg.deleteLater()
        if types is not None:
            log.info("PO: Type of PO suggestions edited: %s -> %s", self._types, types)
            self._types = types
            self._save_types()
            self._refresh_type_combos()

    # ── loading ─────────────────────────────────────────────────────────────

    def invalidate_tracker_rows(self) -> None:
        """The tracker workbook changed (another file, or a write): re-read its rows the next time they are needed."""
        self._tracker_rows, self._rows_for, self._search_model = [], "", None

    def tracker_path_changed(self) -> None:
        """The tracker box of the tab changed: forget the old rows and, when POs are already listed, read the new
        workbook's rows now (a PO loaded before the workbook was picked then gets its suggestions)."""
        self.invalidate_tracker_rows()
        if self._rows and Path(self._tracker_path().strip() or ".").is_file():
            self._ensure_tracker_rows()

    def clear(self) -> None:
        if self._rows:
            log.info("PO: list cleared (%d item(s) dropped, nothing written)", len(self._rows))
        self._rows.clear()
        self._tbl.setRowCount(0)
        self._status.setText("")
        self._write_btn.setEnabled(False)

    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose purchase order file(s)", "", "Purchase orders (*.pdf *.PDF)")
        if paths:
            self.load_files(paths)

    def load_files(self, paths: list[str]) -> None:
        errors, added = [], 0
        for p in paths:
            try:
                po = read_po(p)
            except POError as exc:
                errors.append(str(exc))
                continue
            for item in po.items:
                if any(r.po.po_number == po.po_number and r.item.number == item.number for r in self._rows):
                    errors.append(f"PO {po.po_number} item {item.number} is already in the list.")
                    continue
                self._add_row(_PORow(po=po, item=item))
                added += 1
        log.info("PO: %d file(s) chosen, %d item(s) added to the list, %d problem(s)", len(paths), added, len(errors))
        for e in errors:
            if "already in the list" in e:
                log.warning("PO: %s", e)
        if errors:
            QMessageBox.warning(self, "Purchase orders", "\n".join(errors))
        if added:
            self._ensure_tracker_rows()
        self._write_btn.setEnabled(bool(self._rows))

    def _add_row(self, r: _PORow) -> None:
        self._rows.append(r)
        t = self._tbl
        row = t.rowCount()
        t.insertRow(row)
        from ctr_tools.window import _ro_item
        t.setItem(row, _C_PO, _ro_item(r.po.po_number))
        t.setItem(row, _C_ITEM, _ro_item(str(r.item.number)))
        t.setItem(row, _C_DESC, _ro_item(r.item.description))
        t.setItem(row, _C_VALUE, _ro_item(f"{_fmt_money(r.item.amount)} {r.po.currency}"))
        t.setItem(row, _C_DELIVERY, _ro_item(_fmt_date(r.item.delivery)))
        t.setItem(row, _C_ACCOUNT, _ro_item(r.item.account))

        r.company_edit = QLineEdit(short_company(r.po.company) or "")
        r.company_edit.setPlaceholderText("short name")
        r.company_edit.setToolTip(f"The PO says: {r.po.company}\nThe tracker's Company column uses the short name "
                                  "(the list is in Settings → PO company short names).")
        r.company_edit.textChanged.connect(lambda _=None, rr=r: self._refresh_note(rr))
        t.setCellWidget(row, _C_COMPANY, r.company_edit)

        r.type_combo = QComboBox()
        r.type_combo.setEditable(True)
        r.type_combo.addItems(self._types)
        r.type_combo.setCurrentIndex(-1)
        r.type_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        r.type_combo.lineEdit().setPlaceholderText("e.g. LIMIT/FIX")
        r.type_combo.setToolTip("Type of PO — type it, or pick one you used before ('Type of PO suggestions…' removes "
                                "old suggestions). Left blank, the cell is not touched.")
        t.setCellWidget(row, _C_TYPE, r.type_combo)

        r.ctr_combo = _CtrCombo(lambda rr=r: self._fill_dropdown(rr))
        r.ctr_combo.setEditable(True)
        r.ctr_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        r.ctr_combo.setMaxVisibleItems(14)
        r.ctr_combo.currentIndexChanged.connect(lambda _=None, rr=r: self._on_ctr_changed(rr))
        # What is typed here searches every tracker row (all the words, any order, in the full text), not just the
        # first words of the listed ones: a completer over a word filter, separate from the combo's own short list.
        r.proxy = _WordFilter(r.ctr_combo)
        r.completer = QCompleter(r.proxy, r.ctr_combo)
        r.completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        r.completer.setMaxVisibleItems(12)
        r.completer.activated[QModelIndex].connect(lambda idx, rr=r: self._on_completion(rr, idx))
        r.ctr_combo.lineEdit().setCompleter(r.completer)
        r.ctr_combo.lineEdit().textEdited.connect(lambda text, rr=r: self._search_typed(rr, text))
        r.ctr_combo.lineEdit().editingFinished.connect(lambda rr=r: self._restore_description_if_empty(rr))
        t.setCellWidget(row, _C_CTR, r.ctr_combo)

        r.file_btn = QPushButton("CTR file…")
        r.file_btn.setToolTip("Optional: pick the CTR's own file to compare its pricebook number with the PO's. It is "
                              "taken automatically from a CTR loaded in the batch above.")
        r.file_btn.clicked.connect(lambda _=None, rr=r: self._choose_ctr_file(rr))
        t.setCellWidget(row, _C_FILE, r.file_btn)

        r.note = QLabel()
        r.note.setTextFormat(Qt.TextFormat.RichText)
        r.note.setWordWrap(True)
        t.setCellWidget(row, _C_NOTE, r.note)

        rm = QPushButton("×")
        rm.setFixedWidth(24)
        rm.clicked.connect(lambda _=None, rr=r: self._remove(rr))
        t.setCellWidget(row, _C_REMOVE, rm)
        t.setRowHeight(row, 46)
        self._populate(r)
        self._refresh_note(r)

    def _remove(self, r: _PORow, silent: bool = False) -> None:
        if not silent:
            log.info("PO %s item %s: removed from the list by the user (nothing written)", r.po.po_number, r.item.number)
        i = self._rows.index(r)
        self._rows.pop(i)
        self._tbl.removeRow(i)
        self._write_btn.setEnabled(bool(self._rows))
        for other in self._rows:
            self._refresh_note(other)

    # ── the tracker rows and the pickers ────────────────────────────────────

    def _ensure_tracker_rows(self) -> None:
        path = self._tracker_path().strip()
        if not path or not Path(path).is_file():
            self._status.setText("Pick the tracker workbook above first — the CTR rows come from it.")
            self._status.setStyleSheet(f"color: {RED}; font-size: 11px;")
            return
        if self._rows_for == path and self._tracker_rows:
            for r in self._rows:
                self._populate(r)
            return
        if self._rows_worker is not None and self._rows_worker.isRunning():
            return
        self._status.setText("Reading the tracker's rows …")
        self._status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._rows_worker = _RowsWorker(path, self)
        self._rows_worker.done.connect(self._rows_ready)
        self._rows_worker.error.connect(self._rows_failed)
        self._rows_worker.start()

    def _rows_ready(self, path: str, rows: list) -> None:
        self._tracker_rows, self._rows_for = rows, path
        self._search_model = self._build_search_model(rows)
        log.info("PO: %d tracker rows ready for the CTR pickers (%d with a CTR number)", len(rows),
                 self._search_model.rowCount())
        self._status.setText(f"{len(rows):,} tracker rows read.")
        self._status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        for r in self._rows:
            self._populate(r)

    def _rows_failed(self, message: str) -> None:
        self._status.setText(f"The tracker could not be read: {message}")
        self._status.setStyleSheet(f"color: {RED}; font-size: 11px;")

    @staticmethod
    def _label(rec: dict, star: bool = False, why: str = "") -> str:
        desc = (rec.get("description") or "")[:48]
        cur = rec.get("currency") or "?"
        head = f"{'★ ' if star else ''}{rec['ctr']} · row {rec['row']} · {cur}"
        if rec.get("revision"):
            head += f" · rev {rec['revision']}"
        return f"{head} · {desc}" + (f"   — {why}" if why else "")

    def _populate(self, r: _PORow) -> None:
        combo = r.ctr_combo
        if combo is None:
            return
        keep = self._chosen_row(r)
        text_before = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        by_row = {rec["row"]: rec for rec in self._tracker_rows}
        r.suggested = suggest_rows(r.item, self._tracker_rows)
        if self._tracker_rows:
            log.info("PO %s item %s: %d suggestion(s) for %r%s", r.po.po_number, r.item.number, len(r.suggested),
                     (r.item.description or "")[:60],
                     "" if not r.suggested else " — top: " + ", ".join(
                         f"row {x['row']} ({x['score']:g}; {x['reasons'][0]})" for x in r.suggested[:3]))
        for s in r.suggested:
            rec = by_row[s["row"]]
            combo.addItem(self._label(rec, True, "; ".join(s["reasons"])), rec["row"])
        if r.proxy is not None:
            r.proxy.setSourceModel(self._search_model)
        if keep is not None and combo.findData(keep) < 0 and keep in by_row:
            combo.addItem(self._label(by_row[keep]), keep)          # a row picked earlier stays in the list
        if keep is not None:
            combo.setCurrentIndex(combo.findData(keep))
        else:
            # Nothing picked: the box holds the PO item's description (or what was typed), as text only — it picks
            # nothing and nothing is written until a CTR is chosen from the list.
            combo.setCurrentIndex(-1)
            combo.setEditText(text_before or r.item.description)
        combo.lineEdit().setPlaceholderText("type a CTR number or words of the description")
        combo.blockSignals(False)
        self._on_ctr_changed(r)

    def _restore_description_if_empty(self, r: _PORow) -> None:
        """Leaving an emptied box puts the PO item's description back (as text only, nothing is picked)."""
        c = r.ctr_combo
        if self._chosen_row(r) is None and not c.currentText().strip():
            c.setEditText(r.item.description)

    def _fill_dropdown(self, r: _PORow) -> None:
        """Called just before the list opens: build it for what is in the box now. Untouched (the PO description) or a
        picked row: the starred suggestions for the description. Something typed: the rows that match it, the same
        ones the typing list showed — so the list is never the leftover of an earlier search."""
        c = r.ctr_combo
        picked = self._chosen_row(r)
        text = c.currentText()
        query = text.strip()
        typed = picked is None and bool(query) and query != (r.item.description or "").strip()
        by_row = {rec["row"]: rec for rec in self._tracker_rows}
        c.blockSignals(True)
        c.clear()
        if typed and self._search_model is not None:
            r.proxy.set_query(query)
            for i in range(min(r.proxy.rowCount(), 60)):
                idx = r.proxy.index(i, 0)
                c.addItem(str(idx.data(Qt.ItemDataRole.DisplayRole)), idx.data(Qt.ItemDataRole.UserRole))
        else:
            for sug in r.suggested:
                rec = by_row.get(sug["row"])
                if rec is not None:
                    c.addItem(self._label(rec, True, "; ".join(sug["reasons"])), rec["row"])
            if picked is not None and c.findData(picked) < 0 and picked in by_row:
                c.addItem(self._label(by_row[picked]), picked)
        if picked is not None and c.findData(picked) >= 0:
            c.setCurrentIndex(c.findData(picked))
        else:
            c.setCurrentIndex(-1)
            c.setEditText(text)                    # what was typed (or the description) stays in the box
        c.blockSignals(False)

    def _build_search_model(self, rows: list[dict]) -> QStandardItemModel:
        """One list of every tracker row with a CTR number (newest first), shared by all the CTR boxes."""
        model = QStandardItemModel(self)
        descriptions: list[str] = []              # the description of source row i (for the typo-tolerant word search)
        for rec in reversed(rows):
            if not rec.get("ctr"):
                continue
            descriptions.append(rec.get("description") or "")
            item = QStandardItem(self._label(rec))
            item.setData(rec["row"], Qt.ItemDataRole.UserRole)
            hay = " ".join(str(x) for x in (
                rec["ctr"], f"row {rec['row']}", rec.get("currency"), rec.get("revision"), rec.get("client"), rec.get("po"),
                rec.get("account"), rec.get("description"), rec.get("po_type"), rec.get("value"), rec.get("po_value"))
                if x not in (None, ""))
            item.setData(hay.lower(), _SEARCH_ROLE)
            model.appendRow(item)
        model.word_index = WordIndex(descriptions)
        model.word_index.positions = list(range(len(descriptions)))     # position in the list == source row of the model
        return model

    def _search_typed(self, r: _PORow, text: str) -> None:
        """Typing in the CTR box: filter the rows by the words typed and (re)open the list."""
        if self._search_model is None:
            self._ensure_tracker_rows()          # no tracker rows yet: read them (or say what is missing)
        r.proxy.set_query(text)
        r.completer.setCompletionPrefix(text)       # makes the completer re-read the filtered rows
        if r.proxy.rowCount():
            r.completer.complete()
        else:
            r.completer.popup().hide()

    def _on_completion(self, r: _PORow, index: QModelIndex) -> None:
        row = index.data(Qt.ItemDataRole.UserRole)
        label = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if row is not None:
            # the completer writes its own text into the box after this signal: pick the row once it has
            QTimer.singleShot(0, lambda: self._select_row(r, int(row), label))

    def _select_row(self, r: _PORow, row: int, label: str = "") -> None:
        """Make `row` the picked CTR of an item (it is added to the box's list when it is not in it yet)."""
        combo = r.ctr_combo
        i = combo.findData(row)
        if i < 0:
            rec = next((x for x in self._tracker_rows if x["row"] == row), None)
            if rec is None:
                return
            combo.addItem(label or self._label(rec), row)
            i = combo.count() - 1
        combo.setCurrentIndex(i)
        combo.setEditText(combo.itemText(i))
        self._on_ctr_changed(r)

    def _record(self, r: _PORow) -> dict | None:
        row = self._chosen_row(r)
        return next((rec for rec in self._tracker_rows if rec["row"] == row), None) if row else None

    @staticmethod
    def _chosen_row(r: _PORow) -> int | None:
        c = r.ctr_combo
        i = c.currentIndex()
        if i < 0 or c.currentText() != c.itemText(i):
            return None          # nothing picked, or the typed text no longer matches a picked row
        return c.itemData(i)

    def _on_ctr_changed(self, r: _PORow) -> None:
        r.ctr_combo.lineEdit().setCursorPosition(0)        # show the start of the long label, not its end
        rec = self._record(r)
        picked = rec["row"] if rec is not None else None
        if picked != r.picked:                    # another CTR: the CTR file picked for the old one no longer applies
            if picked is not None:
                log.info("PO %s item %s: CTR row %s picked (%s, %s%s)", r.po.po_number, r.item.number, picked,
                         rec["ctr"], rec.get("currency") or "?", f", rev {rec['revision']}" if rec.get("revision") else "")
            elif r.picked is not None:
                log.info("PO %s item %s: the picked CTR row %s is no longer picked", r.po.po_number, r.item.number, r.picked)
            r.picked = picked
            r.ctr_file = r.ctr_pricebook = r.ctr_file_ctr = ""
            r.file_btn.setText("CTR file…")
            r.file_btn.setToolTip("Optional: pick the CTR's own file to compare its pricebook number with the PO's. It "
                                  "is taken automatically from a CTR loaded in the batch above.")
        if rec is not None and not r.ctr_file:
            path = self._ctr_files().get(ctr_base(rec["ctr"]))
            if path:
                self._read_pricebook(r, path)
        self._refresh_note(r)
        for other in self._rows:
            if other is not r:
                self._refresh_note(other)     # a double choice shows on both

    def _choose_ctr_file(self, r: _PORow) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose the CTR file", "", "Excel (*.xlsx *.xlsm *.xls)")
        if path:
            self._read_pricebook(r, path)
            self._refresh_note(r)

    def _read_pricebook(self, r: _PORow, path: str) -> None:
        try:
            r.ctr_pricebook, r.ctr_file_ctr = read_ctr_file_info(path)
            r.ctr_file = path
            log.info("PO %s item %s: CTR file %s read — CTR %s, pricebook %s (PO's pricebook %s)", r.po.po_number,
                     r.item.number, Path(path).name, r.ctr_file_ctr or "?", r.ctr_pricebook or "?", r.item.pricebook or "?")
            if pricebook_differs(r.item.pricebook, r.ctr_pricebook):
                log.warning("PO %s item %s: pricebook differs — PO %s, CTR file %s", r.po.po_number, r.item.number,
                            r.item.pricebook, r.ctr_pricebook)
            r.file_btn.setText("✓ " + Path(path).stem[:10])
            r.file_btn.setToolTip(f"{path}\nCTR {r.ctr_file_ctr or '(not found)'} · Pricebook # {r.ctr_pricebook or '(none found)'}")
        except Exception as exc:
            log.exception("Reading the CTR's pricebook failed")
            r.ctr_pricebook = r.ctr_file_ctr = ""
            QMessageBox.warning(self, "CTR file", f"The pricebook number could not be read from that file:\n{exc}")

    # ── warnings ────────────────────────────────────────────────────────────

    def problems(self, r: _PORow) -> tuple[list[str], list[str]]:
        """(warnings, info lines) for one item — warnings are shown in red."""
        warn, info = [], []
        rec = self._record(r)
        if rec is None:
            warn.append("No CTR chosen — this item is skipped.")
        else:
            if rec.get("currency") and r.po.currency and rec["currency"].upper() != r.po.currency.upper():
                warn.append(f"The PO is in {r.po.currency}, the tracker row in {rec['currency']}.")
            if rec.get("value") is not None and r.item.amount is not None and abs(rec["value"] - r.item.amount) > 0.005 \
                    and (not rec.get("currency") or rec["currency"].upper() == r.po.currency.upper()):
                info.append(f"The CTR value is {_fmt_money(rec['value'])}, the PO {_fmt_money(r.item.amount)}.")
            row = rec["row"]
            if sum(1 for o in self._rows if self._chosen_row(o) == row) > 1:
                warn.append(f"Another item points at row {row} too.")
        if r.ctr_pricebook and pricebook_differs(r.item.pricebook, r.ctr_pricebook):
            warn.append(f"Pricebook differs: PO {r.item.pricebook}, CTR {r.ctr_pricebook}.")
        if not r.company_edit.text().strip():
            warn.append(f"Company not in the short-name list ({r.po.company}) — type its short name.")
        warn += [n[0].upper() + n[1:] + "." for n in r.po.notes if f"item {r.item.number}" in n or "total" in n]
        return warn, info

    def reminders(self, r: _PORow) -> list[str]:
        """Things to look at before writing — shown in yellow, never blocking."""
        rec = self._record(r)
        if rec is None:
            return []
        if not r.ctr_file:
            return ["Pricebook not checked — pick the CTR file (CTR file…) and check it before writing."]
        want = ctr_base(rec["ctr"])
        if not r.ctr_file_ctr:
            return ["The CTR number could not be read from the CTR file — check it is the right file."]
        if want and r.ctr_file_ctr != want:
            return [f"The CTR file is for {r.ctr_file_ctr}, the chosen row is {want} — check the file."]
        return []

    def _refresh_note(self, r: _PORow) -> None:
        if r.note is None:
            return
        warn, info = self.problems(r)
        parts = ([f'<span style="color:{RED}">{w}</span>' for w in warn]
                 + [f'<span style="color:{YELLOW}">{x}</span>' for x in self.reminders(r)]
                 + [f'<span style="color:{MUTED}">{i}</span>' for i in info])
        r.note.setText("<br>".join(parts))
        r.note.setToolTip(r.note.text())

    # ── writing ─────────────────────────────────────────────────────────────

    def _fields_of(self, r: _PORow) -> dict:
        f = {"po_number": r.po.po_number, "po_line": r.item.number}
        company = r.company_edit.text().strip()
        if company:
            f["company"] = company
        if r.item.account:
            f["account"] = r.item.account
        if r.item.delivery is not None:
            f["delivery"] = r.item.delivery
        if r.item.amount is not None:
            f["value"] = r.item.amount
        t = r.type_combo.currentText().strip()
        if t:
            f["po_type"] = t
        return f

    def _write(self) -> None:
        path = self._tracker_path().strip()
        if not path or not Path(path).is_file():
            log.warning("PO write refused: no usable tracker workbook (%r)", path)
            QMessageBox.warning(self, "Purchase orders", "Pick the tracker workbook first.")
            return
        picked = [r for r in self._rows if self._chosen_row(r) is not None]
        log.info("PO write requested: %d of %d item(s) have a CTR chosen, tracker %s, backup %s", len(picked),
                 len(self._rows), Path(path).name, "on" if self._backup() else "OFF")
        if not picked:
            log.warning("PO write refused: no item has a CTR chosen")
            QMessageBox.information(self, "Purchase orders", "Choose the CTR for at least one item first.")
            return
        rows_used = [self._chosen_row(r) for r in picked]
        if len(set(rows_used)) != len(rows_used):
            log.warning("PO write refused: two items point at the same tracker row (%s)", sorted(rows_used))
            QMessageBox.warning(self, "Purchase orders", "Two items point at the same tracker row — fix that first "
                                "(the red notes show which).")
            return
        if self._write_worker is not None and self._write_worker.isRunning():
            return
        try:
            fresh = {rec["row"]: rec for rec in read_tracker_rows(path)}
        except Exception as exc:
            log.exception("PO write stopped: the tracker could not be read before writing")
            QMessageBox.critical(self, "Purchase orders", f"The tracker could not be read:\n{exc}")
            return

        assignments, titles, conflicts = [], {}, {}
        for r in picked:
            row = self._chosen_row(r)
            rec = fresh.get(row)
            if rec is None:
                log.warning("PO write stopped: row %s of the tracker no longer exists", row)
                QMessageBox.warning(self, "Purchase orders", f"Row {row} no longer exists in the tracker — load again.")
                return
            fields = self._fields_of(r)
            idx = len(assignments)
            titles[idx] = f"PO {r.po.po_number} item {r.item.number} → {rec['ctr']} (row {row})"
            lines = []
            for name, new in fields.items():
                key, label = _FIELDS[name]
                old = rec.get(key)
                if old not in (None, "") and not _same(name, old, new):
                    lines.append((label, _show(name, old), _show(name, new)))
            if lines:
                conflicts[idx] = lines
                log.info("PO %s item %s → row %s: %d cell(s) already hold a different value: %s", r.po.po_number,
                         r.item.number, row, len(lines), "; ".join(f"{lab}: {old} -> {new}" for lab, old, new in lines))
            assignments.append({"row": row, "ctr": rec["ctr"], "currency": r.po.currency, "fields": fields,
                                "_source": r, "_conflict_names": [n for n in fields if (
                                    rec.get(_FIELDS[n][0]) not in (None, "") and not _same(n, rec.get(_FIELDS[n][0]), fields[n]))]})

        if conflicts:
            dlg = _OverwriteDialog(self, conflicts, titles)
            ok = dlg.exec() == QDialog.DialogCode.Accepted
            flags = dlg.replace_flags() if ok else {}
            dlg.deleteLater()
            if not ok:
                log.info("PO write cancelled in the 'values already in the tracker' window")
                return
            for idx, replace in flags.items():
                log.info("PO write: %s — old values %s", titles[idx], "REPLACED" if replace else "kept (only empty cells filled)")
                if not replace:
                    for n in assignments[idx]["_conflict_names"]:
                        assignments[idx]["fields"].pop(n, None)

        warnings = []
        for r in picked:
            warn, _ = self.problems(r)
            warnings += [f"PO {r.po.po_number} item {r.item.number}: {w}" for w in warn if "skipped" not in w]
        text = f"Write {len(assignments)} PO item(s) into the tracker?"
        if self._backup():
            text += "\nA timestamped backup copy is saved first."
        else:
            text += "\nThe backup copy is switched OFF."
        if warnings:
            text += "\n\nWarnings:\n• " + "\n• ".join(warnings)
        remind = [f"PO {r.po.po_number} item {r.item.number}: {m}" for r in picked for m in self.reminders(r)]
        for w_ in warnings:
            log.warning("PO write: warning shown — %s", w_)
        for m_ in remind:
            log.info("PO write: reminder shown — %s", m_)
        if remind:
            text += "\n\nCheck before writing:\n• " + "\n• ".join(remind)
        if QMessageBox.question(self, "Write purchase orders", text, QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel
                                ) != QMessageBox.StandardButton.Yes:
            log.info("PO write cancelled at the confirmation")
            return

        sources = [a.pop("_source") for a in assignments]
        for a in assignments:
            log.info("PO write: row %s (%s) <- %s", a["row"], a["ctr"], ", ".join(f"{k}={_show(k, v)!r}" for k, v in a["fields"].items()))
        for a in assignments:
            a.pop("_conflict_names", None)
        self._write_btn.setEnabled(False)
        self._write_worker = _PoWriteWorker(path, assignments, self._backup(), self)
        self._write_worker.finished_ok.connect(lambda w, s, b, src=sources: self._written(w, s, b, src))
        self._write_worker.error.connect(self._write_failed)
        self._write_worker.start()

    def _written(self, written: list, skipped: list, backup, sources: list) -> None:
        done = [sources[i] for i, _ctr, _row in written]
        log.info("PO write finished: %d written (rows %s), %d skipped, backup %s", len(written),
                 [row for _i, _c, row in written], len(skipped), backup.name if backup else "none")
        for _i, ctr, reason in skipped:
            log.warning("PO write: not written — %s: %s", ctr, reason)
        self._remember_types([r.type_combo.currentText() for r in done])
        for r in done:
            self._remove(r, silent=True)
        self.invalidate_tracker_rows()
        lines = [f"{len(written)} PO item(s) written."]
        if backup:
            lines.append(f"Backup: {backup}")
        if skipped:
            lines.append("")
            lines.append("Not written:")
            lines += [f"• {ctr}: {reason}" for _i, ctr, reason in skipped]
        lines.append("")
        lines.append("Excel recalculates the formulas (PO value USD, the lookup key) when the file is opened; if it "
                     "shows old numbers press Ctrl+Alt+Shift+F9.")
        QMessageBox.information(self, "Purchase orders", "\n".join(lines))
        self._write_btn.setEnabled(bool(self._rows))
        if self._rows:
            self._ensure_tracker_rows()

    def _write_failed(self, message: str) -> None:
        log.error("PO write failed, nothing lost (items kept in the list): %s", message)
        self._write_btn.setEnabled(bool(self._rows))
        QMessageBox.critical(self, "Purchase orders", message + "\n\nNothing was lost: your PO items (the chosen CTRs, "
                             "companies and Type of PO) are still in the list. Fix the problem (for example close the "
                             "tracker in Excel) and press Write again.")
