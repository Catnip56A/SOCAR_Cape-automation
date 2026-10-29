"""
ctr_tools/window.py

CTR Generator dialog for the SOCAR Cape desktop app.

Layout (top → bottom inside a QScrollArea):
  [Section 1] Source Files — AZN/USD templates + Combined DB (shared) + two
                sub-tabs:
                "Pricebook-Based"   — AZN Pricebook / SAGE Export / USD
                                       Pricebook pickers, each its own file;
                                       manpower matches AZN, consumables
                                       match SAGE, equipment matches the USD
                                       Pricebook (via the Equipment Names DB
                                       bridge in Combined DB, when selected)
                "CTR Request-Based" — reads the CTR Request sheet from the
                                       Combined DB, fills header fields and
                                       the three tables below
  [Section 2] Manpower / Equipment / Consumables — one table per category.
                Each row shows what was requested next to its pricebook/SAGE
                match, and IS the row used for CTR generation — there is no
                separate "add to CTR" copy step. A row is included in the
                generated CTR only when its Status starts with "✓"
                ("✓ Matched" via pricebook/SAGE, or "✓ Manual" for a
                hand-added row); "✗ No match" rows are skipped at generation
                time so unmatched requests never silently appear at rate 0.
  [Section 3] Generate — CTR header info, job ref, output folder,
                          Generate button.

Worker thread follows the same ParseWorker pattern used in app.py.
"""

from __future__ import annotations

import json
import html
import logging
import time
import weakref
import re
from datetime import date as _date, datetime as _datetime
from pathlib import Path

import pandas as pd
from ctr_tools import __version__ as _VERSION
from common.formula_widgets import FormulaDoubleSpinBox, install_formula_edit
from common.formula import FormulaError, evaluate as evaluate_formula, format_number, is_formula
from PySide6.QtCore import (
    QEvent, Qt, QItemSelectionModel, QObject, QPoint, QRunnable, QSettings, QThread, QThreadPool, QTimer, Signal,
)
from PySide6.QtGui import QBrush, QColor, QCursor, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QListWidget, QMenu, QMessageBox, QProgressDialog, QPushButton, QScrollArea,
    QSizePolicy, QStyle, QStyleOptionViewItem, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QTabWidget,
    QToolButton, QToolTip, QVBoxLayout, QWidget, QWidgetAction,
)

from ctr_tools.aliases import (
    load_aliases, get_alias, set_alias, delete_alias, save_aliases,
)
from ctr_tools.desc_renames import (
    load_desc_renames, get_desc_rename, set_desc_rename, delete_desc_rename,
    save_desc_renames,
)
from ctr_tools.config import CFG
from stats import cost_stats
from common.find_bar import install_find
from common.hover_text import install_elided_tooltips
from stats.stats_view import StatsPanel
from ctr_tools.presets import load_presets, save_presets, _PRESET_FIELDS
from ctr_tools.source_choices import load_choices, save_choices
from ctr_tools.builder_azn import (
    activities_label,
    build_azn,
    is_support_manpower,
    strip_support_keyword,
)
from ctr_tools.builder_usd import build_usd
from ctr_tools.builder_pdms import (
    attention_from_requester, build_pdms, compute_pdms, pdms_defaults, salutation_from_attention,
)
from ctr_tools.pdms_overview import add_overview_sheet
from ctr_tools.parser import (
    parse_azn_pricebook, parse_usd_pricebook, parse_sage,
    parse_ctr_request, parse_equip_names_db, parse_encoding_info,
)
from ctr_tools.pdf_exporter import export_to_pdf
from ctr_tools import cbar_rates
from ctr_tools.tasks import TASKS, Job

log = logging.getLogger(__name__)

# ── colour palette (matches app.py) ──────────────────────────────────────────
PRIMARY   = "#1976D2"
MR_COLOR  = "#1565C0"
CTR_COLOR = "#00695C"
MUTED     = "#757575"
BORDER    = "#E0E0E0"
MR_LIGHT  = "#E3F2FD"
CTR_LIGHT = "#E0F2F1"

_DEFAULT_MARKUP_PCT = CFG["usd_template"]["markup_rate"] * 100   # spinbox default, e.g. 6.5

# What a preset stores, named as the fields are labelled in this UI. Built
# from presets._PRESET_FIELDS rather than hardcoded, so adding a field to a
# preset updates what the Presets label claims it saves instead of letting
# the two drift apart. An unmapped key falls back to its raw name, which is
# ugly enough to notice.
_PRESET_FIELD_LABELS = {
    "client":          "Client",
    "sub_client":      "Sub-Client",
    "location":        "Location",
    "scope":           "Scope",
    "revision":        "Revision",
    "project_type":    "Project Type",
    "job_ref":         "Job Ref",
    "output_dir":      "Output folder",
    "markup_rate_pct": "Markup Rate",
}
_PRESET_SAVES = ", ".join(_PRESET_FIELD_LABELS.get(f, f) for f in _PRESET_FIELDS)
_PRESET_OMITS = (
    "Date, Contract No (AZN/USD) and Comments are not saved — they belong to "
    "one specific document, so loading a preset never overwrites them. Line "
    "items (manpower, equipment, consumables, transport, scaffold) aren't "
    "saved either."
)

# ── unified manpower table column indices ─────────────────────────────────────
# One table does double duty: shows the CTR Request match AND is the direct
# source for CTR generation — there is no separate "add to CTR" copy step.
# Type/Shift/Shift Type together select the correct AZN pricebook rate —
# the stock code encodes all three (e.g. NOV-NAT-OFF-12 = Night, Overtime,
# Offshore); see _decode_azn_stock_code / _match_azn_labor below.
_MP_TYPE      = 0   # "Onshore" / "Offshore" — editable
_MP_SHIFT     = 1   # "Day" / "Night" — editable
_MP_SHIFTTYPE = 2   # "Normal" / "Overtime" / "Rotational" / "Standby" — editable
_MP_ENC       = 3   # ENCODING, e.g. "NS-NAT-OFF-12" — from the request, or chosen from the role's valid variants
_MP_DESC      = 4   # description — editable
_MP_MATCH     = 5   # match-by search key (work name, or a literal stock code override) — editable
_MP_MDESC     = 6   # matched pricebook item + stock code — read-only
_MP_NUMEMP    = 7   # num employees — editable
_MP_QTY       = 8   # quantity in hours (working days × hours/shift) — editable
_MP_UOM       = 9   # UOM — always "Hours" for manpower, read-only
_MP_RATE      = 10  # rate AZN — editable (auto-filled from match, overridable)
_MP_TOTAL     = 11  # read-only, computed
_MP_STATUS    = 12  # read-only: "✓ Matched" / "✓ Manual" / "✗ No match — ..."
_MP_NAT       = 13  # nationality — editable

_MANPOWER_HEADERS = [
    "Type", "Shift", "Shift Type", "Encoding", "Description", "Match By", "Matched Item",
    "Num\nEmployees", "Quantity (Hrs)", "UOM", "Rate AZN", "Total AZN", "Status", "Nationality",
]

# ── unified equipment table column indices ────────────────────────────────────
_EQ_DESC   = 0
_EQ_CODE   = 1
_EQ_MATCH  = 2
_EQ_MDESC  = 3
_EQ_QTY    = 4
_EQ_UNIT   = 5
_EQ_RATE   = 6
_EQ_DAYS   = 7
_EQ_TOTAL  = 8
_EQ_STATUS = 9

_EQUIPMENT_HEADERS = [
    "Description", "Stock Code", "Match By", "Matched Item",
    "Quantity", "Unit", "Rate/Day USD", "Days", "Total USD", "Status",
]

# ── unified consumables table column indices ──────────────────────────────────
_CS_DESC   = 0
_CS_CODE   = 1
_CS_MATCH  = 2
_CS_MDESC  = 3
_CS_QTY    = 4
_CS_UNIT   = 5
_CS_PRICE  = 6
_CS_TOTAL  = 7
_CS_STATUS = 8

_CONSUMABLES_HEADERS = [
    "Description", "Stock Code", "Match By", "Matched Item",
    "Quantity", "Unit", "Unit Price USD", "Total USD", "Status",
]

# ── transport table column indices (AZN "Third Party Activities") ────────────
_TR_TYPE   = 0   # transport type exactly as the CTR Request asked for it
_TR_DESC   = 1   # description written into the CTR — editable
_TR_QTY    = 2
_TR_DUR    = 3
_TR_UOM    = 4   # what the duration counts: "Days", "Trips", …
_TR_RATE   = 5
_TR_MARKUP = 6   # per-line mark-up, shown as a percentage
_TR_TOTAL  = 7   # read-only, computed

_TRANSPORT_HEADERS = [
    "Requested Type", "Description", "Quantity", "Duration", "Duration UOM",
    "Rate AZN", "Mark Up %", "Total AZN",
]

# What the headers say in PDMS mode: a price is in its own cell's currency (the cell shows the
# currency's sign), so the fixed AZN / USD goes from the Rate headers; every Total is in manats.
_PDMS_HEADERS = {
    "manpower":    {_MP_QTY: "Quantity (Days)", _MP_RATE: "Rate/Day", _MP_TOTAL: "Total AZN"},
    "equipment":   {_EQ_RATE: "Rate/Day", _EQ_TOTAL: "Total AZN"},
    "consumables": {_CS_PRICE: "Unit Price", _CS_TOTAL: "Total AZN"},
    "transport":   {_TR_RATE: "Rate", _TR_TOTAL: "Total AZN"},
}


# ─────────────────────────────────────────────────────────────────────────────
# Worker thread
# ─────────────────────────────────────────────────────────────────────────────

def stoppable_progress_dialog(parent, text: str, title: str, job: Job) -> QProgressDialog:
    """A modal busy dialog whose button asks `job` to stop after the task that
    is running now (see ctr_tools/tasks.py) — the dialog stays up until the
    worker really finishes, so the user can see it wind down."""
    dlg = QProgressDialog(text, "Stop after current step", 0, 0, parent)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setAutoReset(False)
    dlg.setAutoClose(False)
    btn = dlg.findChild(QPushButton)

    def _stop():
        job.request_stop()
        if btn is not None:
            btn.setEnabled(False)
        dlg.setLabelText("Stopping after the current step…")

    if btn is not None:
        try:
            btn.clicked.disconnect()   # the dialog's own cancel() would just hide it
        except (RuntimeError, TypeError):
            pass
        btn.clicked.connect(_stop)
    dlg.canceled.connect(_stop)       # Esc / window close count as Stop too
    dlg.show()
    return dlg


class CTRWorker(QThread):
    progress = Signal(str)
    finished = Signal(list)   # list[str] of output file paths
    error    = Signal(str)

    def __init__(
        self,
        support_rows:  list[dict],
        other_rows:    list[dict],
        equip_rows:    list[dict],
        consump_rows:  list[dict],
        azn_tpl:       str,
        usd_tpl:       str,
        output_dir:    str,
        job_ref:       str,
        third_party_rows: list[dict] | None = None,
        header_azn:    dict | None = None,
        header_usd:    dict | None = None,
        markup_rate:   float | None = None,
        pdms: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        # PDMS proposal mode: {"template", "header", "rates", "hotbed"}. The AZN / USD Excel CTRs are
        # then not written; the proposal (and its PDF, where LibreOffice can) is.
        self._pdms = pdms
        self._support_rows  = support_rows
        self._other_rows    = other_rows
        self._equip_rows    = equip_rows
        self._consump_rows  = consump_rows
        self._third_party_rows = list(third_party_rows or [])
        self._azn_tpl       = azn_tpl
        self._usd_tpl       = usd_tpl
        self._output_dir    = output_dir
        self._job_ref       = job_ref
        self._header_azn    = header_azn
        self._header_usd    = header_usd
        self._markup_rate   = markup_rate
        # Ordered steps, shown in the F12 Inspector. A stop request is only
        # honoured between steps, so a spreadsheet is never cut off halfway.
        self.job: Job = TASKS.begin_job(f"Generate CTR {job_ref}", (
            ["Write PDMS proposal", "Export PDMS PDF"] if pdms else [
                "Write AZN spreadsheet", "Write USD spreadsheet",
                "Export AZN PDF", "Export USD PDF",
            ]))
        self.stopped = False

    def _run_pdms(self, out: Path, job: Job, produced: list, stop_here) -> None:
        """PDMS proposal mode: the Word proposal, then (best effort) its PDF."""
        job.start(0)
        self.progress.emit("Writing PDMS proposal…")
        pdms = self._pdms
        docx_path = build_pdms(
            self._support_rows, self._other_rows, self._equip_rows, self._consump_rows,
            pdms["template"], out, pdms["header"], rates=pdms["rates"],
            markup_rate=self._markup_rate or 0.0, third_party_rows=self._third_party_rows,
            hotbed=pdms.get("hotbed"), currencies=pdms.get("currencies"),
        )
        log.info("CTRWorker: PDMS proposal written: %s", docx_path)
        cbar_rates.log_usage(docx_path.name, pdms["header"].get("prp_number", ""), pdms["rates_used"])
        job.done(0)
        produced.append(str(docx_path))
        if stop_here(1):
            return
        job.start(1)
        self.progress.emit("Exporting PDMS PDF via LibreOffice…")
        try:
            produced.append(str(export_to_pdf(docx_path, out)))
            job.done(1)
        except RuntimeError as e:
            log.warning("CTRWorker: PDMS PDF export skipped: %s", e)
            produced.append(f"[PDF skipped: {e}]")
            job.done(1, "PDF skipped")
        job.finish()
        self.finished.emit(produced)

    def run(self):
        log.info(
            "CTRWorker: job_ref=%s support=%d other=%d equip=%d consumables=%d "
            "third_party=%d output_dir=%s",
            self._job_ref, len(self._support_rows), len(self._other_rows),
            len(self._equip_rows), len(self._consump_rows),
            len(self._third_party_rows), self._output_dir,
        )
        job = self.job
        step = 0
        produced: list[str] = []

        def _stop_here(next_step: int) -> bool:
            """Between steps only: finish cleanly with what is already saved."""
            if not job.should_stop():
                return False
            job.skip_remaining(next_step)
            job.finish()
            self.stopped = True
            log.info("CTRWorker: stopped on request after step %d; saved: %s", next_step, produced)
            self.finished.emit(produced)
            return True

        try:
            out = Path(self._output_dir)

            if self._pdms:
                self._run_pdms(out, job, produced, _stop_here)
                return

            step = 0
            job.start(step)
            self.progress.emit("Writing AZN CTR spreadsheet…")
            azn_xlsx = build_azn(
                self._support_rows, self._other_rows, self._azn_tpl, out, self._job_ref,
                header=self._header_azn, third_party_rows=self._third_party_rows,
            )
            log.info("CTRWorker: AZN spreadsheet written: %s", azn_xlsx)
            job.done(step)
            produced.append(str(azn_xlsx))
            if _stop_here(1):
                return

            step = 1
            job.start(step)
            self.progress.emit("Writing USD CTR spreadsheet…")
            usd_xlsx = build_usd(
                self._equip_rows, self._consump_rows, self._usd_tpl, out, self._job_ref,
                header=self._header_usd, markup_rate=self._markup_rate,
            )
            log.info("CTRWorker: USD spreadsheet written: %s", usd_xlsx)
            job.done(step)
            produced.append(str(usd_xlsx))
            if _stop_here(2):
                return

            step = 2
            job.start(step)
            self.progress.emit("Exporting AZN PDF via LibreOffice…")
            try:
                azn_pdf = export_to_pdf(azn_xlsx, out)
                azn_pdf_str = str(azn_pdf)
                job.done(step)
            except RuntimeError as e:
                log.warning("CTRWorker: AZN PDF export skipped: %s", e)
                azn_pdf_str = f"[PDF skipped: {e}]"
                job.done(step, "PDF skipped")
            produced.append(azn_pdf_str)
            if _stop_here(3):
                return

            step = 3
            job.start(step)
            self.progress.emit("Exporting USD PDF via LibreOffice…")
            try:
                usd_pdf = export_to_pdf(usd_xlsx, out)
                usd_pdf_str = str(usd_pdf)
                job.done(step)
            except RuntimeError as e:
                log.warning("CTRWorker: USD PDF export skipped: %s", e)
                usd_pdf_str = f"[PDF skipped: {e}]"
                job.done(step, "PDF skipped")
            produced.append(usd_pdf_str)

            log.info("CTRWorker: finished successfully")
            job.finish()
            self.finished.emit(produced)

        except Exception as exc:
            log.exception("CTRWorker failed")
            job.failed(step, str(exc))
            job.skip_remaining(step + 1, "not run")
            job.finish()
            self.error.emit(str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Async file-loading workers
# ─────────────────────────────────────────────────────────────────────────────

class _LoadSignals(QObject):
    """Carries the result signal for _LoadTask (QRunnable can't have signals)."""
    result = Signal(str, object)   # (task_name, DataFrame | Exception)


class _TaskStopped(Exception):
    """Result of a load task that was skipped because the user asked to stop
    after the task that was already running (see ctr_tools/tasks.py)."""


class _LoadTask(QRunnable):
    """Runs one file-parse function on a pool thread, then emits result."""
    def __init__(self, name: str, fn, signals: _LoadSignals,
                 job: Job | None = None, index: int = 0):
        super().__init__()
        self._name    = name
        self._fn      = fn
        self._signals = signals
        self._job     = job
        self._index   = index
        self.setAutoDelete(True)

    def run(self):
        job, i = self._job, self._index
        # Checked before the task starts, never inside it.
        if job is not None and job.should_stop():
            job.skip(i)
            self._signals.result.emit(self._name, _TaskStopped("stopped before it started"))
            return
        if job is not None:
            job.start(i)
        try:
            result = self._fn()
        except Exception as exc:
            if job is not None:
                job.failed(i, str(exc))
            self._signals.result.emit(self._name, exc)
            return
        if job is not None:
            job.done(i)
        self._signals.result.emit(self._name, result)


class _CbarWorker(QThread):
    """Fetches today's CBAR rates off the main thread (a task in the F12 Inspector)."""
    finished = Signal(dict)       # {code: {"rate", "date", "warnings", "error"}}
    error    = Signal(str)

    def __init__(self, codes: list, parent=None):
        super().__init__(parent)
        self._codes = list(codes)
        self.job = TASKS.begin_job("Fetch CBAR rates (" + ", ".join(self._codes) + ")",
                                   ["Download CBAR rates"], stoppable=False)

    def run(self):
        self.job.start(0)
        try:
            results = cbar_rates.fetch_rates(self._codes)
        except Exception as exc:
            self.job.failed(0, str(exc))
            self.job.finish()
            self.error.emit(str(exc))
            return
        self.job.done(0)
        self.job.finish()
        self.finished.emit(results)


class _CTRRequestWorker(QThread):
    """Parses a CTR Request file off the main thread."""
    finished = Signal(dict)
    error    = Signal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path
        self.job = TASKS.begin_job("Load CTR Request", ["Read CTR Request sheet"], stoppable=False)

    def run(self):
        self.job.start(0)
        try:
            data = parse_ctr_request(self._path)
        except Exception as exc:
            self.job.failed(0, str(exc))
            self.job.finish()
            self.error.emit(str(exc))
            return
        self.job.done(0)
        self.job.finish()
        self.finished.emit(data)


# At most 3 file-parse tasks run concurrently — limits RAM and disk contention
# when the user loads many large Excel files at once.
_LOAD_POOL = QThreadPool()
_LOAD_POOL.setMaxThreadCount(3)


# ─────────────────────────────────────────────────────────────────────────────
# UI helpers
# ─────────────────────────────────────────────────────────────────────────────

def _group_css(color: str) -> str:
    return f"""
        QGroupBox {{
            font-weight: bold; color: {color};
            border: 1.5px solid {color}; border-radius: 6px;
            margin-top: 10px; padding-top: 4px; background: white;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin; left: 10px;
            padding: 0 4px; background: white;
        }}
    """


_JUMP_ROLE = Qt.UserRole + 5   # True on the one cell "Go to missing value" last jumped to
_CUR_ROLE = Qt.UserRole + 50   # a Rate / Price cell's own currency code (None = its category's); PDMS mode
_FORMULA_ROLE = Qt.UserRole + 51   # (formula text, the value text it gave, unit); live only while the cell still holds that value


class _JumpOverlayDelegate(QStyledItemDelegate):
    """Default cell painting, plus an orange overlay and border on the cell
    "Go to missing value" jumped to. It is drawn *over* the cell, because the
    blue selection of the row would otherwise hide any cell background."""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        self._paint_jump(painter, option, index)

    @staticmethod
    def _paint_jump(painter, option, index):
        f = index.data(_FORMULA_ROLE)
        if f and str(index.data(Qt.ItemDataRole.EditRole)) == f[1]:       # a formula cell: small blue corner
            painter.save()
            r = option.rect
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#1976D2"))
            painter.drawPolygon([QPoint(r.right() - 6, r.top() + 1), QPoint(r.right() - 1, r.top() + 1),
                                 QPoint(r.right() - 1, r.top() + 6)])
            painter.restore()
        if index.data(_JUMP_ROLE):
            painter.save()
            r = option.rect.adjusted(1, 1, -1, -1)
            painter.fillRect(r, QColor(255, 167, 38, 225))
            painter.setPen(QPen(QColor("#E65100"), 2))
            painter.drawRect(r)
            painter.restore()


class _DaysViewMixin:
    """Number cells that take formulas ("=25/12"), and the manpower days view.

    A formula is kept beside the value (_FORMULA_ROLE) and is live only while the cell still holds the
    value it gave, so anything else that rewrites the cell (a re-match, a reload) retires it by itself.
    PDMS mode shows a manpower row in days and a day rate (the proposal's own figures) while the table
    keeps hours and the hourly rate; a formula typed there is in days. The owner's _days_view(index) says
    what to show; a row without a shift length stays in hours and gets a red "!" whose tooltip says why."""

    def _item(self, index):
        return self.parent().item(index.row(), index.column())

    # What an editor was opened on, per cell. Not kept on the editor: the view hands the delegate a fresh
    # Python wrapper of its (C++) editor each time, so an attribute set on one is gone by setModelData.
    def _remember(self, index, text: str) -> None:
        self.__dict__.setdefault("_opened", {})[(index.row(), index.column())] = text

    def _recall(self, index) -> str | None:
        return self.__dict__.setdefault("_opened", {}).pop((index.row(), index.column()), None)

    def _live_formula(self, index):
        """(formula, value, unit) while the cell holds the value that formula gave, else None."""
        item = self._item(index)
        d = item.data(_FORMULA_ROLE) if item is not None else None
        return d if d and item.text() == d[1] else None

    def _unit(self, index):
        v = self._owner._days_view(index)
        return "days" if v and v["ph"] and index.column() in (_MP_QTY, _MP_RATE) else None

    def _paint_bang(self, painter, option, index, left: bool) -> None:
        v = self._owner._days_view(index)
        if not v or not v["problem"]:
            return
        painter.save()
        f = painter.font()
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor("#C62828"))
        flag = Qt.AlignmentFlag.AlignLeft if left else Qt.AlignmentFlag.AlignRight
        painter.drawText(option.rect.adjusted(3, 0, -3, 0), int(flag | Qt.AlignmentFlag.AlignVCenter), "!")
        painter.restore()

    def helpEvent(self, event, view, option, index):
        if event.type() == QEvent.Type.ToolTip:
            tip = self._owner._cell_hint(index, self._live_formula(index), self._unit(index))
            if tip:
                QToolTip.showText(event.globalPos(), tip, view)
                return True
        return super().helpEvent(event, view, option, index)

    def _editor_text(self, index) -> str:
        """What the editor opens on: the live formula (if written in the way the cell is shown now),
        else the shown number at full precision, else the stored text."""
        live = self._live_formula(index)
        if live and live[2] == self._unit(index):
            return live[0]
        v = self._owner._days_view(index)
        return v["edit"] if v else str(index.data(Qt.ItemDataRole.EditRole) or "")

    def _commit(self, model, index, typed: str, initial: str | None) -> None:
        """Typed text -> the cell. A formula is worked out and kept; days / a day rate go back to hours /
        an hourly rate; nothing is written when the box was left as it was opened (no float noise)."""
        if typed == initial:
            return
        tbl, row, col = self.parent(), index.row(), index.column()
        v = self._owner._days_view(index)
        ph = v["ph"] if v else None
        formula = None
        if is_formula(typed):
            try:
                n = evaluate_formula(typed)
            except FormulaError as exc:
                QToolTip.showText(QCursor.pos(), f"Formula not accepted: {exc}. The cell keeps its value.", tbl)
                return
            formula, text = typed.strip(), None
        else:
            try:
                n = float(typed)
            except ValueError:
                n, text = None, typed                                # not a number: stored as typed, as before
            else:
                text = typed if not ph else None
        if n is not None and text is None:
            back = (n * ph if col == _MP_QTY else n / ph) if ph else n
            text = format_number(back)
        item = tbl.item(row, col)
        if item is not None:                                         # set silently; the text change below notifies
            was = tbl.blockSignals(True)
            item.setData(_FORMULA_ROLE, (formula, text, "days" if ph else None) if formula else None)
            tbl.blockSignals(was)
        model.setData(index, text, Qt.ItemDataRole.EditRole)


class _DaysQtyDelegate(_DaysViewMixin, _JumpOverlayDelegate):
    """A manpower number cell: formulas, and for Quantity / UOM days and "Days" in PDMS mode."""

    def __init__(self, parent, owner):
        super().__init__(parent)
        self._owner = owner

    def paint(self, painter, option, index):
        v = self._owner._days_view(index)
        if v is None:
            return super().paint(painter, option, index)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = v["shown"]
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        self._paint_jump(painter, option, index)
        if index.column() == _MP_QTY:
            self._paint_bang(painter, option, index, left=False)

    def setEditorData(self, editor, index):
        if index.column() == _MP_UOM or not hasattr(editor, "setText"):
            return super().setEditorData(editor, index)
        text = self._editor_text(index)
        editor.setText(text)
        self._remember(index, text)
        editor.selectAll()

    def setModelData(self, editor, model, index):
        if index.column() == _MP_UOM or not hasattr(editor, "text"):
            return super().setModelData(editor, model, index)
        self._commit(model, index, editor.text(), self._recall(index))


class _MoneyDelegate(_DaysViewMixin, _JumpOverlayDelegate):
    """A Rate / Price cell in PDMS mode: the number in the left half and the currency in the right
    half, drawn as the currency's sign (₼ $ £ €). The editor is a number box plus a drop-down of
    the currency codes (AZN, USD, GBP …) that are turned on. Outside PDMS mode it is an ordinary cell."""

    NON_RECHARGEABLE = "NON-RECHARGEABLE"

    def __init__(self, parent, owner, key: str):
        super().__init__(parent)
        self._owner, self._key = owner, key
        self._popup_next = False

    def _active(self) -> bool:
        return self._owner._pdms_on()

    def parts(self, index) -> tuple[str, str]:
        """(the number as typed, the currency's sign); no sign for a text such as NONRECHARG."""
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        v = self._owner._days_view(index) if self._key == "manpower" else None
        if v:
            text = v["shown"]
        try:
            float(text)
        except ValueError:
            return text, ""
        code = str(index.data(_CUR_ROLE) or self._owner._cat_currency(self._key)).upper()
        return text, cbar_rates.currency_sign(code)

    def paint(self, painter, option, index):
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if text.strip().upper().startswith("NONRECH"):
            # A non-rechargeable line (the cell holds the sentinel NONRECHARG): no number, no currency —
            # the words, in capitals, from the left edge of the whole cell. In every mode.
            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            opt.text = ""
            style = opt.widget.style() if opt.widget else QApplication.style()
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
            area = option.rect.adjusted(4, 0, -4, 0)
            painter.save()
            painter.setPen(option.palette.highlightedText().color()
                           if option.state & QStyle.StateFlag.State_Selected else option.palette.text().color())
            painter.drawText(area, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             painter.fontMetrics().elidedText(self.NON_RECHARGEABLE, Qt.TextElideMode.ElideRight,
                                                              area.width()))
            painter.restore()
            self._paint_jump(painter, option, index)
            return
        if not self._active():
            return super().paint(painter, option, index)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        number, sign = self.parts(index)
        half = option.rect.width() // 2
        left = option.rect.adjusted(4, 0, -(option.rect.width() - half) - 2, 0)
        right = option.rect.adjusted(half + 4, 0, -2, 0)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setPen(option.palette.highlightedText().color() if selected else option.palette.text().color())
        painter.drawText(left, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), number)
        if sign:
            painter.setPen(option.palette.highlightedText().color() if selected else QColor(MUTED))
            painter.drawText(right, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), sign)
        painter.restore()
        self._paint_jump(painter, option, index)
        if self._key == "manpower":
            self._paint_bang(painter, option, index, left=True)

    def editorEvent(self, event, model, option, index):
        """A click on the right half (the currency) opens the drop-down at once; a click on the left half
        edits the number. Returning False lets the view open the editor as usual."""
        if self._active() and event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            self._popup_next = event.position().x() > option.rect.x() + option.rect.width() // 2
        return False

    def createEditor(self, parent, option, index):
        if not self._active():
            return super().createEditor(parent, option, index)
        box = QWidget(parent)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        box._edit, box._combo = QLineEdit(box), QComboBox(box)
        box._combo.addItems(self._owner._currency_choices())
        box._combo.setToolTip("The currency of this price. Shown in the table as its sign.")
        lay.addWidget(box._edit, 1)
        lay.addWidget(box._combo, 1)
        box.setFocusProxy(box._edit)
        box.setAutoFillBackground(True)
        # Choosing a currency is the whole edit: it is stored at once (the totals follow) and the
        # editor closes — no second click to leave the cell.
        def _chosen(_i, b=box):
            self.commitData.emit(b)
            self.closeEditor.emit(b)
        box._combo.activated.connect(_chosen)
        if getattr(self, "_popup_next", False):                   # the click was on the currency half
            self._popup_next = False
            QTimer.singleShot(0, lambda b=box: (b._combo.setFocus(), b._combo.showPopup()))
        return box

    def setEditorData(self, editor, index):
        text = self._editor_text(index)
        self._remember(index, text)
        if not self._active():
            editor.setText(text)
            editor.selectAll()
            return
        editor._edit.setText(text)
        code = str(index.data(_CUR_ROLE) or self._owner._cat_currency(self._key)).upper()
        if editor._combo.findText(code) < 0:                     # one that was turned off since: keep it visible
            editor._combo.addItem(code)
        editor._combo.setCurrentText(code)
        editor._edit.selectAll()

    def setModelData(self, editor, model, index):
        if not self._active():
            return self._commit(model, index, editor.text(), self._recall(index))
        chosen = editor._combo.currentText().upper()
        own = None if chosen == self._owner._cat_currency(self._key) else chosen   # the default follows the box
        model.setData(index, own, _CUR_ROLE)
        self._commit(model, index, editor._edit.text(), self._recall(index))


def _make_table(headers: list[str], stretch_col: int = 0) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setItemDelegate(_JumpOverlayDelegate(t))
    t.setHorizontalHeaderLabels(headers)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    # Open the cell editor on a single click, not just double-click — read-only
    # cells (built via _ro_item) are unaffected since they lack ItemIsEditable.
    t.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    t.horizontalHeader().setStretchLastSection(False)
    t.horizontalHeader().setSectionResizeMode(stretch_col, QHeaderView.ResizeMode.Stretch)
    t.itemSelectionChanged.connect(lambda: _on_table_selection_changed(t))
    install_elided_tooltips(t)      # full text on hover when a cell is cut off
    install_find(t)                 # Ctrl+F
    return t


def _ro_item(text: str) -> QTableWidgetItem:
    """Create a read-only, non-editable table item."""
    item = QTableWidgetItem(str(text))
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    return item


_ROW_TINT_ROLE = Qt.UserRole + 1   # stores a row's warning colour key ("red"/"yellow"/None)
_DESC_ANCHOR_ROLE = Qt.UserRole + 2   # stores a row's original (un-renamed) requested Description
_SCAFFOLD_ROLE = Qt.UserRole + 4   # True on the Description cell of the scaffold line (not costed by the statistics box)
_SAGE_COST_ROLE = Qt.UserRole + 3   # stores a row's SAGE local_expect_cost (float) — independent
                                     # of the billed rate/price shown in the cell, and unaffected
                                     # by rechargeability, unlike that cell's "NONRECHARG" text.
                                     # Feeds the Pricing sheet's internal "Total SC Eq/Mat Cost"
                                     # column (build_usd's equip_rows/consump_rows "sage_cost" key).

# (background, font) colours per warning key. A row's background tint is
# swapped for a font colour while the row is selected — Qt's selection
# highlight paints over any background colour, so a background-only tint
# quietly vanishes for exactly the rows the user is looking at.
_ROW_TINT_COLORS: dict[str, tuple[QColor, QColor]] = {
    "red":    (QColor("#FFEBEE"), QColor("#B71C1C")),   # unmatched rows
    "yellow": (QColor("#FFF9C4"), QColor("#F57F17")),   # matched-with-a-warning rows
}


def _apply_row_tint(tbl: QTableWidget, row: int) -> None:
    """Repaints a row per its stored warning key and current selection state."""
    col0 = tbl.item(row, 0)
    key = col0.data(_ROW_TINT_ROLE) if col0 else None
    colors = _ROW_TINT_COLORS.get(key)
    selected = bool(col0) and col0.isSelected()
    # Every setBackground/setForeground emits itemChanged, and the tables'
    # itemChanged handlers re-match the row (Match By column) — which tints
    # it again. Painting is cosmetic, so it must not trigger any of that.
    was_blocked = tbl.blockSignals(True)
    try:
        for col in range(tbl.columnCount()):
            item = tbl.item(row, col)
            if not item:
                continue
            if colors is None:
                item.setBackground(QBrush())
                item.setForeground(QBrush())
            elif selected:
                item.setBackground(QBrush())
                item.setForeground(QBrush(colors[1]))
            else:
                item.setBackground(QBrush(colors[0]))
                item.setForeground(QBrush())
    finally:
        tbl.blockSignals(was_blocked)


