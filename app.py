"""
app.py — SOCAR Cape MR vs CTR Comparator (PySide6 desktop)

Run:   python app.py
Build: pyinstaller app.spec   (Windows only, see justfile)
"""

import json
import logging
import re
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
from PySide6.QtCore import (
    QAbstractTableModel, QEvent, QModelIndex, QSettings, QSortFilterProxyModel,
    QSize, Qt, QThread, QTimer, Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont, QKeySequence, QPainter, QPalette, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QProgressDialog, QPushButton, QScrollArea,
    QSplitter, QStackedWidget, QStyle, QStyledItemDelegate, QTabWidget,
    QTableView, QToolButton, QVBoxLayout, QWidget,
)

sys.path.insert(0, str(Path(__file__).parent))
from comparator.sheet_parser import is_cover_sheet, parse_workbook
from comparator import doc_filter
from stats import cost_stats
from ctr_tools.stockcode_types import current_types
from stats.stats_view import STOPPED as _STATS_STOPPED, StatsPanel, StatsWorker
from common.settings_view import SettingsDialog, gear_icon
from common.attention import ATTENTION_ROLE, AttentionDelegate
from common.find_bar import install_find
from common.hover_text import install_elided_tooltips
from comparator.comparison_history import (
    comparisons_dir, default_label, list_saved_comparisons, load_comparison,
    save_comparison, suggest_save_path,
)
from ctr_tools import __version__ as _CTR_VERSION
from ctr_tools.window import CTRGeneratorWidget, _file_picker_row, file_stamp, stoppable_progress_dialog
from ctr_tools.tasks import TASKS, Job
from ctr_tools.inspector import InspectorDock
from ctr_tools.tracker_window import CTRTrackerWidget

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Palette
# ─────────────────────────────────────────────────────────────────────────────

MR_COLOR  = "#1565C0"   # blue — used for all MR elements
MR_LIGHT  = "#E3F2FD"
CTR_COLOR = "#00695C"   # teal — used for all CTR elements
CTR_LIGHT = "#E0F2F1"
PRIMARY   = "#1976D2"
MUTED     = "#757575"
BORDER    = "#E0E0E0"


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def normalize_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def find_key_col(columns: list) -> str | None:
    priority = ["stockcode", "equipmentcode", "itemcode", "code",
                "description", "item", "name"]
    for token in priority:
        for col in columns:
            if token in normalize_col(col):
                return col
    return columns[0] if columns else None


def _find_col(df: pd.DataFrame, *candidates: str) -> str | None:
    norm_map = {normalize_col(c): c for c in df.columns}
    for cand in candidates:
        hit = norm_map.get(normalize_col(cand))
        if hit:
            return hit
    return None


def _norm_val(v) -> str:
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "") else s


# ─────────────────────────────────────────────────────────────────────────────
# Rate (CTR) vs Rechargeable (MR) consistency check
# ─────────────────────────────────────────────────────────────────────────────
# Both sides encode "not billable" with the same literal token, "NONRECHARG"
# (MR's Rechargeable column and CTR's Rate column both use it — the CTR just
# writes it into Rate instead of a number). A rechargeable item's CTR Rate is
# a plain number instead. So a mismatch is: MR says RECHARGE but CTR has no
# usable number, or MR says NONRECHARG but CTR has a real rate.

def _rate_implied_status(rate_val) -> str | None:
    """'RECHARGE' if Rate is a usable number, 'NONRECHARG' if it's that
    literal placeholder, None if blank/unrecognized (nothing to compare)."""
    s = _norm_val(rate_val).upper()
    if s == "":
        return None
    if s == "NONRECHARG":
        return "NONRECHARG"
    try:
        float(s)
    except ValueError:
        return None
    return "RECHARGE"


def _find_rate_rechargeable_mismatches(display_df: pd.DataFrame) -> set[tuple[int, str]]:
    """(row, col) cells where MR's Rechargeable flag and CTR's Rate disagree
    about whether the item is billable."""
    cells: set[tuple[int, str]] = set()
    if "Rechargeable" not in display_df.columns or "Rate (CTR)" not in display_df.columns:
        return cells
    # .map() over just the two needed columns instead of .iterrows() over
    # the whole frame — iterrows() reconstructs a full mixed-type row
    # (every column) for every row just to read two values out of it.
    rech = display_df["Rechargeable"].map(lambda v: _norm_val(v).upper())
    rate_status = display_df["Rate (CTR)"].map(_rate_implied_status)
    mismatch = (
        rech.isin(("RECHARGE", "NONRECHARG"))
        & rate_status.notna()
        & (rate_status != rech)
    )
    for row_idx in display_df.index[mismatch]:
        cells.add((row_idx, "Rechargeable"))
        cells.add((row_idx, "Rate (CTR)"))
    return cells


# ─────────────────────────────────────────────────────────────────────────────
# Combined view: per-Stock-Code MR/CTR quantity totals
# ─────────────────────────────────────────────────────────────────────────────

def _aggregate_combined(
    mr: pd.DataFrame, ctr: pd.DataFrame,
    qty_mr: str | None, unit_mr: str | None,
    qty_ctr: str | None, unit_ctr: str | None,
    matched_keys: set,
    desc_mr: str | None = None, desc_ctr: str | None = None,
) -> dict[str, dict]:
    """Group MR and CTR rows by Stock Code (restricted to matched_keys) and
    sum Qty per side. Returns {key: {"mr_qty", "mr_units", "mr_docs",
    "mr_desc", "mr_parts", "ctr_qty", "ctr_units", "ctr_docs", "ctr_desc",
    "ctr_parts"}} (*_parts = [{"doc", "sheet", "qty"}] per document+sheet) — *_units is
    the sorted set of distinct non-blank units contributing to that side's
    total, so more than one entry signals a unit conflict the caller must
    resolve before trusting the sum. *_docs is the sorted set of distinct
    source filenames contributing rows to that side's total (for the
    Combined view's optional "Show Document Names" column, same source as
    the detail tables'). *_desc is the first non-blank Description found
    among that side's contributing rows, so the Combined view can show an
    item name next to the Stock Code the same way the detail view does."""

    def _agg_side(df: pd.DataFrame, qty_col: str | None, unit_col: str | None,
                  desc_col: str | None) -> dict:
        result: dict = {}
        if qty_col is None or qty_col not in df.columns:
            return result
        for key, grp in df.groupby("_KEY_"):
            if key not in matched_keys:
                continue
            qty_numeric = pd.to_numeric(grp[qty_col], errors="coerce")
            qty_total = float(qty_numeric.sum()) if qty_numeric.notna().any() else None
            units = sorted({
                _norm_val(u).upper()
                for u in (grp[unit_col] if unit_col and unit_col in grp.columns else [])
                if _norm_val(u)
            })
            docs = sorted({
                _norm_val(f)
                for f in (grp["_SourceFile"] if "_SourceFile" in grp.columns else [])
                if _norm_val(f)
            })
            desc = ""
            if desc_col and desc_col in grp.columns:
                desc = next((v for v in (_norm_val(d) for d in grp[desc_col]) if v), "")
            # Per (document, sheet) share of the total — drives the Combined
            # view's document filter and its "x of y from selected" tooltips.
            parts = []
            if "_SourceFile" in grp.columns:
                sheets = (grp["_SourceSheet"] if "_SourceSheet" in grp.columns
                          else pd.Series("", index=grp.index))
                tmp = pd.DataFrame({"doc": grp["_SourceFile"].map(_norm_val),
                                    "sheet": sheets.map(_norm_val),
                                    "qty": qty_numeric})
                for (d, sh), g in tmp.groupby(["doc", "sheet"], sort=True):
                    parts.append({"doc": d, "sheet": sh,
                                  "qty": float(g["qty"].sum()) if g["qty"].notna().any() else None})
            result[key] = {"qty": qty_total, "units": units, "docs": docs, "desc": desc,
                           "parts": parts}
        return result

    mr_agg  = _agg_side(mr,  qty_mr,  unit_mr,  desc_mr)
    ctr_agg = _agg_side(ctr, qty_ctr, unit_ctr, desc_ctr)

    raw: dict[str, dict] = {}
    for key in matched_keys:
        m = mr_agg.get(key,  {"qty": None, "units": [], "docs": [], "desc": "", "parts": []})
        c = ctr_agg.get(key, {"qty": None, "units": [], "docs": [], "desc": "", "parts": []})
        raw[key] = {
            "mr_qty": m["qty"],   "mr_units":  m["units"],  "mr_docs":  m["docs"],  "mr_desc":  m["desc"],
            "ctr_qty": c["qty"],  "ctr_units": c["units"],  "ctr_docs": c["docs"],  "ctr_desc": c["desc"],
            "mr_parts": m["parts"], "ctr_parts": c["parts"],
        }
    return raw


# ─────────────────────────────────────────────────────────────────────────────
# Custom widgets
# ─────────────────────────────────────────────────────────────────────────────

class StepIndicator(QWidget):
    """Horizontal numbered step progress bar painted via QPainter."""

    def __init__(self, steps: list[str], parent=None):
        super().__init__(parent)
        self._steps = steps
        self._current = 0
        self.setFixedHeight(62)

    def set_step(self, n: int):
        self._current = max(0, min(n, len(self._steps) - 1))
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        n   = len(self._steps)
        R   = 14
        cy  = 22
        sw  = self.width() / n
        cx  = [int(sw * i + sw / 2) for i in range(n)]

        # Connecting lines
        for i in range(n - 1):
            color = QColor(PRIMARY) if i < self._current else QColor(BORDER)
            p.setPen(QPen(color, 2))
            p.drawLine(cx[i] + R, cy, cx[i + 1] - R, cy)

        # Circles + numbers + labels
        for i, label in enumerate(self._steps):
            done   = i < self._current
            active = i == self._current

            if done or active:
                p.setBrush(QBrush(QColor(PRIMARY)))
                p.setPen(QPen(QColor(PRIMARY), 2))
            else:
                p.setBrush(QBrush(QColor("#F5F5F5")))
                p.setPen(QPen(QColor(BORDER), 2))
            p.drawEllipse(cx[i] - R, cy - R, R * 2, R * 2)

            nf = QFont(); nf.setPointSize(9); nf.setBold(True)
            p.setFont(nf)
            p.setPen(QPen(QColor("white") if (done or active) else QColor(MUTED)))
            p.drawText(cx[i] - R, cy - R, R * 2, R * 2,
                       Qt.AlignmentFlag.AlignCenter, str(i + 1))

            lf = QFont(); lf.setPointSize(8); lf.setBold(active)
            p.setFont(lf)
            p.setPen(QPen(QColor(PRIMARY) if active else QColor(MUTED)))
            p.drawText(cx[i] - 70, cy + R + 6, 140, 18,
                       Qt.AlignmentFlag.AlignCenter, label)

        p.end()


class EmptyState(QWidget):
    """Centered placeholder shown before results exist."""

    def __init__(self, message: str, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(10)

        dash = QLabel("—")
        dash.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = dash.font(); f.setPointSize(30); dash.setFont(f)
        dash.setStyleSheet(f"color: {BORDER};")

        msg = QLabel(message)
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        msg.setMaximumWidth(340)

        lay.addWidget(dash)
        lay.addWidget(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Column-side detection (drives cell / header coloring)
# ─────────────────────────────────────────────────────────────────────────────

def _col_side(col_name: str) -> str:
    """Return 'mr', 'ctr', or '' (neutral) for a display column name."""
    if "(MR)" in col_name or col_name in ("MR Document", "MR Sheet", "Rechargeable", "Allocation"):
        return "mr"
    if "(CTR)" in col_name or col_name in ("CTR Document", "CTR Sheet"):
        return "ctr"
    return ""


# Cell background colours (filled vs empty within MR/CTR columns)
_MR_FILL   = QColor("#DDEEFF")   # light blue — MR cell with data
_MR_EMPTY  = QColor("#F2F7FF")   # very light blue — MR cell, no data
_CTR_FILL  = QColor("#D6F0EA")   # light teal — CTR cell with data
_CTR_EMPTY = QColor("#EFF9F6")   # very light teal — CTR cell, no data




# ─────────────────────────────────────────────────────────────────────────────
# DataFrame → QTableView adapter
# ─────────────────────────────────────────────────────────────────────────────

_MISMATCH_BG = QColor("#FFCDD2")   # light red    — mismatched cell
_MISMATCH_FG = QColor("#B71C1C")   # dark red     — mismatched cell text
_COMBINED_BG = QColor("#FFE0B2")   # light orange — Stock Code spans >1 document (Combined view)
_COMBINED_FG = QColor("#E65100")   # dark orange  — Stock Code spans >1 document text

# Highlight "kind" -> (background, foreground). A cell's highlight kind is
# looked up in this map wherever a colour is actually needed, so adding a
# new kind (e.g. the Combined view's multi-document highlighting) never
# touches the mismatch-detection logic that decides *which* cells get one.
_HIGHLIGHT_COLORS = {
    "mismatch": (_MISMATCH_BG, _MISMATCH_FG),
    "combined": (_COMBINED_BG, _COMBINED_FG),
}
# A Days cell that is missing (equipment is costed × days): painted like a highlight, orange.
_DAYS_NEEDED_COLORS = (QColor("#FFE0B2"), QColor("#E65100"))


class PandasModel(QAbstractTableModel):
    # The user typed days into an editable Days cell: (source row, column name, text).
    days_edited = Signal(int, str, str)

    def __init__(self, df: pd.DataFrame, parent=None):
        super().__init__(parent)
        self._df = df.reset_index(drop=True)
        # data() runs once per cell per role on every paint and column-resize
        # pass; DataFrame.iloc per call is far too slow for that (~50 µs each),
        # so read the values and column names from plain lists made once here.
        self._values = self._df.to_numpy(dtype=object).tolist()
        self._col_names = [str(c) for c in self._df.columns]
        self._highlights: dict[tuple[int, str], str] = {}  # (src_row, col_name) -> kind
        self._tips: dict[tuple[int, str], str] = {}        # (src_row, col_name) -> tooltip
        self._days_needed: set[tuple[int, str]] = set()    # (src_row, col_name) of Days cells waiting for the user
        self._days_editable: set[tuple[int, str]] = set()  # (src_row, col_name) of Days cells the user may edit
        self._waiting: tuple[str, str] | None = None       # (cell text, hover text) while the statistics are not ready

    def set_tooltips(self, tips: dict[tuple[int, str], str]):
        self._tips = tips

    def attention(self, row: int, col_name: str):
        """(background, foreground) of a cell the user must not miss — a highlight kind from
        _HIGHLIGHT_COLORS (red mismatch, orange Combined, and any kind added later) or a missing Days
        cell — else None. Every colour that should stay visible when selected goes through here."""
        kind = self._highlights.get((row, col_name))
        if kind:
            return _HIGHLIGHT_COLORS[kind]
        if (row, col_name) in self._days_needed:
            return _DAYS_NEEDED_COLORS
        return None

    _WAITING_COLUMNS = ("Days (", "Local cost")

    def set_waiting(self, text: str | None, reason: str = ""):
        """While the statistics are not ready the Days / Local cost cells would just be blank: show a waiting
        mark (and say what is awaited on hover) instead — for lines that exist on that side. The mark is
        display only: exports and saved comparisons never contain it."""
        self._waiting = (text, reason) if text else None
        if self.rowCount() > 0 and self.columnCount() > 0:
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, self.columnCount() - 1))

    def _is_waiting_cell(self, row: int, col: int) -> bool:
        name = self._col_names[col]
        if self._waiting is None or not name.startswith(self._WAITING_COLUMNS):
            return False
        v = self._values[row][col]
        if not (v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == ""):
            return False
        for side in ("MR", "CTR"):                                  # a side's cell only waits if that side has a line
            if name.endswith(f"({side})") and f"{side} Document" in self._col_names:
                doc = self._values[row][self._col_names.index(f"{side} Document")]
                return bool(str(doc or "").strip()) and not (isinstance(doc, float) and pd.isna(doc))
        return True

    def set_days_editable(self, cells: set[tuple[int, str]]):
        """The Days cells (equipment lines) the user may edit: double-click, type, Enter."""
        self._days_editable = cells

    def flags(self, index):
        base = super().flags(index)
        if index.isValid() and (index.row(), self._col_names[index.column()]) in self._days_editable:
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        """An edit of a Days cell is only announced (days_edited); the window decides what it means (and asks
        whether to sync the other side), then rebuilds the columns, which is what updates the cell."""
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        col_name = self._col_names[index.column()]
        if (index.row(), col_name) not in self._days_editable:
            return False
        self.days_edited.emit(index.row(), col_name, str(value))
        return True

    def set_days_needed(self, cells: set[tuple[int, str]]):
        """Days cells that are missing (equipment is costed × days) — painted orange, like the Statistics tab."""
        self._days_needed = cells
        if self.rowCount() > 0 and self.columnCount() > 0:
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, self.columnCount() - 1))

    def set_tail_columns(self, base_cols: int, tail: pd.DataFrame):
        """Replace every column after the first `base_cols` with the columns of `tail` (same
        row order). The columns are removed / inserted properly, so a view keeps its sort,
        selection and the columns the user hid."""
        cur = self.columnCount()
        if cur > base_cols:
            self.beginRemoveColumns(QModelIndex(), base_cols, cur - 1)
            self._df = self._df.iloc[:, :base_cols]
            self._values = [row[:base_cols] for row in self._values]
            self._col_names = self._col_names[:base_cols]
            self.endRemoveColumns()
        if len(tail.columns):
            n = len(tail.columns)
            self.beginInsertColumns(QModelIndex(), base_cols, base_cols + n - 1)
            tail = tail.reset_index(drop=True)
            self._df = pd.concat([self._df, tail], axis=1)
            extra = tail.to_numpy(dtype=object).tolist()
            self._values = [row + extra[i] for i, row in enumerate(self._values)]
            self._col_names = self._col_names + [str(c) for c in tail.columns]
            self.endInsertColumns()

    def set_highlights(self, cells: dict[tuple[int, str], str]):
        self._highlights = cells
        if self.rowCount() > 0 and self.columnCount() > 0:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self.rowCount() - 1, self.columnCount() - 1),
            )

    def rowCount(self, parent=QModelIndex()):    return len(self._df)
    def columnCount(self, parent=QModelIndex()): return len(self._df.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        col_name = self._col_names[index.column()]
        val      = self._values[index.row()][index.column()]

        if role == Qt.ItemDataRole.DisplayRole:
            if self._waiting is not None and self._is_waiting_cell(index.row(), index.column()):
                return self._waiting[0]
            if isinstance(val, float) and pd.isna(val):
                return ""
            return str(val) if val is not None else ""

        if role == Qt.ItemDataRole.EditRole:
            return "" if val is None or (isinstance(val, float) and pd.isna(val)) else str(val)

        if role == Qt.ItemDataRole.ToolTipRole:
            if self._waiting is not None and self._is_waiting_cell(index.row(), index.column()):
                return self._waiting[1]
            return self._tips.get((index.row(), col_name))

        # A highlight (mismatch, Combined, missing Days, ...) overrides all other cell colours.
        kind = self._highlights.get((index.row(), col_name))
        if role == Qt.ItemDataRole.UserRole:
            return kind
        att = self.attention(index.row(), col_name)
        if role == ATTENTION_ROLE:
            return True if att else None
        if att:
            if role == Qt.ItemDataRole.BackgroundRole:
                return QBrush(att[0])
            if role == Qt.ItemDataRole.ForegroundRole:
                return QBrush(att[1])

        if role == Qt.ItemDataRole.BackgroundRole:
            side = _col_side(col_name)
            if side:
                is_empty = (val is None
                            or (isinstance(val, float) and pd.isna(val))
                            or str(val).strip() == "")
                if side == "mr":
                    return QBrush(_MR_EMPTY if is_empty else _MR_FILL)
                return QBrush(_CTR_EMPTY if is_empty else _CTR_FILL)

        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            col_name = self._col_names[section]
            if role == Qt.ItemDataRole.DisplayRole:
                return col_name
            if role == Qt.ItemDataRole.ForegroundRole:
                side = _col_side(col_name)
                if side == "mr":
                    return QBrush(QColor(MR_COLOR))
                if side == "ctr":
                    return QBrush(QColor(CTR_COLOR))
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return str(section + 1)
        return None


def _sort_number(text: str) -> float | None:
    try:
        return float(text.strip())
    except (ValueError, AttributeError):
        return None


class NumericSortProxy(QSortFilterProxyModel):
    """Sorts numeric cells as numbers (the model's DisplayRole is text, so a
    plain text sort puts -10 before -2 and -30 before 0.0). Numbers come
    before text, and blanks stay last in both ascending and descending.

    Also hides rows by a per-source-row mask (the document filter)."""

    _mask: list[bool] | None = None

    def set_mask(self, mask: list[bool] | None):
        self._mask = mask
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        return self._mask is None or (source_row < len(self._mask) and self._mask[source_row])

    def lessThan(self, left, right):
        a = left.data(Qt.ItemDataRole.DisplayRole) or ""
        b = right.data(Qt.ItemDataRole.DisplayRole) or ""
        a_blank, b_blank = not str(a).strip(), not str(b).strip()
        if a_blank or b_blank:
            if a_blank and b_blank:
                return False
            # Qt reverses lessThan for descending, so flip to keep blanks last.
            return a_blank == (self.sortOrder() == Qt.SortOrder.DescendingOrder)
        fa, fb = _sort_number(str(a)), _sort_number(str(b))
        if fa is not None and fb is not None:
            return fa < fb
        if fa is not None or fb is not None:       # number vs text
            return (fa is not None) == (self.sortOrder() == Qt.SortOrder.AscendingOrder)
        return str(a).casefold() < str(b).casefold()


class ResultTableDelegate(AttentionDelegate):
    """The results tables' delegate: highlighted cells (red / orange — see PandasModel.attention) keep their
    colours when the row is selected; see common/attention.py."""


def _make_view(compact: bool = False) -> QTableView:
    v = QTableView()
    v.setAlternatingRowColors(True)
    v.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    # Only the Days cells of equipment lines are editable (PandasModel.flags); every other cell ignores this.
    v.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
    v.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    v.horizontalHeader().setStretchLastSection(True)
    v.verticalHeader().setVisible(False)
    v.setSortingEnabled(not compact)
    if not compact:
        # A third click on the same header goes back to the original row order
        # (ascending -> descending -> unsorted).
        v.horizontalHeader().setSortIndicatorClearable(True)
    if compact:
        v.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
    _attach_column_hiding(v)
    install_elided_tooltips(v)      # full text on hover when a cell is cut off
    install_find(v)                 # Ctrl+F
    return v


# ── Visual-only column hiding ─────────────────────────────────────────────────
# Right-click a column header → Hide. Purely a display choice: it never
# touches the DataFrames, the Excel report or saved comparisons, and it is
# not remembered — _load_view resets it whenever a table is (re)loaded.
# Tracked separately from the "Show Document Names" toggle, which owns the
# MR/CTR Document columns (see MainWindow._apply_doc_column_visibility).

def _attach_column_hiding(view: QTableView):
    view._user_hidden = {}          # {column index: column name}
    view._on_hidden_changed = None  # optional callback, set by the owner
    header = view.horizontalHeader()
    header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    header.customContextMenuRequested.connect(
        lambda pos, v=view: _header_menu(v, pos))


def _column_name(view: QTableView, idx: int) -> str:
    return str(view.model().headerData(idx, Qt.Orientation.Horizontal) or idx)


def _notify_hidden_changed(view: QTableView):
    if view._on_hidden_changed:
        view._on_hidden_changed()


def _header_menu(view: QTableView, pos):
    model = view.model()
    if model is None:
        return
    header = view.horizontalHeader()
    idx    = header.logicalIndexAt(pos)
    menu   = QMenu(view)
    if idx >= 0 and not header.isSectionHidden(idx):
        visible = sum(1 for i in range(model.columnCount())
                      if not header.isSectionHidden(i))
        act = menu.addAction(f"Hide column “{_column_name(view, idx)}”")
        act.setEnabled(visible > 1)   # never hide the last visible column
        act.triggered.connect(lambda _=False: _hide_column(view, idx))
    if view._user_hidden:
        act = menu.addAction(f"Unhide columns… ({len(view._user_hidden)})")
        act.triggered.connect(lambda _=False: _unhide_dialog(view))
    if not menu.isEmpty():
        menu.exec(header.mapToGlobal(pos))


def _hide_column(view: QTableView, idx: int):
    view._user_hidden[idx] = _column_name(view, idx)
    view.setColumnHidden(idx, True)
    _notify_hidden_changed(view)


def _unhide_dialog(view: QTableView):
    """Pop-up listing the columns hidden by the user, to pick which to bring back."""
    if not view._user_hidden:
        return
    dlg = QDialog(view)
    dlg.setWindowTitle("Unhide columns")
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel("Tick the columns to show again:"))
    boxes = {}
    for idx, name in sorted(view._user_hidden.items()):
        cb = QCheckBox(name)
        lay.addWidget(cb)
        boxes[idx] = cb
    all_cb = QCheckBox("Select all")
    all_cb.toggled.connect(lambda on: [b.setChecked(on) for b in boxes.values()])
    lay.addWidget(all_cb)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                          | QDialogButtonBox.StandardButton.Cancel)
    bb.button(QDialogButtonBox.StandardButton.Ok).setText("Unhide")
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)
    accepted = dlg.exec() == QDialog.DialogCode.Accepted
    dlg.deleteLater()
    if not accepted:
        return
    for idx, cb in boxes.items():
        if cb.isChecked():
            view.setColumnHidden(idx, False)
            view._user_hidden.pop(idx, None)
    _notify_hidden_changed(view)


