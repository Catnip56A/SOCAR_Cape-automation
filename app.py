"""
app.py — SOCAR Cape MR vs CTR Comparator (PySide6 desktop)

Run:   python app.py
Build: pyinstaller app.spec   (Windows only, see justfile)
"""

import logging
import re
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QSettings, QSortFilterProxyModel,
    Qt, QThread, Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressDialog, QPushButton, QScrollArea,
    QSplitter, QStackedWidget, QStyle, QStyledItemDelegate, QTabWidget,
    QTableView, QVBoxLayout, QWidget,
)

sys.path.insert(0, str(Path(__file__).parent))
from sheet_parser import parse_workbook
from comparison_history import (
    comparisons_dir, default_label, list_saved_comparisons, load_comparison,
    save_comparison, suggest_save_path,
)
from ctr_generator import __version__ as _CTR_VERSION
from ctr_generator.window import CTRGeneratorWidget
from ctr_generator.tracker_window import CTRTrackerWidget

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
    for row_idx, row in display_df.iterrows():
        rech = _norm_val(row["Rechargeable"]).upper()
        if rech not in ("RECHARGE", "NONRECHARG"):
            continue
        rate_status = _rate_implied_status(row["Rate (CTR)"])
        if rate_status is None or rate_status == rech:
            continue
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
) -> dict[str, dict]:
    """Group MR and CTR rows by Stock Code (restricted to matched_keys) and
    sum Qty per side. Returns {key: {"mr_qty", "mr_units", "ctr_qty",
    "ctr_units"}} — *_units is the sorted set of distinct non-blank units
    contributing to that side's total, so more than one entry signals a
    unit conflict the caller must resolve before trusting the sum."""

    def _agg_side(df: pd.DataFrame, qty_col: str | None, unit_col: str | None) -> dict:
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
            result[key] = {"qty": qty_total, "units": units}
        return result

    mr_agg  = _agg_side(mr,  qty_mr,  unit_mr)
    ctr_agg = _agg_side(ctr, qty_ctr, unit_ctr)

    raw: dict[str, dict] = {}
    for key in matched_keys:
        m = mr_agg.get(key,  {"qty": None, "units": []})
        c = ctr_agg.get(key, {"qty": None, "units": []})
        raw[key] = {
            "mr_qty": m["qty"],   "mr_units":  m["units"],
            "ctr_qty": c["qty"],  "ctr_units": c["units"],
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
    if "(MR)" in col_name or col_name in ("MR Document", "Rechargeable", "Allocation"):
        return "mr"
    if "(CTR)" in col_name or col_name == "CTR Document":
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

_MISMATCH_BG = QColor("#FFCDD2")   # light red   — mismatched cell / negative diff
_MISMATCH_FG = QColor("#B71C1C")   # dark red    — mismatched cell / negative diff text
_POSITIVE_BG = QColor("#C8E6C9")   # light green — positive diff (Combined view)
_POSITIVE_FG = QColor("#1B5E20")   # dark green  — positive diff text

# Highlight "kind" -> (background, foreground). A cell's highlight kind is
# looked up in this map wherever a colour is actually needed, so adding a
# new kind (e.g. the Combined view's Diff colouring) never touches the
# mismatch-detection logic that decides *which* cells get one.
_HIGHLIGHT_COLORS = {
    "mismatch": (_MISMATCH_BG, _MISMATCH_FG),
    "negative": (_MISMATCH_BG, _MISMATCH_FG),
    "positive": (_POSITIVE_BG, _POSITIVE_FG),
}


class PandasModel(QAbstractTableModel):
    def __init__(self, df: pd.DataFrame, parent=None):
        super().__init__(parent)
        self._df = df.reset_index(drop=True)
        self._highlights: dict[tuple[int, str], str] = {}  # (src_row, col_name) -> kind

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
        col_name = str(self._df.columns[index.column()])
        val      = self._df.iloc[index.row(), index.column()]

        if role == Qt.ItemDataRole.DisplayRole:
            if isinstance(val, float) and pd.isna(val):
                return ""
            return str(val) if val is not None else ""

        # A highlight (mismatch, or a Combined-view Diff colour) overrides
        # all other cell colours.
        kind = self._highlights.get((index.row(), col_name))
        if role == Qt.ItemDataRole.UserRole:
            return kind
        if kind:
            bg, fg = _HIGHLIGHT_COLORS[kind]
            if role == Qt.ItemDataRole.BackgroundRole:
                return QBrush(bg)
            if role == Qt.ItemDataRole.ForegroundRole:
                return QBrush(fg)

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
            col_name = str(self._df.columns[section])
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


class ResultTableDelegate(QStyledItemDelegate):
    """Keeps a highlighted cell's colour visible even when the row is
    selected.

    Qt's selection layer is normally opaque and paints over BackgroundRole.
    For highlighted cells we take over paint(), draw the highlight colour
    first, then lay a semi-transparent blue tint so the selection is still
    perceptible, and finally draw the text in the highlight's foreground.
    """
    _SEL_TINT = QColor(25, 118, 210, 45)   # PRIMARY at ~18 % opacity

    def paint(self, painter, option, index):
        kind = index.data(Qt.ItemDataRole.UserRole)
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)

        if kind and is_selected:
            bg, fg = _HIGHLIGHT_COLORS[kind]
            painter.save()
            painter.fillRect(option.rect, bg)
            painter.fillRect(option.rect, self._SEL_TINT)
            text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
            painter.setPen(fg)
            painter.drawText(
                option.rect.adjusted(6, 0, -4, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                text)
            painter.restore()
        else:
            super().paint(painter, option, index)


def _make_view(compact: bool = False) -> QTableView:
    v = QTableView()
    v.setAlternatingRowColors(True)
    v.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    v.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    v.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    v.horizontalHeader().setStretchLastSection(True)
    v.verticalHeader().setVisible(False)
    v.setSortingEnabled(not compact)
    if compact:
        v.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
    return v


def _load_view(view: QTableView, df: pd.DataFrame):
    proxy = QSortFilterProxyModel()
    proxy.setSourceModel(PandasModel(df))
    proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    view.setModel(proxy)
    view.resizeColumnsToContents()


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
        self.resize(560, 360)
        self.selected_path: Path | None = None

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"{len(entries)} saved comparison{'s' if len(entries) != 1 else ''}:"))

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        for entry in entries:
            mr_names  = ", ".join(Path(f).name for f in entry["mr_files"])  or "—"
            ctr_names = ", ".join(Path(f).name for f in entry["ctr_files"]) or "—"
            item = QListWidgetItem(f"{entry['label']}\n{entry['timestamp'] or 'unknown time'}")
            item.setToolTip(f"MR:  {mr_names}\nCTR: {ctr_names}\n\n{entry['path']}")
            item.setData(Qt.ItemDataRole.UserRole, entry["path"])
            self._list.addItem(item)
        self._list.itemDoubleClicked.connect(self._accept_current)
        lay.addWidget(self._list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Open).setEnabled(False)
        buttons.accepted.connect(self._accept_current)
        buttons.rejected.connect(self.reject)
        self._list.itemSelectionChanged.connect(
            lambda: buttons.button(QDialogButtonBox.StandardButton.Open).setEnabled(
                bool(self._list.selectedItems())
            )
        )
        lay.addWidget(buttons)

        if entries:
            self._list.setCurrentRow(0)

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
    f = lbl.font(); f.setPointSize(22); f.setBold(True); lbl.setFont(f)
    lbl.setStyleSheet(f"color: {color};")
    QVBoxLayout(box).addWidget(lbl)
    return box, lbl


