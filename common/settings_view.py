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
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QInputDialog,
)

import ctr_tools.aliases as _aliases
import ctr_tools.desc_renames as _renames
import ctr_tools.presets as _presets
import ctr_tools.source_choices as _choices
import ctr_tools.stockcode_types as _types
from stats import stats_view as _sv
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
    if "equipment" in data and "consumables_stockcodes" in data:        # the Stock Code types list
        return len(data["equipment"]) + len(data["consumables_stockcodes"])
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


class _ChoicesDialog(QDialog):
    """Edit the rechargeability choices: for a Stock Code whose rechargeability differs between a
    document and SAGE, which source is right — the document or SAGE. The same list the Disparities
    window fills in with *Choose source…*; add a code here to decide it before it ever shows up."""

    _LABELS = (("Document is right", "DOC"), ("SAGE is right", "SAGE"))

    def __init__(self, parent, entries: dict[str, str]):
        super().__init__(parent)
        self.setWindowTitle("Rechargeability choices")
        self.resize(560, 480)
        lay = QVBoxLayout(self)
        note = QLabel(
            "For a Stock Code whose rechargeability differs between a document and SAGE, which source is "
            "right. These are the same choices you make with “Choose source…” in the Disparities window, "
            "shared by the MR vs CTR statistics and the CTR Generator. A code listed here is settled "
            "without asking; you can change or remove it at any time.")
        note.setWordWrap(True)
        note.setStyleSheet(_MUTED)
        lay.addWidget(note)
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Stock Code", "Which source is right"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 200)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        lay.addWidget(self._table, 1)
        for code, source in sorted(entries.items()):
            self._add(code, source)
        row = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(lambda: self._add("", "DOC", edit=True))
        rem = QPushButton("Remove selected")
        rem.clicked.connect(self._remove)
        ok = QPushButton("Save")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _add(self, code: str, source: str, edit: bool = False) -> None:
        r = self._table.rowCount()
        self._table.insertRow(r)
        self._table.setItem(r, 0, QTableWidgetItem(code))
        combo = QComboBox()
        for label, value in self._LABELS:
            combo.addItem(label, value)
        combo.setCurrentIndex(max(0, combo.findData(source)))
        self._table.setCellWidget(r, 1, combo)
        if edit:
            self._table.setCurrentCell(r, 0)
            self._table.editItem(self._table.item(r, 0))

    def _remove(self) -> None:
        for r in sorted({i.row() for i in self._table.selectedIndexes()}, reverse=True):
            self._table.removeRow(r)

    def entries(self) -> dict[str, str]:
        out = {}
        for r in range(self._table.rowCount()):
            code = (self._table.item(r, 0).text() if self._table.item(r, 0) else "").strip().upper()
            combo = self._table.cellWidget(r, 1)
            if code and combo is not None:
                out[code] = combo.currentData()
        return out