def _set_row_tint(tbl: QTableWidget, row: int, key: str | None) -> None:
    """Sets a row's warning colour ("red"/"yellow"/None) and repaints it."""
    col0 = tbl.item(row, 0)
    if col0:
        col0.setData(_ROW_TINT_ROLE, key)
    _apply_row_tint(tbl, row)


def _on_table_selection_changed(tbl: QTableWidget) -> None:
    """Re-applies tint for rows that switch between background (not
    selected) and font colour (selected) as the selection changes.

    Only rows carrying an actual warning tint need this: for an untinted
    row, _apply_row_tint's two branches both just clear the brushes
    regardless of selected state, so Qt's own native selection highlight
    already handles it with no work from us. Repainting every row here on
    every single click — the previous behaviour — was the main source of
    UI lag on large tables, since almost every row in a well-matched
    table has no tint at all."""
    for row in range(tbl.rowCount()):
        col0 = tbl.item(row, 0)
        if col0 and col0.data(_ROW_TINT_ROLE):
            _apply_row_tint(tbl, row)


def _highlight_row(tbl: QTableWidget, row: int, matched: bool) -> None:
    """
    Colour every cell in the row with a light red tint when not matched,
    or clear back to the default alternating colour when matched / manual.
    """
    _set_row_tint(tbl, row, None if matched else "red")


def _file_picker_row(label: str, ext_filter: str) -> tuple[QHBoxLayout, QLineEdit]:
    """Return (row_layout, line_edit) for a labelled file picker."""
    row = QHBoxLayout()
    row.setSpacing(6)
    lbl = QLabel(label)
    lbl.setFixedWidth(130)
    lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
    edit = QLineEdit()
    edit.setReadOnly(True)
    edit.setPlaceholderText("(not selected)")
    btn = QPushButton("Browse…")
    btn.setFixedWidth(80)

    _captured_edit   = edit
    _captured_filter = ext_filter

    def _browse():
        path, _ = QFileDialog.getOpenFileName(None, f"Select {label}", "", _captured_filter)
        if path:
            _captured_edit.setText(path)

    btn.clicked.connect(_browse)
    row.addWidget(lbl)
    row.addWidget(edit)
    row.addWidget(btn)
    return row, edit


# Normalised-text -> first-row-position index per (dataframe, column). A
# lookup used to lowercase/strip the whole column (91k rows for SAGE) on every
# call, and a CTR Request load makes hundreds of them on the UI thread. The
# dataframes are only ever replaced wholesale on load (never edited in
# place), so an index built once per dataframe stays valid; the entry holds a
# reference to its dataframe so its id can't be reused while cached.
_LOOKUP_INDEXES: dict[int, tuple[weakref.ref, dict[str, dict[str, int]]]] = {}


def _lookup_index(df: pd.DataFrame, col: str) -> dict[str, int]:
    """First row position for each trimmed, lower-cased value of df[col]."""
    key = id(df)
    entry = _LOOKUP_INDEXES.get(key)
    if entry is None or entry[0]() is not df:
        # A weak reference: the index must not keep a replaced table alive —
        # when the table is freed (e.g. SAGE reloaded) its index goes with it.
        def _forget(ref, key=key):
            current = _LOOKUP_INDEXES.get(key)
            if current is not None and current[0] is ref:
                del _LOOKUP_INDEXES[key]
        entry = _LOOKUP_INDEXES[key] = (weakref.ref(df, _forget), {})
    by_col = entry[1]
    index = by_col.get(col)
    if index is None:
        index = {}
        for pos, value in enumerate(df[col].astype(str).str.strip().str.lower().tolist()):
            index.setdefault(value, pos)
        by_col[col] = index
    return index


def _prime_sage_index(df: pd.DataFrame) -> pd.DataFrame:
    """Builds SAGE's lookup indexes — called on the load thread so the first
    match after loading doesn't pay for it on the UI thread."""
    for col in ("product", "long_description"):
        if col in df.columns:
            _lookup_index(df, col)
    return df


def _match_lookup(df: pd.DataFrame, code_col: str, desc_col: str, price_col: str, key: str):
    """
    Look up a row in a reference dataframe (SAGE, or the AZN pricebook via
    _match_azn_labor) by stock code first, then by description —
    case-insensitive exact match.

    Returns (matched_description, rate, stock_code) or None if no match.
    """
    if df is None or df.empty or not key:
        return None
    key_norm = key.strip().lower()

    if code_col in df.columns:
        pos = _lookup_index(df, code_col).get(key_norm)
        if pos is not None:
            row = df.iloc[pos]
            return str(row[desc_col]), float(row[price_col]), str(row[code_col])

    if desc_col in df.columns:
        pos = _lookup_index(df, desc_col).get(key_norm)
        if pos is not None:
            row = df.iloc[pos]
            code_val = str(row[code_col]) if code_col in df.columns else ""
            return str(row[desc_col]), float(row[price_col]), code_val

    return None


def _match_usd_equipment(df: pd.DataFrame, desc: str) -> tuple[str, float] | None:
    """
    Look up an equipment description in the USD Pricebook (see
    parse_usd_pricebook) by supplier_desc — case-insensitive exact match.
    Equipment stock codes there aren't unique per item (every row is
    generically tagged "*-NOR" or "*-STBY"), so description is the only
    reliable key. When both a NOR (normal) and STBY (standby) row exist for
    the same description, the NOR rate is used by default.

    Returns (matched_description, unit_price) or None if no match.
    """
    if df is None or df.empty or not desc:
        return None
    desc_norm = desc.strip().lower()
    hits = df[df["supplier_desc"].astype(str).str.strip().str.lower() == desc_norm]
    if hits.empty:
        return None
    if len(hits) > 1:
        nor = hits[~hits["stock_code"].astype(str).str.upper().str.contains("STBY")]
        if not nor.empty:
            hits = nor
    row = hits.iloc[0]
    return str(row["supplier_desc"]), float(row["unit_price"])


def _transport_lines(transport_type: str, quantity: str, duration: str) -> list[dict]:
    """
    Expand one requested transport line into the CTR lines that bill it.

    The CTR Request only states the vehicle, how many, and for how long —
    the rate, the mark-up, and what the duration counts ("6 Days" vs "2
    Trips") are commercial figures, so they come from the transport_rates
    table in template_config.json. One requested vehicle can bill as
    several lines (a minibus charges vehicle+driver and its fuel
    separately, only the former marked up). A type that isn't in the table
    still produces one line — its requested wording, "_default"'s mark-up
    and a blank rate — so it shows up in the table, in red, to be priced
    by hand instead of silently disappearing (Generate leaves it out until
    it is priced).
    """
    rates = CFG.get("transport_rates", {})
    wanted = transport_type.strip().lower()
    specs = next(
        (v for k, v in rates.items() if not k.startswith("_") and k.strip().lower() == wanted),
        rates.get("_default", [{}]),
    )
    return [
        {
            "transport_type": transport_type,
            "description":    spec.get("description") or transport_type,
            "quantity":       quantity,
            "duration":       duration,
            "uom":            spec.get("uom", "Days"),
            "rate_azn":       spec.get("rate_azn") or "",   # 0/missing = not priced
            "markup":         spec.get("markup", 0.0),
        }
        for spec in specs
    ]


