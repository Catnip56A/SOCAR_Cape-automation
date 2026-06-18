"""
app.py — SOCAR Cape MR vs CTR Comparator (PySide6 desktop)

Run:   python app.py
Build: pyinstaller app.spec   (Windows only, see justfile)
"""

import re
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel,
    Qt, QThread, Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressDialog, QPushButton, QScrollArea,
    QSplitter, QStackedWidget, QTabWidget,
    QTableView, QVBoxLayout, QWidget,
)

sys.path.insert(0, str(Path(__file__).parent))
from sheet_parser import parse_workbook


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

class PandasModel(QAbstractTableModel):
    def __init__(self, df: pd.DataFrame, parent=None):
        super().__init__(parent)
        self._df = df.reset_index(drop=True)

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


def _result_stack(empty_msg: str) -> tuple[QStackedWidget, QTableView]:
    """Returns (stack, view). Stack index 0 = empty state, 1 = table."""
    stack = QStackedWidget()
    stack.addWidget(EmptyState(empty_msg))
    view = _make_view()
    stack.addWidget(view)
    return stack, view


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
        try:
            results = []
            for name, data in self._payloads:
                results.extend(parse_workbook(BytesIO(data), filename=name))
            self.finished.emit(results)
        except Exception as exc:
            self.error.emit(str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MR vs CTR Comparator — SOCAR Cape")
        self.resize(1440, 920)

        self._mr_tables:  list = []
        self._ctr_tables: list = []
        self._mr_df  = pd.DataFrame()
        self._ctr_df = pd.DataFrame()
        self._display_df = pd.DataFrame()
        self._omr_dl     = pd.DataFrame()
        self._octr_dl    = pd.DataFrame()
        self._workers:   list = []

        self._build_ui()
        self._set_status("Upload MR and CTR files to begin.")

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(12, 8, 12, 4)
        rl.setSpacing(6)

        # Step indicator
        self._step_bar = StepIndicator(
            ["Upload Files", "Select Tables", "Compare & Review"]
        )
        rl.addWidget(self._step_bar)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {BORDER};")
        rl.addWidget(sep)

        # Main vertical splitter
        self._splitter = QSplitter(Qt.Orientation.Vertical)
        rl.addWidget(self._splitter)

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
        self._mr_preview,    self._mr_key_cb,       \
        self._mr_filter = self._add_side_column(
            two_col, "mr",
            MR_COLOR,
            file_tip  = "Select one or more MR Excel files (.xlsx / .xlsm).",
            table_tip = "Check the sheets to include.\nUncheck any you want to exclude.",
            key_tip   = "Column used to match rows between MR and CTR.\nUsually 'Stock Code'.",
            filt_tip  = "Optional — comma-separated stock codes to limit the comparison.",
        )
        self._ctr_file_list, self._ctr_table_list, \
        self._ctr_preview,   self._ctr_key_cb,     \
        self._ctr_filter = self._add_side_column(
            two_col, "ctr",
            CTR_COLOR,
            file_tip  = "Select one or more CTR Excel files (.xlsx / .xlsm).",
            table_tip = "Check the sheets to include.\nUncheck any you want to exclude.",
            key_tip   = "Column used to match rows between MR and CTR.\nUsually 'Stock Code' or 'Equipment Code'.",
            filt_tip  = "Optional — comma-separated stock codes to limit the comparison.",
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

        self._tabs.addTab(self._stack_matched,  "Matched")
        self._tabs.addTab(self._stack_only_mr,  "Only in MR")
        self._tabs.addTab(self._stack_only_ctr, "Only in CTR")
        res_l.addWidget(self._tabs)

        # Download button
        self._download_btn = QPushButton("Download Excel Report…")
        self._download_btn.setEnabled(False)
        self._download_btn.setFixedHeight(36)
        self._download_btn.setToolTip(
            "Save a .xlsx report with three sheets:\n"
            "Matched, Only in MR, Only in CTR."
        )
        self._download_btn.setStyleSheet(f"""
            QPushButton {{
                background: white;
                color: {PRIMARY};
                border: 1.5px solid {PRIMARY};
                border-radius: 6px;
                font-size: 12px;
            }}
            QPushButton:hover    {{ background: {MR_LIGHT}; }}
            QPushButton:disabled {{ color: {BORDER}; border-color: {BORDER}; }}
        """)
        self._download_btn.clicked.connect(self._download_report)
        res_l.addWidget(self._download_btn)


    def _add_side_column(
        self,
        parent: QHBoxLayout,
        side: str,
        color: str,
        file_tip: str,
        table_tip: str,
        key_tip: str,
        filt_tip: str,
    ) -> tuple:
        """Build one full MR or CTR column and add it to parent layout.
        Returns (file_list, table_list, preview_view, key_cb, filter_le).
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

        # ── Step 3: settings ──
        sb = QGroupBox(f"Step 3 — {label} Settings")
        sb.setStyleSheet(_group_css(color))
        sbl = QFormLayout(sb)
        key_cb = QComboBox()
        key_cb.setToolTip(key_tip)
        filt_le = QLineEdit()
        filt_le.setPlaceholderText("e.g. AS1500100000, AS1500200000")
        filt_le.setToolTip(filt_tip)
        sbl.addRow("Join key:", key_cb)
        sbl.addRow("Filter:", filt_le)
        col.addWidget(sb)

        parent.addLayout(col)
        return file_lw, table_lw, preview, key_cb, filt_le

    # ── File browsing ─────────────────────────────────────────────────────────

    def _browse(self, side: str):
        paths, _ = QFileDialog.getOpenFileNames(
            self, f"Select {side.upper()} files", "",
            "Excel files (*.xlsx *.xls *.xlsm)"
        )
        if not paths:
            return
        payloads: list[tuple[str, bytes]] = []
        for p in paths:
            try:
                payloads.append((Path(p).name, Path(p).read_bytes()))
            except Exception as exc:
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
        self._refresh_settings()
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
        self._refresh_settings()
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

    def _refresh_settings(self):
        mr_cols  = [c for c in self._mr_df.columns  if not c.startswith("_")]
        ctr_cols = [c for c in self._ctr_df.columns if not c.startswith("_")]
        for cb, cols in [(self._mr_key_cb, mr_cols), (self._ctr_key_cb, ctr_cols)]:
            cb.blockSignals(True)
            cb.clear()
            cb.addItems(cols)
            default = find_key_col(cols)
            if default:
                cb.setCurrentText(default)
            cb.blockSignals(False)

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
            self._set_status("Upload MR and CTR files to begin.")
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
        mr_key  = self._mr_key_cb.currentText()
        ctr_key = self._ctr_key_cb.currentText()
        if not mr_key or not ctr_key:
            QMessageBox.warning(self, "No join key",
                                "Select a join key for both MR and CTR.")
            return

        mr  = self._mr_df.copy()
        ctr = self._ctr_df.copy()

        mr["_KEY_"]  = mr[mr_key].astype(str).str.strip().str.upper()
        ctr["_KEY_"] = ctr[ctr_key].astype(str).str.strip().str.upper()

        bad = {"", "NAN", "-", "NONE", "STOCKCODE", "0"}
        mr  = mr[~mr["_KEY_"].isin(bad)]
        ctr = ctr[~ctr["_KEY_"].isin(bad)]

        for df, text in [(mr,  self._mr_filter.text().strip()),
                         (ctr, self._ctr_filter.text().strip())]:
            if text:
                codes = {c.strip().upper() for c in text.split(",") if c.strip()}
                df.drop(df[~df["_KEY_"].isin(codes)].index, inplace=True)

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
            # Source documents
            out["MR Document"]  = df[src_mr].values  if src_mr  in df.columns else ""
            out["CTR Document"] = df[src_ctr].values if (src_ctr and src_ctr in df.columns) else ""
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
            return out.sort_values("Stock Code").reset_index(drop=True)

        self._display_df = _build_display(matched)
        self._omr_dl     = _build_display(only_mr_df)
        self._octr_dl    = _build_display(only_ctr_df)

        # Update metrics
        self._m_matched.setText(str(len(matched)))
        self._m_only_mr.setText(str(len(only_mr_df)))
        self._m_only_ctr.setText(str(len(only_ctr_df)))
        self._metrics_w.setVisible(True)

        # Populate result stacks
        def _show(stack, view, df, empty_df=None):
            if df.empty:
                stack.setCurrentIndex(0)
            else:
                _load_view(view, df)
                stack.setCurrentIndex(1)

        _show(self._stack_matched,  self._tab_matched,  self._display_df)
        _show(self._stack_only_mr,  self._tab_only_mr,  self._omr_dl)
        _show(self._stack_only_ctr, self._tab_only_ctr, self._octr_dl)

        self._tabs.setCurrentWidget(self._stack_matched)
        self._download_btn.setEnabled(True)
        self._step_bar.set_step(2)

        # Expand results panel to take ~70 % of the window height
        if not self._results_w.isVisible():
            self._results_w.setVisible(True)
        total = self._splitter.height()
        self._splitter.setSizes([int(total * 0.30), int(total * 0.70)])

        now = datetime.now().strftime("%H:%M")
        self._set_status(
            f"Compared at {now} — "
            f"{len(matched)} matched · "
            f"{len(only_mr_df)} only in MR · "
            f"{len(only_ctr_df)} only in CTR."
        )

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
            Path(path).write_bytes(buf.getvalue())
            QMessageBox.information(self, "Saved", f"Report saved:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Save error", str(exc))

    # ── Utility ───────────────────────────────────────────────────────────────

    def _collapse_results(self):
        self._results_w.setVisible(False)

    def _set_status(self, msg: str):
        self.statusBar().showMessage(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
