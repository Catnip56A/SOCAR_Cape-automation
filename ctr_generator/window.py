"""
ctr_generator/window.py

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
import logging
import re
from datetime import date as _date
from pathlib import Path

import pandas as pd
from ctr_generator import __version__ as _VERSION
from PySide6.QtCore import Qt, QObject, QRunnable, QSettings, QThread, QThreadPool, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QProgressDialog, QPushButton, QScrollArea,
    QSizePolicy, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ctr_generator.aliases import (
    load_aliases, get_alias, set_alias, delete_alias, save_aliases,
)
from ctr_generator.desc_renames import (
    load_desc_renames, get_desc_rename, set_desc_rename, delete_desc_rename,
    save_desc_renames,
)
from ctr_generator.config import CFG
from ctr_generator.presets import load_presets, save_presets, _PRESET_FIELDS
from ctr_generator.builder_azn import (
    activities_label,
    build_azn,
    is_support_manpower,
    strip_support_keyword,
)
from ctr_generator.builder_usd import build_usd
from ctr_generator.parser import (
    parse_azn_pricebook, parse_usd_pricebook, parse_sage,
    parse_ctr_request, parse_equip_names_db,
)
from ctr_generator.pdf_exporter import export_to_pdf

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

# ── unified manpower table column indices ─────────────────────────────────────
# One table does double duty: shows the CTR Request match AND is the direct
# source for CTR generation — there is no separate "add to CTR" copy step.
# Type/Shift/Shift Type together select the correct AZN pricebook rate —
# the stock code encodes all three (e.g. NOV-NAT-OFF-12 = Night, Overtime,
# Offshore); see _decode_azn_stock_code / _match_azn_labor below.
_MP_TYPE      = 0   # "Onshore" / "Offshore" — editable
_MP_SHIFT     = 1   # "Day" / "Night" — editable
_MP_SHIFTTYPE = 2   # "Normal" / "Overtime" / "Rotational" / "Standby" — editable
_MP_DESC      = 3   # description — editable
_MP_MATCH     = 4   # match-by search key (work name, or a literal stock code override) — editable
_MP_MDESC     = 5   # matched pricebook item + stock code — read-only
_MP_NUMEMP    = 6   # num employees — editable
_MP_QTY       = 7   # quantity in hours (working days × hours/shift) — editable
_MP_UOM       = 8   # UOM — always "Hours" for manpower, read-only
_MP_RATE      = 9   # rate AZN — editable (auto-filled from match, overridable)
_MP_TOTAL     = 10  # read-only, computed
_MP_STATUS    = 11  # read-only: "✓ Matched" / "✓ Manual" / "✗ No match — ..."
_MP_NAT       = 12  # nationality — editable

_MANPOWER_HEADERS = [
    "Type", "Shift", "Shift Type", "Description", "Match By", "Matched Item",
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


# ─────────────────────────────────────────────────────────────────────────────
# Worker thread
# ─────────────────────────────────────────────────────────────────────────────

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
        header_azn:    dict | None = None,
        header_usd:    dict | None = None,
        markup_rate:   float | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._support_rows  = support_rows
        self._other_rows    = other_rows
        self._equip_rows    = equip_rows
        self._consump_rows  = consump_rows
        self._azn_tpl       = azn_tpl
        self._usd_tpl       = usd_tpl
        self._output_dir    = output_dir
        self._job_ref       = job_ref
        self._header_azn    = header_azn
        self._header_usd    = header_usd
        self._markup_rate   = markup_rate

    def run(self):
        log.info(
            "CTRWorker: job_ref=%s support=%d other=%d equip=%d consumables=%d output_dir=%s",
            self._job_ref, len(self._support_rows), len(self._other_rows),
            len(self._equip_rows), len(self._consump_rows), self._output_dir,
        )
        try:
            out = Path(self._output_dir)

            self.progress.emit("Writing AZN CTR spreadsheet…")
            azn_xlsx = build_azn(
                self._support_rows, self._other_rows, self._azn_tpl, out, self._job_ref,
                header=self._header_azn,
            )
            log.info("CTRWorker: AZN spreadsheet written: %s", azn_xlsx)

            self.progress.emit("Writing USD CTR spreadsheet…")
            usd_xlsx = build_usd(
                self._equip_rows, self._consump_rows, self._usd_tpl, out, self._job_ref,
                header=self._header_usd, markup_rate=self._markup_rate,
            )
            log.info("CTRWorker: USD spreadsheet written: %s", usd_xlsx)

            self.progress.emit("Exporting AZN PDF via LibreOffice…")
            try:
                azn_pdf = export_to_pdf(azn_xlsx, out)
                azn_pdf_str = str(azn_pdf)
            except RuntimeError as e:
                log.warning("CTRWorker: AZN PDF export skipped: %s", e)
                azn_pdf_str = f"[PDF skipped: {e}]"

            self.progress.emit("Exporting USD PDF via LibreOffice…")
            try:
                usd_pdf = export_to_pdf(usd_xlsx, out)
                usd_pdf_str = str(usd_pdf)
            except RuntimeError as e:
                log.warning("CTRWorker: USD PDF export skipped: %s", e)
                usd_pdf_str = f"[PDF skipped: {e}]"

            log.info("CTRWorker: finished successfully")
            self.finished.emit([str(azn_xlsx), str(usd_xlsx), azn_pdf_str, usd_pdf_str])

        except Exception as exc:
            log.exception("CTRWorker failed")
            self.error.emit(str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Async file-loading workers
# ─────────────────────────────────────────────────────────────────────────────

class _LoadSignals(QObject):
    """Carries the result signal for _LoadTask (QRunnable can't have signals)."""
    result = Signal(str, object)   # (task_name, DataFrame | Exception)


class _LoadTask(QRunnable):
    """Runs one file-parse function on a pool thread, then emits result."""
    def __init__(self, name: str, fn, signals: _LoadSignals):
        super().__init__()
        self._name    = name
        self._fn      = fn
        self._signals = signals
        self.setAutoDelete(True)

    def run(self):
        try:
            self._signals.result.emit(self._name, self._fn())
        except Exception as exc:
            self._signals.result.emit(self._name, exc)


class _CTRRequestWorker(QThread):
    """Parses a CTR Request file off the main thread."""
    finished = Signal(dict)
    error    = Signal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path

    def run(self):
        try:
            self.finished.emit(parse_ctr_request(self._path))
        except Exception as exc:
            self.error.emit(str(exc))


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


def _make_table(headers: list[str], stretch_col: int = 0) -> QTableWidget:
    t = QTableWidget(0, len(headers))
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
    return t


def _ro_item(text: str) -> QTableWidgetItem:
    """Create a read-only, non-editable table item."""
    item = QTableWidgetItem(str(text))
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    return item


_ROW_TINT_ROLE = Qt.UserRole + 1   # stores a row's warning colour key ("red"/"yellow"/None)
_DESC_ANCHOR_ROLE = Qt.UserRole + 2   # stores a row's original (un-renamed) requested Description

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


def _set_row_tint(tbl: QTableWidget, row: int, key: str | None) -> None:
    """Sets a row's warning colour ("red"/"yellow"/None) and repaints it."""
    col0 = tbl.item(row, 0)
    if col0:
        col0.setData(_ROW_TINT_ROLE, key)
    _apply_row_tint(tbl, row)


def _on_table_selection_changed(tbl: QTableWidget) -> None:
    """Re-applies every row's tint so it switches between background (not
    selected) and font colour (selected) as the selection changes."""
    for row in range(tbl.rowCount()):
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
        code_matches = df[df[code_col].astype(str).str.strip().str.lower() == key_norm]
        if not code_matches.empty:
            row = code_matches.iloc[0]
            return str(row[desc_col]), float(row[price_col]), str(row[code_col])

    if desc_col in df.columns:
        desc_matches = df[df[desc_col].astype(str).str.strip().str.lower() == key_norm]
        if not desc_matches.empty:
            row = desc_matches.iloc[0]
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
    hits = sage_df[sage_df["product"].astype(str).str.strip().str.lower() == key_norm]
    if hits.empty:
        return None
    return bool(hits.iloc[0]["non_recharge"])


# AZN labor stock codes encode shift + shift-type in their prefix, e.g.
# "MSU-NAT-OFF-12" = Day/Normal, Offshore; "NOV-NAT-OFF-12" = Night/Overtime,
# Offshore. An optional "GE" prefix (e.g. "GENOV-...") marks a different
# labor category (seen on industrial-cleaning roles) but uses the same
# shift-type suffix scheme, so it's stripped before lookup.
#
# Configurable via the "azn_shift_prefixes" section of template_config.json
# (see ctr_generator/config.py) — add a prefix there, no code change needed,
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
    manually added row), hours is left unconstrained. If no row matches the
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

    candidates = df[df["supplier_desc"].astype(str).str.strip().str.lower() == key_norm]
    if candidates.empty:
        return None

    loc = _normalize_location(location)
    sh  = _normalize_shift(shift)
    st  = _normalize_shift_type(shift_type)

    for _, row in candidates.iterrows():
        decoded = _decode_azn_stock_code(row["stock_code"])
        if decoded is None:
            continue
        c_loc, c_shift, c_shift_type, c_hours = decoded
        if (c_loc == loc and c_shift == sh and c_shift_type == st
                and (hours is None or c_hours == hours)):
            return str(row["supplier_desc"]), float(row["unit_price"]), str(row["stock_code"])

    return None


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