def _load_view(view: QTableView, df: pd.DataFrame):
    # Hidden columns are deliberately not remembered across loads.
    for idx in list(getattr(view, "_user_hidden", {})):
        view.setColumnHidden(idx, False)
    if getattr(view, "_user_hidden", None):
        view._user_hidden = {}
    proxy = NumericSortProxy()
    proxy.setSourceModel(PandasModel(df))
    proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    view.setModel(proxy)
    view.resizeColumnsToContents()
    _notify_hidden_changed(view)


def _show_result(stack: QStackedWidget, view: QTableView, df: pd.DataFrame):
    """Loads df into view and switches stack to the table (or the empty
    state if df has nothing in it). Shared by a fresh Compare and by
    reopening a saved comparison — both populate the same result stacks."""
    if df.empty:
        stack.setCurrentIndex(0)
    else:
        _load_view(view, df)
        stack.setCurrentIndex(1)


def _result_stack(empty_msg: str) -> tuple[QStackedWidget, QTableView]:
    """Returns (stack, view). Stack index 0 = empty state, 1 = table."""
    stack = QStackedWidget()
    stack.addWidget(EmptyState(empty_msg))
    view = _make_view()
    view.setItemDelegate(ResultTableDelegate(view))
    stack.addWidget(view)
    return stack, view


def _review_stack(empty_msg: str) -> tuple[QStackedWidget, QListWidget]:
    """Like _result_stack, but a QListWidget of custom row widgets instead
    of a table — each row needs live Approve/Reject buttons, which a
    QTableView cell can't host without a lot more machinery."""
    stack = QStackedWidget()
    stack.addWidget(EmptyState(empty_msg))
    lw = QListWidget()
    lw.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    stack.addWidget(lw)
    return stack, lw