def _request_qty_text(raw) -> str:
    """Quantity cell text for a CTR Request row: the number, or blank when the
    request gives none. Blank is deliberate — a quantity nobody stated is not
    1; the user has to enter it (see _incomplete_cells, which also keeps
    Generate from including the row until they do)."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return ""
    return "" if v != v else str(v)


def _scaffold_request_item(scaffold: dict) -> dict:
    """
    A scaffold tonnage from the request's extras block, shaped like an
    ordinary equipment request row — scaffold is equipment, and a
    hand-priced scaffold CTR shows it as one more Plant & Equipment line:

        Conventional Scaffold | 66.95867 | TON | rate/day | days

    The block states three things and only three — the system's name, how
    much of it, and that it's measured in tonnes — so that is all this
    carries over. Rate and duration are left to the pricebook match and the
    user, exactly as for any other equipment line whose rate the request
    doesn't state; the row lands in the table as "✗ No match" until its
    Match By is set, and that correction is then remembered like any other
    rename.
    """
    return {
        "stock_code":     "",
        "description":    str(scaffold.get("system", "") or "").strip(),
        "uom":            scaffold.get("uom") or CFG.get("scaffold", {}).get("default_uom", "TON"),
        "quantity":       scaffold.get("tonnage"),
        "rechargability": "",
    }


def _sage_non_recharge(sage_df: pd.DataFrame, key: str) -> bool | None:
    """
    SAGE's own analysis_b-derived rechargeability flag for a stock code (see
    parse_sage) — the rechargeability source for consumables, mirroring how
    equipment shows the "NONRECHARG" sentinel. Returns None if the code
    isn't in SAGE at all.
    """
    if sage_df is None or sage_df.empty or not key:
        return None
    key_norm = key.strip().lower()
    pos = _lookup_index(sage_df, "product").get(key_norm)
    if pos is None:
        return None
    return bool(sage_df.iloc[pos]["non_recharge"])


# AZN labor stock codes encode shift + shift-type in their prefix, e.g.
# "MSU-NAT-OFF-12" = Day/Normal, Offshore; "NOV-NAT-OFF-12" = Night/Overtime,
# Offshore. An optional "GE" prefix (e.g. "GENOV-...") marks a different
# labor category (seen on industrial-cleaning roles) but uses the same
# shift-type suffix scheme, so it's stripped before lookup.
#
# Configurable via the "azn_shift_prefixes" section of template_config.json
# (see ctr_tools/config.py) — add a prefix there, no code change needed,
# if the pricebook introduces one this doesn't cover yet. An unrecognized
# prefix makes manpower matching refuse to guess (see
# _decode_manpower_encoding_full / _match_azn_labor) rather than silently
# picking a possibly-wrong rate.
_AZN_SHIFT_PREFIXES: dict[str, tuple[str, str]] = {
    prefix: tuple(value) for prefix, value in CFG["azn_shift_prefixes"].items()
}


def _decode_azn_stock_code(stock_code: str):
    """
    Decodes an AZN labor stock code into (location, shift, shift_type, hours),
    or None if it doesn't match the <PREFIX>-NAT-<ON|OFF>-<hours> pattern.
    location is "on"/"off"; shift is "day"/"night"; shift_type is
    "normal"/"overtime"/"standby"; hours is the trailing shift-length digits
    (e.g. "8", "10", "12") as a string, or None if absent.
    """
    parts = str(stock_code).strip().upper().split("-NAT-")
    if len(parts) != 2 or not parts[1]:
        return None
    prefix, rest = parts
    if prefix.startswith("GE"):
        prefix = prefix[2:]
    shift_info = _AZN_SHIFT_PREFIXES.get(prefix)
    if shift_info is None:
        return None
    rest_parts = rest.split("-")
    location = rest_parts[0].lower()
    if location not in ("on", "off"):
        return None
    hours = rest_parts[1] if len(rest_parts) > 1 else None
    shift, shift_type = shift_info
    return location, shift, shift_type, hours


_SHIFT_DISPLAY      = {"day": "Day", "night": "Night"}
_SHIFT_TYPE_DISPLAY = {"normal": "Normal", "overtime": "Overtime", "standby": "Standby"}


def _decode_manpower_encoding_full(encoding: str):
    """
    Fully decodes the CTR Request's "ENCODING" column, which requesters
    populate inconsistently: sometimes just the bare "NAT-<ON|OFF>-<hours>"
    suffix (e.g. "NAT-ON-10"), sometimes a full AZN stock code with its
    shift-type prefix prepended (e.g. "MSU-NAT-OFF-12" = Day/Normal/
    Offshore/12h — see _decode_azn_stock_code for the prefix table reused
    here).

    A prefix that isn't a recognized shift code (e.g. "MF-NAT-ON-8" — "MF"
    is not one of _AZN_SHIFT_PREFIXES) means we can't tell what shift/
    shift-type it actually encodes, and it may well disagree with the
    row's own Shift/Status columns (as in that example: Night/Standby text
    next to a code that turns out to mean Day/Normal). Guessing by falling
    back to the Shift/Status columns risks silently matching a *different*,
    wrong pricebook row — so this is reported back as `ambiguous=True` and
    the caller should refuse to auto-match by description at all rather
    than pick a side.

    Returns (location, shift, shift_type, hours, ambiguous) using the same
    "Onshore"/"Offshore", "Day"/"Night", "Normal"/"Overtime"/"Standby"
    display strings used elsewhere in the manpower table (None for any
    field the encoding doesn't determine), or None entirely if even the
    NAT-<ON|OFF> location portion can't be parsed.
    """
    text = (encoding or "").strip().upper()
    if "-NAT-" in text:
        prefix, _, rest = text.partition("-NAT-")
    elif text.startswith("NAT-"):
        prefix, rest = "", text[len("NAT-"):]
    else:
        return None

    rest_parts = rest.split("-")
    location = rest_parts[0].lower() if rest_parts else ""
    if location not in ("on", "off"):
        return None
    hours = rest_parts[1] if len(rest_parts) > 1 and rest_parts[1].isdigit() else None

    shift = shift_type = None
    ambiguous = False
    if prefix:
        p = prefix[2:] if prefix.startswith("GE") else prefix
        info = _AZN_SHIFT_PREFIXES.get(p)
        if info:
            shift, shift_type = info
        else:
            ambiguous = True

    return (
        "Onshore" if location == "on" else "Offshore",
        _SHIFT_DISPLAY.get(shift),
        _SHIFT_TYPE_DISPLAY.get(shift_type),
        hours,
        ambiguous,
    )


def _recharge_mismatch(request_tag: str, db_non_recharge: bool) -> bool:
    """
    True if the CTR Request's own Rechargability column (equipment col J /
    consumables col R) disagrees with what the CTR_NAMES_DB_USD bridge
    determined for the matched stock code. The request-sheet tag is usually
    populated by the requester from the same DB, so a mismatch is worth
    surfacing as a warning rather than silently trusting either side.
    """
    tag = (request_tag or "").strip().upper()
    if not tag:
        return False
    return ("NON" in tag) != db_non_recharge


def _normalize_location(raw: str) -> str:
    return "off" if "off" in (raw or "").strip().lower() else "on"


def _normalize_shift(raw: str) -> str:
    return "night" if "night" in (raw or "").strip().lower() else "day"


def _normalize_shift_type(raw: str) -> str:
    raw = (raw or "").strip().lower()
    if "overtime" in raw or raw == "ot":
        return "overtime"
    if "standby" in raw or "stand by" in raw:
        return "standby"
    return "normal"   # covers "normal", "rotational", and empty/blank


def _match_azn_labor(df: pd.DataFrame, work_name: str, location: str, shift: str,
                      shift_type: str, hours: str | None = None, ambiguous: bool = False):
    """
    Finds the AZN pricebook rate for a manpower line, picking the variant
    that matches the requested location/shift/shift-type (and shift-length
    hours, when known) combination.

    work_name is tried first as a literal stock code — this lets "Match By"
    be overridden with an exact code, bypassing the Type/Shift/Shift Type
    columns (and `ambiguous`) entirely. Otherwise work_name is matched as a
    description, and narrowed down to the row whose stock code decodes
    (via _decode_azn_stock_code) to the same location/shift/shift-type/hours.

    Matching is exact, not "closest": when several pricebook rows share the
    same description but differ only in shift-length hours (e.g.
    "NSB-NAT-ON-8" vs "NSB-NAT-ON-12"), only the row whose decoded hours
    equal `hours` is accepted — the wrong-but-similar row is never silently
    substituted. If `hours` is None (no request-encoded hint, e.g. a
    manually added row), the description path refuses to match at all. If no row matches the
    known dimensions, returns None so the row surfaces as unmatched instead
    of picking a wrong rate.

    `ambiguous` (see _decode_manpower_encoding_full) means the CTR
    Request's own ENCODING carries a shift-type prefix we don't recognize,
    which may contradict the Shift/Status columns — description matching
    is skipped entirely in that case (rather than guessing which side is
    right), so the row surfaces as unmatched until the request is
    corrected or "Match By" is set to the exact intended stock code.

    Returns (matched_description, rate, stock_code) or None.
    """
    if df is None or df.empty or not work_name:
        return None

    key_norm = work_name.strip().lower()

    code_matches = df[df["stock_code"].astype(str).str.strip().str.lower() == key_norm]
    if not code_matches.empty:
        row = code_matches.iloc[0]
        return str(row["supplier_desc"]), float(row["unit_price"]), str(row["stock_code"])

    if ambiguous:
        return None

    # Shift / Shift Type not stated (and no exact stock code given above): the
    # row stays unmatched until the user picks them — never assumed Day/Normal.
    if not (shift or "").strip() or not (shift_type or "").strip():
        return None

    candidates = df[df["supplier_desc"].astype(str).str.strip().str.lower() == key_norm]
    if candidates.empty:
        return None

    loc = _normalize_location(location)
    sh  = _normalize_shift(shift)
    st  = _normalize_shift_type(shift_type)

    # No shift length (no Encoding): never pick a rate from the description
    # alone — the user chooses the Encoding (or gives an exact stock code).
    if hours is None:
        return None

    hits = []
    for _, row in candidates.iterrows():
        decoded = _decode_azn_stock_code(row["stock_code"])
        if decoded is None:
            continue
        c_loc, c_shift, c_shift_type, c_hours = decoded
        if (c_loc == loc and c_shift == sh and c_shift_type == st
                and c_hours == hours):
            hits.append(row)

    # Onshore and Offshore variants never mix: the location must be equal above.
    if not hits:
        return None
    row = hits[0]
    return str(row["supplier_desc"]), float(row["unit_price"]), str(row["stock_code"])


def _is_dir_writable(path: Path) -> bool:
    """Quick check — tries to create and immediately delete a temp file."""
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(dir=path, delete=True):
            pass
        return True
    except OSError:
        return False


def _btn_style(color: str = PRIMARY, light: str = MR_LIGHT) -> str:
    return f"""
        QPushButton {{
            background: white; color: {color};
            border: 1.5px solid {color}; border-radius: 4px;
            padding: 2px 10px; font-size: 11px; min-height: 24px;
        }}
        QPushButton:hover    {{ background: {light}; }}
        QPushButton:disabled {{ color: {BORDER}; border-color: {BORDER}; }}
    """


# ─────────────────────────────────────────────────────────────────────────────
# Saved renames manager
# ─────────────────────────────────────────────────────────────────────────────

def _stock_rename_key(desc: str, code: str) -> str:
    """
    Composite lookup/storage key for Equipment/Consumables renames (both
    Match By aliases and Description renames) — the same description text
    can legitimately belong to two different stock codes (e.g. two rate
    variants of the same-named item), and a rename made for one must not
    silently apply to the other. Falls back to description alone when
    there's no stock code to disambiguate with, e.g. a manually typed row
    that never had one. Manpower has no stock-code concept and isn't
    affected — it keys on description alone, same as before.
    """
    desc = (desc or "").strip()
    code = (code or "").strip()
    return f"{desc} [{code}]" if code else desc


_STOCK_KEY_RE = re.compile(r"^(.*) \[([^\[\]]*)\]$")


def _split_stock_rename_key(key: str) -> tuple[str, str]:
    """
    Reverses _stock_rename_key for display purposes only (the Manage
    Renames dialog's separate Stock Code column) — splits a stored
    Equipment/Consumables rename key back into (description, stock code).
    A key saved before the stock-code composite existed, or one that never
    had a code to begin with, has no bracket suffix and splits to
    (key, "").
    """
    m = _STOCK_KEY_RE.match(key)
    return (m.group(1), m.group(2)) if m else (key, "")


def _get_alias_by_stock(aliases: dict, category: str, desc: str, code: str) -> str | None:
    """
    get_alias() keyed by (description, stock code) — falls back to a bare
    description-only lookup for renames saved before this composite key
    existed, so those don't silently stop applying.
    """
    val = get_alias(aliases, category, _stock_rename_key(desc, code))
    if val is None and code:
        val = get_alias(aliases, category, desc)
    return val


def _get_desc_rename_by_stock(desc_renames: dict, category: str, desc: str, code: str) -> str | None:
    """See _get_alias_by_stock — same idea, for desc_renames.py."""
    val = get_desc_rename(desc_renames, category, _stock_rename_key(desc, code))
    if val is None and code:
        val = get_desc_rename(desc_renames, category, desc)
    return val


def _coerce_rename_dict(data: dict) -> dict:
    """
    Validates/normalizes an already JSON-parsed {category: {requested:
    renamed}} dict into the standard three-category shape shared by
    aliases.py and desc_renames.py. Raises ValueError on a malformed
    category value.
    """
    result = {}
    for cat in ("manpower", "equipment", "consumable"):
        mapping = data.get(cat) or {}
        if not isinstance(mapping, dict):
            raise ValueError(f'"{cat}" should be an object of {{requested name: renamed-to}} pairs.')
        result[cat] = {str(k): str(v) for k, v in mapping.items()}
    return result


class AliasManagerDialog(QDialog):
    """Lists learned renames — for one category, or all three — and lets
    the user delete one. Two kinds of rename share this list, distinguished
    by a "Type" column: "Match By" (teaches the pricebook/SAGE lookup key,
    from aliases.py) and "Description" (teaches the CTR display text, from
    desc_renames.py) — independently learned and persisted, but shown
    together since both answer "what did I rename this requested item to?"."""

    def __init__(self, aliases: dict, desc_renames: dict, parent=None, applied_count: int = 0,
                 category: str | None = None):
        super().__init__(parent)
        self._category = category
        cat_title = category.capitalize() if category else None
        self.setWindowTitle(f"Manage {cat_title} Renames" if cat_title else "Manage Saved Renames")
        self.resize(700, 420)
        self._aliases = aliases
        self._desc_renames = desc_renames
        self._applied_count = applied_count
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        scope = f"a {self._category.capitalize()} row" if self._category \
            else "a Manpower, Equipment or Consumables row"
        info = QLabel(
            f"These renames were learned automatically when you fixed "
            f"\"No match\" on {scope} by editing \"Match By\" (teaches the "
            f"pricebook lookup key), or by editing \"Description\" on an "
            f"already-loaded row (teaches what to display in the CTR). Both "
            "are applied automatically the next time the same requested "
            "description appears in a future CTR Request. Select a row and "
            "click Delete to forget a rename."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        layout.addWidget(info)

        if self._applied_count:
            table_scope = f"{self._category.capitalize()} table" if self._category \
                else "Manpower/Equipment/Consumables tables"
            applied_lbl = QLabel(
                f"✓ Just applied {self._applied_count} of these rename(s) to "
                f"matching rows already in the {table_scope} below the "
                f"Generate section."
            )
            applied_lbl.setWordWrap(True)
            applied_lbl.setStyleSheet(f"color: {CTR_COLOR}; font-weight: bold; font-size: 11px;")
            layout.addWidget(applied_lbl)

        self._tbl = QTableWidget(0, 5)
        self._tbl.setHorizontalHeaderLabels(
            ["Category", "Type", "Requested Name", "Stock Code", "New Value"])
        self._tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tbl.verticalHeader().setVisible(False)
        install_elided_tooltips(self._tbl)
        install_find(self._tbl)
        self._tbl.horizontalHeader().setStretchLastSection(True)
        # The Category column is redundant once the dialog is already scoped
        # to one category via its title — keep the data (still used by
        # _delete_selected) but hide the column itself.
        if self._category:
            self._tbl.setColumnHidden(0, True)
        layout.addWidget(self._tbl)
        self._reload_table()

        io_row = QHBoxLayout()
        import_btn = QPushButton("Import…")
        import_btn.setToolTip(
            "Load renames from a JSON file, merging them into the current "
            "dictionary (imported entries overwrite matching ones)."
        )
        import_btn.setStyleSheet(_btn_style())
        import_btn.clicked.connect(self._import_aliases)
        export_btn = QPushButton("Export…")
        export_btn.setToolTip(
            "Save the current renames dictionary to a JSON file — for "
            "backup, or to share with another machine/user."
        )
        export_btn.setStyleSheet(_btn_style())
        export_btn.clicked.connect(self._export_aliases)
        io_row.addWidget(import_btn)
        io_row.addWidget(export_btn)
        io_row.addStretch()
        layout.addLayout(io_row)

        btn_row = QHBoxLayout()
        del_btn = QPushButton("Delete Selected")
        del_btn.setStyleSheet(_btn_style())
        del_btn.clicked.connect(self._delete_selected)
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(90)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _reload_table(self):
        self._tbl.setRowCount(0)
        # Fixed category order (not dict insertion order) so Manpower,
        # Equipment and Consumable renames each show up in a stable, expected
        # place regardless of which category was edited most recently.
        categories = (self._category,) if self._category else ("manpower", "equipment", "consumable")

        def _add_rows(store, type_label):
            for category in categories:
                mapping = store.get(category, {})
                for req_key, renamed in sorted(mapping.items()):
                    # Equipment/Consumables keys are the composite
                    # "description [stock code]" from _stock_rename_key —
                    # split back apart so Stock Code gets its own column
                    # instead of showing up embedded in Requested Name.
                    # Manpower has no stock-code concept, so it's blank.
                    if category == "manpower":
                        desc, code = req_key, ""
                    else:
                        desc, code = _split_stock_rename_key(req_key)
                    r = self._tbl.rowCount()
                    self._tbl.insertRow(r)
                    self._tbl.setItem(r, 0, QTableWidgetItem(category.capitalize()))
                    self._tbl.setItem(r, 1, QTableWidgetItem(type_label))
                    desc_item = QTableWidgetItem(desc)
                    # Stash the raw storage key (not just the split
                    # description) so _delete_selected can delete the exact
                    # entry without having to reconstruct it.
                    desc_item.setData(Qt.UserRole, req_key)
                    self._tbl.setItem(r, 2, desc_item)
                    self._tbl.setItem(r, 3, QTableWidgetItem(code))
                    self._tbl.setItem(r, 4, QTableWidgetItem(renamed))

        _add_rows(self._aliases, "Match By")
        _add_rows(self._desc_renames, "Description")

    def _delete_selected(self):
        rows = sorted({i.row() for i in self._tbl.selectedItems()}, reverse=True)
        for r in rows:
            category = self._tbl.item(r, 0).text().lower()
            rtype    = self._tbl.item(r, 1).text()
            req_key  = self._tbl.item(r, 2).data(Qt.UserRole)
            if rtype == "Description":
                delete_desc_rename(self._desc_renames, category, req_key)
            else:
                delete_alias(self._aliases, category, req_key)
        self._reload_table()

    def _export_aliases(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Renames", "ctr_renames.json", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            Path(path).write_text(
                json.dumps({"match_by": self._aliases, "description": self._desc_renames},
                           indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            QMessageBox.critical(self, "Export failed", str(e))
            return
        QMessageBox.information(self, "Exported", f"Renames exported to:\n{path}")

    def _import_aliases(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Renames", "", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            QMessageBox.critical(self, "Import failed", str(e))
            return
        if not isinstance(raw, dict):
            QMessageBox.critical(self, "Import failed",
                                  "File does not contain a renames dictionary (expected a JSON object).")
            return

        # New combined format has "match_by"/"description" wrapper keys; a
        # file exported before this dialog merged the two kinds is just the
        # flat {category: {...}} shape and is treated as Match By renames
        # only, so previously-exported files keep working.
        try:
            if "match_by" in raw or "description" in raw:
                imported_aliases = _coerce_rename_dict(raw.get("match_by") or {})
                imported_desc    = _coerce_rename_dict(raw.get("description") or {})
            else:
                imported_aliases = _coerce_rename_dict(raw)
                imported_desc    = {}
        except ValueError as e:
            QMessageBox.critical(self, "Import failed", str(e))
            return

        def _merge(existing_store, imported):
            added = updated = 0
            for category, mapping in imported.items():
                existing = existing_store.setdefault(category, {})
                for key, value in mapping.items():
                    if key not in existing:
                        added += 1
                    elif existing[key] != value:
                        updated += 1
                    existing[key] = value
            return added, updated

        a_added, a_updated = _merge(self._aliases, imported_aliases)
        d_added, d_updated = _merge(self._desc_renames, imported_desc)
        save_aliases(self._aliases)
        save_desc_renames(self._desc_renames)
        self._reload_table()
        QMessageBox.information(
            self, "Imported",
            f"Imported {a_added + d_added} new and {a_updated + d_updated} updated "
            f"rename(s) from:\n{path}"
        )


class _ChoiceDelegate(_JumpOverlayDelegate):
    """A table cell edited with a drop-down (first entry empty = not chosen)."""

    def __init__(self, choices, parent=None):
        super().__init__(parent)
        self._choices = list(choices)

    def createEditor(self, parent, option, index):
        box = QComboBox(parent)
        box.addItems(["", *self._choices])
        box.activated.connect(lambda _i, b=box: (self.commitData.emit(b), self.closeEditor.emit(b)))
        return box

    def setEditorData(self, editor, index):
        editor.setCurrentText(str(index.data() or ""))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText())


class _TypeAheadCombo(QComboBox):
    """A plain (not editable) drop-down — so a click anywhere on it opens the
    list, not just on a small arrow — that you can also type into: the typed
    letters select the first entry that starts with (else contains) them, and
    Enter confirms. Only a listed value can ever be chosen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._typed = ""
        self._typed_at = 0.0

    def keyPressEvent(self, e):
        txt = e.text()
        if txt and txt.isprintable() and not (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            now = time.monotonic()
            self._typed = (self._typed if now - self._typed_at < 1.2 else "") + txt.upper()
            self._typed_at = now
            for flag in (Qt.MatchFlag.MatchStartsWith, Qt.MatchFlag.MatchContains):
                i = self.findText(self._typed, flag | Qt.MatchFlag.MatchFixedString)
                if i >= 0:
                    self.setCurrentIndex(i)          # programmatic: nothing is committed yet
                    break
            e.accept()
            return
        super().keyPressEvent(e)


class _EncodingDelegate(_JumpOverlayDelegate):
    """The Encoding cell: a drop-down of the encodings valid for the row's role
    and location (empty clears it). Typing selects within that list."""

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner

    def createEditor(self, parent, option, index):
        box = _TypeAheadCombo(parent)
        box.addItems(["", *self._owner._encoding_options(index.row())])
        box.setMaxVisibleItems(15)
        # Picking from the list commits once. (Enter on the closed combo is
        # committed by the delegate itself, so it is not connected here — doing
        # both made Qt commit an editor it had already closed.)
        box.activated.connect(lambda _i, b=box: self._finish(b))
        return box

    def _finish(self, box) -> None:
        if box.property("done"):
            return
        box.setProperty("done", True)
        self.commitData.emit(box)
        self.closeEditor.emit(box)

    def setEditorData(self, editor, index):
        editor.setCurrentText(str(index.data() or ""))

    def setModelData(self, editor, model, index):
        text = editor.currentText().strip().upper()
        options = self._owner._encoding_options(index.row())
        if text and text not in options:
            self._owner._reject_encoding(index.row(), text, options)
            return
        model.setData(index, text)


class _SaveLocationDialog(QDialog):
    """Shows where a file is about to be saved — the folder remembered from
    last time and the file name — so the user can approve it as it is or
    change the folder first. With no remembered folder it opens the folder
    chooser straight away and "Save" stays disabled until one is picked."""

    def __init__(self, parent, title: str, folder: str, filename: str, start_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self._folder = folder
        self._start_dir = start_dir
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("The file will be saved in this folder:"))
        row = QHBoxLayout()
        self._edit = QLineEdit(folder)
        self._edit.setReadOnly(True)
        self._edit.setPlaceholderText("(no folder chosen yet)")
        change = QPushButton("Change folder…")
        change.clicked.connect(self._change)
        row.addWidget(self._edit, 1)
        row.addWidget(change)
        lay.addLayout(row)
        name = QLabel(f"File name: {filename}")
        name.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        lay.addWidget(name)
        note = QLabel("The folder is remembered and offered again next time.")
        note.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        lay.addWidget(note)
        btns = QHBoxLayout()
        btns.addStretch(1)
        self._save_btn = QPushButton("Save here")
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addWidget(self._save_btn)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        self._save_btn.setEnabled(bool(folder))
        if not folder:
            QTimer.singleShot(0, self._change)

    def _change(self):
        d = QFileDialog.getExistingDirectory(
            self, "Choose the folder to save in", self._folder or self._start_dir)
        if d:
            self._folder = d
            self._edit.setText(d)
            self._save_btn.setEnabled(True)

    def folder(self) -> str:
        return self._folder


# ─────────────────────────────────────────────────────────────────────────────
# Main dialog
# ─────────────────────────────────────────────────────────────────────────────

def file_stamp(path) -> tuple[int, int] | None:
    """(modified time in ns, size) of a file — its fingerprint: when it differs from the one taken when the file
    was read, the file changed on disk since. None when the path is empty or the file is not there."""
    if not path:
        return None                                  # an empty path would otherwise mean "the current folder"
    try:
        st = Path(path).stat()
    except (OSError, TypeError, ValueError):
        return None
    return (st.st_mtime_ns, st.st_size)


class CTRGeneratorWidget(QWidget):
    # Emitted whenever the SAGE Export path text changes (typed, browsed or
    # restored). The MR vs CTR tab keeps its own SAGE field in step with this
    # one — see MainWindow, and set_sage_path() below.
    sage_path_changed = Signal(str)
    choices_reloaded = Signal(object)          # the saved rechargeability choices were re-read (Settings reset / restore)
    # The USD Pricebook / Combined DB path text changed. The MR vs CTR tab has its own fields
    # for them (the price statistics need both) and keeps them in step with these, like SAGE.
    usd_pricebook_path_changed = Signal(str)
    combined_db_path_changed = Signal(str)
    cbar_currencies_changed = Signal()         # Settings turned CBAR currencies on / off
    stats_defaults_changed = Signal()          # Settings changed the statistics defaults (mode, scaffold, markup)
    files_loaded = Signal()                    # Load Files finished: the reference tables were just re-read from disk

    def __init__(self, parent=None):
        super().__init__(parent)

        self._azn_df:      pd.DataFrame = pd.DataFrame()
        self._sage_df:     pd.DataFrame = pd.DataFrame()
        # Which file _sage_df was parsed from, so the MR vs CTR statistics can
        # reuse it instead of parsing the same 100k-row export a second time.
        self._sage_df_path:      str = ""
        self._sage_loading_path: str = ""
        self._usd_pb_df:   pd.DataFrame = pd.DataFrame()
        self._names_db_df: pd.DataFrame = pd.DataFrame()
        # Which files those two were parsed from (see loaded_usd_df / loaded_names_df).
        self._usd_pb_df_path:      str = ""
        self._names_db_df_path:    str = ""
        self._usd_loading_path:    str = ""
        self._names_loading_path:  str = ""
        # The files' fingerprints (modified time, size) taken when they were read: a parsed table is only
        # reused (loaded_*_df) while its file is unchanged on disk.
        self._sage_df_stamp = self._usd_pb_df_stamp = self._names_db_df_stamp = None
        self._loading_stamps: dict = {}
        # Manpower encoding rules from the Combined DB (parse_encoding_info):
        # valid encodings per role, and the roles that are always Onshore.
        self._enc_variants: dict[str, list[str]] = {}
        self._onshore_roles: set[str] = set()
        self._workers:     list = []
        self._aliases:     dict = load_aliases()
        self._desc_renames: dict = load_desc_renames()
        self._presets:     dict = load_presets()
        # Additional Information items the loaded CTR Request marked
        # "Required" — appended to the AZN CTR's activities section header.
        self._required_info: list[str] = []

        # Async file-loading state
        self._load_signals = _LoadSignals()
        self._load_signals.result.connect(self._on_file_loaded)
        self._pending_loads: int  = 0
        self._load_job: Job | None = None
        self._load_errors:   list = []   # [(name, msg, is_warning)]
        self._load_status:   list = []

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(0)

        # Scrollable content
        scroll = QScrollArea()
        self._content_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        content = QWidget()
        self._content_widget = content
        cl = QVBoxLayout(content)
        cl.setSpacing(12)
        cl.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # ─── Section 1: File Inputs ──────────────────────────────────────────
        self._build_file_section(cl)

        # ─── Section 2: Manpower / Equipment / Consumables ────────────────────
        self._build_manpower_section(cl)
        self._build_equipment_section(cl)
        self._build_consumables_section(cl)
        self._build_extras_section(cl)
        self._build_stats_section(cl)

        # ─── Section 3: Generate ─────────────────────────────────────────────
        self._build_generate_section(cl)

        cl.addStretch()

    # ── Section 1 ─────────────────────────────────────────────────────────────

    def _build_file_section(self, parent_layout: QVBoxLayout):
        grp = QGroupBox(f"Source Files  —  CTR Generator v{_VERSION}")
        grp.setStyleSheet(_group_css(PRIMARY))
        gl = QVBoxLayout(grp)
        gl.setSpacing(6)

        # Output templates + Combined DB — shared by both data-source sub-tabs below
        row4, self._azn_tpl_edit = _file_picker_row("AZN Template", "Excel (*.xlsx *.xls)")
        row5, self._usd_tpl_edit = _file_picker_row("USD Template", "Excel (*.xlsx *.xls)")
        row_pd, self._pdms_tpl_edit = _file_picker_row("PDMS Template", "Word (*.docx)")
        row6, self._combined_db_edit = _file_picker_row(
            "Combined DB (Names + Request)", "Excel (*.xlsx *.xls)")
        gl.addLayout(row4)
        gl.addLayout(row5)
        gl.addLayout(row_pd)
        gl.addLayout(row6)

        sub_tabs = QTabWidget()
        sub_tabs.setStyleSheet(f"""
            QTabBar::tab {{ padding: 5px 16px; font-size: 11px; }}
            QTabBar::tab:selected {{ font-weight: bold; color: {PRIMARY}; }}
        """)
        gl.addWidget(sub_tabs)

        sub_tabs.addTab(self._build_pricebook_tab(), "Pricebook-Based")
        sub_tabs.addTab(self._build_ctr_request_tab(), "CTR Request-Based")

        parent_layout.addWidget(grp)

    def _build_pricebook_tab(self) -> QWidget:
        tab = QWidget()
        tl = QVBoxLayout(tab)
        tl.setSpacing(6)
        tl.setContentsMargins(2, 8, 2, 2)

        info = QLabel(
            "Loads these as reference data for the \"Customer Request vs "
            "Pricebook/SAGE Match\" lookups below — no rows are added to "
            "the CTR generation tables here. Use \"+ Add Row\" in a CTR "
            "table directly if you want to add an item by hand. Manpower "
            "matches against the AZN Pricebook, consumables against SAGE "
            "Export, and equipment against the USD Pricebook (resolved via "
            "the Equipment Names DB bridge in Combined DB above, when "
            "selected)."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        tl.addWidget(info)

        row1, self._azn_pb_edit = _file_picker_row("AZN Pricebook", "Excel (*.xlsx *.xls)")
        row2, self._sage_edit   = _file_picker_row("SAGE Export", "Excel (*.xlsx *.xlsm *.xls)")
        row3, self._usd_pb_edit = _file_picker_row("USD Pricebook", "Excel (*.xlsx *.xls)")
        self._sage_edit.textChanged.connect(self.sage_path_changed)
        self._usd_pb_edit.textChanged.connect(self.usd_pricebook_path_changed)
        self._combined_db_edit.textChanged.connect(self.combined_db_path_changed)
        tl.addLayout(row1)
        tl.addLayout(row2)
        tl.addLayout(row3)

        load_btn = QPushButton("Load Files")
        load_btn.setFixedHeight(36)
        load_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {PRIMARY}; color: white;
                border: none; border-radius: 6px;
                font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover   {{ background-color: {MR_COLOR}; }}
            QPushButton:pressed {{ background-color: #0D47A1; }}
            QPushButton:disabled {{ background-color: {BORDER}; }}
        """)
        self._load_files_btn = load_btn
        load_btn.clicked.connect(self._load_files)
        tl.addWidget(load_btn)

        self._pricebook_status = QLabel("")
        self._pricebook_status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._pricebook_status.setWordWrap(True)
        tl.addWidget(self._pricebook_status)
        tl.addStretch()
        return tab

    def _build_ctr_request_tab(self) -> QWidget:
        tab = QWidget()
        tl = QVBoxLayout(tab)
        tl.setSpacing(6)
        tl.setContentsMargins(2, 8, 2, 2)

        info = QLabel(
            "Reads the CTR_REQUEST sheet from the Combined DB file selected above."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        tl.addWidget(info)

        load_btn = QPushButton("Load CTR Request")
        load_btn.setFixedHeight(36)
        load_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {CTR_COLOR}; color: white;
                border: none; border-radius: 6px;
                font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover    {{ background-color: #00897B; }}
            QPushButton:pressed  {{ background-color: #004D40; }}
            QPushButton:disabled {{ background-color: {BORDER}; }}
        """)
        self._load_ctr_btn = load_btn
        load_btn.clicked.connect(self._load_ctr_request)
        tl.addWidget(load_btn)

        self._ctr_req_status = QLabel(
            "Parses the CTR_REQUEST sheet: fills the Client/Location/Scope/Date "
            "fields below, and shows requested manpower/equipment/consumables "
            "in the \"Customer Request vs Pricebook/SAGE Match\" section below."
        )
        self._ctr_req_status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._ctr_req_status.setWordWrap(True)
        tl.addWidget(self._ctr_req_status)
        tl.addStretch()
        return tab

    # ── Section 2a: Manpower ──────────────────────────────────────────────────

    def _build_manpower_section(self, parent_layout: QVBoxLayout):
        grp = QGroupBox("Manpower — AZN")
        self._groups = getattr(self, "_groups", {})
        self._groups["manpower"] = grp
        grp.setStyleSheet(_group_css(MR_COLOR))
        gl = QVBoxLayout(grp)
        gl.setSpacing(6)

        info = QLabel(
            "Loaded from the CTR Request, matched against the AZN pricebook. "
            "Type (Onshore/Offshore), Shift (Day/Night) and Shift Type "
            "(Normal/Overtime/Rotational/Standby) together select the "
            "correct pricebook rate — the AZN stock code encodes all three "
            "(e.g. NOV-NAT-OFF-12 = Night, Overtime, Offshore); Rotational "
            "is billed at the Normal rate. Edit any field directly — "
            "changing Type/Shift/Shift Type/Match By re-runs the lookup. "
            "Only rows with a \"✓\" Status (matched, or manually added) are "
            "included when generating; \"✗ No match\" rows are skipped."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        gl.addWidget(info)

        top_btn_row = QHBoxLayout()
        rematch_btn = QPushButton("Re-match All Manpower")
        rematch_btn.setToolTip(
            "Re-run matching for every Manpower row against the currently "
            "loaded AZN pricebook. Use this if you load the CTR Request "
            "before loading the Pricebook-Based files."
        )
        rematch_btn.setStyleSheet(_btn_style(MR_COLOR, MR_LIGHT))
        rematch_btn.clicked.connect(lambda: self._ask_rematch("manpower"))
        top_btn_row.addWidget(rematch_btn)

        manage_btn = QPushButton("Manage Manpower Renames…")
        manage_btn.setToolTip(
            "View or delete Manpower renames learned from your \"Match By\" edits."
        )
        manage_btn.setStyleSheet(_btn_style(MUTED, "#F5F5F5"))
        manage_btn.clicked.connect(lambda: self._open_alias_manager("manpower"))
        top_btn_row.addWidget(manage_btn)
        top_btn_row.addStretch()
        gl.addLayout(top_btn_row)
        gl.addWidget(self._rematch_note())

        search_row = QHBoxLayout()
        search_lbl = QLabel("Search:")
        search_lbl.setFixedWidth(50)
        search_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._manpower_search = QLineEdit()
        self._manpower_search.setPlaceholderText("Filter rows by any column…")
        self._manpower_search.textChanged.connect(
            lambda t: self._filter_table(self._req_manpower_tbl, t))
        search_row.addWidget(search_lbl)
        search_row.addWidget(self._manpower_search)
        gl.addLayout(search_row)

        self._req_manpower_tbl = _make_table(_MANPOWER_HEADERS, stretch_col=_MP_DESC)
        self._req_manpower_tbl.setMinimumHeight(220)
        self._req_manpower_tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._req_manpower_tbl.cellChanged.connect(self._on_manpower_changed)
        # Shift / Shift Type are chosen from a list (blank until chosen).
        self._req_manpower_tbl.setItemDelegateForColumn(
            _MP_SHIFT, _ChoiceDelegate(("Day", "Night"), self._req_manpower_tbl))
        self._req_manpower_tbl.setItemDelegateForColumn(
            _MP_SHIFTTYPE, _ChoiceDelegate(("Normal", "Overtime", "Standby"), self._req_manpower_tbl))
        self._req_manpower_tbl.setItemDelegateForColumn(
            _MP_TYPE, _ChoiceDelegate(("Onshore", "Offshore"), self._req_manpower_tbl))
        self._req_manpower_tbl.setItemDelegateForColumn(
            _MP_ENC, _EncodingDelegate(self, self._req_manpower_tbl))
        gl.addWidget(self._req_manpower_tbl)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Row")
        add_btn.setStyleSheet(_btn_style(MR_COLOR, MR_LIGHT))
        add_btn.clicked.connect(self._add_manpower_row)
        del_btn = QPushButton("Remove Selected Row")
        del_btn.setStyleSheet(_btn_style(MR_COLOR, MR_LIGHT))
        del_btn.clicked.connect(self._del_manpower_row)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        mp_prev_btn = QPushButton("◀ Prev Unmatched")
        mp_prev_btn.setToolTip("Jump to previous unmatched row")
        mp_prev_btn.setStyleSheet(_btn_style("#B71C1C", "#FFEBEE"))
        mp_prev_btn.clicked.connect(
            lambda: self._jump_unmatched(self._req_manpower_tbl, _MP_STATUS, -1))
        mp_next_btn = QPushButton("Next Unmatched ▶")
        mp_next_btn.setToolTip("Jump to next unmatched row")
        mp_next_btn.setStyleSheet(_btn_style("#B71C1C", "#FFEBEE"))
        mp_next_btn.clicked.connect(
            lambda: self._jump_unmatched(self._req_manpower_tbl, _MP_STATUS, +1))
        btn_row.addWidget(mp_prev_btn)
        btn_row.addWidget(mp_next_btn)
        gl.addLayout(btn_row)

        # PDMS proposal only: the standing "Hotbed – if required" labour line. Rate (editable,
        # standard 59) × Days = its total; left out of the proposal while either is blank. It is a
        # line of its own, not a table row, so no matching / encoding / "missing value" rule applies.
        self._hotbed_widgets = []
        hotbed_row = QHBoxLayout()
        _hb_lbl = QLabel(f"PDMS — {pdms_defaults()['hotbed_label']}:")
        _hb_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._hotbed_incl_chk = QCheckBox("Include")
        self._hotbed_incl_chk.setChecked(True)
        self._hotbed_incl_chk.setToolTip(
            "Ticked (standard): the Hotbed line is always in the proposal — with a rate and days it is "
            "calculated; with one of them missing the line just carries what is given, with no total.\n"
            "Unticked: a line with a rate and days is still calculated and included; an incomplete one "
            "is left out.")
        self._hotbed_rate_edit = QLineEdit(f"{float(pdms_defaults()['hotbed_rate']):g}")
        self._hotbed_rate_edit.setFixedWidth(70)
        self._hotbed_rate_edit.setToolTip("Rate per day. Standard 59; change it when this job differs.")
        self._hotbed_days_edit = QLineEdit()
        self._hotbed_days_edit.setFixedWidth(70)
        self._hotbed_days_edit.setPlaceholderText("days")
        self._hotbed_days_edit.setToolTip("Days of hotbed. With a rate and days the total is calculated; "
                                          "without them the line is inserted without a total (see Include).")
        for _e in (self._hotbed_rate_edit, self._hotbed_days_edit):
            install_formula_edit(_e)
        self._hotbed_cur_combo = QComboBox()
        self._hotbed_cur_combo.setToolTip("The currency of the Hotbed rate.")
        self._hotbed_total_lbl = QLabel("")
        self._hotbed_total_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        for _w in (_hb_lbl, self._hotbed_incl_chk, QLabel("Rate"), self._hotbed_rate_edit, self._hotbed_cur_combo,
                   QLabel("× Days"),
                   self._hotbed_days_edit, self._hotbed_total_lbl):
            hotbed_row.addWidget(_w)
            self._hotbed_widgets.append(_w)
        hotbed_row.addStretch()
        gl.addLayout(hotbed_row)
        self._hotbed_rate_edit.textChanged.connect(self._update_hotbed_total)
        self._hotbed_days_edit.textChanged.connect(self._update_hotbed_total)
        self._hotbed_incl_chk.toggled.connect(lambda _on: self._update_hotbed_total())
        self._update_hotbed_total()                  # the label says what the line will do from the start
        self._hotbed_cur_combo.currentTextChanged.connect(lambda _t: self._update_hotbed_total())
        for _w in self._hotbed_widgets:
            _w.setVisible(False)

        tot_row = QHBoxLayout()
        self._azn_onshore_lbl = QLabel("Project Support:  ₼ 0.00")
        self._azn_onshore_lbl.setStyleSheet(
            f"color: {MR_COLOR}; font-weight: bold; font-size: 12px;")
        self._azn_offshore_lbl = QLabel("Total Offshore:  ₼ 0.00")
        self._azn_offshore_lbl.setStyleSheet(
            f"color: {MR_COLOR}; font-weight: bold; font-size: 12px;")
        self._azn_total_lbl = QLabel("AZN CTR Total:  ₼ 0.00")
        self._azn_total_lbl.setStyleSheet(
            f"color: {MR_COLOR}; font-weight: bold; font-size: 12px;")
        tot_row.addWidget(self._azn_onshore_lbl)
        tot_row.addSpacing(20)
        tot_row.addWidget(self._azn_offshore_lbl)
        tot_row.addSpacing(20)
        tot_row.addWidget(self._azn_total_lbl)
        tot_row.addStretch()
        gl.addLayout(tot_row)

        parent_layout.addWidget(grp)

    # ── Section 2b: Equipment ─────────────────────────────────────────────────

    def _build_equipment_section(self, parent_layout: QVBoxLayout):
        grp = QGroupBox("Plant & Equipment — USD")
        self._groups = getattr(self, "_groups", {})
        self._groups["equipment"] = grp
        grp.setStyleSheet(_group_css(CTR_COLOR))
        gl = QVBoxLayout(grp)
        gl.setSpacing(6)

        top_btn_row = QHBoxLayout()
        rematch_btn = QPushButton("Re-match All Equipment")
        rematch_btn.setToolTip(
            "Re-run matching for every Equipment row against the currently "
            "loaded USD pricebook / Equipment Names DB. Use this if you "
            "load the CTR Request before loading the Pricebook-Based files."
        )
        rematch_btn.setStyleSheet(_btn_style(CTR_COLOR, CTR_LIGHT))
        rematch_btn.clicked.connect(lambda: self._ask_rematch("equipment"))
        top_btn_row.addWidget(rematch_btn)

        manage_btn = QPushButton("Manage Equipment Renames…")
        manage_btn.setToolTip(
            "View or delete Equipment renames learned from your \"Match By\" edits."
        )
        manage_btn.setStyleSheet(_btn_style(MUTED, "#F5F5F5"))
        manage_btn.clicked.connect(lambda: self._open_alias_manager("equipment"))
        top_btn_row.addWidget(manage_btn)
        top_btn_row.addStretch()
        gl.addLayout(top_btn_row)
        gl.addWidget(self._rematch_note())

        search_row = QHBoxLayout()
        search_lbl = QLabel("Search:")
        search_lbl.setFixedWidth(50)
        search_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._equip_search = QLineEdit()
        self._equip_search.setPlaceholderText("Filter rows by any column…")
        self._equip_search.textChanged.connect(
            lambda t: self._filter_table(self._req_equip_tbl, t))
        search_row.addWidget(search_lbl)
        search_row.addWidget(self._equip_search)
        gl.addLayout(search_row)

        self._req_equip_tbl = _make_table(_EQUIPMENT_HEADERS, stretch_col=_EQ_DESC)
        self._req_equip_tbl.setMinimumHeight(180)
        self._req_equip_tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._req_equip_tbl.cellChanged.connect(self._on_equip_changed)
        gl.addWidget(self._req_equip_tbl)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Row")
        add_btn.setStyleSheet(_btn_style(CTR_COLOR, CTR_LIGHT))
        add_btn.clicked.connect(self._add_equip_row)
        del_btn = QPushButton("Remove Selected Row")
        del_btn.setStyleSheet(_btn_style(CTR_COLOR, CTR_LIGHT))
        del_btn.clicked.connect(self._del_equip_row)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        eq_prev_btn = QPushButton("◀ Prev Unmatched")
        eq_prev_btn.setToolTip("Jump to previous unmatched row")
        eq_prev_btn.setStyleSheet(_btn_style("#B71C1C", "#FFEBEE"))
        eq_prev_btn.clicked.connect(
            lambda: self._jump_unmatched(self._req_equip_tbl, _EQ_STATUS, -1))
        eq_next_btn = QPushButton("Next Unmatched ▶")
        eq_next_btn.setToolTip("Jump to next unmatched row")
        eq_next_btn.setStyleSheet(_btn_style("#B71C1C", "#FFEBEE"))
        eq_next_btn.clicked.connect(
            lambda: self._jump_unmatched(self._req_equip_tbl, _EQ_STATUS, +1))
        btn_row.addWidget(eq_prev_btn)
        btn_row.addWidget(eq_next_btn)
        gl.addLayout(btn_row)

        tot_row = QHBoxLayout()
        self._usd_equip_lbl = QLabel("Equipment:  $0.00")
        self._usd_equip_lbl.setStyleSheet(
            f"color: {CTR_COLOR}; font-weight: bold; font-size: 12px;")
        tot_row.addWidget(self._usd_equip_lbl)
        tot_row.addStretch()
        gl.addLayout(tot_row)

        parent_layout.addWidget(grp)

    # ── Section 2c: Consumables ───────────────────────────────────────────────

    def _build_consumables_section(self, parent_layout: QVBoxLayout):
        grp = QGroupBox("Consumables — USD")
        self._groups = getattr(self, "_groups", {})
        self._groups["consumables"] = grp
        grp.setStyleSheet(_group_css(MUTED))
        gl = QVBoxLayout(grp)
        gl.setSpacing(4)

        top_btn_row = QHBoxLayout()
        rematch_btn = QPushButton("Re-match All Consumables")
        rematch_btn.setToolTip(
            "Re-run matching for every Consumables row against the currently "
            "loaded SAGE export. Use this if you load the CTR Request before "
            "loading the Pricebook-Based files."
        )
        rematch_btn.setStyleSheet(_btn_style(MUTED, "#F5F5F5"))
        rematch_btn.clicked.connect(lambda: self._ask_rematch("consumables"))
        top_btn_row.addWidget(rematch_btn)

        manage_btn = QPushButton("Manage Consumables Renames…")
        manage_btn.setToolTip(
            "View or delete Consumables renames learned from your \"Match By\" edits."
        )
        manage_btn.setStyleSheet(_btn_style(MUTED, "#F5F5F5"))
        manage_btn.clicked.connect(lambda: self._open_alias_manager("consumable"))
        top_btn_row.addWidget(manage_btn)
        top_btn_row.addStretch()
        gl.addLayout(top_btn_row)
        gl.addWidget(self._rematch_note())

        search_row = QHBoxLayout()
        search_lbl = QLabel("Search:")
        search_lbl.setFixedWidth(50)
        search_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._cons_search = QLineEdit()
        self._cons_search.setPlaceholderText("Filter rows by any column…")
        self._cons_search.textChanged.connect(
            lambda t: self._filter_table(self._req_cons_tbl, t))
        search_row.addWidget(search_lbl)
        search_row.addWidget(self._cons_search)
        gl.addLayout(search_row)

        self._req_cons_tbl = _make_table(_CONSUMABLES_HEADERS, stretch_col=_CS_DESC)
        self._req_cons_tbl.setMinimumHeight(150)
        self._req_cons_tbl.setMaximumHeight(300)
        self._req_cons_tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._req_cons_tbl.cellChanged.connect(self._on_cons_changed)
        gl.addWidget(self._req_cons_tbl)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Row")
        add_btn.setStyleSheet(_btn_style(MUTED, "#F5F5F5"))
        add_btn.clicked.connect(self._add_cons_row)
        del_btn = QPushButton("Remove Selected Row")
        del_btn.setStyleSheet(_btn_style(MUTED, "#F5F5F5"))
        del_btn.clicked.connect(self._del_cons_row)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        cs_prev_btn = QPushButton("◀ Prev Unmatched")
        cs_prev_btn.setToolTip("Jump to previous unmatched row")
        cs_prev_btn.setStyleSheet(_btn_style("#B71C1C", "#FFEBEE"))
        cs_prev_btn.clicked.connect(
            lambda: self._jump_unmatched(self._req_cons_tbl, _CS_STATUS, -1))
        cs_next_btn = QPushButton("Next Unmatched ▶")
        cs_next_btn.setToolTip("Jump to next unmatched row")
        cs_next_btn.setStyleSheet(_btn_style("#B71C1C", "#FFEBEE"))
        cs_next_btn.clicked.connect(
            lambda: self._jump_unmatched(self._req_cons_tbl, _CS_STATUS, +1))
        btn_row.addWidget(cs_prev_btn)
        btn_row.addWidget(cs_next_btn)
        gl.addLayout(btn_row)

        tot_row = QHBoxLayout()
        self._usd_cons_lbl = QLabel("Consumables (incl. 6.5% markup):  $0.00")
        self._usd_cons_lbl.setStyleSheet(f"color: {MUTED}; font-weight: bold; font-size: 12px;")
        self._usd_total_lbl = QLabel("USD CTR Total:  $0.00")
        self._usd_total_lbl.setStyleSheet(f"color: {MUTED}; font-weight: bold; font-size: 12px;")
        tot_row.addWidget(self._usd_cons_lbl)
        tot_row.addSpacing(20)
        tot_row.addWidget(self._usd_total_lbl)
        tot_row.addStretch()
        gl.addLayout(tot_row)

        parent_layout.addWidget(grp)

    # ── Section 2d: Additional Info / Transport / Scaffold ────────────────────

    def _build_extras_section(self, parent_layout: QVBoxLayout):
        grp = QGroupBox("Additional Info & Transport — AZN")
        self._groups = getattr(self, "_groups", {})
        self._groups["transport"] = grp
        grp.setStyleSheet(_group_css(CTR_COLOR))
        gl = QVBoxLayout(grp)
        gl.setSpacing(4)

        info = QLabel(
            "Read from the CTR Request's \"Additional Information / Type of "
            "Scaffold System / TRANSPORT\" block. Transport lines become the "
            "AZN CTR's \"Third Party Activities\" section — the request only "
            "says which vehicle, how many and for how long, so the rate, "
            "mark-up and duration unit start from the transport rate table "
            "in template_config.json and are editable here. A requested "
            "scaffold tonnage is added to the Equipment table above instead, "
            "as an ordinary USD Plant & Equipment line — name, quantity and "
            "TON, with the rate and days set there like any other line."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        gl.addWidget(info)

        self._required_info_lbl = QLabel("Additional Information required:  (none)")
        self._required_info_lbl.setWordWrap(True)
        self._required_info_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._required_info_lbl.setToolTip(
            "Items the CTR Request marked \"Required\". Written into the AZN "
            "CTR's dedicated Comments box."
        )
        gl.addWidget(self._required_info_lbl)

        self._transport_tbl = _make_table(_TRANSPORT_HEADERS, stretch_col=_TR_DESC)
        self._transport_tbl.setMinimumHeight(120)
        self._transport_tbl.setMaximumHeight(220)
        self._transport_tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._transport_tbl.cellChanged.connect(self._on_transport_changed)
        gl.addWidget(self._transport_tbl)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Row")
        add_btn.setStyleSheet(_btn_style(MUTED, "#F5F5F5"))
        add_btn.clicked.connect(self._add_transport_row)
        del_btn = QPushButton("Remove Selected Row")
        del_btn.setStyleSheet(_btn_style(MUTED, "#F5F5F5"))
        del_btn.clicked.connect(self._del_transport_row)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        gl.addLayout(btn_row)

        self._azn_third_party_lbl = QLabel("Total Third Party Activities:  ₼ 0.00")
        self._azn_third_party_lbl.setStyleSheet(
            f"color: {MUTED}; font-weight: bold; font-size: 12px;")
        gl.addWidget(self._azn_third_party_lbl)

        parent_layout.addWidget(grp)

    # ── Cost statistics box (MA131) ───────────────────────────────────────────

    def _build_stats_section(self, parent_layout: QVBoxLayout):
        grp = QGroupBox("Cost statistics — SAGE expected cost of this request (Equipment & Consumables)")
        self._stats_grp = grp
        gl = QVBoxLayout(grp)
        gl.setSpacing(4)

        info = QLabel(
            "What the loaded CTR Request costs at SAGE's expected unit cost, split into "
            "rechargeable and non-rechargeable. Rechargeability is read the way the CTR "
            "itself is generated: a line priced NONRECHARG is non-rechargeable, a priced "
            "line is rechargeable (up for discussion with the department). Only matched "
            "(✓) lines are counted — the ones that go into the CTR — and scaffold is not "
            "included. This is SAGE's expected cost, not what the client is billed."
        )
        self._stats_info = info
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        gl.addWidget(info)

        warn_row = QHBoxLayout()
        self._qty_warn_lbl = QLabel("")
        self._qty_warn_lbl.setWordWrap(True)
        self._qty_warn_lbl.setStyleSheet(
            "background: #FFF8E1; color: #6D4C00; border: 1px solid #FFD54F;"
            "border-radius: 4px; padding: 6px 10px; font-size: 12px;")
        self._qty_warn_lbl.setVisible(False)
        # One button, two ways in: clicking it goes to the next value (a missing
        # required value, then a Stock Code not in SAGE); its ▾ arrow opens a
        # list to choose from.
        self._qty_jump_btn = QToolButton()
        self._qty_jump_btn.setText("Go to value")
        self._qty_jump_btn.setToolTip(
            "Click: go to the next value that needs attention (a missing required value, "
            "then a Stock Code that is not in SAGE).\nArrow: choose from the list.")
        self._qty_jump_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._qty_jump_btn.setVisible(False)
        self._qty_jump_btn.clicked.connect(self._jump_empty_qty)
        self._goto_menu = QMenu(self._qty_jump_btn)
        self._goto_list = QListWidget()
        self._goto_list.itemClicked.connect(self._on_goto_picked)
        self._goto_list.itemActivated.connect(self._on_goto_picked)      # Enter
        _act = QWidgetAction(self._goto_menu)
        _act.setDefaultWidget(self._goto_list)
        self._goto_menu.addAction(_act)
        self._goto_menu.aboutToShow.connect(self._fill_goto_menu)
        self._qty_jump_btn.setMenu(self._goto_menu)
        self._goto_shown: list[dict] = []
        warn_row.addWidget(self._qty_warn_lbl, 1)
        warn_row.addWidget(self._qty_jump_btn)
        gl.addLayout(warn_row)

        self._stats_panel = StatsPanel(
            group_labels={"CTR": "CTR Request — equipment & consumables"},
            show_type_filter=False, min_tree_height=240, show_markup=True,
            no_sage_note="Please enter their cost by hand: a Stock Code that is not in SAGE has no SAGE "
                         "cost, so the CTR's internal SC cost cell stays EMPTY for it until you do.")
        self._stats_panel.reload_requested.connect(self._refresh_stats)
        self._stats_panel.row_clicked.connect(self._show_stats_row)
        self._stats_panel.choices_changed.connect(self._on_choices_changed)
        self._stats_panel.set_choices(load_choices())          # remembered from earlier runs
        self._stats_panel.view_changed.connect(self._refresh_price_totals)
        self._stats_panel.view_changed.connect(self._sync_stats_texts)
        self.stats_defaults_changed.connect(self._stats_panel.apply_defaults)
        gl.addWidget(self._stats_panel)
        self._sync_stats_texts()

        # Price totals of the request, per category and per currency (AZN and USD are never added).
        self._price_totals_lbl = QLabel("")
        self._price_totals_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._price_totals_lbl.setWordWrap(True)
        self._price_totals_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        gl.addWidget(self._price_totals_lbl)

        self._stats_note_lbl = QLabel("")
        self._stats_note_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        gl.addWidget(self._stats_note_lbl)

        save_row = QHBoxLayout()
        self._stats_save_btn = QPushButton("Save statistics (Excel)…")
        self._stats_save_btn.setToolTip(
            "Save these statistics as an Excel file: per-side summary, every line "
            "with its unit cost, and a sheet of notes. You choose the folder; it is "
            "remembered and shown again before each save.")
        self._stats_save_btn.clicked.connect(self._save_stats_excel)
        save_row.addWidget(self._stats_save_btn)
        save_row.addStretch(1)
        gl.addLayout(save_row)

        # Refreshes shortly after any edit / load, not on every keystroke.
        self._stats_timer = QTimer(self)
        self._stats_timer.setSingleShot(True)
        self._stats_timer.setInterval(250)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._empty_qty_idx = -1

        parent_layout.addWidget(grp)
        self._refresh_stats()

    @staticmethod
    def _num_missing(text: str, allow_nonrecharg: bool = False) -> bool:
        """No usable number: blank, or not a number. A rate/price may also be
        the NONRECHARG sentinel — a non-rechargeable line needs no rate, a
        rechargeable one does."""
        t = text.strip()
        if allow_nonrecharg and t.upper() == "NONRECHARG":
            return False
        try:
            v = float(t)
        except ValueError:
            return True
        return v != v

    def _required_specs(self):
        """(table, section, description column, status column or None, restore
        row tint, ((column, field name, kind), …)) for each Generator table.
        kind: "num" = a number, "rate" = a number or NONRECHARG, "text" = any
        text. Only rows that go into the CTR (✓ status; every transport row)
        are checked."""
        return (
            (self._req_manpower_tbl, "Manpower", _MP_DESC, _MP_STATUS,
             ((_MP_NUMEMP, "Num Employees", "num"), (_MP_QTY, "Qty (hours)", "num"),
              (_MP_RATE, "Rate", "num"), (_MP_SHIFT, "Shift", "text"),
              (_MP_SHIFTTYPE, "Shift Type", "text"), (_MP_NAT, "Nationality", "text"))),
            (self._req_equip_tbl, "Equipment", _EQ_DESC, _EQ_STATUS,
             ((_EQ_QTY, "Qty", "num"), (_EQ_UNIT, "Unit", "text"),
              (_EQ_RATE, "Rate", "rate"), (_EQ_DAYS, "Days", "num"))),
            (self._req_cons_tbl, "Consumables", _CS_DESC, _CS_STATUS,
             ((_CS_QTY, "Qty", "num"), (_CS_UNIT, "Unit", "text"),
              (_CS_PRICE, "Price", "rate"))),
            (self._transport_tbl, "Transport", _TR_DESC, None,
             ((_TR_QTY, "Qty", "num"), (_TR_DUR, "Duration", "num"),
              (_TR_UOM, "Unit", "text"), (_TR_RATE, "Rate", "num"))),
        )

    def _incomplete_cells(self) -> list[dict]:
        """Every required cell that is blank / not a number on a row that goes
        into the CTR. Nothing is ever assumed in its place: Generate leaves
        such a row out (after the user confirms), and the cell shows in red.
        Each entry: {tbl, row, col, section, desc, field}."""
        out = []
        for tbl, section, c_desc, c_status, fields in self._required_specs():
            for r in range(tbl.rowCount()):
                if c_status is not None:
                    st = tbl.item(r, c_status)
                    if not (st and st.text().startswith("✓")):
                        continue
                d = tbl.item(r, c_desc)
                desc = d.text().strip() if d else ""
                if not desc and tbl is self._transport_tbl:
                    ty = tbl.item(r, _TR_TYPE)
                    desc = ty.text().strip() if ty else ""
                for col, field, kind in fields:
                    it = tbl.item(r, col)
                    text = it.text() if it else ""
                    if kind == "text":
                        missing = not text.strip()
                    else:
                        missing = self._num_missing(text, kind == "rate")
                    if missing:
                        out.append({"tbl": tbl, "row": r, "col": col, "section": section,
                                    "desc": desc or f"row {r + 1}", "field": field})
        return out

    def _mark_incomplete(self, cells: list[dict] | None = None):
        """Tint the missing required cells red, and take the tint off cells
        filled in since (the row goes back to its normal highlight)."""
        red = QColor("#FFCDD2")
        bad: dict[int, set[tuple[int, int]]] = {}
        for c in (self._incomplete_cells() if cells is None else cells):
            bad.setdefault(id(c["tbl"]), set()).add((c["row"], c["col"]))
        tgt = getattr(self, "_jump_target", None)
        if tgt is not None and (tgt[1], tgt[2]) not in bad.get(id(tgt[0]), set()):
            self._set_jump_target(None)          # filled in since: the orange goes
        for tbl, _sec, _c_desc, c_status, fields in self._required_specs():
            wrong = bad.get(id(tbl), set())
            tbl.blockSignals(True)     # setBackground would fire cellChanged
            try:
                for r in range(tbl.rowCount()):
                    tinted = False
                    for col, _f, _k in fields:
                        it = tbl.item(r, col)
                        if it is not None and (
                                it.background().style() == Qt.BrushStyle.SolidPattern
                                and it.background().color() == red):
                            tinted = True
                            break
                    here = [col for col, _f, _k in fields if (r, col) in wrong]
                    if not tinted and not here:
                        continue
                    if c_status is None:
                        _set_row_tint(tbl, r, None)
                    else:
                        st = tbl.item(r, c_status)
                        _highlight_row(tbl, r, bool(st) and st.text().startswith("✓"))
                    for col in here:
                        it = tbl.item(r, col)
                        if it is not None:
                            it.setBackground(QBrush(red))
            finally:
                tbl.blockSignals(False)

    def _set_jump_target(self, target) -> None:
        """Mark `target` = (table, row, col) as the orange "you are here" cell
        (None clears it). The flag is data on the item, so signals are blocked."""
        prev = getattr(self, "_jump_target", None)
        for spot, on in ((prev, False), (target, True)):
            if spot is None:
                continue
            tbl, r, c = spot
            it = tbl.item(r, c) if r < tbl.rowCount() else None
            if it is not None:
                was = tbl.blockSignals(True)
                it.setData(_JUMP_ROLE, True if on else None)
                tbl.blockSignals(was)
                tbl.viewport().update()
        self._jump_target = target

    def _show_stats_row(self, side: str, doc: str, key: str) -> None:
        """A row of the Cost statistics list was clicked: show that line where it
        lives — the Equipment or Consumables table — selected (blue) and scrolled
        into view. A document row ("Equipment" / "Consumables") shows the table."""
        if doc == "Equipment":
            tbl, c_desc, c_match, c_code, c_status = (
                self._req_equip_tbl, _EQ_DESC, _EQ_MATCH, _EQ_CODE, _EQ_STATUS)
        elif doc == "Consumables":
            tbl, c_desc, c_match, c_code, c_status = (
                self._req_cons_tbl, _CS_DESC, _CS_MATCH, _CS_CODE, _CS_STATUS)
        else:
            return
        rows = []
        if key:
            for r in range(tbl.rowCount()):
                st = tbl.item(r, c_status)
                d = tbl.item(r, c_desc)
                if not (st and st.text().startswith("✓")) or (d is not None and d.data(_SCAFFOLD_ROLE)):
                    continue
                def _t(c, _r=r):
                    it = tbl.item(_r, c)
                    return it.text().strip() if it else ""
                if cost_stats.norm_key(_t(c_match) or _t(c_code) or _t(c_desc)) == key:
                    rows.append(r)
            if not rows:
                return
        self._set_jump_target(None)
        for other, *_rest in self._required_specs():
            other.clearSelection()
        if rows:
            sel = tbl.selectionModel()
            for r in rows:
                sel.select(tbl.model().index(r, 0),
                           QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            tbl.scrollToItem(tbl.item(rows[0], c_desc), QAbstractItemView.ScrollHint.PositionAtCenter)
            pt = tbl.viewport().mapTo(self._content_widget, tbl.visualItemRect(tbl.item(rows[0], c_desc)).center())
            self._content_scroll.ensureVisible(pt.x(), pt.y(), 50, 200)
        else:                                          # a document row: bring the whole table into view
            pt = tbl.mapTo(self._content_widget, QPoint(0, 0))
            self._content_scroll.verticalScrollBar().setValue(max(0, pt.y() - 70))

    def _goto_items(self) -> list[dict]:
        """Everything "Go to value" can take you to, in order: the required
        values still missing, then the Stock Codes SAGE does not have (whose
        cost has to be typed in by hand). Each: {label, kind, payload}."""
        items = [{"label": f"{c['section']} — {c['desc']} — {c['field']}", "kind": "missing", "payload": c}
                 for c in self._incomplete_cells()]
        for side, doc, key in self._stats_panel.no_sage_keys():
            items.append({"label": f"Not in SAGE — {key}  ({doc})", "kind": "sage",
                          "payload": (side, doc, key)})
        return items

    def _goto_item(self, item: dict) -> None:
        if item["kind"] == "missing":
            self._jump_to_cell(item["payload"])
            return
        side, doc, key = item["payload"]
        self._set_jump_target(None)
        for tbl, *_rest in self._required_specs():
            tbl.clearSelection()
        panel = self._stats_panel
        if panel.focus_key(side, doc, key):
            pt = panel._tree.viewport().mapTo(self._content_widget, panel._tree.visualItemRect(
                panel._tree.currentItem()).center()) if panel._tree.currentItem() else \
                panel.mapTo(self._content_widget, panel.rect().topLeft())
            self._content_scroll.ensureVisible(pt.x(), pt.y(), 50, 200)
        else:
            QMessageBox.information(
                self, "Not shown",
                f"“{key}” could not be shown in the statistics list (it may be beyond the "
                "first rows of its group) — narrow the list with the filters above it.")

    def _jump_to_cell(self, c: dict) -> None:
        tbl, row, col = c["tbl"], c["row"], c["col"]
        # Only one row selected (blue) across the four tables, and the missing
        # cell itself marked orange so it stands out inside that row.
        for other, *_rest in self._required_specs():
            if other is not tbl:
                other.clearSelection()
        tbl.clearSelection()
        # The tables open a cell editor whenever the current cell changes; hold
        # that back while selecting, so the jump shows the highlight instead of
        # an empty editor sitting in the row's first cell.
        triggers = tbl.editTriggers()
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        try:
            tbl.setCurrentCell(row, col)
            tbl.selectRow(row)
            tbl.setCurrentCell(row, col)
        finally:
            tbl.setEditTriggers(triggers)
        self._set_jump_target((tbl, row, col))
        item = tbl.item(row, col)
        tbl.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)
        tbl.setFocus()
        pt = tbl.viewport().mapTo(self._content_widget, tbl.visualItemRect(item).center())
        self._content_scroll.ensureVisible(pt.x(), pt.y(), 50, 150)

    def _jump_empty_qty(self):
        """The button's click: go to the next value in the list — a missing
        required value first, then the Stock Codes not in SAGE — and wrap round."""
        items = self._goto_items()
        if not items:
            return
        self._empty_qty_idx = (self._empty_qty_idx + 1) % len(items)
        self._goto_item(items[self._empty_qty_idx])

    def _fill_goto_menu(self) -> None:
        """Rebuild the drop-down list from what is still missing right now."""
        lst = self._goto_list
        lst.clear()
        self._goto_shown = self._goto_items()
        for it in self._goto_shown:
            lst.addItem(it["label"])
        lst.setFixedHeight(min(320, 24 * max(1, len(self._goto_shown)) + 8))
        lst.setMinimumWidth(460)

    def _on_goto_picked(self, row_item) -> None:
        idx = self._goto_list.row(row_item)
        if 0 <= idx < len(self._goto_shown):
            self._goto_menu.close()
            self._empty_qty_idx = idx            # "next" continues from the chosen one
            self._goto_item(self._goto_shown[idx])

    def _update_goto_button(self) -> None:
        n = len(self._goto_items())
        self._qty_jump_btn.setVisible(n > 0)
        if n:
            self._qty_jump_btn.setText(f"Go to value ({n})")

    def _stats_items(self) -> tuple[pd.DataFrame, int]:
        """The Equipment and Consumables lines that go into the CTR (and the scaffold
        line), as input for cost_stats. Returns (items, unmatched lines not counted).
        Each line carries its own description, rate cell and days, so the price view
        shows what the CTR would bill; scaffold is flagged and always rechargeable."""
        specs = (
            (self._req_equip_tbl, "Equipment", "Plant & Equipment",
             _EQ_DESC, _EQ_CODE, _EQ_MATCH, _EQ_QTY, _EQ_RATE, _EQ_STATUS, _EQ_DAYS),
            (self._req_cons_tbl, "Consumables", "Consumables & Materials",
             _CS_DESC, _CS_CODE, _CS_MATCH, _CS_QTY, _CS_PRICE, _CS_STATUS, None),
        )
        rows, unmatched = [], 0
        for tbl, label, section, c_desc, c_code, c_match, c_qty, c_rate, c_status, c_days in specs:
            for r in range(tbl.rowCount()):
                def txt(c, _r=r, _t=tbl):
                    item = _t.item(_r, c)
                    return item.text().strip() if item else ""
                st = tbl.item(r, c_status)
                if not (st and st.text().startswith("✓")):
                    unmatched += 1
                    continue
                desc_item = tbl.item(r, c_desc)
                scaffold = bool(desc_item is not None and desc_item.data(_SCAFFOLD_ROLE))
                key = txt(c_match) or txt(c_code) or txt(c_desc)
                if not key:
                    continue
                rate = txt(c_rate).upper()
                try:
                    float(rate)
                    status = "RECHARGE"
                except ValueError:
                    status = "NONRECHARG" if rate == "NONRECHARG" else None
                if scaffold:
                    status = "RECHARGE"               # scaffold is rechargeable
                rows.append({"key": key, "doc": label, "sheet": "", "side": "CTR",
                             "qty": txt(c_qty), "status": status, "section": section,
                             "description": txt(c_desc), "rate": txt(c_rate),
                             "days": txt(c_days) if c_days is not None else "",
                             "scaffold": scaffold})
        cols = ["key", "doc", "sheet", "side", "qty", "status", "section",
                "description", "rate", "days", "scaffold"]
        return pd.DataFrame(rows, columns=cols), unmatched

    def _refresh_stats(self):
        """Recompute the statistics box from the Equipment and Consumables
        tables. SAGE is already in memory (the same table the rows are
        matched with), so this is instant and runs on the UI thread."""
        if self._apply_all_choices_to_lines():          # a chosen SAGE value changed a line: this runs again
            return
        spin = getattr(self, "_markup_spin", None)
        if spin is not None:
            self._stats_panel.set_markup_rate(spin.value() / 100.0)
        self._refresh_stats_inner()
        self._update_goto_button()      # after the panel is rebuilt (it knows the not-in-SAGE codes)
        self._refresh_price_totals()

    _COST_TITLE = "Cost statistics — SAGE expected cost of this request (Equipment & Consumables)"
    _COST_INFO = None            # the original intro text, remembered on first use

    def _sync_stats_texts(self) -> None:
        """The box's title and intro follow the view: Document costs or Document price."""
        info, grp = getattr(self, "_stats_info", None), getattr(self, "_stats_grp", None)
        if info is None or grp is None:
            return
        if self._COST_INFO is None:
            CTRGeneratorWidget._COST_INFO = info.text()
        if self._stats_panel.mode() == "price":
            grp.setTitle("Price statistics — what this request bills (Equipment & Consumables)")
            info.setText(
                "What the loaded CTR Request bills: quantity × the line's rate (× days for equipment "
                "and scaffold), split into rechargeable and non-rechargeable. A non-rechargeable line "
                "is 0. Only matched (✓) lines are counted — the ones that go into the CTR. Scaffold "
                "and the markup on consumables are switched with the tick boxes below; the price "
                "totals under the list show every category. A line whose rate or days are missing is "
                "not counted (shown in red).")
        else:
            grp.setTitle(self._COST_TITLE)
            info.setText(self._COST_INFO)

    def price_totals(self) -> dict:
        """What the loaded request bills, per category, from the tables' own Total cells
        (the numbers a generated CTR carries). AZN: manpower + transport. USD: equipment
        (scaffold apart: it is added only when the statistics' "Include scaffold" is on)
        + consumables (with the Markup Rate when "With markup" is on). A row with a
        missing required value is left out, as Generate does, and counted in `left_out`.
        AZN and USD are never added together. Non-rechargeable lines are 0."""
        def cell(tbl, r, c) -> float:
            it = tbl.item(r, c)
            try:
                v = float(it.text()) if it else 0.0
            except ValueError:
                return 0.0
            return v if v == v else 0.0

        gone = {(id(c["tbl"]), c["row"]) for c in self._incomplete_cells()}
        left = {"manpower": 0, "transport": 0, "equipment": 0, "consumables": 0}

        def included(tbl, r, c_status, bucket):
            if c_status is not None:
                st = tbl.item(r, c_status)
                if not (st and st.text().startswith("✓")):
                    return False
            if (id(tbl), r) in gone:
                left[bucket] += 1
                return False
            return True

        manpower = sum(cell(self._req_manpower_tbl, r, _MP_TOTAL)
                       for r in range(self._req_manpower_tbl.rowCount())
                       if included(self._req_manpower_tbl, r, _MP_STATUS, "manpower"))
        transport = 0.0
        for r in range(self._transport_tbl.rowCount()):
            if included(self._transport_tbl, r, None, "transport"):
                if cell(self._transport_tbl, r, _TR_QTY) > 0:      # a line at 0 or less is hidden in the CTR
                    transport += cell(self._transport_tbl, r, _TR_TOTAL)
        equipment = scaffold = 0.0
        for r in range(self._req_equip_tbl.rowCount()):
            if included(self._req_equip_tbl, r, _EQ_STATUS, "equipment"):
                d = self._req_equip_tbl.item(r, _EQ_DESC)
                if d is not None and d.data(_SCAFFOLD_ROLE):
                    scaffold += cell(self._req_equip_tbl, r, _EQ_TOTAL)
                else:
                    equipment += cell(self._req_equip_tbl, r, _EQ_TOTAL)
        cons_raw = sum(cell(self._req_cons_tbl, r, _CS_TOTAL)
                       for r in range(self._req_cons_tbl.rowCount())
                       if included(self._req_cons_tbl, r, _CS_STATUS, "consumables"))
        spin = getattr(self, "_markup_spin", None)
        markup = spin.value() / 100.0 if spin is not None and self._stats_panel.markup_on() else 0.0
        consumables = cons_raw * (1.0 + markup)
        with_scaffold = self._stats_panel.include_scaffold()
        usd_total = equipment + consumables + (scaffold if with_scaffold else 0.0)
        hotbed = 0.0                                      # the PDMS proposal's Hotbed line (rate × days, in manats)
        hb = self._hotbed_values() if self._pdms_on() else None
        if hb and hb["rate"] and hb["days"]:          # calculated only when both are given
            code = (hb["currency"] or "AZN").upper()
            d = self._fx.get(code)
            rate = 1.0 if code == "AZN" else (cbar_rates.parse_typed_rate(d["edit"].text()) if d else None)
            hotbed = hb["rate"] * hb["days"] * (rate if rate and rate > 0 else 0.0)
        return {"manpower": manpower, "transport": transport, "hotbed": hotbed,
                "azn_total": manpower + transport + hotbed,
                "equipment": equipment, "scaffold": scaffold, "scaffold_included": with_scaffold,
                "consumables": consumables, "markup": markup, "usd_total": usd_total, "left_out": left}

    def _refresh_price_totals(self) -> None:
        """Show price_totals() under the statistics: one block per currency."""
        t = self.price_totals()
        left = t["left_out"]
        waiting = sum(1 for tbl, c in ((self._req_manpower_tbl, _MP_STATUS), (self._req_equip_tbl, _EQ_STATUS),
                                       (self._req_cons_tbl, _CS_STATUS))
                      for r in range(tbl.rowCount())
                      if (tbl.item(r, c) is not None and tbl.item(r, c).text().startswith("⏳")))

        def note(*keys):
            n = sum(left[k] for k in keys)
            return (f" <span style='color:#C62828'>({n} line(s) not priced — a required value is missing)</span>"
                    if n else "")

        def row(label, amount, symbol, extra="", bold=False):
            amt = f"{symbol} {amount:,.2f}"
            if bold:
                label, amt = f"<b>{label}</b>", f"<b>{amt}</b>"
            return (f"<tr><td style='padding-right:18px'>{label}</td>"
                    f"<td align='right' style='padding-right:12px'>{amt}</td><td>{extra}</td></tr>")

        scaffold_txt = (f"Scaffold included: {t['scaffold']:,.2f}" if t["scaffold_included"] else
                        f"Scaffold not included ({t['scaffold']:,.2f}) — tick “Include scaffold” in Document price"
                        if t["scaffold"] else "Scaffold not included")
        markup_txt = (f"with {t['markup'] * 100:g}% markup" if t["markup"] else
                      "without markup (tick “With markup” in Document price)")
        pdms = self._pdms_on()
        hb_on = pdms and self._hotbed_complete()
        sign_usd = "₼" if pdms else "$"                    # PDMS mode: every amount is in manats
        usd_note = ("<span style='color:#757575'>(converted to manats)</span> " if pdms else "")
        wait_note = (f"<br><span style='color:#E65100'>⏳ Waiting for Load Files — {waiting} line(s) cannot be "
                     "matched or priced yet, so they are not in these totals.</span>" if waiting else "")
        html = (
            "<div style='font-size:11px'>"
            "<b>Price totals</b> — what this request bills (AZN and USD are never added together)"
            + wait_note
            + "<table cellspacing='2'>"
            + row("AZN — Manpower", t["manpower"], "₼",
                  ("<span style='color:#757575'>(without the Hotbed line — it is listed below)</span> " if hb_on else "")
                  + note("manpower"))
            + row("AZN — Transport (Third Party Activities)", t["transport"], "₼", note("transport"))
            + (row("AZN — Hotbed (PDMS proposal)", t["hotbed"], "₼",
                   ("<span style='color:#C62828'>(no CBAR rate for "
                    f"{self._hotbed_cur_combo.currentText()} yet)</span>"
                    if hb_on and not t["hotbed"] else "<span style='color:#757575'>rate × days</span>"))
               if pdms else "")
            + row("AZN CTR total", t["azn_total"], "₼", bold=True)
            + row("USD — Equipment", t["equipment"] + (t["scaffold"] if t["scaffold_included"] else 0.0), sign_usd,
                  usd_note + scaffold_txt + note("equipment"))
            + row("USD — Consumables", t["consumables"], sign_usd, usd_note + markup_txt + note("consumables"))
            + row("USD CTR total", t["usd_total"], sign_usd, bold=True)
            + "</table></div>")
        self._price_totals_lbl.setText(html)

    def _refresh_stats_inner(self):
        cells = self._incomplete_cells()
        self._mark_incomplete(cells)
        self._qty_warn_lbl.setVisible(bool(cells))
        if cells:
            per: dict[str, set] = {}
            for c in cells:
                per.setdefault(c["section"], set()).add((id(c["tbl"]), c["row"]))
            parts = ", ".join(f"{s}: {len(rows)} row(s)" for s, rows in per.items())
            self._qty_warn_lbl.setText(
                f"{len(cells)} required value(s) are missing (shown in red) — {parts}. "
                "Nothing is assumed in their place: fill them in, or Generate will leave "
                "those rows out of the CTR (after asking you). Equipment/Consumable rows "
                "without a quantity are also left out of the statistics.")

        panel = self._stats_panel
        if self._sage_df.empty:
            self._stats_note_lbl.setText("")
            panel.clear("⏳ Waiting for Load Files — the SAGE Export is not loaded yet. These statistics "
                        "appear here by themselves once the files are loaded: set the SAGE Export above "
                        "and press “Load Files”.", "warn")
            return
        items, unmatched = self._stats_items()
        self._stats_note_lbl.setText(
            f"{unmatched} line(s) are not matched yet and are not counted." if unmatched else "")
        if items.empty:
            panel.clear("Load a CTR Request (and let its lines match) to see its cost statistics here.",
                        "info")
            return
        cost_map: dict[str, float] = {}
        for key in set(items["key"]):
            m = _match_lookup(self._sage_df, "product", "long_description",
                              "local_expect_cost", key)
            if m:
                cost_map[cost_stats.norm_key(key)] = m[1]
        # SAGE's own rechargeability for each code, to spot lines the CTR calls
        # one thing and SAGE the other (those are listed under "Disparities").
        sage_tags: dict[str, str] = {}
        sage_names: dict[str, str] = {}
        index = _lookup_index(self._sage_df, "product")
        for key in set(items["key"]):
            nr = _sage_non_recharge(self._sage_df, key)
            if nr is not None:
                nk = cost_stats.norm_key(key)
                sage_tags[nk] = cost_stats.NON_RECHARGEABLE if nr else cost_stats.RECHARGEABLE
                pos = index.get(key.strip().lower())
                if pos is not None:
                    sage_names[nk] = str(self._sage_df.iloc[pos]["long_description"]).strip()
        panel.set_rows(cost_stats.cost_rows(items, cost_map, True, sage_tags=sage_tags,
                                            sage_names=sage_names))

    def _sage_cost_of(self, desc_item, key: str):
        """SAGE's unit cost for a line — or the one typed into the statistics
        box for a Stock Code SAGE does not have — or None when neither exists.
        None is written to the CTR as an empty cell; 0 is never assumed."""
        cost = desc_item.data(_SAGE_COST_ROLE) if desc_item is not None else None
        if cost is None:
            cost = self._stats_panel.manual_costs().get(cost_stats.norm_key(key))
        return cost

    # ── source choices for rechargeability (disparities) ─────────────────────

    def stats_panel(self) -> StatsPanel:
        return self._stats_panel

    def source_choices(self) -> dict[str, str]:
        return self._stats_panel.choices()

    def set_source_choices(self, choices: dict[str, str]) -> None:
        """The choices were changed in another statistics list: take them over."""
        self._stats_panel.set_choices(choices)
        save_choices(self._stats_panel.choices())
        self._refresh_stats()

    def _on_choices_changed(self, choices: dict) -> None:
        save_choices(choices)
        self._refresh_stats()

    def _apply_all_choices_to_lines(self) -> bool:
        """Where the user chose SAGE as the source of a Stock Code's rechargeability,
        make the Equipment / Consumables lines of that code say what SAGE says:
        SAGE non-rechargeable → the rate/price cell becomes NONRECHARG; SAGE
        rechargeable and the cell is NONRECHARG → the cell is emptied (a rate or
        price must be entered — nothing is assumed). Choosing the document changes
        no cell.

        The change is reversible: the cell's text before the first change is remembered, and when
        the choice is cleared (or the document is chosen instead) the line gets that text back —
        unless the user has edited the cell since (their edit is never overwritten).
        Returns True when a cell was changed."""
        tables = {
            "equipment": (self._req_equip_tbl, _EQ_MATCH, _EQ_CODE, _EQ_DESC, _EQ_RATE, _EQ_STATUS),
            "consumable": (self._req_cons_tbl, _CS_MATCH, _CS_CODE, _CS_DESC, _CS_PRICE, _CS_STATUS),
        }

        def line_key(tbl, r, c_match, c_code, c_desc) -> str:
            def _t(c):
                it = tbl.item(r, c)
                return it.text().strip() if it else ""
            return cost_stats.norm_key(_t(c_match) or _t(c_code) or _t(c_desc))

        rewrites = self.__dict__.setdefault("_choice_rewrites", {})
        sage_codes = {k for k, v in self._stats_panel.choices().items() if v == "SAGE"}
        changed = False

        # 1. Give back what a choice changed, once the choice is gone.
        for (name, r), rec in list(rewrites.items()):
            tbl, c_match, c_code, c_desc, c_rate, _c_status = tables[name]
            cell = tbl.item(r, c_rate) if r < tbl.rowCount() else None
            if cell is None or line_key(tbl, r, c_match, c_code, c_desc) != rec["code"]:
                del rewrites[(name, r)]                       # the row is gone or is another line now
            elif rec["code"] not in sage_codes:
                if cell.text() == rec["written"]:              # still what we wrote: not edited since
                    cell.setText(rec["orig"])
                    changed = True
                del rewrites[(name, r)]

        # 2. Apply the SAGE choices.
        if sage_codes and not self._sage_df.empty:
            for name, (tbl, c_match, c_code, c_desc, c_rate, c_status) in tables.items():
                for r in range(tbl.rowCount()):
                    st = tbl.item(r, c_status)
                    if not (st and st.text().startswith("✓")):
                        continue
                    d = tbl.item(r, c_desc)
                    if d is not None and d.data(_SCAFFOLD_ROLE):
                        continue
                    nk = line_key(tbl, r, c_match, c_code, c_desc)
                    if nk not in sage_codes:
                        continue
                    key = (tbl.item(r, c_match).text().strip() if tbl.item(r, c_match) else "") or \
                          (tbl.item(r, c_code).text().strip() if tbl.item(r, c_code) else "") or \
                          (d.text().strip() if d else "")
                    nr = _sage_non_recharge(self._sage_df, key)
                    if nr is None:
                        continue
                    cell_item = tbl.item(r, c_rate)
                    old = cell_item.text() if cell_item else ""
                    cell = old.strip().upper()
                    new = None
                    if nr and cell != "NONRECHARG":
                        new = "NONRECHARG"
                    elif not nr and cell == "NONRECHARG":
                        new = ""
                    if new is None:
                        continue
                    rec = rewrites.get((name, r))
                    if rec is None or rec["code"] != nk:
                        rewrites[(name, r)] = {"code": nk, "orig": old, "written": new}
                    else:
                        rec["written"] = new
                    cell_item.setText(new)
                    changed = True
        return changed

    def _nonpositive_quantities(self, skip: set) -> tuple[list[str], list[str]]:
        """Quantities of 0 or less on the rows that will go into the CTR:
        (manpower / equipment / consumables entries, transport entries). Missing
        values are not listed here — those rows are handled by _incomplete_cells."""
        def val(tbl, r, c):
            it = tbl.item(r, c)
            try:
                return float(it.text()) if it else None
            except ValueError:
                return None
        general, transport = [], []
        for tbl, section, c_desc, c_status, fields in (
                (self._req_manpower_tbl, "Manpower", _MP_DESC, _MP_STATUS,
                 ((_MP_NUMEMP, "Num Employees"), (_MP_QTY, "Hours"))),
                (self._req_equip_tbl, "Equipment", _EQ_DESC, _EQ_STATUS, ((_EQ_QTY, "Qty"),)),
                (self._req_cons_tbl, "Consumables", _CS_DESC, _CS_STATUS, ((_CS_QTY, "Qty"),)),
                (self._transport_tbl, "Transport", _TR_DESC, None, ((_TR_QTY, "Qty"),))):
            for r in range(tbl.rowCount()):
                if (id(tbl), r) in skip:
                    continue
                if c_status is not None:
                    st = tbl.item(r, c_status)
                    if not (st and st.text().startswith("✓")):
                        continue
                d = tbl.item(r, c_desc)
                desc = (d.text().strip() if d else "") or f"row {r + 1}"
                for col, field in fields:
                    v = val(tbl, r, col)
                    if v is not None and v <= 0:
                        entry = f"{desc} — {field} {v:g}"
                        (transport if tbl is self._transport_tbl else general).append(
                            entry if tbl is self._transport_tbl else f"{section}: {entry}")
        return general, transport

    def _lines_without_sage_cost(self, skip: set) -> list[str]:
        """Descriptions of the ✓ Equipment/Consumable lines that will go into
        the CTR but have no SAGE cost (scaffold excluded: it has none)."""
        out = []
        for tbl, c_desc, c_match, c_code, c_status in (
                (self._req_equip_tbl, _EQ_DESC, _EQ_MATCH, _EQ_CODE, _EQ_STATUS),
                (self._req_cons_tbl, _CS_DESC, _CS_MATCH, _CS_CODE, _CS_STATUS)):
            for r in range(tbl.rowCount()):
                st = tbl.item(r, c_status)
                if not (st and st.text().startswith("✓")) or (id(tbl), r) in skip:
                    continue
                d = tbl.item(r, c_desc)
                if d is not None and d.data(_SCAFFOLD_ROLE):
                    continue
                def _t(c, _r=r, _tb=tbl):
                    it = _tb.item(_r, c)
                    return it.text().strip() if it else ""
                key = _t(c_match) or _t(c_code) or _t(c_desc)
                if self._sage_cost_of(d, key) is None:
                    out.append(_t(c_desc) or key)
        return out

    _STATS_DIR_KEY = "stats_save_dir"

    def _save_stats_excel(self):
        """Save the statistics box as an Excel file (Statistics, Statistics
        Items, Info). The folder is asked for, remembered, and shown again
        before every save so it can be changed or simply approved."""
        self._refresh_stats()
        frames = self._stats_panel.report_frames()
        if frames is None:
            QMessageBox.information(
                self, "Nothing to save",
                "There are no statistics yet — load the files and a CTR Request first "
                "(the box above must show figures).")
            return
        settings = self._settings()
        folder = str(settings.value(self._STATS_DIR_KEY, "") or "")
        if folder and not Path(folder).is_dir():
            folder = ""                       # remembered folder is gone: ask again
        job = self._job_ref_edit.text().strip()
        now = _datetime.now()
        filename = f"CTR_Statistics_{job or 'request'}_{now:%Y-%m-%d_%H%M}.xlsx"
        start = self._out_dir_edit.text().strip() or str(Path.home())
        dlg = _SaveLocationDialog(self, "Save statistics", folder, filename, start)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        chosen_folder = dlg.folder()
        dlg.deleteLater()
        if not accepted or not chosen_folder:
            return
        folder = chosen_folder
        settings.setValue(self._STATS_DIR_KEY, folder)     # remembered right away
        path = Path(folder) / filename

        _items, unmatched = self._stats_items()
        n_missing_rows = len({(id(c["tbl"]), c["row"]) for c in self._incomplete_cells()})
        info = pd.DataFrame([
            ("What this is", "SAGE expected cost of the loaded CTR Request (equipment & consumables), "
                             "split by rechargeability. Not what the client is billed."),
            ("Job ref", job),
            ("Saved", f"{now:%Y-%m-%d %H:%M}"),
            ("CTR Request file", self._combined_db_edit.text().strip()),
            ("SAGE Export", str(getattr(self, "_sage_df_path", "") or self._sage_edit.text().strip())),
            ("Cost basis", "unit cost × quantity" if self._stats_panel.use_qty() else "unit cost only"),
            ("Rechargeability", "From CTR generation: a line priced NONRECHARG is non-rechargeable, "
                                "a priced line is rechargeable (up for discussion with the department)."),
            ("Lines not matched (not counted)", unmatched),
            ("Rows with a required value missing", n_missing_rows),
            ("Scaffold", "Not in the cost sheets (it has no SAGE cost). The Price sheets include it "
                         "when “Include scaffold” is ticked."),
            ("Price sheets", "Price Statistics / Price Items: quantity × rate (× days for equipment and "
                             "scaffold) as the CTR lines carry them; non-rechargeable = 0; consumables "
                             + (f"with {self._markup_rate() * 100:g}% markup"
                                if self._stats_panel.markup_rate() else "without markup") + "."),
        ], columns=["Item", "Value"])
        pframes = self._stats_panel.price_report_frames()
        overview_note = ""
        try:
            from io import BytesIO
            buf = BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                sheets = [(frames[0], "Statistics"), (frames[1], "Statistics Items")]
                if pframes is not None:
                    sheets += [(pframes[0], "Price Statistics"), (pframes[1], "Price Items")]
                for df, name in (*sheets, (info, "Info")):
                    df.to_excel(writer, index=False, sheet_name=name)
                    ws = writer.sheets[name]
                    for i, col in enumerate(df.columns, start=1):
                        longest = max([len(str(col))] + [len(str(v)) for v in df[col].head(200)])
                        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(70, longest + 2)
                if self._pdms_on():
                    # "PDMS overview": the proposal's figures with their arithmetic, as the first sheet
                    try:
                        figures, hdr, days = self._pdms_overview_data()
                        add_overview_sheet(writer.book, figures, hdr, days, index=0)
                        writer.book.active = 0
                    except Exception as exc:       # a missing rate / shift length must not lose the other sheets
                        log.warning("PDMS overview sheet skipped: %s", exc)
                        overview_note = f"\n\nThe PDMS overview sheet was NOT added: {exc}"
            path.write_bytes(buf.getvalue())
            log.info("Statistics saved: %s", path)
            QMessageBox.information(self, "Saved", f"Statistics saved:\n{path}{overview_note}")
        except Exception as exc:
            log.exception("Failed to save statistics: %s", path)
            QMessageBox.critical(self, "Save error", str(exc))

    def _transport_row_values(self, row: int) -> dict:
        """One transport table row as the dict build_azn expects (mark-up as
        a fraction, not the percentage the table shows)."""
        def _txt(col: int) -> str:
            item = self._transport_tbl.item(row, col)
            return item.text().strip() if item else ""

        def _num(col: int) -> float | None:
            """The cell as a number, or None when blank / not a number —
            never a stand-in value (see _incomplete_cells)."""
            try:
                v = float(_txt(col))
            except ValueError:
                return None
            return v if v == v else None

        return {
            "comment":     "",
            "transport_type": _txt(_TR_TYPE),
            "description": _txt(_TR_DESC) or _txt(_TR_TYPE),
            "quantity":    _num(_TR_QTY),
            "duration":    _num(_TR_DUR),
            "uom":         _txt(_TR_UOM),
            "rate_azn":    _num(_TR_RATE),
            # A blank mark-up is "no mark-up" (a stated case, e.g. the fuel
            # line), not a missing figure.
            "markup":      (_num(_TR_MARKUP) or 0.0) / 100.0,
            "currency":    self._cell_currency("transport", self._transport_tbl, _TR_RATE, row),
        }

    def _append_transport_row(self, line: dict):
        tbl = self._transport_tbl
        r = tbl.rowCount()
        tbl.blockSignals(True)
        tbl.insertRow(r)
        tbl.setItem(r, _TR_TYPE,   _ro_item(str(line.get("transport_type", ""))))
        tbl.setItem(r, _TR_DESC,   QTableWidgetItem(str(line.get("description", ""))))
        tbl.setItem(r, _TR_QTY,    QTableWidgetItem(_request_qty_text(line.get("quantity"))))
        tbl.setItem(r, _TR_DUR,    QTableWidgetItem(_request_qty_text(line.get("duration"))))
        tbl.setItem(r, _TR_UOM,    QTableWidgetItem(str(line.get("uom", "Days"))))
        _rate_txt = _request_qty_text(line.get("rate_azn"))
        tbl.setItem(r, _TR_RATE,   QTableWidgetItem(f"{float(_rate_txt):.2f}" if _rate_txt else ""))
        # Trailing zeros trimmed so a plain rate reads "6.5", not "6.50",
        # while an odd one (6.25) keeps its precision.
        _pct = float(line.get("markup", 0) or 0) * 100
        tbl.setItem(r, _TR_MARKUP, QTableWidgetItem(f"{_pct:g}"))
        tbl.setItem(r, _TR_TOTAL,  _ro_item("0.00"))
        tbl.blockSignals(False)
        self._recalc_transport_row(r)

    def _add_transport_row(self):
        self._append_transport_row({
            "transport_type": "", "description": "", "quantity": "", "duration": "",
            "uom": "Days", "rate_azn": "", "markup": self._markup_rate(),
        })
        self._recalc_azn_totals()

    def _del_transport_row(self):
        rows = sorted({i.row() for i in self._transport_tbl.selectedItems()}, reverse=True)
        for r in rows:
            self._transport_tbl.removeRow(r)
        self._recalc_azn_totals()

    def _on_transport_changed(self, row: int, col: int):
        if col == _TR_TOTAL:
            return
        self._recalc_transport_row(row)
        self._recalc_azn_totals()

    def _recalc_transport_row(self, row: int):
        vals = self._transport_row_values(row)
        # Display only: a blank cell counts as 0 in the shown total; the row
        # is flagged and left out of the CTR until it is filled in.
        total = ((vals["quantity"] or 0.0) * (vals["rate_azn"] or 0.0)
                 * (vals["duration"] or 0.0) * (1 + vals["markup"])
                 * self._fx_factor("transport", self._transport_tbl, _TR_RATE, row))
        self._transport_tbl.blockSignals(True)
        self._transport_tbl.setItem(row, _TR_TOTAL, self._total_item(
            total, "transport", self._transport_tbl, _TR_RATE, row))
        self._transport_tbl.blockSignals(False)

    # ── Section 3: Generate ──────────────────────────────────────────────────

    def _build_generate_section(self, parent_layout: QVBoxLayout):
        grp = QGroupBox("Generate")
        grp.setStyleSheet(_group_css(PRIMARY))
        gl = QVBoxLayout(grp)
        gl.setSpacing(8)

        # ── CTR header info — visible & editable regardless of which
        # Source Files sub-tab was used; auto-filled by Load CTR Request.
        hdr_lbl = QLabel("CTR Header Info  (auto-filled from CTR Request, editable)")
        hf = hdr_lbl.font()
        hf.setBold(True)
        hdr_lbl.setFont(hf)
        hdr_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        gl.addWidget(hdr_lbl)

        def _hdr_row(label: str, default: str = ""):
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(95)
            lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
            edit = QLineEdit(default)
            row.addWidget(lbl)
            row.addWidget(edit)
            return row, edit

        row_c,   self._client_edit          = _hdr_row("Client:")
        row_sc,  self._sub_client_edit      = _hdr_row("Sub-Client:")
        row_l,   self._location_edit        = _hdr_row("Location:")
        row_s,   self._scope_edit           = _hdr_row("Scope:")
        row_d,   self._date_edit            = _hdr_row("Date:", _date.today().strftime("%Y-%m-%d"))
        row_cna, self._contract_no_azn_edit = _hdr_row("Contract No (AZN):")
        row_cnu, self._contract_no_usd_edit = _hdr_row("Contract No (USD):")
        row_rv,  self._revision_edit        = _hdr_row("Revision:", "0")
        row_pt,  self._project_type_edit    = _hdr_row("Project Type:", "Offshore")
        row_cm,  self._comments_edit        = _hdr_row("Comments:")
        self._comments_edit.setToolTip(
            "Free text from the CTR Request's own Comments box (O6). "
            "Written to C1 of both the AZN and USD CTR."
        )
        self._project_type_edit.setToolTip(
            "Onshore or Offshore — sets the AZN CTR's non-support manpower "
            "section header (\"Onshore Activities\" / \"Offshore Activities\")."
        )

        # PDMS proposal mode: a Word proposal instead of the AZN / USD CTRs. The same rows
        # and values; only the header differs (no Contract No / Sub-Client / Revision /
        # Comments — a PRP number, the addressee and the signer instead). The two sets of
        # rows share the same place in the layout: ticking the box swaps them, so the
        # window does not grow.
        dflt = pdms_defaults()
        self._pdms_check = QCheckBox("PDMS proposal (Word) instead of AZN / USD CTR")
        self._pdms_check.setToolTip(
            "Generates the PDMS proposal (.docx) from the PDMS Template, with the same "
            "calculated values. The AZN and USD Excel CTRs are not written.")
        gl.addWidget(self._pdms_check)
        row_prp, self._prp_edit      = _hdr_row("PRP No:")
        row_cu,  self._customer_edit = _hdr_row("Customer:", dflt["customer_name"])
        row_ci,  self._customer_city_edit = _hdr_row("Customer city:", dflt["customer_city"])
        row_at,  self._attention_edit = _hdr_row("Attention:")
        row_ld,  self._letter_date_edit = _hdr_row("Letter date:", _date.today().strftime("%Y-%m-%d"))
        row_sg,  self._signer_edit   = _hdr_row("Signer:", dflt["signer_name"])
        row_st,  self._signer_title_edit = _hdr_row("Signer title:", dflt["signer_title"])
        self._prp_edit.setPlaceholderText("e.g. PRP-26-056")
        self._attention_edit.setToolTip(
            "Filled from the requester in the CTR Request; \"Dear …,\" uses its first name.")
        # CBAR rates: one indented line per turned-on currency (Settings), each with its own
        # Reload button. PROVISIONAL (task log MA202): the proposal converts every price that is
        # not in AZN with these rates; how the colleagues want it done is not confirmed yet.
        self._fx: dict[str, dict] = {}
        self._cbar_worker = None
        self._fx_group = QWidget()
        self._fx_lay = QVBoxLayout(self._fx_group)
        self._fx_lay.setContentsMargins(0, 2, 0, 2)
        self._fx_lay.setSpacing(2)
        # The currency each category is priced in unless a row says otherwise (the ones the CTRs use),
        # and the currency of the Hotbed rate. A row's own Currency cell wins.
        self._cat_widget = QWidget()
        _cl = QHBoxLayout(self._cat_widget)
        _cl.setContentsMargins(0, 0, 0, 0)
        _cat_lbl = QLabel("Default currency:")
        _cat_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        _cl.addWidget(_cat_lbl)
        self._cat_combos: dict[str, QComboBox] = {}
        for _key, _title in (("manpower", "Manpower"), ("equipment", "Equipment"),
                             ("consumables", "Consumables"), ("transport", "Transport")):
            _l = QLabel(_title)
            _l.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
            _c = QComboBox()
            _c.setToolTip(f"The currency the {_title.lower()} prices are in unless a row's Currency cell says otherwise.")
            self._cat_combos[_key] = _c
            _cl.addWidget(_l)
            _cl.addWidget(_c)
        _cl.addStretch(1)
        self._normal_headers = {
            key: {col: tbl.horizontalHeaderItem(col).text() for col in cols}
            for key, tbl, cols in (("manpower", self._req_manpower_tbl, _PDMS_HEADERS["manpower"]),
                                   ("equipment", self._req_equip_tbl, _PDMS_HEADERS["equipment"]),
                                   ("consumables", self._req_cons_tbl, _PDMS_HEADERS["consumables"]),
                                   ("transport", self._transport_tbl, _PDMS_HEADERS["transport"]))}
        self._rebuild_fx_rows()
        self.cbar_currencies_changed.connect(self._rebuild_fx_rows)
        # A Rate / Price cell holds the number in its left half and its currency (a drop-down, shown
        # as the currency's sign) in its right half; the currency is kept on the cell, not in a column.
        for _tbl, _cols in ((self._req_manpower_tbl, (_MP_NUMEMP, _MP_QTY, _MP_UOM)),   # + days and "Days" in PDMS mode
                            (self._req_equip_tbl, (_EQ_QTY, _EQ_DAYS)), (self._req_cons_tbl, (_CS_QTY,)),
                            (self._transport_tbl, (_TR_QTY, _TR_DUR, _TR_MARKUP))):
            for _c in _cols:                           # number cells that take a formula
                _tbl.setItemDelegateForColumn(_c, _DaysQtyDelegate(_tbl, self))
        for _key, _tbl, _col in self._money_columns():
            _tbl.setItemDelegateForColumn(_col, _MoneyDelegate(_tbl, self, _key))
            _tbl.cellChanged.connect(lambda r, c, cc=_col: c == cc and self._update_group_titles_soon())
            _tbl.model().rowsInserted.connect(lambda *_a: self._update_group_titles_soon())
            _tbl.model().rowsRemoved.connect(lambda *_a: self._update_group_titles_soon())
        for _c in self._cat_combos.values():
            _c.currentTextChanged.connect(lambda _t: (self._recalc_all_totals(), self._update_group_titles()))
        self._pdms_rows = [row_prp, row_cu, row_ci, row_at, row_ld, row_sg, row_st]
        self._ctr_only_rows = [row_sc, row_cna, row_cnu, row_rv, row_cm]
        for row in (row_c, row_sc, row_l, row_s, row_d, row_cna, row_cnu, row_rv, row_pt, row_cm):
            gl.addLayout(row)
        for row in self._pdms_rows:
            gl.addLayout(row)
        gl.addWidget(self._fx_group)
        gl.addWidget(self._cat_widget)
        self._pdms_check.toggled.connect(self._set_pdms_mode)
        self._set_pdms_mode(False)

        gl.addSpacing(6)

        ref_row = QHBoxLayout()
        ref_lbl = QLabel("Job Ref:")
        ref_lbl.setFixedWidth(70)
        self._job_ref_edit = QLineEdit("217")
        self._job_ref_edit.setFixedWidth(120)
        self._job_ref_edit.setPlaceholderText("e.g. 217")
        ref_row.addWidget(ref_lbl)
        ref_row.addWidget(self._job_ref_edit)
        ref_row.addStretch()
        gl.addLayout(ref_row)

        markup_row = QHBoxLayout()
        markup_lbl = QLabel("Markup Rate:")
        markup_lbl.setFixedWidth(70)
        self._markup_spin = FormulaDoubleSpinBox()
        self._markup_spin.setFixedWidth(120)
        self._markup_spin.setRange(0.0, 100.0)
        self._markup_spin.setDecimals(2)
        self._markup_spin.setSuffix(" %")
        self._markup_spin.setValue(_DEFAULT_MARKUP_PCT)
        self._markup_spin.setToolTip(
            "USD consumables markup — written into the generated CTR's "
            '"Mark up (For Consumables)" cell. Saved/loaded with presets.'
        )
        self._markup_spin.valueChanged.connect(self._recalc_usd_totals)
        markup_row.addWidget(markup_lbl)
        markup_row.addWidget(self._markup_spin)
        markup_row.addStretch()
        gl.addLayout(markup_row)

        # ── Presets ────────────────────────────────────────────────────────────
        preset_lbl = QLabel(f"<b>Presets:</b>  (saves {_PRESET_SAVES})")
        preset_lbl.setWordWrap(True)
        preset_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        preset_lbl.setToolTip(_PRESET_OMITS)
        gl.addWidget(preset_lbl)

        preset_row = QHBoxLayout()
        self._preset_combo = QComboBox()
        self._preset_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._preset_combo.setToolTip("Select a saved preset to load")
        self._refresh_preset_combo()

        load_preset_btn = QPushButton("Load")
        load_preset_btn.setFixedWidth(60)
        load_preset_btn.setStyleSheet(_btn_style())
        load_preset_btn.setToolTip("Fill the fields above from the selected preset")
        load_preset_btn.clicked.connect(self._load_preset)

        save_preset_btn = QPushButton("Save…")
        save_preset_btn.setFixedWidth(60)
        save_preset_btn.setStyleSheet(_btn_style())
        save_preset_btn.setToolTip("Save current fields as a new or updated preset")
        save_preset_btn.clicked.connect(self._save_preset)

        del_preset_btn = QPushButton("Delete")
        del_preset_btn.setFixedWidth(60)
        del_preset_btn.setStyleSheet(_btn_style(MUTED, "#F5F5F5"))
        del_preset_btn.setToolTip("Delete the selected preset")
        del_preset_btn.clicked.connect(self._delete_preset)

        preset_row.addWidget(self._preset_combo)
        preset_row.addWidget(load_preset_btn)
        preset_row.addWidget(save_preset_btn)
        preset_row.addWidget(del_preset_btn)
        gl.addLayout(preset_row)

        out_row = QHBoxLayout()
        out_lbl = QLabel("Output folder:")
        out_lbl.setFixedWidth(100)
        self._out_dir_edit = QLineEdit()
        self._out_dir_edit.setReadOnly(True)
        self._out_dir_edit.setPlaceholderText("(select output folder)")
        out_browse = QPushButton("Browse…")
        out_browse.setFixedWidth(80)
        out_browse.clicked.connect(self._browse_output)
        out_row.addWidget(out_lbl)
        out_row.addWidget(self._out_dir_edit)
        out_row.addWidget(out_browse)
        gl.addLayout(out_row)

        gen_btn = QPushButton("Generate CTR Documents")
        gen_btn.setFixedHeight(44)
        gen_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {PRIMARY}; color: white;
                border: none; border-radius: 6px;
                font-size: 14px; font-weight: bold;
            }}
            QPushButton:hover   {{ background-color: {MR_COLOR}; }}
            QPushButton:pressed {{ background-color: #0D47A1; }}
        """)
        gen_btn.clicked.connect(self._generate)
        gl.addWidget(gen_btn)

        parent_layout.addWidget(grp)

    # ── Settings persistence ──────────────────────────────────────────────────

    @staticmethod
    def _settings() -> QSettings:
        return QSettings("SOCAR", "CTRGenerator")

    def sage_path(self) -> str:
        return self._sage_edit.text().strip()

    def set_sage_path(self, path: str) -> None:
        """Set the SAGE Export path from outside (the MR vs CTR tab's field).
        Does nothing when it already holds that path, which is what stops the
        two fields from echoing each other forever."""
        if self._sage_edit.text() != path:
            self._sage_edit.setText(path)

    def sage_names(self) -> dict[str, str]:
        """{STOCK CODE: SAGE description} of the loaded SAGE Export (empty before Load Files); lets
        the Settings window show each Stock Code's description."""
        return cost_stats.sage_name_map(self._sage_df)

    def usd_pricebook_path(self) -> str:
        return self._usd_pb_edit.text().strip()

    def set_usd_pricebook_path(self, path: str) -> None:
        if self._usd_pb_edit.text() != path:
            self._usd_pb_edit.setText(path)

    def combined_db_path(self) -> str:
        return self._combined_db_edit.text().strip()

    def set_combined_db_path(self, path: str) -> None:
        if self._combined_db_edit.text() != path:
            self._combined_db_edit.setText(path)

    def loaded_usd_df(self, path: str) -> pd.DataFrame | None:
        """The parsed USD Pricebook if it was loaded from exactly `path`, else None."""
        if (path and path == self._usd_pb_df_path and not self._usd_pb_df.empty
                and file_stamp(path) == self._usd_pb_df_stamp):
            return self._usd_pb_df
        return None

    def loaded_names_df(self, path: str) -> pd.DataFrame | None:
        """The parsed Equipment Names bridge if it was loaded from exactly `path`, else None."""
        if (path and path == self._names_db_df_path and not self._names_db_df.empty
                and file_stamp(path) == self._names_db_df_stamp):
            return self._names_db_df
        return None

    # ── Used by the Settings tab ─────────────────────────────────────────────

    def sage_path(self) -> str:
        return self._sage_edit.text().strip()

    def output_dir(self) -> str:
        return self._out_dir_edit.text().strip()

    def set_output_dir(self, path: str) -> None:
        self._out_dir_edit.setText(path)

    def stats_save_dir(self) -> str:
        return str(self._settings().value(self._STATS_DIR_KEY, "") or "")

    def set_stats_save_dir(self, path: str) -> None:
        s = self._settings()
        if path:
            s.setValue(self._STATS_DIR_KEY, path)
        else:
            s.remove(self._STATS_DIR_KEY)

    def saved_data_counts(self) -> dict[str, int]:
        """How many Match By aliases, Description renames and presets are
        currently saved."""
        return {
            "aliases": sum(len(v) for v in self._aliases.values()),
            "renames": sum(len(v) for v in self._desc_renames.values()),
            "presets": len(self._presets),
            "choices": len(self._stats_panel.choices()),
        }

    def reload_saved_data(self) -> None:
        """Re-read aliases, renames and presets from disk (after the Settings
        tab reset one of them). Rows already in the tables are left as they
        are; the reloaded values apply from the next request load."""
        self._aliases = load_aliases()
        self._desc_renames = load_desc_renames()
        self._presets = load_presets()
        self._refresh_preset_combo()
        self._stats_panel.set_choices(load_choices())
        self._refresh_stats()
        self.choices_reloaded.emit(self._stats_panel.choices())

    def open_alias_manager(self, category: str | None = None) -> None:
        self._open_alias_manager(category)

    def loaded_sage_df(self, path: str) -> pd.DataFrame | None:
        """The parsed SAGE table if it was loaded from exactly `path`, else
        None (not loaded yet, or loaded from a different file)."""
        if (path and path == self._sage_df_path and not self._sage_df.empty
                and file_stamp(path) == self._sage_df_stamp):
            return self._sage_df
        return None                              # not loaded, another file, or the file changed since it was read

    def restore_settings(self) -> None:
        """Load last-used paths from QSettings. Call after the widget is shown."""
        s = self._settings()
        _edits = {
            "azn_template":   self._azn_tpl_edit,
            "usd_template":   self._usd_tpl_edit,
            "pdms_template":  self._pdms_tpl_edit,
            "azn_pricebook":  self._azn_pb_edit,
            "sage_export":    self._sage_edit,
            "usd_pricebook":  self._usd_pb_edit,
            "combined_db":    self._combined_db_edit,
            "output_dir":     self._out_dir_edit,
        }
        for key, edit in _edits.items():
            val = s.value(f"paths/{key}", "")
            if val:
                edit.setText(val)
        for key, edit in self._pdms_remembered().items():
            val = s.value(f"pdms/{key}", "")
            if val:
                edit.setText(val)
        self._pdms_check.setChecked(str(s.value("pdms/enabled", "false")).lower() == "true")

    def save_settings(self) -> None:
        """Persist current paths to QSettings. Call from the parent closeEvent."""
        s = self._settings()
        s.setValue("paths/azn_template",  self._azn_tpl_edit.text())
        s.setValue("paths/usd_template",  self._usd_tpl_edit.text())
        s.setValue("paths/pdms_template", self._pdms_tpl_edit.text())
        for key, edit in self._pdms_remembered().items():
            s.setValue(f"pdms/{key}", edit.text())
        s.setValue("pdms/enabled", self._pdms_on())
        s.setValue("paths/azn_pricebook", self._azn_pb_edit.text())
        s.setValue("paths/sage_export",   self._sage_edit.text())
        s.setValue("paths/usd_pricebook", self._usd_pb_edit.text())
        s.setValue("paths/combined_db",   self._combined_db_edit.text())
        s.setValue("paths/output_dir",    self._out_dir_edit.text())

    # ── Presets ───────────────────────────────────────────────────────────────

    # ── PDMS proposal mode ────────────────────────────────────────────────────

    def _pdms_remembered(self) -> dict:
        """The PDMS header fields that are remembered between runs (not PRP No / Attention /
        Letter date: those belong to one proposal)."""
        return {
            "customer": self._customer_edit, "customer_city": self._customer_city_edit,
            "signer": self._signer_edit, "signer_title": self._signer_title_edit,
        }               # not the USD→AZN rate: it changes daily (see Reload CBAR)

    def _hotbed_complete(self) -> bool:
        """True when the Hotbed has both a rate and days (it is then calculated, whatever Include says)."""
        hb = self._hotbed_values()
        return bool(hb and hb["rate"] and hb["days"])

    def _hotbed_values(self) -> dict | None:
        """The Hotbed line as {"rate", "days"}, or None while the rate or the days is blank /
        not a number (it is then left out — nothing is assumed)."""
        rate = cbar_rates.parse_typed_rate(self._hotbed_rate_edit.text())
        days = cbar_rates.parse_typed_rate(self._hotbed_days_edit.text())
        rate = rate if rate is not None and rate > 0 else None
        days = days if days is not None and days > 0 else None
        # Complete: always in, calculated. Incomplete: in with just what is given while Include is
        # ticked, otherwise left out (None).
        if (rate is None or days is None) and not self._hotbed_incl_chk.isChecked():
            return None
        return {"rate": rate, "days": days, "currency": self._hotbed_cur_combo.currentText() or "AZN"}

    def _update_hotbed_total(self) -> None:
        hb = self._hotbed_values()
        cur = self._hotbed_cur_combo.currentText() or "AZN"
        if hb and hb["rate"] and hb["days"]:
            self._hotbed_total_lbl.setText(f"= {hb['rate'] * hb['days']:,.2f} {cbar_rates.currency_sign(cur)}")
        elif hb:
            self._hotbed_total_lbl.setText("(inserted as given, no total)")
        else:
            self._hotbed_total_lbl.setText("(left out of the proposal)")
        if hasattr(self, "_stats_panel"):              # the price totals and the manpower sums mention it
            self._refresh_price_totals()
            self._recalc_azn_totals()

    # ── CBAR rates (PDMS proposal) ────────────────────────────────────────────

    _FX_NOTE = ("PROVISIONAL: prices not in AZN are converted with these rates (then the consumables / "
                "transport mark-up is added). How the colleagues want this done is not confirmed yet.")

    def _rebuild_fx_rows(self) -> None:
        """One indented line per turned-on currency (Settings): its rate, its own Reload button and
        where the figure came from. Values already typed or fetched are kept."""
        old = {code: (d["edit"].text(), d["date"]) for code, d in self._fx.items()}
        self._fx_history = {code: (d.get("stamp", ""), d.get("action", "")) for code, d in self._fx.items()}
        while self._fx_lay.count():
            item = self._fx_lay.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                while item.layout().count():
                    w = item.layout().takeAt(0).widget()
                    if w is not None:
                        w.deleteLater()
        self._fx = {}
        head = QHBoxLayout()
        lbl = QLabel("CBAR rates  (AZN per 1 unit)")
        lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px; font-weight: bold;")
        lbl.setToolTip(self._FX_NOTE)
        all_btn = QPushButton("Reload all")
        all_btn.setToolTip("Fetch the latest official rates of every currency below.")
        all_btn.clicked.connect(lambda: self._reload_cbar(None))
        head.addWidget(lbl)
        head.addStretch(1)
        head.addWidget(all_btn)
        self._fx_lay.addLayout(head)
        self._fx_all_btn = all_btn
        for code in cbar_rates.enabled_currencies():
            row = QHBoxLayout()
            row.setContentsMargins(16, 0, 0, 0)               # the indent
            name = QLabel(f"{code} → AZN:")
            name.setFixedWidth(95 - 16 + 16)
            name.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
            edit = QLineEdit()
            edit.setToolTip(f"AZN per 1 {code} (CBAR). A formula such as =1.7*1.02 is allowed.")
            install_formula_edit(edit)
            edit.setStyleSheet("background: #FFF3E0;")           # orange = provisional
            btn = QPushButton("Reload")
            btn.setFixedWidth(70)
            note = QLabel("")
            note.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
            for w in (name, edit, btn, note):
                row.addWidget(w)
            row.addStretch(1)
            self._fx_lay.addLayout(row)
            d = {"edit": edit, "btn": btn, "note": note, "date": None}
            self._fx[code] = d
            text, day = old.get(code, (None, None))
            saved = None
            if text is None:                                   # first time: what was saved last (no network)
                saved = cbar_rates.saved_rate(code)
                if saved:
                    text, day = f"{saved[0]:.4f}", saved[1]
                elif code == "USD":
                    text = str(pdms_defaults()["default_usd_azn"])
            edit.setText(text or "")
            d["date"] = day
            if code in old:                                     # kept across a rebuild
                d["stamp"], d["action"] = self._fx_history.get(code, ("", ""))
            elif saved:
                self._fx_event(code, "Loaded from the last saved CBAR rate", stamp=cbar_rates.saved_checked_at(code))
            elif code == "USD":                                 # the only currency with a built-in starting value
                self._fx_event(code, "Default value, not fetched yet", stamp="")
            else:
                self._fx_event(code, "No rate yet", stamp="")
            self._fx_note(code)
            edit.textEdited.connect(lambda _t, c=code: self._on_fx_typed(c))   # any rate may be typed
            edit.textChanged.connect(lambda _t: self._recalc_all_totals())
            btn.clicked.connect(lambda _=False, c=code: self._reload_cbar([c]))
        self._fx_group.setVisible(self._pdms_on())
        self._refresh_currency_combos()
        if hasattr(self, "_req_manpower_tbl"):
            self._recalc_all_totals()

    def _currency_choices(self) -> list[str]:
        return ["AZN", *cbar_rates.enabled_currencies()]

    def _refresh_currency_combos(self) -> None:
        """AZN + the turned-on currencies in each category combo (and the Hotbed one); a choice that is
        no longer offered falls back to the category's standard currency."""
        std = pdms_defaults()["category_currencies"]
        choices = self._currency_choices()
        for key, combo in [*self._cat_combos.items(), ("hotbed", self._hotbed_cur_combo)]:
            prev = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(choices)
            want = prev if prev in choices else (std.get(key, "AZN") if std.get(key, "AZN") in choices else "AZN")
            combo.setCurrentText(want)
            combo.blockSignals(False)

    def _switch_markup(self, pdms: bool) -> None:
        """A PDMS proposal usually carries a 10 % mark-up, the CTRs 6.5 %: ticking PDMS puts the
        PDMS figure in the Markup Rate box, unticking puts the value it had back. Either can be
        changed by hand in between."""
        spin = getattr(self, "_markup_spin", None)
        if spin is None:
            return
        if pdms and getattr(self, "_markup_before_pdms", None) is None:
            self._markup_before_pdms = spin.value()
            spin.setValue(float(pdms_defaults()["markup_rate"]) * 100.0)
        elif not pdms and getattr(self, "_markup_before_pdms", None) is not None:
            spin.setValue(self._markup_before_pdms)
            self._markup_before_pdms = None
        spin.setToolTip(
            ("USD consumables markup — written into the generated CTR's \"Mark up (For Consumables)\" cell. "
             "Saved/loaded with presets." if not pdms else
             "Mark-up added to the consumables (after the conversion to AZN) in the PDMS proposal. "
             "Usually 10 %; change it when the job differs. Unticking PDMS puts the CTR value back."))

    def _row_shift_hours(self, row: int) -> float | None:
        """A manpower row's shift length in hours: the end of its Encoding (NS-NAT-OFF-12 -> 12), else
        the matched stock code's; None when neither gives one."""
        tbl = self._req_manpower_tbl

        def txt(c):
            item = tbl.item(row, c)
            return item.text().strip() if item else ""
        dec = _decode_manpower_encoding_full(txt(_MP_ENC))
        hours = dec[3] if dec else None
        if not hours:
            m = re.search(r"\(([^()]+)\)\s*$", txt(_MP_MDESC))
            dc = _decode_azn_stock_code(m.group(1)) if m else None
            hours = dc[3] if dc else None
        return float(hours) if hours and float(hours) > 0 else None

    _NUMBER_HINTS = {
        ("equipment", _EQ_QTY):   "Number of units of this equipment.",
        ("equipment", _EQ_RATE):  "Rate <b>per day</b> of the matched item (or the one typed over it); in PDMS mode in the cell's currency.",
        ("equipment", _EQ_DAYS):  "Days the equipment is on the job. Total = Quantity × Rate × Days.",
        ("consumables", _CS_QTY): "Number of units of this consumable.",
        ("consumables", _CS_PRICE): "<b>Unit</b> price of the matched item (or the one typed over it); in PDMS mode in the cell's currency.",
        ("transport", _TR_QTY):   "Number of units (vehicles, trips …).",
        ("transport", _TR_DUR):   "Duration, counted in the UOM column (days, trips …). Total = Quantity × Rate × Duration × (1 + Mark Up).",
        ("transport", _TR_MARKUP): "This line's mark-up in %, added on top of its cost.",
    }

    def _table_key(self, index) -> str | None:
        m = index.model()
        for key, tbl in (("manpower", self._req_manpower_tbl), ("equipment", self._req_equip_tbl),
                         ("consumables", self._req_cons_tbl), ("transport", self._transport_tbl)):
            if m is tbl.model():
                return key
        return None

    def _cell_hint(self, index, live, unit) -> str | None:
        """The tooltip (rich text) of a number cell: what the number means, with the formula behind it
        when there is one. None = no tooltip of ours (the cell's own, if any)."""
        lines = []
        mp = index.model() is self._req_manpower_tbl.model()
        v = self._days_view(index) if mp else None
        if mp:
            col = index.column()
            ph = v["ph"] if v else None
            if col == _MP_NUMEMP:
                lines.append("Number of employees on this line.")
            elif col == _MP_QTY and ph:
                lines.append(f"Quantity in <b>days</b> = {index.data(Qt.ItemDataRole.EditRole)} h ÷ {ph:g} h per shift "
                             f"(the shift length at the end of the Encoding) = {v['shown']}. The cell keeps the hours.")
            elif col == _MP_QTY:
                lines.append("Quantity in <b>hours</b> = working days × hours per shift (from the Encoding).")
            elif col == _MP_RATE and ph:
                lines.append(f"Rate <b>per day</b> = hourly rate {index.data(Qt.ItemDataRole.EditRole)} × {ph:g} h per shift "
                             f"= {v['shown']}. The cell keeps the hourly rate.")
            elif col == _MP_RATE:
                lines.append("<b>Hourly</b> rate of the matched item (or the one typed over it).")
            if v and v["problem"]:
                lines.append(f"<span style='color:#C62828'><b>!</b> {v['problem']}</span>")
        if not mp:
            hint = self._NUMBER_HINTS.get((self._table_key(index), index.column()))
            if hint:
                lines.append(hint)
        if live:
            f = live[0]
            note = "" if live[2] == unit else " (written in days; shown here as the value only)"
            lines.append(f"<b>Formula:</b> {f} → {live[1]}{note}")
        return "<br>".join(lines) if lines else None

    def _days_view(self, index) -> dict | None:
        """PDMS mode, manpower Quantity / UOM / Rate cell: what to show in days and a day rate
        (hours / shift length, hourly rate x shift length, 2 decimals; the cell itself stays in hours).
        {"shown", "edit" (full precision), "ph" (shift length or None), "problem" (reason or None)};
        None when the cell is shown as it is."""
        if (not self._pdms_on() or index.column() not in (_MP_QTY, _MP_UOM, _MP_RATE)
                or index.model() is not self._req_manpower_tbl.model()):
            return None
        row, col = index.row(), index.column()
        qty_item = self._req_manpower_tbl.item(row, _MP_QTY)
        qty = (qty_item.text() if qty_item else "").strip()
        try:
            float(qty)
        except ValueError:
            return None                                    # nothing to convert yet
        ph = self._row_shift_hours(row)
        if col == _MP_UOM:
            return {"shown": "Days" if ph else "Hours", "edit": "Days", "ph": ph, "problem": None}
        text = str(index.data(Qt.ItemDataRole.EditRole) or "").strip()
        try:
            n = float(text)
        except ValueError:
            return None
        if not ph:
            enc = self._req_manpower_tbl.item(row, _MP_ENC)
            enc = (enc.text().strip() if enc else "")
            why = ("The row has no Encoding, so its shift length (hours per day) is unknown" if not enc else
                   f"The Encoding {enc} has no shift length (hours per day) at its end")
            return {"shown": text, "edit": text, "ph": None,
                    "problem": f"{why}, and the matched stock code gives none, so the days and the day rate "
                               "cannot be worked out. The cell shows the hours / the hourly rate. Choose an "
                               "Encoding like NS-NAT-OFF-12 (the proposal cannot be generated for this row "
                               "without it)."}
        new = n / ph if col == _MP_QTY else n * ph
        return {"shown": f"{new:.2f}", "edit": f"{new:.10g}", "ph": ph, "problem": None}

    def _pdms_on(self) -> bool:
        """True while the PDMS proposal box is ticked (False before the header block is built)."""
        box = getattr(self, "_pdms_check", None)
        return bool(box is not None and box.isChecked())

    def _money_columns(self):
        """(category, table, rate / price column) of the four tables."""
        return (("manpower", self._req_manpower_tbl, _MP_RATE), ("equipment", self._req_equip_tbl, _EQ_RATE),
                ("consumables", self._req_cons_tbl, _CS_PRICE), ("transport", self._transport_tbl, _TR_RATE))

    def _cat_currency(self, key: str) -> str:
        """A category's default currency: the box in PDMS mode, else the one its CTR is in."""
        if self._pdms_on():
            return self._cat_combos[key].currentText() or pdms_defaults()["category_currencies"][key]
        return pdms_defaults()["category_currencies"][key]

    def _cell_currency(self, key: str, tbl, col: int, row: int) -> str:
        """The currency of a row's Rate / Price cell: its own choice, else its category's."""
        it = tbl.item(row, col)
        own = it.data(_CUR_ROLE) if it is not None else None
        return str(own or self._cat_currency(key)).upper()

    def _fx_factor(self, key: str, tbl, col: int, row: int) -> float:
        """What a row's amounts are multiplied by to show manats: 1 outside PDMS mode (the tables
        keep their CTR's own currency) and for AZN; the currency's CBAR rate otherwise (0 while it
        has none — the total then shows 0.00 with a tooltip)."""
        if not self._pdms_on():
            return 1.0
        code = self._cell_currency(key, tbl, col, row)
        if code == "AZN":
            return 1.0
        d = self._fx.get(code)
        rate = cbar_rates.parse_typed_rate(d["edit"].text()) if d else None
        return rate if rate and rate > 0 else 0.0

    def _total_item(self, total: float, key: str, tbl, col: int, row: int):
        """A read-only Total cell; in PDMS mode it is in manats, and says so when a currency has no rate."""
        item = _ro_item(f"{total:.2f}")
        recipe = {"manpower": "Num Employees × Quantity (hours) × Rate",
                  "equipment": "Quantity × Rate/Day × Days",
                  "consumables": "Quantity × Unit Price",
                  "transport": "Quantity × Rate × Duration × (1 + Mark Up)"}[key]
        if self._pdms_on():
            code = self._cell_currency(key, tbl, col, row)
            item.setToolTip(f"Total in manats = {recipe}"
                            + ("" if code == "AZN" else f", converted from {code} with its CBAR rate") + ".")
            if code != "AZN" and not self._fx_factor(key, tbl, col, row):
                item.setToolTip(f"No CBAR rate for {code} yet — enter or reload it under CBAR rates.")
        else:
            item.setToolTip(f"Total = {recipe}.")
        return item

    def _recalc_all_totals(self) -> None:
        """Every row's total again (a currency, a default or a rate changed), then the sums."""
        for r in range(self._req_manpower_tbl.rowCount()):
            self._recalc_manpower_row(r)
        for r in range(self._req_equip_tbl.rowCount()):
            self._recalc_equip_row(r)
        for r in range(self._req_cons_tbl.rowCount()):
            self._recalc_cons_row(r)
        for r in range(self._transport_tbl.rowCount()):
            self._recalc_transport_row(r)
        if hasattr(self, "_markup_spin"):          # the sums exist once the Generate section is built
            self._recalc_azn_totals()
            self._recalc_usd_totals()

    _GROUP_BASE = {"manpower": "Manpower", "equipment": "Plant & Equipment",
                   "consumables": "Consumables", "transport": "Additional Info & Transport"}

    def _update_group_titles_soon(self) -> None:
        QTimer.singleShot(0, self._update_group_titles)

    def _update_group_titles(self) -> None:
        """"Equipment — USD, GBP": the category's currency first, then any other currency a row in
        its table uses (outside PDMS mode just the CTR's own)."""
        for key, tbl, col in self._money_columns():
            main = self._cat_currency(key)
            others = set()
            if self._pdms_on():
                others = {self._cell_currency(key, tbl, col, r) for r in range(tbl.rowCount())} - {main}
            self._groups[key].setTitle(f"{self._GROUP_BASE[key]} — " + ", ".join([main, *sorted(others)]))

    def _currencies_in_use(self) -> set[str]:
        """Every currency a price may be in: the category defaults, the Hotbed's and any row's own
        (AZN excluded: it needs no rate)."""
        used = {self._cat_currency(k) for k in self._cat_combos}
        _hb = self._hotbed_values()
        if _hb and _hb["rate"]:
            used.add(self._hotbed_cur_combo.currentText())
        for key, tbl, col in self._money_columns():
            for r in range(tbl.rowCount()):
                used.add(self._cell_currency(key, tbl, col, r))
        used.discard("AZN")
        return used

    def _fx_event(self, code: str, action: str, stamp: str | None = None) -> None:
        """Remember what last happened to a rate and when (shown in the PDMS overview sheet)."""
        d = self._fx[code]
        d["action"] = action
        d["stamp"] = _datetime.now().strftime("%Y-%m-%d %H:%M") if stamp is None else (
            stamp.replace("T", " ")[:16] if stamp else "")

    def _fx_note(self, code: str, warning: str = "") -> None:
        d = self._fx[code]
        if warning:
            d["note"].setText("⚠ " + warning)
            d["note"].setToolTip(warning)
        elif d["date"]:
            d["note"].setText(f"CBAR {d['date']}")
            d["note"].setToolTip(self._FX_NOTE)
        else:
            d["note"].setText("typed by hand" if d["edit"].text().strip() else "no rate")
            d["note"].setToolTip(self._FX_NOTE)

    def _on_fx_typed(self, code: str) -> None:
        """A typed rate is no longer CBAR's (and is never restricted)."""
        self._fx[code]["date"] = None
        self._fx_event(code, "Typed by hand")
        self._fx_note(code)

    def _fx_rates(self) -> dict[str, tuple[float | None, str | None]]:
        """{code: (rate, CBAR day or None when typed)} for the turned-on currencies; the rate is
        None while the field is blank / not a number. AZN is not listed (always 1)."""
        return {c: (cbar_rates.parse_typed_rate(d["edit"].text()), d["date"]) for c, d in self._fx.items()}

    def _reload_cbar(self, codes=None) -> None:
        """Fetch the CBAR rates of the given currencies (None = every line) in the background and fill the fields.
        A rate that looks unusual is shown to the user, who decides whether to use it (it may be right);
        one that cannot be read keeps the old rate."""
        if self._cbar_worker is not None and self._cbar_worker.isRunning():
            return
        codes = list(self._fx) if codes is None else list(codes)
        if not codes:
            return
        for c in codes:
            self._fx[c]["btn"].setEnabled(False)
            self._fx[c]["btn"].setText("…")
        self._fx_all_btn.setEnabled(False)
        worker = _CbarWorker(codes, self)
        self._cbar_worker = worker

        def _restore_buttons():
            for c in codes:
                if c in self._fx:
                    self._fx[c]["btn"].setEnabled(True)
                    self._fx[c]["btn"].setText("Reload")
            self._fx_all_btn.setEnabled(True)

        def _done(results: dict):
            _restore_buttons()
            unusable = []
            for code, res in results.items():
                if code not in self._fx:
                    continue
                if res["error"]:                                  # nothing to approve: the entry cannot be read
                    unusable.append(f"{code}: {res['error']}")
                    self._fx_event(code, "Fetch could not be used — previous rate kept")
                    self._fx_note(code, res["error"])
                    continue
                if res["warnings"]:
                    # It may well be right (a real jump, a new peg): the user decides.
                    why = "\n• ".join(res["warnings"])
                    box = QMessageBox.question(
                        self, f"CBAR {code} rate looks unusual",
                        f"CBAR's {code} rate for {res['date']} is {res['rate']:.4f}, but:\n\n• {why}\n\n"
                        f"It may still be correct. Use it? (No keeps {self._fx[code]['edit'].text() or 'the current rate'}.)",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No)
                    if box != QMessageBox.StandardButton.Yes:
                        self._fx_event(code, f"Fetched {res['rate']:.4f} but not used (looked unusual) — previous rate kept")
                        self._fx_note(code, "; ".join(res["warnings"]) + " — not used")
                        continue
                    cbar_rates.accept_snapshot(code, res["date"])    # the next start begins from it too
                    self._fx_event(code, "Fetched from CBAR; looked unusual, approved by the user")
                    self._fx[code]["edit"].setText(f"{res['rate']:.4f}")
                    self._fx[code]["date"] = res["date"]
                    self._fx_note(code)
                    continue
                self._fx[code]["edit"].setText(f"{res['rate']:.4f}")
                self._fx[code]["date"] = res["date"]
                self._fx_event(code, "Fetched from CBAR")
                self._fx_note(code)
            if unusable:
                QMessageBox.warning(
                    self, "CBAR rate unusable",
                    "CBAR's entry could not be used — the rate that was in the field was kept:\n\n• "
                    + "\n• ".join(unusable) + "\n\nType the rate you want.")

        def _err(msg: str):
            _restore_buttons()
            QMessageBox.warning(self, "CBAR rate",
                                "Could not get the CBAR rate — the rate in the field was kept; "
                                f"type a rate if it is out of date.\n\n{msg}")

        worker.finished.connect(_done)
        worker.error.connect(_err)
        worker.start()

    @staticmethod
    def _show_row(row: QHBoxLayout, visible: bool) -> None:
        for i in range(row.count()):
            w = row.itemAt(i).widget()
            if w is not None:
                w.setVisible(visible)

    def _set_pdms_mode(self, on: bool) -> None:
        """Swap the CTR-only header rows for the PDMS ones (the values of the hidden ones
        are kept, so unticking brings them back)."""
        for row in self._ctr_only_rows:
            self._show_row(row, not on)
        for row in self._pdms_rows:
            self._show_row(row, on)
        # Contract No does not exist in a proposal; the AZN / USD templates are not used
        for edit in (self._azn_tpl_edit, self._usd_tpl_edit):
            edit.setEnabled(not on)
        self._pdms_tpl_edit.setEnabled(on)
        for w in self._hotbed_widgets:
            w.setVisible(on)
        self._fx_group.setVisible(on)
        self._cat_widget.setVisible(on)
        self._switch_markup(on)
        for tbl, key in ((self._req_manpower_tbl, "manpower"), (self._req_equip_tbl, "equipment"),
                         (self._req_cons_tbl, "consumables"), (self._transport_tbl, "transport")):
            for col, label in _PDMS_HEADERS[key].items():
                hi = tbl.horizontalHeaderItem(col)
                if hi is not None:
                    hi.setText(label if on else self._normal_headers[key][col])
            tbl.viewport().update()
        self._recalc_all_totals()
        self._update_group_titles()
        if hasattr(self, "_stats_panel"):
            self._refresh_price_totals()

    def _markup_rate(self) -> float:
        """Current markup rate as a fraction (e.g. 0.065), for build_usd()."""
        return self._markup_spin.value() / 100.0

    def _current_preset_data(self) -> dict:
        # Contract No (AZN/USD) is deliberately excluded — it's specific to
        # each generated document, so loading a preset must never overwrite
        # whatever the user has already typed there.
        return {
            "client":          self._client_edit.text(),
            "sub_client":      self._sub_client_edit.text(),
            "location":        self._location_edit.text(),
            "scope":           self._scope_edit.text(),
            "revision":        self._revision_edit.text(),
            "project_type":    self._project_type_edit.text(),
            "job_ref":         self._job_ref_edit.text(),
            "output_dir":      self._out_dir_edit.text(),
            "markup_rate_pct": self._markup_spin.value(),
        }

    def _apply_preset_data(self, data: dict) -> None:
        self._client_edit.setText(data.get("client", ""))
        self._sub_client_edit.setText(data.get("sub_client", ""))
        self._location_edit.setText(data.get("location", ""))
        self._scope_edit.setText(data.get("scope", ""))
        self._revision_edit.setText(data.get("revision", ""))
        self._project_type_edit.setText(data.get("project_type", "Offshore"))
        self._job_ref_edit.setText(data.get("job_ref", ""))
        if data.get("output_dir"):
            self._out_dir_edit.setText(data["output_dir"])
        try:
            self._markup_spin.setValue(float(data.get("markup_rate_pct", _DEFAULT_MARKUP_PCT)))
        except (TypeError, ValueError):
            self._markup_spin.setValue(_DEFAULT_MARKUP_PCT)

    def _refresh_preset_combo(self) -> None:
        self._preset_combo.clear()
        self._preset_combo.addItem("(no preset selected)")
        for name in sorted(self._presets):
            self._preset_combo.addItem(name)

    def _load_preset(self) -> None:
        name = self._preset_combo.currentText()
        if name not in self._presets:
            QMessageBox.information(self, "No preset", "Select a preset from the dropdown first.")
            return
        self._apply_preset_data(self._presets[name])

    def _save_preset(self) -> None:
        current = self._preset_combo.currentText()
        default_name = current if current in self._presets else ""
        name, ok = QInputDialog.getText(
            self, "Save Preset", "Preset name:", text=default_name
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        self._presets[name] = self._current_preset_data()
        save_presets(self._presets)
        self._refresh_preset_combo()
        idx = self._preset_combo.findText(name)
        if idx >= 0:
            self._preset_combo.setCurrentIndex(idx)

    def _delete_preset(self) -> None:
        name = self._preset_combo.currentText()
        if name not in self._presets:
            return
        reply = QMessageBox.question(
            self, "Delete preset",
            f'Delete preset "{name}"?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            del self._presets[name]
            save_presets(self._presets)
            self._refresh_preset_combo()

    # ── File loading ──────────────────────────────────────────────────────────

    def _load_files(self):
        """
        Submits AZN Pricebook + SAGE Export + USD Pricebook (each its own
        file) to the thread pool, plus the Equipment Names DB bridge from
        the Combined DB file if one is selected, and returns immediately —
        the UI stays responsive while Excel files are being parsed. Results
        arrive via _on_file_loaded (queued connection).
        """
        azn_pb   = self._azn_pb_edit.text().strip()
        sage     = self._sage_edit.text().strip()
        usd_pb   = self._usd_pb_edit.text().strip()
        combined = self._combined_db_edit.text().strip()

        errors = []
        if not azn_pb: errors.append("AZN Pricebook not selected.")
        if not sage:   errors.append("SAGE Export not selected.")
        if not usd_pb: errors.append("USD Pricebook not selected.")
        if errors:
            QMessageBox.warning(self, "Missing files", "\n".join(errors))
            return

        tasks = [
            ("azn_pb", lambda p=azn_pb: parse_azn_pricebook(p)),
            ("sage",   lambda p=sage:   _prime_sage_index(parse_sage(p))),
            ("usd_pb", lambda p=usd_pb: parse_usd_pricebook(p)),
        ]
        # Optional — only needed to resolve a CTR Request equipment stock
        # code to a USD Pricebook description; without it, equipment falls
        # back to matching "Match By" directly against the USD Pricebook.
        if combined:
            tasks.append(("names_db", lambda p=combined: parse_equip_names_db(p)))
            # The manpower encoding rules (valid encodings per role, roles that
            # are always Onshore) live in the same workbook.
            tasks.append(("enc_info", lambda p=combined: parse_encoding_info(p)))

        self._sage_loading_path = sage
        self._usd_loading_path = usd_pb
        self._names_loading_path = combined
        # Taken BEFORE the files are read: a file edited while it is being read then counts as changed next time.
        self._loading_stamps = {"sage": file_stamp(sage), "usd": file_stamp(usd_pb),
                                "names": file_stamp(combined) if combined else None}
        job = TASKS.begin_job("Load pricebook files", [name for name, _fn in tasks],
                               parallel=True)
        self._pending_loads += len(tasks)
        self._load_errors.clear()
        self._load_status.clear()
        self._load_files_btn.setEnabled(False)
        self._pricebook_status.setText(f"Loading {len(tasks)} file(s)…")

        log.info(
            "Async loading pricebook files: azn=%s sage=%s usd_pb=%s combined_db=%s",
            azn_pb, sage, usd_pb, combined,
        )
        for i, (name, fn) in enumerate(tasks):
            _LOAD_POOL.start(_LoadTask(name, fn, self._load_signals, job, i))
        self._load_job = job

    def _on_file_loaded(self, name: str, result) -> None:
        """Slot (always on main thread via queued connection) for pool-task results."""
        if isinstance(result, _TaskStopped):
            self._load_status.append(f"{name}: not loaded (stopped on request)")
        elif isinstance(result, Exception):
            is_warning = name in ("names_db", "enc_info")
            self._load_errors.append((name, str(result), is_warning))
            log.error("Failed to load %s: %s", name, result)
        else:
            if name == "azn_pb":
                self._azn_df = result
                self._load_status.append(f"AZN Pricebook ({len(result)} rows)")
            elif name == "sage":
                self._sage_df = result
                self._sage_df_path = self._sage_loading_path
                self._sage_df_stamp = self._loading_stamps.get("sage")
                self._load_status.append(f"SAGE Export ({len(result)} rows)")
            elif name == "usd_pb":
                self._usd_pb_df = result
                self._usd_pb_df_path = self._usd_loading_path
                self._usd_pb_df_stamp = self._loading_stamps.get("usd")
                self._load_status.append(f"USD Pricebook ({len(result)} rows)")
            elif name == "names_db":
                self._names_db_df = result
                self._names_db_df_path = self._names_loading_path
                self._names_db_df_stamp = self._loading_stamps.get("names")
                self._load_status.append(f"Equipment Names DB ({len(result)} rows)")
            elif name == "enc_info":
                self._enc_variants = result["variants"]
                self._onshore_roles = set(result["onshore"])
                self._load_status.append(
                    f"Encoding rules ({len(result['variants'])} roles, "
                    f"{len(result['onshore'])} always-Onshore)")

        self._pending_loads -= 1
        if self._pending_loads <= 0:
            self._pending_loads = 0
            self._load_files_btn.setEnabled(True)
            if self._load_job is not None:
                self._load_job.finish()
            self._finish_file_load()

    def _finish_file_load(self) -> None:
        """Called when all pool tasks in the current batch have completed."""
        _label = {
            "azn_pb":   "AZN Pricebook",
            "sage":     "SAGE Export",
            "usd_pb":   "USD Pricebook",
            "names_db": "Equipment Names DB",
            "enc_info": "Encoding rules",
        }
        _fallback = {
            "enc_info": ("The Encoding list in the Manpower table falls back to the AZN "
                         "Pricebook's variants for the role, and no role is treated as "
                         "always Onshore."),
            "names_db": ("Equipment matching will fall back to matching "
                         "\"Match By\" directly against the USD Pricebook "
                         "by description, without the request stock code's "
                         "canonical-name resolution."),
        }
        for name, msg, is_warning in self._load_errors:
            label = _label.get(name, name)
            fb    = _fallback.get(name, "")
            if is_warning:
                QMessageBox.warning(
                    self, f"{label} error",
                    f"Could not load {label}:\n{msg}" + (f"\n\n{fb}" if fb else ""),
                )
            else:
                QMessageBox.critical(self, f"{label} error", msg)

        self._rematch_all_requests()
        if self._load_status:
            self._pricebook_status.setText(
                "Loaded for matching (no rows added to the CTR tables): "
                + "; ".join(self._load_status)
            )
        self.files_loaded.emit()                 # e.g. the MR vs CTR statistics recalculate from the fresh tables

    def _prune_workers(self) -> None:
        """Drops workers that have finished (see MainWindow._prune_workers)."""
        for w in [w for w in self._workers if w.isFinished()]:
            self._workers.remove(w)
            w.deleteLater()

    def _load_ctr_request(self):
        """Starts parsing the CTR Request sheet of the Combined DB file on a background thread."""
        path = self._combined_db_edit.text().strip()
        if not path:
            QMessageBox.warning(
                self, "Missing file",
                "Select the Combined DB file first (Source Files section above).",
            )
            return

        log.info("Loading CTR Request: %s", path)
        self._load_ctr_btn.setEnabled(False)
        self._ctr_req_status.setText("Loading CTR Request…")

        worker = _CTRRequestWorker(path, parent=self)
        self._prune_workers()
        self._workers.append(worker)
        worker.finished.connect(self._on_ctr_loaded)
        worker.error.connect(self._on_ctr_error)
        worker.start()

    def _on_ctr_loaded(self, data: dict) -> None:
        """Slot called on the main thread when CTR Request parsing finishes."""
        self._load_ctr_btn.setEnabled(True)

        if data.get("client"):
            self._client_edit.setText(data["client"])
        if data.get("location"):
            self._location_edit.setText(data["location"])
        if data.get("job_description"):
            self._scope_edit.setText(data["job_description"])
        if data.get("project_type"):
            self._project_type_edit.setText(data["project_type"])
        # Written even when the request's Comments box is empty, so loading
        # a second request clears the previous one's comments rather than
        # carrying them into an unrelated CTR.
        self._comments_edit.setText(data.get("comments", ""))
        # PDMS proposal: the requester is the addressee. Written even when blank, like the comments.
        self._attention_edit.setText(attention_from_requester(data.get("requester", "")))
        commencement = data.get("commencement_date")
        if commencement:
            try:
                self._date_edit.setText(commencement.strftime("%Y-%m-%d"))
            except AttributeError:
                self._date_edit.setText(str(commencement))

        self._req_manpower_tbl.setRowCount(0)
        for mp in data.get("manpower_rows", []):
            self._append_manpower_row_from_request(mp)

        self._req_equip_tbl.setRowCount(0)
        for eq in data.get("equipment_rows", []):
            self._append_equip_row_from_request(eq)

        self._req_cons_tbl.setRowCount(0)
        for cs in data.get("consumable_rows", []):
            self._append_cons_row_from_request(cs)

        # A requested scaffold tonnage is one more Plant & Equipment line.
        # It carries no rate, so the pricebook lookup can't match it and it
        # would be dropped from the output as an unmatched row — but the
        # request did ask for scaffold, and the line has to appear whether
        # or not anyone gets round to pricing it. Marked includable up
        # front so it's written with its name, tonnage and TON; setting a
        # rate later is what turns it into a non-zero cost.
        scaffold = data.get("scaffold")
        if scaffold:
            self._append_equip_row_from_request(_scaffold_request_item(scaffold))
            row = self._req_equip_tbl.rowCount() - 1
            # Scaffold is equipment but has no SAGE cost; the statistics box skips it.
            self._req_equip_tbl.item(row, _EQ_DESC).setData(_SCAFFOLD_ROLE, True)
            self._req_equip_tbl.blockSignals(True)
            # Days is left empty rather than taking the usual 30-day default:
            # the request block states no duration for scaffold, and a number
            # nobody chose is worse than a blank cell asking to be filled in.
            self._req_equip_tbl.setItem(row, _EQ_DAYS, QTableWidgetItem(""))
            status = self._req_equip_tbl.item(row, _EQ_STATUS)
            if status and not status.text().startswith("✓"):
                self._req_equip_tbl.setItem(
                    row, _EQ_STATUS, _ro_item("✓ Manual — set Rate/Day and Days to price it"))
            self._req_equip_tbl.blockSignals(False)
            _highlight_row(self._req_equip_tbl, row, True)
            self._recalc_equip_row(row)

        self._transport_tbl.setRowCount(0)
        for tr in data.get("transport_rows", []):
            for line in _transport_lines(
                tr.get("transport_type", ""), tr.get("quantity", ""), tr.get("duration", "")
            ):
                self._append_transport_row(line)

        self._required_info = [
            item["label"] for item in data.get("additional_info", []) if item.get("required")
        ]
        self._required_info_lbl.setText(
            "Additional Information required:  "
            + (", ".join(self._required_info) if self._required_info else "(none)")
        )

        self._recalc_azn_totals()
        self._recalc_usd_totals()

        n_mp = len(data.get("manpower_rows", []))
        n_eq = len(data.get("equipment_rows", []))
        n_cs = len(data.get("consumable_rows", []))
        n_tr = self._transport_tbl.rowCount()
        log.info(
            "CTR Request loaded: manpower=%d equipment=%d consumable=%d "
            "transport=%d scaffold=%s required_info=%s",
            n_mp, n_eq, n_cs, n_tr, bool(scaffold), self._required_info,
        )
        extras = []
        if scaffold:
            extras.append(
                f"scaffold ({scaffold.get('tonnage')} {scaffold.get('uom')}) added to "
                f"the Equipment table — set its Match By and Days to price it"
            )
        if n_tr:
            extras.append(f"{n_tr} transport line(s) — check their rates")
        if self._required_info:
            extras.append(
                f"\"{', '.join(self._required_info)}\" marked Required, written "
                f"to the AZN CTR's Comments box"
            )
        self._ctr_req_status.setText(
            f"Loaded {n_mp} manpower, {n_eq} equipment, {n_cs} consumable "
            f"request(s) into the tables below. Exact matches on stock code "
            f"or description fill in the rate automatically and mark the "
            f"row \"✓ Matched\". Edit \"Match By\" to fix a row marked "
            f"\"✗ No match\", or load the Pricebook-Based files and click "
            f"\"Re-match All\". CTR Header Info fields in the Generate "
            f"section are pre-filled."
            + (("  Also read: " + "; ".join(extras) + ".") if extras else "")
        )

    def _on_ctr_error(self, msg: str) -> None:
        """Slot called on the main thread when CTR Request parsing fails."""
        self._load_ctr_btn.setEnabled(True)
        self._ctr_req_status.setText("Failed to load CTR Request.")
        log.error("Failed to parse CTR Request: %s", msg)
        QMessageBox.critical(self, "CTR Request error", msg)

    # ── Matching against pricebook/SAGE ─────────────────────────────────────────

    _REMATCH_WHAT = {
        "manpower":    ("Re-match All Manpower", "the AZN pricebook", lambda s: s._rematch_all_manpower),
        "equipment":   ("Re-match All Equipment", "the USD pricebook / Equipment Names DB", lambda s: s._rematch_all_equip),
        "consumables": ("Re-match All Consumables", "SAGE", lambda s: s._rematch_all_cons),
    }

    def _rematch_note(self) -> QLabel:
        """The short reminder shown under each table's Re-match button."""
        lbl = QLabel("Re-matching re-reads each row's rate / price from its source: a rate you typed over is replaced, "
                     "and in PDMS mode the cell's currency goes back to the category's. Editing Match By, Type, Shift, "
                     "Shift Type or Encoding re-matches that row the same way.")
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        return lbl

    def _ask_rematch(self, key: str) -> None:
        """The Re-match All buttons: say what will happen to the rows, and only go on after a Yes."""
        title, source, runner = self._REMATCH_WHAT[key]
        lines = [
            f"Every row is looked up again in {source}.",
            "• A rate / price you typed over is replaced by the one found there (or NONRECHARG when it is non-rechargeable).",
            "• A row that finds no match keeps what it has, as ✓ Manual if it was already included.",
            "• The matched item, the status and the total are updated to the new match.",
        ]
        if key == "manpower":
            lines.append("• The quantity is worked out again from the working days × the shift length, unless you typed it.")
        if self._pdms_on():
            lines.append("• PDMS mode: each row's currency goes back to the category's default.")
        answer = QMessageBox.question(
            self, title, "\n".join(lines) + "\n\nGo on?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            runner(self)()

    def _rematch_all_manpower(self):
        for r in range(self._req_manpower_tbl.rowCount()):
            self._rematch_manpower_row(r)
        self._recalc_azn_totals()

    def _rematch_all_equip(self):
        for r in range(self._req_equip_tbl.rowCount()):
            self._rematch_equip_row(r)
        self._recalc_usd_totals()

    def _rematch_all_cons(self):
        for r in range(self._req_cons_tbl.rowCount()):
            self._rematch_cons_row(r)
        self._recalc_usd_totals()

    def _rematch_all_requests(self):
        self._rematch_all_manpower()
        self._rematch_all_equip()
        self._rematch_all_cons()

    def _open_alias_manager(self, category: str | None = None):
        # Sweep every saved rename (both Match By and Description) across
        # the rows already on screen first — a rename learned from fixing
        # one row otherwise only applies to *future* rows (the next CTR
        # Request load), leaving sibling rows with the same description
        # still sitting unmatched/unrenamed. Scoped to just this category's
        # table when the dialog itself is category-specific.
        if category == "manpower":
            applied = (self._apply_aliases_to_manpower_rows()
                       + self._apply_desc_renames_to_manpower_rows())
            if applied:
                self._recalc_azn_totals()
        elif category == "equipment":
            applied = (self._apply_aliases_to_equip_rows()
                       + self._apply_desc_renames_to_equip_rows())
            if applied:
                self._recalc_usd_totals()
        elif category == "consumable":
            applied = (self._apply_aliases_to_cons_rows()
                       + self._apply_desc_renames_to_cons_rows())
            if applied:
                self._recalc_usd_totals()
        else:
            applied = self._apply_aliases_to_all_rows() + self._apply_desc_renames_to_all_rows()
            if applied:
                self._recalc_azn_totals()
                self._recalc_usd_totals()

        # Note: deleting a rename here only stops it being applied as the
        # default for *future* request rows — it deliberately does not
        # touch text already sitting in the tables below, since re-matching
        # would just re-learn the same rename from that text.
        dlg = AliasManagerDialog(self._aliases, self._desc_renames, parent=self,
                                  applied_count=applied, category=category)
        dlg.exec()
        dlg.deleteLater()

    def _apply_aliases_to_manpower_rows(self) -> int:
        """
        Overwrites "Match By" with the saved rename for every Manpower row
        whose Description has a known alias — even if Match By currently
        holds something else — since a saved rename represents the best
        known correct search term. Each change fires cellChanged, which
        re-runs matching for that row. Returns how many rows were updated.
        """
        applied = 0
        tbl = self._req_manpower_tbl
        for r in range(tbl.rowCount()):
            desc_item  = tbl.item(r, _MP_DESC)
            match_item = tbl.item(r, _MP_MATCH)
            if desc_item is None or match_item is None:
                continue
            alias = get_alias(self._aliases, "manpower", desc_item.text().strip())
            if alias and match_item.text().strip() != alias:
                match_item.setText(alias)
                applied += 1
        return applied

    def _apply_aliases_to_equip_rows(self) -> int:
        """Same idea as _apply_aliases_to_manpower_rows, for Equipment —
        keyed by (Description, Stock Code), see _stock_rename_key."""
        applied = 0
        tbl = self._req_equip_tbl
        for r in range(tbl.rowCount()):
            desc_item  = tbl.item(r, _EQ_DESC)
            code_item  = tbl.item(r, _EQ_CODE)
            match_item = tbl.item(r, _EQ_MATCH)
            if desc_item is None or match_item is None:
                continue
            code  = code_item.text().strip() if code_item else ""
            alias = _get_alias_by_stock(self._aliases, "equipment", desc_item.text().strip(), code)
            if alias and match_item.text().strip() != alias:
                match_item.setText(alias)
                applied += 1
        return applied

    def _apply_aliases_to_cons_rows(self) -> int:
        """Same idea as _apply_aliases_to_manpower_rows, for Consumables —
        keyed by (Description, Stock Code), see _stock_rename_key."""
        applied = 0
        tbl = self._req_cons_tbl
        for r in range(tbl.rowCount()):
            desc_item  = tbl.item(r, _CS_DESC)
            code_item  = tbl.item(r, _CS_CODE)
            match_item = tbl.item(r, _CS_MATCH)
            if desc_item is None or match_item is None:
                continue
            code  = code_item.text().strip() if code_item else ""
            alias = _get_alias_by_stock(self._aliases, "consumable", desc_item.text().strip(), code)
            if alias and match_item.text().strip() != alias:
                match_item.setText(alias)
                applied += 1
        return applied

    def _apply_aliases_to_all_rows(self) -> int:
        return (self._apply_aliases_to_manpower_rows()
                + self._apply_aliases_to_equip_rows()
                + self._apply_aliases_to_cons_rows())

    def _apply_desc_renames_to_manpower_rows(self) -> int:
        """
        Overwrites Description with the saved rename for every Manpower row
        whose original requested description has a known Description
        rename — looked up by the row's stashed original text (Qt.UserRole
        + 1), not its current (possibly already-renamed) text, since that's
        what a future CTR Request load will actually match against.
        """
        applied = 0
        tbl = self._req_manpower_tbl
        for r in range(tbl.rowCount()):
            desc_item = tbl.item(r, _MP_DESC)
            if desc_item is None:
                continue
            anchor = desc_item.data(_DESC_ANCHOR_ROLE) or desc_item.text().strip()
            renamed = get_desc_rename(self._desc_renames, "manpower", anchor)
            if renamed and desc_item.text().strip() != renamed:
                desc_item.setText(renamed)
                applied += 1
        return applied

    def _apply_desc_renames_to_equip_rows(self) -> int:
        """Same idea as _apply_desc_renames_to_manpower_rows, for Equipment —
        keyed by (original Description, Stock Code), see _stock_rename_key."""
        applied = 0
        tbl = self._req_equip_tbl
        for r in range(tbl.rowCount()):
            desc_item = tbl.item(r, _EQ_DESC)
            code_item = tbl.item(r, _EQ_CODE)
            if desc_item is None:
                continue
            code    = code_item.text().strip() if code_item else ""
            anchor  = desc_item.data(_DESC_ANCHOR_ROLE) or desc_item.text().strip()
            renamed = _get_desc_rename_by_stock(self._desc_renames, "equipment", anchor, code)
            if renamed and desc_item.text().strip() != renamed:
                desc_item.setText(renamed)
                applied += 1
        return applied

    def _apply_desc_renames_to_cons_rows(self) -> int:
        """Same idea as _apply_desc_renames_to_manpower_rows, for Consumables —
        keyed by (original Description, Stock Code), see _stock_rename_key."""
        applied = 0
        tbl = self._req_cons_tbl
        for r in range(tbl.rowCount()):
            desc_item = tbl.item(r, _CS_DESC)
            code_item = tbl.item(r, _CS_CODE)
            if desc_item is None:
                continue
            code    = code_item.text().strip() if code_item else ""
            anchor  = desc_item.data(_DESC_ANCHOR_ROLE) or desc_item.text().strip()
            renamed = _get_desc_rename_by_stock(self._desc_renames, "consumable", anchor, code)
            if renamed and desc_item.text().strip() != renamed:
                desc_item.setText(renamed)
                applied += 1
        return applied

    def _apply_desc_renames_to_all_rows(self) -> int:
        return (self._apply_desc_renames_to_manpower_rows()
                + self._apply_desc_renames_to_equip_rows()
                + self._apply_desc_renames_to_cons_rows())

    # -- manpower --

    # -- manpower encoding --

    def _job_location(self) -> str:
        """"Onshore" / "Offshore" from the request's Project Type, or "" when it says neither."""
        pt = self._project_type_edit.text().strip().lower()
        return "Onshore" if "onshore" in pt else "Offshore" if "offshore" in pt else ""

    def _is_onshore_role(self, *names) -> bool:
        """True for a role the Combined DB lists as always Onshore (ENC_LIST)."""
        return any((n or "").strip().upper() in self._onshore_roles for n in names)

    def _row_role_names(self, row: int) -> list[str]:
        """The names a manpower row's role can be looked up under (Match By
        without the "support" keyword, then the Description), upper-case."""
        tbl = self._req_manpower_tbl
        out = []
        for col in (_MP_MATCH, _MP_DESC):
            it = tbl.item(row, col)
            txt = it.text().strip() if it else ""
            if txt:
                out.append(strip_support_keyword(txt).strip().upper())
                if col == _MP_DESC:
                    out.append(txt.upper())
        return list(dict.fromkeys(out))

    def _default_location(self, desc: str) -> str:
        """Location for a row whose request gives no encoding: Onshore for the
        always-Onshore roles (or "(onshore)" in the text), otherwise the job's
        Project Type — "" when that says neither."""
        if self._is_onshore_role(strip_support_keyword(desc), desc) or "(onshore)" in desc.lower():
            return "Onshore"
        return self._job_location()

    def _encoding_options(self, row: int) -> list[str]:
        """Encodings valid for this row's role at its location. Onshore and
        Offshore never mix: an always-Onshore role is offered only Onshore
        codes, any other row only the codes of its Type. Source: the Combined
        DB's Possible Variants sheet, else the AZN Pricebook rows of the role.
        Only NAT codes for now (allowance codes like FLO-EXP-OFF are not
        handled yet)."""
        names = self._row_role_names(row)
        codes = next((self._enc_variants[n] for n in names if n in self._enc_variants), None)
        if codes is None:
            codes = []
            df = self._azn_df
            if not df.empty:
                for n in names:
                    m = df[df["supplier_desc"].astype(str).str.strip().str.upper() == n]
                    if not m.empty:
                        codes = [str(c).strip().upper() for c in m["stock_code"]]
                        break
        codes = [c for c in dict.fromkeys(codes) if _decode_manpower_encoding_full(c)]
        type_item = self._req_manpower_tbl.item(row, _MP_TYPE)
        want = "Onshore" if self._is_onshore_role(*names) else (type_item.text().strip() if type_item else "")
        if want:
            codes = [c for c in codes if _decode_manpower_encoding_full(c)[0] == want]
        return codes

    def _reject_encoding(self, row: int, text: str, options: list[str]) -> None:
        QToolTip.showText(
            QCursor.pos(),
            f"“{text}” is not a valid encoding for this role and location"
            + (f" — choose one of the {len(options)} listed." if options else
               " — none exists for this Type (choose the other Type, or add the role to the "
               "always-Onshore list in the Combined DB)."),
            self._req_manpower_tbl)

    def _reset_manpower_match(self, row: int) -> None:
        """Forget the matched item and rate of a row whose Encoding changed:
        the rate must come from the new encoding, never linger from the old one
        (a row that stayed "✓ Manual" would keep billing the old rate)."""
        tbl = self._req_manpower_tbl
        was = tbl.blockSignals(True)
        tbl.setItem(row, _MP_MDESC, _ro_item(""))
        tbl.setItem(row, _MP_RATE, QTableWidgetItem(""))
        tbl.setItem(row, _MP_STATUS, _ro_item("✗ No match — edit Match By or Description"))
        tbl.blockSignals(was)

    def _apply_manpower_encoding(self, row: int) -> None:
        """The Encoding cell decides Type, Shift, Shift Type and the shift
        length: copy them into the row (and its hours hint) from it."""
        tbl = self._req_manpower_tbl
        enc_item, desc_item = tbl.item(row, _MP_ENC), tbl.item(row, _MP_DESC)
        if enc_item is None or desc_item is None:
            return
        self._reset_manpower_match(row)
        decoded = _decode_manpower_encoding_full(enc_item.text())
        was = tbl.blockSignals(True)
        try:
            if decoded:
                loc, sh, st, hours, amb = decoded
                for col, val in ((_MP_TYPE, loc), (_MP_SHIFT, sh), (_MP_SHIFTTYPE, st)):
                    if val:
                        tbl.setItem(row, col, QTableWidgetItem(val))
                desc_item.setData(Qt.UserRole, (hours or "", amb))
            else:
                desc_item.setData(Qt.UserRole, ("", False))
        finally:
            tbl.blockSignals(was)

    def _drop_stale_encoding(self, row: int) -> None:
        """Type / Shift / Shift Type was edited by hand: an Encoding that now
        disagrees with them is cleared (the user is overriding it), so the row
        never carries two different answers."""
        tbl = self._req_manpower_tbl
        enc_item, desc_item = tbl.item(row, _MP_ENC), tbl.item(row, _MP_DESC)
        if enc_item is None or desc_item is None or not enc_item.text().strip():
            return
        decoded = _decode_manpower_encoding_full(enc_item.text())
        cells = [(tbl.item(row, c).text().strip() if tbl.item(row, c) else "")
                 for c in (_MP_TYPE, _MP_SHIFT, _MP_SHIFTTYPE)]
        if decoded and all(not d or d == c for d, c in zip(decoded[:3], cells)):
            return
        was = tbl.blockSignals(True)
        enc_item.setText("")
        desc_item.setData(Qt.UserRole, ("", False))
        tbl.blockSignals(was)
        self._reset_manpower_match(row)

    def _mark_waiting(self, tbl: QTableWidget, row: int, status_col: int) -> None:
        """The reference files are not loaded yet, so the row cannot be matched
        — that is not a problem with the row, so it is not shown in red."""
        was = tbl.blockSignals(True)
        tbl.setItem(row, status_col, _ro_item("⏳ Waiting for Load Files"))
        _set_row_tint(tbl, row, None)
        tbl.blockSignals(was)

    def _append_manpower_row_from_request(self, request_item: dict):
        tbl = self._req_manpower_tbl
        r = tbl.rowCount()
        tbl.insertRow(r)

        desc = request_item.get("description", "")
        # Display text may differ from the raw requested description if the
        # user previously taught a Description rename for it — but every
        # other lookup below (Match By alias, Onshore/Offshore detection,
        # support-row classification) stays keyed on the raw text, since
        # that's what will actually recur across future CTR Requests.
        desc_display = get_desc_rename(self._desc_renames, "manpower", desc) or desc
        default_key = get_alias(self._aliases, "manpower", desc) or desc
        encoding = request_item.get("encoding") or ""
        decoded = _decode_manpower_encoding_full(encoding)
        (encoded_location, encoded_shift, encoded_shift_type,
         encoded_hours, encoding_ambiguous) = decoded or (None, None, None, None, False)
        row_type = encoded_location or self._default_location(desc)
        shift_type_raw = (request_item.get("status") or "").strip()
        # A recognized shift-type prefix in ENCODING (e.g. "MSU-NAT-OFF-12")
        # is authoritative over the free-text Shift/Status columns, since
        # requesters sometimes leave those stale/inconsistent with the code
        # they actually intend (see _decode_manpower_encoding_full).
        # Not stated -> blank (the row stays unmatched, in red, until the user
        # picks one from the Shift / Shift Type drop-down) — never assumed.
        _shift_req = (request_item.get("shift") or "").lower()
        shift      = encoded_shift or ("Night" if "night" in _shift_req else "Day" if "day" in _shift_req else "")
        shift_type = encoded_shift_type or (shift_type_raw.title() if shift_type_raw else "")
        # A figure the request does not state stays blank (red) — it is
        # never assumed to be 1 / 0 (see _incomplete_cells).
        num_emp_text = _request_qty_text(request_item.get("quantity"))
        wd_text = _request_qty_text(request_item.get("working_days"))
        working_days = float(wd_text) if wd_text else None
        # Quantity is billed in hours (working days × hours/shift), not days
        # — the AZN pricebook's labor rows are all rated per hour (UOM
        # "HUR"), and the shift length lives in the matched stock code's
        # suffix (see _decode_azn_stock_code). The actual hours/shift is
        # only known once matching runs, so start from the plain working-day
        # count here and let _rematch_manpower_row multiply it in once the
        # shift length is resolved. working_days itself is stashed on the
        # Quantity cell so a later rematch (e.g. after Shift/Type/Match By
        # changes the matched stock code's hours) can recompute from the
        # original day count instead of compounding onto an already-
        # multiplied value.
        qty_text = wd_text

        tbl.setItem(r, _MP_TYPE,      QTableWidgetItem(row_type))
        tbl.setItem(r, _MP_SHIFT,     QTableWidgetItem(shift))
        tbl.setItem(r, _MP_SHIFTTYPE, QTableWidgetItem(shift_type))
        _was = tbl.blockSignals(True)
        tbl.setItem(r, _MP_ENC,       QTableWidgetItem(encoding.strip().upper()))
        tbl.blockSignals(_was)
        tbl.setItem(r, _MP_DESC,      QTableWidgetItem(desc_display))
        tbl.item(r, _MP_DESC).setData(Qt.UserRole, (encoded_hours or "", encoding_ambiguous))
        # Stashed so a later Description edit can learn/re-apply a rename
        # keyed by the true original requested text, not whatever text
        # (possibly already renamed) currently sits in the cell.
        tbl.item(r, _MP_DESC).setData(_DESC_ANCHOR_ROLE, desc)
        tbl.setItem(r, _MP_MATCH,     QTableWidgetItem(default_key))
        tbl.setItem(r, _MP_MDESC,     _ro_item(""))
        tbl.setItem(r, _MP_NUMEMP,    QTableWidgetItem(num_emp_text))
        tbl.setItem(r, _MP_QTY,       QTableWidgetItem(qty_text))
        # blockSignals: setData on an item already in the table fires
        # cellChanged same as a real edit would — without this, stashing
        # working_days here would immediately trip _on_manpower_changed's
        # "manual Quantity edit" handling and clear the very value just set.
        tbl.blockSignals(True)
        tbl.item(r, _MP_QTY).setData(Qt.UserRole, working_days)
        tbl.blockSignals(False)
        tbl.setItem(r, _MP_UOM,       _ro_item("Hours"))
        tbl.setItem(r, _MP_RATE,      QTableWidgetItem(""))
        tbl.setItem(r, _MP_TOTAL,     _ro_item("0.00"))
        tbl.setItem(r, _MP_STATUS,    _ro_item("✗ No match — edit Match By or Description"))
        tbl.setItem(r, _MP_NAT,       QTableWidgetItem("NAT"))

        self._rematch_manpower_row(r, learn_alias=False)

    def _add_manpower_row(self):
        tbl = self._req_manpower_tbl
        tbl.blockSignals(True)
        r = tbl.rowCount()
        tbl.insertRow(r)
        tbl.setItem(r, _MP_TYPE,      QTableWidgetItem(self._job_location()))
        tbl.setItem(r, _MP_SHIFT,     QTableWidgetItem(""))
        tbl.setItem(r, _MP_SHIFTTYPE, QTableWidgetItem(""))
        tbl.setItem(r, _MP_ENC,       QTableWidgetItem(""))
        tbl.setItem(r, _MP_DESC,      QTableWidgetItem(""))
        tbl.setItem(r, _MP_MATCH,     QTableWidgetItem(""))
        tbl.setItem(r, _MP_MDESC,     _ro_item(""))
        tbl.setItem(r, _MP_NUMEMP,    QTableWidgetItem(""))
        tbl.setItem(r, _MP_QTY,       QTableWidgetItem(""))
        tbl.setItem(r, _MP_UOM,       _ro_item("Hours"))
        tbl.setItem(r, _MP_RATE,      QTableWidgetItem(""))
        tbl.setItem(r, _MP_TOTAL,     _ro_item("0.00"))
        tbl.setItem(r, _MP_STATUS,    _ro_item("✓ Manual"))
        tbl.setItem(r, _MP_NAT,       QTableWidgetItem("NAT"))
        tbl.blockSignals(False)
        _highlight_row(tbl, r, False)

    def _del_manpower_row(self):
        rows = sorted({i.row() for i in self._req_manpower_tbl.selectedItems()}, reverse=True)
        for r in rows:
            self._req_manpower_tbl.removeRow(r)
        self._recalc_azn_totals()

    def _on_manpower_changed(self, row: int, col: int):
        if col == _MP_DESC:
            self._apply_desc_alias_manpower(row)
            self._learn_desc_rename_manpower(row)
        if col == _MP_ENC:
            self._apply_manpower_encoding(row)
            self._rematch_manpower_row(row)
        elif col in (_MP_TYPE, _MP_SHIFT, _MP_SHIFTTYPE, _MP_MATCH):
            if col != _MP_MATCH:
                self._drop_stale_encoding(row)
            self._rematch_manpower_row(row)
        elif col in (_MP_NUMEMP, _MP_QTY, _MP_RATE):
            if col == _MP_QTY:
                # A direct manual edit means this Quantity is no longer
                # derived from the request's working-day count — clear the
                # stash so a later Shift/Type/Match rematch treats it as a
                # plain hours entry instead of silently recomputing over
                # what was just typed in (see _rematch_manpower_row).
                # blockSignals: setData on an item already in the table
                # re-fires cellChanged for *any* role, not just user edits
                # — without this it would immediately re-enter this same
                # handler for the very setData call clearing the stash.
                item = self._req_manpower_tbl.item(row, _MP_QTY)
                if item is not None:
                    self._req_manpower_tbl.blockSignals(True)
                    item.setData(Qt.UserRole, None)
                    self._req_manpower_tbl.blockSignals(False)
            self._recalc_manpower_row(row)
        if col in (_MP_TYPE, _MP_SHIFT, _MP_SHIFTTYPE, _MP_ENC, _MP_MATCH, _MP_NUMEMP, _MP_QTY, _MP_RATE):
            self._recalc_azn_totals()
        self._req_manpower_tbl.viewport().update()      # the days view follows the Encoding

    def _apply_desc_alias_manpower(self, row: int):
        """
        Fires when a row's Description changes (typed by hand, or a fresh
        "+ Add Row" being filled in). If the new text has a saved rename,
        apply it to Match By immediately — same authority as the "Manage
        Saved Renames" sweep. Otherwise, give a still-blank Match By a
        sensible starting point so a brand-new row is searchable right away.
        """
        tbl = self._req_manpower_tbl
        desc_item  = tbl.item(row, _MP_DESC)
        match_item = tbl.item(row, _MP_MATCH)
        if desc_item is None or match_item is None:
            return
        desc  = desc_item.text().strip()
        alias = get_alias(self._aliases, "manpower", desc)
        if alias:
            if match_item.text().strip() != alias:
                match_item.setText(alias)   # cascades: rematch + totals
        elif not match_item.text().strip() and desc:
            match_item.setText(desc)        # cascades: rematch + totals

    def _learn_desc_rename_manpower(self, row: int):
        """
        Fires when a row's Description changes. The item's _DESC_ANCHOR_ROLE
        holds the row's original requested description — a fixed anchor set
        once (at load time from the CTR Request, or on this cell's first
        edit for a hand-typed "+ Add Row"). Comparing the new text against
        that anchor (rather than whatever the previous edit left behind)
        means a rename is always learned as "original → latest", so a
        future CTR Request with the same original description picks up the
        final intended rename, not an intermediate one.
        """
        tbl = self._req_manpower_tbl
        desc_item = tbl.item(row, _MP_DESC)
        if desc_item is None:
            return
        current = desc_item.text().strip()
        anchor = desc_item.data(_DESC_ANCHOR_ROLE)
        if not anchor:
            if current:
                desc_item.setData(_DESC_ANCHOR_ROLE, current)
            return
        anchor = str(anchor).strip()
        if current and current.lower() != anchor.lower():
            set_desc_rename(self._desc_renames, "manpower", anchor, current)

    def _recalc_manpower_row(self, row: int):
        tbl = self._req_manpower_tbl
        # blockSignals covers the whole function, not just the final
        # setItem below: _val's setToolTip on a malformed cell fires
        # cellChanged the same as a real edit would, which would otherwise
        # re-enter _on_manpower_changed and recurse back into this method.
        tbl.blockSignals(True)
        def _val(c):
            item = tbl.item(row, c)
            if not item:
                return 0.0
            try:
                v = float(item.text())
            except ValueError:
                item.setToolTip("Empty — enter a value; treated as 0 here." if not item.text().strip()
                                else f'"{item.text()}" is not a valid number — treated as 0 here.')
                return 0.0
            item.setToolTip("")
            return v
        total = _val(_MP_NUMEMP) * _val(_MP_QTY) * _val(_MP_RATE) * self._fx_factor("manpower", tbl, _MP_RATE, row)
        tbl.setItem(row, _MP_TOTAL, self._total_item(total, "manpower", tbl, _MP_RATE, row))
        tbl.blockSignals(False)

    def _rematch_manpower_row(self, row: int, learn_alias: bool = True):
        tbl = self._req_manpower_tbl
        desc_item   = tbl.item(row, _MP_DESC)
        match_item  = tbl.item(row, _MP_MATCH)
        type_item   = tbl.item(row, _MP_TYPE)
        shift_item  = tbl.item(row, _MP_SHIFT)
        stype_item  = tbl.item(row, _MP_SHIFTTYPE)
        status_item = tbl.item(row, _MP_STATUS)
        if desc_item is None or match_item is None:
            return

        key  = match_item.text().strip()
        desc = desc_item.text().strip()
        location   = type_item.text()  if type_item  else ""
        shift      = shift_item.text() if shift_item else ""
        shift_type = stype_item.text() if stype_item else ""
        hint = desc_item.data(Qt.UserRole)
        hours, ambiguous = hint if isinstance(hint, tuple) else (hint, False)
        hours = hours or None
        was_includable = bool(status_item) and status_item.text().startswith("✓")
        if self._azn_df.empty and not was_includable:
            self._mark_waiting(tbl, row, _MP_STATUS)       # nothing to match against yet
            return
        enc_item = tbl.item(row, _MP_ENC)
        enc = (enc_item.text() if enc_item else "").strip().upper()
        names = self._row_role_names(row)
        code_key = strip_support_keyword(key).strip().lower()
        exact_code = (not self._azn_df.empty and bool(
            (self._azn_df["stock_code"].astype(str).str.strip().str.lower() == code_key).any()))
        # Why a row must stay unmatched even though a rate might exist: the
        # Encoding is not a valid variant of the role, or it would mix Onshore
        # and Offshore. (An exact stock code in Match By bypasses all of this.)
        why = None
        if not exact_code:
            dec = _decode_manpower_encoding_full(enc) if enc else None
            on_role = self._is_onshore_role(*names)
            known = next((self._enc_variants[n] for n in names if n in self._enc_variants), None)
            job_loc = self._job_location()
            if enc and dec is None:
                why = f"“{enc}” is not an encoding (expected like NS-NAT-OFF-12)"
            elif enc and known is not None and enc not in known:
                why = f"{enc} is not a valid variant of this role — choose one from the Encoding list"
            elif dec and on_role and dec[0] == "Offshore":
                why = "this role is always Onshore — choose an Onshore encoding"
            elif dec and not on_role and job_loc and dec[0] != job_loc:
                why = (f"{dec[0]} encoding in an {job_loc} request — Onshore and Offshore are "
                       f"not mixed; choose an {job_loc} encoding")
        match = None if why else _match_azn_labor(
            self._azn_df, strip_support_keyword(key), location, shift, shift_type, hours, ambiguous)

        tbl.blockSignals(True)
        if match:
            mdesc, rate, stock_code = match
            tbl.setItem(row, _MP_MDESC, _ro_item(f"{mdesc} ({stock_code})"))
            tbl.setItem(row, _MP_RATE, QTableWidgetItem(f"{rate:.2f}"))
            tbl.setItem(row, _MP_STATUS, _ro_item("✓ Matched"))
        else:
            tbl.setItem(row, _MP_MDESC, _ro_item(""))
            if was_includable:
                status_text = "✓ Manual"
            elif why:
                status_text = f"✗ No match — {why}"
            elif ambiguous:
                status_text = "✗ No match — ENCODING prefix unrecognized/conflicts with Shift+Status; fix request or set Match By to the exact stock code"
            elif not enc:
                if not self._encoding_options(row) and (self._enc_variants or not self._azn_df.empty):
                    status_text = ("✗ No match — no encoding exists for this role at this Type "
                                   "(see the Combined DB's variants; an always-Onshore role is listed in ENC_LIST)")
                else:
                    status_text = "✗ No match — the request gives no Encoding: choose it from the Encoding list"
            elif not shift.strip() or not shift_type.strip():
                status_text = "✗ No match — the request does not give the Shift / Shift Type: choose them in the table"
            else:
                status_text = "✗ No match — edit Match By or Description"
            tbl.setItem(row, _MP_STATUS, _ro_item(status_text))

        # Quantity is billed in hours (working days × hours/shift) — the
        # matched stock code's suffix is the authoritative hours/shift once
        # a match is found (see _decode_azn_stock_code); the request's own
        # ENCODING hint is used as a fallback for e.g. a "✓ Manual" row that
        # never matched a stock code. working_days (stashed on this cell by
        # _append_manpower_row_from_request) is the original day count, so
        # rematching (e.g. after Shift/Type changes the resolved hours)
        # recomputes from that instead of compounding onto an
        # already-multiplied value. Manually-added rows have no stashed
        # working_days and are left as a plain hours entry.
        qty_item = tbl.item(row, _MP_QTY)
        working_days = qty_item.data(Qt.UserRole) if qty_item else None
        if isinstance(working_days, (int, float)):
            effective_hours = None
            if match:
                decoded = _decode_azn_stock_code(stock_code)
                if decoded:
                    effective_hours = decoded[3]
            effective_hours = effective_hours or hours
            if effective_hours:
                try:
                    qty_item.setText(str(float(working_days) * float(effective_hours)))
                except (TypeError, ValueError):
                    pass
        # _highlight_row colours every cell in the row (including Quantity)
        # via setBackground/setForeground, which fires cellChanged the same
        # as a real edit would — blockSignals only suppresses that signal,
        # not the actual repaint, so calling it before unblocking is free
        # and avoids spuriously re-entering _on_manpower_changed for every
        # column (which, for Quantity, would wrongly look like a manual
        # edit and clear the working_days stash above).
        _highlight_row(tbl, row, bool(match))
        tbl.blockSignals(False)
        self._recalc_manpower_row(row)

        # Learn the rename if Match By differs from the requested description
        # — remembered so future CTR Requests auto-fill the same correction.
        if learn_alias and match and key and key.lower() != desc.lower():
            set_alias(self._aliases, "manpower", desc, key)

    # -- equipment --

    def _append_equip_row_from_request(self, request_item: dict):
        tbl = self._req_equip_tbl
        r = tbl.rowCount()
        tbl.insertRow(r)

        desc = request_item.get("description", "")
        code = request_item.get("stock_code", "")
        # Display text may differ from the raw requested description if the
        # user previously taught a Description rename for it — keyed by
        # (description, stock code) since the same description can belong
        # to different stock codes with different intended renames. Match
        # By alias lookup below uses the same composite key.
        desc_display = _get_desc_rename_by_stock(self._desc_renames, "equipment", desc, code) or desc
        default_key = _get_alias_by_stock(self._aliases, "equipment", desc, code) or code or desc
        qty_text = _request_qty_text(request_item.get("quantity"))
        days_text = _request_qty_text(request_item.get("days"))   # blank stays blank

        tbl.setItem(r, _EQ_DESC,   QTableWidgetItem(desc_display))
        tbl.setItem(r, _EQ_CODE,   QTableWidgetItem(code))
        tbl.setItem(r, _EQ_MATCH,  QTableWidgetItem(default_key))
        tbl.setItem(r, _EQ_MDESC,  _ro_item(""))
        tbl.setItem(r, _EQ_QTY,    QTableWidgetItem(qty_text))
        tbl.setItem(r, _EQ_UNIT,   QTableWidgetItem(request_item.get("uom") or ""))
        tbl.setItem(r, _EQ_RATE,   QTableWidgetItem(""))
        tbl.setItem(r, _EQ_DAYS,   QTableWidgetItem(days_text))
        tbl.setItem(r, _EQ_TOTAL,  _ro_item("0.00"))
        tbl.setItem(r, _EQ_STATUS, _ro_item("✗ No match — edit Match By or Description"))
        # Stash the request sheet's own Rechargability tag so _rematch_equip_row
        # can cross-check it against the CTR_NAMES_DB_USD bridge result.
        tbl.item(r, _EQ_DESC).setData(Qt.UserRole, request_item.get("rechargability") or "")
        # Stashed so a later Description edit can learn/re-apply a rename
        # keyed by the true original requested text, not whatever text
        # (possibly already renamed) currently sits in the cell.
        tbl.item(r, _EQ_DESC).setData(_DESC_ANCHOR_ROLE, desc)

        self._rematch_equip_row(r, learn_alias=False)

    def _add_equip_row(self):
        tbl = self._req_equip_tbl
        tbl.blockSignals(True)
        r = tbl.rowCount()
        tbl.insertRow(r)
        tbl.setItem(r, _EQ_DESC,   QTableWidgetItem(""))
        tbl.setItem(r, _EQ_CODE,   QTableWidgetItem(""))
        tbl.setItem(r, _EQ_MATCH,  QTableWidgetItem(""))
        tbl.setItem(r, _EQ_MDESC,  _ro_item(""))
        tbl.setItem(r, _EQ_QTY,    QTableWidgetItem(""))
        tbl.setItem(r, _EQ_UNIT,   QTableWidgetItem(""))
        tbl.setItem(r, _EQ_RATE,   QTableWidgetItem(""))
        tbl.setItem(r, _EQ_DAYS,   QTableWidgetItem(""))
        tbl.setItem(r, _EQ_TOTAL,  _ro_item("0.00"))
        tbl.setItem(r, _EQ_STATUS, _ro_item("✓ Manual"))
        tbl.blockSignals(False)
        _highlight_row(tbl, r, False)

    def _del_equip_row(self):
        rows = sorted({i.row() for i in self._req_equip_tbl.selectedItems()}, reverse=True)
        for r in rows:
            self._req_equip_tbl.removeRow(r)
        self._recalc_usd_totals()

    def _on_equip_changed(self, row: int, col: int):
        if col in (_EQ_DESC, _EQ_CODE):
            self._apply_desc_alias_equip(row)
        if col == _EQ_DESC:
            self._learn_desc_rename_equip(row)
        if col == _EQ_MATCH:
            self._rematch_equip_row(row)
        elif col in (_EQ_QTY, _EQ_RATE, _EQ_DAYS):
            self._recalc_equip_row(row)
        if col in (_EQ_MATCH, _EQ_QTY, _EQ_RATE, _EQ_DAYS):
            self._recalc_usd_totals()

    def _apply_desc_alias_equip(self, row: int):
        """See _apply_desc_alias_manpower — same idea, keyed by (Description,
        Stock Code), see _stock_rename_key."""
        tbl = self._req_equip_tbl
        desc_item  = tbl.item(row, _EQ_DESC)
        code_item  = tbl.item(row, _EQ_CODE)
        match_item = tbl.item(row, _EQ_MATCH)
        if desc_item is None or match_item is None:
            return
        desc  = desc_item.text().strip()
        code  = code_item.text().strip() if code_item else ""
        alias = _get_alias_by_stock(self._aliases, "equipment", desc, code)
        if alias:
            if match_item.text().strip() != alias:
                match_item.setText(alias)
        elif not match_item.text().strip():
            # Default to stock code, then description (mirrors append-from-request logic)
            match_item.setText(code or desc)

    def _learn_desc_rename_equip(self, row: int):
        """See _learn_desc_rename_manpower — same idea, for Equipment, keyed
        by (original Description, Stock Code), see _stock_rename_key."""
        tbl = self._req_equip_tbl
        desc_item = tbl.item(row, _EQ_DESC)
        code_item = tbl.item(row, _EQ_CODE)
        if desc_item is None:
            return
        current = desc_item.text().strip()
        anchor = desc_item.data(_DESC_ANCHOR_ROLE)
        if not anchor:
            if current:
                desc_item.setData(_DESC_ANCHOR_ROLE, current)
            return
        anchor = str(anchor).strip()
        code = code_item.text().strip() if code_item else ""
        if current and current.lower() != anchor.lower():
            set_desc_rename(self._desc_renames, "equipment", _stock_rename_key(anchor, code), current)

    def _recalc_equip_row(self, row: int):
        tbl = self._req_equip_tbl
        tbl.blockSignals(True)
        def _val(c):
            item = tbl.item(row, c)
            if not item:
                return 0.0
            try:
                v = float(item.text())
            except ValueError:
                item.setToolTip("Empty — enter a value; treated as 0 here." if not item.text().strip()
                                else f'"{item.text()}" is not a valid number — treated as 0 here.')
                return 0.0
            item.setToolTip("")
            return v
        total = _val(_EQ_QTY) * _val(_EQ_RATE) * _val(_EQ_DAYS) * self._fx_factor("equipment", tbl, _EQ_RATE, row)
        tbl.setItem(row, _EQ_TOTAL, self._total_item(total, "equipment", tbl, _EQ_RATE, row))
        tbl.blockSignals(False)

    def _rematch_equip_row(self, row: int, learn_alias: bool = True):
        tbl = self._req_equip_tbl
        desc_item   = tbl.item(row, _EQ_DESC)
        code_item   = tbl.item(row, _EQ_CODE)
        match_item  = tbl.item(row, _EQ_MATCH)
        status_item = tbl.item(row, _EQ_STATUS)
        if desc_item is None or match_item is None:
            return

        key  = match_item.text().strip()
        desc = desc_item.text().strip()
        code = code_item.text().strip() if code_item else ""
        identity = (code or desc).lower()
        was_includable = bool(status_item) and status_item.text().startswith("✓")

        if self._usd_pb_df.empty and not was_includable:
            self._mark_waiting(tbl, row, _EQ_STATUS)       # nothing to match against yet
            return

        # Stage 1: Equipment Names DB bridge lookup by "Match By" as a stock
        # code — resolves the request's internal stock code to the canonical
        # description used to price it in the USD Pricebook (equipment
        # stock codes there are generic rate-type codes, not unique per
        # item — see _match_usd_equipment). non_recharge rows always bill
        # at 0, shown via legacy_name, without a pricebook lookup at all.
        lookup_desc: str | None = None    # description to search the USD Pricebook with
        matched_name: str | None = None   # display name once resolved
        matched_rate: float = 0.0
        db_non_recharge: bool | None = None

        if not self._names_db_df.empty:
            key_up = key.upper()
            db_hits = self._names_db_df[
                self._names_db_df["product"].str.strip().str.upper() == key_up
            ]
            if not db_hits.empty:
                bridge_hit = db_hits.iloc[0]
                db_non_recharge = bool(bridge_hit["non_recharge"])
                if bridge_hit["non_recharge"]:
                    matched_name = bridge_hit["legacy_name"]
                    matched_rate = 0.0
                else:
                    lookup_desc = bridge_hit["canonical_name"] or bridge_hit["legacy_name"]

        # Stage 2: the stock code isn't in the bridge at all — fall back to
        # matching whatever "Match By" contains directly against the USD
        # Pricebook by description.
        if matched_name is None and lookup_desc is None:
            lookup_desc = key

        if matched_name is None and lookup_desc:
            usd_match = _match_usd_equipment(self._usd_pb_df, lookup_desc)
            if usd_match:
                matched_name, matched_rate = usd_match

        # Cross-check the request sheet's own Rechargability tag against the
        # DB bridge's determination, purely as a data-quality warning — the
        # tag never feeds into matched_rate/rate_text above; the bridge is
        # always authoritative for equipment pricing.
        request_tag = desc_item.data(Qt.UserRole) or ""
        mismatch = db_non_recharge is not None and _recharge_mismatch(request_tag, db_non_recharge)

        # SAGE local_expect_cost, purely for the internal "Total SC Eq Cost"
        # Pricing-sheet column (see _SAGE_COST_ROLE) — a separate lookup from
        # the USD Pricebook match above, independent of matched_rate (what's
        # actually billed) and unaffected by rechargeability. Same key and
        # helper consumables already use for their own SAGE match.
        sage_match = _match_lookup(
            self._sage_df, "product", "long_description", "local_expect_cost", key)
        sage_cost = sage_match[1] if sage_match else None     # None = not in SAGE

        tbl.blockSignals(True)
        desc_item.setData(_SAGE_COST_ROLE, sage_cost)
        if matched_name is not None:
            tbl.setItem(row, _EQ_MDESC, _ro_item(matched_name))
            rate_text = "NONRECHARG" if db_non_recharge else f"{matched_rate:.2f}"
            tbl.setItem(row, _EQ_RATE, QTableWidgetItem(rate_text))
            status = "✓ Matched"
            if mismatch:
                status += "  ⚠ Rechargability mismatch vs. request sheet — using Equipment Names DB"
            tbl.setItem(row, _EQ_STATUS, _ro_item(status))
        else:
            tbl.setItem(row, _EQ_MDESC, _ro_item(""))
            tbl.setItem(row, _EQ_STATUS, _ro_item(
                "✓ Manual" if was_includable else "✗ No match — edit Match By or Description"))
        tbl.blockSignals(False)
        self._recalc_equip_row(row)
        _highlight_row(tbl, row, matched_name is not None)

        if learn_alias and matched_name is not None and key and key.lower() != identity:
            set_alias(self._aliases, "equipment", _stock_rename_key(desc, code), key)

    # -- consumables --

    def _append_cons_row_from_request(self, request_item: dict):
        tbl = self._req_cons_tbl
        r = tbl.rowCount()
        tbl.insertRow(r)

        desc = request_item.get("description", "")
        code = request_item.get("stock_code", "")
        # Display text may differ from the raw requested description if the
        # user previously taught a Description rename for it — keyed by
        # (description, stock code) since the same description can belong
        # to different stock codes with different intended renames. Match
        # By alias lookup below uses the same composite key.
        desc_display = _get_desc_rename_by_stock(self._desc_renames, "consumable", desc, code) or desc
        default_key = _get_alias_by_stock(self._aliases, "consumable", desc, code) or code or desc
        qty_text = _request_qty_text(request_item.get("quantity"))

        tbl.setItem(r, _CS_DESC,   QTableWidgetItem(desc_display))
        tbl.setItem(r, _CS_CODE,   QTableWidgetItem(code))
        tbl.setItem(r, _CS_MATCH,  QTableWidgetItem(default_key))
        tbl.setItem(r, _CS_MDESC,  _ro_item(""))
        tbl.setItem(r, _CS_QTY,    QTableWidgetItem(qty_text))
        tbl.setItem(r, _CS_UNIT,   QTableWidgetItem(request_item.get("uom") or ""))
        tbl.setItem(r, _CS_PRICE,  QTableWidgetItem(""))
        tbl.setItem(r, _CS_TOTAL,  _ro_item("0.00"))
        tbl.setItem(r, _CS_STATUS, _ro_item("✗ No match — edit Match By or Description"))
        # Stash the request sheet's own Rechargability tag so _rematch_cons_row
        # can cross-check it against the CTR_NAMES_DB_USD bridge result.
        tbl.item(r, _CS_DESC).setData(Qt.UserRole, request_item.get("rechargability") or "")
        # Stashed so a later Description edit can learn/re-apply a rename
        # keyed by the true original requested text, not whatever text
        # (possibly already renamed) currently sits in the cell.
        tbl.item(r, _CS_DESC).setData(_DESC_ANCHOR_ROLE, desc)

        self._rematch_cons_row(r, learn_alias=False)

    def _add_cons_row(self):
        tbl = self._req_cons_tbl
        tbl.blockSignals(True)
        r = tbl.rowCount()
        tbl.insertRow(r)
        tbl.setItem(r, _CS_DESC,   QTableWidgetItem(""))
        tbl.setItem(r, _CS_CODE,   QTableWidgetItem(""))
        tbl.setItem(r, _CS_MATCH,  QTableWidgetItem(""))
        tbl.setItem(r, _CS_MDESC,  _ro_item(""))
        tbl.setItem(r, _CS_QTY,    QTableWidgetItem(""))
        tbl.setItem(r, _CS_UNIT,   QTableWidgetItem(""))
        tbl.setItem(r, _CS_PRICE,  QTableWidgetItem(""))
        tbl.setItem(r, _CS_TOTAL,  _ro_item("0.00"))
        tbl.setItem(r, _CS_STATUS, _ro_item("✓ Manual"))
        tbl.blockSignals(False)
        _highlight_row(tbl, r, False)

    def _del_cons_row(self):
        rows = sorted({i.row() for i in self._req_cons_tbl.selectedItems()}, reverse=True)
        for r in rows:
            self._req_cons_tbl.removeRow(r)
        self._recalc_usd_totals()

    def _on_cons_changed(self, row: int, col: int):
        if col in (_CS_DESC, _CS_CODE):
            self._apply_desc_alias_cons(row)
        if col == _CS_DESC:
            self._learn_desc_rename_cons(row)
        if col == _CS_MATCH:
            self._rematch_cons_row(row)
        elif col in (_CS_QTY, _CS_PRICE):
            self._recalc_cons_row(row)
        if col in (_CS_MATCH, _CS_QTY, _CS_PRICE):
            self._recalc_usd_totals()

    def _apply_desc_alias_cons(self, row: int):
        """See _apply_desc_alias_manpower — same idea, keyed by (Description,
        Stock Code), see _stock_rename_key."""
        tbl = self._req_cons_tbl
        desc_item  = tbl.item(row, _CS_DESC)
        code_item  = tbl.item(row, _CS_CODE)
        match_item = tbl.item(row, _CS_MATCH)
        if desc_item is None or match_item is None:
            return
        desc  = desc_item.text().strip()
        code  = code_item.text().strip() if code_item else ""
        alias = _get_alias_by_stock(self._aliases, "consumable", desc, code)
        if alias:
            if match_item.text().strip() != alias:
                match_item.setText(alias)
        elif not match_item.text().strip() and (code or desc):
            match_item.setText(code or desc)

    def _learn_desc_rename_cons(self, row: int):
        """See _learn_desc_rename_manpower — same idea, for Consumables,
        keyed by (original Description, Stock Code), see _stock_rename_key."""
        tbl = self._req_cons_tbl
        desc_item = tbl.item(row, _CS_DESC)
        code_item = tbl.item(row, _CS_CODE)
        if desc_item is None:
            return
        current = desc_item.text().strip()
        anchor = desc_item.data(_DESC_ANCHOR_ROLE)
        if not anchor:
            if current:
                desc_item.setData(_DESC_ANCHOR_ROLE, current)
            return
        anchor = str(anchor).strip()
        code = code_item.text().strip() if code_item else ""
        if current and current.lower() != anchor.lower():
            set_desc_rename(self._desc_renames, "consumable", _stock_rename_key(anchor, code), current)

    def _recalc_cons_row(self, row: int):
        tbl = self._req_cons_tbl
        tbl.blockSignals(True)
        def _val(c):
            item = tbl.item(row, c)
            if not item:
                return 0.0
            try:
                v = float(item.text())
            except ValueError:
                item.setToolTip("Empty — enter a value; treated as 0 here." if not item.text().strip()
                                else f'"{item.text()}" is not a valid number — treated as 0 here.')
                return 0.0
            item.setToolTip("")
            return v
        total = _val(_CS_QTY) * _val(_CS_PRICE) * self._fx_factor("consumables", tbl, _CS_PRICE, row)
        tbl.setItem(row, _CS_TOTAL, self._total_item(total, "consumables", tbl, _CS_PRICE, row))
        tbl.blockSignals(False)

    def _rematch_cons_row(self, row: int, learn_alias: bool = True):
        tbl = self._req_cons_tbl
        desc_item  = tbl.item(row, _CS_DESC)
        code_item  = tbl.item(row, _CS_CODE)
        match_item = tbl.item(row, _CS_MATCH)
        status_item = tbl.item(row, _CS_STATUS)
        if desc_item is None or match_item is None:
            return

        key  = match_item.text().strip()
        desc = desc_item.text().strip()
        code = code_item.text().strip() if code_item else ""
        identity = (code or desc).lower()
        was_includable = bool(status_item) and status_item.text().startswith("✓")
        if self._sage_df.empty and not was_includable:
            self._mark_waiting(tbl, row, _CS_STATUS)       # nothing to match against yet
            return
        match = _match_lookup(self._sage_df, "product", "long_description", "local_expect_cost", key)

        # SAGE's own analysis_b flag is the rechargeability source for
        # consumables — a non_recharge hit forces the price to 0 and shows
        # "NONRECHARG", mirroring equipment's treatment (see
        # _rematch_equip_row / _match_usd_equipment).
        db_non_recharge = _sage_non_recharge(self._sage_df, key)

        # Cross-check the request sheet's own Rechargability tag against
        # SAGE's determination, purely as a data-quality warning — the tag
        # never feeds into price_text above; SAGE is always authoritative.
        request_tag = desc_item.data(Qt.UserRole) or ""
        mismatch = db_non_recharge is not None and _recharge_mismatch(request_tag, db_non_recharge)

        tbl.blockSignals(True)
        if match:
            mdesc, price, _code = match
            # Stashed before price_text below can replace the visible cell
            # with the "NONRECHARG" sentinel — the internal "Total SC Mat
            # Cost" column (see _SAGE_COST_ROLE) needs the real number on
            # every row, rechargeable or not.
            desc_item.setData(_SAGE_COST_ROLE, price)
            tbl.setItem(row, _CS_MDESC, _ro_item(mdesc))
            price_text = "NONRECHARG" if db_non_recharge else f"{price:.2f}"
            tbl.setItem(row, _CS_PRICE, QTableWidgetItem(price_text))
            status = "✓ Matched"
            if mismatch:
                status += "  ⚠ Rechargability mismatch vs. request sheet — using SAGE"
            tbl.setItem(row, _CS_STATUS, _ro_item(status))
        else:
            desc_item.setData(_SAGE_COST_ROLE, None)           # not in SAGE
            tbl.setItem(row, _CS_MDESC, _ro_item(""))
            tbl.setItem(row, _CS_STATUS, _ro_item(
                "✓ Manual" if was_includable else "✗ No match — edit Match By or Description"))
        tbl.blockSignals(False)
        self._recalc_cons_row(row)
        _highlight_row(tbl, row, bool(match))

        if learn_alias and match and key and key.lower() != identity:
            set_alias(self._aliases, "consumable", _stock_rename_key(desc, code), key)

    # ── Totals ────────────────────────────────────────────────────────────────

    def _recalc_azn_totals(self):
        timer = getattr(self, "_stats_timer", None)
        if timer is not None:
            timer.start()           # re-check the missing-value markers shortly
        tbl = self._req_manpower_tbl
        support_total = 0.0
        other_total = 0.0
        for r in range(tbl.rowCount()):
            status_item = tbl.item(r, _MP_STATUS)
            if not (status_item and status_item.text().startswith("✓")):
                continue
            total_item = tbl.item(r, _MP_TOTAL)
            try:
                total = float(total_item.text()) if total_item else 0.0
            except ValueError:
                total = 0.0
            desc_item = tbl.item(r, _MP_DESC)
            if is_support_manpower(desc_item.text() if desc_item else ""):
                support_total += total
            else:
                other_total += total

        third_party_total = 0.0
        for r in range(self._transport_tbl.rowCount()):
            total_item = self._transport_tbl.item(r, _TR_TOTAL)
            try:
                third_party_total += float(total_item.text()) if total_item else 0.0
            except ValueError:
                pass

        other_label = activities_label(self._project_type_edit.text())
        self._azn_onshore_lbl.setText(f"Project Support:  ₼ {support_total:,.2f}")  # PDMS mode: all rows are in manats
        self._azn_offshore_lbl.setText(f"Total {other_label}:  ₼ {other_total:,.2f}")
        self._azn_third_party_lbl.setText(
            f"Total Third Party Activities:  ₼ {third_party_total:,.2f}")
        self._azn_total_lbl.setText(
            f"AZN CTR Total:  ₼ {support_total + other_total + third_party_total:,.2f}"
            + ("   (Hotbed not included — see the price totals)"
               if self._pdms_on() and self._hotbed_complete() else ""))

    def _recalc_usd_totals(self):
        timer = getattr(self, "_stats_timer", None)
        if timer is not None:
            timer.start()           # refresh the cost statistics box shortly
        equip_total = 0.0
        for r in range(self._req_equip_tbl.rowCount()):
            status_item = self._req_equip_tbl.item(r, _EQ_STATUS)
            if not (status_item and status_item.text().startswith("✓")):
                continue
            total_item = self._req_equip_tbl.item(r, _EQ_TOTAL)
            try:
                equip_total += float(total_item.text()) if total_item else 0.0
            except ValueError:
                pass

        cons_raw = 0.0
        for r in range(self._req_cons_tbl.rowCount()):
            status_item = self._req_cons_tbl.item(r, _CS_STATUS)
            if not (status_item and status_item.text().startswith("✓")):
                continue
            total_item = self._req_cons_tbl.item(r, _CS_TOTAL)
            try:
                cons_raw += float(total_item.text()) if total_item else 0.0
            except ValueError:
                pass

        markup_pct       = self._markup_spin.value()
        cons_with_markup = cons_raw * (1 + markup_pct / 100.0)
        ctr_total        = equip_total + cons_with_markup

        sign = "₼" if self._pdms_on() else "$"        # PDMS mode: the rows' totals are in manats
        self._usd_equip_lbl.setText(f"Equipment:  {sign}{equip_total:,.2f}")
        self._usd_cons_lbl.setText(
            f"Consumables (incl. {markup_pct:.2f}% markup):  {sign}{cons_with_markup:,.2f}")
        self._usd_total_lbl.setText(("Equipment + Consumables Total:  " if self._pdms_on()
                                     else "USD CTR Total:  ") + f"{sign}{ctr_total:,.2f}")

    # ── Search filter ─────────────────────────────────────────────────────────

    def _filter_table(self, tbl: QTableWidget, text: str):
        text = text.strip().lower()
        for row in range(tbl.rowCount()):
            visible = not text or any(
                text in (tbl.item(row, col).text().lower() if tbl.item(row, col) else "")
                for col in range(tbl.columnCount())
            )
            tbl.setRowHidden(row, not visible)

    # ── Unmatched navigation ──────────────────────────────────────────────────

    def _jump_unmatched(self, tbl: QTableWidget, status_col: int, direction: int) -> None:
        """
        Select and scroll to the next (direction=+1) or previous (-1) row
        whose Status starts with "✗". Wraps around. Does nothing if all rows
        are matched or the table is empty.
        """
        n = tbl.rowCount()
        if n == 0:
            return
        current = tbl.currentRow()
        start = current if current >= 0 else (-1 if direction > 0 else n)
        for step in range(1, n + 1):
            candidate = (start + step * direction) % n
            if tbl.isRowHidden(candidate):
                continue
            status = tbl.item(candidate, status_col)
            if status and status.text().startswith("✗"):
                # setCurrentCell updates currentRow() so the next click
                # advances from here; selectRow alone does not.
                tbl.setCurrentCell(candidate, 0)
                tbl.scrollToItem(
                    tbl.item(candidate, status_col),
                    QAbstractItemView.ScrollHint.EnsureVisible,
                )
                return

    # ── Output folder ─────────────────────────────────────────────────────────

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "Select output folder")
        if path:
            self._out_dir_edit.setText(path)

    # ── Generate ──────────────────────────────────────────────────────────────

    def _pdms_payload(self, pdms_tpl: str = "") -> dict:
        """What the PDMS proposal (and the overview sheet) is made from, read from the header fields."""
        return {
            "template": pdms_tpl,
            "rates":      {c: r for c, (r, _d) in self._fx_rates().items()},
            "rates_used": {c: (r, d, "default" if (self._fx[c].get("action") or "").startswith("Default value") else None)
                           for c, (r, d) in self._fx_rates().items() if c in self._currencies_in_use()},
            "currencies": {k: c.currentText() for k, c in self._cat_combos.items()},
            "hotbed":    self._hotbed_values(),
            "header": {
                "prp_number":     self._prp_edit.text().strip(),
                "date":           self._letter_date_edit.text().strip(),
                "customer_name":  self._customer_edit.text().strip(),
                "customer_city":  self._customer_city_edit.text().strip(),
                "attention_name": self._attention_edit.text().strip(),
                "salutation_name": salutation_from_attention(self._attention_edit.text()),
                "scope":          self._scope_edit.text().strip(),
                "project_type":   self._project_type_edit.text().strip(),
                "signer_name":    self._signer_edit.text().strip(),
                "signer_title":   self._signer_title_edit.text().strip(),
            },
        }

    def _pdms_overview_data(self) -> tuple[dict, dict, dict]:
        """(figures, header, {currency: CBAR day or None}) for the "PDMS overview" sheet: the same rows
        and the same arithmetic as the Word proposal. Raises ValueError when a figure is missing
        (a currency without a rate, a manpower row without a shift length)."""
        incomplete = self._incomplete_cells()
        bad_rows = {(id(c["tbl"]), c["row"]) for c in incomplete}
        rows = self._collect_rows(True, bad_rows)
        job = self._pdms_payload()
        data = compute_pdms(
            rows["support_rows"], rows["other_rows"], rows["equip_rows"], rows["consump_rows"], job["header"],
            job["rates"], self._markup_rate(), rows["third_party_rows"], job["hotbed"], job["currencies"])
        info = {c: {"day": d["date"], "stamp": d.get("stamp", ""), "action": d.get("action", "")}
                for c, d in self._fx.items()}
        return data, job["header"], info

    def _collect_rows(self, pdms_on: bool, bad_rows: set) -> dict:
        """The rows that go into a generated document, read from the four tables: only rows with a
        "✓" status and without a missing value (`bad_rows`). Shared by Generate and the PDMS overview."""
        # Only rows with a "✓" status (matched against pricebook/SAGE, or
        # manually added) are included — "✗ No match" rows are skipped so
        # unmatched requests never silently appear in the output at rate 0.
        support_rows, other_rows = [], []
        mp_skipped = 0
        incomplete_skipped = len(bad_rows)    # rows left out for a missing value
        for r in range(self._req_manpower_tbl.rowCount()):
            status_item = self._req_manpower_tbl.item(r, _MP_STATUS)
            if not (status_item and status_item.text().startswith("✓")):
                mp_skipped += 1
                continue
            if (id(self._req_manpower_tbl), r) in bad_rows:
                continue
            def _txt(c, _r=r):
                item = self._req_manpower_tbl.item(_r, c)
                return item.text().strip() if item else ""
            # Every figure below was checked by _incomplete_cells: no stand-ins.
            num_emp = float(_txt(_MP_NUMEMP))
            qty     = float(_txt(_MP_QTY))
            rate    = float(_txt(_MP_RATE))
            shift      = _txt(_MP_SHIFT)
            shift_type = _txt(_MP_SHIFTTYPE)
            # "Normal" is the default Shift Type — worth calling out only
            # when it's something else (Overtime/Rotational/Standby).
            comment = f"{shift} Shift" if shift_type == "Normal" else f"{shift} Shift, {shift_type}"
            # Shift length (hours) from the Encoding, e.g. NS-NAT-OFF-12 -> 12; the matched stock
            # code is the fallback. The PDMS proposal needs it to show days and a day rate.
            _hours = self._row_shift_hours(r)
            row_dict = {
                # Comment is otherwise unused for a manpower row — this is
                # the only place Shift/Shift Type end up in the generated
                # CTR at all (they're used internally to pick the AZN
                # pricebook rate, see _match_azn_labor, but never written
                # to the output on their own).
                "comment":       comment,
                "num_employees": num_emp,
                "description":   _txt(_MP_DESC),
                "quantity":      qty,
                "uom":           _txt(_MP_UOM),
                "rate_azn":      rate,
                "nationality":   _txt(_MP_NAT),
                "hours_per_shift": float(_hours) if _hours else None,
                "encoding":      _txt(_MP_ENC),
                "currency":      self._cell_currency("manpower", self._req_manpower_tbl, _MP_RATE, r) if pdms_on else None,
            }
            if is_support_manpower(row_dict["description"]):
                support_rows.append(row_dict)
            else:
                other_rows.append(row_dict)

        equip_rows = []
        eq_skipped = 0
        for r in range(self._req_equip_tbl.rowCount()):
            status_item = self._req_equip_tbl.item(r, _EQ_STATUS)
            if not (status_item and status_item.text().startswith("✓")):
                eq_skipped += 1
                continue
            if (id(self._req_equip_tbl), r) in bad_rows:
                continue
            def _etxt(c, _r=r):
                item = self._req_equip_tbl.item(_r, c)
                return item.text().strip() if item else ""
            qty = float(_etxt(_EQ_QTY))     # checked by _incomplete_cells: no stand-ins
            rate_txt = _etxt(_EQ_RATE)
            try:
                rate = float(rate_txt)
            except ValueError:
                # Only "NONRECHARG" gets here (checked) — passed through as-is
                # so build_usd can print it instead of a misleading 0.00.
                rate = rate_txt
            days = float(_etxt(_EQ_DAYS))
            # Always show the requested description as typed in the CTR
            # Request, not the matched pricebook name — "Matched Item" is
            # only an internal lookup key used to find the rate, and isn't
            # client-facing text.
            eq_desc_item = self._req_equip_tbl.item(r, _EQ_DESC)
            sage_cost = self._sage_cost_of(eq_desc_item, _etxt(_EQ_MATCH) or _etxt(_EQ_CODE) or _etxt(_EQ_DESC))
            equip_rows.append({
                "description":  _etxt(_EQ_DESC),
                "quantity":     qty,
                "unit":         _etxt(_EQ_UNIT),
                "rate_per_day": rate,
                "days":         days,
                "stock_code":   _etxt(_EQ_CODE),
                "sage_cost":    sage_cost,
                "currency":     self._cell_currency("equipment", self._req_equip_tbl, _EQ_RATE, r) if pdms_on else None,
            })

        consump_rows = []
        cs_skipped = 0
        for r in range(self._req_cons_tbl.rowCount()):
            status_item = self._req_cons_tbl.item(r, _CS_STATUS)
            if not (status_item and status_item.text().startswith("✓")):
                cs_skipped += 1
                continue
            if (id(self._req_cons_tbl), r) in bad_rows:
                continue
            def _ctxt(c, _r=r):
                item = self._req_cons_tbl.item(_r, c)
                return item.text().strip() if item else ""
            qty = float(_ctxt(_CS_QTY))     # checked by _incomplete_cells: no stand-ins
            price_txt = _ctxt(_CS_PRICE)
            try:
                price = float(price_txt)
            except ValueError:
                # Only "NONRECHARG" gets here (checked) — passed through as-is
                # so build_usd can print it instead of a misleading 0.00.
                price = price_txt
            # Same reasoning as equipment above — always show the requested
            # description, not the internal matched-item name.
            cs_desc_item = self._req_cons_tbl.item(r, _CS_DESC)
            sage_cost = self._sage_cost_of(cs_desc_item, _ctxt(_CS_MATCH) or _ctxt(_CS_CODE) or _ctxt(_CS_DESC))
            consump_rows.append({
                "long_description":  _ctxt(_CS_DESC),
                "local_expect_cost": price,
                "unit_code":         _ctxt(_CS_UNIT),
                "product":           _ctxt(_CS_CODE),
                "quantity":          qty,
                "sage_cost":         sage_cost,
                "currency":          self._cell_currency("consumables", self._req_cons_tbl, _CS_PRICE, r) if pdms_on else None,
            })

        # Transport / hired services — the AZN CTR's "Third Party Activities"
        # section. Nothing to match against a pricebook here, so every row is
        # included — except one with a quantity, duration, unit or rate still
        # missing (checked above), which is left out like any other.
        third_party_rows = [
            self._transport_row_values(r) for r in range(self._transport_tbl.rowCount())
            if (id(self._transport_tbl), r) not in bad_rows
        ]
        return {"support_rows": support_rows, "other_rows": other_rows, "equip_rows": equip_rows,
                "consump_rows": consump_rows, "third_party_rows": third_party_rows,
                "mp_skipped": mp_skipped, "eq_skipped": eq_skipped, "cs_skipped": cs_skipped,
                "incomplete_skipped": incomplete_skipped}

    def _generate(self):
        errors   = []
        warnings = []
        job_ref    = self._job_ref_edit.text().strip()
        azn_tpl    = self._azn_tpl_edit.text().strip()
        usd_tpl    = self._usd_tpl_edit.text().strip()
        output_dir = self._out_dir_edit.text().strip()
        pdms_on    = self._pdms_on()
        pdms_tpl   = self._pdms_tpl_edit.text().strip()

        # ── Required fields ────────────────────────────────────────────────────
        if not job_ref:
            errors.append("Job Ref is required.")
        elif not job_ref.isdigit():
            errors.append("Job Ref must be a number (e.g. 217).")

        if pdms_on:
            if not pdms_tpl:
                errors.append("PDMS Template not selected.")
            elif not Path(pdms_tpl).is_file():
                errors.append(f"PDMS Template not found: {pdms_tpl}")
            if not self._prp_edit.text().strip():
                errors.append("PRP No is required for a PDMS proposal.")
            _in_use = self._currencies_in_use()
            for _code, (_rate, _day) in self._fx_rates().items():
                if _code in _in_use and (_rate is None or _rate <= 0):
                    errors.append(f"{_code}→AZN rate must be a positive number (e.g. 1.70) — prices are in {_code}.")
            for _code in sorted(_in_use - set(self._fx)):
                errors.append(f"{_code} is used but turned off in Settings → CBAR currencies.")
        else:
            if not azn_tpl:
                errors.append("AZN Template not selected.")
            elif not Path(azn_tpl).is_file():
                errors.append(f"AZN Template not found: {azn_tpl}")

            if not usd_tpl:
                errors.append("USD Template not selected.")
            elif not Path(usd_tpl).is_file():
                errors.append(f"USD Template not found: {usd_tpl}")

        if not output_dir:
            errors.append("Output folder not selected.")
        else:
            out_path = Path(output_dir)
            if not out_path.exists():
                try:
                    out_path.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    errors.append(f"Cannot create output folder: {e}")
            elif not _is_dir_writable(out_path):
                errors.append(f"Output folder is not writable: {output_dir}")

        if self._req_manpower_tbl.rowCount() == 0:
            errors.append("Manpower table is empty — load a CTR Request or add rows first.")

        self._refresh_stats()
        open_disp = self._stats_panel.disparity_table()
        if len(open_disp):
            codes = sorted(set(open_disp["Stock Code"]))
            shown_d = ", ".join(codes[:6]) + (f" … (+{len(codes) - 6} more)" if len(codes) > 6 else "")
            errors.append(
                f"{len(codes)} Stock Code(s) have a rechargeability that differs from SAGE: {shown_d}. "
                "Open Disparities in the Cost statistics box and choose, for each, which source is right "
                "(the document or SAGE) before generating.")

        _waiting = sum(
            1 for tbl, c in ((self._req_manpower_tbl, _MP_STATUS), (self._req_equip_tbl, _EQ_STATUS),
                             (self._req_cons_tbl, _CS_STATUS))
            for r in range(tbl.rowCount())
            if (tbl.item(r, c) or _ro_item("")).text().startswith("⏳"))
        if _waiting:
            errors.append(f"{_waiting} row(s) are still waiting for the reference files — press "
                          "Load Files first, so they can be matched.")

        if not self._client_edit.text().strip():
            warnings.append("Client is empty — the template cell will be left blank.")

        if pdms_on:
            if not self._attention_edit.text().strip():
                warnings.append("Attention is empty — the proposal's \"Attention:\" and \"Dear …\" lines will be blank.")
            warnings.append("The USD→AZN conversion of equipment and consumables is provisional "
                            "(the colleagues' rule is not confirmed yet).")
            for _code, (_rate, _day) in self._fx_rates().items():
                if _code in self._currencies_in_use() and _day is None and _rate and _rate > 0:   # typed by hand: check it against the log (warn only)
                    _hist = cbar_rates.history(_code)
                    _ref = (_hist[-1][1], _hist[-1][0]) if _hist else None
                    if _code == "USD":
                        _msgs = cbar_rates.validate_typed_rate(str(_rate), _ref)
                    else:
                        _msgs = ([f"{_rate:g} differs by more than {cbar_rates.TYPED_WARN_PCT:g}% from CBAR's "
                                  f"{_ref[0]:.4f} of {_ref[1]}."]
                                 if _ref and abs(_rate - _ref[0]) / _ref[0] * 100 > cbar_rates.TYPED_WARN_PCT else [])
                    warnings += [f"{_code}→AZN rate: {m}" for m in _msgs]
        else:
            if not self._contract_no_azn_edit.text().strip():
                warnings.append("Contract No (AZN) is empty — the AZN CTR's Contract No will be left blank.")
            if not self._contract_no_usd_edit.text().strip():
                warnings.append("Contract No (USD) is empty — the USD CTR's Contract No will be left blank.")

        # Count includable rows to warn if everything is unmatched
        mp_includable = sum(
            1 for r in range(self._req_manpower_tbl.rowCount())
            if (self._req_manpower_tbl.item(r, _MP_STATUS) or _ro_item("")).text().startswith("✓")
        )
        if self._req_manpower_tbl.rowCount() > 0 and mp_includable == 0:
            warnings.append(
                "All manpower rows are unmatched (✗). The AZN CTR will have no labor rows."
            )

        if errors:
            log.warning("Generate blocked: %s", "; ".join(errors))
            QMessageBox.warning(self, "Cannot generate", "\n".join(errors))
            return

        # Rows with a required value missing are never filled with a stand-in
        # (1, 0, 30, …): the user is told which, and if they still generate,
        # those rows are left out of the CTR.
        incomplete = self._incomplete_cells()
        bad_rows = {(id(c["tbl"]), c["row"]) for c in incomplete}
        no_sage = self._lines_without_sage_cost(bad_rows)
        if no_sage:
            if self._sage_df.empty:
                warnings.append(
                    "The SAGE Export is not loaded, so no SAGE cost is known: the CTR's internal "
                    "SC cost cells (Pricing sheet) will be EMPTY. Load the files first if you want them filled.")
            else:
                shown_ns = ", ".join(no_sage[:6]) + (f" … (+{len(no_sage) - 6} more)" if len(no_sage) > 6 else "")
                warnings.append(
                    f"{len(no_sage)} line(s) are NOT IN SAGE ({shown_ns}) — their SAGE cost is unknown, "
                    "so the internal SC cost cell in the CTR is left EMPTY for them. To fill it, press "
                    "Cancel and type each cost into the Cost statistics box (double-click the red row's "
                    "Unit cost); otherwise generate as it is.")
        zero_general, zero_transport = self._nonpositive_quantities(bad_rows)
        if zero_transport:
            warnings.append(
                "Transport line(s) with a quantity of 0 or less are HIDDEN in the AZN CTR (their total "
                "counts as 0, and if every line is like that the section is left out): "
                + "; ".join(zero_transport))
        if zero_general:
            shown_z = "; ".join(zero_general[:8]) + (f"; … (+{len(zero_general) - 8} more)" if len(zero_general) > 8 else "")
            warnings.append(
                f"{len(zero_general)} quantity value(s) are 0 or less — they go into the CTR as entered, "
                f"so check them: {shown_z}")
        if incomplete:
            self._mark_incomplete(incomplete)
            per_row: dict[tuple, list] = {}
            for c in incomplete:
                per_row.setdefault((c["section"], id(c["tbl"]), c["row"], c["desc"]), []).append(c["field"])
            lines = [f"{sec}: {desc} — missing {', '.join(fields)}"
                     for (sec, _t, _r, desc), fields in per_row.items()]
            shown = "<br>".join(html.escape(x) for x in lines[:15])
            if len(lines) > 15:
                shown += f"<br>… and {len(lines) - 15} more"
            log.warning("Generate: %d row(s) with missing required values: %s",
                        len(per_row), "; ".join(lines))
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Missing values — rows will not be included")
            box.setTextFormat(Qt.TextFormat.RichText)
            box.setText(
                '<p style="color:#C62828"><b>'
                f"{len(per_row)} row(s) are not filled in completely and will NOT be "
                "included in the CTR.</b></p>"
                f"<p>{shown}</p>"
                "<p>Nothing is assumed in place of a missing value. Press <b>Cancel</b> to go "
                "and fill them in (they are shown in red), or <b>Generate without them</b>.</p>")
            go = box.addButton("Generate without them", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            generate_anyway = box.clickedButton() is go
            box.deleteLater()
            if not generate_anyway:
                self._empty_qty_idx = -1
                self._jump_empty_qty()
                return

        if warnings:
            reply = QMessageBox.warning(
                self, "Proceed with warnings?",
                "\n".join(warnings) + "\n\nGenerate anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        log.info("Generate clicked: job_ref=%s output_dir=%s", job_ref, output_dir)

        rows = self._collect_rows(pdms_on, bad_rows)
        support_rows, other_rows = rows["support_rows"], rows["other_rows"]
        equip_rows, consump_rows = rows["equip_rows"], rows["consump_rows"]
        third_party_rows = rows["third_party_rows"]
        mp_skipped, eq_skipped, cs_skipped = rows["mp_skipped"], rows["eq_skipped"], rows["cs_skipped"]
        incomplete_skipped = rows["incomplete_skipped"]

        if pdms_on:
            # The proposal's labour table shows days and a day rate; both come from the shift length.
            _bad = [r_["description"] for r_ in support_rows + other_rows if not r_["hours_per_shift"]]
            if _bad:
                QMessageBox.critical(
                    self, "Shift length missing",
                    "The PDMS proposal shows days and a day rate, which need the shift length — the "
                    "hours at the end of the Encoding (for example NS-NAT-OFF-12 = 12 hours). These "
                    "manpower rows have none:\n\n• " + "\n• ".join(_bad[:15])
                    + "\n\nChoose an Encoding for them (or set Match By to the exact stock code) and "
                      "generate again.")
                return


        total_skipped = mp_skipped + eq_skipped + cs_skipped
        if total_skipped:
            log.info(
                "Generate: skipping %d unmatched row(s) (manpower=%d equip=%d cons=%d)",
                total_skipped, mp_skipped, eq_skipped, cs_skipped,
            )

        # CTR header info — shared base, with separate contract numbers per currency
        _base = {
            "client":     self._client_edit.text().strip(),
            "sub_client": self._sub_client_edit.text().strip(),
            "location":   self._location_edit.text().strip(),
            "scope":      self._scope_edit.text().strip(),
            "date":       self._date_edit.text().strip(),
            "revision":   self._revision_edit.text().strip(),
            "comments":   self._comments_edit.text().strip(),
        }
        header_azn = {
            **_base,
            "contract_no":   self._contract_no_azn_edit.text().strip(),
            "project_type":  self._project_type_edit.text().strip(),
            "required_info": list(self._required_info),
        }
        header_usd = {**_base, "contract_no": self._contract_no_usd_edit.text().strip()}
        pdms_job = self._pdms_payload(pdms_tpl) if pdms_on else None

        worker = CTRWorker(
            support_rows  = support_rows,
            other_rows    = other_rows,
            equip_rows    = equip_rows,
            consump_rows  = consump_rows,
            third_party_rows = third_party_rows,
            azn_tpl       = azn_tpl,
            usd_tpl       = usd_tpl,
            output_dir    = output_dir,
            job_ref       = job_ref,
            header_azn    = header_azn,
            header_usd    = header_usd,
            markup_rate   = self._markup_rate(),
            pdms          = pdms_job,
            parent        = self,
        )
        self._prune_workers()
        self._workers.append(worker)
        dlg = stoppable_progress_dialog(
            self, "Generating CTR documents…", "Generating", worker.job)

        def _done(paths: list[str]):
            dlg.close()
            dlg.deleteLater()
            body = "\n".join(f"  • {p}" for p in paths) or "  (nothing was written)"
            note = (
                f"\n\nSkipped {total_skipped} unmatched row(s) — not included "
                f"in the output." if total_skipped else ""
            ) + (
                f"\n\n{incomplete_skipped} row(s) with a missing value were left out "
                "(see the log for which)." if incomplete_skipped else ""
            )
            if worker.stopped:
                QMessageBox.information(
                    self, "Generation stopped",
                    "Stopped after the step that was running — the remaining steps "
                    f"were not run.\n\nSaved so far:\n\n{body}{note}"
                )
                return
            QMessageBox.information(
                self, "Generation complete",
                f"CTR documents saved:\n\n{body}{note}"
            )

        def _err(msg: str):
            dlg.close()
            dlg.deleteLater()
            QMessageBox.critical(self, "Generation failed", msg)

        worker.finished.connect(_done)
        worker.error.connect(_err)
        worker.start()