# ─────────────────────────────────────────────────────────────────────────────
# Background parse worker
# ─────────────────────────────────────────────────────────────────────────────

class ParseWorker(QThread):
    finished = Signal(list)
    error    = Signal(str)

    def __init__(self, payloads: list[tuple[str, bytes]], parent=None):
        super().__init__(parent)
        self._payloads = payloads

    def run(self):
        log.info("ParseWorker: parsing %d file(s)", len(self._payloads))
        try:
            results = []
            for name, data in self._payloads:
                results.extend(parse_workbook(BytesIO(data), filename=name))
            log.info("ParseWorker: produced %d table(s)", len(results))
            self.finished.emit(results)
        except Exception as exc:
            log.exception("ParseWorker failed")
            self.error.emit(str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

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
        self._rate_rech_mismatch_cells: set[tuple[int, str]] = set()

        # Combined view: per-Stock-Code MR/CTR qty totals for matched keys.
        self._combined_raw:      dict[str, dict] = {}   # key -> {mr_qty, mr_units, ctr_qty, ctr_units}
        self._combined_decisions: dict[str, bool] = {}  # key -> True (approved) / False (rejected)
        self._combined_df        = pd.DataFrame()
        self._combined_review_df = pd.DataFrame()
        self._combined_error_df  = pd.DataFrame()

        # Source file names behind the current results — for labelling a
        # saved comparison. Populated by a fresh Compare or by loading one.
        self._mr_files_used:  list[str] = []
        self._ctr_files_used: list[str] = []

        self._build_ui()

        s = QSettings("SOCAR", "CTRGenerator")
        geom = s.value("window/geometry")
        if geom:
            self.restoreGeometry(geom)

    def closeEvent(self, event):
        s = QSettings("SOCAR", "CTRGenerator")
        s.setValue("window/geometry", self.saveGeometry())
        self._ctr_gen.save_settings()
        self._ctr_tracker.save_settings()
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

        # Main vertical splitter
        self._splitter = QSplitter(Qt.Orientation.Vertical)
        _crl.addWidget(self._splitter)

        # ── Top: scrollable controls ───────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        ctrl = QWidget()
        ctrl.setStyleSheet("background: transparent;")
        cl = QVBoxLayout(ctrl)
        cl.setSpacing(10)
        cl.setContentsMargins(2, 4, 2, 4)
        scroll.setWidget(ctrl)
        self._splitter.addWidget(scroll)

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

        # Hint + Compare button (full width, below columns)
        self._compare_hint = QLabel(
            "Upload at least one MR and one CTR file to continue."
        )
        self._compare_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._compare_hint.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        cl.addWidget(self._compare_hint)

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
        self._compare_btn.clicked.connect(self._run_compare)
        cl.addWidget(self._compare_btn)

        # Alternative to uploading + comparing: reopen a past comparison
        # directly. Lives here (not with Download/Save below) so it works
        # even before anything's been uploaded.
        or_lbl = QLabel("— or —")
        or_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        or_lbl.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        cl.addWidget(or_lbl)

        self._load_btn = QPushButton("Load Comparison…")
        self._load_btn.setFixedHeight(36)
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
                font-size: 12px;
            }}
            QPushButton:hover {{ background: {MR_LIGHT}; }}
        """)
        self._load_btn.clicked.connect(self._load_comparison)
        cl.addWidget(self._load_btn)

        # ── Bottom: results (hidden until first compare) ───────────────────
        self._results_w = QWidget()
        res_l = QVBoxLayout(self._results_w)
        res_l.setContentsMargins(0, 4, 0, 0)
        res_l.setSpacing(6)
        self._splitter.addWidget(self._results_w)
        self._results_w.setVisible(False)

        # Results header bar
        res_header = QHBoxLayout()
        res_title = QLabel("Results")
        rf = res_title.font(); rf.setBold(True); rf.setPointSize(11)
        res_title.setFont(rf)
        res_title.setStyleSheet(f"color: {MUTED};")
        collapse_btn = QPushButton("▲  Hide results")
        collapse_btn.setFlat(True)
        collapse_btn.setToolTip("Collapse the results panel.")
        collapse_btn.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        collapse_btn.clicked.connect(self._collapse_results)
        res_header.addWidget(res_title)
        res_header.addStretch()
        res_header.addWidget(collapse_btn)
        res_l.addLayout(res_header)

        hline = QFrame()
        hline.setFrameShape(QFrame.Shape.HLine)
        hline.setStyleSheet(f"color: {BORDER};")
        res_l.addWidget(hline)

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
        res_l.addWidget(self._metrics_w)

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
            "Turn on Combined View — stock codes whose MR/CTR rows disagree "
            "on Unit will appear here for manual approval.")
        self._stack_error,   self._tab_error     = _result_stack(
            "Rejected unit-conflict entries will appear here.")

        self._tabs.addTab(self._stack_matched,  "Matched")
        self._tabs.addTab(self._stack_only_mr,  "Only in MR")
        self._tabs.addTab(self._stack_only_ctr, "Only in CTR")
        self._tabs.addTab(self._stack_review,   "Needs Review")
        self._tabs.addTab(self._stack_error,    "Error Data")

        # Corner toolbar: navigate between mismatches + toggle highlight
        _corner = QWidget()
        _cl = QHBoxLayout(_corner)
        _cl.setContentsMargins(0, 2, 6, 0)
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
        self._prev_mm_btn = QPushButton("↑")
        self._prev_mm_btn.setToolTip(
            "Jump to previous mismatched row  (Qty, Unit, or Rate/Rechargeable differs)")
        self._prev_mm_btn.setStyleSheet(nav_css)
        self._prev_mm_btn.setEnabled(False)
        self._prev_mm_btn.clicked.connect(lambda: self._nav_mismatch(-1))

        self._next_mm_btn = QPushButton("↓")
        self._next_mm_btn.setToolTip(
            "Jump to next mismatched row  (Qty, Unit, or Rate/Rechargeable differs)")
        self._next_mm_btn.setStyleSheet(nav_css)
        self._next_mm_btn.setEnabled(False)
        self._next_mm_btn.clicked.connect(lambda: self._nav_mismatch(+1))

        self._compare_vals_btn = QPushButton("Compare Values")
        self._compare_vals_btn.setCheckable(True)
        self._compare_vals_btn.setEnabled(False)
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
        self._compare_vals_btn.toggled.connect(self._apply_value_highlights)

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

        _cl.addWidget(self._prev_mm_btn)
        _cl.addWidget(self._next_mm_btn)
        _cl.addSpacing(4)
        _cl.addWidget(self._compare_vals_btn)
        _cl.addSpacing(4)
        _cl.addWidget(self._combined_btn)
        self._tabs.setCornerWidget(_corner)

        res_l.addWidget(self._tabs)

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

        res_l.addLayout(export_row)

        # ── Tab 1: CTR Generator ────────────────────────────────────────────
        self._ctr_gen = CTRGeneratorWidget(parent=self)
        self._main_tabs.addTab(self._ctr_gen, "CTR Generator")
        self._ctr_gen.restore_settings()

        # ── Tab 2: CTR Tracker ───────────────────────────────────────────────
        self._ctr_tracker = CTRTrackerWidget(parent=self)
        self._main_tabs.addTab(self._ctr_tracker, "CTR Tracker")
        self._ctr_tracker.restore_settings()

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
        file_lw.setFixedHeight(54)
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
        log.info("%s: browsing %d file(s)", side.upper(), len(paths))
        payloads: list[tuple[str, bytes]] = []
        for p in paths:
            try:
                payloads.append((Path(p).name, Path(p).read_bytes()))
            except Exception as exc:
                log.exception("Failed to read file: %s", p)
                QMessageBox.warning(self, "File error",
                                    f"Cannot read {Path(p).name}:\n{exc}")
        if not payloads:
            return
        lw = self._mr_file_list if side == "mr" else self._ctr_file_list
        for name, _ in payloads:
            lw.addItem(name)
        self._set_status(f"Parsing {side.upper()} files…")
        self._parse(side, payloads)

    def _clear(self, side: str):
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
        self._update_state()

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _parse(self, side: str, payloads: list[tuple[str, bytes]]):
        dlg = QProgressDialog(f"Parsing {side.upper()} files…", None, 0, 0, self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.show()

        worker = ParseWorker(payloads, parent=self)
        self._workers.append(worker)

        def _done(tables: list):
            dlg.close()
            if side == "mr":
                self._mr_tables.extend(tables)
                self._fill_table_list(self._mr_table_list, self._mr_tables)
            else:
                self._ctr_tables.extend(tables)
                self._fill_table_list(self._ctr_table_list, self._ctr_tables)
            self._on_table_sel_changed()

        def _err(msg: str):
            dlg.close()
            QMessageBox.critical(self, "Parse error", msg)
            self._set_status("Error parsing file — see dialog.")

        worker.finished.connect(_done)
        worker.error.connect(_err)
        worker.start()

    def _fill_table_list(self, lw: QListWidget, tables: list):
        lw.clear()
        for t in tables:
            stem  = Path(t["source_file"]).stem
            label = f"{stem} › {t['table_name']}  ({len(t['data'])} rows)"
            tip   = (f"File: {t['source_file']}\n"
                     f"Sheet: {t['source_sheet']}\n"
                     f"Rows: {len(t['data'])}")
            item = QListWidgetItem()
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            lw.addItem(item)
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setToolTip(tip)
            # connect after setChecked so the initial toggle doesn't fire yet
            cb.toggled.connect(lambda _: self._on_table_sel_changed())
            lw.setItemWidget(item, cb)
            item.setSizeHint(cb.sizeHint())

    # ── Table selection ───────────────────────────────────────────────────────

    def _on_table_sel_changed(self):
        self._mr_df  = self._concat_checked(self._mr_table_list,  self._mr_tables)
        self._ctr_df = self._concat_checked(self._ctr_table_list, self._ctr_tables)
        self._refresh_inline_previews()
        self._update_state()

    def _concat_checked(self, lw: QListWidget, tables: list) -> pd.DataFrame:
        parts = []
        for i in range(min(lw.count(), len(tables))):
            w = lw.itemWidget(lw.item(i))
            if isinstance(w, QCheckBox) and w.isChecked():
                parts.append(tables[i]["data"])
        return pd.concat(parts, ignore_index=True, sort=False) if parts else pd.DataFrame()

    # ── Settings ──────────────────────────────────────────────────────────────

    # ── Inline previews ───────────────────────────────────────────────────────

    def _refresh_inline_previews(self):
        def _vis(df: pd.DataFrame) -> pd.DataFrame:
            return df[[c for c in df.columns if not c.startswith("_")]]

        if not self._mr_df.empty:
            _load_view(self._mr_preview,  _vis(self._mr_df))
        if not self._ctr_df.empty:
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

    def _run_compare(self):
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
            return out.sort_values("Stock Code").reset_index(drop=True)

        self._display_df = _build_display(matched)
        self._omr_dl     = _build_display(only_mr_df)
        self._octr_dl    = _build_display(only_ctr_df)

        # Rate (CTR) vs Rechargeable (MR) consistency check — always runs on
        # Compare, independent of the Compare Values toggle below.
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

        # Fresh Compare = fresh decisions; a stock code approved/rejected
        # before this run no longer applies once the underlying data changed.
        matched_keys = set(matched["_KEY_"])
        self._combined_raw = _aggregate_combined(
            mr, ctr, qty_mr_raw, unit_mr_raw, qty_ctr_raw, unit_ctr_raw, matched_keys)
        self._combined_decisions = {}
        self._mr_files_used  = sorted(mr["_SourceFile"].dropna().unique().tolist()) \
            if "_SourceFile" in mr.columns else []
        self._ctr_files_used = sorted(ctr["_SourceFile"].dropna().unique().tolist()) \
            if "_SourceFile" in ctr.columns else []

        self._present_compare_results()

        now = datetime.now().strftime("%H:%M")
        n_rate_rech = len({r for r, _ in self._rate_rech_mismatch_cells})
        rate_rech_note = (
            f" · {n_rate_rech} Rate/Rechargeable mismatch{'es' if n_rate_rech != 1 else ''}"
            if n_rate_rech else ""
        )
        self._set_status(
            f"Compared at {now} — "
            f"{len(matched)} matched · "
            f"{len(only_mr_df)} only in MR · "
            f"{len(only_ctr_df)} only in CTR"
            f"{rate_rech_note}."
        )
        log.info("Compare finished: matched=%d only_mr=%d only_ctr=%d",
                  len(matched), len(only_mr_df), len(only_ctr_df))

    def _present_compare_results(self):
        """Populates the results panel — metrics, the three main tabs, the
        Rate/Rechargeable baseline highlight, and the Combined/Needs
        Review/Error Data tabs — from whatever is currently in
        self._display_df/_omr_dl/_octr_dl/_combined_raw/_combined_decisions/
        _rate_rech_mismatch_cells. Shared by a fresh Compare and by
        reopening a saved comparison, so both end up in the same state."""
        self._refresh_combined_tables()

        self._m_matched.setText(str(len(self._display_df)))
        self._m_only_mr.setText(str(len(self._omr_dl)))
        self._m_only_ctr.setText(str(len(self._octr_dl)))
        self._metrics_w.setVisible(True)

        _show_result(self._stack_matched,  self._tab_matched,  self._display_df)
        _show_result(self._stack_only_mr,  self._tab_only_mr,  self._omr_dl)
        _show_result(self._stack_only_ctr, self._tab_only_ctr, self._octr_dl)

        if not self._display_df.empty:
            self._tab_matched.model().sourceModel().set_highlights(
                {c: "mismatch" for c in self._rate_rech_mismatch_cells})

        self._tabs.setCurrentWidget(self._stack_matched)
        self._download_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        self._step_bar.set_step(2)

        # Reset value-comparison state (new data loaded, old highlights gone)
        self._compare_vals_btn.blockSignals(True)
        self._compare_vals_btn.setChecked(False)
        self._compare_vals_btn.blockSignals(False)
        self._compare_vals_btn.setEnabled(not self._display_df.empty)
        self._mismatch_rows = []
        self._mismatch_pos  = -1
        self._prev_mm_btn.setEnabled(False)
        self._next_mm_btn.setEnabled(False)
        self._mismatch_lbl.setVisible(False)

        # Reset combined-view state (new data loaded, old grouping gone)
        self._combined_btn.blockSignals(True)
        self._combined_btn.setChecked(False)
        self._combined_btn.blockSignals(False)
        self._combined_btn.setEnabled(bool(self._combined_raw))

        # Expand results panel to take ~70 % of the window height
        if not self._results_w.isVisible():
            self._results_w.setVisible(True)
        total = self._splitter.height()
        self._splitter.setSizes([int(total * 0.30), int(total * 0.70)])

    # ── Value comparison highlights ───────────────────────────────────────────

    def _apply_value_highlights(self, active: bool):
        proxy = self._tab_matched.model()
        if proxy is None:
            return
        source: PandasModel = proxy.sourceModel()

        if self._combined_btn.isChecked():
            self._apply_combined_diff_highlights(source, active)
            return

        if not active or self._display_df.empty:
            # Turning this off doesn't clear the Rate/Rechargeable check —
            # that one runs unconditionally on every Compare (see
            # _run_compare), this toggle only adds/removes the Qty/Unit layer.
            source.set_highlights({c: "mismatch" for c in self._rate_rech_mismatch_cells})
            self._mismatch_rows = []
            self._mismatch_pos  = -1
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
            for row_idx in range(len(df)):
                if _differs(df.iloc[row_idx][col_mr], df.iloc[row_idx][col_ctr]):
                    mismatch_cells[(row_idx, col_mr)] = "mismatch"
                    mismatch_cells[(row_idx, col_ctr)] = "mismatch"
                    mismatch_row_set.add(row_idx)

        source.set_highlights(mismatch_cells)
        self._mismatch_rows = sorted(mismatch_row_set)
        self._mismatch_pos  = 0 if self._mismatch_rows else -1

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

    def _apply_combined_diff_highlights(self, source: "PandasModel", active: bool):
        """Compare Values, applied to the Combined view: colours the Diff
        column green where CTR's total is higher than MR's, red where it's
        lower, instead of the detail view's Qty/Unit/Rate mismatch
        highlighting. No row navigation here — the arrows stay tied to the
        detail view's per-row mismatches."""
        self._mismatch_rows = []
        self._mismatch_pos  = -1
        self._prev_mm_btn.setEnabled(False)
        self._next_mm_btn.setEnabled(False)

        diff_col = "Diff (CTR − MR)"
        if not active or self._combined_df.empty or diff_col not in self._combined_df.columns:
            source.set_highlights({})
            self._mismatch_lbl.setVisible(False)
            return

        cells: dict[tuple[int, str], str] = {}
        positive = negative = 0
        for row_idx, val in enumerate(self._combined_df[diff_col]):
            if val is None or (isinstance(val, float) and pd.isna(val)):
                continue
            if val > 0:
                cells[(row_idx, diff_col)] = "positive"
                positive += 1
            elif val < 0:
                cells[(row_idx, diff_col)] = "negative"
                negative += 1

        source.set_highlights(cells)

        if positive or negative:
            self._mismatch_lbl.setText(
                f'<span style="color:#1B5E20; font-size:11px;">'
                f'&#9650; {positive} CTR &gt; MR</span>'
                f'&nbsp;&nbsp;'
                f'<span style="color:#C62828; font-size:11px;">'
                f'&#9660; {negative} CTR &lt; MR</span>'
            )
            self._mismatch_lbl.setVisible(True)
            self._set_status(
                f"Compare Values — Combined totals: {positive} Stock Code(s) with "
                f"CTR higher than MR (green), {negative} with CTR lower than MR (red).")
        else:
            self._mismatch_lbl.setText(
                '<span style="color:#2E7D32; font-size:11px;">'
                '&#10003;&nbsp; All combined totals match'
                '</span>')
            self._mismatch_lbl.setVisible(True)
            self._set_status("Compare Values — no MR/CTR total differences in Combined view.")

    def _nav_mismatch(self, step: int, absolute: bool = False):
        if not self._mismatch_rows:
            return
        n = len(self._mismatch_rows)
        if absolute:
            self._mismatch_pos = step          # step is the target index
        else:
            self._mismatch_pos = (self._mismatch_pos + step) % n

        src_row = self._mismatch_rows[self._mismatch_pos]
        proxy   = self._tab_matched.model()
        if proxy is None:
            return
        src_idx   = proxy.sourceModel().index(src_row, 0)
        proxy_idx = proxy.mapFromSource(src_idx)
        self._tab_matched.scrollTo(
            proxy_idx, QAbstractItemView.ScrollHint.PositionAtCenter)
        self._tab_matched.setCurrentIndex(proxy_idx)
        self._set_status(
            f"Mismatch {self._mismatch_pos + 1} of {n}  — "
            f"Stock Code: {self._display_df.iloc[src_row]['Stock Code']}")

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
                "Qty (MR) Total":  mr_qty,
                "Qty (CTR) Total": ctr_qty,
                "Diff (CTR − MR)": diff,
                "Unit":            " / ".join(all_units),
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

        combined_cols = ["Stock Code", "Qty (MR) Total", "Qty (CTR) Total",
                          "Diff (CTR − MR)", "Unit", "Flag"]
        review_cols   = ["Stock Code", "Qty (MR) Total", "Qty (CTR) Total",
                          "Diff (CTR − MR)", "Unit"]
        error_cols    = ["Stock Code", "Qty (MR) Total", "Qty (CTR) Total",
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

        self._fill_review_list()
        self._update_review_tab_badge()
        _show_result(self._stack_error, self._tab_error, self._combined_error_df)

        if self._combined_btn.isChecked():
            _load_view(self._tab_matched, self._combined_df)
            # A fresh model has no highlights — reapply Compare Values'
            # green/red Diff colouring if it was on before this rebuild.
            self._apply_value_highlights(self._compare_vals_btn.isChecked())

    def _update_review_tab_badge(self):
        """So a pending unit-conflict review is visible on the tab bar
        itself, without having to open the tab to find out."""
        idx = self._tabs.indexOf(self._stack_review)
        n = len(self._combined_review_df)
        if n:
            self._tabs.setTabText(idx, f"Needs Review  ⚠ {n}")
            self._tabs.tabBar().setTabTextColor(idx, QColor("#E65100"))
        else:
            self._tabs.setTabText(idx, "Needs Review")
            self._tabs.tabBar().setTabTextColor(idx, QColor())

    def _fill_review_list(self):
        lw = self._review_list
        lw.clear()
        if self._combined_review_df.empty:
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
        # Compare Values works in both views (Qty/Unit/Rate mismatches in
        # detail, green/red Diff colouring in Combined) — a fresh model from
        # _load_view above has no highlights, so reapply whichever state it
        # was already in for the view we just switched to.
        self._apply_value_highlights(self._compare_vals_btn.isChecked())

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
            Path(path).write_bytes(buf.getvalue())
            log.info("Report saved: %s", path)
            QMessageBox.information(self, "Saved", f"Report saved:\n{path}")
        except Exception as exc:
            log.exception("Failed to save report: %s", path)
            QMessageBox.critical(self, "Save error", str(exc))

    # ── Comparison history ───────────────────────────────────────────────────

    def _save_comparison(self):
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
            )
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
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.selected_path is None:
            return
        path = dlg.selected_path

        try:
            data = load_comparison(path)
        except Exception as exc:
            log.exception("Failed to load comparison: %s", path)
            QMessageBox.critical(self, "Load error", str(exc))
            return

        # A saved comparison is a complete, self-contained snapshot — it
        # doesn't correspond to whatever is currently uploaded (if
        # anything), so that gets wiped rather than left sitting stale next
        # to results from a different comparison entirely.
        self._clear("mr")
        self._clear("ctr")
        self._mr_preview.setModel(None)
        self._ctr_preview.setModel(None)

        self._display_df                = data["display_df"]
        self._omr_dl                    = data["omr_df"]
        self._octr_dl                   = data["octr_df"]
        self._combined_raw              = data["combined_raw"]
        self._combined_decisions        = data["combined_decisions"]
        self._mr_files_used             = data["mr_files"]
        self._ctr_files_used            = data["ctr_files"]
        self._rate_rech_mismatch_cells = _find_rate_rechargeable_mismatches(self._display_df)

        self._present_compare_results()

        log.info("Comparison loaded: %s", path)
        self._set_status(
            f"Loaded comparison “{data['label']}” "
            f"(saved {data['timestamp'] or 'unknown time'}) — "
            f"{len(self._display_df)} matched · "
            f"{len(self._omr_dl)} only in MR · "
            f"{len(self._octr_dl)} only in CTR."
        )

    # ── CTR Generator ─────────────────────────────────────────────────────────

    def _open_ctr_generator(self):
        self._main_tabs.setCurrentIndex(1)

    # ── Utility ───────────────────────────────────────────────────────────────

    def _collapse_results(self):
        self._results_w.setVisible(False)

    def _set_status(self, msg: str):
        self.statusBar().showMessage(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from app_logging import setup_logging
    log_dir = setup_logging()
    log.info("Application starting (log directory: %s)", log_dir)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # Suppress the black shadow artifact that appears on X11/WSL without a
    # compositor. Setting an explicit stylesheet disables Qt's drop-shadow
    # and gives the tooltip a clean, solid border instead.
    app.setStyleSheet("""
        QToolTip {
            background-color: #FAFAFA;
            color: #212121;
            border: 1px solid #BDBDBD;
            padding: 4px 6px;
            border-radius: 3px;
            opacity: 255;
        }
    """)
    win = MainWindow()
    win.show()
    log.info("Main window shown")
    exit_code = app.exec()
    log.info("Application exiting with code %s", exit_code)
    sys.exit(exit_code)
