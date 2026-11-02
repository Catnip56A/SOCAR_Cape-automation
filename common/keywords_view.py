"""
keywords_view.py — the Column keywords editor (Settings › Saved lists › Column keywords › Edit…).

One table, one row per standard column ("Quantity", "Stock Code" …) of the CTR and of the MR, with the header
spellings that lead to it, comma-separated; three header-row boxes; and the tools that make a change safe:
  * Test on a file…  parses a workbook with the words and rows in the editor (not yet saved) and shows what it finds;
  * Import… / Export…  a JSON file with the whole configuration (an import is checked first and only fills the editor);
  * Save checks the words (see comparator/column_keywords.py › problems) and asks before saving anything risky.
The words only decide how a header in the Excel file is FOUND; the app keeps using its standard names.
"""

from __future__ import annotations

import copy
import logging
from io import BytesIO
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from comparator import column_keywords as CK

log = logging.getLogger(__name__)

_MUTED = "color: #757575; font-size: 11px;"
_SIDE_TITLE = {"ctr": "CTR", "mr": "MR"}


def split_words(text: str) -> list[str]:
    """"qty, =unit ,unit price" -> ["qty", "=unit", "unit price"] (blank and repeated words dropped)."""
    out, seen = [], set()
    for w in str(text).replace("\n", ",").split(","):
        w = w.strip()
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out


MAX_SHOWN_ROWS = 2000          # a table longer than this is cut in the window (the file itself is not touched)


def _table_widget(df) -> QTableWidget:
    """A read-only grid of a parsed table: its standard columns (the internal _Source… ones left out; the section of
    a CTR line first)."""
    cols = [c for c in df.columns if not str(c).startswith("_") or c == "_Section"]
    cols.sort(key=lambda c: 0 if c == "_Section" else 1)                # stable: Section first, the rest as parsed
    shown = df.head(MAX_SHOWN_ROWS)
    tbl = QTableWidget(len(shown), len(cols))
    tbl.setHorizontalHeaderLabels(["Section" if c == "_Section" else str(c) for c in cols])
    tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    tbl.setAlternatingRowColors(True)
    tbl.verticalHeader().setDefaultSectionSize(22)
    for r, row in enumerate(shown[cols].itertuples(index=False)):
        for c, v in enumerate(row):
            text = "" if v is None or (isinstance(v, float) and v != v) else str(v)
            tbl.setItem(r, c, QTableWidgetItem(text))
    tbl.resizeColumnsToContents()
    for c in range(len(cols)):
        if tbl.columnWidth(c) > 320:
            tbl.setColumnWidth(c, 320)
    return tbl


class TestResultDialog(QDialog):
    """What “Test on a file…” found: the summary and the notes on top, and below them every table the parser read, one
    tab per table, with all its lines — so the result can be judged against the Excel file."""

    def __init__(self, parent, cfg: dict, name: str, tables: list):
        super().__init__(parent)
        self.setWindowTitle("Test on a file")
        self.resize(1100, 720)
        lay = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Vertical)
        box = QPlainTextEdit(describe(cfg, name, tables))
        box.setReadOnly(True)
        box.setFont(QFont("monospace"))
        split.addWidget(box)
        self.tabs = QTabWidget()
        for t in tables:
            df = t["data"]
            self.tabs.addTab(_table_widget(df), f"{t['source_sheet']} › {t['table_name']} ({len(df)})")
        if not tables:
            self.tabs.addTab(QLabel("  No tables could be read with these words and rows."), "No tables")
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        lay.addWidget(split, 1)
        cut = [f"{t['table_name']} ({len(t['data'])} lines)" for t in tables if len(t["data"]) > MAX_SHOWN_ROWS]
        if cut:
            note = QLabel(f"Only the first {MAX_SHOWN_ROWS} lines of {', '.join(cut)} are shown here.")
            note.setStyleSheet(_MUTED)
            lay.addWidget(note)
        ok = QPushButton("Close")
        ok.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(ok)
        lay.addLayout(row)


