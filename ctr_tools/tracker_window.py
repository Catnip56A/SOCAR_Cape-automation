"""
ctr_tools/tracker_window.py

CTR Tracker tab for the SOCAR Cape desktop app.

Workflow:
  1. Pick the CTR Tracker workbook (.xlsm) once — remembered across runs.
  2. For each CTR (up to 10): browse to its generated output file, "Load
     Fields" reads client / CTR number / date / revision / description /
     value / currency straight off it (see ctr_tools.tracker). The
     Location dropdown auto-selects when the file's site text (cell B4)
     matches a configured site name, filling Project Code and the
     tracker's Onshore/Offshore/Georgia bucket — otherwise it's picked by
     hand. Every field stays editable before "Add to Batch".
  3. "Write to Tracker" writes each CTR into the existing tracker row
     whose CTR number column already contains its base number (currency
     suffix stripped — a human pre-creates that row by hand; this never
     creates or shifts rows). A CTR with no matching, still-empty row is
     skipped with a warning shown after the write. Optionally writes a
     timestamped backup of the workbook next to it first. The write runs
     on a background thread (see _WriteWorker) using the fast raw-XML
     patch in ctr_tools.tracker_fast — only the target sheet is
     touched, everything else in the .xlsm is copied byte-for-byte.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date as _date

from PySide6.QtCore import QSettings, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressDialog, QPushButton, QRadioButton,
    QScrollArea, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ctr_tools.tracker import (
    CTREntry, company_project_code, extract_ctr_data, job_type_options,
    location_options, match_site,
)
from ctr_tools.tracker_fast import IN_BATCH_CONFLICT_MARKER, write_entries_fast
from ctr_tools.window import _file_picker_row, _group_css, _highlight_row, _make_table, _ro_item

log = logging.getLogger(__name__)

PRIMARY = "#1976D2"
MUTED   = "#757575"
BORDER  = "#E0E0E0"

_MAX_BATCH = 10
_RETRY_COUNTDOWN_SECONDS = 3

_BATCH_HEADERS = [
    "Client", "CTR Number", "Date", "Location", "Project Code",
    "Job Type", "Description", "Value", "Currency", "Revision", "",
]
_BC_CLIENT, _BC_CTR_NO, _BC_DATE, _BC_LOCATION, _BC_PROJECT, \
    _BC_JOB_TYPE, _BC_DESC, _BC_VALUE, _BC_CURRENCY, _BC_REVISION, \
    _BC_REMOVE = range(11)


# ─────────────────────────────────────────────────────────────────────────────
# Background write worker
# ─────────────────────────────────────────────────────────────────────────────

class _WriteWorker(QThread):
    """Runs write_entries_fast() off the UI thread. It patches only the
    target sheet's raw XML and copies every other part of the .xlsm
    untouched (see tracker_fast.py), instead of round-tripping the whole
    workbook through openpyxl — ~3s instead of ~11s on the real tracker
    file, and it also stops openpyxl's data-validation/drawing/print-
    settings/calc-chain stripping on every save."""
    finished_ok = Signal(list, list, object)   # written[(idx, ctr_number, row)], skipped[(idx, ctr_number, reason)], backup_path | None
    error       = Signal(str)

    def __init__(self, tracker_path: str, entries: list[CTREntry], make_backup: bool,
                 allow_overwrite: bool, revision_mode: str, parent=None):
        super().__init__(parent)
        self._tracker_path = tracker_path
        self._entries = entries
        self._make_backup = make_backup
        self._allow_overwrite = allow_overwrite
        self._revision_mode = revision_mode

    def run(self):
        try:
            written, skipped, backup_path = write_entries_fast(
                self._tracker_path, self._entries,
                make_backup=self._make_backup, allow_overwrite=self._allow_overwrite,
                revision_mode=self._revision_mode,
            )
            self.finished_ok.emit(written, skipped, backup_path)
        except Exception as exc:
            log.exception("CTR Tracker write failed")
            self.error.emit(str(exc))


def _hdr_row(label: str, widget: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(6)
    lbl = QLabel(label)
    lbl.setFixedWidth(110)
    lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
    row.addWidget(lbl)
    row.addWidget(widget)
    return row


class CTRTrackerWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._batch: list[CTREntry] = []
        self._current_entry: CTREntry | None = None
        self._locations = location_options()
        self._workers: list[_WriteWorker] = []
        self._retry_tick_timer = QTimer(self)
        self._retry_tick_timer.setInterval(1000)
        self._retry_tick_timer.timeout.connect(self._on_retry_tick)
        self._retry_seconds_left = 0
        self._retry_round = 0
        self._retry_conflict_ctrs: list[str] = []
        self._build_ui()

    # ── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setSpacing(12)
        cl.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        self._build_workbook_section(cl)
        self._build_add_section(cl)
        self._build_batch_section(cl)

        cl.addStretch()

    def _build_workbook_section(self, parent_layout: QVBoxLayout):
        grp = QGroupBox("Tracker Workbook")
        grp.setStyleSheet(_group_css(PRIMARY))
        gl = QVBoxLayout(grp)
        row, self._tracker_edit = _file_picker_row(
            "CTR Tracker File", "Excel Macro-Enabled (*.xlsm);;Excel (*.xlsx)"
        )
        self._tracker_edit.textChanged.connect(self._refresh_batch_state)
        gl.addLayout(row)
        info = QLabel(
            "Each CTR is written into the existing row whose CTR number "
            "column already contains its number (e.g. \"CTR-26-217\", "
            "currency suffix not included — pre-create that row by hand "
            "first; Client/Location/Project Code may already be filled "
            "in, that's fine). A CTR with no matching row still empty in "
            "Date/Description/Value/Currency/Revision is skipped with a "
            "warning instead of guessing where it goes. Also skipped if the "
            "CTR's own Onshore/Offshore labor section doesn't match the "
            "selected Location — nothing is written until that's resolved. "
            "Where available, cost-breakdown totals (Labor, Equipment, "
            "Consumables, Customs & Transportation, 3rd Party) are also "
            "filled in from the CTR's summary section."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        gl.addWidget(info)

        self._backup_check = QCheckBox("Save a timestamped backup copy before writing")
        # Defaults on: a write can fall back to live Excel automation
        # (tracker_xlwings.py) to insert a row when no spare one is
        # available, and that path has no real-Excel verification behind
        # it — a backup is the only safety net against it corrupting the
        # shared tracker file.
        self._backup_check.setChecked(True)
        self._backup_check.setStyleSheet("font-size: 11px;")
        gl.addWidget(self._backup_check)

        self._overwrite_check = QCheckBox(
            "Overwrite rows that already have data (Date/Description/Value/Currency/Revision)"
        )
        self._overwrite_check.setChecked(False)
        self._overwrite_check.setToolTip(
            "Off (default): a matching row with any of those fields already filled in is "
            "skipped, not overwritten.\n"
            "On: the first matching row is used regardless of its current contents — "
            "existing data in it will be replaced with no undo besides the backup above."
        )
        self._overwrite_check.setStyleSheet("font-size: 11px; color: #C62828;")
        self._overwrite_check.toggled.connect(self._on_overwrite_toggled)
        gl.addWidget(self._overwrite_check)

        # Only meaningful once Overwrite is on — how to handle a CTR whose
        # matching row already has data in it.
        revision_row = QHBoxLayout()
        revision_row.setContentsMargins(20, 0, 0, 0)   # indented under the checkbox above
        revision_row.setSpacing(10)
        self._revision_group = QButtonGroup(self)
        self._overwrite_revision_radio = QRadioButton("Overwrite this row")
        self._overwrite_revision_radio.setToolTip(
            "Replace the existing row's data in place. Its Revision "
            "column (AI) and Comment (Y) are updated to this CTR's own "
            "revision number."
        )
        self._separate_revision_radio = QRadioButton("Add as separate revision")
        self._separate_revision_radio.setToolTip(
            "Leave the existing row untouched and write this CTR into a "
            "spare pre-created row sharing the same CTR number, if one is "
            "available. If none is available, a new row is inserted "
            "directly below the existing one (via Excel automation — "
            "requires Excel installed; see tracker_xlwings.py). Either "
            "way, this CTR's own revision number is written to both the "
            "Revision column (AI) and Comment (Y)."
        )
        self._overwrite_revision_radio.setChecked(True)
        self._revision_group.addButton(self._overwrite_revision_radio)
        self._revision_group.addButton(self._separate_revision_radio)
        for rb in (self._overwrite_revision_radio, self._separate_revision_radio):
            rb.setStyleSheet("font-size: 11px;")
            rb.setEnabled(False)
            revision_row.addWidget(rb)
        revision_row.addStretch()
        gl.addLayout(revision_row)

        parent_layout.addWidget(grp)

    def _on_overwrite_toggled(self, checked: bool):
        self._overwrite_revision_radio.setEnabled(checked)
        self._separate_revision_radio.setEnabled(checked)

    def _revision_mode(self) -> str:
        """'separate' or 'overwrite' — only meaningful when the Overwrite
        checkbox is on; write_entries_fast ignores it otherwise."""
        return "separate" if self._separate_revision_radio.isChecked() else "overwrite"

    def _build_add_section(self, parent_layout: QVBoxLayout):
        grp = QGroupBox("Add a CTR")
        grp.setStyleSheet(_group_css(PRIMARY))
        gl = QVBoxLayout(grp)
        gl.setSpacing(6)

        row, self._ctr_file_edit = _file_picker_row("CTR File", "Excel (*.xlsx *.xls)")
        gl.addLayout(row)

        load_btn = QPushButton("Load Fields From File")
        load_btn.setFixedHeight(32)
        load_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {PRIMARY}; color: white;
                border: none; border-radius: 6px; font-size: 12px; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: #1565C0; }}
        """)
        load_btn.clicked.connect(self._load_fields)
        gl.addWidget(load_btn)

        self._client_edit      = QLineEdit()
        self._client_edit.textChanged.connect(self._on_client_changed)
        self._ctr_no_edit      = QLineEdit()
        self._date_edit        = QLineEdit()
        self._date_edit.setPlaceholderText("YYYY-MM-DD")
        self._revision_edit    = QLineEdit()
        self._revision_edit.setPlaceholderText("(blank = none)")
        self._desc_edit        = QLineEdit()
        self._value_edit       = QLineEdit()
        self._value_warning    = QLabel()
        self._value_warning.setStyleSheet("color: #C62828; font-size: 10px;")
        self._value_warning.setVisible(False)
        self._currency_combo   = QComboBox()
        self._currency_combo.addItems(["AZN", "USD"])

        self._site_combo = QComboBox()
        self._site_combo.addItem("(select a location)")
        self._site_combo.addItems(sorted(self._locations.keys()))
        self._site_combo.currentTextChanged.connect(self._on_site_changed)

        self._project_code_edit = QLineEdit()
        self._tracker_location_combo = QComboBox()
        self._tracker_location_combo.addItems(["Onshore", "Offshore", "Georgia"])

        self._job_type_combo = QComboBox()
        self._job_type_combo.addItem("(select a job type)")
        self._job_type_combo.addItems(job_type_options())

        gl.addLayout(_hdr_row("Client:", self._client_edit))
        gl.addLayout(_hdr_row("CTR Number:", self._ctr_no_edit))
        gl.addLayout(_hdr_row("Date:", self._date_edit))
        gl.addLayout(_hdr_row("Revision:", self._revision_edit))
        gl.addLayout(_hdr_row("Description:", self._desc_edit))
        vrow = _hdr_row("Value:", self._value_edit)
        gl.addLayout(vrow)
        gl.addWidget(self._value_warning)
        gl.addLayout(_hdr_row("Currency:", self._currency_combo))
        gl.addLayout(_hdr_row("Location:", self._site_combo))
        gl.addLayout(_hdr_row("Project Code:", self._project_code_edit))
        gl.addLayout(_hdr_row("Tracker Location:", self._tracker_location_combo))
        gl.addLayout(_hdr_row("Job Type:", self._job_type_combo))

        self._add_batch_btn = QPushButton("+ Add to Batch")
        self._add_batch_btn.setFixedHeight(34)
        self._add_batch_btn.setStyleSheet(f"""
            QPushButton {{
                background: white; color: {PRIMARY};
                border: 1.5px solid {PRIMARY}; border-radius: 6px;
                font-size: 12px; font-weight: bold;
            }}
            QPushButton:hover    {{ background: #E3F2FD; }}
            QPushButton:disabled {{ color: {BORDER}; border-color: {BORDER}; }}
        """)
        self._add_batch_btn.clicked.connect(self._add_to_batch)
        gl.addWidget(self._add_batch_btn)

        parent_layout.addWidget(grp)

    def _build_batch_section(self, parent_layout: QVBoxLayout):
        grp = QGroupBox(f"Batch  (0 / {_MAX_BATCH})")
        grp.setStyleSheet(_group_css(PRIMARY))
        self._batch_group = grp
        gl = QVBoxLayout(grp)
        gl.setSpacing(6)

        self._batch_tbl = _make_table(_BATCH_HEADERS, stretch_col=_BC_DESC)
        self._batch_tbl.setFixedHeight(220)
        gl.addWidget(self._batch_tbl)

        self._write_btn = QPushButton("Write Batch to Tracker…")
        self._write_btn.setFixedHeight(40)
        self._write_btn.setEnabled(False)
        self._write_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {PRIMARY}; color: white;
                border: none; border-radius: 6px; font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover    {{ background-color: #1565C0; }}
            QPushButton:disabled {{ background-color: {BORDER}; color: #9E9E9E; }}
        """)
        self._write_btn.clicked.connect(self._write_batch)
        gl.addWidget(self._write_btn)

        retry_row = QHBoxLayout()
        self._retry_label = QLabel()
        self._retry_label.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._retry_label.setVisible(False)
        self._retry_cancel_btn = QPushButton("Cancel")
        self._retry_cancel_btn.setFixedHeight(22)
        self._retry_cancel_btn.setVisible(False)
        self._retry_cancel_btn.clicked.connect(self._cancel_retry_countdown)
        retry_row.addWidget(self._retry_label, 1)
        retry_row.addWidget(self._retry_cancel_btn)
        gl.addLayout(retry_row)

        parent_layout.addWidget(grp)

    # ── Load / Add flow ─────────────────────────────────────────────────────

    def _load_fields(self):
        path = self._ctr_file_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "No file selected", "Choose a CTR file first.")
            return
        try:
            entry = extract_ctr_data(path)
        except Exception as exc:
            log.exception("Failed to read CTR file %s", path)
            QMessageBox.critical(self, "Could not read file", str(exc))
            return

        self._current_entry = entry
        self._client_edit.setText(entry.client)
        self._ctr_no_edit.setText(entry.ctr_number)
        self._date_edit.setText(entry.ctr_date.isoformat() if entry.ctr_date else "")
        self._revision_edit.setText(entry.revision)
        self._desc_edit.setText(entry.description)
        self._value_edit.setText("" if entry.value is None else f"{entry.value:g}")
        if entry.currency in ("AZN", "USD"):
            self._currency_combo.setCurrentText(entry.currency)

        matched_site = match_site(entry.site_hint)
        self._site_combo.setCurrentText(matched_site or "(select a location)")

        if entry.value_is_estimate:
            self._value_warning.setText(
                "Couldn't read a computed total from this file (it may need to be "
                "opened and saved in Excel first) — enter the value manually."
            )
            self._value_warning.setVisible(True)
        else:
            self._value_warning.setVisible(False)

    def _on_site_changed(self, name: str):
        loc = self._locations.get(name)
        if loc:
            self._tracker_location_combo.setCurrentText(loc["tracker_location"])
            # A company-based Project Code (see _on_client_changed) takes
            # priority over the location's — don't let picking a site
            # silently clobber it back.
            if not company_project_code(self._client_edit.text()):
                self._project_code_edit.setText(loc["project_code"])

    def _on_client_changed(self, client: str):
        code = company_project_code(client)
        if code:
            self._project_code_edit.setText(code)
        else:
            # No company override applies (anymore) — fall back to
            # whatever the currently selected Location gives, same as if
            # it had just been picked. If no Location is selected either,
            # there's nothing to fall back to, so the field is left alone.
            loc = self._locations.get(self._site_combo.currentText())
            if loc:
                self._project_code_edit.setText(loc["project_code"])

    def _add_to_batch(self):
        if len(self._batch) >= _MAX_BATCH:
            QMessageBox.warning(
                self, "Batch full", f"At most {_MAX_BATCH} CTRs can be queued at once."
            )
            return

        ctr_no = self._ctr_no_edit.text().strip()
        if not ctr_no:
            QMessageBox.warning(self, "Missing CTR number", "Enter or load a CTR number first.")
            return
        if self._site_combo.currentIndex() == 0:
            QMessageBox.warning(self, "Missing location", "Select a Location for this CTR.")
            return
        if self._job_type_combo.currentIndex() == 0:
            QMessageBox.warning(self, "Missing job type", "Select a Job Type for this CTR.")
            return

        date_val = None
        date_txt = self._date_edit.text().strip()
        if date_txt:
            try:
                date_val = _date.fromisoformat(date_txt)
            except ValueError:
                QMessageBox.warning(self, "Invalid date", "Date must be in YYYY-MM-DD format.")
                return

        value_val = None
        value_txt = self._value_edit.text().strip()
        if value_txt:
            try:
                value_val = float(value_txt)
            except ValueError:
                QMessageBox.warning(self, "Invalid value", "Value must be a number.")
                return

        base = self._current_entry or CTREntry()
        entry = replace(
            base,
            client=self._client_edit.text().strip(),
            ctr_number=ctr_no,
            ctr_date=date_val,
            revision=self._revision_edit.text().strip(),
            description=self._desc_edit.text().strip(),
            value=value_val,
            currency=self._currency_combo.currentText(),
            site=self._site_combo.currentText(),
            project_code=self._project_code_edit.text().strip(),
            tracker_location=self._tracker_location_combo.currentText(),
            job_type=self._job_type_combo.currentText(),
        )
        self._batch.append(entry)
        self._append_batch_row(entry)
        self._refresh_batch_state()

        # Reset the whole add form for the next CTR — nothing here is
        # inferred well enough for one CTR's Location/Currency/Project
        # Code/Job Type to be a safe guess for the next one, and only
        # Site/Currency were ever refreshed by a later _load_fields
        # anyway (Project Code, Tracker Location and Job Type would
        # otherwise silently carry over unless the user noticed and
        # changed them by hand).
        self._ctr_file_edit.setText("")
        self._client_edit.setText("")
        self._ctr_no_edit.setText("")
        self._date_edit.setText("")
        self._revision_edit.setText("")
        self._desc_edit.setText("")
        self._value_edit.setText("")
        self._value_warning.setVisible(False)
        self._currency_combo.setCurrentIndex(0)
        self._site_combo.setCurrentIndex(0)
        self._project_code_edit.setText("")
        self._tracker_location_combo.setCurrentIndex(0)
        self._job_type_combo.setCurrentIndex(0)
        self._current_entry = None

    # ── Batch table ──────────────────────────────────────────────────────────

    def _append_batch_row(self, entry: CTREntry):
        tbl = self._batch_tbl
        r = tbl.rowCount()
        tbl.insertRow(r)
        tbl.setItem(r, _BC_CLIENT,   QTableWidgetItem(entry.client))
        tbl.setItem(r, _BC_CTR_NO,   QTableWidgetItem(entry.ctr_number))
        tbl.setItem(r, _BC_DATE,     QTableWidgetItem(entry.ctr_date.isoformat() if entry.ctr_date else ""))
        tbl.setItem(r, _BC_LOCATION, QTableWidgetItem(entry.tracker_location))
        tbl.setItem(r, _BC_PROJECT,  QTableWidgetItem(entry.project_code))
        tbl.setItem(r, _BC_JOB_TYPE, QTableWidgetItem(entry.job_type))
        tbl.setItem(r, _BC_DESC,     QTableWidgetItem(entry.description))
        tbl.setItem(r, _BC_VALUE,    QTableWidgetItem("" if entry.value is None else f"{entry.value:g}"))
        tbl.setItem(r, _BC_CURRENCY, QTableWidgetItem(entry.currency))
        tbl.setItem(r, _BC_REVISION, QTableWidgetItem(entry.revision))
        tbl.setItem(r, _BC_REMOVE,   _ro_item(""))

        rm_btn = QPushButton("✕")
        rm_btn.setFixedSize(24, 24)
        rm_btn.setToolTip("Remove this CTR from the batch")
        rm_btn.clicked.connect(lambda: self._remove_batch_row(rm_btn))
        tbl.setCellWidget(r, _BC_REMOVE, rm_btn)

        if entry.value_is_estimate:
            _highlight_row(tbl, r, matched=False)

    def _remove_batch_row(self, button: QPushButton):
        tbl = self._batch_tbl
        for r in range(tbl.rowCount()):
            if tbl.cellWidget(r, _BC_REMOVE) is button:
                tbl.removeRow(r)
                del self._batch[r]
                break
        self._refresh_batch_state()

    def _remove_written_from_batch(self, written: list[tuple[int, str, int]]) -> None:
        """Drops only the entries that were actually written, by their
        exact position in the batch that was submitted — anything skipped
        stays in the batch so it's easy to fix and retry (e.g. once a
        human adds the missing row). Matching by CTR number alone would
        be ambiguous whenever two entries in the same batch share one
        (e.g. two revisions of the same CTR — see IN_BATCH_CONFLICT_MARKER
        in tracker_fast.py): it could just as easily drop the entry that
        still needs writing and keep the one already written."""
        written_indices = {idx for idx, _ctr_number, _row in written}
        for r in reversed(range(len(self._batch))):
            if r in written_indices:
                del self._batch[r]
                self._batch_tbl.removeRow(r)

    def _refresh_batch_state(self):
        n = len(self._batch)
        self._batch_group.setTitle(f"Batch  ({n} / {_MAX_BATCH})")
        self._add_batch_btn.setEnabled(n < _MAX_BATCH)
        self._write_btn.setEnabled(n > 0 and bool(self._tracker_edit.text().strip()))

    # ── Write ────────────────────────────────────────────────────────────────

    def _write_batch(self):
        """The "Write Batch to Tracker…" button handler — always asks for
        confirmation and always starts a fresh retry-round budget, since
        this is the user explicitly choosing to write right now (as
        opposed to _start_write(skip_confirm=True), the automatic
        follow-up an in-batch conflict schedules for itself — see
        _maybe_start_retry_countdown)."""
        self._cancel_retry_countdown()
        self._retry_round = 0
        self._start_write(skip_confirm=False)

    def _start_write(self, *, skip_confirm: bool):
        tracker_path = self._tracker_edit.text().strip()
        if not tracker_path:
            QMessageBox.warning(self, "No tracker selected", "Choose the CTR Tracker workbook first.")
            return
        if not self._batch:
            return

        n = len(self._batch)
        make_backup = self._backup_check.isChecked()
        allow_overwrite = self._overwrite_check.isChecked()
        revision_mode = self._revision_mode()

        if not skip_confirm:
            backup_note = (
                "A timestamped backup will be saved next to the file first."
                if make_backup else
                "No backup copy will be made (enable the checkbox above to save one)."
            )
            if not allow_overwrite:
                overwrite_note = ""
            elif revision_mode == "overwrite":
                overwrite_note = (
                    "\n\n⚠ Overwrite mode is ON — a matching row's existing data will be "
                    "replaced, its Revision and Comment updated."
                )
            else:
                overwrite_note = (
                    "\n\n⚠ Overwrite mode is ON, \"Add as separate revision\" selected — a "
                    "matching row that already has data will be left alone, and this CTR "
                    "written into a spare row or a newly inserted one instead. A row insert "
                    "requires Excel to be installed on this machine."
                )
            reply = QMessageBox.question(
                self, "Write to Tracker",
                f"Write {n} CTR{'s' if n != 1 else ''} to:\n{tracker_path}\n\n{backup_note}"
                f"{overwrite_note}\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        dlg = QProgressDialog(
            "Writing to the tracker workbook…",
            None, 0, 0, self,
        )
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setWindowTitle("Writing…")
        dlg.setMinimumDuration(0)
        dlg.show()

        self._write_btn.setEnabled(False)
        self._add_batch_btn.setEnabled(False)

        worker = _WriteWorker(
            tracker_path, list(self._batch), make_backup, allow_overwrite,
            revision_mode, parent=self,
        )
        self._workers.append(worker)

        def _done(written: list, skipped: list, backup_path):
            dlg.close()
            self._workers.remove(worker)

            n_written, n_total = len(written), len(written) + len(skipped)
            lines = [f"Wrote {n_written} of {n_total} CTR{'s' if n_total != 1 else ''}."]
            for _idx, ctr_number, row in written:
                lines.append(f"  • {ctr_number} → row {row}")
            if skipped:
                lines.append("")
                lines.append("Skipped (still in the batch — fix and retry):")
                for _idx, ctr_number, reason in skipped:
                    lines.append(f"  • {ctr_number}: {reason}")
            if backup_path is not None:
                lines.append("")
                lines.append(f"Backup saved to:\n{backup_path.name}")
            QMessageBox.information(self, "Written", "\n".join(lines))

            self._remove_written_from_batch(written)
            self._refresh_batch_state()
            self._maybe_start_retry_countdown(skipped)

        def _err(message: str):
            dlg.close()
            self._workers.remove(worker)
            QMessageBox.critical(self, "Write failed", message)
            self._refresh_batch_state()

        worker.finished_ok.connect(_done)
        worker.error.connect(_err)
        worker.start()

    # ── In-batch-conflict retry ─────────────────────────────────────────────
    #
    # Two entries in the same batch for the same CTR number + currency
    # (e.g. two revisions of one AZN CTR) can't both be placed correctly
    # in a single pass — see IN_BATCH_CONFLICT_MARKER and _plan_insertions
    # in tracker_fast.py for why. The first is written normally; the rest
    # are skipped and — since _remove_written_from_batch only drops what
    # was actually written — are still sitting in the batch table
    # afterward. Retrying them in a follow-up batch resolves correctly,
    # because by then the first entry's data is genuinely on disk instead
    # of only planned. A batch of N same-CTR-currency entries needs up to
    # N-1 such rounds to fully resolve (round 2 can itself produce a new
    # conflict between what round 1 left behind), so this keeps
    # re-arming itself — capped by _retry_round so a persistent, unrelated
    # failure can't retry forever.

    def _maybe_start_retry_countdown(self, skipped: list[tuple[int, str, str]]):
        conflict_ctrs = [
            ctr_number for _idx, ctr_number, reason in skipped
            if IN_BATCH_CONFLICT_MARKER in reason
        ]
        if not conflict_ctrs or self._retry_round >= _MAX_BATCH or not self._batch:
            self._cancel_retry_countdown()
            return

        self._retry_round += 1
        self._retry_conflict_ctrs = conflict_ctrs
        self._retry_seconds_left = _RETRY_COUNTDOWN_SECONDS
        self._update_retry_label()
        self._retry_label.setVisible(True)
        self._retry_cancel_btn.setVisible(True)
        self._retry_tick_timer.start()

    def _update_retry_label(self):
        n = len(self._retry_conflict_ctrs)
        names = ", ".join(self._retry_conflict_ctrs)
        plural = "s" if n != 1 else ""
        verb = "share" if n != 1 else "shares"
        self._retry_label.setText(
            f"{n} CTR{plural} ({names}) {verb} a CTR number + currency with one just "
            f"written — retrying in {self._retry_seconds_left}s…"
        )

    def _on_retry_tick(self):
        self._retry_seconds_left -= 1
        if self._retry_seconds_left <= 0:
            self._retry_tick_timer.stop()
            self._retry_label.setVisible(False)
            self._retry_cancel_btn.setVisible(False)
            self._start_write(skip_confirm=True)
        else:
            self._update_retry_label()

    def _cancel_retry_countdown(self):
        self._retry_tick_timer.stop()
        self._retry_label.setVisible(False)
        self._retry_cancel_btn.setVisible(False)
        self._retry_round = 0
        self._retry_conflict_ctrs = []

    # ── Settings persistence ────────────────────────────────────────────────

    @staticmethod
    def _settings() -> QSettings:
        return QSettings("SOCAR", "CTRGenerator")

    def restore_settings(self) -> None:
        s = self._settings()
        val = s.value("paths/ctr_tracker_workbook", "")
        if val:
            self._tracker_edit.setText(val)
        self._backup_check.setChecked(s.value("ctr_tracker/make_backup", True, type=bool))
        # Overwrite mode is deliberately NOT remembered across restarts — it should
        # always come up off, so it can never be silently left on from a prior session.
        self._refresh_batch_state()

    def save_settings(self) -> None:
        s = self._settings()
        s.setValue("paths/ctr_tracker_workbook", self._tracker_edit.text())
        s.setValue("ctr_tracker/make_backup", self._backup_check.isChecked())
