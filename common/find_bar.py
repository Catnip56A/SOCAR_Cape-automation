"""
find_bar.py — Ctrl+F "find in this table" for any table or tree.

`install_find(view)` gives a QAbstractItemView (QTableWidget, QTableView,
QTreeWidget, …) a Ctrl+F find bar that appears over its top-right corner:
a text box, ◀ ▶ buttons and a counter ("3 of 12"). Enter / ▶ / F3 go to the next
match, Shift+Enter / ◀ / Shift+F3 to the previous one, Esc closes the bar.
The match's row is selected and scrolled into view (a tree opens the groups
leading to it). It searches every visible column, case-insensitively, and does
not hide any row (the "Filter rows" boxes do that; this one only moves you
around). While it moves, no cell editor is opened.
"""

from __future__ import annotations

import weakref

from PySide6.QtCore import QEvent, QItemSelectionModel, QModelIndex, QObject, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QLabel, QLineEdit, QToolButton, QTreeView,
)


class _Edit(QLineEdit):
    """Enter = next match, Shift+Enter = previous, Esc = close."""

    def __init__(self, bar):
        super().__init__()
        self._bar = bar

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._bar.step(-1 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
        elif e.key() == Qt.Key.Key_Escape:
            self._bar.close_bar()
        else:
            super().keyPressEvent(e)


class FindBar(QFrame):
    def __init__(self, view: QAbstractItemView):
        super().__init__(view)
        self._view = view
        self._cache: list[QModelIndex] | None = None     # matches for the current text
        self._cache_text = ""
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)                  # no stylesheet: it would break tooltips
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(4)
        self._edit = _Edit(self)
        self._edit.setPlaceholderText("Find in this table…")
        self._edit.setMinimumWidth(200)
        self._count = QLabel("")
        self._count.setMinimumWidth(70)
        self._prev = QToolButton()
        self._prev.setText("◀")
        self._prev.setToolTip("Previous match (Shift+Enter)")
        self._next = QToolButton()
        self._next.setText("▶")
        self._next.setToolTip("Next match (Enter)")
        close = QToolButton()
        close.setText("✕")
        close.setToolTip("Close (Esc)")
        for w in (self._edit, self._count, self._prev, self._next, close):
            lay.addWidget(w)
        self._prev.clicked.connect(lambda: self.step(-1))
        self._next.clicked.connect(lambda: self.step(1))
        close.clicked.connect(self.close_bar)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._text_changed)
        self._edit.textChanged.connect(lambda _t: self._timer.start())
        self._model_wr = None                             # the model the cache belongs to (weak)
        self._attach_model()
        view.installEventFilter(self)                     # keep the bar in the corner on resize
        self.hide()

    # ── showing / hiding ─────────────────────────────────────────────────────

    def open_bar(self) -> None:
        self._place()
        self.show()
        self.raise_()
        self._edit.setFocus()
        self._edit.selectAll()
        self._update_count()

    def close_bar(self) -> None:
        self.hide()
        self._view.setFocus()

    def _place(self) -> None:
        self.adjustSize()
        vp = self._view.viewport().geometry()
        self.move(max(0, vp.right() - self.width() - 4), vp.top() + 4)

    def eventFilter(self, obj, e):
        if obj is self._view and e.type() in (QEvent.Type.Resize, QEvent.Type.Show) and self.isVisible():
            self._place()
        return False

    # ── searching ────────────────────────────────────────────────────────────

    def _invalidate(self, *_a) -> None:
        self._cache = None

    def _attach_model(self) -> None:
        """Follow the view's CURRENT model. A results table gets a brand-new model on every Compare (and
        the bar is installed while the table still has none), so the matches found in an old model must
        never be used again: they are references into a model that no longer exists, and using one
        crashes the app (access violation). When the model has changed, the cache is dropped and the
        "table content changed" signals are connected to the new model."""
        m = self._view.model()
        old = self._model_wr() if self._model_wr is not None else None
        if m is old and (m is not None or self._model_wr is not None):
            return
        self._cache = None
        self._model_wr = weakref.ref(m) if m is not None else None
        if m is not None:
            for sig in (m.modelReset, m.layoutChanged, m.rowsInserted, m.rowsRemoved, m.dataChanged):
                sig.connect(self._invalidate)

    def _matches(self) -> list[QModelIndex]:
        self._attach_model()
        text = self._edit.text().strip().lower()
        if self._cache is not None and self._cache_text == text:
            return self._cache
        out: list[QModelIndex] = []
        view, model = self._view, self._view.model()
        if text and model is not None:
            is_tree = isinstance(view, QTreeView)
            row_mode = view.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectRows

            def walk(parent: QModelIndex):
                for r in range(model.rowCount(parent)):
                    if view.isRowHidden(r, parent) if is_tree else view.isRowHidden(r):
                        continue
                    first, hit = None, False
                    for c in range(model.columnCount(parent)):
                        if (not is_tree) and view.isColumnHidden(c):
                            continue
                        idx = model.index(r, c, parent)
                        val = idx.data(Qt.ItemDataRole.DisplayRole)
                        if val is not None and text in str(val).lower() and not (row_mode and hit):
                            out.append(idx)          # whole-row selection: one match per row
                            hit = True
                        if first is None:
                            first = idx
                    if is_tree and first is not None and model.hasChildren(first):
                        walk(first)

            walk(QModelIndex())
        self._cache, self._cache_text = out, text
        return out

    @staticmethod
    def _key(idx: QModelIndex):
        path, p = [], idx
        while p.isValid():
            path.append((p.row(), p.column()))
            p = p.parent()
        return tuple(reversed(path))

    def _position(self, matches) -> int:
        """0-based position of the current cell among the matches, or -1."""
        cur = self._view.currentIndex()
        if not cur.isValid():
            return -1
        key = self._key(cur)
        for i, m in enumerate(matches):
            if self._key(m) == key:
                return i
        return -1

    def _text_changed(self) -> None:
        self._cache = None
        matches = self._matches()
        if matches:
            self._goto(matches[self._first_from_current(matches)])
        self._update_count()

    def _first_from_current(self, matches) -> int:
        """Index of the first match at or after the current cell (wrapping)."""
        cur = self._view.currentIndex()
        if not cur.isValid():
            return 0
        ck = self._key(cur)
        for i, m in enumerate(matches):
            if self._key(m) >= ck:
                return i
        return 0

    def step(self, direction: int) -> None:
        matches = self._matches()
        if not matches:
            self._update_count()
            return
        pos = self._position(matches)
        if pos < 0:                                       # not on a match: nearest in that direction
            first = self._first_from_current(matches)
            nxt = first if direction > 0 else (first - 1) % len(matches)
        else:
            nxt = (pos + direction) % len(matches)
        self._goto(matches[nxt])
        self._update_count()

    def _goto(self, idx: QModelIndex) -> None:
        view = self._view
        if not idx.isValid() or idx.model() is not view.model():      # a match from a model that is gone
            return
        if isinstance(view, QTreeView):
            p = idx.parent()
            while p.isValid():
                view.expand(p)
                p = p.parent()
        triggers = view.editTriggers()
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)   # no editor while moving
        try:
            flags = QItemSelectionModel.SelectionFlag.ClearAndSelect
            if view.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectRows:
                flags |= QItemSelectionModel.SelectionFlag.Rows
            view.selectionModel().setCurrentIndex(idx, flags)
            view.scrollTo(idx, QAbstractItemView.ScrollHint.EnsureVisible)
        finally:
            view.setEditTriggers(triggers)

    def _update_count(self) -> None:
        text = self._edit.text().strip()
        if not text:
            self._count.setText("")
            return
        matches = self._matches()
        if not matches:
            self._count.setText("No matches")
            return
        pos = self._position(matches)
        self._count.setText(f"{pos + 1} of {len(matches)}" if pos >= 0 else f"{len(matches)} matches")


def install_find(view: QAbstractItemView) -> FindBar:
    """Idempotent: Ctrl+F (and F3 / Shift+F3 once a search is open) on `view`."""
    existing = getattr(view, "_find_bar", None)
    if existing is not None:
        return existing
    bar = FindBar(view)
    ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
    QShortcut(QKeySequence.StandardKey.Find, view, activated=bar.open_bar, context=ctx)
    QShortcut(QKeySequence(Qt.Key.Key_F3), view, activated=lambda: bar.step(1), context=ctx)
    QShortcut(QKeySequence("Shift+F3"), view, activated=lambda: bar.step(-1), context=ctx)
    view._find_bar = bar
    return bar