class ComparisonPickerDialog(QDialog):
    """Lists every saved comparison (newest first) so the user picks one by
    name instead of having to browse the filesystem for it. selected_path
    is set on accept(); None means the dialog was cancelled."""

    def __init__(self, entries: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Load Comparison")
        self.resize(560, 400)
        self.selected_path: Path | None = None
        self._entries = entries

        lay = QVBoxLayout(self)
        self._count_lbl = QLabel(f"{len(entries)} saved comparison{'s' if len(entries) != 1 else ''}:")
        lay.addWidget(self._count_lbl)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by name, timestamp, or file…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        lay.addWidget(self._search)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        for entry in entries:
            mr_names  = ", ".join(Path(f).name for f in entry["mr_files"])  or "—"
            ctr_names = ", ".join(Path(f).name for f in entry["ctr_files"]) or "—"
            item = QListWidgetItem(f"{entry['label']}\n{entry['timestamp'] or 'unknown time'}")
            item.setToolTip(f"MR:  {mr_names}\nCTR: {ctr_names}\n\n{entry['path']}")
            item.setData(Qt.ItemDataRole.UserRole, entry["path"])
            search_blob = " ".join([
                entry["label"], entry["timestamp"] or "", mr_names, ctr_names,
            ]).lower()
            item.setData(Qt.ItemDataRole.UserRole + 1, search_blob)
            self._list.addItem(item)
        self._list.itemDoubleClicked.connect(self._accept_current)
        lay.addWidget(self._list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        self._open_btn = buttons.button(QDialogButtonBox.StandardButton.Open)
        self._open_btn.setEnabled(False)
        buttons.accepted.connect(self._accept_current)
        buttons.rejected.connect(self.reject)
        self._list.itemSelectionChanged.connect(
            lambda: self._open_btn.setEnabled(bool(self._list.selectedItems()))
        )
        lay.addWidget(buttons)

        if entries:
            self._list.setCurrentRow(0)
        self._search.setFocus()

    def _apply_filter(self, text: str):
        needle = text.strip().lower()
        first_visible = None
        for i in range(self._list.count()):
            item = self._list.item(i)
            blob = item.data(Qt.ItemDataRole.UserRole + 1) or ""
            match = needle in blob
            item.setHidden(not match)
            if match and first_visible is None:
                first_visible = item

        selected = self._list.selectedItems()
        if not selected or selected[0].isHidden():
            self._list.clearSelection()
            if first_visible is not None:
                self._list.setCurrentItem(first_visible)
        self._open_btn.setEnabled(bool(self._list.selectedItems()))

    def _accept_current(self):
        items = self._list.selectedItems()
        if not items:
            return
        self.selected_path = items[0].data(Qt.ItemDataRole.UserRole)
        self.accept()


# ─────────────────────────────────────────────────────────────────────────────
# Style helpers
# ─────────────────────────────────────────────────────────────────────────────

def _group_css(color: str) -> str:
    return f"""
        QGroupBox {{
            font-weight: bold;
            color: {color};
            border: 1.5px solid {color};
            border-radius: 6px;
            margin-top: 10px;
            padding-top: 4px;
            background: white;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
            background: white;
        }}
        QHeaderView::section {{
            background: #F2F2F2;
            color: #333333;
            border: none;
            border-bottom: 1px solid #CCCCCC;
            border-right: 1px solid #CCCCCC;
            padding: 3px 6px;
        }}
        QHeaderView::section:last {{
            border-right: none;
        }}
    """


def _metric_box(label: str, color: str,
                tooltip: str) -> tuple[QGroupBox, QLabel]:
    box = QGroupBox(label)
    box.setToolTip(tooltip)
    box.setStyleSheet(f"""
        QGroupBox {{
            font-size: 11px; color: {MUTED};
            border: 1px solid {BORDER}; border-radius: 6px;
            margin-top: 10px; background: white;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin; left: 8px;
            padding: 0 4px; background: white;
        }}
    """)
    lbl = QLabel("—")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    f = lbl.font(); f.setPointSize(15); f.setBold(True); lbl.setFont(f)
    lbl.setStyleSheet(f"color: {color};")
    bl = QVBoxLayout(box)
    bl.setContentsMargins(6, 2, 6, 2)
    bl.setSpacing(0)
    bl.addWidget(lbl)
    return box, lbl


# ─────────────────────────────────────────────────────────────────────────────
# Background parse worker
# ─────────────────────────────────────────────────────────────────────────────

class ParseWorker(QThread):
    finished = Signal(list)
    error    = Signal(str)

    def __init__(self, payloads: list[tuple[str, bytes]], parent=None,
                 include_cover: bool = False):
        super().__init__(parent)
        self._payloads = payloads
        self._include_cover = include_cover   # MR side: also parse the Socar-Cape sheet
        # One step per file, shown in the F12 Inspector. A stop request is
        # only honoured between files, so a file that is being parsed always
        # finishes.
        self.job: Job = TASKS.begin_job(
            f"Parse {'MR' if include_cover else 'CTR'} files",
            [name for name, _data in payloads],
        )
        self.stopped_after: int | None = None   # files parsed when stopped early

    def run(self):
        payloads, self._payloads = self._payloads, None   # the worker must not keep the raw files
        log.info("ParseWorker: parsing %d file(s)", len(payloads))
        job, i = self.job, 0
        try:
            results = []
            for i, (name, data) in enumerate(payloads):
                if job.should_stop():
                    job.skip_remaining(i)
                    self.stopped_after = i
                    log.info("ParseWorker: stopped on request after %d of %d file(s)",
                             i, len(payloads))
                    break
                job.start(i)
                results.extend(parse_workbook(BytesIO(data), filename=name,
                                              include_cover=self._include_cover))
                payloads[i] = (name, b"")                 # this file's bytes are done with
                job.done(i)
            log.info("ParseWorker: produced %d table(s)", len(results))
            job.finish()
            self.finished.emit(results)
        except Exception as exc:
            log.exception("ParseWorker failed")
            job.failed(i, str(exc))
            job.skip_remaining(i + 1, "not run")
            job.finish()
            self.error.emit(str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class StampWorker(QThread):
    """Reads the modified time / size of the added files off the UI thread: a network share or an offline
    OneDrive folder can make a plain stat() block for seconds each time the window gets focus."""
    done = Signal(dict)

    def __init__(self, paths: list[str], parent=None):
        super().__init__(parent)
        self._paths = paths

    def run(self):
        self.done.emit({p: file_stamp(p) for p in self._paths})


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"SOCAR Cape automation  v{_CTR_VERSION}")
        self.resize(1440, 920)

        self._mr_tables:  list = []
        self._ctr_tables: list = []
        self._mr_df  = pd.DataFrame()
        self._ctr_df = pd.DataFrame()
        self._display_df = pd.DataFrame()
        self._omr_dl     = pd.DataFrame()
        self._octr_dl    = pd.DataFrame()
        self._workers:   list = []
        self._mismatch_rows: list[int] = []   # source-model rows with any mismatch
        self._mismatch_pos:  int       = -1   # current navigation position
        self._mismatch_kinds: dict[int, set[str]] = {}   # source row -> {"red", "orange"} (what highlights it)
        self._rate_rech_mismatch_cells: set[tuple[int, str]] = set()

        # Combined view: per-Stock-Code MR/CTR qty totals for matched keys.
        self._combined_raw:      dict[str, dict] = {}   # key -> {mr_qty, mr_units, ctr_qty, ctr_units}
        self._combined_decisions: dict[str, bool] = {}  # key -> True (approved) / False (rejected)
        self._combined_df        = pd.DataFrame()
        self._combined_review_df = pd.DataFrame()
        self._combined_error_df  = pd.DataFrame()
        # Document filter (view-only; downloads and saves keep every row).
        self._doc_filter_tree = None            # parsed expression, None = off
        # Cost statistics (MA131): rows of the last Compare, the running
        # worker's token (an older worker's result is ignored) and the threads.
        self._stats_items: pd.DataFrame | None = None
        self._restored_days: dict[str, float] = {}      # days saved with a reopened comparison
        # (side, file name) -> (full path, fingerprint when it was read): to notice a file edited after it was added
        self._file_origin: dict[tuple[str, str], tuple[str, tuple | None]] = {}
        self._pending_stats: pd.DataFrame | None = None   # handed from _run_compare to the present step
        self._pending_saved_stats: dict | None = None     # same, from a loaded comparison
        # (side, key, doc, sheet) of every row, for the document filter — the
        # Compare's own rows, or the saved ones of a reopened comparison.
        self._stats_pairs: pd.DataFrame | None = None
        # Rows with no numeric quantity: listed in Needs Review, left out of
        # the cost statistics.
        self._missing_qty_df = pd.DataFrame(columns=cost_stats.MISSING_QTY_COLUMNS)
        # Stock Code -> Equipment / Consumable, from the CTR sections of the
        # last Compare. None = unknown (e.g. a reopened saved comparison).
        self._category_by_key: dict[str, str] | None = None
        self._stats_token = 0
        self._stats_workers: list[StatsWorker] = []
        self._matched_mask: list[bool] | None = None   # mask of whatever the Matched tab shows
        self._filter_pairs: list[tuple[str, str, str]] = []   # (side, document, sheet) for suggestions
        self._filter_has_sheets = True          # False for comparisons saved before sheets were recorded

        # Source file names behind the current results — for labelling a
        # saved comparison. Populated by a fresh Compare or by loading one.
        self._mr_files_used:  list[str] = []
        self._ctr_files_used: list[str] = []

        # Path of the saved comparison currently open, if any — lets Save
        # offer "overwrite this one" instead of always Save As. Cleared by
        # Clear (either side) or by starting fresh with new uploads.
        self._loaded_comparison_path: Path | None = None

        # Splitter state remembered across a Hide-results / Show-results
        # round trip, so Show restores exactly what was on screen before
        # Hide — including whether Maximize was active — instead of always
        # resetting to the default 30/70 split.
        self._pre_hide_sizes:     list[int] | None = None
        self._pre_hide_maximized: bool             = False

        self._build_ui()
        self._build_inspector()

        s = QSettings("SOCAR", "CTRGenerator")
        geom = s.value("window/geometry")
        if geom:
            self.restoreGeometry(geom)
        # Where the Inspector was left (side, size, floating or docked) —
        # but it always starts closed; F12 opens it.
        dock_state = s.value("inspector/state")
        if dock_state:
            self.restoreState(dock_state, 2)   # v2: older saves had Qt-floating docks
        if self._inspector.isFloating():
            self._inspector.setFloating(False)
        self._inspector.hide()
        self._inspector.restore(s)

    def _build_inspector(self):
        """F12 toggles the read-only Inspector dock (see ctr_tools/inspector.py)."""
        self._inspector = InspectorDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._inspector)
        self.resizeDocks([self._inspector], [640], Qt.Orientation.Horizontal)
        # Application-wide, so F12 also works while the inspector is floating
        # and has the focus.
        self._inspector_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F12), self)
        self._inspector_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._inspector_shortcut.activated.connect(self._inspector.toggle)

    def closeEvent(self, event):
        s = QSettings("SOCAR", "CTRGenerator")
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("inspector/state", self.saveState(2))
        self._inspector.save(s)
        self._ctr_gen.save_settings()
        self._ctr_tracker.save_settings()
        for w in list(self._stats_workers):
            w.wait(3000)          # a QThread must not be destroyed while running
        super().closeEvent(event)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(12, 8, 12, 4)
        rl.setSpacing(6)

        # Main tab widget
        self._main_tabs = QTabWidget()
        self._main_tabs.setStyleSheet(f"""
            QTabBar::tab {{
                padding: 7px 24px; min-width: 150px; font-size: 12px;
            }}
            QTabBar::tab:selected {{
                font-weight: bold; color: {PRIMARY};
            }}
        """)
        rl.addWidget(self._main_tabs)

        # ── Tab 0: MR vs CTR Comparator ────────────────────────────────────
        _comp = QWidget()
        _crl  = QVBoxLayout(_comp)
        _crl.setContentsMargins(0, 6, 0, 0)
        _crl.setSpacing(6)
        self._main_tabs.addTab(_comp, "MR vs CTR Comparator")

        # Step indicator
        self._step_bar = StepIndicator(
            ["Upload Files", "Select Tables", "Compare & Review"]
        )
        _crl.addWidget(self._step_bar)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {BORDER};")
        _crl.addWidget(sep)
        self._step_sep = sep

        # ── Pinned action row: hint + Compare + Load Comparison ────────────
        # Always visible, regardless of how long the MR/CTR file lists below
        # get (or how the splitter/results panel is sized) — previously this
        # lived inside the scrollable controls pane and could get pushed
        # below the visible viewport after loading a comparison with many
        # files, with no obvious way to scroll back to it.
        pinned = QWidget()
        pinned_l = QVBoxLayout(pinned)
        pinned_l.setContentsMargins(2, 4, 2, 0)
        pinned_l.setSpacing(6)
        _crl.addWidget(pinned)
        self._pinned_row = pinned

        self._compare_hint = QLabel(
            "Upload at least one MR and one CTR file to continue."
        )
        self._compare_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._compare_hint.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        pinned_l.addWidget(self._compare_hint)

        self._compare_btn = QPushButton("Compare")
        self._compare_btn.setFixedHeight(44)
        self._compare_btn.setEnabled(False)
        self._compare_btn.setToolTip(
            "Match MR and CTR rows by the selected join key.\n"
            "Shows matched items, and items that appear on one side only."
        )
        self._compare_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {PRIMARY};
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover   {{ background-color: #1565C0; }}
            QPushButton:pressed {{ background-color: #0D47A1; }}
            QPushButton:disabled {{
                background-color: {BORDER};
                color: #9E9E9E;
            }}
        """)
        self._compare_btn.clicked.connect(lambda _=False: self._run_compare())

        # Alternative to uploading + comparing: reopen a past comparison
        # directly. Lives here (not with Download/Save below) so it works
        # even before anything's been uploaded.
        or_lbl = QLabel("or")
        or_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        or_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")

        self._load_btn = QPushButton("Load Comparison…")
        self._load_btn.setFixedHeight(44)      # same height as Compare: they share a line
        self._load_btn.setToolTip(
            "Reopen a previously saved comparison exactly as it was left, "
            "without needing the original MR/CTR files.\n"
            "Replaces anything currently uploaded or compared."
        )
        self._load_btn.setStyleSheet(f"""
            QPushButton {{
                background: white;
                color: {PRIMARY};
                border: 1.5px solid {PRIMARY};
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background: {MR_LIGHT}; }}
        """)
        self._load_btn.clicked.connect(self._load_comparison)
        # Compare and Load Comparison share one line, with the SAGE Export
        # field on the line below. The window's minimum height matters here:
        # a taller top area made it taller than a maximized WSLg window
        # (Wayland protocol error, app closed) — sharing the line pays for
        # the extra SAGE line. The field lives here, not in the results panel
        # (hidden until the first Compare), because the path is set before it.
        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        action_row.addWidget(self._compare_btn, 1)
        action_row.addWidget(or_lbl)
        action_row.addWidget(self._load_btn, 1)        # same width and text size as Compare
        pinned_l.addLayout(action_row)
        pinned_l.addSpacing(8)                          # a little air before the SAGE line
        pinned_l.addLayout(self._build_sage_row())
        pinned_l.addLayout(self._build_pricebook_row())

        pinned_sep = QFrame()
        pinned_sep.setFrameShape(QFrame.Shape.HLine)
        pinned_sep.setStyleSheet(f"color: {BORDER};")
        pinned_l.addWidget(pinned_sep)

        # Main vertical splitter
        self._splitter = QSplitter(Qt.Orientation.Vertical)
        _crl.addWidget(self._splitter)

        # ── Top: scrollable controls ───────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        ctrl = QWidget()
        # A plain QWidget doesn't autofill its background by default, so
        # this achieves the same "let the scroll area's own background
        # show through" effect without a per-widget stylesheet — applying
        # ANY QSS to a widget (even just this) switches Qt to its CSS
        # engine for that whole subtree, which is what was producing a
        # black background on tooltips from every widget inside here
        # (Step 1/Step 2's file lists, table lists, previews) while
        # tooltips outside this container (Compare, Load Comparison…)
        # were unaffected.
        ctrl.setAutoFillBackground(False)
        cl = QVBoxLayout(ctrl)
        cl.setSpacing(10)
        cl.setContentsMargins(2, 4, 2, 4)
        scroll.setWidget(ctrl)
        self._splitter.addWidget(scroll)
        self._controls_scroll = scroll

        # Shown only while an added MR / CTR file has changed on disk (or is gone): re-reads those files.
        self._reload_btn = QPushButton()
        self._reload_btn.setVisible(False)
        self._reload_btn.setStyleSheet("QPushButton { color: #E65100; font-weight: bold; }")
        self._reload_btn.clicked.connect(lambda: self._reload_changed_files())
        cl.addWidget(self._reload_btn)
        self._reload_pending = 0
        self._reload_notes: list[str] = []

        # 2-column area: MR (left) | CTR (right)
        two_col = QHBoxLayout()
        two_col.setSpacing(14)
        cl.addLayout(two_col)

        self._mr_file_list,  self._mr_table_list,  \
        self._mr_preview = self._add_side_column(
            two_col, "mr", MR_COLOR,
            file_tip  = "Select one or more MR Excel files (.xlsx / .xlsm).",
            table_tip = "Check the sheets to include.\nUncheck any you want to exclude.",
        )
        self._ctr_file_list, self._ctr_table_list, \
        self._ctr_preview = self._add_side_column(
            two_col, "ctr", CTR_COLOR,
            file_tip  = "Select one or more CTR Excel files (.xlsx / .xlsm).",
            table_tip = "Check the sheets to include.\nUncheck any you want to exclude.",
        )

        # ── Bottom: results (hidden until first compare) ───────────────────
        # A scroll area, so the results pane's own minimum height (its metrics, filter row,
        # tabs and export buttons: ~320 px) no longer sets the whole window's minimum height —
        # with it, the window could not shrink below ~690 px after Compare and did not fit a
        # 1080p screen at 150 % scaling (MA164). Small windows scroll the results instead.
        # Everything that shows/hides the results (`_results_w.setVisible`, the splitter
        # sizes) still talks to this widget unchanged.
        self._results_w = QScrollArea()
        self._results_w.setWidgetResizable(True)
        self._results_w.setFrameShape(QFrame.Shape.NoFrame)
        _results_inner = QWidget()
        _results_inner.setAutoFillBackground(False)       # no per-widget QSS (see the note above)
        res_l = QVBoxLayout(_results_inner)
        res_l.setContentsMargins(0, 4, 0, 0)
        res_l.setSpacing(6)
        self._results_w.setWidget(_results_inner)
        self._splitter.addWidget(self._results_w)
        self._results_w.setVisible(False)

        # Results header bar
        res_header = QHBoxLayout()
        res_title = QLabel("Results")
        rf = res_title.font(); rf.setBold(True); rf.setPointSize(11)
        res_title.setFont(rf)
        res_title.setStyleSheet(f"color: {MUTED};")

        self._maximize_btn = QPushButton("⛶  Maximize")
        self._maximize_btn.setCheckable(True)
        self._maximize_btn.setFlat(True)
        self._maximize_btn.setToolTip(
            "Expand the results panel to take up most of the window.")
        self._maximize_btn.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._maximize_btn.toggled.connect(self._toggle_maximize_results)

        self._collapse_btn = QPushButton("▲  Hide results")
        self._collapse_btn.setCheckable(True)
        self._collapse_btn.setFlat(True)
        self._collapse_btn.setToolTip(
            "Hide the results panel without losing them — click again to "
            "bring them back, no need to re-run Compare.")
        self._collapse_btn.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._collapse_btn.toggled.connect(self._toggle_results_visibility)

        res_header.addWidget(res_title)
        res_header.addStretch()
        res_header.addWidget(self._maximize_btn)
        res_header.addWidget(self._collapse_btn)
        res_l.addLayout(res_header)

        # Everything below the header lives in its own widget so "Hide
        # results" can hide just this — the header (and its Show-results
        # button) stays visible, otherwise there'd be no way back without
        # re-running Compare.
        self._results_body_w = QWidget()
        body_l = QVBoxLayout(self._results_body_w)
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.setSpacing(6)
        res_l.addWidget(self._results_body_w)

        hline = QFrame()
        hline.setFrameShape(QFrame.Shape.HLine)
        hline.setStyleSheet(f"color: {BORDER};")
        body_l.addWidget(hline)

        # Metrics (hidden until first compare)
        self._metrics_w = QWidget()
        mrow = QHBoxLayout(self._metrics_w)
        mrow.setContentsMargins(0, 0, 0, 0)
        mrow.setSpacing(8)
        b, self._m_matched  = _metric_box(
            "Matched keys", PRIMARY,
            "Items found in both MR and CTR, matched by the join key.")
        self._mismatch_lbl = QLabel()
        self._mismatch_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mismatch_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._mismatch_lbl.setVisible(False)
        b.layout().addWidget(self._mismatch_lbl)
        mrow.addWidget(b)
        b, self._m_only_mr  = _metric_box(
            "Only in MR", MR_COLOR,
            "Items present in MR but not found in any CTR document.")
        mrow.addWidget(b)
        b, self._m_only_ctr = _metric_box(
            "Only in CTR", CTR_COLOR,
            "Items present in CTR but not found in any MR document.")
        mrow.addWidget(b)
        self._metrics_w.setVisible(False)
        body_l.addWidget(self._metrics_w)

        # Result tabs with empty states
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(f"""
            QTabBar::tab {{
                padding: 6px 20px;
                min-width: 110px;
            }}
            QTabBar::tab:selected {{
                font-weight: bold;
                color: {PRIMARY};
            }}
        """)
        self._stack_matched,  self._tab_matched  = _result_stack(
            "Run Compare to see matched items.")
        self._stack_only_mr,  self._tab_only_mr  = _result_stack(
            "Run Compare to see items that appear only in MR.")
        self._stack_only_ctr, self._tab_only_ctr = _result_stack(
            "Run Compare to see items that appear only in CTR.")
        self._stack_review,  self._review_list   = _review_stack(
            "Stock codes whose MR/CTR rows disagree on Unit (turn on Combined View) "
            "and rows with no quantity will appear here.")
        self._stack_error,   self._tab_error     = _result_stack(
            "Rejected unit-conflict entries will appear here.")

        self._tabs.addTab(self._stack_matched,  "Matched")
        self._tabs.addTab(self._stack_only_mr,  "Only in MR")
        self._tabs.addTab(self._stack_only_ctr, "Only in CTR")
        self._tabs.addTab(self._stack_review,   "Needs Review")
        self._tabs.addTab(self._stack_error,    "Error Data")
        self._stats_panel = StatsPanel(use_stock_types=True, show_scaffold=False)
        self._stats_panel.reload_requested.connect(self._on_stats_reload_clicked)
        # In a scroll area so this tab page can be squeezed like the table tabs:
        # a tab page's minimum height counts toward the window's, and the bare
        # panel (banner + toolbar + tree) was 55 px taller than the others —
        # enough to push the window past a maximized WSLg screen (Wayland
        # "buffer does not match the configured maximized state", app closed).
        _stats_scroll = QScrollArea()
        _stats_scroll.setWidgetResizable(True)
        _stats_scroll.setFrameShape(QFrame.Shape.NoFrame)
        _stats_scroll.setWidget(self._stats_panel)
        self._stats_tab = _stats_scroll
        self._tabs.addTab(_stats_scroll,        "Statistics")

        # Corner toolbar: navigate between mismatches + toggle highlight
        _corner = QWidget()
        _cl = QHBoxLayout(_corner)
        _cl.setContentsMargins(0, 0, 0, 0)
        _cl.setSpacing(3)

        nav_css = f"""
            QPushButton {{
                background: white; border: 1px solid {BORDER};
                border-radius: 4px; font-size: 13px;
                min-width: 26px; min-height: 24px; max-width: 26px; max-height: 24px;
            }}
            QPushButton:hover    {{ border-color: {PRIMARY}; color: {PRIMARY}; }}
            QPushButton:disabled {{ color: {BORDER}; }}
        """
        # Tooltips are set by _set_compare_vals_mode() below, since they
        # depend on whether Compare Values or Show Combined is active.
        self._prev_mm_btn = QPushButton("↑")
        self._prev_mm_btn.setStyleSheet(nav_css)
        self._prev_mm_btn.setEnabled(False)
        self._prev_mm_btn.clicked.connect(lambda: self._nav_mismatch(-1))

        self._next_mm_btn = QPushButton("↓")
        self._next_mm_btn.setStyleSheet(nav_css)
        self._next_mm_btn.setEnabled(False)
        self._next_mm_btn.clicked.connect(lambda: self._nav_mismatch(+1))

        # Which highlighted rows the arrows step through. Shown only in Combined View
        # when both kinds exist (red: values differ, orange: several documents).
        self._mm_kind_combo = QComboBox()
        self._mm_kind_combo.addItem("All", "all")
        self._mm_kind_combo.addItem("Red", "red")
        self._mm_kind_combo.addItem("Orange", "orange")
        self._mm_kind_combo.setToolTip(
            "Which highlighted rows the ↑ ↓ arrows step through:\n"
            "All, Red only (Qty / Unit differ between MR and CTR),\n"
            "or Orange only (Stock Code built from several documents).")
        self._mm_kind_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._mm_kind_combo.setVisible(False)
        self._mm_kind_combo.currentIndexChanged.connect(lambda _=0: self._nav_mismatch(0, absolute=True))

        self._compare_vals_btn = QPushButton()
        self._compare_vals_btn.setCheckable(True)
        self._compare_vals_btn.setEnabled(False)
        self._set_compare_vals_mode(combined=False)   # starts out as "Compare Values"
        self._compare_vals_btn.toggled.connect(self._apply_value_highlights)

        # Combined-view-only value check (Qty totals / Unit). Separate from
        # _compare_vals_btn, which is "Show Combined" while Combined is on.
        self._combined_cmp_btn = QPushButton("Compare Values")
        self._combined_cmp_btn.setCheckable(True)
        self._combined_cmp_btn.setEnabled(False)
        self._combined_cmp_btn.setVisible(False)
        self._combined_cmp_btn.setToolTip(
            "Highlight Stock Codes where the MR and CTR Qty totals differ, or\n"
            "where MR and CTR use different Units, in red.\n"
            "Use ↑ ↓ to jump between them. Click again to clear.")
        self._combined_cmp_btn.setStyleSheet(f"""
            QPushButton {{
                background: white; color: {PRIMARY};
                border: 1px solid {PRIMARY}; border-radius: 4px;
                padding: 2px 10px; font-size: 11px;
                min-height: 24px;
            }}
            QPushButton:checked {{
                background: #FFEBEE; color: #C62828;
                border: 1.5px solid #C62828; font-weight: bold;
            }}
            QPushButton:disabled {{ color: {BORDER}; border-color: {BORDER}; }}
        """)
        self._combined_cmp_btn.toggled.connect(
            lambda _=False: self._apply_value_highlights(self._compare_vals_btn.isChecked()))

        self._combined_btn = QPushButton("Combined View")
        self._combined_btn.setCheckable(True)
        self._combined_btn.setEnabled(False)
        self._combined_btn.setToolTip(
            "Show one row per Stock Code, with MR and CTR Qty summed across "
            "every matched row and a Diff column.\n"
            "Stock codes whose contributing rows disagree on Unit are held "
            "out — resolve them in the 'Needs Review' tab."
        )
        self._combined_btn.setStyleSheet(f"""
            QPushButton {{
                background: white; color: #E65100;
                border: 1px solid #E65100; border-radius: 4px;
                padding: 2px 10px; font-size: 11px;
                min-height: 24px;
            }}
            QPushButton:checked {{
                background: #FFF3E0; color: #E65100;
                border: 1.5px solid #E65100; font-weight: bold;
            }}
            QPushButton:disabled {{ color: {BORDER}; border-color: {BORDER}; }}
        """)
        self._combined_btn.toggled.connect(self._apply_combined_view)

        # Local cost (SAGE's local_expect_cost) at the end of Matched / Only in MR / Only in CTR:
        # the unit cost, or — with this box ticked — multiplied by each side's quantity.
        self._local_mult_chk = QCheckBox("Local cost total")
        self._local_mult_chk.setToolTip(
            "The “Local cost” column at the end of Matched, Only in MR and Only in CTR is SAGE's\n"
            "expected unit cost (local_expect_cost) of the Stock Code.\n"
            "Tick this to show the TOTAL instead, with the same formula as the statistics:\n"
            "equipment = days × cost × quantity, consumables = cost × quantity.\n"
            "Matched then shows one total for the MR quantity and one for the CTR quantity.\n"
            "Only on these three tabs.")
        self._local_mult_chk.setVisible(False)
        self._local_mult_chk.toggled.connect(lambda _=False: self._apply_local_costs())

        self._show_docs_btn = QPushButton("Show Document Names")
        self._show_docs_btn.setCheckable(True)
        self._show_docs_btn.setChecked(False)
        self._show_docs_btn.setToolTip(
            "Show which MR/CTR file each row came from.\n"
            "Hidden by default to keep the table compact.")
        self._show_docs_btn.setStyleSheet(f"""
            QPushButton {{
                background: white; color: {MUTED};
                border: 1px solid {BORDER}; border-radius: 4px;
                padding: 2px 10px; font-size: 11px;
                min-height: 24px;
            }}
            QPushButton:checked {{
                background: #ECEFF1; color: #263238;
                border: 1.5px solid #607D8B; font-weight: bold;
            }}
            QPushButton:disabled {{ color: {BORDER}; border-color: {BORDER}; }}
        """)
        self._show_docs_btn.toggled.connect(self._apply_doc_column_visibility)


        self._cols_btn = QPushButton("Columns")
        self._cols_btn.setToolTip(
            "Choose which columns you hid (right-click a header → Hide) to show again.\n"
            "Hiding is visual only — the Excel download always has every column.")
        self._cols_btn.setStyleSheet(f"""
            QPushButton {{
                background: white; color: {MUTED};
                border: 1px solid {BORDER}; border-radius: 4px;
                padding: 2px 8px; font-size: 11px;
                min-height: 24px;
            }}
            QPushButton:enabled {{ color: #263238; border-color: #607D8B; }}
            QPushButton:disabled {{ color: {BORDER}; border-color: {BORDER}; }}
        """)
        self._cols_btn.setEnabled(False)
        self._cols_btn.clicked.connect(
            lambda: (v := self._current_result_view()) and _unhide_dialog(v))
        for _v in (self._tab_matched, self._tab_only_mr, self._tab_only_ctr, self._tab_error):
            _v._on_hidden_changed = self._refresh_cols_btn
        self._tabs.currentChanged.connect(lambda _=0: self._refresh_cols_btn())
        self._corner_layout = _cl
        self._arrange_toolbar(combined=False)

        # This button's label swaps between "Compare Values" and "Show
        # Combined" (_set_compare_vals_mode) — reserve room for the longer
        # one so the buttons to its right don't shift when it changes.
        for _b in (self._combined_cmp_btn, self._combined_btn):
            _b.setMinimumWidth(_b.sizeHint().width() + 14)   # +bold when checked
        _keep = self._compare_vals_btn.text()
        _w = 0
        for _t in ("Compare Values", "Show Combined"):
            self._compare_vals_btn.setText(_t)
            _w = max(_w, self._compare_vals_btn.sizeHint().width())
        self._compare_vals_btn.setText(_keep)
        self._compare_vals_btn.setMinimumWidth(_w + 14)   # +bold when checked

        body_l.addWidget(self._build_filter_row())
        body_l.addWidget(_corner)
        body_l.addWidget(self._tabs)

        # Export / persist row: Excel report download, plus save a full
        # comparison snapshot for later. ("Load Comparison…" lives in the
        # top controls instead, since it works before a Compare too.)
        _export_btn_css = f"""
            QPushButton {{
                background: white;
                color: {PRIMARY};
                border: 1.5px solid {PRIMARY};
                border-radius: 6px;
                font-size: 12px;
            }}
            QPushButton:hover    {{ background: {MR_LIGHT}; }}
            QPushButton:disabled {{ color: {BORDER}; border-color: {BORDER}; }}
        """
        export_row = QHBoxLayout()
        export_row.setSpacing(8)

        self._download_btn = QPushButton("Download Excel Report…")
        self._download_btn.setEnabled(False)
        self._download_btn.setFixedHeight(36)
        self._download_btn.setToolTip(
            "Save a .xlsx report with sheets:\n"
            "Matched, Only in MR, Only in CTR, plus Combined and Error Data "
            "when the Combined View has been used."
        )
        self._download_btn.setStyleSheet(_export_btn_css)
        self._download_btn.clicked.connect(self._download_report)
        export_row.addWidget(self._download_btn)

        self._save_btn = QPushButton("Save Comparison…")
        self._save_btn.setEnabled(False)
        self._save_btn.setFixedHeight(36)
        self._save_btn.setToolTip(
            "Save the full current results (Matched, Only in MR/CTR, Combined "
            "totals, and any unit-conflict decisions) so you can reopen this "
            "exact comparison later, even if the source files change."
        )
        self._save_btn.setStyleSheet(_export_btn_css)
        self._save_btn.clicked.connect(self._save_comparison)
        export_row.addWidget(self._save_btn)

        body_l.addLayout(export_row)

        # ── Tab 1: CTR Generator ────────────────────────────────────────────
        self._ctr_gen = CTRGeneratorWidget(parent=self)
        self._main_tabs.addTab(self._ctr_gen, "CTR Generator")
        # One SAGE Export path shared by both tabs: setting it in either
        # fills the other (each side ignores a value it already holds, so
        # they cannot echo forever). Connected before restore_settings() so
        # the saved path reaches the MR vs CTR field too.
        # Which source is right for a Stock Code's rechargeability (a disparity between a
        # document and SAGE) is one list for the whole app: a choice made in either
        # statistics list shows up in the other, and is remembered between runs.
        self._stats_panel.set_choices(self._ctr_gen.source_choices())
        self._stats_panel.choices_changed.connect(self._ctr_gen.set_source_choices)
        self._ctr_gen.stats_panel().choices_changed.connect(self._stats_panel.set_choices)
        self._ctr_gen.choices_reloaded.connect(self._stats_panel.set_choices)
        self._ctr_gen.sage_path_changed.connect(self._set_comparator_sage_path)
        self._sage_edit.textChanged.connect(self._ctr_gen.set_sage_path)
        self._sage_edit.textChanged.connect(self._on_sage_path_edited)
        # The same for the two files the price view needs.
        self._ctr_gen.usd_pricebook_path_changed.connect(self._set_comparator_usd_path)
        self._ctr_gen.combined_db_path_changed.connect(self._set_comparator_db_path)
        self._usd_pb_edit.textChanged.connect(self._ctr_gen.set_usd_pricebook_path)
        self._combined_db_edit.textChanged.connect(self._ctr_gen.set_combined_db_path)
        self._usd_pb_edit.textChanged.connect(self._on_price_source_edited)
        self._combined_db_edit.textChanged.connect(self._on_price_source_edited)
        self._ctr_gen.stats_defaults_changed.connect(self._stats_panel.apply_defaults)
        # Load Files re-read the SAGE / pricebooks: the statistics of the current compare must not keep old figures.
        self._ctr_gen.files_loaded.connect(lambda: self._on_generator_files_loaded())
        # Stock Code types decided in the statistics, or edited / reset / restored in Settings:
        # the Type column, filter and tooltips of the results tables follow.
        self._stats_panel.types_changed.connect(self._on_types_changed)
        self._stats_panel.days_changed.connect(lambda _d=None: self._apply_local_costs())     # days typed or picked
        self._tabs.currentChanged.connect(self._sync_local_toggle)
        self._ctr_gen.choices_reloaded.connect(lambda _c=None: self._on_types_changed())
        self._ctr_gen.restore_settings()
        self._update_price_note()

        # ── Tab 2: CTR Tracker ───────────────────────────────────────────────
        self._ctr_tracker = CTRTrackerWidget(parent=self)
        self._main_tabs.addTab(self._ctr_tracker, "CTR Tracker")
        self._ctr_tracker.restore_settings()

        # ── Settings: gear button at the left end of the tab bar ────────────
        # Opens the Settings window (not a tab). A corner widget sits in the
        # tab bar's own row, so it lines up with the tabs.
        self._settings_dlg = SettingsDialog(self._ctr_gen, self)
        self._settings_tab = self._settings_dlg.tab
        self._gear_btn = QToolButton()
        self._gear_btn.setIcon(gear_icon(20))
        self._gear_btn.setIconSize(QSize(20, 20))
        self._gear_btn.setAutoRaise(True)
        self._gear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._gear_btn.setToolTip("Settings — SAGE path, folders, saved aliases / renames / presets")
        self._gear_btn.clicked.connect(self._settings_dlg.open_window)
        _gear_box = QWidget()
        _gl = QHBoxLayout(_gear_box)
        _gl.setContentsMargins(4, 0, 6, 0)
        _gl.addWidget(self._gear_btn)
        self._main_tabs.setCornerWidget(_gear_box, Qt.Corner.TopLeftCorner)

    def _app_settings(self) -> QSettings:
        return QSettings("SOCAR", "CTRGenerator")

    def _add_side_column(
        self,
        parent: QHBoxLayout,
        side: str,
        color: str,
        file_tip: str,
        table_tip: str,
    ) -> tuple:
        """Build one full MR or CTR column and add it to parent layout.
        Returns (file_list, table_list, preview_view).
        """
        label = side.upper()
        col   = QVBoxLayout()
        col.setSpacing(8)

        # ── Step 1: file upload ──
        fb = QGroupBox(f"Step 1 — {label} Files")
        fb.setStyleSheet(_group_css(color))
        fbl = QVBoxLayout(fb)
        btn_row = QHBoxLayout()
        browse_btn = QPushButton("Browse…")
        browse_btn.setToolTip(file_tip)
        browse_btn.clicked.connect(lambda: self._browse(side))
        clear_btn = QPushButton("Clear")
        clear_btn.setToolTip(f"Remove all loaded {label} files and reset this side.")
        clear_btn.clicked.connect(lambda: self._clear(side))
        btn_row.addWidget(browse_btn)
        btn_row.addWidget(clear_btn)
        fbl.addLayout(btn_row)
        file_lw = QListWidget()
        file_lw.setFixedHeight(64)
        file_lw.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        file_lw.setToolTip(f"Files loaded for {label}.")
        fbl.addWidget(file_lw)
        col.addWidget(fb)

        # ── Step 2: table selection + inline preview ──
        tb = QGroupBox(f"Step 2 — {label} Tables")
        tb.setStyleSheet(_group_css(color))
        tbl = QVBoxLayout(tb)
        table_lw = QListWidget()
        table_lw.setFixedHeight(96)
        table_lw.setToolTip(table_tip)
        tbl.addWidget(table_lw)

        prev_label = QLabel(f"Preview — selected {label} data")
        prev_label.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        tbl.addWidget(prev_label)
        preview = _make_view(compact=True)
        preview.setFixedHeight(120)
        preview.setToolTip(
            f"Live preview of the combined checked {label} tables.\n"
            "Updates automatically when you check or uncheck sheets."
        )
        tbl.addWidget(preview)
        col.addWidget(tb)

        parent.addLayout(col)
        return file_lw, table_lw, preview

    # ── File browsing ─────────────────────────────────────────────────────────

    def _browse(self, side: str):
        paths, _ = QFileDialog.getOpenFileNames(
            self, f"Select {side.upper()} files", "",
            "Excel files (*.xlsx *.xls *.xlsm)"
        )
        if not paths:
            return
        if self._reload_pending:
            self._set_status("Wait for the files to finish reloading, then add files.")
            return
        log.info("%s: browsing %d file(s)", side.upper(), len(paths))
        payloads: list[tuple[str, bytes]] = []
        lw0 = self._mr_file_list if side == "mr" else self._ctr_file_list
        already = [lw0.item(k).data(Qt.ItemDataRole.UserRole) for k in range(lw0.count())]
        new_origin: dict[str, tuple] = {}
        for p in paths:
            try:
                stamp = file_stamp(p)                    # before reading: an edit during the read counts as a change
                payloads.append((Path(p).name, Path(p).read_bytes()))
                if Path(p).name in already:
                    new_origin[Path(p).name] = (str(p), stamp)       # committed only when the new version is parsed
                else:
                    self._file_origin[(side, Path(p).name)] = (str(p), stamp)
            except Exception as exc:
                log.exception("Failed to read file: %s", p)
                QMessageBox.warning(self, "File error",
                                    f"Cannot read {Path(p).name}:\n{exc}")
        if not payloads:
            return
        lw = self._mr_file_list if side == "mr" else self._ctr_file_list
        loaded = [lw.item(k).data(Qt.ItemDataRole.UserRole) for k in range(lw.count())]
        replace = {}
        for name, _ in payloads:
            if name in loaded:                           # same name again: the new file REPLACES the old tables, never adds to them
                path, stamp = new_origin.get(name) or self._file_origin[(side, name)]
                replace[name] = {"old": name, "path": path, "stamp": stamp}
            else:
                self._add_file_row(lw, side, name)
        self._set_status(f"Parsing {side.upper()} files…")
        if not replace:
            self._parse(side, payloads)
            return

        def _replaced(ok, failed):
            if failed:
                QMessageBox.warning(self, "Not replaced", "These files could not be read, so their earlier "
                                    "tables were kept:\n" + "\n".join(f"  • {side.upper()}: {f}" for f in failed))
            self._mark_changed_files()
            self._set_status(f"Replaced {len(ok)} file(s) that were already added.")
        self._parse(side, payloads, replace=replace, on_done=_replaced)

    def _add_file_row(self, lw: QListWidget, side: str, name: str):
        """One Step 1 entry: the file name plus a ✕ that removes just that
        file. The name lives in the item's UserRole (not its text — the row
        widget draws it) so other code reads it from there."""
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setData(Qt.ItemDataRole.UserRole, name)
        lw.addItem(item)

        row_w = QWidget()
        row_l = QHBoxLayout(row_w)
        row_l.setContentsMargins(2, 0, 2, 0)
        row_l.setSpacing(4)
        lbl = QLabel(name)
        row_l.addWidget(lbl, 1)
        remove_btn = QPushButton("✕")
        remove_btn.setFixedSize(20, 20)
        remove_btn.setFlat(True)
        remove_btn.setToolTip(f"Remove {name} and all its tables from {side.upper()}.")
        remove_btn.setStyleSheet(f"""
            QPushButton {{ color: {MUTED}; font-size: 12px; border: none; }}
            QPushButton:hover {{ color: #C62828; }}
        """)
        remove_btn.clicked.connect(lambda _, n=name: self._remove_file(side, n))
        row_l.addWidget(remove_btn)
        row_w._label = lbl
        lw.setItemWidget(item, row_w)
        item.setSizeHint(row_w.sizeHint())

    def _remove_file(self, side: str, name: str):
        """Removes one whole file: its Step 1 entry and every table it
        contributed (including its Socar-Cape row). Different from
        _remove_table, which drops a single sheet and leaves the file listed."""
        if self._reload_pending:
            self._set_status("Wait for the files to finish reloading, then remove it.")
            return
        tables = self._mr_tables if side == "mr" else self._ctr_tables
        flw    = self._mr_file_list if side == "mr" else self._ctr_file_list
        tlw    = self._mr_table_list if side == "mr" else self._ctr_table_list
        tables[:] = [t for t in tables if t["source_file"] != name]
        for k in reversed(range(flw.count())):
            if flw.item(k).data(Qt.ItemDataRole.UserRole) == name:
                flw.takeItem(k)
        # A preload no longer matches what's loaded — same as _clear.
        self._loaded_comparison_path = None
        self._fill_table_list(tlw, tables, side)
        self._on_table_sel_changed()
        self._refresh_file_warnings(side)
        self._set_status(f"Removed {name} from {side.upper()} files.")

    def _clear(self, side: str):
        if self._reload_pending:
            self._set_status("Wait for the files to finish reloading, then clear.")
            return
        if side == "mr":
            self._mr_file_list.clear()
            self._mr_table_list.clear()
            self._mr_tables = []
            self._mr_df = pd.DataFrame()
        else:
            self._ctr_file_list.clear()
            self._ctr_table_list.clear()
            self._ctr_tables = []
            self._ctr_df = pd.DataFrame()
        # Clearing either side breaks the tie to whatever preload was open
        # (if any) — a fresh Save from here should offer Save As, not
        # silently overwrite a comparison this no longer matches.
        self._loaded_comparison_path = None
        self._refresh_inline_previews()
        self._update_state()

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _prune_workers(self) -> None:
        """Drops workers that have finished — a worker is a Qt child of the
        window, so it (and anything it references) lives until it is deleted."""
        for w in [w for w in self._workers if w.isFinished()]:
            self._workers.remove(w)
            w.deleteLater()

    def _parse(self, side: str, payloads: list[tuple[str, bytes]], replace: dict | None = None, on_done=None):
        """Parses the files and adds their tables. `replace` ({new file name: {"old", "path", "stamp"}}) makes this a
        reload: the old tables of a file are swapped for the new ones only when the new version produced tables;
        `on_done(reloaded names, failed names)` is called when finished."""
        n_files = len(payloads)     # the closures below must not capture `payloads` (the raw file bytes)
        worker = ParseWorker(payloads, parent=self, include_cover=(side == "mr"))
        self._prune_workers()
        self._workers.append(worker)
        dlg = stoppable_progress_dialog(
            self, f"Parsing {side.upper()} files…", "Parsing", worker.job)

        def _done(tables: list):
            dlg.close()
            dlg.deleteLater()
            ok, failed = [], []
            if replace:
                got = {t["source_file"] for t in tables}
                for new, info in replace.items():
                    if new in got:
                        ok.append(new)
                        gone = self._swap_file_tables(side, info["old"], new, info["path"], info["stamp"],
                                                      [t for t in tables if t["source_file"] == new])
                        self._reload_notes.extend(f"{side.upper()}: {new} — table {g!r} is no longer in the file" for g in gone)
                    elif worker.stopped_after is not None:
                        failed.append(f"{new} — not read, the parse was stopped")
                    else:
                        failed.append(f"{new} — no tables could be read")
            if side == "mr":
                self._mr_tables.extend(tables)
                self._fill_table_list(self._mr_table_list, self._mr_tables, "mr")
            else:
                self._ctr_tables.extend(tables)
                self._fill_table_list(self._ctr_table_list, self._ctr_tables, "ctr")
            self._on_table_sel_changed()
            self._refresh_file_warnings(side)
            if worker.stopped_after is not None:
                self._set_status(
                    f"Stopped on request — parsed {worker.stopped_after} of "
                    f"{n_files} {side.upper()} file(s); the rest were not read.")
            if on_done:
                on_done(ok, failed)

        def _err(msg: str):
            dlg.close()
            dlg.deleteLater()
            if on_done:                                 # a reload reports it with the others; the old tables stay
                on_done([], [f"{n} — {msg}" for n in (replace or {})])
                return
            QMessageBox.critical(self, "Parse error", msg)
            self._set_status("Error parsing file — see dialog.")

        worker.finished.connect(_done)
        worker.error.connect(_err)
        worker.start()

    def _fill_table_list(self, lw: QListWidget, tables: list, side: str):
        # Rebuilding wipes the row widgets, so carry the user's check state
        # over — otherwise removing one table would silently re-check every
        # other one (and switch the opt-in Socar-Cape toggle back on/off).
        prev_checked = {}
        for i in range(lw.count()):
            old_w = lw.itemWidget(lw.item(i))
            key = getattr(old_w, "_table_key", None)
            if key is not None:
                prev_checked[key] = old_w._checkbox.isChecked()
        lw.clear()

        def _add_row(idx, t, file_name=None):
            """idx=None + file_name → placeholder cover toggle (sheet absent)."""
            cover  = t is None or is_cover_sheet(t["source_sheet"])
            if t is not None:
                stem  = Path(t["source_file"]).stem
                label = f"{stem} › {t['table_name']}  ({len(t['data'])} rows)"
                tip   = (f"File: {t['source_file']}\n"
                         f"Sheet: {t['source_sheet']}\n"
                         f"Rows: {len(t['data'])}")
                key   = (t["source_file"], t["source_sheet"], t["table_name"])
            else:
                label = f"{Path(file_name).stem} › Socar-Cape sheet  (not found in this file)"
                tip   = f"File: {file_name}\nThis file has no readable Socar-Cape sheet."
                key   = None
            highlight = False
            if cover and t is not None:
                label = f"{Path(t['source_file']).stem} › Socar-Cape sheet  ({len(t['data'])} rows)"
                # No CH-*/NCH-* data left for this MR → point the user at the
                # cover sheet. A hint only: they decide whether to tick it.
                highlight = not any(
                    o["source_file"] == t["source_file"] and not is_cover_sheet(o["source_sheet"])
                    for o in tables
                )
                tip += ("\n\nMR cover sheet — same structure as the CH-*/NCH-* sheets "
                        "but repeats the whole request. Use it instead of, or together "
                        "with, them (both together double-count every row).")
                if highlight:
                    label += "  ⚠ no CH/NCH sheets"
                    tip += "\n\nNo CH-*/NCH-* sheets with data were found in this MR."

            item = QListWidgetItem()
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            lw.addItem(item)

            row_w = QWidget()
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(2, 0, 2, 0)
            row_l.setSpacing(4)

            cb = QCheckBox(label)
            # Cover sheet is opt-in (off by default); everything else on.
            cb.setChecked(prev_checked.get(key, not cover))
            if t is None:
                cb.setEnabled(False)
                cb.setToolTip(tip)
            else:
                cb.setToolTip(tip + "\n\nUnchecking excludes it from Compare without "
                                     "removing it — use the ✕ button to remove it entirely.")
            if highlight:
                # QPalette, not setStyleSheet — a stylesheet on a container
                # breaks tooltips for its whole subtree.
                pal = row_w.palette()
                pal.setColor(row_w.backgroundRole(), QColor("#FFF3CD"))
                pal.setColor(cb.backgroundRole(), QColor("#FFF3CD"))
                pal.setColor(cb.foregroundRole(), QColor("#7A5200"))
                row_w.setPalette(pal)
                cb.setPalette(pal)
                row_w.setAutoFillBackground(True)
                cb.setAutoFillBackground(True)
                f = cb.font(); f.setBold(True); cb.setFont(f)
            # connect after setChecked so the initial toggle doesn't fire yet
            cb.toggled.connect(lambda _: self._on_table_sel_changed())
            row_l.addWidget(cb, 1)

            if t is not None:
                remove_btn = QPushButton("✕")
                remove_btn.setFixedSize(20, 20)
                remove_btn.setFlat(True)
                remove_btn.setToolTip("Remove this table from the list entirely.")
                remove_btn.setStyleSheet(f"""
                    QPushButton {{ color: {MUTED}; font-size: 12px; border: none; }}
                    QPushButton:hover {{ color: #C62828; }}
                """)
                remove_btn.clicked.connect(lambda _, idx=idx: self._remove_table(side, idx))
                row_l.addWidget(remove_btn)

            row_w._checkbox  = cb    # so _concat_checked can find it without a child search
            row_w._table_idx = idx   # index into `tables`; None for a placeholder
            row_w._table_key = key
            lw.setItemWidget(item, row_w)
            item.setSizeHint(row_w.sizeHint())

        if side != "mr":
            for i, t in enumerate(tables):
                _add_row(i, t)
            return

        # MR: group per file, and always end each file with its Socar-Cape
        # toggle (a disabled placeholder when that sheet wasn't found).
        files = [self._mr_file_list.item(k).data(Qt.ItemDataRole.UserRole)
                 for k in range(self._mr_file_list.count())]
        for t in tables:
            if t["source_file"] not in files:
                files.append(t["source_file"])
        for fname in files:
            mine = [(i, t) for i, t in enumerate(tables) if t["source_file"] == fname]
            for i, t in mine:
                if not is_cover_sheet(t["source_sheet"]):
                    _add_row(i, t)
            covers = [(i, t) for i, t in mine if is_cover_sheet(t["source_sheet"])]
            for i, t in covers:
                _add_row(i, t)
            if not covers:
                _add_row(None, None, fname)

    def _remove_table(self, side: str, index: int):
        """Removes one table entirely (not just unchecking it) from the
        Step 2 list — e.g. the wrong sheet got included and re-uploading
        everything else for that side would be wasteful."""
        tables = self._mr_tables if side == "mr" else self._ctr_tables
        if not (0 <= index < len(tables)):
            return
        removed = tables.pop(index)
        lw = self._mr_table_list if side == "mr" else self._ctr_table_list
        self._fill_table_list(lw, tables, side)
        self._on_table_sel_changed()
        self._refresh_file_warnings(side)
        self._set_status(
            f"Removed {removed['table_name']!r} ({Path(removed['source_file']).name}) "
            f"from {side.upper()} tables."
        )

    def _refresh_file_warnings(self, side: str):
        self._paint_file_warnings(side)
        self._mark_changed_files()

    def _paint_file_warnings(self, side: str):
        """Marks a Step 1 file entry in red, with an explanatory tooltip,
        once every table it contributed has been individually removed from
        Step 2 — the file is still listed as "loaded", but none of its
        data reaches Compare anymore, which is easy to miss otherwise."""
        lw     = self._mr_file_list if side == "mr" else self._ctr_file_list
        tables = self._mr_tables    if side == "mr" else self._ctr_tables
        remaining_files = {t["source_file"] for t in tables}
        for i in range(lw.count()):
            item  = lw.item(i)
            name  = item.data(Qt.ItemDataRole.UserRole)
            label = lw.itemWidget(item)._label
            if name in remaining_files:
                label.setPalette(QPalette())
                label.setToolTip("")
            else:
                pal = label.palette()
                pal.setColor(label.foregroundRole(), _MISMATCH_FG)
                label.setPalette(pal)
                label.setToolTip(
                    f'All tables from "{name}" have been removed — '
                    "none of its data will be included in Compare.\n"
                    "Browse for it again to re-add it."
                )

    # ── Table selection ───────────────────────────────────────────────────────

    def _on_table_sel_changed(self):
        self._mr_df  = self._concat_checked(self._mr_table_list,  self._mr_tables)
        self._ctr_df = self._concat_checked(self._ctr_table_list, self._ctr_tables)
        self._refresh_inline_previews()
        self._update_state()

    def _concat_checked(self, lw: QListWidget, tables: list) -> pd.DataFrame:
        parts = []
        for k in range(lw.count()):
            row_w = lw.itemWidget(lw.item(k))
            cb  = getattr(row_w, "_checkbox", None)
            idx = getattr(row_w, "_table_idx", None)
            if (isinstance(cb, QCheckBox) and cb.isChecked()
                    and idx is not None and 0 <= idx < len(tables)):
                parts.append(tables[idx]["data"])
        return pd.concat(parts, ignore_index=True, sort=False) if parts else pd.DataFrame()

    # ── Settings ──────────────────────────────────────────────────────────────

    # ── Inline previews ───────────────────────────────────────────────────────

    def _refresh_inline_previews(self):
        """Keeps each side's preview in sync with its current _mr_df/
        _ctr_df — always, not just when there's data, so unchecking or
        removing the last table (or a table-list edit that empties it any
        other way) clears the preview instead of leaving the previous
        table's rows sitting there looking current."""
        def _vis(df: pd.DataFrame) -> pd.DataFrame:
            return df[[c for c in df.columns if not c.startswith("_")]]

        _load_view(self._mr_preview,  _vis(self._mr_df))
        _load_view(self._ctr_preview, _vis(self._ctr_df))

    # ── State machine: step indicator, hint, button, status ───────────────────

    def _update_state(self):
        has_mr  = not self._mr_df.empty
        has_ctr = not self._ctr_df.empty
        ready   = has_mr and has_ctr

        self._compare_btn.setEnabled(ready)

        if not has_mr and not has_ctr:
            self._step_bar.set_step(0)
            self._compare_hint.setText(
                "Upload at least one MR and one CTR file to continue.")
            self._set_status("")
        elif has_mr and not has_ctr:
            self._step_bar.set_step(1)
            self._compare_hint.setText("Now upload a CTR file to continue.")
            self._set_status(
                f"MR loaded ({len(self._mr_df)} rows) — upload CTR files to continue.")
        elif not has_mr and has_ctr:
            self._step_bar.set_step(1)
            self._compare_hint.setText("Now upload an MR file to continue.")
            self._set_status(
                f"CTR loaded ({len(self._ctr_df)} rows) — upload MR files to continue.")
        else:
            self._step_bar.set_step(1)
            self._compare_hint.setText("")
            self._set_status(
                f"Ready — {len(self._mr_df)} MR rows · {len(self._ctr_df)} CTR rows. "
                "Review table selection, then click Compare.")

    # ── Compare ───────────────────────────────────────────────────────────────

    def _changed_source_files(self, stamps: dict | None = None) -> list[tuple[str, str, str]]:
        """(side, file name, what happened) for every MR / CTR file whose tables are loaded but whose file on disk
        changed (or is gone) since it was read. Files reopened from a saved comparison have no origin: skipped."""
        out = []
        for side, tables in (("MR", self._mr_tables), ("CTR", self._ctr_tables)):
            for name in sorted({t["source_file"] for t in tables}):
                origin = self._file_origin.get((side.lower(), name))
                if origin is None:
                    continue
                path, before = origin
                now = stamps[path] if stamps is not None and path in stamps else file_stamp(path)
                if now is None:
                    out.append((side, name, "the file is no longer there"))
                elif now != before:
                    out.append((side, name, "the file was changed after it was added"))
        return out

    def _check_files_in_background(self):
        """Window activation: look at the files off the UI thread, then mark the changed ones."""
        w = getattr(self, "_stamp_worker", None)
        if w is not None and w.isRunning():
            return
        paths = sorted({path for path, _s in self._file_origin.values()})
        if not paths:
            return
        self._stamp_worker = StampWorker(paths, self)
        self._stamp_worker.done.connect(lambda stamps: self._mark_changed_files(stamps))
        self._stamp_worker.start()

    def _mark_changed_files(self, stamps: dict | None = None):
        """Orange ⟳ on the Step 1 rows whose file changed on disk (or is gone), and the Reload button above the lists."""
        for side in ("mr", "ctr"):
            self._paint_file_warnings(side)
        changed = self._changed_source_files(stamps)
        why = {(side.lower(), name): w for side, name, w in changed}
        for side, lw in (("mr", self._mr_file_list), ("ctr", self._ctr_file_list)):
            for i in range(lw.count()):
                item = lw.item(i)
                name = item.data(Qt.ItemDataRole.UserRole)
                label = lw.itemWidget(item)._label
                label.setText(name)
                if (side, name) not in self._file_origin and not label.toolTip():
                    label.setToolTip(f"{name} came from a saved comparison (or its location is unknown): there is no "
                                     "file on disk to check or reload. Browse for it to add it again.")
                if (side, name) in why:
                    label.setText("⟳ " + name)
                    pal = label.palette()
                    pal.setColor(label.foregroundRole(), QColor("#E65100"))
                    label.setPalette(pal)
                    label.setToolTip(f"{name}: {why[(side, name)]}.\nThe tables loaded from it are the OLD version — "
                                     "use \"Reload changed files\" to read it again.")
        self._reload_btn.setVisible(bool(changed))
        self._reload_btn.setEnabled(self._reload_pending == 0)
        self._reload_btn.setText(f"⟳  Reload changed files ({len(changed)})")
        self._reload_btn.setToolTip("Reads these files again from where they were added and replaces their tables "
                                    "(your unticked sheets stay unticked):\n"
                                    + "\n".join(f"  • {s}: {n} — {w}" for s, n, w in changed))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow() and hasattr(self, "_reload_btn"):
            self._check_files_in_background()           # e.g. back from saving the file in Excel

    def _ask_changed_files(self, changed: list[tuple[str, str, str]]) -> str:
        """Warn that loaded files changed on disk: "reload", "anyway" or "cancel". `changed_files_chooser`
        replaces the dialog (tests); it may also return True ("anyway") / False ("cancel")."""
        if getattr(self, "changed_files_chooser", None) is not None:
            r = self.changed_files_chooser(changed)
            return r if isinstance(r, str) else ("anyway" if r else "cancel")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Files changed since they were added")
        box.setText("These files changed on disk after they were added, so the tables below are from the OLD "
                    "version of each file:\n\n" + "\n".join(f"  • {side}: {name} — {why}" for side, name, why in changed)
                    + "\n\n\"Reload them and compare\" reads each file again from where it was added.")
        reload_btn = box.addButton("Reload them and compare", QMessageBox.ButtonRole.AcceptRole)
        anyway = box.addButton("Compare anyway", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(reload_btn)
        box.exec()
        clicked = box.clickedButton()
        box.deleteLater()
        return "reload" if clicked is reload_btn else "anyway" if clicked is anyway else "cancel"

    def _ask_missing_file(self, side: str, name: str, path: str):
        """The file to reload is gone: "keep" the old tables, "remove" the file, or a new path to read instead.
        `missing_file_chooser` replaces the dialog (tests)."""
        if getattr(self, "missing_file_chooser", None) is not None:
            return self.missing_file_chooser(side, name, path)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("File not found")
        box.setText(f"{side}: {name} is no longer at\n{path}\n\nIt may have been deleted, moved or renamed.")
        locate = box.addButton("Locate it…", QMessageBox.ButtonRole.AcceptRole)
        remove = box.addButton("Remove it from the list", QMessageBox.ButtonRole.DestructiveRole)
        keep = box.addButton("Keep the old tables", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        box.deleteLater()
        if clicked is remove:
            return "remove"
        if clicked is locate:
            found, _ = QFileDialog.getOpenFileName(self, f"Locate {name}", str(Path(path).parent),
                                                   "Excel files (*.xlsx *.xls *.xlsm)")
            return found or "keep"
        return "keep"

    def _swap_file_tables(self, side: str, old: str, new: str, path: str, stamp, new_tables=()) -> list[str]:
        """The new version of a file was parsed: drop the old tables (kept only until now), point the Step 1 row and
        the remembered origin at the new file. Returns the names of old tables the new version no longer has."""
        tables = self._mr_tables if side == "mr" else self._ctr_tables
        kept = {(t["source_sheet"], t["table_name"]) for t in new_tables}
        gone = [t["table_name"] for t in tables if t["source_file"] == old and (t["source_sheet"], t["table_name"]) not in kept]
        tables[:] = [t for t in tables if t["source_file"] != old]
        self._file_origin.pop((side, old), None)
        self._file_origin[(side, new)] = (path, stamp)
        if new != old:
            tlw = self._mr_table_list if side == "mr" else self._ctr_table_list
            for k in range(tlw.count()):                         # the unticked sheets stay unticked under the new name
                w = tlw.itemWidget(tlw.item(k))
                key = getattr(w, "_table_key", None)
                if key is not None and key[0] == old:
                    w._table_key = (new,) + tuple(key[1:])
            flw = self._mr_file_list if side == "mr" else self._ctr_file_list
            for k in reversed(range(flw.count())):
                if flw.item(k).data(Qt.ItemDataRole.UserRole) == old:
                    flw.takeItem(k)
            self._add_file_row(flw, side, new)
        self._loaded_comparison_path = None
        return gone

    def _reload_changed_files(self, changed=None, compare_after: bool = False):
        """Re-reads every changed MR / CTR file from where it was added, and swaps in its new tables. Nothing is lost
        when a step fails: the old tables of that file stay and the problem is reported at the end."""
        if self._reload_pending:
            return
        changed = changed if changed is not None else self._changed_source_files()
        if not changed:
            return
        problems: list[str] = []
        self._reload_notes = []
        batches: dict[str, tuple[list, dict]] = {"mr": ([], {}), "ctr": ([], {})}
        for side_u, name, _why in changed:
            side = side_u.lower()
            path, _before = self._file_origin[(side, name)]
            if file_stamp(path) is None:
                choice = self._ask_missing_file(side_u, name, path)
                if choice == "remove":
                    self._remove_file(side, name)
                    self._file_origin.pop((side, name), None)
                    continue
                if choice == "keep" or not choice:
                    problems.append(f"{side_u}: {name} — the file is gone; the old tables were kept")
                    continue
                path = str(choice)
            new = Path(path).name
            files = self._mr_file_list if side == "mr" else self._ctr_file_list
            names = [files.item(k).data(Qt.ItemDataRole.UserRole) for k in range(files.count())]
            if names.count(name) > 1:
                problems.append(f"{side_u}: {name} — two files with this name are loaded, so the app cannot tell which "
                                "tables belong to which; remove one with ✕, then reload")
                continue
            if new != name and new in names:
                problems.append(f"{side_u}: {name} — {new} is already in the list; the old tables were kept")
                continue
            try:
                stamp = file_stamp(path)               # before reading: an edit during the read counts as a change
                data = Path(path).read_bytes()
            except Exception as exc:
                log.exception("Failed to re-read file: %s", path)
                problems.append(f"{side_u}: {name} — cannot be read ({exc}); the old tables were kept")
                continue
            batches[side][0].append((new, data))
            batches[side][1][new] = {"old": name, "path": str(path), "stamp": stamp}
        jobs = [(s, b) for s, b in batches.items() if b[0]]
        reloaded: list[str] = []
        if not jobs:
            self._finish_reload(reloaded, problems, compare_after)
            return
        self._reload_pending = len(jobs)
        self._reload_btn.setEnabled(False)
        self._set_status("Reading the changed files again…")

        def _one_done(side, ok, failed):
            reloaded.extend(f"{side.upper()}: {n}" for n in ok)
            problems.extend(f"{side.upper()}: {n}; the old tables were kept" for n in failed)
            self._reload_pending -= 1
            if self._reload_pending == 0:
                self._finish_reload(reloaded, problems, compare_after)

        for side, (payloads, info) in jobs:
            self._parse(side, payloads, replace=info, on_done=lambda ok, failed, s=side: _one_done(s, ok, failed))

    def _finish_reload(self, reloaded: list[str], problems: list[str], compare_after: bool):
        self._on_table_sel_changed()
        self._mark_changed_files()
        done = f"Reloaded {len(reloaded)} file(s) — press Compare to see them." if reloaded else "Nothing was reloaded."
        notes = list(self._reload_notes)
        self._set_status(done if not problems else done + f" {len(problems)} problem(s).")
        anyway = False
        if problems or (notes and not compare_after):
            lines = problems + notes
            if getattr(self, "reload_report", None) is not None:
                anyway = bool(self.reload_report(lines)) and compare_after and bool(problems)
            else:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Icon.Warning)
                box.setWindowTitle("Some files were not reloaded" if problems else "Reloaded — some tables are gone")
                box.setText("\n".join(f"  • {p}" for p in lines))
                go = box.addButton("Compare anyway (old tables for those files)", QMessageBox.ButtonRole.DestructiveRole) \
                    if (compare_after and problems) else None
                box.addButton(QMessageBox.StandardButton.Close)
                box.exec()
                anyway = go is not None and box.clickedButton() is go
                box.deleteLater()
        if compare_after and (not problems or anyway):
            if notes:
                log.info("Reload before compare: %s", "; ".join(notes))
            self._run_compare(skip_file_check=anyway)       # "Compare anyway" was just chosen: do not ask again

    def _run_compare(self, skip_file_check: bool = False):
        changed = [] if skip_file_check else self._changed_source_files()
        if changed:
            choice = self._ask_changed_files(changed)
            if choice == "reload":
                self._reload_changed_files(changed, compare_after=True)
                return
            if choice != "anyway":
                self._set_status("Compare cancelled — reload the changed file(s) first.")
                return
        log.info("Compare started: MR=%d rows, CTR=%d rows",
                  len(self._mr_df), len(self._ctr_df))
        mr  = self._mr_df.copy()
        ctr = self._ctr_df.copy()

        key_col = "Stock Code"
        for side, df in [("MR", mr), ("CTR", ctr)]:
            col = find_key_col(list(df.columns))
            if not col:
                QMessageBox.warning(
                    self, "Stock Code not found",
                    f"Could not find a Stock Code column in the {side} data.\n"
                    f"Columns available: {', '.join(df.columns[:8])}")
                return
            df["_KEY_"] = df[col].astype(str).str.strip().str.upper()

        bad = {"", "NAN", "-", "NONE", "STOCKCODE", "0"}
        mr  = mr[~mr["_KEY_"].isin(bad)]
        ctr = ctr[~ctr["_KEY_"].isin(bad)]

        merged = pd.merge(mr, ctr, on="_KEY_", how="outer",
                          suffixes=("_MR", "_CTR"), indicator=True)

        only_mr_df  = merged[merged["_merge"] == "left_only"].copy()
        only_ctr_df = merged[merged["_merge"] == "right_only"].copy()
        matched     = merged[merged["_merge"] == "both"].copy()

        src_mr  = "_SourceFile_MR"  if "_SourceFile_MR"  in merged.columns else "_SourceFile"
        src_ctr = "_SourceFile_CTR" if "_SourceFile_CTR" in merged.columns else None
        sheet_mr  = "_SourceSheet_MR"  if "_SourceSheet_MR"  in merged.columns else (
            "_SourceSheet" if "_SourceSheet" in merged.columns else None)
        sheet_ctr = "_SourceSheet_CTR" if "_SourceSheet_CTR" in merged.columns else None

        # Resolve column references once from the full merged frame so every
        # subset (matched / only_mr / only_ctr) can reuse them.
        desc_mr   = _find_col(merged, "Description_MR",  "Description")
        desc_ctr  = _find_col(merged, "Description_CTR")
        qty_mr    = _find_col(merged, "Qty_MR",           "Qty")
        unit_mr   = _find_col(merged, "Unit_MR",           "Unit")
        rech_col  = _find_col(merged, "Rechargeable_MR",  "Rechargeable")
        alloc_col = _find_col(merged, "Allocation_MR",    "Allocation")
        qty_ctr   = _find_col(merged, "Quantity_CTR",     "Quantity")
        unit_ctr  = _find_col(merged, "Unit_CTR")
        rate_col  = _find_col(merged, "Rate_CTR",         "Rate")

        def _build_display(df: pd.DataFrame) -> pd.DataFrame:
            """
            Build the standardised display table for any merge subset.
            Columns that don't exist on one side will contain NaN (shown as
            blank, tinted with the appropriate side colour).
            Column order: key → docs → description pair → qty pair →
                          unit pair → MR-only fields → CTR-only fields.
            """
            out = pd.DataFrame({"Stock Code": df["_KEY_"].values})
            # Description pair (side by side)
            if desc_mr:  out["Description (MR)"]  = df[desc_mr].values
            if desc_ctr: out["Description (CTR)"] = df[desc_ctr].values
            # Qty pair (side by side)
            if qty_mr:   out["Qty (MR)"]   = df[qty_mr].values
            if qty_ctr:  out["Qty (CTR)"]  = df[qty_ctr].values
            # Unit pair (side by side)
            if unit_mr:  out["Unit (MR)"]  = df[unit_mr].values
            if unit_ctr: out["Unit (CTR)"] = df[unit_ctr].values
            # MR-only fields
            if rech_col:  out["Rechargeable"] = df[rech_col].values
            if alloc_col: out["Allocation"]   = df[alloc_col].values
            # CTR-only fields
            if rate_col:  out["Rate (CTR)"]   = df[rate_col].values
            # Source documents — rightmost so they don't crowd the comparison columns
            out["MR Document"]  = df[src_mr].values  if src_mr  in df.columns else ""
            out["CTR Document"] = df[src_ctr].values if (src_ctr and src_ctr in df.columns) else ""
            out["MR Sheet"]     = df[sheet_mr].values  if (sheet_mr  and sheet_mr  in df.columns) else ""
            out["CTR Sheet"]    = df[sheet_ctr].values if (sheet_ctr and sheet_ctr in df.columns) else ""
            return out.sort_values("Stock Code").reset_index(drop=True)

        self._display_df = _build_display(matched)
        self._omr_dl     = _build_display(only_mr_df)
        self._octr_dl    = _build_display(only_ctr_df)
        self._set_local_cost_frames(None)           # blank Days / Local cost columns at the end; filled when the statistics are ready

        # Rate (CTR) vs Rechargeable (MR) consistency check — computed on
        # every Compare, but only highlighted once Compare Values is pressed.
        self._rate_rech_mismatch_cells = _find_rate_rechargeable_mismatches(self._display_df)

        # Combined view: per-Stock-Code MR/CTR Qty totals, restricted to keys
        # that actually matched. Column names here are resolved against the
        # raw, pre-merge mr/ctr frames (not `merged`) — pandas only suffixes
        # a column with _MR/_CTR when both sides share that exact name (e.g.
        # "Unit"); "Qty" vs "Quantity" never collide, so those stay
        # unsuffixed in `merged` while the raw frames still use their own
        # native names on both counts. Reusing qty_mr/unit_mr/etc. (resolved
        # against `merged`, for the display table) here would silently find
        # nothing on the "Unit" columns.
        qty_mr_raw   = _find_col(mr,  "Qty")
        unit_mr_raw  = _find_col(mr,  "Unit")
        qty_ctr_raw  = _find_col(ctr, "Quantity", "Qty")
        unit_ctr_raw = _find_col(ctr, "Unit")
        desc_mr_raw  = _find_col(mr,  "Description")
        desc_ctr_raw = _find_col(ctr, "Description")

        # Fresh Compare = fresh decisions; a stock code approved/rejected
        # before this run no longer applies once the underlying data changed.
        matched_keys = set(matched["_KEY_"])
        self._combined_raw = _aggregate_combined(
            mr, ctr, qty_mr_raw, unit_mr_raw, qty_ctr_raw, unit_ctr_raw, matched_keys,
            desc_mr_raw, desc_ctr_raw)
        self._combined_decisions = {}
        self._mr_files_used  = sorted(mr["_SourceFile"].dropna().unique().tolist()) \
            if "_SourceFile" in mr.columns else []
        self._ctr_files_used = sorted(ctr["_SourceFile"].dropna().unique().tolist()) \
            if "_SourceFile" in ctr.columns else []

        self._pending_stats = self._build_stats_items(mr, ctr)
        self._present_compare_results()
        self._start_stats_job_later()

        now = datetime.now().strftime("%H:%M")
        self._set_status(
            f"Compared at {now} — "
            f"{len(matched)} matched · "
            f"{len(only_mr_df)} only in MR · "
            f"{len(only_ctr_df)} only in CTR."
        )
        log.info("Compare finished: matched=%d only_mr=%d only_ctr=%d",
                  len(matched), len(only_mr_df), len(only_ctr_df))

    def _present_compare_results(self):
        """Populates the results panel — metrics, the three main tabs, the
        and the Combined/Needs Review/Error Data tabs — from whatever is currently in
        self._display_df/_omr_dl/_octr_dl/_combined_raw/_combined_decisions/
        _rate_rech_mismatch_cells. Shared by a fresh Compare and by
        reopening a saved comparison, so both end up in the same state."""
        # Statistics belong to the Compare that made them (or to the saved
        # comparison being reopened); set their state before the Needs Review
        # list is built, since it lists the rows with no quantity.
        self._stats_token += 1
        self._local_ready = False                    # the previous compare's figures must not leak into this one
        self._local_state = "waiting" if self._pending_stats is not None else "none"
        self._set_stats_tab_busy(False)
        self._stats_items, self._pending_stats = self._pending_stats, None
        saved, self._pending_saved_stats = self._pending_saved_stats, None
        restored = self._load_stats_state(saved)
        self._refresh_combined_tables()
        if restored is not None:
            rows, manual, use_qty = restored
            self._stats_panel.restore(rows, manual, use_qty, days=self._restored_days)
            self._stats_panel.set_doc_filter(self._stats_doc_pred())
            self._local_ready = True
            self._local_state = "ready"
            self._apply_local_costs()
        else:
            self._stats_panel.clear(
                "Statistics are calculated when you press Compare."
                if self._stats_items is not None else
                "This comparison has no cost statistics yet. Press Compare with the "
                "original MR/CTR files to calculate them.")

        self._m_matched.setText(str(len(self._display_df)))
        self._m_only_mr.setText(str(len(self._omr_dl)))
        self._m_only_ctr.setText(str(len(self._octr_dl)))
        self._metrics_w.setVisible(True)

        _show_result(self._stack_matched,  self._tab_matched,  self._display_df)
        _show_result(self._stack_only_mr,  self._tab_only_mr,  self._omr_dl)
        _show_result(self._stack_only_ctr, self._tab_only_ctr, self._octr_dl)
        self._apply_doc_column_visibility(self._show_docs_btn.isChecked())
        self._decorate_views()

        self._tabs.setCurrentWidget(self._stack_matched)
        self._sync_local_toggle()
        self._download_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        self._step_bar.set_step(2)

        self._reset_doc_filter()
        self._refresh_type_filter_state()

        # Reset value-comparison state (new data loaded, old highlights gone)
        self._compare_vals_btn.blockSignals(True)
        self._compare_vals_btn.setChecked(False)
        self._compare_vals_btn.blockSignals(False)
        self._compare_vals_btn.setEnabled(not self._display_df.empty)
        self._mismatch_rows = []
        self._mismatch_pos  = -1
        self._set_mismatch_kinds({})
        self._prev_mm_btn.setEnabled(False)
        self._next_mm_btn.setEnabled(False)
        self._mismatch_lbl.setVisible(False)

        # Reset combined-view state (new data loaded, old grouping gone)
        self._combined_btn.blockSignals(True)
        self._combined_btn.setChecked(False)
        self._combined_btn.blockSignals(False)
        self._combined_btn.setEnabled(bool(self._combined_raw))
        self._combined_cmp_btn.blockSignals(True)
        self._combined_cmp_btn.setChecked(False)
        self._combined_cmp_btn.blockSignals(False)
        self._combined_cmp_btn.setVisible(False)
        self._combined_cmp_btn.setEnabled(bool(self._combined_raw))

        # Expand results panel to take ~70 % of the window height, and drop
        # any stale Hide/Maximize state from a previous set of results.
        self._collapse_btn.blockSignals(True)
        self._collapse_btn.setChecked(False)
        self._collapse_btn.blockSignals(False)
        self._collapse_btn.setText("▲  Hide results")
        self._maximize_btn.setEnabled(True)

        self._maximize_btn.blockSignals(True)
        self._maximize_btn.setChecked(False)
        self._maximize_btn.blockSignals(False)
        self._maximize_btn.setText("⛶  Maximize")
        self._set_controls_chrome_visible(True)

        self._results_w.setVisible(True)
        self._results_body_w.setVisible(True)
        total = self._splitter.height()
        self._splitter.setSizes([int(total * 0.30), int(total * 0.70)])

    # ── Cost statistics (MA131) ───────────────────────────────────────────────

    def _load_stats_state(self, saved: dict | None):
        """Set the category map, the document-filter pairs and the rows with no
        quantity from this Compare's items or from a saved comparison's
        `stats` block. Returns (rows, manual costs, use_qty) to show in the
        Statistics tab for a saved comparison, else None."""
        empty_missing = pd.DataFrame(columns=cost_stats.MISSING_QTY_COLUMNS)
        if self._stats_items is not None:
            it = self._stats_items
            self._category_by_key = cost_stats.ctr_category_map(it)
            self._stats_pairs = it[["side", "key", "doc", "sheet"]]
            self._missing_qty_df = cost_stats.missing_qty(it)
            return None
        self._category_by_key = None
        self._stats_pairs = None
        self._missing_qty_df = empty_missing
        if not saved:
            return None
        try:
            self._category_by_key = dict(saved.get("category_by_key") or {}) or None
            self._missing_qty_df = pd.DataFrame(saved.get("missing_qty") or [],
                                                columns=cost_stats.MISSING_QTY_COLUMNS)
            if not saved.get("rows"):
                return None
            use_qty = bool(saved.get("use_qty", True))
            rows = cost_stats.rows_from_records(saved["rows"], use_qty)
            self._stats_pairs = rows[["side", "key", "doc", "sheet"]]
            manual = {str(k): float(v) for k, v in (saved.get("manual") or {}).items()}
            self._restored_days = {str(k): float(v) for k, v in (saved.get("days") or {}).items()}
            return rows, manual, use_qty
        except Exception:
            log.exception("Saved statistics could not be restored")
            return None

    def _stats_snapshot(self) -> dict | None:
        """The statistics to store in a saved comparison (None = nothing to store)."""
        rows = self._stats_panel.base_rows()
        has_missing = not self._missing_qty_df.empty
        if rows is None and not has_missing and not self._category_by_key:
            return None
        return {
            "rows": cost_stats.rows_to_records(rows) if rows is not None else [],
            "manual": self._stats_panel.manual_costs(),
            "days": self._stats_panel.days(),
            "use_qty": self._stats_panel.use_qty(),
            "category_by_key": self._category_by_key or {},
            "missing_qty": self._missing_qty_df.to_dict(orient="records"),
        }

    def _on_stats_reload_clicked(self):
        if self._stats_items is None:
            QMessageBox.information(
                self, "Reload statistics",
                "These statistics were saved with the comparison, so there is nothing to "
                "recalculate from.\n\nPress Compare with the original MR/CTR files to "
                "calculate them again against the current SAGE Export.")
            return
        self._start_stats_job(force=True)

    @staticmethod
    def _build_stats_items(mr: pd.DataFrame, ctr: pd.DataFrame) -> pd.DataFrame:
        """Every MR and CTR row of this Compare (matched or not) as the input
        of cost_stats.cost_rows. Rechargeability comes from the documents:
        MR's Rechargeable column, and for the CTR the Rate column (a number
        means rechargeable, NONRECHARG means not) — same rule as the
        Rate/Rechargeable check. Up for discussion with the department.

        Days (Document price, equipment): a CTR row carries its own Days; an MR row takes the days
        of the matching CTR row (same Stock Code) when the CTR rows agree. When the code has no
        CTR row, the CTR row has no days, or the CTR rows give different days, the MR row gets
        none and says why: it is shown highlighted, waiting for the user to type the days."""
        ctr_days: dict[str, dict[float, set[str]]] = {}      # key -> {days: documents that say so}
        ctr_keys: set[str] = set()
        if not ctr.empty:
            days_c = _find_col(ctr, "Days")
            ctr_keys = set(ctr["_KEY_"])
            if days_c:
                docs = (ctr["_SourceFile"].map(_norm_val) if "_SourceFile" in ctr.columns
                        else pd.Series([""] * len(ctr), index=ctr.index))
                for k, v, doc in zip(ctr["_KEY_"], pd.to_numeric(ctr[days_c], errors="coerce"), docs):
                    if pd.notna(v):
                        ctr_days.setdefault(k, {}).setdefault(float(v), set()).add(doc)

        def mr_days(keys) -> tuple[list[float], list[str], list[str]]:
            vals, notes, options = [], [], []
            for k in keys:
                got = ctr_days.get(k)
                if got and len(got) == 1:
                    vals.append(next(iter(got)))
                    notes.append("from the matching CTR row")
                    options.append("")
                elif got:                                       # two or more CTRs disagree: the user decides
                    vals.append(float("nan"))
                    notes.append("the matching CTRs give different days ("
                                 + " / ".join(f"{d:g}" for d in sorted(got)) + ")")
                    options.append(json.dumps([{"days": d, "docs": sorted(got[d])} for d in sorted(got)]))
                else:
                    vals.append(float("nan"))
                    notes.append("the matching CTR row has no days" if k in ctr_keys else "")
                    options.append("")
            return vals, notes, options

        parts = []
        for side, df in (("MR", mr), ("CTR", ctr)):
            if df.empty:
                continue
            n = len(df)
            desc_c = _find_col(df, "Description")
            if side == "MR":
                qty_c = _find_col(df, "Qty")
                st_c = _find_col(df, "Rechargeable")
                status = (df[st_c].map(lambda v: _norm_val(v).upper()
                                       if _norm_val(v).upper() in ("RECHARGE", "NONRECHARG")
                                       else None).values if st_c else [None] * n)
            else:
                qty_c = _find_col(df, "Quantity", "Qty")
                rate_c = _find_col(df, "Rate")
                status = df[rate_c].map(_rate_implied_status).values if rate_c else [None] * n
            if side == "MR":
                days_vals, days_notes, days_opts = mr_days(df["_KEY_"].tolist())
            else:
                dc = _find_col(df, "Days")
                days_vals = (pd.to_numeric(df[dc], errors="coerce").tolist() if dc else [float("nan")] * n)
                days_notes = [""] * n
                days_opts = [""] * n
            parts.append(pd.DataFrame({
                "key":     df["_KEY_"].values,
                "doc":     df["_SourceFile"].map(_norm_val).values if "_SourceFile" in df.columns else [""] * n,
                "sheet":   df["_SourceSheet"].map(_norm_val).values if "_SourceSheet" in df.columns else [""] * n,
                "side":    side,
                "qty":     df[qty_c].values if qty_c else [float("nan")] * n,
                "status":  status,
                "section": df["_Section"].values if (side == "CTR" and "_Section" in df.columns) else [None] * n,
                "description": df[desc_c].map(_norm_val).values if desc_c else [""] * n,
                "days":    days_vals,
                "days_note": days_notes,
                "days_options": days_opts,
            }))
        if not parts:
            return pd.DataFrame(columns=["key", "doc", "sheet", "side", "qty", "status", "section",
                                         "description", "days", "days_note", "days_options"])
        return pd.concat(parts, ignore_index=True)

    def _start_stats_job_later(self, delay_ms: int = 400):
        """Start the statistics shortly AFTER the comparison is on screen, so
        showing the results never waits for them. With no usable SAGE file
        there is no work to defer, so that warning appears at once."""
        path = self._sage_edit.text().strip()
        if self._stats_items is None or not path or not Path(path).is_file():
            self._start_stats_job()
            return
        self._stats_panel.clear(
            "Statistics will appear here shortly — they are calculated in the background "
            "after the comparison is shown. You can keep working meanwhile.", "info")
        token = self._stats_token
        QTimer.singleShot(
            delay_ms,
            lambda: self._start_stats_job() if token == self._stats_token else None)

    def _start_stats_job(self, force: bool = False):
        self._start_stats_job_inner(force)
        self._decorate_views()                  # the waiting reason in the tables follows (SAGE set / not set ...)

    def _start_stats_job_inner(self, force: bool = False):
        """(Re)start the background cost statistics for the last Compare.
        force=True (Reload statistics) re-reads the SAGE file instead of
        reusing the table the Generator already parsed."""
        panel = self._stats_panel
        self._set_stats_tab_busy(False)
        if self._stats_items is None:
            panel.clear("Statistics are calculated when you press Compare.")
            return
        path = self._sage_edit.text().strip()
        if not path:
            self._stats_token += 1
            panel.clear("SAGE Export is not set, so statistics cannot be calculated. Choose the "
                        "file in “SAGE Export” above the Compare button (it is shared with the "
                        "CTR Generator tab). They will load as soon as it is set.")
            return
        if not Path(path).is_file():
            self._stats_token += 1
            panel.clear(f"The SAGE Export file was not found:\n{path}\n"
                        "Choose it again above the Compare button.", "error")
            return
        self._stats_token += 1
        panel.clear("Statistics are loading and will appear here shortly — reading the SAGE "
                    "Export and costing every row. You can keep working meanwhile.", "info")
        reuse = None if force else self._ctr_gen.loaded_sage_df(path)
        usd = self._usd_edit_path()
        names = self._combined_db_edit.text().strip()
        usd = usd if usd and Path(usd).is_file() else ""          # a missing file is reported by the price note
        names = names if names and Path(names).is_file() else ""
        self._update_price_note()
        w = StatsWorker(self._stats_token, self._stats_items, path, reuse, self,
                        usd_path=usd, usd_df=None if force else self._ctr_gen.loaded_usd_df(usd),
                        names_path=names, names_df=None if force else self._ctr_gen.loaded_names_df(names))
        w.done.connect(self._on_stats_done)
        w.finished.connect(lambda w=w: self._stats_workers.remove(w) if w in self._stats_workers else None)
        w.finished.connect(w.deleteLater)
        self._stats_workers.append(w)
        self._set_stats_tab_busy(True)            # "Statistics ⏳" until the worker reports
        w.start(QThread.Priority.LowPriority)     # the window comes first

    def _on_stats_done(self, token: int, rows, error: str):
        if token != self._stats_token:
            return                              # a newer Compare / Reload replaced this run
        self._set_stats_tab_busy(False)
        if error:
            self._local_state = "failed"        # nothing more is awaited: the tables stop showing the waiting mark
            self._decorate_views()
        if error == _STATS_STOPPED:
            self._stats_panel.clear("Statistics were stopped before they finished. "
                                    "Press “Reload statistics” to run them again.")
        elif error:
            log.error("Statistics failed: %s", error)
            self._stats_panel.clear(f"Statistics could not be calculated:\n{error}", "error")
        else:
            self._stats_panel.set_rows(rows)
            self._stats_panel.set_doc_filter(self._stats_doc_pred())
            self._local_ready = True
            self._local_state = "ready"
            self._apply_local_costs()
            log.info("Statistics ready: %d rows", len(rows))

    def _on_generator_files_loaded(self) -> None:
        if self._stats_items is not None:
            self._start_stats_job()

    def _on_sage_path_edited(self, _text: str):
        if self._stats_items is not None:
            self._start_stats_job()

    def _stats_doc_pred(self):
        """rows -> one bool per row for the document filter, or None when it
        is off. Same pairing as the Matched / Only tabs: a row stays when its
        own (document, sheet) satisfies the expression together with some
        (document, sheet) of the OTHER side that has the same Stock Code — or
        with no partner at all (the Only-in-MR / Only-in-CTR case)."""
        tree, items = self._doc_filter_tree, self._stats_pairs
        if tree is None or items is None or items.empty:
            return None
        partners: dict[str, dict[str, set]] = {"MR": {}, "CTR": {}}
        for side, key, doc, sheet in zip(items["side"], items["key"], items["doc"], items["sheet"]):
            partners[side].setdefault(key, set()).add((doc, sheet))

        def pred(rows: pd.DataFrame) -> list[bool]:
            cache: dict[tuple, bool] = {}
            out = []
            for side, doc, sheet, key in zip(rows["side"], rows["doc"], rows["sheet"], rows["key"]):
                ck = (side, doc, sheet, key)
                if ck not in cache:
                    me = (doc, sheet)
                    others = [None, *partners["CTR" if side == "MR" else "MR"].get(key, ())]
                    cache[ck] = any(
                        doc_filter.evaluate(tree, me, o) if side == "MR"
                        else doc_filter.evaluate(tree, o, me)
                        for o in others)
                out.append(cache[ck])
            return out
        return pred

    # ── SAGE Export path (shared with the CTR Generator tab) ─────────────────

    def _build_sage_row(self) -> QHBoxLayout:
        row, self._sage_edit = _file_picker_row("SAGE Export", "Excel (*.xlsx *.xlsm *.xls)")
        self._sage_edit.setToolTip(
            "SAGE Export used for the cost statistics.\n"
            "Shared with the CTR Generator tab — setting it here sets it there too.")
        return row

    def _set_comparator_sage_path(self, path: str) -> None:
        if self._sage_edit.text() != path:
            self._sage_edit.setText(path)

    def _build_pricebook_row(self) -> QHBoxLayout:
        """USD Pricebook and Combined DB, side by side on one line (the price view of
        the statistics needs them for the equipment rates). Both are shared with the
        CTR Generator tab, like the SAGE Export."""
        row = QHBoxLayout()
        row.setSpacing(12)
        usd_row, self._usd_pb_edit = _file_picker_row("USD Pricebook", "Excel (*.xlsx *.xls)")
        db_row, self._combined_db_edit = _file_picker_row("Combined DB", "Excel (*.xlsx *.xls)")
        self._usd_pb_edit.setToolTip(
            "USD Pricebook — the equipment rates for “Document price” in the Statistics tab.\n"
            "Shared with the CTR Generator tab — setting it here sets it there too.")
        self._combined_db_edit.setToolTip(
            "Combined DB — its equipment-names sheet turns a stock code into the pricebook description\n"
            "(optional, like in the CTR Generator). Shared with the CTR Generator tab.")
        row.addLayout(usd_row, 1)
        row.addLayout(db_row, 1)
        return row

    def _set_comparator_usd_path(self, path: str) -> None:
        if self._usd_pb_edit.text() != path:
            self._usd_pb_edit.setText(path)

    def _set_comparator_db_path(self, path: str) -> None:
        if self._combined_db_edit.text() != path:
            self._combined_db_edit.setText(path)

    def _on_price_source_edited(self, _text: str = ""):
        """The USD Pricebook or the Combined DB changed: the equipment rates need recalculating."""
        if self._stats_items is not None:
            self._start_stats_job()
        else:
            self._update_price_note()

    def _update_price_note(self) -> None:
        """Tell the price view when equipment cannot be priced because no USD Pricebook is set."""
        usd = self._usd_edit_path()
        if not usd:
            note = ("The USD Pricebook is not set (field above the Statistics), so equipment has no "
                    "rate in Document price. Consumables are priced from SAGE.")
        elif not Path(usd).is_file():
            note = f"The USD Pricebook file was not found:\n{usd}"
        else:
            note = ""
        self._stats_panel.set_price_note(note)

    def _usd_edit_path(self) -> str:
        return self._usd_pb_edit.text().strip()

    # ── Document filter ───────────────────────────────────────────────────────

    def _build_filter_row(self) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lbl = QLabel("Filter documents:")
        lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText(
            "e.g.  (MR:Jan>CH-01 or MR:Feb) and not CTR:draft   — type a name to get suggestions")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.setEnabled(False)
        self._filter_edit.setToolTip(
            "Show only rows from the documents you choose. Results, Excel downloads and\n"
            "saved comparisons still contain everything.\n\n"
            "  MR:<document>   or   CTR:<document>   — part of the file name is enough\n"
            "  MR:<document>><sheet>   — limit to one sheet of that file\n"
            "  and / or / not, and ( ) brackets to group\n"
            "  Quote names that contain ( ) > or the words and/or/not:\n"
            '  MR:"Rock and Roll (1).xlsx"')
        self._filter_info = QLabel("")
        self._filter_info.setStyleSheet(f"color: {MUTED}; font-size: 11px;")

        # Suggestions are an overlay list inside the window, not a popup
        # window: a separate top-level popup was positioned wrongly / hidden
        # behind other windows on some setups (WSLg). NoFocus keeps typing in
        # the box; the box's key events drive the list (see eventFilter).
        self._filter_items: list[tuple[str, str]] = []   # (label, text to insert)
        self._filter_list = QListWidget(self._results_body_w)
        self._filter_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._filter_list.setFrameShape(QFrame.Shape.Box)
        self._filter_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._filter_list.setAutoFillBackground(True)
        _pal = self._filter_list.palette()
        _pal.setColor(QPalette.ColorRole.Base, QColor("#FFFFFF"))
        _pal.setColor(QPalette.ColorRole.Highlight, QColor("#E3F2FD"))
        _pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#0D47A1"))
        self._filter_list.setPalette(_pal)
        self._filter_list.setMouseTracking(True)
        self._filter_list.itemEntered.connect(self._filter_list.setCurrentItem)
        self._filter_list.itemClicked.connect(lambda _it: self._accept_filter_suggestion())
        self._filter_list.hide()
        self._filter_edit.installEventFilter(self)

        # Re-evaluate shortly after typing stops, not on every keystroke.
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(250)
        self._filter_timer.timeout.connect(self._commit_filter_text)
        self._filter_edit.textEdited.connect(self._update_filter_suggestions)
        self._filter_edit.textChanged.connect(lambda _t: self._filter_timer.start())

        # Equipment / Consumable filter — the type comes from the CTR section
        # the Stock Code is listed under, so it needs no SAGE file.
        type_lbl = QLabel("Type:")
        type_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._type_combo = QComboBox()
        self._type_combo.addItems(["All types", cost_stats.EQUIPMENT,
                                   cost_stats.CONSUMABLE, cost_stats.UNCATEGORISED])
        self._type_combo.setEnabled(False)
        self._type_combo.setToolTip(
            "Show only Equipment or only Consumable Stock Codes (view only — Excel\n"
            "downloads and saved comparisons still contain everything).\n"
            "The type is the CTR section the Stock Code is listed under; a Stock Code\n"
            "that is in no CTR is Uncategorised.")
        self._type_combo.currentIndexChanged.connect(lambda _=0: self._apply_doc_filter())

        lay.addWidget(lbl)
        lay.addWidget(self._filter_edit, 1)
        lay.addWidget(type_lbl)
        lay.addWidget(self._type_combo)
        lay.addWidget(self._filter_info)
        return row

    def _update_filter_suggestions(self, _text: str = ""):
        edit = self._filter_edit
        prefix = edit.text()[:edit.cursorPosition()]
        fragment = prefix[doc_filter.fragment_start(prefix):]
        # Right after "(" / and / or / not the next name is about to start, so
        # offer everything; only an entirely empty box stays quiet.
        found = (doc_filter.suggest(fragment, self._filter_pairs)
                 if fragment.strip() or prefix.strip() else [])
        self._show_filter_suggestions(found)

    def _show_filter_suggestions(self, found: list[tuple[str, str]]):
        self._filter_items = found
        lst = self._filter_list
        if not found or not self._filter_edit.hasFocus():
            lst.hide()
            return
        lst.clear()
        lst.setTextElideMode(Qt.TextElideMode.ElideMiddle)   # long names keep their ends
        lst.addItems([label for label, _ in found])
        for k, (_label, insert) in enumerate(found):
            lst.item(k).setToolTip(insert)
        lst.setCurrentRow(0)
        row_h = lst.sizeHintForRow(0) if lst.count() else 20
        host = self._results_body_w
        top_left = self._filter_edit.mapTo(host, self._filter_edit.rect().bottomLeft())
        room = max(host.height() - top_left.y() - 4, row_h + 4)
        rows = min(len(found), 10)
        lst.setGeometry(top_left.x(), top_left.y() + 1, self._filter_edit.width(),
                        min(rows * row_h + 4, room))
        lst.show()
        lst.raise_()

    def _hide_filter_suggestions(self):
        self._filter_items = []
        self._filter_list.hide()

    def _accept_filter_suggestion(self):
        row = self._filter_list.currentRow()
        if not self._filter_list.isVisible() or not (0 <= row < len(self._filter_items)):
            log.info("Filter suggestion not accepted: visible=%s row=%s items=%d",
                     self._filter_list.isVisible(), row, len(self._filter_items))
            return
        label, insert = self._filter_items[row]
        edit = self._filter_edit
        text, cur = edit.text(), edit.cursorPosition()
        start = doc_filter.fragment_start(text[:cur])
        log.info("Filter suggestion accepted: %r -> %r (replacing %r)", label, insert, text[start:cur])
        edit.setText(text[:start] + insert + text[cur:])
        edit.setCursorPosition(start + len(insert))
        self._hide_filter_suggestions()      # setText doesn't re-trigger suggestions
        edit.setFocus()
        # A document (not a sheet) was just chosen: offer its sheets right
        # away; Esc, or just typing on, ignores them.
        if not label.startswith(doc_filter.SHEET_LABEL_PREFIX):
            self._show_filter_suggestions(doc_filter.suggest(insert + ">", self._filter_pairs))

    def eventFilter(self, obj, event):
        if obj is self._filter_edit:
            et = event.type()
            lst = self._filter_list
            if et == QEvent.Type.KeyPress and lst.isVisible():
                key = event.key()
                if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                    step = 1 if key == Qt.Key.Key_Down else -1
                    lst.setCurrentRow((lst.currentRow() + step) % lst.count())
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
                    self._accept_filter_suggestion()
                    return True
                if key == Qt.Key.Key_Escape:
                    self._hide_filter_suggestions()
                    return True
            elif et == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Down:
                self._update_filter_suggestions()      # re-open the list on demand
                return self._filter_list.isVisible()
            elif et in (QEvent.Type.FocusOut, QEvent.Type.Hide):
                self._hide_filter_suggestions()
        return super().eventFilter(obj, event)

    def _commit_filter_text(self):
        """Parse the box and apply it. An expression that doesn't parse
        yet (e.g. a bracket still open) leaves the last valid filter on."""
        text = self._filter_edit.text().strip()
        try:
            tree = doc_filter.parse(text)
        except doc_filter.FilterSyntaxError as exc:
            self._filter_info.setText(f'<span style="color:#C62828;">{exc}</span>')
            return
        self._doc_filter_tree = tree
        self._apply_doc_filter()
        # A name that matches no file/sheet makes the table go empty with no
        # explanation (typically a missing ">" before the sheet) — say so.
        hints = [h for t in doc_filter.terms(tree)
                 if (h := doc_filter.explain_unmatched(t, self._filter_pairs))]
        if (hints and not self._filter_has_sheets
                and any(t.sheet is not None for t in doc_filter.terms(tree))):
            fix = ("Click Compare again (the uploaded files were restored with it)"
                   if self._mr_tables and self._ctr_tables else
                   "Upload the MR/CTR files again and Compare")
            hints = ["This comparison has no sheet information (it was saved by an older "
                     f"version), so “>sheet” can't match. {fix} to get sheet filtering."]
        if hints:
            self._filter_info.setText(
                f'<span style="color:#E65100;">&#9888; no match — see status bar</span>  '
                f'{self._filter_info.text()}')
            self._set_status(hints[0])

    def _reset_doc_filter(self):
        """New results: clear the expression and rebuild the suggestions."""
        self._filter_timer.stop()
        self._filter_edit.blockSignals(True)
        self._filter_edit.clear()
        self._filter_edit.blockSignals(False)
        self._doc_filter_tree = None
        self._matched_mask = None
        self._filter_info.setText("")
        self._hide_filter_suggestions()
        pairs: set[tuple[str, str, str]] = set()
        for df in (self._display_df, self._omr_dl, self._octr_dl):
            if df.empty:
                continue
            for side, dcol, scol in (("MR", "MR Document", "MR Sheet"),
                                     ("CTR", "CTR Document", "CTR Sheet")):
                if dcol not in df.columns:
                    continue
                sheets = df[scol] if scol in df.columns else [""] * len(df)
                for d, sh in zip(df[dcol], sheets):
                    if _norm_val(d):
                        pairs.add((side, _norm_val(d), _norm_val(sh)))
        self._filter_pairs = sorted(pairs)
        self._filter_edit.setEnabled(bool(pairs))
        # Comparisons saved before sheet filtering existed carry no sheet
        # names, so ">sheet" can never match and no sheets can be suggested.
        self._filter_has_sheets = any(sh for _s, _d, sh in pairs)
        if pairs and not self._filter_has_sheets:
            self._filter_info.setText(
                '<span style="color:#E65100;">&#9888; no sheet info in these results '
                '(saved by an older version) — run Compare again to filter by sheet</span>')

    def _refresh_type_filter_state(self):
        """New results: reset the Type box, and put the type on the Stock Code
        tooltips of the three detail tabs (the filter's own tooltips are
        applied by _apply_doc_filter)."""
        known = self._types_known()
        self._type_combo.blockSignals(True)
        self._type_combo.setCurrentIndex(0)
        self._type_combo.blockSignals(False)
        self._type_combo.setEnabled(known)
        if not known:
            self._type_combo.setToolTip(
                "Not available for a reopened saved comparison — press Compare with the "
                "original files to filter by Equipment / Consumable.")
        self._refresh_type_tooltips()

    def _refresh_type_tooltips(self):
        self._decorate_views()

    # ── Local cost columns (Matched / Only in MR / Only in CTR) ─────────────────

    _DAYS_KINDS = (cost_stats.EQUIPMENT, cost_stats.SCAFFOLD)

    @staticmethod
    def _with_local_costs(df: pd.DataFrame, ctx: dict | None, total: bool,
                          editable: set | None = None) -> tuple[pd.DataFrame, dict]:
        """`df` with its Days and Local cost columns (re)built at the very end, plus the hover texts for
        the cells that stay blank (a reason each). `ctx` = {"unit": {STOCK CODE: SAGE local_expect_cost},
        "info": {(side, document, sheet, STOCK CODE): (kind, days, type status)}} from the finished
        statistics, or None before they exist (the columns are then blank).

        Days (MR) / Days (CTR): the days of that side's line — for equipment (and scaffold) only.
        Local cost: SAGE's unit cost. With `total`: the total, by the statistics' formula — equipment =
        days × cost × quantity, consumables = cost × quantity — in one column per side
        (Local cost total (MR) / (CTR))."""
        drop = ("Local cost", "Days (")
        base = df[[c for c in df.columns if not str(c).startswith(drop)]].copy()
        tips: dict[tuple[int, str], str] = {}
        if "Stock Code" not in base.columns:
            return base, tips
        ctx = ctx or {"unit": {}, "info": {}}
        keys = base["Stock Code"].map(cost_stats.norm_key)
        unit = keys.map(ctx["unit"]).astype(float)
        nan = float("nan")
        days_cols: dict[str, list] = {}
        cost_cols: dict[str, list] = {}
        for side in ("MR", "CTR"):
            docs = base[f"{side} Document"] if f"{side} Document" in base.columns else pd.Series("", index=base.index)
            sheets = base[f"{side} Sheet"] if f"{side} Sheet" in base.columns else pd.Series("", index=base.index)
            qty = pd.to_numeric(base[f"Qty ({side})"], errors="coerce") if f"Qty ({side})" in base.columns \
                else pd.Series(nan, index=base.index)
            days, cost = [], []
            for n, (k, d, sh, q, u) in enumerate(zip(keys, docs, sheets, qty, unit)):
                kind, dy, status = ctx["info"].get((side, _norm_val(d), _norm_val(sh), k), ("", nan, ""))
                has_line = bool(_norm_val(d))
                equip = kind in MainWindow._DAYS_KINDS
                days.append(dy if equip else nan)
                if equip and has_line and editable is not None:
                    editable.add((n, f"Days ({side})"))              # every equipment line's Days can be edited
                # Why a Days cell is empty — a hover text for every case (the missing ones are also orange).
                if not has_line:
                    tips[(n, f"Days ({side})")] = f"No {side} line for this Stock Code."
                elif equip and pd.isna(dy):
                    tips[(n, f"Days ({side})")] = ("Equipment needs Days (costed and priced × days) and this line has none "
                                                   "yet — type or pick them in the Statistics tab.")
                elif kind == cost_stats.CONSUMABLE:
                    tips[(n, f"Days ({side})")] = ("No days: a consumable is not billed per day — its cost is "
                                                   "cost × quantity.")
                elif not equip:
                    tips[(n, f"Days ({side})")] = ("No days shown: whether days apply depends on the type (Equipment or "
                                                   "Consumable), which is not decided yet — decide it in the Statistics tab.")
                value, why = nan, ""
                if has_line and total:
                    if pd.isna(u):
                        why = "No total: the Stock Code is not in the SAGE Export."
                    elif status == "unknown" or not kind:
                        why = "No total: the type (Equipment or Consumable) is unknown — decide it in the Statistics tab."
                    elif status == "conflict":
                        why = "No total: the type needs a decision — press Stock Code types in the Statistics tab."
                    elif pd.isna(q):
                        why = "No total: there is no quantity."
                    elif equip and pd.isna(dy):
                        why = "No total because the days are missing (equipment = days × cost × quantity) — type or pick them in the Statistics tab."
                    else:
                        value = round(float(u) * float(q) * (float(dy) if equip else 1.0), 2)
                cost.append(value)
                if why:
                    tips[(n, f"Local cost total ({side})")] = why
            days_cols[side], cost_cols[side] = days, cost
        for side in ("MR", "CTR"):                      # Days (MR), Days (CTR), then the Local cost column(s)
            base[f"Days ({side})"] = days_cols[side]
        if total:
            for side in ("MR", "CTR"):
                base[f"Local cost total ({side})"] = cost_cols[side]
        else:
            base["Local cost"] = unit.round(2)
        return base, tips

    @staticmethod
    def _with_local_costs_combined(df: pd.DataFrame, ctx: dict | None, total: bool,
                                   editable: set | None = None) -> tuple[pd.DataFrame, dict]:
        """The Combined View table with its Days and Local cost column(s) at the very end (same names as the
        other tabs). One row is a Stock Code built from all its lines, so per side:
          Days: the days of its equipment lines — one number when they agree, "7 / 182" when they differ
                (hover lists which document says what); blank for consumables.
          Local cost: SAGE's unit cost. With `total`: the sum LINE BY LINE of unit cost × quantity (× days for
                equipment) — the only correct total when the lines carry different days.
        A total that cannot be worked out stays blank, with a hover reason; missing equipment days are orange."""
        drop = ("Local cost", "Days (")
        base = df[[c for c in df.columns if not str(c).startswith(drop)]].copy()
        tips: dict[tuple[int, str], str] = {}
        if "Stock Code" not in base.columns:
            return base, tips
        ctx = ctx or {"unit": {}, "lines": {}}
        keys = base["Stock Code"].map(cost_stats.norm_key)
        unit = keys.map(ctx["unit"]).astype(float)
        nan = float("nan")
        days_cols: dict[str, list] = {"MR": [], "CTR": []}
        cost_cols: dict[str, list] = {"MR": [], "CTR": []}
        equip_kinds = MainWindow._DAYS_KINDS
        for n, (k, u) in enumerate(zip(keys, unit)):
            for side in ("MR", "CTR"):
                lines = ctx["lines"].get(k, {}).get(side, [])
                equip = [ln for ln in lines if ln[3] in equip_kinds]
                dcol = f"Days ({side})"
                if equip and editable is not None:
                    editable.add((n, dcol))                          # typed days apply to all of this side's lines
                # -- days
                if not lines:
                    days_cols[side].append(nan)
                    tips[(n, dcol)] = f"No {side} line for this Stock Code."
                elif equip:
                    known = sorted({float(ln[2]) for ln in equip if pd.notna(ln[2])})
                    missing = any(pd.isna(ln[2]) for ln in equip)
                    if known and not missing and len(known) == 1:
                        days_cols[side].append(known[0])
                    elif known:
                        days_cols[side].append(" / ".join(f"{d:g}" for d in known))
                    else:
                        days_cols[side].append(nan)
                    if missing:
                        tips[(n, dcol)] = ("Equipment needs Days (costed and priced × days) and a line has none yet — "
                                           "type or pick them in the Statistics tab.")
                    elif len(known) > 1:
                        per: dict[str, list] = {}
                        for ln in equip:
                            per.setdefault(f"{float(ln[2]):g}", []).append(ln[0] or "?")
                        tips[(n, dcol)] = "The lines give different days: " + "; ".join(
                            f"{d} days — {', '.join(sorted(set(docs)))}" for d, docs in per.items())
                else:
                    days_cols[side].append(nan)
                    if any(ln[3] == cost_stats.CONSUMABLE for ln in lines):
                        tips[(n, dcol)] = "No days: a consumable is not billed per day — its cost is cost × quantity."
                    else:
                        tips[(n, dcol)] = ("No days shown: whether days apply depends on the type (Equipment or "
                                           "Consumable), which is not decided yet — decide it in the Statistics tab.")
                # -- total
                value, why = nan, ""
                if lines and total:
                    if pd.isna(u):
                        why = "No total: the Stock Code is not in the SAGE Export."
                    elif any(ln[4] == "unknown" or not ln[3] for ln in lines):
                        why = "No total: the type (Equipment or Consumable) is unknown — decide it in the Statistics tab."
                    elif any(ln[4] == "conflict" for ln in lines):
                        why = "No total: the type needs a decision — press Stock Code types in the Statistics tab."
                    elif any(pd.isna(ln[1]) for ln in lines):
                        why = "No total: a line has no quantity."
                    elif any(ln[3] in equip_kinds and pd.isna(ln[2]) for ln in lines):
                        why = ("No total because the days are missing (equipment = days × cost × quantity) — "
                               "type or pick them in the Statistics tab.")
                    else:
                        value = round(sum(float(u) * float(ln[1]) * (float(ln[2]) if ln[3] in equip_kinds else 1.0)
                                          for ln in lines), 2)
                cost_cols[side].append(value)
                if why:
                    tips[(n, f"Local cost total ({side})")] = why
        for side in ("MR", "CTR"):
            base[f"Days ({side})"] = pd.Series(days_cols[side], index=base.index, dtype=object)
        if total:
            for side in ("MR", "CTR"):
                base[f"Local cost total ({side})"] = cost_cols[side]
        else:
            base["Local cost"] = unit.round(2)
        return base, tips

    def _waiting_reason(self) -> str | None:
        """Why the Days / Local cost cells are not filled in yet, while the statistics of THIS compare are
        awaited — None when they are ready, failed, or never expected (a reopened comparison without them)."""
        if getattr(self, "_local_ready", False) or getattr(self, "_local_state", "none") != "waiting":
            return None
        path = self._sage_edit.text().strip()
        if not path:
            return ("Waiting for the SAGE Export — choose it in the field above the tabs. The Days and Local cost "
                    "figures are filled in when the statistics have been calculated.")
        if not Path(path).is_file():
            return f"Waiting for the SAGE Export — the file was not found: {path}"
        return ("Waiting for the statistics to finish — they are calculated in the background after Compare; "
                "the Days and Local cost figures appear when they are done.")

    def _set_stats_tab_busy(self, busy: bool) -> None:
        """A waiting mark on the Statistics tab's title while its figures are being calculated."""
        idx = self._tabs.indexOf(self._stats_tab)
        if idx >= 0:
            self._tabs.setTabText(idx, "Statistics  ⏳" if busy else "Statistics")

    def _local_cost_context(self) -> dict | None:
        """{"unit": SAGE costs, "info": per-line kind / days / type status, "lines": per Stock Code and side
        the lines it is built from} from the finished statistics — None until they are ready for THIS compare
        (a new Compare must not show the previous one's figures)."""
        if not getattr(self, "_local_ready", False):
            return None
        rows = self._stats_panel.effective_rows()
        if rows is None or rows.empty:
            return None
        sage = rows[rows["source"] == cost_stats.SOURCE_SAGE]
        unit = {k: float(v) for k, v in zip(sage["key"], sage["unit_cost"]) if pd.notna(v)}
        info: dict[tuple, tuple] = {}
        lines: dict[str, dict[str, list]] = {}
        for side, doc, sheet, key, kind, days, status, qty in zip(
                rows["side"], rows["doc"], rows["sheet"], rows["key"], rows["kind"], rows["days"],
                rows["type_status"], rows["qty"]):
            info.setdefault((side, _norm_val(doc), _norm_val(sheet), key), (kind, days, status))
            lines.setdefault(key, {"MR": [], "CTR": []})[side].append((_norm_val(doc), qty, days, kind, status))
        return {"unit": unit, "info": info, "lines": lines}

    def _set_local_cost_frames(self, ctx: dict | None) -> None:
        total = self._local_mult_chk.isChecked()
        self._local_tips = {}
        self._local_edit = {}
        for name in ("_display_df", "_omr_dl", "_octr_dl"):
            cells: set = set()
            df, tips = self._with_local_costs(getattr(self, name), ctx, total, cells)
            setattr(self, name, df)
            self._local_tips[name] = tips
            self._local_edit[name] = cells
        cells = set()
        self._combined_df, tips = self._with_local_costs_combined(self._combined_df, ctx, total, cells)
        self._local_tips["_combined_df"] = tips
        self._local_edit["_combined_df"] = cells

    def _table_frames(self):
        """(frame name, view, frame) for the tables that carry Days / Local cost — the Matched table shows
        either the detail frame or, with Combined View on, the combined one."""
        matched = (("_combined_df", self._combined_df) if self._combined_btn.isChecked()
                   else ("_display_df", self._display_df))
        return [(matched[0], self._tab_matched, matched[1]),
                ("_omr_dl", self._tab_only_mr, self._omr_dl),
                ("_octr_dl", self._tab_only_ctr, self._octr_dl)]

    def _decorate_views(self) -> None:
        """Hover texts (type + Days / Local cost reasons) and the orange of missing Days on every table
        that shows a frame with those columns."""
        for name, view, df in self._table_frames():
            proxy = view.model()
            if proxy is None or df.empty:
                continue
            model = proxy.sourceModel()
            if model.rowCount() != len(df):
                continue
            tips = dict(self._type_tips(df))
            tips.update(getattr(self, "_local_tips", {}).get(name, {}))
            editable = getattr(self, "_local_edit", {}).get(name, set())
            for cell in editable:                                   # tell the user the cell can be edited
                tips[cell] = (tips.get(cell, "").rstrip() + "\n" if cell in tips else "") + \
                             "Double-click to edit the days (you will be asked whether to sync the other side)."
            model.set_tooltips(tips)
            reason = self._waiting_reason()
            model.set_waiting("⏳ waiting" if reason else None, reason or "")
            model.set_days_editable(editable)
            model.set_days_needed({k for k, t in getattr(self, "_local_tips", {}).get(name, {}).items()
                                   if k[1].startswith("Days (") and t.startswith("Equipment needs Days")})
            if not getattr(model, "_days_wired", False):
                model.days_edited.connect(lambda r, c, t, n=name: self._on_days_edited(n, r, c, t))
                model._days_wired = True

    def _sync_local_toggle(self, *_a) -> None:
        """The "Local cost total" box only makes sense on Matched / Only in MR / Only in CTR."""
        on = self._tabs.currentWidget() in (self._stack_matched, self._stack_only_mr, self._stack_only_ctr)
        has_results = not (self._display_df.empty and self._omr_dl.empty and self._octr_dl.empty)
        self._local_mult_chk.setVisible(on and has_results)

    def _apply_local_costs(self) -> None:
        """Fill / rebuild the Days and Local cost column(s) of the three tables from the statistics,
        keeping each table's sort, selection and hidden columns. Runs when the statistics finish, when
        the user types or picks days, decides a type, or toggles the "Local cost total" box."""
        if self._display_df is None:
            return
        self._set_local_cost_frames(self._local_cost_context())
        for name, view, df in self._table_frames():
            proxy = view.model()
            if proxy is None or df.empty:
                continue
            model = proxy.sourceModel()
            if model.rowCount() != len(df):
                continue
            tail_names = [c for c in df.columns if str(c).startswith(("Local cost", "Days ("))]
            base_n = len(df.columns) - len(tail_names)
            model.set_tail_columns(base_n, df[tail_names])
            for col in range(base_n, model.columnCount()):
                view.resizeColumnToContents(col)
        self._decorate_views()

    def _on_days_edited(self, frame_name: str, row: int, col_name: str, text: str) -> None:
        """The user typed days into a Days (MR) / Days (CTR) cell of one of the tables: the statistics keep them
        per side, ask whether to sync the other side, and everything is rebuilt from there."""
        frame = getattr(self, frame_name, None)
        if frame is None or row >= len(frame) or col_name not in frame.columns:
            return
        side = "MR" if col_name.endswith("(MR)") else "CTR"
        current = frame.iloc[row][col_name]
        shown = "" if pd.isna(current) else (f"{current:g}" if isinstance(current, (int, float)) else str(current))
        ok, value = self._stats_panel.parse_days(text)
        unchanged = text.strip() == shown or (
            ok and value is not None and isinstance(current, (int, float)) and not pd.isna(current)
            and abs(float(current) - value) < 1e-9)
        if unchanged:
            return                                              # nothing changed (e.g. Enter on "7 / 182" or "10.0")
        if not ok:
            self._set_status(f'"{text}" is not a number of days — nothing was changed.')
            return
        key = cost_stats.norm_key(frame.iloc[row]["Stock Code"])
        self._stats_panel.edit_days(key, side, value)

    def _on_types_changed(self) -> None:
        if self._display_df is None or self._display_df.empty:
            return
        self._type_combo.setEnabled(self._types_known())       # the chosen filter is kept
        self._refresh_type_tooltips()
        self._apply_doc_filter()
        self._apply_local_costs()                              # the kind decides whether days apply

    def _types_known(self) -> bool:
        """The Type box works when the CTR sections are known (a fresh Compare) or the Stock Code
        types list has entries."""
        types = current_types()
        return self._category_by_key is not None or bool(types["equipment"] or types["consumable"])

    def _type_and_source(self, stock_code) -> tuple[str, str]:
        section = (self._category_by_key or {}).get(cost_stats.norm_key(stock_code), cost_stats.UNCATEGORISED)
        kind, source = cost_stats.kind_for(stock_code, section, current_types())
        return (kind or cost_stats.UNCATEGORISED), source

    def _type_of(self, stock_code) -> str:
        return self._type_and_source(stock_code)[0]

    def _type_tips(self, df: pd.DataFrame) -> dict[tuple[int, str], str]:
        """Hover text on each Stock Code cell saying its type and where that
        comes from."""
        if not self._types_known() or df.empty or "Stock Code" not in df.columns:
            return {}
        def why(code) -> str:
            kind, source = self._type_and_source(code)
            if kind == cost_stats.UNCATEGORISED:
                return ("Type: Uncategorised — this Stock Code is not in the Stock Code types list and "
                        "no CTR section lists it (add it in Settings → Stock Code types)")
            where = {"confirmed": "your decision", "list": "the Stock Code types list",
                     "CTR section": "its CTR section"}[source]
            return f"Type: {kind} (from {where})"
        return {(i, "Stock Code"): why(k) for i, k in enumerate(df["Stock Code"])}

    def _type_mask(self, df: pd.DataFrame) -> list[bool] | None:
        """Per-row result of the Type box (None = every row)."""
        want = self._type_combo.currentText()
        if (want == "All types" or not self._types_known()
                or df.empty or "Stock Code" not in df.columns):
            return None
        return [self._type_of(k) == want for k in df["Stock Code"]]

    @staticmethod
    def _and_masks(a: list[bool] | None, b: list[bool] | None) -> list[bool] | None:
        if a is None:
            return b
        if b is None:
            return a
        return [x and y for x, y in zip(a, b)]

    @staticmethod
    def _detail_mask(df: pd.DataFrame, tree) -> list[bool]:
        """Per-row result of the expression for a Matched / Only in MR /
        Only in CTR table (columns MR/CTR Document and Sheet)."""
        def col(name):
            return ([_norm_val(v) for v in df[name]] if name in df.columns
                    else [""] * len(df))
        mr_d, mr_s, ctr_d, ctr_s = (col("MR Document"), col("MR Sheet"),
                                    col("CTR Document"), col("CTR Sheet"))
        cache: dict[tuple, bool] = {}
        mask = []
        for key in zip(mr_d, mr_s, ctr_d, ctr_s):
            if key not in cache:
                mr  = (key[0], key[1]) if key[0] else None
                ctr = (key[2], key[3]) if key[2] else None
                cache[key] = doc_filter.evaluate(tree, mr, ctr)
            mask.append(cache[key])
        return mask

    def _combined_mask_and_tips(self, tree):
        """Combined view: a Stock Code stays when some pairing of one MR
        (document, sheet) with one CTR (document, sheet) that contributed
        to it satisfies the expression — the same pairing a Matched row is.
        Tooltips on the Qty cells say how much of each total comes from the
        documents that satisfied it."""
        def parts(agg, side):
            p = agg.get(f"{side}_parts")
            if p:
                return p
            # Saved before per-sheet breakdowns existed: documents only.
            return [{"doc": d, "sheet": "", "qty": None} for d in agg.get(f"{side}_docs", [])]

        def fmt(x):
            return f"{x:,.10g}"

        mask: list[bool] = []
        tips: dict[tuple[int, str], str] = {}
        for row_idx, key in enumerate(self._combined_df["Stock Code"]):
            agg = self._combined_raw.get(key, {})
            mrs, ctrs = parts(agg, "mr"), parts(agg, "ctr")
            used_mr: set[int] = set()
            used_ctr: set[int] = set()
            for i, m in enumerate([*mrs] or [None]):
                for j, c in enumerate([*ctrs] or [None]):
                    mp = (m["doc"], m["sheet"]) if m else None
                    cp = (c["doc"], c["sheet"]) if c else None
                    if doc_filter.evaluate(tree, mp, cp):
                        if m: used_mr.add(i)
                        if c: used_ctr.add(j)
                        if not m: used_mr.add(-1)
                        if not c: used_ctr.add(-1)
            visible = bool(used_mr or used_ctr)
            mask.append(visible)
            if not visible:
                continue

            sel: dict[str, float | None] = {}
            for side, plist, used, total in (("mr", mrs, used_mr, agg.get("mr_qty")),
                                              ("ctr", ctrs, used_ctr, agg.get("ctr_qty"))):
                chosen = [plist[i] for i in sorted(used) if i >= 0]
                col = "Qty (MR) Total" if side == "mr" else "Qty (CTR) Total"
                label = side.upper()
                src = ", ".join(f"{p['doc']}" + (f" › {p['sheet']}" if p["sheet"] else "")
                                for p in chosen) or "none"
                if not chosen:
                    tips[(row_idx, col)] = f"No {label} document matches the filter for this Stock Code."
                    sel[side] = 0.0
                elif any(p["qty"] is None for p in plist) or total is None:
                    tips[(row_idx, col)] = (f"{label} from selected documents: {src}\n"
                                            "Quantity breakdown isn't available for this row "
                                            "(older saved comparison).")
                    sel[side] = None
                else:
                    got = sum(p["qty"] for p in chosen)
                    pct = f" ({got / total * 100:.0f}%)" if total else ""
                    tips[(row_idx, col)] = (f"{label} from selected documents: {fmt(got)} of {fmt(total)}{pct}\n"
                                            f"{src}")
                    sel[side] = got
            if sel.get("mr") is not None and sel.get("ctr") is not None:
                tips[(row_idx, "Diff (CTR − MR)")] = (
                    f"Selected documents only: CTR {fmt(sel['ctr'])} − MR {fmt(sel['mr'])} "
                    f"= {fmt(sel['ctr'] - sel['mr'])}")
        return mask, tips

    def _apply_doc_filter(self, rehighlight: bool = True):
        """Apply self._doc_filter_tree to every result table (hiding rows —
        the data frames, downloads and saved comparisons are untouched) and
        refresh the Combined tooltips and the counts label."""
        tree = self._doc_filter_tree
        combined = self._combined_btn.isChecked()
        views = [
            (self._tab_only_mr,  self._omr_dl,  "only in MR"),
            (self._tab_only_ctr, self._octr_dl, "only in CTR"),
        ]
        info: list[str] = []
        self._matched_mask = None

        # Matched tab: detail rows, or the Combined rows.
        proxy = self._tab_matched.model()
        if proxy is not None:
            src = proxy.sourceModel()
            if tree is None:
                mask, tips = None, {}
            elif combined:
                mask, tips = self._combined_mask_and_tips(tree)
            else:
                mask, tips = self._detail_mask(self._display_df, tree), {}
            mask = self._and_masks(mask, self._type_mask(self._combined_df if combined
                                                         else self._display_df))
            proxy.set_mask(mask)
            src.set_tooltips({**self._type_tips(self._combined_df if combined
                                                else self._display_df), **tips})
            self._matched_mask = mask
            if mask is not None:
                what = "Stock Codes" if combined else "matched"
                info.append(f"{sum(mask)} of {len(mask)} {what}")

        for view, df, label in views:
            proxy = view.model()
            if proxy is None:
                continue
            mask = None if tree is None or df.empty else self._detail_mask(df, tree)
            mask = self._and_masks(mask, self._type_mask(df))
            proxy.set_mask(mask)
            proxy.sourceModel().set_tooltips(self._type_tips(df))
            if mask is not None:
                info.append(f"{sum(mask)} of {len(mask)} {label}")

        self._filter_info.setText(
            ("Showing " + " · ".join(info)) if info else "")
        self._stats_panel.set_doc_filter(self._stats_doc_pred())
        if rehighlight:
            self._apply_value_highlights(self._compare_vals_btn.isChecked())

    def _row_visible(self, src_row: int) -> bool:
        m = self._matched_mask
        return m is None or (src_row < len(m) and m[src_row])

    # ── Value comparison highlights ───────────────────────────────────────────

    def _apply_value_highlights(self, active: bool):
        proxy = self._tab_matched.model()
        if proxy is None:
            return
        source: PandasModel = proxy.sourceModel()

        if self._combined_btn.isChecked():
            self._apply_combined_doc_highlights(source, active)
            return

        if not active or self._display_df.empty:
            # Qty/Unit and Rate/Rechargeable mismatches are all highlighted
            # only while this toggle is on.
            source.set_highlights({})
            self._mismatch_rows = []
            self._mismatch_pos  = -1
            self._set_mismatch_kinds({})
            self._prev_mm_btn.setEnabled(False)
            self._next_mm_btn.setEnabled(False)
            self._mismatch_lbl.setVisible(False)
            return

        df = self._display_df

        def _norm(v) -> str:
            s = str(v).strip()
            return "" if s.lower() in ("nan", "none", "") else s

        def _differs(a, b) -> bool:
            sa, sb = _norm(a), _norm(b)
            if sa == sb:
                return False
            try:                          # treat "1" and "1.0" as equal
                return float(sa) != float(sb)
            except (ValueError, TypeError):
                return True               # non-numeric: fall back to string compare

        # Rate/Rechargeable mismatches always count as navigable "mismatch
        # rows" here too, alongside the Qty/Unit differences this toggle
        # was originally built for — both are surfaced by the same arrows.
        mismatch_cells: dict[tuple[int, str], str] = {
            c: "mismatch" for c in self._rate_rech_mismatch_cells
        }
        mismatch_row_set: set[int] = {r for r, _ in self._rate_rech_mismatch_cells}

        for col_mr, col_ctr in [("Qty (MR)", "Qty (CTR)"), ("Unit (MR)", "Unit (CTR)")]:
            if col_mr not in df.columns or col_ctr not in df.columns:
                continue
            # Extract each column once and zip, instead of two separate
            # df.iloc[row_idx][col] positional lookups per row — each
            # .iloc[] call reconstructs that row from scratch.
            for row_idx, val_mr, val_ctr in zip(df.index, df[col_mr], df[col_ctr]):
                if _differs(val_mr, val_ctr):
                    mismatch_cells[(row_idx, col_mr)] = "mismatch"
                    mismatch_cells[(row_idx, col_ctr)] = "mismatch"
                    mismatch_row_set.add(row_idx)

        source.set_highlights(mismatch_cells)
        self._mismatch_rows = sorted(r for r in mismatch_row_set if self._row_visible(r))
        self._mismatch_pos  = 0 if self._mismatch_rows else -1
        self._set_mismatch_kinds({r: {"red"} for r in self._mismatch_rows})

        has = len(self._mismatch_rows) > 0
        self._prev_mm_btn.setEnabled(has)
        self._next_mm_btn.setEnabled(has)

        if has:
            n = len(self._mismatch_rows)
            self._mismatch_lbl.setText(
                f'<span style="color:#C62828; font-size:11px;">'
                f'&#9888;&nbsp; {n} value mismatch{"es" if n != 1 else ""}'
                f'</span>')
            self._mismatch_lbl.setVisible(True)
            self._nav_mismatch(0, absolute=True)   # scroll to first match
            self._set_status(
                f"Compare Values — {n} row{'s' if n != 1 else ''} with "
                "Qty, Unit, or Rate/Rechargeable mismatch highlighted in red.")
        else:
            self._mismatch_lbl.setText(
                '<span style="color:#2E7D32; font-size:11px;">'
                '&#10003;&nbsp; All values match'
                '</span>')
            self._mismatch_lbl.setVisible(True)
            self._set_status("Compare Values — no Qty, Unit, or Rate/Rechargeable mismatches found.")

    def _apply_combined_doc_highlights(self, source: "PandasModel", active: bool):
        """Combined-view highlighting. `active` is Show Combined (orange):
        highlights every Stock Code whose combined total was built from more
        than one MR or CTR source document (regardless of whether the MR/CTR
        totals actually differ), and wires the ↑/↓ buttons to step between
        those rows — unlike the old Diff-colouring behaviour this replaced,
        which left row navigation dead in Combined view. The separate
        Compare Values button (_combined_cmp_btn) adds red Qty/Unit cells
        on top and feeds the same ↑/↓ navigation."""
        self._mismatch_rows = []
        self._mismatch_pos  = -1
        self._set_mismatch_kinds({})
        self._prev_mm_btn.setEnabled(False)
        self._next_mm_btn.setEnabled(False)

        cmp_active = self._combined_cmp_btn.isChecked()
        if self._combined_df.empty or not (active or cmp_active):
            source.set_highlights({})
            self._mismatch_lbl.setVisible(False)
            return

        cells: dict[tuple[int, str], str] = {}
        row_set: set[int] = set()
        n_doc = 0
        kinds: dict[int, set[str]] = {}
        highlight_cols = [c for c in ("Stock Code", "Description (MR)", "Description (CTR)",
                                       "MR Document", "CTR Document")
                          if c in self._combined_df.columns]
        for row_idx, key in enumerate(self._combined_df["Stock Code"]):
            agg = self._combined_raw.get(key, {})
            if active and (len(agg.get("mr_docs", [])) > 1 or len(agg.get("ctr_docs", [])) > 1):
                n_doc += 1
                row_set.add(row_idx)
                kinds.setdefault(row_idx, set()).add("orange")
                for col in highlight_cols:
                    cells[(row_idx, col)] = "combined"

        # Compare Values (Combined): Qty totals, and MR-vs-CTR Units. Applied
        # after the document colouring so red wins on any shared cell.
        n_val = 0
        if cmp_active:
            for row_idx, key in enumerate(self._combined_df["Stock Code"]):
                agg = self._combined_raw.get(key, {})
                bad_cols: list[str] = []
                mr_q, ctr_q = agg.get("mr_qty"), agg.get("ctr_qty")
                if (mr_q is None) != (ctr_q is None) or (
                        mr_q is not None and abs(mr_q - ctr_q) > 1e-9):
                    bad_cols += ["Qty (MR) Total", "Qty (CTR) Total", "Diff (CTR − MR)"]
                if sorted(agg.get("mr_units", [])) != sorted(agg.get("ctr_units", [])):
                    bad_cols.append("Unit")
                if bad_cols:
                    n_val += 1
                    row_set.add(row_idx)
                    kinds.setdefault(row_idx, set()).add("red")
                    for col in bad_cols:
                        if col in self._combined_df.columns:
                            cells[(row_idx, col)] = "mismatch"

        rows = sorted(r for r in row_set if self._row_visible(r))
        source.set_highlights(cells)
        self._mismatch_rows = rows
        self._mismatch_pos  = 0 if rows else -1
        self._set_mismatch_kinds({r: kinds.get(r, set()) for r in rows})

        has = len(rows) > 0
        self._prev_mm_btn.setEnabled(has)
        self._next_mm_btn.setEnabled(has)

        parts: list[str] = []
        if n_val:
            parts.append(f'<span style="color:#C62828;">&#9888;&nbsp; {n_val} Qty/Unit mismatch'
                         f'{"es" if n_val != 1 else ""}</span>')
        if n_doc:
            parts.append(f'<span style="color:{_COMBINED_FG.name()};">&#9679;&nbsp; {n_doc} '
                         'Stock Code(s) span multiple MR/CTR documents</span>')
        if has:
            self._mismatch_lbl.setText(
                '<span style="font-size:11px;">' + " &nbsp;·&nbsp; ".join(parts) + '</span>')
            self._mismatch_lbl.setVisible(True)
            self._nav_mismatch(0, absolute=True)   # scroll to first match
            self._set_status(
                f"Combined — {len(rows)} highlighted Stock Code(s): red = Qty/Unit "
                "differs between MR and CTR, orange = built from several documents. "
                "Use ↑ ↓ to step through them.")
        else:
            what = ("No Qty or Unit differences between MR and CTR" if cmp_active
                    else "No Stock Code spans multiple documents")
            self._mismatch_lbl.setText(
                f'<span style="color:#2E7D32; font-size:11px;">&#10003;&nbsp; {what}</span>')
            self._mismatch_lbl.setVisible(True)
            self._set_status(f"Combined — {what.lower()}.")

    def _set_mismatch_kinds(self, kinds: dict[int, set[str]]) -> None:
        """Remember what highlights each navigable row, and show the colour selector only
        when both red and orange rows exist (Combined View)."""
        self._mismatch_kinds = kinds
        has_red = any("red" in k for k in kinds.values())
        has_orange = any("orange" in k for k in kinds.values())
        self._mm_kind_combo.blockSignals(True)
        self._mm_kind_combo.setCurrentIndex(0)                  # back to "All highlighted"
        self._mm_kind_combo.blockSignals(False)
        self._mm_kind_combo.setVisible(has_red and has_orange)

    def _navigable_rows(self) -> list[int]:
        """The highlighted source rows the arrows step through, in the order they are shown
        now (after sorting), limited to the colour chosen in the selector. Rows the document
        filter hides are left out."""
        proxy = self._tab_matched.model()
        if proxy is None:
            return []
        want = self._mm_kind_combo.currentData() if self._mm_kind_combo.isVisible() else "all"
        src = proxy.sourceModel()
        shown = []
        for r in self._mismatch_rows:
            if want != "all" and want not in self._mismatch_kinds.get(r, set()):
                continue
            pr = proxy.mapFromSource(src.index(r, 0)).row()
            if pr >= 0:
                shown.append((pr, r))
        return [r for _, r in sorted(shown)]

    def _nav_mismatch(self, step: int, absolute: bool = False):
        if not self._mismatch_rows:
            return
        proxy = self._tab_matched.model()
        if proxy is None:
            return
        rows = self._navigable_rows()
        if not rows:
            self._set_status("No highlighted rows of that colour to step through.")
            return
        src = proxy.sourceModel()
        n = len(rows)
        if absolute:
            pos = max(0, min(step, n - 1))               # step is the target index
        else:
            # Relative to the row selected on screen, so sorting, clicking elsewhere or
            # filtering never leaves the arrows walking a stale order.
            cur = self._tab_matched.currentIndex().row()
            on_screen = [proxy.mapFromSource(src.index(r, 0)).row() for r in rows]
            if cur < 0:
                pos = 0 if step > 0 else n - 1
            elif step > 0:
                pos = next((i for i, pr in enumerate(on_screen) if pr > cur), 0)
            else:
                pos = next((i for i in range(n - 1, -1, -1) if on_screen[i] < cur), n - 1)
        self._mismatch_pos = pos
        src_row = rows[pos]
        proxy_idx = proxy.mapFromSource(src.index(src_row, 0))
        self._tab_matched.scrollTo(
            proxy_idx, QAbstractItemView.ScrollHint.PositionAtCenter)
        self._tab_matched.setCurrentIndex(proxy_idx)

        combined    = self._combined_btn.isChecked()
        active_df   = self._combined_df if combined else self._display_df
        label       = "Combined item" if combined else "Mismatch"
        self._set_status(
            f"{label} {pos + 1} of {n}  — "
            f"Stock Code: {active_df.iloc[src_row]['Stock Code']}")

    # ── Combined view ────────────────────────────────────────────────────────

    def _refresh_combined_tables(self):
        """Rebuild the Combined / Needs Review / Error Data frames from
        _combined_raw + _combined_decisions, and refresh the tabs that show
        them. Called after every Compare and after every Approve/Reject."""
        rows_combined, rows_review, rows_error = [], [], []

        for key in sorted(self._combined_raw):
            agg = self._combined_raw[key]
            mr_qty, ctr_qty = agg["mr_qty"], agg["ctr_qty"]
            all_units = sorted(set(agg["mr_units"]) | set(agg["ctr_units"]))
            conflict  = len(all_units) > 1
            diff = (ctr_qty - mr_qty) if (mr_qty is not None and ctr_qty is not None) else None

            base = {
                "Stock Code":      key,
                # .get(..., "") — combined_raw restored from a comparison
                # saved before this column existed won't have these keys;
                # degrade to blank rather than failing the whole load.
                "Description (MR)":  agg.get("mr_desc", ""),
                "Description (CTR)": agg.get("ctr_desc", ""),
                "Qty (MR) Total":  mr_qty,
                "Qty (CTR) Total": ctr_qty,
                "Diff (CTR − MR)": diff,
                "Unit":            " / ".join(all_units),
                "MR Document":     ", ".join(agg.get("mr_docs", [])),
                "CTR Document":    ", ".join(agg.get("ctr_docs", [])),
            }

            if not conflict:
                base["Flag"] = ""
                rows_combined.append(base)
                continue

            decision = self._combined_decisions.get(key)
            if decision is True:
                base["Flag"] = "⚠ Unit conflict — approved"
                rows_combined.append(base)
            elif decision is False:
                base["Reason"] = (
                    f"Unit conflict ({', '.join(all_units)}) — "
                    "rejected, excluded from Combined totals"
                )
                rows_error.append(base)
            else:
                rows_review.append(base)

        desc_cols     = ["Description (MR)", "Description (CTR)"]
        combined_cols = ["Stock Code", *desc_cols, "Qty (MR) Total", "Qty (CTR) Total",
                          "Diff (CTR − MR)", "Unit", "MR Document", "CTR Document", "Flag"]
        review_cols   = ["Stock Code", *desc_cols, "Qty (MR) Total", "Qty (CTR) Total",
                          "Diff (CTR − MR)", "Unit"]
        error_cols    = ["Stock Code", *desc_cols, "Qty (MR) Total", "Qty (CTR) Total",
                          "Unit", "Reason"]

        self._combined_df = (
            pd.DataFrame(rows_combined, columns=combined_cols)
              .sort_values("Stock Code").reset_index(drop=True)
            if rows_combined else pd.DataFrame(columns=combined_cols)
        )
        self._combined_review_df = (
            pd.DataFrame(rows_review, columns=review_cols)
              .sort_values("Stock Code").reset_index(drop=True)
            if rows_review else pd.DataFrame(columns=review_cols)
        )
        self._combined_error_df = (
            pd.DataFrame(rows_error, columns=error_cols)
              .sort_values("Stock Code").reset_index(drop=True)
            if rows_error else pd.DataFrame(columns=error_cols)
        )

        cells: set = set()
        self._combined_df, tips = self._with_local_costs_combined(
            self._combined_df, self._local_cost_context(), self._local_mult_chk.isChecked(), cells)
        self._local_tips = getattr(self, "_local_tips", {})
        self._local_tips["_combined_df"] = tips
        self._local_edit = getattr(self, "_local_edit", {})
        self._local_edit["_combined_df"] = cells

        self._fill_review_list()
        self._update_review_tab_badge()
        _show_result(self._stack_error, self._tab_error, self._combined_error_df)

        if self._combined_btn.isChecked():
            _load_view(self._tab_matched, self._combined_df)
            self._apply_doc_filter(rehighlight=False)
            # A fresh model has no highlights — reapply Show Combined's
            # multi-document highlighting if it was on before this rebuild.
            self._apply_value_highlights(self._compare_vals_btn.isChecked())
            self._decorate_views()

    def _update_review_tab_badge(self):
        """So a pending review (unit conflicts, rows with no quantity) is
        visible on the tab bar itself, without having to open the tab."""
        idx = self._tabs.indexOf(self._stack_review)
        n = len(self._combined_review_df) + len(self._missing_qty_df)
        if n:
            self._tabs.setTabText(idx, f"Needs Review  ⚠ {n}")
            self._tabs.tabBar().setTabTextColor(idx, QColor("#E65100"))
        else:
            self._tabs.setTabText(idx, "Needs Review")
            self._tabs.tabBar().setTabTextColor(idx, QColor())

    def _fill_review_list(self):
        lw = self._review_list
        lw.clear()
        if self._combined_review_df.empty and self._missing_qty_df.empty:
            self._stack_review.setCurrentIndex(0)
            return
        self._stack_review.setCurrentIndex(1)

        for _, row in self._combined_review_df.iterrows():
            key = row["Stock Code"]
            agg = self._combined_raw[key]
            mr_units  = ", ".join(agg["mr_units"])  or "—"
            ctr_units = ", ".join(agg["ctr_units"]) or "—"
            label = (
                f"<b>{key}</b> &mdash; MR: {agg['mr_qty']} ({mr_units})"
                f"&nbsp;&nbsp; CTR: {agg['ctr_qty']} ({ctr_units})"
                f"&nbsp;&nbsp; <span style='color:#C62828;'>units disagree, "
                "can't be summed automatically</span>"
            )

            item = QListWidgetItem()
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            row_w = QWidget()
            rl = QHBoxLayout(row_w)
            rl.setContentsMargins(6, 4, 6, 4)
            lbl = QLabel(label)
            lbl.setWordWrap(True)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            rl.addWidget(lbl, 1)

            approve_btn = QPushButton("Approve")
            approve_btn.setToolTip(
                "Include in the Combined totals anyway, flagged as a warning.")
            approve_btn.setFixedWidth(84)
            approve_btn.clicked.connect(lambda _, k=key: self._on_review_decision(k, True))

            reject_btn = QPushButton("Reject")
            reject_btn.setToolTip(
                "Exclude from the Combined totals; move to Error Data.")
            reject_btn.setFixedWidth(84)
            reject_btn.clicked.connect(lambda _, k=key: self._on_review_decision(k, False))

            rl.addWidget(approve_btn)
            rl.addWidget(reject_btn)

            lw.addItem(item)
            lw.setItemWidget(item, row_w)
            item.setSizeHint(row_w.sizeHint())

        self._fill_missing_qty_review(lw)

    def _fill_missing_qty_review(self, lw: QListWidget, cap: int = 500):
        """Rows with no numeric quantity: listed for information (there is
        nothing to approve) — they are left out of the cost statistics until
        a quantity is filled in in the source file."""
        df = self._missing_qty_df
        if df.empty:
            return
        head = QListWidgetItem(
            f"No quantity — {len(df)} row(s) left out of the cost statistics "
            "(fill in the quantity in the source file and Compare again)")
        f = head.font()
        f.setBold(True)
        head.setFont(f)
        head.setForeground(QBrush(QColor("#C62828")))
        head.setFlags(Qt.ItemFlag.ItemIsEnabled)
        lw.addItem(head)
        for n, (side, doc, sheet, key) in enumerate(
                zip(df["side"], df["doc"], df["sheet"], df["key"])):
            if n >= cap:
                more = QListWidgetItem(f"… {len(df) - cap} more (all of them are in the "
                                       "Excel report's “No Quantity” sheet)")
                more.setFlags(Qt.ItemFlag.ItemIsEnabled)
                lw.addItem(more)
                break
            it = QListWidgetItem(f"{key}   ·   {side}   ·   {doc}" + (f"   ·   {sheet}" if sheet else ""))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            it.setForeground(QBrush(QColor("#C62828")))
            lw.addItem(it)

    def _on_review_decision(self, stock_code: str, approved: bool):
        self._combined_decisions[stock_code] = approved
        self._refresh_combined_tables()
        self._set_status(
            f"Stock Code {stock_code} — unit conflict "
            + ("approved, included in Combined totals as a warning."
               if approved else "rejected, moved to Error Data.")
        )

    def _apply_combined_view(self, active: bool):
        if self._tab_matched.model() is None:
            return
        _load_view(self._tab_matched, self._combined_df if active else self._display_df)
        self._apply_doc_filter(rehighlight=False)
        # Compare Values doubles as Show Combined while this view is active —
        # same button/checked-state, different label/behaviour.
        self._set_compare_vals_mode(active)
        self._combined_cmp_btn.setVisible(active)
        # A fresh model from _load_view above has no highlights, so reapply
        # whichever state the toggle was already in for the view we just
        # switched to (Qty/Unit/Rate mismatches in detail, multi-document
        # highlighting in Combined).
        self._apply_value_highlights(self._compare_vals_btn.isChecked())
        self._apply_doc_column_visibility(self._show_docs_btn.isChecked())
        self._decorate_views()

    def _arrange_toolbar(self, combined: bool):
        """Lay out the corner toolbar. In Combined view the Compare Values
        button (relabelled Show Combined) goes after Combined View so the
        two orange-themed buttons aren't mistaken for one another; in the
        detail view it keeps its original place before Combined View."""
        cl = self._corner_layout
        while cl.count():
            cl.takeAt(0)          # detaches the item; widgets stay alive
        cl.addWidget(self._prev_mm_btn)
        cl.addWidget(self._next_mm_btn)
        cl.addWidget(self._mm_kind_combo)
        cl.addSpacing(4)
        if combined:
            cl.addWidget(self._combined_cmp_btn)
            cl.addWidget(self._combined_btn)
            cl.addSpacing(4)
            cl.addWidget(self._compare_vals_btn)
        else:
            cl.addWidget(self._compare_vals_btn)
            cl.addSpacing(4)
            cl.addWidget(self._combined_cmp_btn)
            cl.addWidget(self._combined_btn)
        cl.addSpacing(4)
        cl.addWidget(self._show_docs_btn)
        cl.addSpacing(4)
        cl.addWidget(self._cols_btn)
        cl.addSpacing(4)
        cl.addWidget(self._local_mult_chk)
        cl.addStretch(1)          # left-aligned toolbar row above the tabs

    def _set_compare_vals_mode(self, combined: bool):
        """Compare Values and Show Combined are the same button/state
        (_compare_vals_btn.isChecked(), dispatched in _apply_value_highlights)
        with a different label, tooltip, colour theme, and nav-arrow tooltips
        depending on whether Combined View is active. Normal (detail) view
        keeps 'Compare Values' exactly as it always has."""
        if hasattr(self, "_corner_layout"):
            self._arrange_toolbar(combined)
        if combined:
            self._compare_vals_btn.setText("Show Combined")
            self._compare_vals_btn.setToolTip(
                "Highlight Stock Codes built from more than one MR/CTR document.\n"
                "Use ↑ ↓ to jump between them. Click again to clear.")
            self._compare_vals_btn.setStyleSheet(f"""
                QPushButton {{
                    background: white; color: #E65100;
                    border: 1px solid #E65100; border-radius: 4px;
                    padding: 2px 10px; font-size: 11px;
                    min-height: 24px;
                }}
                QPushButton:checked {{
                    background: #FFE0B2; color: #E65100;
                    border: 1.5px solid #E65100; font-weight: bold;
                }}
                QPushButton:disabled {{ color: {BORDER}; border-color: {BORDER}; }}
            """)
            nav_desc = "Stock Code spanning multiple MR/CTR documents"
        else:
            self._compare_vals_btn.setText("Compare Values")
            self._compare_vals_btn.setToolTip(
                "Highlight rows where Qty or Unit differs between MR and CTR.\n"
                "Use ↑ ↓ to jump between mismatches. Click again to clear.")
            self._compare_vals_btn.setStyleSheet(f"""
                QPushButton {{
                    background: white; color: {PRIMARY};
                    border: 1px solid {PRIMARY}; border-radius: 4px;
                    padding: 2px 10px; font-size: 11px;
                    min-height: 24px;
                }}
                QPushButton:checked {{
                    background: #FFEBEE; color: #C62828;
                    border: 1.5px solid #C62828; font-weight: bold;
                }}
                QPushButton:disabled {{ color: {BORDER}; border-color: {BORDER}; }}
            """)
            nav_desc = "mismatched row  (Qty, Unit, or Rate/Rechargeable differs)"

        self._prev_mm_btn.setToolTip(f"Jump to previous {nav_desc}")
        self._next_mm_btn.setToolTip(f"Jump to next {nav_desc}")

    def _current_result_view(self):
        """The QTableView of the active result tab (None on Needs Review,
        which is a list, not a table)."""
        w = self._tabs.currentWidget()
        v = w.widget(1) if isinstance(w, QStackedWidget) else None
        return v if isinstance(v, QTableView) else None

    def _refresh_cols_btn(self):
        v = self._current_result_view()
        n = len(v._user_hidden) if v is not None else 0
        self._cols_btn.setEnabled(n > 0)
        self._cols_btn.setText(f"Columns ({n})" if n else "Columns")

    def _apply_doc_column_visibility(self, show: bool):
        """MR/CTR 'source document' columns are hidden by default to keep
        the comparison tables compact — this toggles them on the three
        detail views and, when active, on the Combined view too (its MR/CTR
        Document columns list every file that contributed to that row's
        totals, comma-joined). Re-applied after every _load_view/
        _show_result call since a fresh model doesn't necessarily keep the
        previous model's hidden-column state."""
        matched_df = self._combined_df if self._combined_btn.isChecked() else self._display_df
        for view, df in (
            (self._tab_matched,  matched_df),
            (self._tab_only_mr,  self._omr_dl),
            (self._tab_only_ctr, self._octr_dl),
        ):
            if view.model() is None:
                continue
            for col_name in ("MR Document", "CTR Document", "MR Sheet", "CTR Sheet"):
                if col_name in df.columns:
                    idx = df.columns.get_loc(col_name)
                    view.setColumnHidden(idx, not show)

    # ── Download ──────────────────────────────────────────────────────────────

    def _download_report(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Excel report", "mr_ctr_report.xlsx",
            "Excel files (*.xlsx)"
        )
        if not path:
            return
        try:
            buf = BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                self._display_df.to_excel(writer, index=False, sheet_name="Matched")
                self._omr_dl.to_excel(writer,     index=False, sheet_name="Only in MR")
                self._octr_dl.to_excel(writer,    index=False, sheet_name="Only in CTR")
                if not self._combined_df.empty:
                    self._combined_df.to_excel(writer, index=False, sheet_name="Combined")
                if not self._combined_error_df.empty:
                    self._combined_error_df.to_excel(writer, index=False, sheet_name="Error Data")
                # Cost statistics: every row, whatever the document / type
                # filters show (the filters are view-only).
                frames = self._stats_panel.report_frames()
                if frames is not None:
                    frames[0].to_excel(writer, index=False, sheet_name="Statistics")
                    frames[1].to_excel(writer, index=False, sheet_name="Statistics Items")
                # The same rows valued at what the documents bill (Document price).
                pframes = self._stats_panel.price_report_frames()
                if pframes is not None:
                    pframes[0].to_excel(writer, index=False, sheet_name="Price Statistics")
                    pframes[1].to_excel(writer, index=False, sheet_name="Price Items")
                if not self._missing_qty_df.empty:
                    self._missing_qty_df.rename(columns={
                        "side": "Side", "doc": "Document", "sheet": "Sheet", "key": "Stock Code",
                    }).to_excel(writer, index=False, sheet_name="No Quantity")
            Path(path).write_bytes(buf.getvalue())
            log.info("Report saved: %s", path)
            QMessageBox.information(self, "Saved", f"Report saved:\n{path}")
        except Exception as exc:
            log.exception("Failed to save report: %s", path)
            QMessageBox.critical(self, "Save error", str(exc))

    # ── Comparison history ───────────────────────────────────────────────────

    def _save_comparison(self):
        path: str | None = None

        # A preload was loaded (and possibly extended with more files) —
        # ask whether to overwrite it in place or save as a separate file,
        # instead of silently doing either.
        if self._loaded_comparison_path is not None:
            box = QMessageBox(self)
            box.setWindowTitle("Save Comparison")
            box.setText(
                f"This comparison was loaded from:\n{self._loaded_comparison_path.name}\n\n"
                "Save changes to that file, or save as a new comparison?"
            )
            overwrite_btn = box.addButton("Overwrite Existing", QMessageBox.ButtonRole.AcceptRole)
            new_btn       = box.addButton("Save as New…",        QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(overwrite_btn)
            box.exec()
            clicked = box.clickedButton()
            box.deleteLater()
            if clicked is overwrite_btn:
                path = str(self._loaded_comparison_path)
            elif clicked is not new_btn:
                return   # Cancel

        if path is None:
            label = default_label(self._mr_files_used, self._ctr_files_used)
            suggested = suggest_save_path(label)
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Comparison", str(suggested), "JSON files (*.json)"
            )
            if not path:
                return

        try:
            save_comparison(
                Path(path),
                label=Path(path).stem,
                mr_files=self._mr_files_used,
                ctr_files=self._ctr_files_used,
                display_df=self._display_df,
                omr_df=self._omr_dl,
                octr_df=self._octr_dl,
                combined_raw=self._combined_raw,
                combined_decisions=self._combined_decisions,
                mr_tables=self._mr_tables,
                ctr_tables=self._ctr_tables,
                stats=self._stats_snapshot(),
            )
            self._loaded_comparison_path = Path(path)
            log.info("Comparison saved: %s", path)
            QMessageBox.information(self, "Saved", f"Comparison saved:\n{path}")
        except Exception as exc:
            log.exception("Failed to save comparison: %s", path)
            QMessageBox.critical(self, "Save error", str(exc))

    def _load_comparison(self):
        entries = list_saved_comparisons()
        if not entries:
            QMessageBox.information(
                self, "No saved comparisons",
                f"No saved comparisons found in:\n{comparisons_dir()}\n\n"
                "Use \"Save Comparison…\" after a Compare to create one."
            )
            return

        dlg = ComparisonPickerDialog(entries, parent=self)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        selected = dlg.selected_path
        dlg.deleteLater()
        if not accepted or selected is None:
            return
        path = selected

        try:
            data = load_comparison(path)
        except Exception as exc:
            log.exception("Failed to load comparison: %s", path)
            QMessageBox.critical(self, "Load error", str(exc))
            return

        # A saved comparison replaces whatever is currently uploaded (if
        # anything), so that gets wiped first — but unlike before, the
        # comparison's own raw per-sheet tables (if it has any — saves made
        # before this was added won't) are restored right after, so more
        # MR/CTR files can be added and merged in via the normal Browse
        # flow instead of this being a read-only snapshot.
        #
        # Everything below is wrapped in one try/except: load_comparison()
        # succeeding only means the JSON parsed — a hand-edited or
        # otherwise malformed file can still have the wrong shape inside
        # (e.g. a combined_raw entry missing mr_qty/mr_units), which would
        # previously throw partway through this sequence and leave the UI
        # half-reset (already cleared, nothing restored) instead of either
        # fully loading or cleanly failing.
        try:
            self._clear("mr")
            self._clear("ctr")
            self._mr_preview.setModel(None)
            self._ctr_preview.setModel(None)

            self._mr_tables  = data["mr_tables"]
            self._ctr_tables = data["ctr_tables"]
            for lw, tables, fside in (
                (self._mr_file_list,  self._mr_tables,  "mr"),
                (self._ctr_file_list, self._ctr_tables, "ctr"),
            ):
                seen = []
                for t in tables:
                    if t["source_file"] not in seen:
                        seen.append(t["source_file"])
                        self._add_file_row(lw, fside, t["source_file"])
            self._fill_table_list(self._mr_table_list,  self._mr_tables,  "mr")
            self._fill_table_list(self._ctr_table_list, self._ctr_tables, "ctr")
            self._on_table_sel_changed()   # rebuilds _mr_df/_ctr_df from the restored tables
            self._refresh_file_warnings("mr")
            self._refresh_file_warnings("ctr")

            self._display_df                = data["display_df"]
            self._omr_dl                    = data["omr_df"]
            self._octr_dl                   = data["octr_df"]
            self._combined_raw              = data["combined_raw"]
            self._combined_decisions        = data["combined_decisions"]
            self._mr_files_used             = data["mr_files"]
            self._ctr_files_used            = data["ctr_files"]
            self._rate_rech_mismatch_cells = _find_rate_rechargeable_mismatches(self._display_df)
            self._loaded_comparison_path    = path

            self._pending_stats = None
            self._pending_saved_stats = data.get("stats")
            self._present_compare_results()
        except Exception as exc:
            log.exception("Failed to restore comparison: %s", path)
            # Roll all the way back to the same clean, empty state as a
            # fresh app launch, rather than leaving whatever partially
            # applied before the exception sitting on screen.
            self._clear("mr")
            self._clear("ctr")
            self._mr_preview.setModel(None)
            self._ctr_preview.setModel(None)
            self._display_df = pd.DataFrame()
            self._omr_dl      = pd.DataFrame()
            self._octr_dl     = pd.DataFrame()
            self._combined_raw       = {}
            self._combined_decisions = {}
            self._rate_rech_mismatch_cells = set()
            self._loaded_comparison_path   = None
            self._pending_saved_stats      = None
            self._results_w.setVisible(False)
            self._metrics_w.setVisible(False)
            self._download_btn.setEnabled(False)
            self._save_btn.setEnabled(False)
            QMessageBox.critical(
                self, "Load error",
                "This saved comparison appears to be corrupted or in an "
                f"unexpected format and could not be loaded:\n\n{exc}"
            )
            return

        log.info("Comparison loaded: %s", path)
        extend_note = (
            "" if self._mr_tables or self._ctr_tables else
            " (saved before file-adding support — upload fresh files and "
            "Compare instead of extending this one)"
        )
        self._set_status(
            f"Loaded comparison “{data['label']}” "
            f"(saved {data['timestamp'] or 'unknown time'}) — "
            f"{len(self._display_df)} matched · "
            f"{len(self._omr_dl)} only in MR · "
            f"{len(self._octr_dl)} only in CTR."
            f"{extend_note}"
        )

    # ── CTR Generator ─────────────────────────────────────────────────────────

    def _open_ctr_tools(self):
        self._main_tabs.setCurrentIndex(1)

    # ── Utility ───────────────────────────────────────────────────────────────

    _RESULTS_HEADER_H = 44   # just enough for the title + Maximize/Hide row

    def _toggle_results_visibility(self, hidden: bool):
        """Hides/shows the results body in place — the header (with this
        very button) stays visible so there's always a way back. The
        underlying _display_df/_omr_dl/_octr_dl/etc. are untouched, so this
        never forces a recompute. Re-running Compare is the only thing that
        recomputes.

        Shrinks the results pane down to just the header row (handing the
        freed space to the upload/controls pane above) instead of leaving a
        blank gap the same size as the old results table. Whatever the
        split looked like right before hiding — including Maximize — is
        remembered and restored exactly on Show, instead of always
        snapping back to the default 30/70."""
        if hidden:
            self._pre_hide_sizes     = self._splitter.sizes()
            self._pre_hide_maximized = self._maximize_btn.isChecked()
            if self._pre_hide_maximized:
                self._maximize_btn.blockSignals(True)
                self._maximize_btn.setChecked(False)
                self._maximize_btn.blockSignals(False)
                self._maximize_btn.setText("⛶  Maximize")
                self._set_controls_chrome_visible(True)

        self._results_body_w.setVisible(not hidden)
        self._collapse_btn.setText("▼  Show results" if hidden else "▲  Hide results")
        # Maximizing an empty (header-only) results pane isn't meaningful.
        self._maximize_btn.setEnabled(not hidden)

        total = self._splitter.height()
        if hidden:
            self._splitter.setSizes(
                [total - self._RESULTS_HEADER_H, self._RESULTS_HEADER_H])
        elif self._pre_hide_maximized:
            # Reuse the Maximize toggle itself so controls-pane visibility,
            # button text, and splitter sizing all end up consistent —
            # rather than re-deriving that logic here.
            self._pre_hide_maximized = False
            self._maximize_btn.setChecked(True)
        elif self._pre_hide_sizes is not None:
            self._splitter.setSizes(self._pre_hide_sizes)
        else:
            self._splitter.setSizes([int(total * 0.30), int(total * 0.70)])

    def _set_controls_chrome_visible(self, visible: bool):
        """Toggles everything Maximize hides besides the results panel
        itself — the step indicator, its separator line, the pinned
        Compare/Load row, and the scrollable MR/CTR file-list pane — so
        maximizing covers the whole tab, not just the file-list area.
        Shared by the Maximize toggle itself and the two places that need
        to restore this same "normal" chrome (Hide-results cancelling a
        prior Maximize, and the post-Compare/Load state reset)."""
        self._step_bar.setVisible(visible)
        self._step_sep.setVisible(visible)
        self._pinned_row.setVisible(visible)
        self._controls_scroll.setVisible(visible)

    def _toggle_maximize_results(self, maximized: bool):
        """Hides the step indicator, pinned Compare/Load row, and upload/
        controls pane so the results panel takes up the whole tab.
        Restoring brings all of it back and re-applies the normal ~30/70
        split."""
        self._set_controls_chrome_visible(not maximized)
        self._maximize_btn.setText("⤡  Restore" if maximized else "⛶  Maximize")
        total = self._splitter.height()
        if maximized:
            self._splitter.setSizes([0, total])
        else:
            self._splitter.setSizes([int(total * 0.30), int(total * 0.70)])

    def _set_status(self, msg: str):
        self.statusBar().showMessage(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from common.app_logging import setup_logging
    log_dir = setup_logging()
    log.info("Application starting (log directory: %s)", log_dir)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Force a light palette/colour scheme explicitly, rather than letting
    # Qt inherit whatever the OS reports, as one layer of defense against
    # popups (tooltips, QMessageBox, QDialog, combo-box dropdowns)
    # rendering with a dark/black palette on a system in dark mode.
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    _palette = QPalette()
    _palette.setColor(QPalette.ColorRole.Window,          QColor("#F0F0F0"))
    _palette.setColor(QPalette.ColorRole.WindowText,      QColor("#212121"))
    _palette.setColor(QPalette.ColorRole.Base,             QColor("#FFFFFF"))
    _palette.setColor(QPalette.ColorRole.AlternateBase,   QColor("#F5F5F5"))
    _palette.setColor(QPalette.ColorRole.Text,             QColor("#212121"))
    _palette.setColor(QPalette.ColorRole.Button,           QColor("#F0F0F0"))
    _palette.setColor(QPalette.ColorRole.ButtonText,      QColor("#212121"))
    _palette.setColor(QPalette.ColorRole.ToolTipBase,     QColor("#FAFAFA"))
    _palette.setColor(QPalette.ColorRole.ToolTipText,     QColor("#212121"))
    _palette.setColor(QPalette.ColorRole.BrightText,      QColor("#FF0000"))
    _palette.setColor(QPalette.ColorRole.Link,             QColor(PRIMARY))
    _palette.setColor(QPalette.ColorRole.Highlight,       QColor(PRIMARY))
    _palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    app.setPalette(_palette)

    # Explicit backgrounds for every kind of popup/floating widget too —
    # belt-and-suspenders alongside the palette fix above, and this is
    # also what actually gives tooltips/menus/dropdowns their border and
    # padding (the palette alone only fixes the colour, not the shape).
    #
    # Deliberately no border-radius or opacity on QToolTip: either one
    # makes Qt's stylesheet engine treat the popup as needing per-pixel
    # alpha compositing (an ARGB top-level window, so rounded corners can
    # show the desktop through them) instead of a plain opaque one. Seen
    # in practice as the *entire* tooltip rendering solid black with none
    # of this styling applied at all — happened identically for a native
    # Windows-installed build and one run directly under WSL, which rules
    # out a WSL/X11-without-compositor cause specifically and points at
    # this instead: whatever's compositing ARGB windows in both of those
    # environments doesn't handle it, so keep every popup rule here fully
    # opaque (no border-radius, no opacity) to avoid needing it at all.
    app.setStyleSheet("""
        QToolTip {
            background-color: #FAFAFA;
            color: #212121;
            border: 1px solid #BDBDBD;
            padding: 4px 6px;
        }
        QMenu {
            background-color: #FAFAFA;
            color: #212121;
            border: 1px solid #BDBDBD;
        }
        QMenu::item:selected {
            background-color: #E3F2FD;
        }
        QComboBox QAbstractItemView {
            background-color: #FAFAFA;
            color: #212121;
            border: 1px solid #BDBDBD;
            selection-background-color: #E3F2FD;
            outline: none;
        }
        QDialog, QMessageBox {
            background-color: #F0F0F0;
        }
    """)
    win = MainWindow()
    win.show()
    log.info("Main window shown")
    exit_code = app.exec()
    log.info("Application exiting with code %s", exit_code)
    sys.exit(exit_code)
