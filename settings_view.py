"""
settings_view.py — the Settings window.

One place for the things the app remembers between runs: the SAGE Export
path, the default output folder, the folder the statistics Excel goes to, and
the saved Match By aliases, Description renames and presets. Each saved list
can be reset; before anything is deleted it is copied next to the original as
`<name>.bak-<date>-<time>.json`. The newest KEEP_BACKUPS copies per list are kept,
and **Restore…** lists them and brings one back (the list as it is now is copied to
a backup first, so a restore can be undone too).
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

import ctr_tools.aliases as _aliases
import ctr_tools.desc_renames as _renames
import ctr_tools.presets as _presets
import ctr_tools.source_choices as _choices
from ctr_tools.paths import user_data_dir

log = logging.getLogger(__name__)

_MUTED = "color: #757575; font-size: 11px;"
KEEP_BACKUPS = 5          # newest backups kept per saved list


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


def backups_for(path: Path) -> list[Path]:
    """The backups of a saved list, newest first (their names carry the time)."""
    path = Path(path)
    return sorted(path.parent.glob(f"{path.stem}.bak-*{path.suffix}"), reverse=True)


def prune_backups(path: Path, keep: int = KEEP_BACKUPS) -> None:
    for old in backups_for(path)[keep:]:
        old.unlink(missing_ok=True)


def check_writable(folder: Path) -> None:
    """Raise OSError with a readable message when `folder` cannot be written to."""
    folder = Path(folder)
    try:
        with tempfile.NamedTemporaryFile(dir=folder, delete=True):
            pass
    except OSError as exc:
        raise OSError(f"The folder {folder} cannot be written to ({exc.strerror or exc}).") from exc


def copy_backup(path: Path) -> Path | None:
    """Copy `path` to `<stem>.bak-<date>-<time><suffix>` beside it and keep only the
    newest KEEP_BACKUPS. Returns the backup, or None when there is nothing to copy."""
    path = Path(path)
    if not path.exists():
        return None
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    bak, n = path.with_name(f"{path.stem}.bak-{stamp}{path.suffix}"), 1
    while bak.exists():                                  # two in the same second
        n += 1
        bak = path.with_name(f"{path.stem}.bak-{stamp}-{n}{path.suffix}")
    shutil.copy2(path, bak)
    prune_backups(path)
    return bak


def backup_and_remove(path: Path) -> Path | None:
    """Back `path` up (see copy_backup), then delete it. Returns the backup path,
    or None when there was nothing to remove."""
    bak = copy_backup(path)
    if bak is not None:
        Path(path).unlink()
    return bak


def count_entries(data) -> int:
    """How many entries a saved list holds: aliases / renames are
    {category: {key: value}} (manpower / equipment / consumable), presets are
    {name: {...}}."""
    if not isinstance(data, dict):
        return 0
    if data and set(data) <= {"manpower", "equipment", "consumable"}:
        return sum(len(v) for v in data.values() if isinstance(v, dict))
    return len(data)


def read_entries(path: Path) -> int | None:
    """Entries in a saved-list file, or None when it is not readable JSON."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return count_entries(data) if isinstance(data, dict) else None


