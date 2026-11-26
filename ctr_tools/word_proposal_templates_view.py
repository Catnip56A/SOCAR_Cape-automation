"""The "Manage templates" window of the Word proposal: which .docx is used for each kind (short / long) and task type.

A template in the chosen folder is found by its name (see word_proposal_templates.py). Names change, so any row can be
**mapped** to a file of your choice instead (it may lie in another folder); a mapping wins over the name. Nothing is
saved until OK is pressed.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ctr_tools import word_proposal_templates as T

_COLS = ("Kind", "Task type", "Template file", "Chosen by")


class WordProposalTemplatesDialog(QDialog):
    def __init__(self, folder: str, mapping: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Word proposal — manage templates")
        self._folder = folder
        self._found = T.discover(folder)
        self._map = dict(mapping)                      # a copy: Cancel leaves the saved mapping alone
        self.resize(760, 340)

        lay = QVBoxLayout(self)
        hint = QLabel(
            "A template in the folder is found by its name (<b>Word_Proposal_Template_long_Insulation.docx</b> = long, "
            "task type Insulation). Names change, so you can <b>map</b> a row to any .docx file instead: the mapping "
            "wins over the name. <b>Add task type…</b> adds a row for a template that has no such name.")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # A QTableWidget is a grid of QTableWidgetItem cells; read-only here, one whole row selected at a time.
        self._tbl = QTableWidget(0, len(_COLS))
        self._tbl.setHorizontalHeaderLabels(_COLS)
        self._tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tbl.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tbl.verticalHeader().setVisible(False)
        head = self._tbl.horizontalHeader()
        head.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self._tbl, 1)

        line = QHBoxLayout()
        self._btn_map = QPushButton("Map to a file…")
        self._btn_map.setToolTip("Use a .docx of your choice for the selected row, whatever its name or folder.")
        self._btn_name = QPushButton("Use the name again")
        self._btn_name.setToolTip("Remove the mapping of the selected row: the file found by its name (if any) is used.")
        self._btn_add = QPushButton("Add task type…")
        self._btn_remove = QPushButton("Remove row")
        self._btn_remove.setToolTip("Only a row that exists because of a mapping can be removed.")
        for b in (self._btn_map, self._btn_name, self._btn_add, self._btn_remove):
            line.addWidget(b)
        line.addStretch(1)
        lay.addLayout(line)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

        self._btn_map.clicked.connect(self._map_selected)
        self._btn_name.clicked.connect(self._unmap_selected)
        self._btn_add.clicked.connect(self._add_row)
        self._btn_remove.clicked.connect(self._remove_selected)
        self._tbl.itemSelectionChanged.connect(self._update_buttons)
        self._fill()

    # ── what the window holds ──
    def mapping(self) -> dict[str, str]:
        """The mapping as edited (read it after OK)."""
        return dict(self._map)

    def _rows(self) -> list[tuple[str, str]]:
        return [(kind, task) for kind in T.KINDS for task in T.types_for(kind, self._found, self._map)]

    def _selected(self) -> tuple[str, str] | None:
        rows = self._tbl.selectionModel().selectedRows()
        if not rows:
            return None
        r = rows[0].row()
        return self._tbl.item(r, 0).text().lower(), self._tbl.item(r, 1).text()

    def _fill(self, select: tuple[str, str] | None = None) -> None:
        self._tbl.setRowCount(0)
        for kind, task in self._rows():
            path = T.resolve(kind, task, self._found, self._map)
            how = T.source_of(kind, task, self._found, self._map)
            missing = path is not None and not path.is_file()
            cells = (kind.capitalize(), task, str(path) if path else "", "missing file" if missing else how)
            r = self._tbl.rowCount()
            self._tbl.insertRow(r)
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if missing and c in (2, 3):
                    item.setForeground(QBrush(QColor("#C62828")))
                self._tbl.setItem(r, c, item)
            if select == (kind, task):
                self._tbl.selectRow(r)
        self._update_buttons()

    def _update_buttons(self) -> None:
        sel = self._selected()
        self._btn_map.setEnabled(sel is not None)
        self._btn_name.setEnabled(sel is not None and T.map_key(*sel) in self._map)
        self._btn_remove.setEnabled(sel is not None and T.map_key(*sel) in self._map and sel not in self._found)

    # ── actions ──
    def _pick_file(self, title: str) -> str:
        start = self._folder if self._folder and Path(self._folder).is_dir() else ""
        path, _ = QFileDialog.getOpenFileName(self, title, start, "Word (*.docx)")
        return path

    def _map_selected(self) -> None:
        sel = self._selected()
        if sel is None:
            return
        path = self._pick_file(f"Template for {sel[0]} / {sel[1]}")
        if path:
            self._map[T.map_key(*sel)] = path
            self._fill(sel)

    def _unmap_selected(self) -> None:
        sel = self._selected()
        if sel is not None:
            self._map.pop(T.map_key(*sel), None)
            self._fill(sel if sel in self._found else None)

    def _add_row(self) -> None:
        kind, ok = QInputDialog.getItem(self, "Add task type", "Kind of proposal:", ["Short", "Long"], 1, False)
        if not ok:
            return
        task, ok = QInputDialog.getText(self, "Add task type", "Task type (for example Insulation):")
        task = " ".join(task.split())
        if not ok or not task or "|" in task:
            return
        path = self._pick_file(f"Template for {kind} / {task}")
        if not path:
            return
        key = T.map_key(kind.lower(), task)
        if key in self._map or (kind.lower(), task) in self._found:
            if QMessageBox.question(self, "Add task type", f"{kind} / {task} already has a template. Replace it with this file?"
                                    ) != QMessageBox.StandardButton.Yes:
                return
        self._map[key] = path
        self._fill((kind.lower(), task))

    def _remove_selected(self) -> None:
        sel = self._selected()
        if sel is not None and T.map_key(*sel) in self._map and sel not in self._found:
            self._map.pop(T.map_key(*sel))
            self._fill()