class _TypesDialog(QDialog):
    """Edit the Stock Code types list: for every Stock Code, whether it is Equipment (priced from the
    USD Pricebook, per day) or a Consumable (priced from SAGE). Searchable, with each item's
    description (from the SAGE Export when it is loaded) so it is easy to decide."""

    def __init__(self, parent, types: dict, names: dict[str, str]):
        super().__init__(parent)
        self.setWindowTitle("Stock Code types — Equipment or Consumable")
        self.resize(900, 620)
        self._orig_kind: dict[str, str] = {}
        self._confirmed: dict[str, str] = dict(types.get("confirmed", {}))
        lay = QVBoxLayout(self)
        note = QLabel(
            "Which Stock Codes are Equipment and which are Consumables. The MR vs CTR Document price uses it "
            "to choose the rate source. A code that was in both lists shows as Equipment (marked). "
            "Select lines and press a button to change their type; your changes are marked as your decision.")
        note.setWordWrap(True)
        note.setStyleSheet(_MUTED)
        lay.addWidget(note)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by Stock Code or description…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        lay.addWidget(self._search)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Stock Code", "Type", "Description", "Note"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 150)
        self._table.setColumnWidth(1, 110)
        self._table.setColumnWidth(2, 380)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.verticalHeader().setVisible(False)
        lay.addWidget(self._table, 1)
        both = types["equipment"] & types["consumable"]
        codes = sorted(types["equipment"] | types["consumable"])
        self._table.setRowCount(len(codes))
        for r, code in enumerate(codes):
            kind = _types.EQUIPMENT if code in types["equipment"] else _types.CONSUMABLE
            self._orig_kind[code] = kind
            note_txt = ("in both lists — Equipment" if code in both
                        else "confirmed" if code in self._confirmed else "")
            for c, v in enumerate((code, kind, names.get(code, ""), note_txt)):
                self._table.setItem(r, c, QTableWidgetItem(v))
        self._count = QLabel("")
        self._count.setStyleSheet(_MUTED)
        row = QHBoxLayout()
        add = QPushButton("Add…")
        add.clicked.connect(self._add)
        to_eq = QPushButton("Set selected → Equipment")
        to_eq.clicked.connect(lambda: self._set_selected(_types.EQUIPMENT))
        to_cons = QPushButton("Set selected → Consumable")
        to_cons.clicked.connect(lambda: self._set_selected(_types.CONSUMABLE))
        rem = QPushButton("Remove selected")
        rem.clicked.connect(self._remove)
        ok = QPushButton("Save")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        for w in (add, to_eq, to_cons, rem):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addWidget(self._count)
        lay.addLayout(row)
        self._update_count()

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self._table.selectedIndexes()})

    def _apply_filter(self, text: str) -> None:
        q = text.strip().lower()
        for r in range(self._table.rowCount()):
            hit = not q or any(q in (self._table.item(r, c).text().lower() if self._table.item(r, c) else "")
                               for c in (0, 2))
            self._table.setRowHidden(r, not hit)

    def _update_count(self) -> None:
        eq = sum(1 for r in range(self._table.rowCount()) if self._table.item(r, 1).text() == _types.EQUIPMENT)
        self._count.setText(f"{eq} Equipment · {self._table.rowCount() - eq} Consumable")

    def _set_selected(self, kind: str) -> None:
        for r in self._selected_rows():
            self._table.item(r, 1).setText(kind)
            self._table.item(r, 3).setText("your decision")
        self._update_count()

    def _add(self) -> None:
        text, ok = QInputDialog.getText(self, "Add Stock Codes", "Stock Code(s), separated by spaces or commas:")
        if not ok or not text.strip():
            return
        existing = {self._table.item(r, 0).text(): r for r in range(self._table.rowCount())}
        for code in [c.strip().upper() for c in text.replace(",", " ").split() if c.strip()]:
            if code in existing:
                continue
            r = self._table.rowCount()
            self._table.insertRow(r)
            for c, v in enumerate((code, _types.CONSUMABLE, "", "new — set its type")):
                self._table.setItem(r, c, QTableWidgetItem(v))
            existing[code] = r
        self._update_count()

    def _remove(self) -> None:
        for r in reversed(self._selected_rows()):
            self._table.removeRow(r)
        self._update_count()

    def result_types(self) -> dict:
        eq, cons, conf = set(), set(), dict(self._confirmed)
        for r in range(self._table.rowCount()):
            code, kind = self._table.item(r, 0).text(), self._table.item(r, 1).text()
            (eq if kind == _types.EQUIPMENT else cons).add(code)
            if self._table.item(r, 3).text() in ("your decision", "new — set its type") or (
                    self._orig_kind.get(code) not in (None, kind)):
                conf[code] = kind                       # the user changed it: a decision, not asked again
        kept = eq | cons
        return {"equipment": eq, "consumable": cons,
                "confirmed": {k: v for k, v in conf.items() if k in kept}}


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
                        "Which source (document or SAGE) is right for a Stock Code whose rechargeability "
                        "differs. Made in the Disparities list of the statistics, or edited here.",
                        self._edit_choices, self._reset_choices,
                        lambda: self._restore("Rechargeability choices", _choices._CHOICES_PATH),
                        open_text="Edit…")
        self._saved_row(g2, "types", "Stock Code types",
                        "Which Stock Codes are Equipment and which are Consumables (used by Document price in the "
                        "MR vs CTR tab). Ships with the app; Merge adds the shipped codes you do not have yet, "
                        "Reset replaces your list with the shipped one.",
                        self._edit_types, self._reset_types,
                        lambda: self._restore("Stock Code types", _types._TYPES_PATH),
                        open_text="Edit…",
                        extra=("Merge shipped…", self._merge_types,
                               "Add the codes of the list that shipped with this version that your list lacks. "
                               "Your own entries and decisions are kept."))
        lay.addWidget(grp2)

        # ── Statistics defaults ──────────────────────────────────────────────
        grp3 = QGroupBox("Statistics defaults")
        g3 = QVBoxLayout(grp3)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Show first"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("Document costs (SAGE expected cost)", _sv.MODE_COST)
        self._mode_combo.addItem("Document price (what the documents bill)", _sv.MODE_PRICE)
        self._mode_combo.currentIndexChanged.connect(lambda _=0: self._save_defaults())
        mode_row.addWidget(self._mode_combo, 1)
        g3.addLayout(mode_row)
        self._scaffold_chk = QCheckBox("Include scaffold in Document price")
        self._scaffold_chk.toggled.connect(lambda _=False: self._save_defaults())
        g3.addWidget(self._scaffold_chk)
        self._markup_chk = QCheckBox("Include the markup on consumables in Document price (CTR Generator)")
        self._markup_chk.toggled.connect(lambda _=False: self._save_defaults())
        g3.addWidget(self._markup_chk)
        hint = QLabel("These are the starting positions of the toggles in the statistics (the MR vs CTR "
                      "tab and the CTR Generator); you can still change them there at any time.")
        hint.setStyleSheet(_MUTED)
        hint.setWordWrap(True)
        g3.addWidget(hint)
        lay.addWidget(grp3)

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

    def _saved_row(self, parent_layout, key, title, hint, open_fn, reset_fn, restore_fn,
                   open_text="Open manager", extra=None):
        row = QHBoxLayout()
        label = QLabel(title)
        label.setMinimumWidth(170)
        count = QLabel("")
        self._counts[key] = count
        row.addWidget(label)
        row.addWidget(count, 1)
        if open_fn is not None:
            b = QPushButton(open_text)
            b.clicked.connect(open_fn)
            row.addWidget(b)
        if extra is not None:
            x = QPushButton(extra[0])
            x.setToolTip(extra[2])
            x.clicked.connect(extra[1])
            row.addWidget(x)
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
        t = _types.current_types()
        self._counts["types"].setText(
            f"{len(t['equipment'])} equipment · {len(t['consumable'])} consumable"
            + ("" if _types.shipped_available() else " · (no shipped list found)"))
        d = _sv.stats_defaults()
        for w, setter in ((self._mode_combo, lambda: self._mode_combo.setCurrentIndex(
                               max(0, self._mode_combo.findData(d["mode"])))),
                          (self._scaffold_chk, lambda: self._scaffold_chk.setChecked(d["scaffold"])),
                          (self._markup_chk, lambda: self._markup_chk.setChecked(d["markup"]))):
            w.blockSignals(True)
            setter()
            w.blockSignals(False)
        for key, path in (("aliases", _aliases._ALIASES_PATH), ("renames", _renames._DESC_RENAMES_PATH),
                          ("presets", _presets._PRESETS_PATH), ("choices", _choices._CHOICES_PATH),
                          ("types", _types._TYPES_PATH)):
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
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        chosen = dlg.chosen()
        dlg.deleteLater()                    # one window per use, not one left behind each time
        if not accepted or chosen is None:
            return
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

    def _save_defaults(self) -> None:
        _sv.save_stats_defaults(self._mode_combo.currentData(), self._scaffold_chk.isChecked(),
                                self._markup_chk.isChecked())
        self._gen.stats_defaults_changed.emit()

    def _edit_choices(self) -> None:
        dlg = _ChoicesDialog(self, _choices.load_choices())
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        entries = dlg.entries() if accepted else None
        dlg.deleteLater()
        if not accepted:
            return
        try:
            check_writable(_choices._CHOICES_PATH.parent)
            copy_backup(_choices._CHOICES_PATH)
            _choices.save_choices(entries)
            self._gen.reload_saved_data()          # both statistics lists take the new choices
        except OSError as exc:
            log.exception("Saving the rechargeability choices failed")
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.refresh()

    def _edit_types(self) -> None:
        names = self._gen.sage_names() if hasattr(self._gen, "sage_names") else {}
        dlg = _TypesDialog(self, _types.current_types(), names)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        result = dlg.result_types() if accepted else None
        dlg.deleteLater()
        if not accepted:
            return
        try:
            check_writable(_types._TYPES_PATH.parent)
            copy_backup(_types._TYPES_PATH)
            _types.save_types(result)
            self._gen.reload_saved_data()
        except OSError as exc:
            log.exception("Saving the Stock Code types failed")
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.refresh()

    def _merge_types(self) -> None:
        if not _types.shipped_available():
            QMessageBox.information(self, "Merge shipped list",
                                    "No shipped Stock Code types list was found in this installation.")
            return
        try:
            check_writable(_types._TYPES_PATH.parent)
            copy_backup(_types._TYPES_PATH)
            added = _types.merge_shipped()
            self._gen.reload_saved_data()
        except OSError as exc:
            log.exception("Merging the shipped Stock Code types failed")
            QMessageBox.critical(self, "Merge failed", str(exc))
            return
        QMessageBox.information(self, "Merge shipped list",
                                f"{added} Stock Code(s) added from the shipped list. Your own entries and "
                                "decisions were kept (a backup of the list was made first).")
        self.refresh()

    def _reset_types(self) -> None:
        if not _types.shipped_available():
            QMessageBox.information(self, "Reset Stock Code types",
                                    "No shipped Stock Code types list was found in this installation, so "
                                    "there is nothing to reset to.")
            return
        if QMessageBox.question(
                self, "Reset Stock Code types",
                "Replace your Stock Code types list with the one that shipped with the app?\n\nYour edits and "
                "decisions are lost from the list (the current file is first copied next to the original as "
                "“…bak-<date>-<time>…”).",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        try:
            check_writable(_types._TYPES_PATH.parent)
            copy_backup(_types._TYPES_PATH)
            _types.reset_to_shipped()
            self._gen.reload_saved_data()
        except OSError as exc:
            log.exception("Reset of the Stock Code types failed")
            QMessageBox.critical(self, "Reset failed", str(exc))
            return
        self.refresh()

    def _reset_choices(self):
        self._reset("rechargeability choices", _choices._CHOICES_PATH,
                    "The list becomes empty: every disparity will need a choice again.")

    def _reset_presets(self):
        self._reset("CTR presets", _presets._PRESETS_PATH,
                    "The list becomes empty (no preset ships with the app).")
