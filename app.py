"""
app.py — SOCAR Cape MR vs CTR Comparator (PySide6 desktop)

Run:   python app.py
Build: pyinstaller app.spec        (Windows only, see justfile)
"""

import re
import sys
from io import BytesIO
from pathlib import Path

import pandas as pd
from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel,
    Qt, QThread, Signal,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressDialog, QPushButton, QScrollArea,
    QSplitter, QTabWidget, QTableView, QVBoxLayout,
    QWidget,
)

sys.path.insert(0, str(Path(__file__).parent))
from sheet_parser import parse_workbook


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers (mirrors main.py)
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
# DataFrame → QTableView adapter
# ─────────────────────────────────────────────────────────────────────────────

class PandasModel(QAbstractTableModel):
    def __init__(self, df: pd.DataFrame, parent=None):
        super().__init__(parent)
        self._df = df.reset_index(drop=True)

    def rowCount(self, parent=QModelIndex()):
        return len(self._df)

    def columnCount(self, parent=QModelIndex()):
        return len(self._df.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        val = self._df.iloc[index.row(), index.column()]
        if isinstance(val, float) and pd.isna(val):
            return ""
        return str(val) if val is not None else ""

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return str(self._df.columns[section])
        return str(section + 1)


def _make_view() -> QTableView:
    view = QTableView()
    view.setAlternatingRowColors(True)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    view.horizontalHeader().setStretchLastSection(True)
    view.verticalHeader().setVisible(False)
    view.setSortingEnabled(True)
    return view


def _load_view(view: QTableView, df: pd.DataFrame):
    proxy = QSortFilterProxyModel()
    proxy.setSourceModel(PandasModel(df))
    proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    view.setModel(proxy)
    view.resizeColumnsToContents()


_EMPTY_INFO = pd.DataFrame({"(none)": []})


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
        self.resize(1440, 900)

        self._mr_tables:  list = []
        self._ctr_tables: list = []
        self._mr_df  = pd.DataFrame()
        self._ctr_df = pd.DataFrame()
        # kept for download
        self._display_df = pd.DataFrame()
        self._omr_dl     = pd.DataFrame()
        self._octr_dl    = pd.DataFrame()
        self._workers: list = []   # prevent GC of running threads

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root_widget = QWidget()
        self.setCentralWidget(root_widget)
        root_layout = QVBoxLayout(root_widget)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Vertical)
        root_layout.addWidget(splitter)

        # ── Top: scrollable controls ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMaximumHeight(500)
        ctrl = QWidget()
        ctrl_layout = QVBoxLayout(ctrl)
        ctrl_layout.setSpacing(8)
        scroll.setWidget(ctrl)
        splitter.addWidget(scroll)

        # File upload row
        file_row = QHBoxLayout()
        self._mr_file_list  = self._add_file_group("MR File(s)",  "mr",  file_row)
        self._ctr_file_list = self._add_file_group("CTR File(s)", "ctr", file_row)
        ctrl_layout.addLayout(file_row)

        # Table selection row
        table_row = QHBoxLayout()
        self._mr_table_list  = self._add_table_group("MR Tables",  table_row)
        self._ctr_table_list = self._add_table_group("CTR Tables", table_row)
        ctrl_layout.addLayout(table_row)

        # Settings
        settings_box = QGroupBox("Comparison settings")
        s_layout = QVBoxLayout(settings_box)

        key_row = QHBoxLayout()
        mr_form = QFormLayout()
        self._mr_key_cb = QComboBox()
        self._mr_filter = QLineEdit()
        self._mr_filter.setPlaceholderText("Stock codes to include (comma-separated)")
        mr_form.addRow("Join key (MR):", self._mr_key_cb)
        mr_form.addRow("Filter (MR):",   self._mr_filter)
        key_row.addLayout(mr_form)
        key_row.addSpacing(32)

        ctr_form = QFormLayout()
        self._ctr_key_cb = QComboBox()
        self._ctr_filter = QLineEdit()
        self._ctr_filter.setPlaceholderText("Stock codes to include (comma-separated)")
        ctr_form.addRow("Join key (CTR):", self._ctr_key_cb)
        ctr_form.addRow("Filter (CTR):",   self._ctr_filter)
        key_row.addLayout(ctr_form)
        s_layout.addLayout(key_row)

        ctrl_layout.addWidget(settings_box)

        # Compare button
        self._compare_btn = QPushButton("Compare")
        self._compare_btn.setFixedHeight(38)
        self._compare_btn.setEnabled(False)
        f = self._compare_btn.font()
        f.setBold(True)
        self._compare_btn.setFont(f)
        self._compare_btn.clicked.connect(self._run_compare)
        ctrl_layout.addWidget(self._compare_btn)

        # ── Bottom: results ──
        results_widget = QWidget()
        res_layout = QVBoxLayout(results_widget)
        res_layout.setContentsMargins(0, 4, 0, 0)
        res_layout.setSpacing(4)
        splitter.addWidget(results_widget)

        # Metrics row
        metrics_row = QHBoxLayout()
        for attr, label in [
            ("_m_matched",  "Matched keys"),
            ("_m_only_mr",  "Only in MR"),
            ("_m_only_ctr", "Only in CTR"),
        ]:
            box = QGroupBox(label)
            bl = QVBoxLayout(box)
            lbl = QLabel("—")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mf = lbl.font()
            mf.setPointSize(20)
            mf.setBold(True)
            lbl.setFont(mf)
            bl.addWidget(lbl)
            metrics_row.addWidget(box)
            setattr(self, attr, lbl)
        res_layout.addLayout(metrics_row)

        # Result tabs
        self._tabs = QTabWidget()
        self._tab_prev_mr  = _make_view()
        self._tab_prev_ctr = _make_view()
        self._tab_matched  = _make_view()
        self._tab_only_mr  = _make_view()
        self._tab_only_ctr = _make_view()
        for view, title in [
            (self._tab_prev_mr,  "Preview MR"),
            (self._tab_prev_ctr, "Preview CTR"),
            (self._tab_matched,  "Matched"),
            (self._tab_only_mr,  "Only in MR"),
            (self._tab_only_ctr, "Only in CTR"),
        ]:
            self._tabs.addTab(view, title)
        res_layout.addWidget(self._tabs)

        self._download_btn = QPushButton("Download Excel report…")
        self._download_btn.setEnabled(False)
        self._download_btn.clicked.connect(self._download_report)
        res_layout.addWidget(self._download_btn)

        splitter.setSizes([500, 400])

    def _add_file_group(self, title: str, side: str,
                        parent: QHBoxLayout) -> QListWidget:
        box = QGroupBox(title)
        bl = QVBoxLayout(box)
        btn_row = QHBoxLayout()
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: self._browse(side))
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self._clear(side))
        btn_row.addWidget(browse)
        btn_row.addWidget(clear)
        bl.addLayout(btn_row)
        lw = QListWidget()
        lw.setFixedHeight(60)
        lw.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        bl.addWidget(lw)
        parent.addWidget(box)
        return lw

    def _add_table_group(self, title: str,
                         parent: QHBoxLayout) -> QListWidget:
        box = QGroupBox(title)
        bl = QVBoxLayout(box)
        lw = QListWidget()
        lw.setFixedHeight(110)
        lw.itemChanged.connect(self._on_table_sel_changed)
        bl.addWidget(lw)
        parent.addWidget(box)
        return lw

    # ── File browsing ─────────────────────────────────────────────────────────

    def _browse(self, side: str):
        paths, _ = QFileDialog.getOpenFileNames(
            self, f"Select {side.upper()} file(s)", "",
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
        file_lw = self._mr_file_list if side == "mr" else self._ctr_file_list
        for name, _ in payloads:
            file_lw.addItem(name)
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
        self._update_btn()

    # ── Background parsing ────────────────────────────────────────────────────

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

        worker.finished.connect(_done)
        worker.error.connect(_err)
        worker.start()

    def _fill_table_list(self, lw: QListWidget, tables: list):
        lw.blockSignals(True)
        lw.clear()
        for t in tables:
            stem = Path(t["source_file"]).stem
            item = QListWidgetItem(
                f"{stem} › {t['table_name']}  ({len(t['data'])} rows)"
            )
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            lw.addItem(item)
        lw.blockSignals(False)

    # ── Table selection → rebuild working DataFrames ──────────────────────────

    def _on_table_sel_changed(self):
        self._mr_df  = self._concat_checked(self._mr_table_list,  self._mr_tables)
        self._ctr_df = self._concat_checked(self._ctr_table_list, self._ctr_tables)
        self._refresh_settings()
        self._refresh_preview()
        self._update_btn()

    def _concat_checked(self, lw: QListWidget, tables: list) -> pd.DataFrame:
        parts = [
            tables[i]["data"]
            for i in range(lw.count())
            if lw.item(i).checkState() == Qt.CheckState.Checked and i < len(tables)
        ]
        return pd.concat(parts, ignore_index=True, sort=False) if parts else pd.DataFrame()

    # ── Settings ──────────────────────────────────────────────────────────────

    def _refresh_settings(self):
        mr_cols  = [c for c in self._mr_df.columns  if not c.startswith("_")]
        ctr_cols = [c for c in self._ctr_df.columns if not c.startswith("_")]

        for cb, cols, default in [
            (self._mr_key_cb,  mr_cols,  find_key_col(mr_cols)),
            (self._ctr_key_cb, ctr_cols, find_key_col(ctr_cols)),
        ]:
            cb.blockSignals(True)
            cb.clear()
            cb.addItems(cols)
            if default and default in cols:
                cb.setCurrentText(default)
            cb.blockSignals(False)


    # ── Preview ───────────────────────────────────────────────────────────────

    def _refresh_preview(self):
        def _vis(df: pd.DataFrame) -> pd.DataFrame:
            return df[[c for c in df.columns if not c.startswith("_")]]

        if not self._mr_df.empty:
            _load_view(self._tab_prev_mr,  _vis(self._mr_df))
        if not self._ctr_df.empty:
            _load_view(self._tab_prev_ctr, _vis(self._ctr_df))

    def _update_btn(self):
        self._compare_btn.setEnabled(
            not self._mr_df.empty and not self._ctr_df.empty
        )

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

        for df, col, text in [
            (mr,  "_KEY_", self._mr_filter.text().strip()),
            (ctr, "_KEY_", self._ctr_filter.text().strip()),
        ]:
            if text:
                codes = {c.strip().upper() for c in text.split(",") if c.strip()}
                df.drop(df[~df[col].isin(codes)].index, inplace=True)

        merged = pd.merge(mr, ctr, on="_KEY_", how="outer",
                          suffixes=("_MR", "_CTR"), indicator=True)

        only_mr_df  = merged[merged["_merge"] == "left_only"].copy()
        only_ctr_df = merged[merged["_merge"] == "right_only"].copy()
        matched     = merged[merged["_merge"] == "both"].copy()

        src_mr  = "_SourceFile_MR"  if "_SourceFile_MR"  in matched.columns else "_SourceFile"
        src_ctr = "_SourceFile_CTR" if "_SourceFile_CTR" in matched.columns else None

        # Matched display
        mc = matched
        desc_mr   = _find_col(mc, "Description_MR",  "Description")
        desc_ctr  = _find_col(mc, "Description_CTR")
        qty_mr    = _find_col(mc, "Qty_MR",           "Qty")
        unit_mr   = _find_col(mc, "Unit_MR",           "Unit")
        rech_col  = _find_col(mc, "Rechargeable_MR",  "Rechargeable")
        alloc_col = _find_col(mc, "Allocation_MR",    "Allocation")
        qty_ctr   = _find_col(mc, "Quantity_CTR",     "Quantity")
        unit_ctr  = _find_col(mc, "Unit_CTR")
        rate_col  = _find_col(mc, "Rate_CTR",         "Rate")

        display = pd.DataFrame({"Stock Code": mc["_KEY_"].values})
        if src_mr in mc.columns:
            display["MR Document"]      = mc[src_mr].values
        if src_ctr and src_ctr in mc.columns:
            display["CTR Document"]     = mc[src_ctr].values
        if desc_mr:   display["Description (MR)"]  = mc[desc_mr].values
        if desc_ctr:  display["Description (CTR)"] = mc[desc_ctr].values
        if qty_mr:    display["Qty (MR)"]           = mc[qty_mr].values
        if unit_mr:   display["Unit (MR)"]          = mc[unit_mr].values
        if rech_col:  display["Rechargeable"]       = mc[rech_col].values
        if alloc_col: display["Allocation"]         = mc[alloc_col].values
        if qty_ctr:   display["Qty (CTR)"]          = mc[qty_ctr].values
        if unit_ctr:  display["Unit (CTR)"]         = mc[unit_ctr].values
        if rate_col:  display["Rate (CTR)"]         = mc[rate_col].values
        self._display_df = display.sort_values("Stock Code").reset_index(drop=True)

        # Only-in tables
        def _only_df(df: pd.DataFrame, src_col: str | None) -> pd.DataFrame:
            out = pd.DataFrame({"Stock Code": df["_KEY_"].values})
            if src_col and src_col in df.columns:
                out.insert(1, "Document", df[src_col].values)
            return out

        self._omr_dl  = _only_df(only_mr_df,  "_SourceFile_MR"  if "_SourceFile_MR"  in only_mr_df.columns  else ("_SourceFile" if "_SourceFile" in only_mr_df.columns  else None))
        self._octr_dl = _only_df(only_ctr_df, "_SourceFile_CTR" if "_SourceFile_CTR" in only_ctr_df.columns else ("_SourceFile" if "_SourceFile" in only_ctr_df.columns else None))

        # Update metrics
        self._m_matched.setText(str(len(matched)))
        self._m_only_mr.setText(str(len(only_mr_df)))
        self._m_only_ctr.setText(str(len(only_ctr_df)))

        # Populate result tabs
        _load_view(self._tab_matched,  self._display_df if not self._display_df.empty else _EMPTY_INFO)
        _load_view(self._tab_only_mr,  self._omr_dl     if not self._omr_dl.empty     else _EMPTY_INFO)
        _load_view(self._tab_only_ctr, self._octr_dl    if not self._octr_dl.empty    else _EMPTY_INFO)

        self._tabs.setCurrentWidget(self._tab_matched)
        self._download_btn.setEnabled(True)

    # ── Download ──────────────────────────────────────────────────────────────

    def _download_report(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Excel report", "mr_ctr_diff_report.xlsx",
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


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