class KeywordsDialog(QDialog):
    def __init__(self, parent, cfg: dict):
        super().__init__(parent)
        self.setWindowTitle("Column keywords")
        self.resize(1000, 640)
        self._cfg = copy.deepcopy(cfg)
        lay = QVBoxLayout(self)
        intro = QLabel(
            "These header spellings only help the app FIND a column in an MR or CTR file; after that it uses its own "
            "standard names (the first column below). A word matches a header that contains it (case and punctuation "
            "ignored, starting at a word): <b>qty</b> matches “Qty *”. Put <b>=</b> in front for a header that is exactly "
            "that: <b>=unit</b> matches “Unit” but not “Unit Price”. When several words match, the most specific one "
            "wins. Changes apply to files loaded afterwards — reload a file to apply them to it.")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(intro)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Document", "Standard column", "Words (comma-separated)", "Yours (not shipped)"])
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        h = self._table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self._table, 1)

        rows = QHBoxLayout()
        rows.addWidget(QLabel("Header rows (Excel row numbers):"))
        self._mr_row = self._spin(1, "MR: the row with the column names. One row for both tables (equipment and consumables "
                                      "are together in an MR). The standard is 9.")
        self._eq_row = self._spin(0, "CTR: the row with the column names of the Plant & Equipment table. “auto” finds it "
                                      "by its words.", auto=True)
        self._co_row = self._spin(0, "CTR: the row with the column names of the Consumables table. “auto” finds it by "
                                      "its words.", auto=True)
        for text, w in (("MR", self._mr_row), ("CTR equipment", self._eq_row), ("CTR consumables", self._co_row)):
            rows.addWidget(QLabel(text))
            rows.addWidget(w)
        rows.addStretch(1)
        lay.addLayout(rows)
        note = QLabel("A CTR read through named ranges (equip_start … cons_end) does not use the header rows above.")
        note.setStyleSheet(_MUTED)
        lay.addWidget(note)

        btns = QHBoxLayout()
        self._test_btn = QPushButton("Test on a file…")
        self._test_btn.setToolTip("Parse an Excel file with the words and rows in this window (not yet saved) and show "
                                  "which tables, columns and notes it gives.")
        self._test_btn.clicked.connect(self._test)
        imp = QPushButton("Import…")
        imp.setToolTip("Fill this window from a column-keywords file. The file is checked first; nothing is saved "
                       "until you press Save.")
        imp.clicked.connect(self._import)
        exp = QPushButton("Export…")
        exp.setToolTip("Write what is in this window to a file (to keep, or to copy to another computer).")
        exp.clicked.connect(self._export)
        save = QPushButton("Save")
        save.setDefault(True)
        save.clicked.connect(self._save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        for b in (self._test_btn, imp, exp):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(save)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        self._fill(self._cfg)

    # ── widgets ──────────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _spin(lo: int, tip: str, auto: bool = False) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, CK.MAX_ROW)
        if auto:
            s.setSpecialValueText("auto")
        s.setToolTip(tip)
        s.setFixedWidth(80)
        return s

    def _fill(self, cfg: dict) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for side in CK.SIDES:
            concepts = list(CK.SHIPPED[side]) + [c for c in cfg.get(side, {}) if c not in CK.SHIPPED[side]]
            for concept in concepts:
                r = self._table.rowCount()
                self._table.insertRow(r)
                words = cfg.get(side, {}).get(concept, [])
                for c, text in enumerate((_SIDE_TITLE[side], concept, ", ".join(words), "")):
                    it = QTableWidgetItem(text)
                    if c != 2:
                        it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self._table.setItem(r, c, it)
                it = self._table.item(r, 1)
                it.setData(Qt.ItemDataRole.UserRole, side)
                if concept == CK.IGNORE:
                    it.setToolTip("Headers with these words are NOT read as a standard column (the generator's internal "
                                  "cost columns).")
                self._refresh_own(r)
        self._table.blockSignals(False)
        rows = cfg.get("rows", {})
        self._mr_row.setValue(rows.get("mr") or CK.SHIPPED_ROWS["mr"])
        self._eq_row.setValue(rows.get("ctr_equipment") or 0)
        self._co_row.setValue(rows.get("ctr_consumables") or 0)

    def _refresh_own(self, r: int) -> None:
        side, concept = self._table.item(r, 1).data(Qt.ItemDataRole.UserRole), self._table.item(r, 1).text()
        own = [w for w in split_words(self._table.item(r, 2).text()) if not CK.is_shipped_word(side, concept, w)]
        it = self._table.item(r, 3)
        it.setText(", ".join(own))
        f = it.font()
        f.setBold(bool(own))
        it.setFont(f)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 2:
            self._table.blockSignals(True)
            self._refresh_own(item.row())
            self._table.blockSignals(False)

    def collected(self) -> dict:
        """The configuration as the window shows it now."""
        cfg = {"version": self._cfg.get("version", CK.SHIPPED_VERSION), "ctr": {}, "mr": {},
               "rows": {"mr": self._mr_row.value(), "ctr_equipment": self._eq_row.value() or None,
                        "ctr_consumables": self._co_row.value() or None}}
        for r in range(self._table.rowCount()):
            side = self._table.item(r, 1).data(Qt.ItemDataRole.UserRole)
            cfg[side][self._table.item(r, 1).text()] = split_words(self._table.item(r, 2).text())
        return cfg

    # ── buttons ──────────────────────────────────────────────────────────────────────────────

    def _save(self) -> None:
        cfg = self.collected()
        probs = CK.problems(cfg)
        errors = [p["text"] for p in probs if p["level"] == "error"]
        if errors:
            QMessageBox.critical(self, "Column keywords", "These must be fixed first:\n\n• " + "\n• ".join(errors))
            return
        warns = [p["text"] for p in probs if p["level"] == "warn"]
        if warns:
            shown = "\n• ".join(warns[:12]) + (f"\n… and {len(warns) - 12} more" if len(warns) > 12 else "")
            if QMessageBox.question(
                    self, "Check these words",
                    "These may cause wrong matches:\n\n• " + shown + "\n\nSave anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
                return
        self._result = cfg
        self.accept()

    def result_cfg(self) -> dict:
        return self._result

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export column keywords", "column_keywords.json", "JSON (*.json)")
        if not path:
            return
        try:
            CK.export_to(Path(path), self.collected())
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Exported", f"Written to {path}.")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import column keywords", "", "JSON (*.json)")
        if not path:
            return
        try:
            cfg = CK.import_from(Path(path))
        except ValueError as exc:
            QMessageBox.critical(self, "Import refused", f"{Path(path).name} was not read:\n\n{exc}\n\nNothing was changed.")
            return
        self._fill(cfg)
        QMessageBox.information(self, "Imported", f"{Path(path).name} is loaded into this window. Press Save to keep it, "
                                                  "or Cancel to leave your settings as they were.")

    def _test(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Test on a file", "", "Excel files (*.xlsx *.xlsm *.xls)")
        if not path:
            return
        from comparator.sheet_parser import parse_workbook
        cfg = self.collected()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            with CK.using(cfg):
                tables = parse_workbook(BytesIO(Path(path).read_bytes()), filename=Path(path).name, include_cover=True)
        except (ValueError, OSError) as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Test on a file", f"{Path(path).name} could not be read:\n\n{exc}")
            return
        QApplication.restoreOverrideCursor()
        dlg = TestResultDialog(self, cfg, Path(path).name, tables)
        dlg.exec()
        dlg.deleteLater()


def describe(cfg: dict, name: str, tables: list) -> str:
    """A plain-text report of a test parse (what the Settings window's “Test on a file” shows)."""
    rows = cfg.get("rows", {})
    lines = [f"File: {name}",
             f"Header rows: MR {rows.get('mr') or CK.SHIPPED_ROWS['mr']}, CTR equipment {rows.get('ctr_equipment') or 'auto'}, "
             f"CTR consumables {rows.get('ctr_consumables') or 'auto'}",
             f"Tables found: {len(tables)}", ""]
    if not tables:
        lines.append("No tables could be read with these words and rows.")
    for t in tables:
        df = t["data"]
        cols = [c for c in df.columns if not str(c).startswith("_")]
        lines.append(f"[{t['source_sheet']}] {t['table_name']} — {len(df)} line(s)")
        lines.append("   columns: " + ", ".join(map(str, cols)))
        for n in t.get("notes", []):
            lines.append(f"   {'CHECK' if n['level'] == 'warn' else 'info '}: {n['text']}")
        lines.append("")
    return "\n".join(lines)
