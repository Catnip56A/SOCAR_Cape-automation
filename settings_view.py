"""
settings_view.py — the Settings tab.

One place for the things the app remembers between runs: the SAGE Export
path, the default output folder, the folder the statistics Excel goes to, and
the saved Match By aliases, Description renames and presets. Each saved list
can be reset; before anything is deleted it is copied next to the original as
`<name>.bak-<date>-<time>.json`, so a reset can always be undone by hand.
"""

from __future__ import annotations

import logging
import math
import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

import ctr_tools.aliases as _aliases
import ctr_tools.desc_renames as _renames
import ctr_tools.presets as _presets
from ctr_tools.paths import user_data_dir

log = logging.getLogger(__name__)

_MUTED = "color: #757575; font-size: 11px;"


def gear_icon(px: int = 20, color: str = "#546E7A") -> QIcon:
    """A gear drawn with QPainter (no font or image file needed, so it looks
    the same on every machine). Rendered at 2x so it stays sharp on HiDPI."""
    scale = 2
    pm = QPixmap(px * scale, px * scale)
    pm.setDevicePixelRatio(scale)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = px / 2
    r_out, r_in, teeth = px * 0.47, px * 0.35, 8
    path = QPainterPath()
    pts = []
    for i in range(teeth):
        a0 = 2 * math.pi * i / teeth
        for da, rad in ((-0.30, r_in), (-0.17, r_out), (0.17, r_out), (0.30, r_in)):
            pts.append(QPointF(c + rad * math.cos(a0 + da), c + rad * math.sin(a0 + da)))
    path.moveTo(pts[0])
    for pt in pts[1:]:
        path.lineTo(pt)
    path.closeSubpath()
    path.addEllipse(QPointF(c, c), px * 0.15, px * 0.15)       # the hole
    path.setFillRule(Qt.FillRule.OddEvenFill)
    p.fillPath(path, QColor(color))
    p.end()
    return QIcon(pm)


def backup_and_remove(path: Path) -> Path | None:
    """Copy `path` to `<stem>.bak-<date>-<time><suffix>` beside it, then delete
    it. Returns the backup path, or None when there was nothing to remove."""
    path = Path(path)
    if not path.exists():
        return None
    bak = path.with_name(f"{path.stem}.bak-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
    shutil.copy2(path, bak)
    path.unlink()
    return bak


class SettingsDialog(QDialog):
    """The Settings window opened by the gear button: the same content as
    SettingsTab, in an ordinary (non-modal) window, so the rest of the app
    stays usable while it is open."""

    def __init__(self, ctr_gen, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(False)
        self.resize(640, 560)
        self.tab = SettingsTab(ctr_gen)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.tab)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll)

    def open_window(self) -> None:
        self.tab.refresh()
        self.show()
        self.raise_()
        self.activateWindow()