class _RestoreDialog(QDialog):
    """Lists the backups of one saved list (newest first); choose one to restore."""

    def __init__(self, parent, what: str, backups: list[Path]):
        super().__init__(parent)
        self.setWindowTitle(f"Restore {what}")
        self.setMinimumWidth(520)
        self._paths: list[Path] = []
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Backups of the {what} (the newest {KEEP_BACKUPS} are kept):"))
        self._list = QListWidget()
        for b in backups:
            n = read_entries(b)
            when = datetime.fromtimestamp(b.stat().st_mtime)
            text = f"{when:%Y-%m-%d %H:%M:%S}   ·   " + (f"{n} entr{'y' if n == 1 else 'ies'}" if n is not None
                                                          else "unreadable")
            it = QListWidgetItem(text)
            it.setToolTip(str(b))
            if n is None:
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(it)
            self._paths.append(b)
        self._list.itemDoubleClicked.connect(lambda _i: self._accept_selected())
        lay.addWidget(self._list)
        row = QHBoxLayout()
        row.addStretch(1)
        self._ok = QPushButton("Restore")
        self._ok.setEnabled(False)
        self._ok.clicked.connect(self._accept_selected)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(self._ok)
        row.addWidget(cancel)
        lay.addLayout(row)
        self._list.currentRowChanged.connect(
            lambda r: self._ok.setEnabled(0 <= r < len(self._paths) and bool(
                self._list.item(r).flags() & Qt.ItemFlag.ItemIsEnabled)))
        if self._paths:
            self._list.setCurrentRow(0)

    def _accept_selected(self) -> None:
        if self._ok.isEnabled():
            self.accept()

    def chosen(self) -> Path | None:
        r = self._list.currentRow()
        return self._paths[r] if 0 <= r < len(self._paths) else None


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
        self._restore_btns: dict[str, QPushButton] = {}
        self._saved_row(g2, "aliases", "Match By aliases",
                        "What a requested description was matched to.",
                        lambda: self._gen.open_alias_manager(), self._reset_aliases,
                        lambda: self._restore("Match By aliases", _aliases._ALIASES_PATH))
        self._saved_row(g2, "renames", "Description renames",
                        "The display text taught per category and Stock Code.",
                        lambda: self._gen.open_alias_manager(), self._reset_renames,
                        lambda: self._restore("Description renames", _renames._DESC_RENAMES_PATH))
        self._saved_row(g2, "presets", "CTR presets",
                        "Saved header details (client, location, scope, …) — managed in the CTR Generator tab.",
                        None, self._reset_presets,
                        lambda: self._restore("CTR presets", _presets._PRESETS_PATH))
        self._saved_row(g2, "choices", "Rechargeability choices",
                        "Which source (document or SAGE) you chose for a Stock Code whose rechargeability "
                        "differs — made in the Disparities list of the statistics.",
                        None, self._reset_choices,
                        lambda: self._restore("Rechargeability choices", _choices._CHOICES_PATH))
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

    def _saved_row(self, parent_layout, key, title, hint, open_fn, reset_fn, restore_fn):
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
        rs = QPushButton("Restore…")
        rs.clicked.connect(restore_fn)
        self._restore_btns[key] = rs
        row.addWidget(rs)
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
        self._counts["choices"].setText(f"{c.get('choices', 0)} saved")
        for key, path in (("aliases", _aliases._ALIASES_PATH), ("renames", _renames._DESC_RENAMES_PATH),
                          ("presets", _presets._PRESETS_PATH), ("choices", _choices._CHOICES_PATH)):
            n = len(backups_for(path))
            btn = self._restore_btns[key]
            btn.setEnabled(n > 0)
            btn.setText(f"Restore… ({n})" if n else "Restore…")
            btn.setToolTip(f"{n} backup(s) of this list; choose one to bring back." if n
                           else "No backup yet — one is made whenever this list is reset or restored.")
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
            check_writable(path.parent)
        except OSError as exc:
            log.warning("Reset %s refused: %s", what, exc)
            QMessageBox.warning(self, "Cannot reset", f"{exc}\n\nNothing was changed.")
            return
        try:
            bak = backup_and_remove(path)
            _aliases.load_aliases()          # re-seeds the aliases that ship with the app
            # (no preset ships with the app: after a reset the preset list is empty)
            self._gen.reload_saved_data()
        except OSError as exc:
            log.exception("Reset %s failed", what)
            QMessageBox.critical(self, "Reset failed", str(exc))
            return
        log.info("Reset %s (backup: %s)", what, bak)
        QMessageBox.information(
            self, "Reset", f"{what.capitalize()} reset." + (f"\nBackup: {bak}" if bak else ""))
        self.refresh()

    def _restore(self, what: str, path: Path) -> None:
        backups = backups_for(path)
        if not backups:
            QMessageBox.information(self, "Restore", f"There is no backup of the {what} yet.")
            return
        dlg = _RestoreDialog(self, what, backups)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.chosen() is None:
            return
        chosen = dlg.chosen()
        n = read_entries(chosen)
        if n is None:
            QMessageBox.critical(self, "Restore", f"{chosen.name} cannot be read — nothing was changed.")
            return
        now = read_entries(path) if Path(path).exists() else 0
        if QMessageBox.question(
                self, f"Restore {what}",
                f"Restore the {what} from this backup ({n} entr{'y' if n == 1 else 'ies'})?\n\n"
                f"The list as it is now ({now if now is not None else 'unreadable'} entries) is first "
                "saved as a backup, so this can be undone too.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        try:
            check_writable(Path(path).parent)
            saved = copy_backup(path)
            shutil.copy2(chosen, path)
            self._gen.reload_saved_data()
        except OSError as exc:
            log.exception("Restore %s failed", what)
            QMessageBox.critical(self, "Restore failed", str(exc))
            return
        log.info("Restored %s from %s (previous list saved as %s)", what, chosen, saved)
        QMessageBox.information(self, "Restored", f"{what.capitalize()} restored from {chosen.name}.")
        self.refresh()

    def _reset_aliases(self):
        self._reset("Match By aliases", _aliases._ALIASES_PATH,
                    "They go back to the aliases that ship with the app.")

    def _reset_renames(self):
        self._reset("Description renames", _renames._DESC_RENAMES_PATH,
                    "The list becomes empty.")

    def _reset_choices(self):
        self._reset("rechargeability choices", _choices._CHOICES_PATH,
                    "The list becomes empty: every disparity will need a choice again.")

    def _reset_presets(self):
        self._reset("CTR presets", _presets._PRESETS_PATH,
                    "The list becomes empty (no preset ships with the app).")