# ─────────────────────────────────────────────────────────────────────────────
# Main dialog
# ─────────────────────────────────────────────────────────────────────────────

class CTRGeneratorWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._azn_df:      pd.DataFrame = pd.DataFrame()
        self._sage_df:     pd.DataFrame = pd.DataFrame()
        self._usd_pb_df:   pd.DataFrame = pd.DataFrame()
        self._names_db_df: pd.DataFrame = pd.DataFrame()
        self._workers:     list = []
        self._aliases:     dict = load_aliases()
        self._desc_renames: dict = load_desc_renames()
        self._presets:     dict = load_presets()

        # Async file-loading state
        self._load_signals = _LoadSignals()
        self._load_signals.result.connect(self._on_file_loaded)
        self._pending_loads: int  = 0
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
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        content = QWidget()
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
        row6, self._combined_db_edit = _file_picker_row(
            "Combined DB (Names + Request)", "Excel (*.xlsx *.xls)")
        gl.addLayout(row4)
        gl.addLayout(row5)
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
        rematch_btn.clicked.connect(self._rematch_all_manpower)
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
        rematch_btn.clicked.connect(self._rematch_all_equip)
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
        rematch_btn.clicked.connect(self._rematch_all_cons)
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
        self._project_type_edit.setToolTip(
            "Onshore or Offshore — sets the AZN CTR's non-support manpower "
            "section header (\"Onshore Activities\" / \"Offshore Activities\")."
        )

        for row in (row_c, row_sc, row_l, row_s, row_d, row_cna, row_cnu, row_rv, row_pt):
            gl.addLayout(row)

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
        self._markup_spin = QDoubleSpinBox()
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
        preset_lbl = QLabel("Presets:")
        pf = preset_lbl.font()
        pf.setBold(True)
        preset_lbl.setFont(pf)
        preset_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
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

    def restore_settings(self) -> None:
        """Load last-used paths from QSettings. Call after the widget is shown."""
        s = self._settings()
        _edits = {
            "azn_template":   self._azn_tpl_edit,
            "usd_template":   self._usd_tpl_edit,
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

    def save_settings(self) -> None:
        """Persist current paths to QSettings. Call from the parent closeEvent."""
        s = self._settings()
        s.setValue("paths/azn_template",  self._azn_tpl_edit.text())
        s.setValue("paths/usd_template",  self._usd_tpl_edit.text())
        s.setValue("paths/azn_pricebook", self._azn_pb_edit.text())
        s.setValue("paths/sage_export",   self._sage_edit.text())
        s.setValue("paths/usd_pricebook", self._usd_pb_edit.text())
        s.setValue("paths/combined_db",   self._combined_db_edit.text())
        s.setValue("paths/output_dir",    self._out_dir_edit.text())

    # ── Presets ───────────────────────────────────────────────────────────────

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
            ("sage",   lambda p=sage:   parse_sage(p)),
            ("usd_pb", lambda p=usd_pb: parse_usd_pricebook(p)),
        ]
        # Optional — only needed to resolve a CTR Request equipment stock
        # code to a USD Pricebook description; without it, equipment falls
        # back to matching "Match By" directly against the USD Pricebook.
        if combined:
            tasks.append(("names_db", lambda p=combined: parse_equip_names_db(p)))

        self._pending_loads += len(tasks)
        self._load_errors.clear()
        self._load_status.clear()
        self._load_files_btn.setEnabled(False)
        self._pricebook_status.setText(f"Loading {len(tasks)} file(s)…")

        log.info(
            "Async loading pricebook files: azn=%s sage=%s usd_pb=%s combined_db=%s",
            azn_pb, sage, usd_pb, combined,
        )
        for name, fn in tasks:
            _LOAD_POOL.start(_LoadTask(name, fn, self._load_signals))

    def _on_file_loaded(self, name: str, result) -> None:
        """Slot (always on main thread via queued connection) for pool-task results."""
        if isinstance(result, Exception):
            is_warning = name == "names_db"
            self._load_errors.append((name, str(result), is_warning))
            log.error("Failed to load %s: %s", name, result)
        else:
            if name == "azn_pb":
                self._azn_df = result
                self._load_status.append(f"AZN Pricebook ({len(result)} rows)")
            elif name == "sage":
                self._sage_df = result
                self._load_status.append(f"SAGE Export ({len(result)} rows)")
            elif name == "usd_pb":
                self._usd_pb_df = result
                self._load_status.append(f"USD Pricebook ({len(result)} rows)")
            elif name == "names_db":
                self._names_db_df = result
                self._load_status.append(f"Equipment Names DB ({len(result)} rows)")

        self._pending_loads -= 1
        if self._pending_loads <= 0:
            self._pending_loads = 0
            self._load_files_btn.setEnabled(True)
            self._finish_file_load()

    def _finish_file_load(self) -> None:
        """Called when all pool tasks in the current batch have completed."""
        _label = {
            "azn_pb":   "AZN Pricebook",
            "sage":     "SAGE Export",
            "usd_pb":   "USD Pricebook",
            "names_db": "Equipment Names DB",
        }
        _fallback = {
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

        self._recalc_azn_totals()
        self._recalc_usd_totals()

        n_mp = len(data.get("manpower_rows", []))
        n_eq = len(data.get("equipment_rows", []))
        n_cs = len(data.get("consumable_rows", []))
        log.info("CTR Request loaded: manpower=%d equipment=%d consumable=%d",
                 n_mp, n_eq, n_cs)
        self._ctr_req_status.setText(
            f"Loaded {n_mp} manpower, {n_eq} equipment, {n_cs} consumable "
            f"request(s) into the tables below. Exact matches on stock code "
            f"or description fill in the rate automatically and mark the "
            f"row \"✓ Matched\". Edit \"Match By\" to fix a row marked "
            f"\"✗ No match\", or load the Pricebook-Based files and click "
            f"\"Re-match All\". CTR Header Info fields in the Generate "
            f"section are pre-filled."
        )

    def _on_ctr_error(self, msg: str) -> None:
        """Slot called on the main thread when CTR Request parsing fails."""
        self._load_ctr_btn.setEnabled(True)
        self._ctr_req_status.setText("Failed to load CTR Request.")
        log.error("Failed to parse CTR Request: %s", msg)
        QMessageBox.critical(self, "CTR Request error", msg)

    # ── Matching against pricebook/SAGE ─────────────────────────────────────────

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
        row_type = encoded_location or ("Onshore" if "(onshore)" in desc.lower() else "Offshore")
        shift_type_raw = (request_item.get("status") or "").strip()
        # A recognized shift-type prefix in ENCODING (e.g. "MSU-NAT-OFF-12")
        # is authoritative over the free-text Shift/Status columns, since
        # requesters sometimes leave those stale/inconsistent with the code
        # they actually intend (see _decode_manpower_encoding_full).
        shift      = encoded_shift or ("Night" if "night" in (request_item.get("shift") or "").lower() else "Day")
        shift_type = encoded_shift_type or (shift_type_raw.title() if shift_type_raw else "Normal")
        try:
            num_emp = float(request_item.get("quantity") or 1)
        except (TypeError, ValueError):
            num_emp = 1.0
        try:
            working_days = float(request_item.get("working_days") or 0)
        except (TypeError, ValueError):
            working_days = 0.0
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
        qty = working_days

        tbl.setItem(r, _MP_TYPE,      QTableWidgetItem(row_type))
        tbl.setItem(r, _MP_SHIFT,     QTableWidgetItem(shift))
        tbl.setItem(r, _MP_SHIFTTYPE, QTableWidgetItem(shift_type))
        tbl.setItem(r, _MP_DESC,      QTableWidgetItem(desc_display))
        tbl.item(r, _MP_DESC).setData(Qt.UserRole, (encoded_hours or "", encoding_ambiguous))
        # Stashed so a later Description edit can learn/re-apply a rename
        # keyed by the true original requested text, not whatever text
        # (possibly already renamed) currently sits in the cell.
        tbl.item(r, _MP_DESC).setData(_DESC_ANCHOR_ROLE, desc)
        tbl.setItem(r, _MP_MATCH,     QTableWidgetItem(default_key))
        tbl.setItem(r, _MP_MDESC,     _ro_item(""))
        tbl.setItem(r, _MP_NUMEMP,    QTableWidgetItem(str(num_emp)))
        tbl.setItem(r, _MP_QTY,       QTableWidgetItem(str(qty)))
        tbl.item(r, _MP_QTY).setData(Qt.UserRole, working_days)
        tbl.setItem(r, _MP_UOM,       _ro_item("Hours"))
        tbl.setItem(r, _MP_RATE,      QTableWidgetItem("0.00"))
        tbl.setItem(r, _MP_TOTAL,     _ro_item("0.00"))
        tbl.setItem(r, _MP_STATUS,    _ro_item("✗ No match — edit Match By or Description"))
        tbl.setItem(r, _MP_NAT,       QTableWidgetItem("NAT"))

        self._rematch_manpower_row(r, learn_alias=False)

    def _add_manpower_row(self):
        tbl = self._req_manpower_tbl
        tbl.blockSignals(True)
        r = tbl.rowCount()
        tbl.insertRow(r)
        tbl.setItem(r, _MP_TYPE,      QTableWidgetItem("Offshore"))
        tbl.setItem(r, _MP_SHIFT,     QTableWidgetItem("Day"))
        tbl.setItem(r, _MP_SHIFTTYPE, QTableWidgetItem("Normal"))
        tbl.setItem(r, _MP_DESC,      QTableWidgetItem(""))
        tbl.setItem(r, _MP_MATCH,     QTableWidgetItem(""))
        tbl.setItem(r, _MP_MDESC,     _ro_item(""))
        tbl.setItem(r, _MP_NUMEMP,    QTableWidgetItem("1"))
        tbl.setItem(r, _MP_QTY,       QTableWidgetItem("0"))
        tbl.setItem(r, _MP_UOM,       _ro_item("Hours"))
        tbl.setItem(r, _MP_RATE,      QTableWidgetItem("0.00"))
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
        if col in (_MP_TYPE, _MP_SHIFT, _MP_SHIFTTYPE, _MP_MATCH):
            self._rematch_manpower_row(row)
        elif col in (_MP_NUMEMP, _MP_QTY, _MP_RATE):
            self._recalc_manpower_row(row)
        if col in (_MP_TYPE, _MP_SHIFT, _MP_SHIFTTYPE, _MP_MATCH, _MP_NUMEMP, _MP_QTY, _MP_RATE):
            self._recalc_azn_totals()

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
        def _val(c):
            item = tbl.item(row, c)
            try:
                return float(item.text()) if item else 0.0
            except ValueError:
                return 0.0
        total = _val(_MP_NUMEMP) * _val(_MP_QTY) * _val(_MP_RATE)
        tbl.blockSignals(True)
        tbl.setItem(row, _MP_TOTAL, _ro_item(f"{total:.2f}"))
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
        match = _match_azn_labor(self._azn_df, strip_support_keyword(key), location, shift,
                                  shift_type, hours, ambiguous)

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
            elif ambiguous:
                status_text = "✗ No match — ENCODING prefix unrecognized/conflicts with Shift+Status; fix request or set Match By to the exact stock code"
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
        tbl.blockSignals(False)
        self._recalc_manpower_row(row)
        _highlight_row(tbl, row, bool(match))

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
        try:
            qty = float(request_item.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1.0
        try:
            days = float(request_item.get("days") or 30)
        except (TypeError, ValueError):
            days = 30.0

        tbl.setItem(r, _EQ_DESC,   QTableWidgetItem(desc_display))
        tbl.setItem(r, _EQ_CODE,   QTableWidgetItem(code))
        tbl.setItem(r, _EQ_MATCH,  QTableWidgetItem(default_key))
        tbl.setItem(r, _EQ_MDESC,  _ro_item(""))
        tbl.setItem(r, _EQ_QTY,    QTableWidgetItem(str(qty)))
        tbl.setItem(r, _EQ_UNIT,   QTableWidgetItem(request_item.get("uom") or "DAY"))
        tbl.setItem(r, _EQ_RATE,   QTableWidgetItem("0.00"))
        tbl.setItem(r, _EQ_DAYS,   QTableWidgetItem(str(days)))
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
        tbl.setItem(r, _EQ_QTY,    QTableWidgetItem("1"))
        tbl.setItem(r, _EQ_UNIT,   QTableWidgetItem("DAY"))
        tbl.setItem(r, _EQ_RATE,   QTableWidgetItem("0.00"))
        tbl.setItem(r, _EQ_DAYS,   QTableWidgetItem("30"))
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
        def _val(c):
            item = tbl.item(row, c)
            try:
                return float(item.text()) if item else 0.0
            except ValueError:
                return 0.0
        total = _val(_EQ_QTY) * _val(_EQ_RATE) * _val(_EQ_DAYS)
        tbl.blockSignals(True)
        tbl.setItem(row, _EQ_TOTAL, _ro_item(f"{total:.2f}"))
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

        tbl.blockSignals(True)
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
        try:
            qty = float(request_item.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1.0

        tbl.setItem(r, _CS_DESC,   QTableWidgetItem(desc_display))
        tbl.setItem(r, _CS_CODE,   QTableWidgetItem(code))
        tbl.setItem(r, _CS_MATCH,  QTableWidgetItem(default_key))
        tbl.setItem(r, _CS_MDESC,  _ro_item(""))
        tbl.setItem(r, _CS_QTY,    QTableWidgetItem(str(qty)))
        tbl.setItem(r, _CS_UNIT,   QTableWidgetItem(request_item.get("uom") or "EA"))
        tbl.setItem(r, _CS_PRICE,  QTableWidgetItem("0.00"))
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
        tbl.setItem(r, _CS_QTY,    QTableWidgetItem("1"))
        tbl.setItem(r, _CS_UNIT,   QTableWidgetItem("EA"))
        tbl.setItem(r, _CS_PRICE,  QTableWidgetItem("0.00"))
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
        def _val(c):
            item = tbl.item(row, c)
            try:
                return float(item.text()) if item else 0.0
            except ValueError:
                return 0.0
        total = _val(_CS_QTY) * _val(_CS_PRICE)
        tbl.blockSignals(True)
        tbl.setItem(row, _CS_TOTAL, _ro_item(f"{total:.2f}"))
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
            tbl.setItem(row, _CS_MDESC, _ro_item(mdesc))
            price_text = "NONRECHARG" if db_non_recharge else f"{price:.2f}"
            tbl.setItem(row, _CS_PRICE, QTableWidgetItem(price_text))
            status = "✓ Matched"
            if mismatch:
                status += "  ⚠ Rechargability mismatch vs. request sheet — using SAGE"
            tbl.setItem(row, _CS_STATUS, _ro_item(status))
        else:
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

        other_label = activities_label(self._project_type_edit.text())
        self._azn_onshore_lbl.setText(f"Project Support:  ₼ {support_total:,.2f}")
        self._azn_offshore_lbl.setText(f"Total {other_label}:  ₼ {other_total:,.2f}")
        self._azn_total_lbl.setText(f"AZN CTR Total:  ₼ {support_total + other_total:,.2f}")

    def _recalc_usd_totals(self):
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

        self._usd_equip_lbl.setText(f"Equipment:  ${equip_total:,.2f}")
        self._usd_cons_lbl.setText(
            f"Consumables (incl. {markup_pct:.2f}% markup):  ${cons_with_markup:,.2f}")
        self._usd_total_lbl.setText(f"USD CTR Total:  ${ctr_total:,.2f}")

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

    def _generate(self):
        errors   = []
        warnings = []
        job_ref    = self._job_ref_edit.text().strip()
        azn_tpl    = self._azn_tpl_edit.text().strip()
        usd_tpl    = self._usd_tpl_edit.text().strip()
        output_dir = self._out_dir_edit.text().strip()

        # ── Required fields ────────────────────────────────────────────────────
        if not job_ref:
            errors.append("Job Ref is required.")
        elif not job_ref.isdigit():
            errors.append("Job Ref must be a number (e.g. 217).")

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

        if not self._client_edit.text().strip():
            warnings.append("Client is empty — the template cell will be left blank.")

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

        if warnings:
            reply = QMessageBox.warning(
                self, "Proceed with warnings?",
                "\n".join(warnings) + "\n\nGenerate anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        log.info("Generate clicked: job_ref=%s output_dir=%s", job_ref, output_dir)

        # Only rows with a "✓" status (matched against pricebook/SAGE, or
        # manually added) are included — "✗ No match" rows are skipped so
        # unmatched requests never silently appear in the output at rate 0.
        support_rows, other_rows = [], []
        mp_skipped = 0
        for r in range(self._req_manpower_tbl.rowCount()):
            status_item = self._req_manpower_tbl.item(r, _MP_STATUS)
            if not (status_item and status_item.text().startswith("✓")):
                mp_skipped += 1
                continue
            def _txt(c, _r=r):
                item = self._req_manpower_tbl.item(_r, c)
                return item.text().strip() if item else ""
            try:
                num_emp = float(_txt(_MP_NUMEMP) or 1)
            except ValueError:
                num_emp = 1.0
            try:
                qty = float(_txt(_MP_QTY) or 0)
            except ValueError:
                qty = 0.0
            try:
                rate = float(_txt(_MP_RATE) or 0)
            except ValueError:
                rate = 0.0
            row_dict = {
                "num_employees": num_emp,
                "description":   _txt(_MP_DESC),
                "quantity":      qty,
                "uom":           _txt(_MP_UOM) or "Hours",
                "rate_azn":      rate,
                "nationality":   _txt(_MP_NAT) or "NAT",
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
            def _etxt(c, _r=r):
                item = self._req_equip_tbl.item(_r, c)
                return item.text().strip() if item else ""
            try:
                qty = float(_etxt(_EQ_QTY) or 1)
            except ValueError:
                qty = 1.0
            rate_txt = _etxt(_EQ_RATE)
            try:
                rate = float(rate_txt or 0)
            except ValueError:
                # Non-numeric — e.g. "NONRECHARG" — passed through as-is so
                # build_usd can print it instead of a misleading 0.00.
                rate = rate_txt
            try:
                days = float(_etxt(_EQ_DAYS) or 30)
            except ValueError:
                days = 30.0
            # Always show the requested description as typed in the CTR
            # Request, not the matched pricebook name — "Matched Item" is
            # only an internal lookup key used to find the rate, and isn't
            # client-facing text.
            equip_rows.append({
                "description":  _etxt(_EQ_DESC),
                "quantity":     qty,
                "unit":         _etxt(_EQ_UNIT) or "DAY",
                "rate_per_day": rate,
                "days":         days,
                "stock_code":   _etxt(_EQ_CODE),
            })

        consump_rows = []
        cs_skipped = 0
        for r in range(self._req_cons_tbl.rowCount()):
            status_item = self._req_cons_tbl.item(r, _CS_STATUS)
            if not (status_item and status_item.text().startswith("✓")):
                cs_skipped += 1
                continue
            def _ctxt(c, _r=r):
                item = self._req_cons_tbl.item(_r, c)
                return item.text().strip() if item else ""
            try:
                qty = float(_ctxt(_CS_QTY) or 1)
            except ValueError:
                qty = 1.0
            price_txt = _ctxt(_CS_PRICE)
            try:
                price = float(price_txt or 0)
            except ValueError:
                # Non-numeric — e.g. "NONRECHARG" — passed through as-is so
                # build_usd can print it instead of a misleading 0.00.
                price = price_txt
            # Same reasoning as equipment above — always show the requested
            # description, not the internal matched-item name.
            consump_rows.append({
                "long_description":  _ctxt(_CS_DESC),
                "local_expect_cost": price,
                "unit_code":         _ctxt(_CS_UNIT) or "EA",
                "product":           _ctxt(_CS_CODE),
                "quantity":          qty,
            })

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
        }
        header_azn = {
            **_base,
            "contract_no":  self._contract_no_azn_edit.text().strip(),
            "project_type": self._project_type_edit.text().strip(),
        }
        header_usd = {**_base, "contract_no": self._contract_no_usd_edit.text().strip()}

        # Progress dialog
        dlg = QProgressDialog("Generating CTR documents…", None, 0, 0, self)
        dlg.setWindowTitle("Generating")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.show()

        worker = CTRWorker(
            support_rows  = support_rows,
            other_rows    = other_rows,
            equip_rows    = equip_rows,
            consump_rows  = consump_rows,
            azn_tpl       = azn_tpl,
            usd_tpl       = usd_tpl,
            output_dir    = output_dir,
            job_ref       = job_ref,
            header_azn    = header_azn,
            header_usd    = header_usd,
            markup_rate   = self._markup_rate(),
            parent        = self,
        )
        self._workers.append(worker)

        def _done(paths: list[str]):
            dlg.close()
            body = "\n".join(f"  • {p}" for p in paths)
            note = (
                f"\n\nSkipped {total_skipped} unmatched row(s) — not included "
                f"in the output." if total_skipped else ""
            )
            QMessageBox.information(
                self, "Generation complete",
                f"CTR documents saved:\n\n{body}{note}"
            )

        def _err(msg: str):
            dlg.close()
            QMessageBox.critical(self, "Generation failed", msg)

        worker.finished.connect(_done)
        worker.error.connect(_err)
        worker.start()