class SettingsTab(QWidget):
    def __init__(self, ctr_gen, parent=None):
        super().__init__(parent)
        self._gen = ctr_gen
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(12)

        intro = QLabel("What the app remembers between runs. Changes here take effect at once.")
        intro.setStyleSheet(_MUTED)
        lay.addWidget(intro)

        # ── Folders and files ────────────────────────────────────────────────
        grp = QGroupBox("Files and folders")
        g = QVBoxLayout(grp)
        self._sage_edit = self._path_row(
            g, "SAGE Export", "The SAGE export used by the CTR Generator and the MR vs CTR statistics "
            "(shared by both tabs).", self._pick_sage, None)
        self._out_edit = self._path_row(
            g, "Default output folder", "Where the CTR Generator saves the generated CTRs.",
            self._pick_output, None)
        self._stats_edit = self._path_row(
            g, "Statistics save folder", "Where “Save statistics (Excel)” saves. It is shown before every "
            "save, so you can still change it there.", self._pick_stats, self._forget_stats,
            clear_text="Forget")
        lay.addWidget(grp)

        # ── Saved lists ──────────────────────────────────────────────────────
        grp2 = QGroupBox("Saved lists")
        g2 = QVBoxLayout(grp2)
        self._counts: dict[str, QLabel] = {}
        self._saved_row(g2, "aliases", "Match By aliases",
                        "What a requested description was matched to.",
                        lambda: self._gen.open_alias_manager(), self._reset_aliases)
        self._saved_row(g2, "renames", "Description renames",
                        "The display text taught per category and Stock Code.",
                        lambda: self._gen.open_alias_manager(), self._reset_renames)
        self._saved_row(g2, "presets", "CTR presets",
                        "Saved header details (client, location, scope, …) — managed in the CTR Generator tab.",
                        None, self._reset_presets)
        lay.addWidget(grp2)

        self._where = QLabel()
        self._where.setStyleSheet(_MUTED)
        self._where.setWordWrap(True)
        lay.addWidget(self._where)
        lay.addStretch(1)

        self._gen.sage_path_changed.connect(lambda _p: self.refresh())
        self.refresh()

    # ── building blocks ──────────────────────────────────────────────────────

    def _path_row(self, parent_layout, title, hint, change_fn, clear_fn, clear_text="Clear"):
        top = QLabel(title)
        parent_layout.addWidget(top)
        row = QHBoxLayout()
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setPlaceholderText("(not set)")
        change = QPushButton("Change…")
        change.clicked.connect(change_fn)
        row.addWidget(edit, 1)
        row.addWidget(change)
        if clear_fn is not None:
            clear = QPushButton(clear_text)
            clear.clicked.connect(clear_fn)
            row.addWidget(clear)
        parent_layout.addLayout(row)
        note = QLabel(hint)
        note.setStyleSheet(_MUTED)
        note.setWordWrap(True)
        parent_layout.addWidget(note)
        return edit

    def _saved_row(self, parent_layout, key, title, hint, open_fn, reset_fn):
        row = QHBoxLayout()
        label = QLabel(title)
        label.setMinimumWidth(170)
        count = QLabel("")
        self._counts[key] = count
        row.addWidget(label)
        row.addWidget(count, 1)
        if open_fn is not None:
            b = QPushButton("Open manager")
            b.clicked.connect(open_fn)
            row.addWidget(b)
        r = QPushButton("Reset…")
        r.clicked.connect(reset_fn)
        row.addWidget(r)
        parent_layout.addLayout(row)
        note = QLabel(hint)
        note.setStyleSheet(_MUTED)
        note.setWordWrap(True)
        parent_layout.addWidget(note)

    # ── showing the current values ───────────────────────────────────────────

    def refresh(self) -> None:
        self._sage_edit.setText(self._gen.sage_path())
        self._out_edit.setText(self._gen.output_dir())
        self._stats_edit.setText(self._gen.stats_save_dir())
        c = self._gen.saved_data_counts()
        self._counts["aliases"].setText(f"{c['aliases']} saved")
        self._counts["renames"].setText(f"{c['renames']} saved")
        self._counts["presets"].setText(f"{c['presets']} saved")
        self._where.setText(f"Saved lists are stored in: {user_data_dir()}")

    def showEvent(self, event):          # values may have changed in another tab
        self.refresh()
        super().showEvent(event)

    # ── changing paths ───────────────────────────────────────────────────────

    def _pick_sage(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "SAGE Export", self._gen.sage_path(), "Excel (*.xlsx *.xlsm *.xls)")
        if p:
            self._gen.set_sage_path(p)
            self._gen.save_settings()
            self.refresh()

    def _pick_output(self):
        d = QFileDialog.getExistingDirectory(self, "Default output folder", self._gen.output_dir())
        if d:
            self._gen.set_output_dir(d)
            self._gen.save_settings()
            self.refresh()

    def _pick_stats(self):
        d = QFileDialog.getExistingDirectory(self, "Statistics save folder", self._gen.stats_save_dir())
        if d:
            self._gen.set_stats_save_dir(d)
            self.refresh()

    def _forget_stats(self):
        self._gen.set_stats_save_dir("")
        self.refresh()

    # ── resetting saved lists ────────────────────────────────────────────────

    def _reset(self, what: str, path: Path, after_note: str) -> None:
        if QMessageBox.question(
                self, f"Reset {what}",
                f"Reset the saved {what}?\n\nThe current file is first copied next to the original "
                f"as “…bak-<date>-<time>…”, so nothing is lost for good.\n{after_note}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        try:
            bak = backup_and_remove(path)
            _aliases.load_aliases()          # re-seeds from the bundled default when there is one
            _presets.load_presets()
            self._gen.reload_saved_data()
        except OSError as exc:
            log.exception("Reset %s failed", what)
            QMessageBox.critical(self, "Reset failed", str(exc))
            return
        log.info("Reset %s (backup: %s)", what, bak)
        QMessageBox.information(
            self, "Reset", f"{what.capitalize()} reset." + (f"\nBackup: {bak}" if bak else ""))
        self.refresh()

    def _reset_aliases(self):
        self._reset("Match By aliases", _aliases._ALIASES_PATH,
                    "They go back to the aliases that ship with the app.")

    def _reset_renames(self):
        self._reset("Description renames", _renames._DESC_RENAMES_PATH,
                    "The list becomes empty.")

    def _reset_presets(self):
        self._reset("CTR presets", _presets._PRESETS_PATH,
                    "They go back to the presets that ship with the app.")
